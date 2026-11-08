"""事务边界（Unit of Work）：目标架构中应用层唯一可以提交事务的地方。

实现位于 `app.db.session.transaction`（与 retry_on_locked 同处，属于持久化层），
本模块只做 re-export，保持 `from app.infrastructure.persistence.unit_of_work import
transaction` 这个稳定入口。

分层注意：表现层（api/routes）不要 import 本模块——那会产生新的
presentation->infrastructure 违规；路由请直接用 `app.db.session.transaction`。
用例/服务层可以继续从这里取，未来接仓储时再扩展 UnitOfWork。

用法：

    from app.infrastructure.persistence.unit_of_work import transaction

    with transaction(db):
        db.add(...)          # 只 flush 不 commit；退出时统一提交

注意：成功退出才 commit；异常先 rollback 再原样抛出（不吞异常）；只重试
commit 本身的 SQLite "database is locked"，绝不包裹网盘等外部副作用。
"""
from __future__ import annotations

from typing import Any

from app.db.session import transaction

__all__ = ["UnitOfWork", "transaction"]


class UnitOfWork:
    """transaction() 的轻量类封装，便于用例持有并在未来挂接仓储。"""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._ctx = None

    def __enter__(self) -> Any:
        self._ctx = transaction(self.db)
        return self._ctx.__enter__()

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        assert self._ctx is not None
        return self._ctx.__exit__(exc_type, exc, tb)
