import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution

@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for i in range(1, 5):
            db.add(Task(id=i, task_uid=str(i), taskname=f'任务{i}', task_type='drama', shareurl='https://x', savepath='/x', enabled=i != 2))
        db.flush()
        db.add_all([TaskExecution(id=1,task_id=1,status='success',run_log='中文😀结尾',adapter_snapshot='{}'), TaskExecution(id=2,task_id=1,status='failed',run_log='secret giant log',adapter_snapshot='{}')])
        db.commit()
        yield db
    engine.dispose()

def test_page_filters_before_pagination_and_no_large_columns(db):
    from app.application.queries.task_reads import page_tasks
    statements=[]
    event.listen(db.bind,'before_cursor_execute', lambda conn,cursor,statement,parameters,context,many: statements.append(statement))
    result=page_tasks(db,page=1,page_size=1,enabled=True)
    assert result['total']==3
    assert result['items'][0]['id']==4
    assert result['facets']['failed_count']==1
    assert not any('run_log' in sql or 'tree_summary' in sql for sql in statements)
    assert not any('task_executions.adapter_snapshot,' in sql for sql in statements)

def test_execution_detail_utf8_and_task_scope(db):
    from app.application.queries.task_executions import execution_detail
    offset=0
    chunks=[]
    for _ in range(20):
        detail=execution_detail(db,1,1,log_offset=offset,log_limit=4)
        chunks.append(detail['run_log'])
        if detail['log_complete']: break
        assert detail['log_next_offset'] > offset
        offset=detail['log_next_offset']
    assert ''.join(chunks)=='中文😀结尾'
    assert detail['log_complete']
    assert execution_detail(db,2,1) is None
    end=execution_detail(db,1,1,log_offset=9999)
    assert end['run_log']=='' and end['log_complete']

def test_execution_summary_omits_large_fields(db):
    from app.application.queries.task_executions import execution_page
    result=execution_page(db,1,page=1,page_size=1)
    assert result['total']==2
    assert result['items'][0]['id']==2
    assert 'run_log' not in result['items'][0]
    assert 'tree_summary' not in result['items'][0]

def test_page_selection_order_and_invalid_ids(db):
    from app.application.queries.task_reads import page_tasks,order_index,overview_tasks,detail_task
    result=page_tasks(db,page_size=2,ids=[1,4],enabled=True)
    assert [row['id'] for row in result['items']]==[1,4]
    with pytest.raises(ValueError): page_tasks(db,page_size=2,ids=[1,2],enabled=True)
    assert len(order_index(db,task_type='drama')['items'])==4
    assert len(overview_tasks(db))==4
    assert detail_task(db,999) is None

def test_summary_configuration_is_whitelisted_and_schedule_preserved(db):
    from app.application.queries.task_reads import page_tasks,detail_task
    task=db.get(Task,1)
    task.extra_json='{"runweek":[1],"runweek_mode":"manual","allow_once":true,"arbitrary":"hidden"}'
    task.addition_json='{"arbitrary":"hidden"}'
    db.commit()
    item=page_tasks(db,page_size=1,ids=[1])['items'][0]
    assert item['run_schedule'].mode=='allow_once'
    assert 'arbitrary' not in item['extra']
    assert 'arbitrary' not in item['addition']
    assert detail_task(db,1)['extra']['arbitrary']=='hidden'

def test_read_api_validation_and_response_models(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import tasks_read,task_executions
    from app.application.container import get_db
    app=FastAPI()
    app.include_router(tasks_read.router,prefix='/tasks')
    app.include_router(task_executions.router,prefix='/tasks')
    app.dependency_overrides[get_db]=lambda: db
    for router in (tasks_read.router,task_executions.router):
        app.dependency_overrides[router.dependencies[0].dependency]=lambda: None
    with TestClient(app) as client:
        assert client.get('/tasks/page?page_size=101').status_code==422
        assert client.get('/tasks/1/executions/1?log_limit=3').status_code==422
        response=client.get('/tasks/page?page_size=1')
        assert response.status_code==200,response.text
        assert len(response.json()['items'])==1
        assert client.get('/tasks/1/executions/1?log_limit=4').status_code==200

def test_historical_large_detail_reads_are_bounded_and_utf8(db):
    import json
    from app.application.queries.task_executions import execution_detail
    item=db.get(TaskExecution,1)
    item.run_log='😀'*100000
    item.tree_summary='中'*100000
    item.adapter_snapshot=json.dumps({'execution_log':{'truncated':True,'dropped_bytes':900},'tree_truncated':True,'secret':'ignored'})
    db.commit()
    statements=[]
    event.listen(db.bind,'before_cursor_execute',lambda c,cu,s,p,co,m: statements.append((s,p)))
    result=execution_detail(db,1,1)
    assert len(result['run_log'].encode('utf-8'))==65536
    assert len(result['tree_summary'].encode('utf-8'))<=65536
    assert result['log_next_offset']==65536 and not result['log_complete']
    assert result['log_truncated'] and result['log_dropped_bytes']==900
    assert result['tree_truncated']
    assert 'secret' not in result['adapter_snapshot']
    assert len(statements)==1
    assert 'substr(CAST(coalesce(task_executions.run_log' in statements[0][0]
    assert 65536 in statements[0][1]

def test_query_count_does_not_grow_with_page_size(db):
    from app.application.queries.task_reads import page_tasks
    counts=[]
    def record(*args): counts.append(1)
    event.listen(db.bind,'before_cursor_execute',record)
    page_tasks(db,page_size=1)
    small=len(counts)
    counts.clear()
    # Clear Session-level config caches so both measurements include them.
    db.info.clear()
    page_tasks(db,page_size=4)
    assert len(counts)<=small

def test_drive_filter_falls_back_to_share_host_and_counts_all_matches(db):
    from app.application.queries.task_reads import page_tasks
    db.get(Task,1).shareurl='https://pan.baidu.com/s/example'
    db.get(Task,3).shareurl='https://pan.baidu.com/s/second'
    db.commit()
    result=page_tasks(db,page_size=1,drive_type='baidu')
    assert result['total']==2
    assert result['items'][0]['drive_type']=='baidu'
    assert result['facets']['drives']==['baidu']

def test_category_and_failure_filters_apply_before_page(db):
    import json
    from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
    from app.application.queries.task_reads import page_tasks
    for task_id in (1,3):
        task=db.get(Task,task_id)
        task.tmdb_id=task_id
        task.tmdb_media_type='tv'
        db.add(TMDBMediaCache(tmdb_id=task_id,media_type='tv',payload_json=json.dumps({'genres':[{'id':16}],'origin_country':['JP']})))
    db.commit()
    result=page_tasks(db,page_size=1,media_category='动漫')
    assert result['total']==2
    assert result['items'][0]['id']==3
    assert result['items'][0]['media_category']=='动漫'
    assert result['facets']['categories']==['动漫', '未分类']
    failed=page_tasks(db,media_category='动漫',failed_only=True)
    assert failed['total']==1 and failed['items'][0]['id']==1
    assert failed['facets']['failed_count']==1

@pytest.mark.parametrize('filters', [
    {'media_category': '动漫'}, {'drive_type': 'baidu'}, {'enabled': False},
    {'q': '不存在的任务'}, {'failed_only': True},
    {'media_category': '动漫', 'drive_type': 'baidu', 'enabled': False},
])
def test_filter_choices_stay_available_even_when_results_are_empty(db, filters):
    import json
    from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
    from app.application.queries.task_reads import page_tasks
    task = db.get(Task, 1)
    task.shareurl = 'https://pan.baidu.com/s/example'
    task.tmdb_id = 1
    task.tmdb_media_type = 'tv'
    db.get(Task, 2).shareurl = 'https://pan.quark.cn/s/example'
    db.add(TMDBMediaCache(tmdb_id=1, media_type='tv', payload_json=json.dumps({'genres': [{'id': 16}]})))
    # Other task types must not add choices to the drama list.
    db.add(Task(task_uid='other', task_type='normal', taskname='其他任务', shareurl='https://alipan.com/s/other', savepath='/x'))
    db.commit()
    baseline = page_tasks(db, task_type='drama', page_size=1)
    filtered = page_tasks(db, task_type='drama', page_size=1, **filters)
    assert filtered['facets']['categories'] == baseline['facets']['categories']
    assert filtered['facets']['drives'] == baseline['facets']['drives']
    assert filtered['facets']['categories'] == ['动漫', '未分类']
    assert filtered['facets']['drives'] == ['baidu', 'quark']

def test_keyword_matches_automatic_account_display_label(db):
    from app.infrastructure.persistence.models.drive_account import DriveAccount
    from app.application.queries.task_reads import page_tasks
    db.add(DriveAccount(name='默认百度账号',drive_type='baidu',cookie='private',enabled=True,is_default=True))
    db.get(Task,1).shareurl='https://pan.baidu.com/s/example'
    db.commit()
    assert page_tasks(db,q='默认百度账号')['total']==1
    assert page_tasks(db,q='自动')['total']==4

def test_overview_retains_latest_success_older_than_three_failures(db):
    from datetime import datetime, timedelta
    from app.application.queries.task_reads import overview_tasks, page_tasks
    start=datetime(2026,1,1)
    db.get(TaskExecution,1).started_at=start
    db.get(TaskExecution,2).started_at=start+timedelta(days=1)
    db.add_all([TaskExecution(id=3,task_id=1,status='failed',started_at=start+timedelta(days=2)),TaskExecution(id=4,task_id=1,status='failed',started_at=start+timedelta(days=3))])
    db.commit()
    sql=[]
    event.listen(db.bind,'before_cursor_execute',lambda c,cu,s,p,co,m:sql.append(s))
    overview=next(row for row in overview_tasks(db) if row['id']==1)
    assert [item['id'] for item in overview['executions']]==[4,3,2,1]
    page=page_tasks(db,page_size=1,ids=[1])['items'][0]
    assert [item['id'] for item in page['executions']]==[4,3,2]
    assert all('run_log' not in statement and 'tree_summary' not in statement for statement in sql)

def test_overview_success_batch_query_count_is_independent_of_task_count(db):
    from app.application.queries.task_reads import overview_tasks
    statements=[]
    event.listen(db.bind,'before_cursor_execute',lambda c,cu,s,p,co,m:statements.append(s))
    overview_tasks(db)
    baseline=len(statements)
    db.add_all([Task(task_uid=str(i),taskname=f'task{i}',task_type='drama',shareurl='https://x',savepath='/x') for i in range(5,15)])
    db.commit()
    statements.clear()
    db.info.clear()
    overview_tasks(db)
    assert len(statements)<=baseline
