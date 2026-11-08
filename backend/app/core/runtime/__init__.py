"""进程内运行时基础设施（平台层：只允许依赖标准库与 app.core）。

单进程假设下可复用的运行时组件：
- keyed_locks：有上限的 key 级锁表（满足 KeyedLockPort）；
- single_flight：同 key 并发只执行一次（满足 SingleFlightPort）；
- clock：系统时钟（满足 ClockPort）；
- tx：只读事务边界工具 end_read_tx。
"""

from app.core.runtime.clock import SystemClock
from app.core.runtime.keyed_locks import LRUKeyedLocks
from app.core.runtime.single_flight import SingleFlight
from app.core.runtime.tx import end_read_tx

__all__ = ["LRUKeyedLocks", "SingleFlight", "SystemClock", "end_read_tx"]
