"""Run from the repository root with the backend Python environment."""

import asyncio
from datetime import datetime, timedelta
import math
import os
from pathlib import Path
import random
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx
from fastapi import FastAPI
from pydantic import TypeAdapter
from sqlalchemy import event


async def benchmark() -> None:
    with TemporaryDirectory(prefix="cloud-save-bench-") as temp:
        os.environ["XXM_TESTING"] = "1"
        os.environ["DATABASE_URL"] = "sqlite:///" + (Path(temp) / "bench.db").as_posix()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        from app.api.routes.tasks import get_tasks, router
        from app.db.base import Base
        from app.db.session import SessionLocal, engine
        from app.middlewares.gzip import GZipMiddleware
        from app.models.task import Task
        from app.models.task_execution import TaskExecution
        from app.schemas.task import TaskOut

        try:
            Base.metadata.create_all(engine)
            now = datetime(2026, 1, 1)
            rng = random.Random(42)
            with SessionLocal.begin() as db:
                for task_id in range(1, 201):
                    db.add(Task(id=task_id, task_uid=f"bench-{task_id}", taskname=f"Task {task_id}",
                                shareurl="https://example.invalid/s/test", savepath="/bench"))
                    for n in range(25):
                        line = f"task={task_id} execution={n} detail={rng.randbytes(64).hex()}\n"
                        db.add(TaskExecution(task_id=task_id, status="success", started_at=now + timedelta(hours=n),
                                             run_log=line * 40))

            app = FastAPI()
            app.include_router(router, prefix="/api/tasks")
            app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
            task_list = next(route for route in app.routes if getattr(route, "endpoint", None) is get_tasks)
            for dependency in task_list.dependant.dependencies:
                if dependency.name is None:
                    app.dependency_overrides[dependency.call] = lambda: None

            count = 0

            @event.listens_for(engine, "before_cursor_execute")
            def count_sql(*args):
                nonlocal count
                count += 1

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bench") as client:
                for indexed in (False, True):
                    with engine.begin() as conn:
                        if indexed:
                            conn.exec_driver_sql("CREATE INDEX bench_recent ON task_executions(task_id, started_at DESC)")
                        ids = ",".join(map(str, range(1, 201)))
                        plan = conn.exec_driver_sql(
                            "EXPLAIN QUERY PLAN SELECT id, row_number() OVER "
                            f"(PARTITION BY task_id ORDER BY started_at DESC) FROM task_executions WHERE task_id IN ({ids})"
                        ).all()
                    print("index", indexed, [row[-1] for row in plan])
                    adapter = TypeAdapter(list[TaskOut])
                    with SessionLocal() as db:
                        result = get_tasks(db)
                        started = perf_counter()
                        adapter.dump_json(result)
                        print("Pydantic serialization ms", round((perf_counter() - started) * 1000, 2))

                    async def request(encoding):
                        started = perf_counter()
                        response = await client.get("/api/tasks", headers={"Accept-Encoding": encoding})
                        response.raise_for_status()
                        return (perf_counter() - started) * 1000, response

                    for encoding in ("identity", "gzip"):
                        for concurrency in (1, 12):
                            await request(encoding)
                            start_count = count
                            times = []
                            for _ in range(12 if concurrency == 1 else 3):
                                batch = await asyncio.gather(*(request(encoding) for _ in range(concurrency)))
                                times.extend(time for time, _ in batch)
                            response = batch[-1][1]
                            assert len(response.json()) == 200 and len(response.json()[0]["executions"]) == 3
                            print(encoding, "concurrency", concurrency, "p50/p95 ms",
                                  round(median(times), 2), round(sorted(times)[math.ceil(len(times) * .95) - 1], 2),
                                  "SQL/request", (count - start_count) / len(times),
                                  "raw/wire bytes", len(response.content), response.headers["content-length"])
        finally:
            engine.dispose()


if __name__ == "__main__":
    asyncio.run(benchmark())
