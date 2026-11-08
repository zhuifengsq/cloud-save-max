"""路由层事务边界护栏：低风险写端点必须走装配点提供的 transaction 边界。

约定（docs/architecture-evolution-plan.md §3.6）：一个用例一个事务，commit 由
transaction 边界负责；已接线的路由里不应再出现裸 db.commit()。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "app" / "api" / "routes"
WIRED = [
    "system_settings.py",
    "notifications.py",
    "tmdb_settings.py",
    "task_templates.py",
]


def _source(name: str) -> str:
    return (ROUTES / name).read_text(encoding="utf-8")


def _imported_from(name: str, module: str) -> set[str]:
    tree = ast.parse(_source(name))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            imported |= {alias.name for alias in node.names}
    return imported


def test_wired_routes_have_no_bare_commit():
    for name in WIRED:
        source = _source(name)
        assert "db.commit()" not in source, f"{name} 仍有裸 db.commit()，事务边界未收口"
        assert "with transaction(db):" in source, f"{name} 未使用 transaction 边界"


def test_wired_routes_import_transaction_from_composition_root():
    """S5：路由只从装配点（app.application.container）取事务边界，不再直连 persistence。"""
    for name in WIRED:
        assert "transaction" in _imported_from(name, "app.application.container"), (
            f"{name} 的 transaction 必须来自 app.application.container"
            "（路由直连 infrastructure/persistence 会新增分层违规）"
        )


def test_routes_do_not_import_persistence_directly():
    """路由层不得 import 任何 app.infrastructure.persistence.*。"""
    strays = []
    for path in sorted(ROUTES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "app.infrastructure.persistence"
            ):
                strays.append(f"{path.name}:{node.lineno}")
    assert strays == [], f"路由层仍有直连 persistence 的 import：{strays}"


def test_transaction_is_reexported_from_unit_of_work():
    from app.infrastructure.persistence.session import transaction as session_transaction
    from app.infrastructure.persistence.unit_of_work import transaction as uow_transaction

    assert session_transaction is uow_transaction
