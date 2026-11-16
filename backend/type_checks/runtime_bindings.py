"""Static composition checks against real implementations, without casts.

This file is checked by mypy and is never imported by the running application.
"""
from app.application.drive_account_types import AccountAdapterFactory, AccountRegistry, AccountRepository
from app.domain.ports.drive_account import DriveAccountRecord
from app.domain.ports.task import TaskExecutionEnginePort, TaskRunStorePort
from app.domain.task_run import TaskExecutionState
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.execution.task_run_stages import PreparedTaskEngine, SqlTaskRunStore
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.repositories import drive_accounts
from sqlalchemy.orm import Session


def check_bindings(db: Session, account: DriveAccount, task: Task) -> None:
    repository: AccountRepository = drive_accounts
    factory: AccountAdapterFactory = AdapterFactory
    registry: AccountRegistry = AdapterRegistry
    record: DriveAccountRecord = account
    execution_state: TaskExecutionState = task
    store: TaskRunStorePort = SqlTaskRunStore(db)
    engine: TaskExecutionEnginePort = PreparedTaskEngine()
    # Keep names live for Ruff too; none of these checks execute at runtime.
    assert repository and factory and registry and record and execution_state and store and engine
