"""SQLite 迁移前热备份（stdlib only，不引入新依赖）。

为什么存在：应用启动时会在 lifespan 里自动执行 Alembic upgrade（backend/app/main.py），
而 SQLite 的迁移是不可逆的 schema 变更；本项目历史上 7 个迁移带静默 return、多个
downgrade 直接 drop 表，一旦升级失败或升错版本，没有备份就只能靠运气。

设计约束：
- 单进程假设：备份发生在真正提供服务之前，此时无业务写入。
- 用 sqlite3 原生 backup API，WAL 模式下也是一致性快照。
- 默认保留最近 5 份，磁盘占用可控。
- XXM_TESTING=1 或 BACKUP_BEFORE_MIGRATIONS=0 时跳过，避免测试变慢/污染。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.core.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_KEEP = 5


def _sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    raw = database_url.split("///", 1)[1]
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return None
    return Path(raw)


def backup_before_migrate(keep: int | None = None) -> Path | None:
    """在迁移前复制一份数据库快照，返回备份路径；不满足条件时返回 None。"""
    if os.getenv("XXM_TESTING", "").strip() == "1":
        return None
    if os.getenv("BACKUP_BEFORE_MIGRATIONS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None

    src = _sqlite_path(settings.database_url)
    if src is None or not src.exists() or src.stat().st_size == 0:
        return None

    keep = _DEFAULT_KEEP if keep is None else max(1, keep)
    dst = src.with_name(f"{src.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        with closing(sqlite3.connect(str(src), timeout=30)) as source:
            with closing(sqlite3.connect(str(dst))) as target:
                source.backup(target)
        logger.info("数据库已备份: %s (%d bytes)", dst.name, dst.stat().st_size)
    except Exception:
        logger.exception("数据库备份失败，继续启动（迁移前请人工确认库文件）")
        dst.unlink(missing_ok=True)
        return None

    for old in sorted(src.parent.glob(f"{src.name}.bak-*"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            logger.warning("旧备份删除失败: %s", old)
    return dst
