"""进程内运行时基础设施（平台层：只允许依赖标准库与 app.core）。

当前只有 key 级锁表；后续单进程假设下的其它运行时组件也放这里。
"""

from app.core.runtime.keyed_locks import LRUKeyedLocks

__all__ = ["LRUKeyedLocks"]
