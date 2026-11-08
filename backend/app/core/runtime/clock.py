"""系统时钟（platform 层）：满足 ClockPort，便于测试注入时间。

只依赖标准库；不读 settings、不碰数据库，因此 application 层可安全使用。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


class SystemClock:
    """真实时钟：本地时间用 now()，UTC 用 now_utc()，单调钟用 monotonic()。"""

    def now(self) -> datetime:
        return datetime.now()

    def now_utc(self) -> datetime:
        # 与仓库既有约定保持一致：naive UTC（不用已弃用的 utcnow()）。
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
