"""静态护栏：`app/**/*.py` 里不允许出现「未定义的全局名」。

为什么需要它：2026-11-09 的 TMDB 缓存页「点击刷新 → 服务器内部错误」事故，
根因是仓储下沉时把 `lookup = select(...)` + `db.scalar(lookup)` 的第一处用法换成了
`tmdb_media_cache_repo().get_row(...)`，但函数末尾第二处 `db.scalar(lookup)` 忘了一起换，
于是运行到那里抛 `NameError: name 'lookup' is not defined` → 500。
同一批自动改写还漏了 4 个 import（`commit` / `drive_accounts_repo` / `datetime` / `random`
以及 `_to_sort_ts`），这些分支都没有测试覆盖，全量测试仍然是绿的。

`NameError` 只有真跑到那一行才会报，静态扫描能在 CI 阶段拦住。实现用标准库 `symtable`：
函数作用域里「既不在本作用域赋值、也不是本作用域 import、也不在模块顶层定义、也不是内建名」
的符号，就是未定义全局名。

允许名单（ALLOWED）必须写理由；空集是常态。
"""
from __future__ import annotations

import builtins
from pathlib import Path
import symtable

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# 允许出现「未定义全局名」的例外：(相对路径, 名字) -> 理由
ALLOWED: dict[tuple[str, str], str] = {}

_ALWAYS_DEFINED = {
    "__file__",
    "__name__",
    "__doc__",
    "__package__",
    "__spec__",
    "__loader__",
    "__builtins__",
    "__debug__",
}


def _module_level_names(table: symtable.SymbolTable) -> set[str]:
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }


def _undefined_in(tree: symtable.SymbolTable, module_defined: set[str], builtin_names: set[str]) -> set[str]:
    found: set[str] = set()
    for child in tree.get_children():
        found |= _undefined_in(child, module_defined, builtin_names)
    for symbol in tree.get_symbols():
        if not symbol.is_global():
            continue
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace():
            continue
        name = symbol.get_name()
        if name in module_defined or name in builtin_names or name in _ALWAYS_DEFINED:
            continue
        found.add(name)
    return found


def test_no_undefined_globals():
    builtin_names = set(dir(builtins))
    offenders: list[str] = []
    checked = 0
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT.parent).as_posix()
        source = path.read_text(encoding="utf-8")
        table = symtable.symtable(source, rel, "exec")
        module_defined = _module_level_names(table)
        checked += 1
        for name in sorted(_undefined_in(table, module_defined, builtin_names)):
            if (rel, name) in ALLOWED:
                continue
            offenders.append(f"{rel}: undefined name {name!r} (NameError at runtime)")

    assert checked > 150, f"只扫到 {checked} 个模块，路径可能变了（预期 200+）"
    assert not offenders, "发现未定义的全局名：\n" + "\n".join(offenders)


def test_allowlist_entries_still_needed():
    """登记了例外但实际已不存在未定义名 → 必须删掉登记，避免允许名单腐化。"""
    builtin_names = set(dir(builtins))
    live: set[tuple[str, str]] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT.parent).as_posix()
        table = symtable.symtable(path.read_text(encoding="utf-8"), rel, "exec")
        for name in _undefined_in(table, _module_level_names(table), builtin_names):
            live.add((rel, name))
    stale = sorted(k for k in ALLOWED if k not in live)
    assert not stale, "允许名单里有已不再需要的条目，请删除：" + repr(stale)
