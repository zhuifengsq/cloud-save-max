"""令牌刷新 single-flight（platform 层，配合 domain.ports.locks.SingleFlightPort 使用）。

问题：同一账户可能同时存在多个适配器实例（实例缓存 TTL 到期重建、cookie 轮换、并发
请求各自 create_adapter）。当 access_token 过期时，多个实例会拿同一个 refresh_token
同时去刷新；多数网盘在刷新后立刻作废旧 refresh_token，于是后到的请求把先到的新令牌
覆盖成失效值，表现为「偶发登录失效、需要重新扫码」。

做法：按「命名空间 + 网盘类型 + 账户」合并并发刷新；进入临界区后再检查一次
is_valid()——leader 刷完，等待者醒来发现令牌已有效就直接复用，不重复刷新。
刷新失败会原样抛给等待者（与 SingleFlight 语义一致）。

单进程假设：仅合并本进程内的并发刷新；多 worker 部署时每个进程各自合并。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from app.core.runtime.single_flight import SingleFlight


@dataclass(frozen=True)
class TokenRefreshOutcome:
    """一次 ensure 的结果：performed=是否真的去刷了；ok=结束后令牌是否有效。"""

    performed: bool
    ok: bool
    result: Any = None


class TokenManager:
    """按 key 合并令牌刷新，并在临界区内二次检查有效性。"""

    def __init__(self, *, single_flight: SingleFlight | None = None, maxsize: int = 128) -> None:
        self._single_flight = single_flight or SingleFlight(maxsize=maxsize)

    def refresh(
        self,
        key: str,
        *,
        is_valid: Callable[[], bool],
        refresh: Callable[[], Any],
    ) -> TokenRefreshOutcome:
        """确保令牌有效：有效则直接返回；否则同 key 并发只执行一次 refresh。"""
        if is_valid():
            return TokenRefreshOutcome(performed=False, ok=True)

        def _run() -> TokenRefreshOutcome:
            # 双重检查：leader 刷新期间，其他等待者的令牌可能已被别人刷好。
            if is_valid():
                return TokenRefreshOutcome(performed=False, ok=True)
            result = refresh()
            ok = result is not False and result is not None
            return TokenRefreshOutcome(performed=True, ok=ok, result=result)

        return self._single_flight.run(key, _run)

    def __len__(self) -> int:
        return len(self._single_flight)


_MANAGER: TokenManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_token_manager() -> TokenManager:
    """进程级单例（同账户的多个适配器实例共享）。"""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = TokenManager()
    return _MANAGER


def reset_token_manager(manager: TokenManager | None = None) -> None:
    """替换/清空单例（测试用）。"""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = manager
