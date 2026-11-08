#!/usr/bin/env python3
"""SQLite 备份 + 校验（stdlib only，零新依赖）。

为什么需要独立脚本：应用启动时的热备（backend/app/db/backup.py）只在自动迁移前
触发；运维手动升级、回滚演练、清库前都需要一次「备份 + 校验 + 解释」。
本脚本复用 sqlite3 原生 backup API，并立刻对产物做 integrity_check / quick_check，
避免出现「备份文件存在但已损坏」的假安心。产物会切成 DELETE journal 模式，
保证备份是单文件快照；--keep 只按主备份文件计数，不会误删成只剩 -wal/-shm。

用法（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/backup_db.py
    backend/.venv/Scripts/python.exe scripts/backup_db.py --db backend/data/app.db --keep 10

退出码：0 = 备份且校验通过；1 = 备份/校验失败；2 = 参数或文件问题。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "backend" / "data" / "app.db"


def _uri(db_path: Path, mode: str) -> str:
    return f"{db_path.resolve().as_uri()}?mode={mode}"


def _open(db_path: Path, mode: str = "ro") -> sqlite3.Connection:
    return sqlite3.connect(_uri(db_path, mode), uri=True, timeout=30)


def verify(path: Path) -> dict[str, object]:
    """对备份产物做完整性检查（只读打开）。"""
    with closing(_open(path)) as con:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        tables = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    return {"integrity_check": integrity, "quick_check": quick, "tables": tables}


def backup(db_path: Path, keep: int) -> tuple[Path, dict[str, object]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    if db_path.stat().st_size == 0:
        raise ValueError(f"数据库为空文件: {db_path}")

    dst = db_path.with_name(f"{db_path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    seq = 1
    while dst.exists():
        dst = db_path.with_name(f"{db_path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{seq}")
        seq += 1

    with closing(_open(db_path, "ro")) as source, closing(sqlite3.connect(str(dst))) as target:
        source.backup(target)
        # 产物默认沿用源库的 WAL 头；切成 DELETE 模式，让备份是「单文件、可直接覆盖」
        # 的自包含快照，避免 -wal/-shm 旁文件被误当成独立备份或漏拷。
        target.execute("PRAGMA journal_mode=DELETE")
    return dst, verify(dst)


_BACKUP_SUFFIXES = ("", "-wal", "-shm", "-journal")


def prune(db_path: Path, keep: int) -> list[Path]:
    """只把主备份文件算作一份；删除时连同 -wal/-shm/-journal 旁文件一起删。

    之前的 glob 会把 *.bak-xxx-wal / *.bak-xxx-shm 当成独立备份，导致
    「保留最近 N 份」时把主文件删掉、只剩旁文件。
    """
    prefix = f"{db_path.name}.bak-"
    backups = sorted(
        p
        for p in db_path.parent.iterdir()
        if p.name.startswith(prefix) and not p.name.endswith(("-wal", "-shm", "-journal"))
    )
    removed = backups[:-keep] if keep > 0 else backups
    for old in removed:
        for suffix in _BACKUP_SUFFIXES:
            Path(f"{old}{suffix}").unlink(missing_ok=True)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 备份 + 校验")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"数据库路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--keep", type=int, default=5, help="保留最近 N 份备份（默认 5）")
    args = parser.parse_args()

    if args.keep < 1:
        print("[error] --keep 必须 >= 1", file=sys.stderr)
        return 2
    try:
        dst, info = backup(args.db, args.keep)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"[error] 备份失败: {exc}", file=sys.stderr)
        return 1

    src_size = args.db.stat().st_size
    dst_size = dst.stat().st_size
    print(f"[backup] source : {args.db.resolve()} ({src_size:,} bytes)")
    print(f"[backup] target : {dst} ({dst_size:,} bytes)")
    print(f"[check]  integrity_check={info['integrity_check']}  quick_check={info['quick_check']}  tables={info['tables']}")
    removed = prune(args.db, args.keep)
    if removed:
        print(f"[prune]  删除旧备份 {len(removed)} 份，保留最近 {args.keep} 份")
    else:
        print(f"[prune]  无需删除（保留最近 {args.keep} 份）")

    if info["integrity_check"] != "ok" or info["quick_check"] != "ok":
        print("[error] 备份校验未通过，请勿用于回滚", file=sys.stderr)
        return 1
    print("[ok] 备份可用于回滚（先停服务，再用备份覆盖 app.db 并删除 -wal/-shm）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
