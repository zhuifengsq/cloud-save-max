"""Pure-Python regression tests for drama progress guards, settings, and task admission waiting."""
from __future__ import annotations

import threading
import time
from datetime import date

import pytest

from app.application.drama_update_progress import build_drama_update_progress
from app.core.errors import ApiError
from app.core.settings import settings
from app.domain.tmdb import is_saved_latest_caught_up, resolve_tmdb_latest_aired_episode
from app.infrastructure.scheduling.task_admission import TaskAdmission


def test_cross_season_missing_saved_season_not_marked_latest() -> None:
    tmdb_details = {
        "seasons": [
            {
                "season_number": 2,
                "episodes": [
                    {"episode_number": 1, "air_date": "2026-10-01"},
                    {"episode_number": 3, "air_date": "2026-10-15"},
                ],
            }
        ],
        "last_episode_to_air": {"season_number": 2, "episode_number": 3, "air_date": "2026-10-15"},
    }
    out = build_drama_update_progress(
        tmdb_details=tmdb_details,
        saved_latest={"season": None, "episode": 24, "at": "2026-10-16T00:00:00+08:00"},
        today=date(2026, 10, 20),
    )
    assert out.available is False
    assert out.is_latest is None
    assert out.reason == "缺少已存季号，无法确认当前季进度"
    assert not is_saved_latest_caught_up(
        tmdb_season=2, tmdb_episode=3, saved_season=None, saved_episode=24
    )


def test_season_one_without_saved_season_still_tracks_progress() -> None:
    tmdb_details = {
        "seasons": [
            {
                "season_number": 1,
                "episodes": [
                    {"episode_number": 1, "air_date": "2026-10-01"},
                    {"episode_number": 2, "air_date": "2026-10-08"},
                    {"episode_number": 3, "air_date": "2026-11-30"},
                ],
            }
        ],
        "last_episode_to_air": {"season_number": 1, "episode_number": 2, "air_date": "2026-10-08"},
    }
    s, e, _ = resolve_tmdb_latest_aired_episode(tmdb_details, today=date(2026, 10, 20))
    assert (s, e) == (1, 2)
    out = build_drama_update_progress(
        tmdb_details=tmdb_details,
        saved_latest={"season": None, "episode": 2},
        today=date(2026, 10, 20),
    )
    assert out.available is True
    assert out.is_latest is True
    assert out.behind_episodes == 0


def test_task_admission_wait_for_capacity_succeeds_and_times_out() -> None:
    admission = TaskAdmission(max_active=1)
    held = admission.reserve(100)

    def _release_soon() -> None:
        time.sleep(0.05)
        held.release()

    worker = threading.Thread(target=_release_soon, daemon=True)
    worker.start()
    acquired = admission.wait_for_capacity(101, timeout=1.0)
    worker.join(timeout=1.0)
    try:
        with pytest.raises(ApiError) as exc_info:
            admission.wait_for_capacity(102, timeout=0.05)
        assert exc_info.value.code == "TASK_CAPACITY_EXCEEDED"
    finally:
        acquired.release()


def test_scheduler_capacity_settings_have_safe_defaults() -> None:
    assert settings.drama_task_max_active >= 1
    assert settings.drama_scheduler_capacity_wait_seconds >= 0.0


def test_baidu_classify_share_failure_auth_precedes_invalid() -> None:
    from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter

    adapter = object.__new__(BaiduAdapter)
    assert adapter.classify_share_failure({"code": -6, "message": "Cookie已失效"})["kind"] == "auth"
    assert adapter.classify_share_failure({"message": "请重新登录，Cookie已失效"})["kind"] == "auth"
    assert adapter.classify_share_failure({"code": -12, "message": "分享已失效"})["kind"] == "invalid"


def test_retry_utils_stops_on_non_transient_auth_and_non_retryable_result() -> None:
    from app.infrastructure.execution.retry_utils import RetryResult, is_transient_error, retry_call

    assert is_transient_error(payload={"status": 500, "message": "Cookie已失效"}) is False
    assert is_transient_error(payload={"status": 500, "message": "百度网盘客户端未初始化"}) is False
    assert is_transient_error(payload={"status": 503, "message": "Service Unavailable"}) is True

    attempts = 0

    def _call() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        return {"status": 503, "message": "bad response"}

    with pytest.raises(RuntimeError):
        retry_call(
            action="test",
            fn=_call,
            validate=lambda r: RetryResult(value=r, ok=False, error_message="permanent", retryable=False),
            attempts=3,
            backoff_seconds=0.0,
        )
    assert attempts == 1


def test_baidu_rename_batch_chunks_and_propagates_errors() -> None:
    from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter

    adapter = object.__new__(BaiduAdapter)
    adapter._session = object()
    adapter._resolve_fid_to_path = lambda fid: str(fid)  # type: ignore[method-assign]
    adapter._get_error_message = lambda errno: f"err:{errno}"  # type: ignore[method-assign]
    batches: list[list[dict[str, str]]] = []

    def _operate(op: str, params: list[dict[str, str]]) -> dict[str, int]:
        assert op == "move"
        batches.append(params)
        return {"errno": 0}

    adapter._api_file_operate = _operate  # type: ignore[method-assign]
    res = adapter.rename_batch(
        [("/show/a.mp4", "E01.mp4"), ("/show/b.mp4", "E02.mp4"), ("/show/c.mp4", "E03.mp4")],
        chunk_size=2,
    )
    assert res == {"code": 0, "message": "success"}
    assert len(batches) == 2
    assert batches[0] == [
        {"from": "/show/a.mp4", "to": "/show/E01.mp4"},
        {"from": "/show/b.mp4", "to": "/show/E02.mp4"},
    ]
    assert batches[1] == [{"from": "/show/c.mp4", "to": "/show/E03.mp4"}]

    adapter._api_file_operate = lambda op, params: {"errno": -9}  # type: ignore[method-assign]
    err_res = adapter.rename_batch([("/show/a.mp4", "E01.mp4")])
    assert err_res == {"code": -9, "message": "err:-9"}
