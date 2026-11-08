"""迁移期兼容层登记（S4-6，2026-11-09）。

本模块不做任何 re-export，只做清点：扫描旧路径垫片（shim），给出
「旧模块 -> 新模块」的完整清单，供删除与回归校验使用。

规则的唯一来源是垫片文件自身的 docstring：

- 自动生成的垫片："Legacy shim: new path app.application.xxx (S4-6, ...)"
- 手写垫片："新路径：app.infrastructure.http.xxx"

删除时机：一个版本之后，先删垫片、再删本目录（regression_tests/test_compat_shims.py
会随垫片一起失效，届时删测试即可）。
"""
from __future__ import annotations

import ast
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1]
LEGACY_ROOTS = ("services", "extensions", "thirdparty")

_TARGET_PATTERNS = (
    re.compile(r"Legacy shim: new path ([A-Za-z0-9_.]+)"),
    re.compile(r"新路径：([A-Za-z0-9_.]+)"),
)


def _target_of(source: str) -> str | None:
    head = source[:600]
    for pattern in _TARGET_PATTERNS:
        match = pattern.search(head)
        if match:
            return match.group(1)
    return None


def iter_legacy_shims() -> list[tuple[str, str]]:
    """返回 [(旧模块名, 新模块名), ...]，按旧模块名排序。"""
    found: list[tuple[str, str]] = []
    for root in LEGACY_ROOTS:
        base = APP / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            target = _target_of(path.read_text(encoding="utf-8"))
            if not target:
                continue
            rel = path.relative_to(APP).with_suffix("")
            parts = rel.parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            found.append((".".join(("app",) + parts), target))
    return sorted(found)


def reexported_names(module_source: str) -> list[str]:
    """解析垫片里 from ... import (...) 的导出名，用于新旧对象同一性校验。"""
    tree = ast.parse(module_source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names.append(alias.asname or alias.name)
    return names
