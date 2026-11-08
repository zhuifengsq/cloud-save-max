#!/usr/bin/env python3
"""schema 双真相门禁雏形：库表集合 vs ORM 模型表集合（只读）。

背景（docs/architecture/audit-data-observability.md §2.1）：实库 37 张表、ORM 26 张，
10 张无模型遗留表（plugin_configs/plugin_definitions 有真实数据）+ 7 个迁移静默跳过。
任何人都可能「改了模型忘了迁移」或「迁移建了表但模型已删」，测试用
Base.metadata.create_all 建库、从不跑 alembic，所以这个偏差无人发现。

本脚本以只读方式（mode=ro）打开数据库，报告两个方向：
- db_only   ：库里有、模型没有（遗留表 / 迁移多建）
- model_only：模型有、库里没有（忘了迁移 / 迁移被静默跳过）

用法（仓库根目录）：
    backend/.venv/Scripts/python.exe scripts/check_schema_drift.py
    backend/.venv/Scripts/python.exe scripts/check_schema_drift.py --ignore-file scripts/schema_drift_allowlist.txt
    backend/.venv/Scripts/python.exe scripts/check_schema_drift.py --ignore plugin_configs --json

退出码：0 = 无偏差（或全部被 --ignore 显式豁免）；1 = 有偏差；2 = 参数/文件问题。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
DEFAULT_DB = BACKEND_ROOT / "data" / "app.db"
IGNORED = {"alembic_version"}


def _uri(db_path: Path) -> str:
    return f"{db_path.resolve().as_uri()}?mode=ro"


def db_tables(db_path: Path) -> list[str]:
    with sqlite3.connect(_uri(db_path), uri=True, timeout=30) as con:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [row[0] for row in rows if row[0] not in IGNORED]


def model_tables() -> list[str]:
    # 延迟把 backend 加入 sys.path：脚本从仓库根目录运行，app 包不在默认路径上。
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    from app.infrastructure.persistence.base import Base  # noqa: E402
    import app.infrastructure.persistence.models  # noqa: E402,F401  显式导入以注册全部模型（db.base 为破环已不代为导入）

    return sorted(Base.metadata.tables)


def main() -> int:
    parser = argparse.ArgumentParser(description="ORM 模型与 SQLite 库的 schema 漂移检查")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"数据库路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--ignore", action="append", default=[], help="显式豁免的表名（可重复）")
    parser.add_argument(
        "--ignore-file",
        type=Path,
        help="每行一个豁免表名（# 开头为注释）；与 --ignore 合并，用于 CI 维护遗留表白名单",
    )
    parser.add_argument("--json", metavar="PATH", help="把报告写成 JSON 文件")
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"[error] 数据库不存在: {args.db}", file=sys.stderr)
        return 2

    ignored = set(IGNORED) | set(args.ignore)
    if args.ignore_file:
        if not args.ignore_file.is_file():
            print(f"[error] 豁免清单不存在: {args.ignore_file}", file=sys.stderr)
            return 2
        for raw_line in args.ignore_file.read_text(encoding="utf-8").splitlines():
            name = raw_line.split("#", 1)[0].strip()
            if name:
                ignored.add(name)
    try:
        in_db = [t for t in db_tables(args.db) if t not in ignored]
        in_models = [t for t in model_tables() if t not in ignored]
    except Exception as exc:  # noqa: BLE001 - 脚本入口，统一转成退出码
        print(f"[error] 读取 schema 失败: {exc}", file=sys.stderr)
        return 2

    db_only = sorted(set(in_db) - set(in_models))
    model_only = sorted(set(in_models) - set(in_db))
    report = {
        "db": str(args.db.resolve()),
        "db_tables": len(in_db),
        "model_tables": len(in_models),
        "db_only": db_only,
        "model_only": model_only,
        "ignored": sorted(ignored),
    }

    print("=" * 78)
    print(f"db_tables={len(in_db)}  model_tables={len(in_models)}  drift={len(db_only) + len(model_only)}")
    if db_only:
        print(f"\ndb_only（库里有、模型没有，共 {len(db_only)} 张）:")
        for name in db_only:
            print(f"  - {name}")
    if model_only:
        print(f"\nmodel_only（模型有、库里没有，共 {len(model_only)} 张）:")
        for name in model_only:
            print(f"  - {name}")
    if not db_only and not model_only:
        print("\n[ok] 模型与数据库表集合一致")

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\njson report -> {args.json}")

    return 1 if (db_only or model_only) else 0


if __name__ == "__main__":
    sys.exit(main())
