#!/usr/bin/env python3
"""迁移预检：把「启动自动 upgrade」的不确定性提前变成可读报告。

背景：backend/app/main.py 在 lifespan 第一步执行 alembic upgrade head（默认
RUN_MIGRATIONS=1，无锁、无预检）。升级前应确认：库版本落后几个 revision、库多大、
-WAL 多大、freelist 比例（决定是否需要 VACUUM/重建）、最近备份是否新鲜。

用法（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/migration_preflight.py
    backend/.venv/Scripts/python.exe scripts/migration_preflight.py --fail-if-pending

退出码：0 = 报告输出成功；1 = 有未应用迁移且指定了 --fail-if-pending；2 = 工具/文件问题。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
DEFAULT_DB = BACKEND_ROOT / "data" / "app.db"


def _uri(db_path: Path) -> str:
    return f"{db_path.resolve().as_uri()}?mode=ro"


def _alembic_script():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def db_snapshot(db_path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(_uri(db_path), uri=True, timeout=30)) as con:
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            row = None
        current = row[0] if row else None
        page_count = con.execute("PRAGMA page_count").fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        freelist = con.execute("PRAGMA freelist_count").fetchone()[0]
        tables = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]

    wal_path = db_path.with_name(db_path.name + "-wal")
    backups = sorted(db_path.parent.glob(f"{db_path.name}.bak-*"))
    return {
        "db": db_path.resolve(),
        "size": db_path.stat().st_size,
        "wal_size": wal_path.stat().st_size if wal_path.exists() else 0,
        "page_count": page_count,
        "page_size": page_size,
        "freelist": freelist,
        "tables": tables,
        "current": current,
        "latest_backup": backups[-1] if backups else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Alembic / SQLite 迁移预检报告")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"数据库路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--fail-if-pending", action="store_true", help="有未应用迁移时退出码 1")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"[error] 数据库不存在: {args.db}", file=sys.stderr)
        return 2

    try:
        script = _alembic_script()
        head = script.get_current_head()
        history_count = sum(1 for _ in script.walk_revisions())
        snapshot = db_snapshot(args.db)
    except Exception as exc:  # noqa: BLE001 - 脚本入口
        print(f"[error] 预检失败: {exc}", file=sys.stderr)
        return 2

    current = snapshot["current"]
    if current is None:
        pending: int | None = None
    else:
        try:
            pending = sum(1 for _ in script.iterate_revisions(head, current))
        except Exception:  # noqa: BLE001 - 版本不在迁移历史里
            pending = None

    pages = max(int(snapshot["page_count"]), 1)
    freelist_pct = int(snapshot["freelist"]) / pages * 100
    latest_backup = snapshot["latest_backup"]
    backup_text = f"{Path(latest_backup).name}（{Path(latest_backup).stat().st_size:,} bytes）" if latest_backup else "无"

    print("=" * 78)
    print("migration preflight")
    print(f"  db          : {snapshot['db']}")
    print(f"  size        : {snapshot['size']:,} bytes")
    print(f"  wal         : {snapshot['wal_size']:,} bytes")
    print(f"  pages       : {snapshot['page_count']} x {snapshot['page_size']} bytes")
    print(f"  freelist    : {snapshot['freelist']} pages ({freelist_pct:.1f}%)")
    print(f"  tables      : {snapshot['tables']}")
    print(f"  alembic     : current={current or '(空库)'}  head={head}  pending={pending if pending is not None else 'unknown'}")
    print(f"  revisions   : {history_count}")
    print(f"  backup      : {backup_text}")
    if pending == 0:
        print("\nverdict: 已是最新 head，无需迁移")
    elif pending is None:
        print("\nverdict: 当前版本不在迁移历史中，禁止直接 upgrade（先人工比对）")
    else:
        print(f"\nverdict: 有 {pending} 个待应用迁移；启动时会自动 upgrade（确认已有备份）")

    if args.fail_if_pending and pending not in (0, None):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
