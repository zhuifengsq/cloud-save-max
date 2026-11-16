"""Preserve background capacity, deduplication and session cleanup during extraction."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.infrastructure.background import tmdb_refresh


class QueuedExecutor:
    def __init__(self):
        self.callbacks = []
        self.reject = False

    def submit(self, callback):
        if self.reject:
            raise RuntimeError("executor stopped")
        self.callbacks.append(callback)


@pytest.fixture
def background(monkeypatch):
    executor = QueuedExecutor()
    monkeypatch.setattr(tmdb_refresh, "ThreadPoolExecutor", lambda **kwargs: executor)
    return tmdb_refresh.TMDBRefreshBackground(), executor


def test_background_deduplicates_per_bind_and_limits_queued_jobs(background):
    runner, executor = background
    engine = create_engine("sqlite://")
    other_engine = create_engine("sqlite://")
    try:
        with Session(engine) as db, Session(other_engine) as other:
            job = ("movie", 1, "zh-CN", "zh-CN")
            assert runner.submit(db, job, lambda reader: None)
            assert runner.submit(db, job, lambda reader: None)
            assert len(executor.callbacks) == 1
            assert runner.submit(other, job, lambda reader: None)
            for number in range(2, 16):
                assert runner.submit(db, ("movie", number, "zh-CN", "zh-CN"), lambda reader: None)
            assert not runner.submit(db, ("movie", 99, "zh-CN", "zh-CN"), lambda reader: None)
            executor.callbacks[0]()
            assert runner.submit(db, job, lambda reader: None)
    finally:
        engine.dispose()
        other_engine.dispose()


def test_failed_background_fetch_closes_its_reader_and_releases_job(background):
    runner, executor = background
    engine = create_engine("sqlite://")
    readers = []

    def fail(reader):
        readers.append(reader)
        reader.execute(text("select 1"))
        raise ValueError("fetch failed")

    try:
        with Session(engine) as db:
            job = ("tv", 1, "zh-CN", "zh-CN")
            assert runner.submit(db, job, fail)
            with pytest.raises(ValueError, match="fetch failed"):
                executor.callbacks[0]()
            assert readers[0] is not db
            assert not readers[0].in_transaction()
            assert runner.submit(db, job, fail)
    finally:
        engine.dispose()


def test_submission_rejection_releases_job_for_retry(background):
    runner, executor = background
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as db:
            job = ("tv", 1, "zh-CN", "zh-CN")
            executor.reject = True
            assert not runner.submit(db, job, lambda reader: None)
            executor.reject = False
            assert runner.submit(db, job, lambda reader: None)
            assert len(executor.callbacks) == 1
    finally:
        engine.dispose()
