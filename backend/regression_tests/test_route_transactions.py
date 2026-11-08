"""路由层事务边界护栏：低风险写端点必须走 app.db.session.transaction。

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


def _imported_from_db_session(name: str) -> set[str]:
    tree = ast.parse(_source(name))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.db.session":
            imported |= {alias.name for alias in node.names}
    return imported


def test_wired_routes_have_no_bare_commit():
    for name in WIRED:
        source = _source(name)
        assert "db.commit()" not in source, f"{name} 仍有裸 db.commit()，事务边界未收口"
        assert "with transaction(db):" in source, f"{name} 未使用 transaction 边界"


def test_wired_routes_import_transaction_from_db_session():
    for name in WIRED:
        assert "transaction" in _imported_from_db_session(name), (
            f"{name} 的 transaction 必须来自 app.db.session"
            "（从 infrastructure 导入会新增 presentation->infrastructure 违规）"
        )


def test_transaction_is_reexported_from_unit_of_work():
    from app.db.session import transaction as session_transaction
    from app.infrastructure.persistence.unit_of_work import transaction as uow_transaction

    assert session_transaction is uow_transaction
