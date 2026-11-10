"""追剧任务「运行日期」（runweek）判定：执行器、任务列表、手动执行入口共用一套口径。

为什么单独成模块：判定逻辑原先内嵌在 drama_executor.execute() 的 validate_schedule 段，
前端只有点下「执行」、执行器跑完并把 status 落成 skipped 之后才知道今天该不该跑；而
任务列表要在点之前就禁用按钮、手动执行入口要在开线程之前就拒绝，三处口径必须一致：

- 执行器（scheduler 定时触发）：按判定结果决定 SkipTask；
- 执行器（手动点击触发，`task_data["manual_run"]=True`）：**无视运行星期直接执行**
  （用户要求「不到设定的日期也能点击执行，并且执行」），只保留配置性拦截（自动模式未绑定 TMDB TV / TMDB 未配置）；
- 任务列表（GET /api/tasks）：把判定结果下发给前端，用于展示「今天不在运行日期」的说明。

纯函数 `resolve_run_schedule()` 不碰数据库：TMDB 更新日由调用方从缓存行算好再传进来
（见 `resolve_item_run_schedule()`），这样列表、详情、执行器可以复用同一份判定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from app.application.dto.task import DramaRunScheduleOut

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# reason 是前后端与测试共同依赖的字符串常量，改名要同步改前端与 regression_tests。
REASON_NOT_DRAMA = "not_drama"
REASON_ALLOW_ONCE = "allow_once"
REASON_AUTO_NOT_TV = "auto_not_tv"
REASON_TMDB_NOT_CONFIGURED = "tmdb_not_configured"
REASON_NO_UPDATE_DAY = "no_update_day"
REASON_NO_RUNWEEK = "no_runweek"
REASON_INVALID_RUNWEEK = "invalid_runweek"
REASON_DUE = "due"
REASON_OFF_DAY = "off_day"

# 手动执行（点按钮 / Telegram / 试运行）可以越过的「日期类」拦截。
# 用户要求：不到设定的日期也能点击执行，并且真的执行；配置类问题（未绑定 TMDB TV、TMDB 未配置）
# 不在其中——那是数据没准备好，放行也只会空跑。
MANUAL_OVERRIDE_REASONS = frozenset({REASON_OFF_DAY, REASON_INVALID_RUNWEEK})


@dataclass(frozen=True)
class RunSchedule:
    """一条追剧任务的运行日期判定结果。

    allowed      今天能否执行
    mode         always / allow_once / manual / auto
    weekdays     生效的运行星期（1=周一 … 7=周日）
    reason       机器可读原因（见 REASON_* 常量）
    message      给用户看的中文说明（列表提示、执行日志、409 文案共用）
    skip_reason  执行器 SkipTask 沿用的旧文案（allowed=True 时为空串，保持历史行为）
    """

    allowed: bool
    mode: str
    weekdays: tuple[int, ...]
    reason: str
    message: str
    skip_reason: str = ""

    def to_out(self) -> DramaRunScheduleOut:
        return DramaRunScheduleOut(
            allowed=self.allowed,
            mode=self.mode,
            weekdays=list(self.weekdays),
            reason=self.reason,
            message=self.message,
        )


def normalize_weekdays(value: Any) -> tuple[int, ...]:
    """把任意输入规整成 1..7 的升序去重元组（与执行器历史容错口径一致）。"""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return ()
    if not isinstance(value, Iterable):
        return ()
    days: set[int] = set()
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 7:
            days.add(number)
    return tuple(sorted(days))


def weekday_label(days: Iterable[int]) -> str:
    out: list[str] = []
    for day in normalize_weekdays(list(days)):
        out.append(WEEKDAY_LABELS[day - 1])
    return "、".join(out)


def _today_label(now: datetime) -> str:
    return WEEKDAY_LABELS[now.isoweekday() - 1]


def _due_or_off(days: tuple[int, ...], now: datetime, mode: str) -> RunSchedule:
    label = weekday_label(days)
    today = _today_label(now)
    if now.isoweekday() in days:
        return RunSchedule(
            allowed=True,
            mode=mode,
            weekdays=days,
            reason=REASON_DUE,
            message=f"今天是运行日期：{today}（{label}）",
        )
    return RunSchedule(
        allowed=False,
        mode=mode,
        weekdays=days,
        reason=REASON_OFF_DAY,
        message=f"今天（{today}）不在设定的运行日期（{label}）内",
        skip_reason="不在允许运行的星期范围内",
    )


def resolve_run_schedule(
    *,
    task_type: str | None,
    extra: dict[str, Any] | None,
    now: datetime | None = None,
    tmdb_id: int | None = None,
    tmdb_media_type: str | None = None,
    tmdb_configured: bool = False,
    episode_weekdays: Iterable[int] | None = None,
    update_weekdays: Iterable[int] | None = None,
) -> RunSchedule:
    """纯判定：给定任务配置与（自动模式下的）TMDB 更新日，判断今天能不能跑。

    口径与改造前的 drama_executor.validate_schedule 段逐条对齐：
    1. 非追剧任务不限制；2. extra.allow_once 忽略限制；3. auto 模式用 TMDB 更新日
    （优先单集播出日，其次整季更新日，都识别不到就按每天）；4. manual 模式用 runweek，
    未配置按每天，配置了但全部非法 → 阻止并沿用「未配置运行星期」文案。
    """
    current = now or datetime.now()
    if str(task_type or "") != "drama":
        return RunSchedule(
            allowed=True,
            mode="always",
            weekdays=(),
            reason=REASON_NOT_DRAMA,
            message="普通任务不受运行日期限制",
        )

    config = extra if isinstance(extra, dict) else {}
    if bool(config.get("allow_once")):
        return RunSchedule(
            allowed=True,
            mode="allow_once",
            weekdays=(),
            reason=REASON_ALLOW_ONCE,
            message="任务已设置运行一次，忽略运行日期限制",
        )

    mode = str(config.get("runweek_mode") or "manual").strip().lower()
    if mode == "auto":
        try:
            bound_id = int(tmdb_id or 0)
        except (TypeError, ValueError):
            bound_id = 0
        media_type = str(tmdb_media_type or "").strip().lower()
        if bound_id <= 0 or media_type != "tv":
            return RunSchedule(
                allowed=False,
                mode="auto",
                weekdays=(),
                reason=REASON_AUTO_NOT_TV,
                message="运行日期为自动模式，需要任务绑定 TMDB 电视剧",
                skip_reason="未绑定 TMDB TV",
            )
        if not tmdb_configured:
            return RunSchedule(
                allowed=False,
                mode="auto",
                weekdays=(),
                reason=REASON_TMDB_NOT_CONFIGURED,
                message="TMDB 未配置，无法自动识别更新日",
                skip_reason="TMDB 未配置",
            )
        days = normalize_weekdays(list(episode_weekdays or [])) or normalize_weekdays(list(update_weekdays or []))
        if not days:
            return RunSchedule(
                allowed=True,
                mode="auto",
                weekdays=(),
                reason=REASON_NO_UPDATE_DAY,
                message="未识别到更新日，按每天运行",
            )
        return _due_or_off(days, current, "auto")

    raw = config.get("runweek")
    if not raw:
        return RunSchedule(
            allowed=True,
            mode="manual",
            weekdays=(),
            reason=REASON_NO_RUNWEEK,
            message="未设置运行星期，按每天运行",
        )
    days = normalize_weekdays(raw)
    if not days:
        return RunSchedule(
            allowed=False,
            mode="manual",
            weekdays=(),
            reason=REASON_INVALID_RUNWEEK,
            message="运行星期设置为空或无效，已阻止执行",
            skip_reason="未配置运行星期",
        )
    return _due_or_off(days, current, "manual")


def _load_extra(item: Any) -> dict[str, Any]:
    raw = getattr(item, "extra_json", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cached_tmdb_weekdays(
    db: Any, tmdb_id: int, *, payload: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, tuple[int, ...]]:
    """从 TMDB 缓存行读「单集播出日 / 整季更新日」，不触发任何网络刷新。

    读不到缓存（刚绑定的任务）时返回空 → 判定退化成「按每天运行」（fail-open），
    宁可让按钮可点、由执行器再按实时数据跳过一次，也不要因为缓存缺失把用户锁死。
    """
    from app.application.container import tmdb_media_cache_repo
    from app.application.tmdb_cache import infer_tv_episode_weekdays_from_details
    from app.application.tmdb_settings import get_tmdb_lang_pair

    language, poster_language = get_tmdb_lang_pair(db)
    row = tmdb_media_cache_repo().get_latest_schedule_row(
        db,
        media_type="tv",
        tmdb_id=int(tmdb_id),
        language=language,
        poster_language=poster_language,
    )
    details = payload
    stored_update: tuple[int, ...] = ()
    if row is not None:
        payload_raw = getattr(row, "payload_json", None)
        update_raw = getattr(row, "update_weekdays_json", None)
        if details is None and payload_raw:
            try:
                parsed = json.loads(payload_raw)
            except (TypeError, ValueError):
                parsed = None
            details = parsed if isinstance(parsed, dict) else None
        if update_raw:
            try:
                loaded = json.loads(update_raw)
            except (TypeError, ValueError):
                loaded = None
            stored_update = normalize_weekdays(loaded)
    episodes = infer_tv_episode_weekdays_from_details(details) if isinstance(details, dict) else []
    return details, normalize_weekdays(episodes) or stored_update


def resolve_item_run_schedule(
    db: Any,
    item: Any,
    *,
    now: datetime | None = None,
    payload: dict[str, Any] | None = None,
) -> DramaRunScheduleOut:
    """任务行（ORM 对象或视图）→ 运行日期判定结果（最多多 1 次缓存查询，绝不发网络请求）。"""
    extra = _load_extra(item)
    task_type = str(getattr(item, "task_type", "") or "")
    if task_type != "drama" or bool(extra.get("allow_once")):
        return resolve_run_schedule(task_type=task_type, extra=extra, now=now).to_out()

    mode = str(extra.get("runweek_mode") or "manual").strip().lower()
    if mode != "auto":
        return resolve_run_schedule(task_type=task_type, extra=extra, now=now).to_out()

    from app.application.tmdb_settings import is_tmdb_configured

    try:
        tmdb_id = int(getattr(item, "tmdb_id", None) or 0)
    except (TypeError, ValueError):
        tmdb_id = 0
    configured = bool(is_tmdb_configured(db))
    episode_weekdays: tuple[int, ...] = ()
    if configured and tmdb_id > 0:
        _, episode_weekdays = _cached_tmdb_weekdays(db, tmdb_id, payload=payload)
    return resolve_run_schedule(
        task_type=task_type,
        extra=extra,
        now=now,
        tmdb_id=tmdb_id,
        tmdb_media_type=getattr(item, "tmdb_media_type", None),
        tmdb_configured=configured,
        episode_weekdays=episode_weekdays,
    ).to_out()


def manual_override_allows(decision: RunSchedule) -> bool:
    """手动执行是否放行。

    放行：判定本来就允许的；以及「日期类」拦截（今天不在运行日、星期配置无效）——
    用户点「执行」就是明确表示「现在就跑」，不再拿运行星期拦人。
    不放行：自动模式下未绑定 TMDB TV / TMDB 未配置 key，这类是配置缺失，跑起来也没有排期可用。
    """
    return bool(decision.allowed) or decision.reason in MANUAL_OVERRIDE_REASONS


def requires_skip(decision: RunSchedule, manual_run: bool) -> bool:
    """执行器是否应该 SkipTask（唯一口径，避免执行器自己再拼一遍条件）。

    定时轮询（manual_run=False）：判定不允许就跳过，全量沿用旧行为；
    手动执行（manual_run=True）：日期类拦截忽略，配置类缺失仍然跳过。
    """
    return not decision.allowed and not (manual_run and manual_override_allows(decision))
