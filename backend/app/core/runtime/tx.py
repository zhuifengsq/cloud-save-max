"""会话状态小工具：结束只读事务（不引入任何 ORM 依赖）。

放在 platform 层（app.core）而不是 app.db，是因为它只是对 Session 状态机的操作，
不导入任何模型/引擎；这样 application 层的服务可以在"进入外部 IO 前释放 SQLite
快照"时使用它，而不必在模块顶层依赖 persistence 层（见方案 §3.6 规则与 §10.10）。
"""

from __future__ import annotations

from typing import Any


def end_read_tx(db: Any) -> None:
    """结束「只读事务」：在进入外部 IO（网盘/TMDB/Telegram）之前释放 SQLite 的快照。

    语义上这是「提交一个没有写入的事务」，与「提交业务数据」不是一回事：
    - 只用于读路径 / 长 IO 之前，避免持有读写锁、卡住 WAL checkpoint；
    - 业务写入的提交必须由事务边界拥有者调用 app.db.session.commit()/transaction()，
      不得用它代替；
    - 没有进行中的事务时是 no-op（显式判断省一次空的 round-trip）。
    """
    if db.in_transaction():
        db.commit()
