"""提交归属与旁路会话的预算护栏（S3 收尾）。

背景：S3 之前，路由层直接 `db.commit()` 89 处，事务边界散落在 HTTP 处理器、服务、
后台线程里，出问题无法判断「谁该为这条写入负责」。S3 把 **请求级** 提交全部上移之后，
剩下的提交点不是"没来得及改"，而是四类有意为之的边界：

1. `infra`：`app/infrastructure/persistence/session.py`（S5 前为 `app/db/session.py`）里 commit 原语自身的实现（commit_with_locked_retry /
   end_read_tx），是"提供提交能力的人"，不算调用方。
2. `app-lifecycle`：`app/main.py` 的 lifespan 初始化（权限种子、执行记录清理）、
   `app/scripts/*` 的运维脚本：进程级一次性写入，没有请求上下文可依附。
3. `background-owned`：调度器 / 执行器 / Telegram 轮询线程自己拥有工作单元
   （task_scheduler / task_executor / telegram_bot_manager）。
4. `progressive`：Telegram 每次 update 一个工作单元（handlers / actions，入口提交 +
   中间渐进持久化，保证卡片能刷新、崩溃后可恢复）；以及长 IO 服务里的"短事务落盘"
   （drama_lead_in 的清理与 _store_result、share_preview_batch 的 `_short_tx`）。

这个测试是**只降不升的预算**：新增文件出现提交点会失败（迫使作者显式登记归属），
已有文件的提交数增加会失败，路由层出现提交点会失败。它不判断业务对错，只保证
"每一处提交都有人认领"。
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# file(相对 app/) -> (预算, 归属类别, 为什么必须由它提交)
COMMIT_BUDGET: dict[str, tuple[int, str, str]] = {
    "infrastructure/persistence/session.py": (1, "infra", "commit_with_locked_retry 的实现本体"),
    "core/runtime/tx.py": (1, "infra", "end_read_tx 的实现本体（platform 层，零 ORM 依赖）"),
    "main.py": (2, "app-lifecycle", "lifespan: 权限种子初始化、启动时执行记录清理"),
    "scripts/ensure_admin.py": (1, "app-lifecycle", "运维脚本：创建管理员后落盘"),
    "scripts/init_db.py": (1, "app-lifecycle", "运维脚本：初始化建表后落盘"),
    "infrastructure/scheduling/task_scheduler.py": (14, "background-owned", "后台调度任务各自拥有工作单元"),
    "infrastructure/execution/task_executor.py": (1, "background-owned", "自动换链每轮独立落盘，刷新卡片且不占写锁"),
    "infrastructure/notifications/telegram_bot_manager.py": (1, "background-owned", "轮询线程启停状态落盘"),
    "application/telegram_bot/handlers.py": (50, "progressive", "每次 update 一个工作单元，入口提交+中间渐进持久化"),
    "application/telegram_bot/actions.py": (20, "progressive", "同上，动作执行后立即持久化以便卡片刷新"),
    "application/drama_lead_in.py": (2, "progressive", "清理过期记录、_store_result 保存记录并隔离异常"),
    "application/share_preview_batch.py": (1, "progressive", "_short_tx 使用独立会话，避免拖长调用方的读事务"),
}

TOTAL_BUDGET = 95

# 允许直接实例化 SessionLocal 的文件（旁路会话必须登记理由；路由层只允许 SSE 流）。
SESSION_LOCAL_ALLOWED: dict[str, str] = {
    "application/container.py": "容器 db_session() 的唯一出口",
    "infrastructure/persistence/session.py": "SessionLocal 定义处",
    "main.py": "lifespan 启动期写入",
    "scripts/ensure_admin.py": "运维脚本",
    "scripts/init_db.py": "运维脚本",
    "infrastructure/scheduling/task_scheduler.py": "后台调度任务，无请求会话可依附",
    "infrastructure/notifications/telegram_bot_manager.py": "轮询线程，无请求会话可依附",
    "application/share_preview_batch.py": "_short_tx 短事务，刻意与调用方读事务隔离",
    "api/routes/tasks_stream.py": "SSE 生成器跨越请求生命周期，需独立会话",
    "api/deps.py": "get_current_user_scoped 的独立会话（S5 从 core/deps.py 迁入）",
}


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _rel(path: Path) -> str:
    return path.relative_to(APP_DIR).as_posix()


def _count_commits(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "commit":
            total += 1
    return total


def _commit_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _iter_py_files(APP_DIR):
        count = _count_commits(path)
        if count:
            counts[_rel(path)] = count
    return counts


def _session_local_files() -> set[str]:
    found = set()
    for path in _iter_py_files(APP_DIR):
        if "SessionLocal(" in path.read_text(encoding="utf-8"):
            found.add(_rel(path))
    return found


def test_routes_have_no_raw_commit() -> None:
    """请求级事务边界必须在路由层之上：路由只允许 commit(db)/transaction(db)。"""
    offenders = {
        rel: count
        for rel, count in _commit_counts().items()
        if rel.startswith("api/routes/")
    }
    assert offenders == {}, (
        "路由层出现裸 db.commit()，请改用 app.infrastructure.persistence.session 的 commit()/transaction()："
        f"{offenders}"
    )


def test_commit_counts_stay_within_registered_budget() -> None:
    actual = _commit_counts()
    unregistered = sorted(set(actual) - set(COMMIT_BUDGET))
    assert unregistered == [], (
        "以下文件新增了未登记归属的提交点，请在 COMMIT_BUDGET 里显式登记类别与理由："
        f"{ {rel: actual[rel] for rel in unregistered} }"
    )
    over_budget = {
        rel: (actual[rel], COMMIT_BUDGET[rel][0])
        for rel in actual
        if actual[rel] > COMMIT_BUDGET[rel][0]
    }
    assert over_budget == {}, f"提交点超出预算（只降不升）：{over_budget}"
    stale = sorted(set(COMMIT_BUDGET) - set(actual))
    assert stale == [], f"预算里登记但已无提交点的文件，请同步收缩预算：{stale}"
    assert sum(actual.values()) <= TOTAL_BUDGET, (
        f"提交点总数 {sum(actual.values())} 超过预算 {TOTAL_BUDGET}"
    )


def test_session_local_usage_is_whitelisted() -> None:
    actual = _session_local_files()
    unregistered = sorted(actual - set(SESSION_LOCAL_ALLOWED))
    assert unregistered == [], (
        "以下文件直接实例化 SessionLocal（旁路会话），请登记理由或改用容器 db_session()："
        f"{unregistered}"
    )
    stale = sorted(set(SESSION_LOCAL_ALLOWED) - actual)
    assert stale == [], f"白名单里登记但已不再直接使用 SessionLocal 的文件：{stale}"
