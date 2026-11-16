#!/usr/bin/env python3
"""Layering / dependency guard for the backend (stdlib only, no new deps).

Why this exists: the refactor plan targets a strict dependency direction
(api -> application -> domain, infrastructure -> domain/ports). Because
import-linter is not a declared dependency, this script provides the same
gate with the standard library, so CI can fail on new coupling.

Usage:
    python scripts/check_layering.py                # human summary
    python scripts/check_layering.py --json out.json
    python scripts/check_layering.py --fail-on 0    # CI gate: any violation fails
    python scripts/check_layering.py --update-baseline .layering-baseline.json

Exit codes: 0 = within budget, 1 = over budget, 2 = tool error.
"""
from __future__ import annotations

import argparse
import ast
from importlib.util import resolve_name
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "backend" / "app"

# ---------------------------------------------------------------- layer model
# Target layering (see docs/architecture-evolution-plan.md). Until packages are
# physically moved, existing packages are mapped onto their target layer.
LAYER_OF_PREFIX = {
    "app.api": "presentation",
    "app.schemas": "presentation",
    "app.middlewares": "presentation",
    "app.application": "application",
    "app.application": "application",
    "app.domain": "domain",
    # 整个持久化子树（base/session/backup/repositories/models）都算 persistence；
    # 只映射 models 会让「模型 import Base」变成 persistence→infrastructure 假违规。
    "app.infrastructure.persistence": "persistence",
    "app.db": "persistence",
    "app.extensions": "infrastructure",
    "app.infrastructure": "infrastructure",
    "app.thirdparty": "infrastructure",
    "app.core": "platform",
    "app.scripts": "tooling",
}
# 装配点（composition root）可以被任意层引用：它只做「把实现接到端口上」，
# 不含业务逻辑。app.application.container 是函数访问器形式的装配入口（§10.3 决策 4）。
COMPOSITION_ROOTS = {"app.main", "app.application.container"}

# allowed: source layer -> layers it may import
ALLOWED = {
    "presentation": {"presentation", "application", "domain", "platform"},
    "application": {"application", "domain", "platform"},
    "domain": {"domain"},
    "persistence": {"persistence", "domain", "platform"},
    "infrastructure": {"infrastructure", "domain", "application", "persistence", "platform"},
    "platform": {"platform"},
        "tooling": {"presentation", "application", "domain", "persistence", "infrastructure", "platform", "tooling"},
}

# Reserved for individually justified exceptions. TMDB persistence now sits behind
# typed ports, so the previous application->persistence exception is removed.
DOCUMENTED_EXCEPTIONS: list[tuple[str, str, str]] = []


def is_documented_exception(src: str, dst: str) -> bool:
    for src_prefix, dst_prefix, _reason in DOCUMENTED_EXCEPTIONS:
        src_ok = src == src_prefix or src.startswith(src_prefix + ".")
        dst_ok = dst == dst_prefix or dst.startswith(dst_prefix + ".")
        if src_ok and dst_ok:
            return True
    return False
def layer_of(module: str) -> str:
    best = ""
    for prefix, layer in LAYER_OF_PREFIX.items():
        if module == prefix or module.startswith(prefix + "."):
            if len(prefix) > len(best):
                best = prefix
    if module in COMPOSITION_ROOTS or module.startswith("app.main"):
        return "composition-root"
    return LAYER_OF_PREFIX.get(best, "unknown")


def collect_modules(app_root: Path) -> tuple[set[str], dict[str, Path]]:
    modules: set[str] = set()
    files: dict[str, Path] = {}
    for path in app_root.rglob("*.py"):
        rel = path.relative_to(app_root.parent)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        module = ".".join(parts)
        modules.add(module)
        files[module] = path
    return modules, files


def resolve(target: str, modules: set[str]) -> str:
    """Longest existing module prefix of an imported name."""
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in modules:
            return candidate
    return target


class ImportVisitor(ast.NodeVisitor):
    def __init__(self, package: str = "") -> None:
        self.depth = 0
        self.package = package
        self.edges: list[tuple[str, int, bool]] = []  # (module, lineno, deferred)
        self.targets: list[tuple[str, int, bool]] = []
        self.fingerprints: list[tuple[str, int]] = []
        self.relative_imports: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node):  # noqa: N802
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ImportFrom(self, node):  # noqa: N802
        module = node.module or ""
        if node.level:
            # Keep the historical absolute-statement count comparable, while
            # resolving relative dependencies in the full architecture graph.
            names = ",".join(sorted(alias.name for alias in node.names))
            fingerprint = (f"from {'.' * node.level}{module} import {names}", node.lineno)
            self.relative_imports.append(fingerprint)
            if self.depth:
                self.fingerprints.append(fingerprint)
            target_module = resolve_name('.' * node.level + module, self.package)
            for alias in node.names:
                target = target_module if alias.name == '*' else f'{target_module}.{alias.name}'
                self.targets.append((target, node.lineno, self.depth > 0))
            return
        if module and module.split(".")[0] == "app":
            self.edges.append((module, node.lineno, self.depth > 0))
            for alias in node.names:
                self.targets.append((f"{module}.{alias.name}", node.lineno, self.depth > 0))
            if self.depth:
                names = ",".join(sorted(alias.name for alias in node.names))
                self.fingerprints.append((f"from {module} import {names}", node.lineno))
        self.generic_visit(node)

    def visit_Import(self, node):  # noqa: N802
        for alias in node.names:
            if alias.name.split(".")[0] == "app":
                self.edges.append((alias.name, node.lineno, self.depth > 0))
                self.targets.append((alias.name, node.lineno, self.depth > 0))
                if self.depth:
                    self.fingerprints.append((f"import {alias.name}", node.lineno))
        self.generic_visit(node)


def scan(app_root: Path):
    return scan_details(app_root)[:4]


def scan_details(app_root: Path):
    modules, files = collect_modules(app_root)
    edges = []          # (src, dst, lineno, deferred)
    parse_errors = []
    precise_edges = []
    fingerprints = []
    relative_imports = []
    for module, path in sorted(files.items()):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            parse_errors.append(f"{path}: {exc}")
            continue
        package = module if path.name == '__init__.py' else module.rpartition('.')[0]
        visitor = ImportVisitor(package)
        try:
            visitor.visit(tree)
        except (ImportError, ValueError) as exc:
            parse_errors.append(f"{path}: {exc}")
            continue
        for target, lineno, deferred in visitor.edges:
            dst = resolve(target, modules)
            if dst == module:
                continue
            edges.append((module, dst, lineno, deferred))
        for target, lineno, deferred in visitor.targets:
            dst = resolve(target, modules)
            if dst != module:
                precise_edges.append((module, dst, lineno, deferred))
        fingerprints.extend({"src": module, "import": target, "line": line} for target, line in visitor.fingerprints)
        relative_imports.extend({"src": module, "import": target, "line": line} for target, line in visitor.relative_imports)
    return modules, files, edges, parse_errors, precise_edges, fingerprints, relative_imports


def sccs(nodes: set[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Tarjan SCC, iterative, stdlib only."""
    graph = defaultdict(set)
    for src, dst in edges:
        if src in nodes and dst in nodes:
            graph[src].add(dst)
    index_counter = [0]
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []

    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = index_counter[0]
        index_counter[0] += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
                if on_stack.get(nxt):
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    comp.append(member)
                    if member == node:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=APP_ROOT, help="package root to scan (default: backend/app)")
    parser.add_argument("--json", metavar="PATH", help="write machine-readable report")
    parser.add_argument("--fail-on", type=int, default=None, metavar="N",
                        help="exit 1 when violations exceed N")
    parser.add_argument("--baseline", metavar="PATH", help="budget file written by --update-baseline")
    parser.add_argument("--update-baseline", action="store_true", help="write current numbers as the budget")
    parser.add_argument("--top", type=int, default=12, help="how many sample edges to print per rule")
    args = parser.parse_args()

    if not args.app_root.is_dir():
        print(f"app package not found: {args.app_root}", file=sys.stderr)
        return 2

    modules, _files, edges, parse_errors, precise_edges, fingerprints, relative_imports = scan_details(args.app_root)

    violations = []
    for src, dst, lineno, deferred in precise_edges:
        src_layer, dst_layer = layer_of(src), layer_of(dst)
        if src_layer == "composition-root":
            continue
        allowed = ALLOWED.get(src_layer)
        if allowed is None or dst_layer == "unknown":
            continue
        # 装配点是唯一例外：任何层都可以引用它，它自身不产生业务依赖。
        if dst_layer == "composition-root":
            continue
        if dst_layer not in allowed:
            if is_documented_exception(src, dst):
                continue
            violations.append({
                "src": src, "dst": dst, "line": lineno,
                "src_layer": src_layer, "dst_layer": dst_layer, "deferred": deferred,
            })

    module_edges = {
        (s, d)
        for s, d, _l, _df in precise_edges
        if layer_of(s) != "composition-root" and layer_of(d) != "composition-root"
    }
    cycles = sccs(modules, sorted(module_edges))
    deferred_edges = [e for e in edges if e[3]]

    by_pair = Counter((v["src_layer"], v["dst_layer"]) for v in violations)
    report = {
        "app_root": str(args.app_root),
        "modules": len(modules),
        "internal_edges": len(edges),
        "deferred_imports": len(deferred_edges),
        "deferred_edges": [{"src": s, "dst": d, "line": line} for s, d, line, _ in deferred_edges],
        "deferred_import_fingerprints": fingerprints,
        "relative_import_fingerprints": relative_imports,
        "violations": len(violations),
        "violations_by_layer_pair": {f"{a}->{b}": n for (a, b), n in by_pair.most_common()},
        "cycles": {"count": len(cycles), "largest": max((len(c) for c in cycles), default=0),
                   "sample": [c[:6] for c in sorted(cycles, key=len, reverse=True)[:5]]},
        "samples": violations[:400],
        "parse_errors": parse_errors,
    }

    print("=" * 78)
    print(f"modules={report['modules']}  internal_imports={report['internal_edges']}  "
          f"deferred_imports={report['deferred_imports']}  violations={report['violations']}")
    print(f"import_cycles={report['cycles']['count']}  largest_cycle={report['cycles']['largest']}")
    _exceptions = [
        (src, dst, lineno, deferred)
        for src, dst, lineno, deferred in edges
        if is_documented_exception(src, dst)
    ]
    if _exceptions:
        print("\ndocumented exceptions (excluded from violations):")
        for src, dst, lineno, deferred in _exceptions:
            print(f"      {src} -> {dst}  line {lineno}{' (deferred)' if deferred else ''}")
    if report["violations_by_layer_pair"]:
        print("\nviolations by layer pair (target rules):")
        for pair, count in report["violations_by_layer_pair"].items():
            print(f"  {pair:44s} {count}")
            shown = 0
            for v in violations:
                if f"{v['src_layer']}->{v['dst_layer']}" == pair and shown < args.top:
                    deferred = " (deferred)" if v["deferred"] else ""
                    print(f"      {v['src']} -> {v['dst']}  line {v['line']}{deferred}")
                    shown += 1
    if cycles:
        print("\nimport cycles (strongly connected components):")
        for comp in sorted(cycles, key=len, reverse=True)[:5]:
            print(f"  size={len(comp)}: {', '.join(comp[:8])}{' ...' if len(comp) > 8 else ''}")
    if parse_errors:
        print("\nparse errors:")
        for err in parse_errors:
            print(f"  {err}")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\njson report -> {args.json}")

    if parse_errors:
        return 2

    stored = None
    if args.baseline and not args.update_baseline:
        try:
            stored = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            for key in ("violations", "deferred_imports", "cycles"):
                if type(stored[key]) is not int or stored[key] < 0:
                    raise ValueError(f"{key} must be a nonnegative integer")
            for key in ("approved_deferred_edges", "approved_deferred_imports", "approved_relative_imports"):
                if key in stored:
                    if not isinstance(stored[key], list) or not all(isinstance(edge, str) for edge in stored[key]):
                        raise ValueError(f"{key} must be a list of strings")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            print(f"invalid baseline: {exc}", file=sys.stderr)
            return 2

    if args.update_baseline:
        if violations or cycles:
            print("baseline cannot approve layering violations or cycles", file=sys.stderr)
            return 1
        budget = {k: report[k] for k in ("violations", "deferred_imports")}
        budget["cycles"] = report["cycles"]["count"]
        budget["approved_deferred_edges"] = sorted(f"{s} -> {d}" for s, d, _, _ in deferred_edges)
        budget["approved_deferred_imports"] = sorted(f"{item['src']}: {item['import']}" for item in fingerprints)
        budget["approved_relative_imports"] = sorted(f"{item['src']}: {item['import']}" for item in relative_imports)
        target = Path(args.baseline or ".layering-baseline.json")
        target.write_text(json.dumps(budget, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"baseline budget -> {target}")

    if stored is not None:
        actual = {"violations": len(violations), "deferred_imports": len(deferred_edges), "cycles": len(cycles)}
        exceeded = [key for key, value in actual.items() if value > stored[key]]
        # Baselines may track deferred composition imports, never legalize layering defects.
        if violations or cycles:
            exceeded.append("architecture")
        if args.fail_on is not None and len(violations) > args.fail_on:
            exceeded.append("fail-on")
        if "approved_deferred_edges" in stored:
            approved = Counter(stored["approved_deferred_edges"])
            unapproved = Counter(f"{s} -> {d}" for s, d, _, _ in deferred_edges) - approved
            if unapproved:
                exceeded.append("unapproved deferred edges")
                for edge in sorted(unapproved):
                    print(f"unapproved: {edge}")
        if "approved_deferred_imports" in stored:
            approved = Counter(stored["approved_deferred_imports"])
            unapproved = Counter(f"{item['src']}: {item['import']}" for item in fingerprints) - approved
            if unapproved:
                exceeded.append("unapproved deferred imports")
                for target in sorted(unapproved):
                    print(f"unapproved: {target}")
        if "approved_relative_imports" in stored:
            approved = Counter(stored["approved_relative_imports"])
            unapproved = Counter(f"{item['src']}: {item['import']}" for item in relative_imports) - approved
            if unapproved:
                exceeded.append("unapproved relative imports")
                for target in sorted(unapproved):
                    print(f"unapproved relative: {target}")
        if exceeded:
            print("budget exceeded: " + ", ".join(exceeded))
        return 1 if exceeded else 0
    if args.fail_on is not None:
        return 1 if len(violations) > args.fail_on or cycles else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
