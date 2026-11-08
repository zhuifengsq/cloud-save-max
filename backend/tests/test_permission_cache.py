from __future__ import annotations

import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.deps import _load_permissions, invalidate_permission_cache
from app.core.settings import settings
from app.db.base import Base
from app.models.permission import Permission
from app.models.role import Role
from app.models.user import User
from app.services.roles import set_role_permissions


class PermissionCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.permission_task_read = Permission(code="task:read", name="任务读取")
        self.permission_audit_read = Permission(code="audit:read", name="审计读取")
        role = Role(name="admin")
        role.permissions = [self.permission_task_read]
        self.user = User(username="cache-user", email="cache@example.com", hashed_password="x", is_active=True)
        self.user.roles = [role]
        self.db.add_all([self.permission_task_read, self.permission_audit_read, role, self.user])
        self.db.flush()
        self.role_id = int(role.id)

        self.query_count = 0
        event.listen(self.engine, "before_cursor_execute", self._count_query)
        self._old_ttl = settings.permission_cache_ttl_seconds
        settings.permission_cache_ttl_seconds = 30
        invalidate_permission_cache()

    def tearDown(self) -> None:
        settings.permission_cache_ttl_seconds = self._old_ttl
        invalidate_permission_cache()
        self.db.close()
        self.engine.dispose()

    def _count_query(self, *args, **kwargs) -> None:
        self.query_count += 1

    def test_second_call_hits_cache(self) -> None:
        self.query_count = 0
        roles, permissions = _load_permissions(self.db, int(self.user.id))
        self.assertEqual(roles, ["admin"])
        self.assertEqual(permissions, {"task:read"})
        self.assertEqual(self.query_count, 2)

        self.query_count = 0
        again_roles, again_permissions = _load_permissions(self.db, int(self.user.id))
        self.assertEqual(again_roles, ["admin"])
        self.assertEqual(again_permissions, {"task:read"})
        self.assertEqual(self.query_count, 0)

    def test_invalidate_after_role_permissions_change(self) -> None:
        _load_permissions(self.db, int(self.user.id))

        set_role_permissions(self.db, role_id=self.role_id, permission_ids=[int(self.permission_audit_read.id)])
        self.db.flush()

        _, permissions = _load_permissions(self.db, int(self.user.id))
        self.assertEqual(permissions, {"audit:read"})

    def test_mutating_returned_values_does_not_poison_cache(self) -> None:
        roles, permissions = _load_permissions(self.db, int(self.user.id))
        roles.append("hacked")
        permissions.add("hacked")

        again_roles, again_permissions = _load_permissions(self.db, int(self.user.id))
        self.assertEqual(again_roles, ["admin"])
        self.assertEqual(again_permissions, {"task:read"})

    def test_ttl_zero_disables_cache(self) -> None:
        settings.permission_cache_ttl_seconds = 0
        invalidate_permission_cache()

        self.query_count = 0
        for _ in range(3):
            _load_permissions(self.db, int(self.user.id))
        self.assertEqual(self.query_count, 6)


if __name__ == "__main__":
    unittest.main()
