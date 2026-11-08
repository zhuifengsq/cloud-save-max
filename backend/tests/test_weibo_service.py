from __future__ import annotations

import pytest

pytest.skip(
    "weibo 功能已从代码库删除（app.services.weibo_images / app.models.weibo_worker 不存在），测试待重写；" 
    "见 docs/architecture-evolution-plan.md 的 S0 项",
    allow_module_level=True,
)

import os
import tempfile
import unittest
import uuid
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
import app.models  # noqa: F401
from app.models.weibo_worker import WeiboWorker
from app.schemas.weibo import (
    WeiboTaskCancelIn,
    WeiboTaskCreateIn,
    WeiboTaskRetryIn,
    WorkerCompleteIn,
    WorkerReportIn,
)
from app.services.weibo_tasks import (
    STATUS_CANCELLED,
    STATUS_COMMENT_DONE,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_PENDING,
    STATUS_POST_DONE,
    STATUS_REPLY_DONE,
    cancel_task,
    claim_task,
    complete_task,
    create_task,
    expire_stale_leases,
    get_task,
    report_step,
    retry_task,
    utcnow,
)


class WeiboServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.worker = WeiboWorker(
            worker_id="worker_test_1",
            name="Test Worker",
            token_hash="hash-" + uuid.uuid4().hex,
            token_hint="wbp_test",
            enabled=True,
        )
        self.worker2 = WeiboWorker(
            worker_id="worker_test_2",
            name="Second Worker",
            token_hash="hash-" + uuid.uuid4().hex,
            token_hint="wbp_test2",
            enabled=True,
        )
        self.db.add_all([self.worker, self.worker2])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _payload(self, **overrides):
        data = dict(
            resource_id="resource_1",
            title="测试剧集",
            post_text="测试剧集 更8集 4K高码",
            first_comment="宝宝们自取点点赞收藏",
            second_reply="夸：https://pan.quark.cn/s/abc\n百：https://pan.baidu.com/s/def",
            quark_url="https://pan.quark.cn/s/abc",
            baidu_url="https://pan.baidu.com/s/def",
            idempotency_key=uuid.uuid4().hex,
        )
        data.update(overrides)
        return WeiboTaskCreateIn(**data)

    def _create_claimed(self):
        task, created = create_task(self.db, self._payload())
        self.db.commit()
        self.assertTrue(created)
        claimed = claim_task(self.db, self.worker)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.task_uid, task.task_uid)
        return claimed

    def test_create_is_idempotent_and_validates_reply(self):
        payload = self._payload()
        first, created = create_task(self.db, payload)
        self.db.commit()
        second, created_again = create_task(self.db, payload)
        self.assertFalse(created_again)
        self.assertEqual(first.task_uid, second.task_uid)
        with self.assertRaises(Exception):
            create_task(
                self.db,
                self._payload(
                    idempotency_key=uuid.uuid4().hex,
                    second_reply="夸：https://pan.quark.cn/s/abc",
                    baidu_url="https://pan.baidu.com/s/def",
                ),
            )

    def test_claim_is_atomic_between_workers(self):
        task = self._create_claimed()
        self.assertEqual(task.status, "claimed")
        self.assertIsNone(claim_task(self.db, self.worker2))

    def test_full_flow_and_duplicate_post_guard(self):
        task = self._create_claimed()
        result = report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="started",
            evidence={"submission_started": True},
        ))
        self.assertEqual(result["status"], "posting")
        result = report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="succeeded",
            post_id="123", post_url="https://weibo.com/1/abc", evidence={"composer_closed": True},
        ))
        self.assertEqual(result["status"], STATUS_POST_DONE)

        task = get_task(self.db, task.id)
        with self.assertRaises(Exception):
            report_step(self.db, self.worker, WorkerReportIn(
                task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="succeeded",
                post_id="999", post_url="https://weibo.com/1/other",
            ))

        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="comment", state="started",
            evidence={"submission_started": True},
        ))
        result = report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="comment", state="succeeded",
            comment_id="c_1", evidence={"comment_text": "宝宝们自取点点赞收藏"},
        ))
        self.assertEqual(result["status"], STATUS_COMMENT_DONE)

        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="reply", state="started",
            evidence={"submission_started": True},
        ))
        result = report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="reply", state="succeeded",
            reply_id="r_1", reply_verified_nested=True,
            evidence={"nested_text": "夸：...", "parent_comment_text": "宝宝们自取点点赞收藏"},
        ))
        self.assertEqual(result["status"], STATUS_REPLY_DONE)
        result = complete_task(self.db, self.worker, WorkerCompleteIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "",
            post_id="123", post_url="https://weibo.com/1/abc", comment_id="c_1", reply_id="r_1",
            reply_verified_nested=True, evidence={"nested_selector": ".comment-item .reply"},
        ))
        self.assertEqual(result["status"], STATUS_COMPLETED)
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_COMPLETED)
        self.assertEqual(task.post_url, "https://weibo.com/1/abc")

    def test_retry_comment_does_not_repeat_post(self):
        task = self._create_claimed()
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="started",
            evidence={"submission_started": True},
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="succeeded",
            post_id="123", post_url="https://weibo.com/1/abc",
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="comment", state="started",
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="comment", state="failed",
            error_code="COMMENT_FAILED", error_message="评论失败",
            evidence={"submission_started": False},
        ))
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_FAILED)
        retry_task(self.db, task.id, WeiboTaskRetryIn(reason="retry comment"))
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_PENDING)
        self.assertEqual(task.current_step, "comment")
        self.assertEqual(task.post_url, "https://weibo.com/1/abc")
        claimed = claim_task(self.db, self.worker2)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.task_uid, task.task_uid)

    def test_retry_reply_does_not_repeat_post_or_comment(self):
        task = self._create_claimed()
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="started",
            evidence={"submission_started": True},
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="succeeded",
            post_id="123", post_url="https://weibo.com/1/abc",
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="comment", state="started",
            evidence={"submission_started": True},
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="comment", state="succeeded",
            comment_id="c_1", evidence={"comment_text": "宝宝们自取点点赞收藏"},
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="reply", state="started",
            evidence={"submission_started": False},
        ))
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="reply", state="failed",
            error_code="REPLY_FAILED", error_message="二级回复失败",
            evidence={"submission_started": False},
        ))
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_FAILED)
        self.assertEqual(task.current_step, "reply")
        retry_task(self.db, task.id, WeiboTaskRetryIn(reason="retry reply"))
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_PENDING)
        self.assertEqual(task.current_step, "reply")
        self.assertEqual(task.post_url, "https://weibo.com/1/abc")
        self.assertEqual(task.comment_id, "c_1")
        claimed = claim_task(self.db, self.worker2)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.task_uid, task.task_uid)

    def test_unknown_post_requires_confirmation_and_marks_review(self):
        task = self._create_claimed()
        report_step(self.db, self.worker, WorkerReportIn(
            task_uid=task.task_uid, lease_id=task.lease_id or "", step="post", state="started",
            evidence={"submission_started": True},
        ))
        task = get_task(self.db, task.id)
        task.lease_expires_at = utcnow() - timedelta(seconds=1)
        self.db.commit()
        changed = expire_stale_leases(self.db, now=utcnow())
        self.db.commit()
        self.assertGreaterEqual(changed, 1)
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_NEEDS_REVIEW)
        with self.assertRaises(Exception):
            retry_task(self.db, task.id, WeiboTaskRetryIn(confirm_review=False))
        retry_task(self.db, task.id, WeiboTaskRetryIn(confirm_review=True))
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_PENDING)
        self.assertEqual(task.current_step, "post")

    def test_cancel_is_terminal(self):
        task = self._create_claimed()
        cancel_task(self.db, task.id, WeiboTaskCancelIn(reason="stop"))
        task = get_task(self.db, task.id)
        self.assertEqual(task.status, STATUS_CANCELLED)


if __name__ == "__main__":
    unittest.main()
