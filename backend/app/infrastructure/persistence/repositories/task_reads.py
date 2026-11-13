"""Narrow task projections for paginated and global views."""

from sqlalchemy import LargeBinary, cast, select, func, case, or_
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from sqlalchemy.orm import aliased

EXTRA_KEYS = (
    "saved_latest",
    "auto_update_shareurl",
    "auto_update_115_shareurl",
    "share_link",
    "runweek",
    "runweek_mode",
    "allow_once",
)
ADDITION_KEYS = ("update_subdir", "save_subdir", "weekdays")


def safe_json(column):
    return case((func.json_valid(column), column), else_="{}")


def json_projection(column, keys):
    args = []
    for key in keys:
        args.extend((key, func.json_extract(safe_json(column), "$." + key)))
    return func.json_object(*args)


def drive_expression():
    hosts = {
        "quark": ("pan.quark.cn",),
        "115": ("115.com", "anxia.com", "115cdn.com"),
        "baidu": ("pan.baidu.com",),
        "xunlei": ("pan.xunlei.com",),
        "aliyun": ("alipan.com", "aliyundrive.com"),
        "uc": ("drive.uc.cn",),
        "123pan": (
            "123pan.com",
            "123865.com",
            "123684.com",
            "123952.com",
            "123912.com",
        ),
        "cloud189": ("cloud.189.cn",),
        "cloud139": ("yun.139.com", "caiyun.139.com"),
        "guangya": ("guangyapan.com",),
    }
    inferred = case(
        *[
            (or_(*(Task.shareurl.contains(host) for host in domains)), drive)
            for drive, domains in hosts.items()
        ],
        else_=""
    )
    return func.coalesce(DriveAccount.drive_type, inferred)


def candidates(
    db,
    *,
    q="",
    task_type=None,
    enabled=None,
    drive_type=None,
    language="zh-CN",
    poster_language="zh-CN"
):
    latest = (
        select(TaskExecution.status)
        .where(TaskExecution.task_id == Task.id)
        .order_by(TaskExecution.started_at.desc(), TaskExecution.id.desc())
        .limit(1)
        .correlate(Task)
        .scalar_subquery()
    )
    media = TMDBMediaCache
    payload = json_projection(
        media.payload_json,
        ("genres", "origin_country", "production_countries", "original_language"),
    )
    stmt = (
        select(
            Task.id,
            Task.taskname,
            Task.tmdb_id,
            Task.tmdb_media_type,
            Task.task_type,
            drive_expression().label("drive_type"),
            latest.label("latest_status"),
            payload.label("category_payload"),
        )
        .outerjoin(DriveAccount, DriveAccount.name == Task.account_name)
        .outerjoin(
            media,
            (media.tmdb_id == Task.tmdb_id)
            & (media.media_type == Task.tmdb_media_type)
            & (media.language == language)
            & (media.poster_language == poster_language),
        )
    )
    if q:
        automatic = aliased(DriveAccount)
        automatic_name = (
            select(automatic.name)
            .where(
                automatic.enabled == True, automatic.drive_type == drive_expression()
            )
            .order_by(automatic.is_default.desc(), automatic.id.asc())
            .limit(1)
            .correlate(Task, DriveAccount)
            .scalar_subquery()
        )
        drive_label = case(
            (drive_expression() != "", "（" + drive_expression() + "）"),
            else_="",
        )
        account_label = case(
            (
                func.trim(func.coalesce(Task.account_name, "")) != "",
                Task.account_name + drive_label,
            ),
            else_=case(
                (automatic_name.is_not(None), "自动：" + automatic_name + drive_label),
                else_="自动" + drive_label,
            ),
        )
        stmt = stmt.where(
            or_(
                *(
                    column.contains(q, autoescape=True)
                    for column in (
                        Task.taskname,
                        Task.shareurl,
                        Task.savepath,
                        account_label,
                    )
                )
            )
        )
    if task_type:
        stmt = stmt.where(Task.task_type == task_type)
    if enabled is not None:
        stmt = stmt.where(Task.enabled == enabled)
    if drive_type:
        stmt = stmt.where(drive_expression() == drive_type)
    return [dict(row) for row in db.execute(stmt.order_by(Task.id.desc())).mappings()]


def read_tasks(db, ids, *, full=False):
    if not ids:
        return []
    columns = [
        column
        for column in Task.__table__.c
        if column.name not in ("extra_json", "addition_json")
    ]
    # Historical malformed/oversized configured values must not bypass summary bounds.
    # The application parser treats an incomplete JSON projection as an empty config.
    extra = func.substr(
        cast(json_projection(Task.extra_json, EXTRA_KEYS), LargeBinary), 1, 16384
    ).label("extra_json")
    addition = func.substr(
        cast(json_projection(Task.addition_json, ADDITION_KEYS), LargeBinary), 1, 4096
    ).label("addition_json")
    columns.extend(
        (
            Task.extra_json if full else extra,
            Task.addition_json if full else addition,
            drive_expression().label("drive_type"),
        )
    )
    return [
        dict(row)
        for row in db.execute(
            select(*columns)
            .outerjoin(DriveAccount, DriveAccount.name == Task.account_name)
            .where(Task.id.in_(ids))
        ).mappings()
    ]


def task_exists(db, task_id):
    return db.scalar(select(Task.id).where(Task.id == task_id)) is not None
