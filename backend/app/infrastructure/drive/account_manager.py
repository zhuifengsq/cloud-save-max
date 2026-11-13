from __future__ import annotations

import copy
import threading

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.runtime.cache_version import drive_accounts_version
from app.core.runtime.ttl_cache import TtlCache
from app.core.settings import settings
from app.infrastructure.drive.factory import AccountManager
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.persistence.models.drive_account import DriveAccount

# 账号快照：低频写、高频读的配置类数据。缓存的是「已经解析成适配器入参」的纯数据，
# 不缓存 ORM 实例（会话一关就失效，缓存 ORM 会跨会话误用）。
_SNAPSHOT_KEY = "drive_accounts"
_snapshot_cache = TtlCache(
    maxsize=8,
    ttl_seconds=max(0.0, float(getattr(settings, "account_snapshot_ttl_seconds", 0) or 0))
    or None,
)
_snapshot_lock = threading.Lock()


def _snapshot_stamp(db: Session) -> tuple[int, int | None, object]:
    """账号表的「形状指纹」：条数 + 最大 id + 最大 updated_at。

    只有 count 一个数字不够：改名/换 cookie 不改变条数。加上 max(updated_at) 能覆盖
    绝大多数更新；SQLite 的 updated_at 只有秒级精度，所以写路径还会主动 bump
    drive_accounts_version()（见 cache_version.py），二者取或。
    """
    row = db.execute(
        select(
            func.count(DriveAccount.id),
            func.max(DriveAccount.id),
            func.max(DriveAccount.updated_at),
        )
    ).one()
    return (int(row[0] or 0), row[1], row[2])


def _load_rows(db: Session) -> list[DriveAccount]:
    return list(
        db.execute(
            select(DriveAccount).order_by(DriveAccount.is_default.desc(), DriveAccount.id.asc())
        )
        .scalars()
        .all()
    )


def _build_payload(rows: list[DriveAccount]) -> dict:
    """把账号行转成 AccountManager.load_accounts 需要的纯数据快照。"""
    accounts = []
    for item in rows:
        parsed = AdapterRegistry.parse_config_json(item.drive_type, item.config_json, item.cookie)
        accounts.append(
            {
                "name": item.name,
                "drive_type": item.drive_type,
                "config": parsed,
                "cookie": AdapterRegistry.serialize_config(item.drive_type, parsed),
                "enabled": item.enabled,
                "default": item.is_default,
            }
        )
    return {"accounts": accounts}


def load_accounts_payload(db: Session) -> dict:
    """读取账号快照（带进程内缓存）。

    命中条件：版本号未变 且 表形状指纹未变。TTL 只是兜底（外部直接改库、时钟回拨等）。
    返回深拷贝，调用方可以随意增删字段而不会污染缓存。
    """
    ttl = float(getattr(settings, "account_snapshot_ttl_seconds", 0) or 0)
    if not bool(getattr(settings, "use_account_snapshot_cache", False)) or ttl <= 0:
        return _build_payload(_load_rows(db))

    # 串行化回填：缓存未命中时避免并发首读各自做一遍全表查询 + 每行两次 JSON 解析。
    with _snapshot_lock:
        version = drive_accounts_version()
        stamp = _snapshot_stamp(db)
        cached = _snapshot_cache.get(_SNAPSHOT_KEY)
        if cached is not None:
            cached_version, cached_stamp, cached_payload = cached
            if cached_version == version and cached_stamp == stamp:
                return copy.deepcopy(cached_payload)
        payload = _build_payload(_load_rows(db))
        _snapshot_cache.set(_SNAPSHOT_KEY, (version, stamp, copy.deepcopy(payload)))
        return payload


def clear_account_snapshot_cache() -> None:
    """清空账号快照缓存（测试 / 手工改库后使用）。"""
    with _snapshot_lock:
        _snapshot_cache.clear()


class DatabaseAccountManager:
    """每次实例化只做一次「取快照 + 组装 AccountManager」，快照本身走进程内缓存。"""

    def __init__(self, db: Session, *, no_login: bool = False):
        self.db = db
        self.manager = AccountManager()
        self.manager.load_accounts(load_accounts_payload(db), no_login=no_login)

    @classmethod
    def from_payload(cls, payload: dict, *, no_login: bool = False):
        """Construct adapters after the session used to read the payload is closed."""
        instance = cls.__new__(cls)
        instance.db = None
        instance.manager = AccountManager()
        instance.manager.load_accounts(payload, no_login=no_login)
        return instance

    def get_adapter_for_task(self, task: dict, *, allow_inactive: bool = False):
        return self.manager.get_adapter_for_task(task, allow_inactive=allow_inactive)

    def get_default_adapter(self):
        return self.manager.get_default_adapter()

    def init_for_tasks(self, tasks: list[dict]) -> None:
        self.manager.init_adapters_for_tasks(tasks)
