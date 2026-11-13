from dataclasses import dataclass


@dataclass(frozen=True)
class CreateTaskShareCommand:
    task_id: int
    actor_user_id: int | None
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class TaskShareResult:
    share_url: str
    share_id: str
    title: str
    drive_type: str
    account_name: str
    passcode: str | None
    expires_in_days: int | None
    saved_at: str
