"""Bounded background TMDB refresh execution and reader-session ownership."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session


class TMDBRefreshBackground:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._jobs: set[tuple[Engine | Connection, tuple[str, int, str, str]]] = set()
        self._lock = Lock()

    def submit(self, db: Session, job: tuple[str, int, str, str], refresh: Callable[[Session], None]) -> bool:
        bind = db.get_bind()
        identity = (bind, job)

        def run() -> None:
            try:
                with Session(bind=bind) as reader:
                    refresh(reader)
            finally:
                with self._lock:
                    self._jobs.discard(identity)

        with self._lock:
            if identity in self._jobs:
                return True
            if len(self._jobs) >= 16:
                return False
            self._jobs.add(identity)
            try:
                self._executor.submit(run)
            except RuntimeError:
                self._jobs.discard(identity)
                return False
        return True
