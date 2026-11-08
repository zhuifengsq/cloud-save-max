# -*- coding: utf-8 -*-
"""审计日志读模型（application/queries/audit_logs）的视图与响应映射护栏。

路由原先内联把 (AuditLog, username) 拼成响应模型；改造后拆成「查询层出视图 →
presenter 出响应」。本测试锁住：字段映射（尤其 success 布尔化、username join）、
倒序与分页 total，避免以后又把拼装逻辑搬回路由。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.presenters.audit_log import audit_log_list
from app.application.queries.audit_logs import list_audit_log_views
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.audit_log import AuditLog
from app.infrastructure.persistence.models.user import User


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'audit.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(User(id=1, username="admin", email="admin@example.com", hashed_password="x", is_active=True))
    db.add(AuditLog(id=1, actor_user_id=1, action="task.create", target_type="task", target_id="7", success=True, detail="detail-1"))
    db.add(AuditLog(id=2, actor_user_id=None, action="task.delete", target_type="task", target_id="8", success=False, detail="detail-2"))
    db.commit()
    return db


def test_list_audit_log_views_maps_fields_and_order(tmp_path):
    db = _session(tmp_path)
    try:
        views, total = list_audit_log_views(db, page=1, page_size=10)
    finally:
        db.close()

    assert total == 2
    assert [view.id for view in views] == [2, 1], "按 id 倒序（最新在前）"
    newest, oldest = views
    assert newest.actor_username is None, "匿名操作 username 为空"
    assert newest.success is False
    assert oldest.actor_username == "admin"
    assert oldest.success is True
    assert oldest.action == "task.create"


def test_audit_log_presenter_keeps_response_contract(tmp_path):
    db = _session(tmp_path)
    try:
        views, total = list_audit_log_views(db, page=1, page_size=1)
    finally:
        db.close()

    payload = audit_log_list(views, page=1, page_size=1, total=total).model_dump(mode="json")
    assert payload["page"] == 1 and payload["page_size"] == 1 and payload["total"] == 2
    assert len(payload["items"]) == 1
    assert list(payload["items"][0].keys()) == [
        "id",
        "actor_user_id",
        "actor_username",
        "action",
        "target_type",
        "target_id",
        "ip",
        "user_agent",
        "success",
        "detail",
        "created_at",
    ]
    assert payload["items"][0]["id"] == 2
