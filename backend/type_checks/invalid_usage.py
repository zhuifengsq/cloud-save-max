"""Deliberately invalid calls: the type gate must reject every marked line.

Checked separately by scripts/check_backend_types.py; never run or imported.
"""

from sqlalchemy.orm import Session
from app.application.drive_accounts import create_drive_account, get_drive_account
from app.domain.ports.task import (
    TaskExecutionEnginePort,
    TaskRunStorePort,
    TaskRunUseCasePort,
)
from app.domain.task_run import (
    ExecutionLogPort,
    RunPolicyPort,
    TaskExecutionState,
    TaskRunPlan,
)
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.session import transaction


def bad_recovery(
    task: TaskExecutionState, log: ExecutionLogPort, tried: set[str] | None
) -> str:
    return "yes"


def invalid_calls(
    db: Session,
    store: TaskRunStorePort,
    engine: TaskExecutionEnginePort,
    usecase: TaskRunUseCasePort,
    plan: TaskRunPlan,
    policy: RunPolicyPort,
    row: DriveAccount,
) -> None:
    create_drive_account(db, name=123, drive_type="quark")  # expect-error: arg-type
    account = get_drive_account(db, 1)
    account.enabled = "yes"  # expect-error: assignment
    account.set_credentials(cookie=123, config_json=None)  # expect-error: arg-type
    store.prepare("task")  # expect-error: arg-type
    store.save(plan, "success")  # expect-error: arg-type
    engine.execute(plan, policy, None, bad_recovery)  # expect-error: arg-type
    usecase.execute("task", policy)  # expect-error: arg-type
    row.enabled = "yes"  # expect-error: assignment
    with transaction("db"):  # expect-error: arg-type
        pass


from app.application.repository_ports import (
    ResourceSearchSourcesRepository,
    TasksRepository,
)
from app.application.use_cases.run_task import RunTaskUseCase


def invalid_repository_calls(
    db: Session, sources: ResourceSearchSourcesRepository, tasks: TasksRepository
) -> None:
    sources.create_resource_search_source(
        db, key="x", enabled="yes", config_json="{}"  # expect-error: arg-type
    )
    sources.find_unknown_source(db)  # expect-error: attr-defined
    tasks.get_task_row(db, "task")  # expect-error: arg-type
    RunTaskUseCase(db)  # expect-error: call-arg
