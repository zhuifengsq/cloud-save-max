"""Isolated deterministic read benchmark; opt in through bench_task_list --bounded."""
import gc
import gzip
import json
import math
import os
from pathlib import Path
from random import Random
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc


def benchmark():
    with TemporaryDirectory(prefix='cloud-save-bounded-bench-') as temp:
        os.environ.update(XXM_TESTING='1', DATABASE_URL='sqlite:///'+(Path(temp)/'bench.db').as_posix(), RUN_MIGRATIONS='0', SCHEDULER_ENABLED='0')
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
        from datetime import datetime,timedelta
        from sqlalchemy import insert,event
        from app.infrastructure.persistence.session import engine,SessionLocal
        from app.infrastructure.persistence.base import Base
        from app.infrastructure.persistence.models.task import Task
        from app.infrastructure.persistence.models.task_execution import TaskExecution
        from app.application.queries.task_reads import page_tasks
        from app.application.queries.task_views import list_task_views
        from app.api.presenters.task import task_out
        from app.schemas.task_reads import TaskPageOut
        Base.metadata.create_all(engine)  # isolated benchmark, never a production schema source
        rng=Random(731); now=datetime(2026,11,13); statements=[]
        @event.listens_for(engine,'before_cursor_execute')
        def record(conn,cursor,statement,*args):
            if statement.lstrip().upper().startswith(('SELECT','WITH')): statements.append(statement)
        results=[]
        previous=0
        for count in (200,2000):
            with engine.begin() as con:
                con.execute(insert(Task),[dict(id=i,task_uid=f'bench-{i}',taskname=f'Task {i}',task_type='drama',shareurl='https://pan.quark.cn/s/bench',savepath='/bench',enabled=True,extra_json='{}',addition_json='{}') for i in range(previous+1,count+1)])
                rows=[]
                for i in range(previous+1,count+1):
                    for k in range(20):
                        rows.append(dict(task_id=i,status='success',started_at=now+timedelta(seconds=k),run_log=rng.randbytes(16384).hex(),adapter_snapshot='{}'))
                    if len(rows)>=200:
                        con.execute(insert(TaskExecution),rows); rows=[]
                if rows: con.execute(insert(TaskExecution),rows)
            previous=count
            measurements={}
            for label in ('old','page'):
                times=[]; sql_counts=[]; payload=b''; selected=b''
                tracemalloc.start()
                for _ in range(3):
                    statements.clear(); start=perf_counter()
                    with SessionLocal() as db:
                        if label=='old':
                            models=[task_out(v) for v in list_task_views(db)]
                            payload=json.dumps([m.model_dump(mode='json') for m in models],ensure_ascii=False).encode()
                            selected=json.dumps([m.model_dump(mode='json') for m in models[:20]],ensure_ascii=False).encode()
                            del models
                        else:
                            payload=TaskPageOut(**page_tasks(db,page=1,page_size=20)).model_dump_json().encode()
                            assert all('run_log' not in q and 'tree_summary' not in q for q in statements)
                    times.append((perf_counter()-start)*1000); sql_counts.append(len(statements));gc.collect()
                _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
                measurements[label]=dict(p50_ms=round(median(times),2),p95_ms=round(max(times),2),sql=sql_counts[-1],raw_bytes=len(payload),gzip_bytes=len(gzip.compress(payload)),peak_python_bytes=peak,corresponding_20_bytes=len(selected) if label=='old' else len(payload))
            row=dict(tasks=count,history_per_task=20,log_bytes=32768,measurements=measurements)
            reduction=1-measurements['page']['raw_bytes']/measurements['old']['corresponding_20_bytes']
            row['corresponding_payload_reduction']=round(reduction,4);assert reduction>=.8
            results.append(row);print(json.dumps(row),flush=True)
        engine.dispose()
        report=Path(__file__).resolve().parents[1]/'.superpowers/sdd/backend-hardening-taskbook/benchmark.json'
        report.write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__': benchmark()
