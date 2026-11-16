"""Streaming iteration may run each next() in a distinct context/thread."""

import asyncio
from types import SimpleNamespace

from starlette.concurrency import iterate_in_threadpool
from app.application import share_preview_batch as batch
from app.application.share_validation import ShareLinkValidator


def test_stream_can_finish_through_starlette_threadpool(monkeypatch):
    monkeypatch.setattr(batch, "_update_validate_cache_ttl", lambda: None)
    batch.cache_clear()

    async def consume():
        return [
            item
            async for item in iterate_in_threadpool(
                batch.validate_share_links_streaming(
                    None, ["https://example.invalid/s/fake"]
                )
            )
        ]

    items = asyncio.run(consume())
    assert len(items) == 1 and not items[0].ok


def test_validation_releases_manager_if_read_release_fails():
    events = []
    manager = SimpleNamespace(close=lambda: events.append("closed"))
    accounts = SimpleNamespace(
        list_enabled_by_type=lambda *a, **kw: [SimpleNamespace(name="fake")]
    )

    def release():
        raise RuntimeError("read release failed")

    import pytest

    with pytest.raises(RuntimeError, match="read release failed"):
        list(
            ShareLinkValidator().stream(
                None,
                ["https://pan.quark.cn/s/fake"],
                registry=SimpleNamespace(detect_drive_type=lambda _: "quark"),
                accounts=accounts,
                manager_factory=lambda *a, **kw: manager,
                read_setting=lambda *a: "5",
                release_read=release,
            )
        )
    assert events == ["closed"]
