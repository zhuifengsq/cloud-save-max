"""缓存版本号（platform 层）：写操作 bump，读缓存把它拼进 key 实现主动失效。

为什么不用「缓存对象 + clear()」：写操作大多落在 application（services）或
persistence（repositories）层，而这些层不允许反向依赖 infrastructure（缓存实现所在层）。
版本号放在 platform 层，任何层都能 import（分层规则允许），缓存实现自己读版本号，
于是「写者只管 bump、读者自动失效」，不新增任何跨层依赖。

单进程假设：版本号是进程内整数，多 worker 时不共享（与缓存本身的单进程假设一致）。
"""
from __future__ import annotations

import threading


class CacheVersion:
    """单调递增的版本计数器（进程内）。"""

    __slots__ = ("_name", "_value", "_lock")

    def __init__(self, name: str) -> None:
        self._name = str(name)
        self._value = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def bump(self) -> int:
        """递增并返回新版本（写操作提交前后均可调用）。"""
        with self._lock:
            self._value += 1
            return self._value


_drive_accounts = CacheVersion("drive_accounts")


def drive_accounts_version() -> int:
    """网盘账号快照的当前版本（缓存 key 的一部分）。"""
    return _drive_accounts.value


def invalidate_drive_accounts() -> int:
    """账号被增删改后调用：让所有已缓存的账号快照失效。"""
    return _drive_accounts.bump()
