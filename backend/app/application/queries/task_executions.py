"""Execution read queries, independent of HTTP schemas."""

from app.application.container import execution_reads_repo, task_reads_repo


def task_exists(db, task_id):
    return task_reads_repo().task_exists(db, task_id)


def execution_page(db, task_id, *, page=1, page_size=20):
    return execution_reads_repo().read_page(db, task_id, page=page, page_size=page_size)


def execution_detail(db, task_id, execution_id, *, log_offset=0, log_limit=65536):
    return execution_reads_repo().read_detail(
        db, task_id, execution_id, log_offset=log_offset, log_limit=log_limit
    )
