"""任务租约：状态机、租约数学与 worker 可用的队列接口（契约 §3，CT-11）。

**事务边界（契约 §3「幂等与事务」）**

外部模型调用必须发生在短事务之外：

1. :meth:`TaskQueue.claim_next` 在一个短事务内选取任务、写入租约并提交；
2. worker 退出该事务后才调用模型/外部服务；禁止在持有数据库事务（更不用说
   行锁）的情况下等待外部调用；
3. 结果写入用另一个短事务完成：:meth:`TaskQueue.complete` 或
   :meth:`TaskQueue.fail`，并以租约持有者做条件更新。

这样模型超时或进程崩溃都不会长期占用连接或行锁；租约到期后任务可被其他
worker 重新领取，而旧 worker 的写入会被 :class:`LeaseLostError` 拒绝，避免
重复最终结果（CT-11）。

纯函数（``can_claim`` / ``lease_deadline`` / ``next_status_after_failure``）
只负责判定，持久化与并发由 :class:`TaskQueue` 通过 ``SELECT ... FOR UPDATE
SKIP LOCKED`` 完成；多 worker 场景不依赖进程内锁。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from bank_assistant.config.settings import settings
from bank_assistant.infra.db import get_session_factory
from bank_assistant.infra.models import Task
from bank_assistant.observability.metrics import TASK_EVENTS

__all__ = [
    "LeaseLostError",
    "MAX_ATTEMPTS_ERROR_CODE",
    "TaskQueue",
    "TaskStatus",
    "can_claim",
    "is_lease_active",
    "lease_deadline",
    "next_status_after_failure",
]

# 超过重试上限时写入的稳定错误码；worker 崩溃留下的过期租约也用它终结。
MAX_ATTEMPTS_ERROR_CODE = "MAX_ATTEMPTS_EXCEEDED"


class TaskStatus(StrEnum):
    """契约 §3 的质检任务状态；``cancelled`` 只能由明确取消写入。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LeaseLostError(RuntimeError):
    """worker 不再持有有效租约：不得续租、写结果或覆盖已有终态。"""


def lease_deadline(now: datetime, lease_seconds: int) -> datetime:
    """返回租约到期时刻；租约必须是正数秒，避免“立即过期”的隐性配置错误。"""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds 必须为正整数")
    return now + timedelta(seconds=lease_seconds)


def is_lease_active(expires_at: datetime | None, now: datetime) -> bool:
    """租约在到期前有效；``None`` 表示当前没有有效租约。"""
    return expires_at is not None and expires_at > now


def can_claim(
    status: TaskStatus | str,
    lease_expires_at: datetime | None,
    attempts: int,
    max_attempts: int,
    now: datetime,
) -> bool:
    """判定任务此刻能否被 worker 领取。

    - ``queued``：可领取，但达到尝试上限时不再领取（由 sweep 终结为 failed）；
    - ``running``：只有租约过期才可重领（崩溃恢复）；
    - 终态不可领取。
    """
    if attempts >= max_attempts:
        return False
    current = TaskStatus(status)
    if current is TaskStatus.QUEUED:
        return True
    if current is TaskStatus.RUNNING:
        return not is_lease_active(lease_expires_at, now)
    return False


def next_status_after_failure(
    attempts: int,
    max_attempts: int,
    retryable: bool,
) -> TaskStatus:
    """失败后仍可重试且未达上限 → 重排队；否则终结为 failed。"""
    if retryable and attempts < max_attempts:
        return TaskStatus.QUEUED
    return TaskStatus.FAILED


def _require_short_text(value: str | None, *, field: str, limit: int) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} 不能为空")
    if len(value) > limit:
        raise ValueError(f"{field} 超过 {limit} 字符上限")
    return value


class TaskQueue:
    """基于数据库租约的任务队列。

    每次调用都开启并提交自己的短事务（默认使用进程会话工厂）；测试可注入
    ``session_factory``、``max_attempts`` 与 ``now`` 来验证边界，而无需 sleep。
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        max_attempts: int | None = None,
        now: Callable[[], datetime] | None = None,
        default_lease_seconds: int | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._max_attempts = (
            settings.TASK_MAX_ATTEMPTS if max_attempts is None else max_attempts
        )
        if self._max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        self._now = now or (lambda: datetime.now(UTC))
        self._default_lease_seconds = (
            settings.TASK_DEFAULT_LEASE_SECONDS
            if default_lease_seconds is None
            else default_lease_seconds
        )
        if self._default_lease_seconds <= 0:
            raise ValueError("default_lease_seconds 必须为正整数")

    @contextmanager
    def _session(self) -> Iterator[Session]:
        """独立短事务：成功提交，异常回滚，最后关闭（不跨外部调用）。"""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _sweep_exhausted(self, session: Session, task_type: str, now: datetime) -> None:
        """把达到尝试上限、又无法再领取的任务终结为 failed。

        覆盖两类：租约已过期的 ``running``（worker 崩溃在最后一次尝试）和
        无租约的 ``queued``（异常数据，防御性终结）。
        """
        result = session.execute(
            update(Task)
            .where(
                Task.type == task_type,
                Task.attempts >= self._max_attempts,
                or_(
                    Task.status == TaskStatus.QUEUED.value,
                    and_(
                        Task.status == TaskStatus.RUNNING.value,
                        or_(
                            Task.lease_expires_at.is_(None),
                            Task.lease_expires_at < now,
                        ),
                    ),
                ),
            )
            .values(
                status=TaskStatus.FAILED.value,
                error_code=MAX_ATTEMPTS_ERROR_CODE,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        if result.rowcount:
            TASK_EVENTS.inc(
                {"event": "sweep", "task_type": task_type}, result.rowcount
            )

    def claim_next(
        self,
        task_type: str,
        lease_seconds: int | None = None,
        *,
        owner: str,
    ) -> Task | None:
        """领取该类型下一个可运行任务并写入租约；没有可领取任务时返回 ``None``。

        使用 ``FOR UPDATE SKIP LOCKED``：两个 worker 并发时只有一个能拿到同
        一行，另一个直接跳过，而不是等待或在应用层加锁。
        """
        _require_short_text(task_type, field="task_type", limit=64)
        _require_short_text(owner, field="owner", limit=128)
        seconds = (
            self._default_lease_seconds if lease_seconds is None else lease_seconds
        )
        now = self._now()
        deadline = lease_deadline(now, seconds)

        with self._session() as session:
            self._sweep_exhausted(session, task_type, now)
            task = session.scalar(
                select(Task)
                .where(
                    Task.type == task_type,
                    Task.attempts < self._max_attempts,
                    or_(
                        Task.status == TaskStatus.QUEUED.value,
                        and_(
                            Task.status == TaskStatus.RUNNING.value,
                            or_(
                                Task.lease_expires_at.is_(None),
                                Task.lease_expires_at < now,
                            ),
                        ),
                    ),
                )
                .order_by(Task.created_at.asc(), Task.task_id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return None
            reclaimed = task.status == TaskStatus.RUNNING.value
            # 与 SQL 谓词同一套规则；行锁后复核，防止谓词与判定漂移。
            if not can_claim(
                TaskStatus(task.status),
                task.lease_expires_at,
                task.attempts,
                self._max_attempts,
                now,
            ):
                return None
            task.status = TaskStatus.RUNNING.value
            task.lease_owner = owner
            task.lease_expires_at = deadline
            task.attempts += 1
            task.error_code = None
            task.updated_at = now
            session.flush()
            TASK_EVENTS.inc(
                {
                    "event": "reclaim" if reclaimed else "claim",
                    "task_type": task_type,
                }
            )
            return task

    def _load_locked(self, session: Session, task_id: UUID) -> Task | None:
        return session.scalar(
            select(Task).where(Task.task_id == task_id).with_for_update()
        )

    @staticmethod
    def _ensure_lease(task: Task | None, owner: str, now: datetime) -> Task:
        if task is None:
            raise LeaseLostError("任务不存在或已被移除")
        if (
            task.status != TaskStatus.RUNNING.value
            or task.lease_owner != owner
            or not is_lease_active(task.lease_expires_at, now)
        ):
            TASK_EVENTS.inc({"event": "lease_lost", "task_type": task.type})
            raise LeaseLostError("租约已失效：任务可能已过期、完成或转由其他 worker 持有")
        return task

    def renew(
        self,
        task_id: UUID,
        *,
        owner: str,
        lease_seconds: int | None = None,
    ) -> Task:
        """在租约仍有效时延长它；过期/被他人持有一律 ``LeaseLostError``。"""
        _require_short_text(owner, field="owner", limit=128)
        seconds = (
            self._default_lease_seconds if lease_seconds is None else lease_seconds
        )
        now = self._now()
        deadline = lease_deadline(now, seconds)

        with self._session() as session:
            task = self._ensure_lease(
                self._load_locked(session, task_id), owner, now
            )
            task.lease_expires_at = deadline
            task.updated_at = now
            session.flush()
            TASK_EVENTS.inc({"event": "renew", "task_type": task.type})
            return task

    def complete(
        self,
        task_id: UUID,
        *,
        owner: str,
        result_ref: str,
    ) -> Task:
        """写入最终结果并置为 succeeded；同一结果的重试是幂等的。

        与已有 succeeded 结果不一致的写入会被拒绝，防止旧 worker 或重复投递
        覆盖最终结果（CT-11）。
        """
        _require_short_text(owner, field="owner", limit=128)
        _require_short_text(result_ref, field="result_ref", limit=512)
        now = self._now()

        with self._session() as session:
            task = self._load_locked(session, task_id)
            if task is not None and task.status == TaskStatus.SUCCEEDED.value:
                if task.result_ref == result_ref:
                    TASK_EVENTS.inc(
                        {"event": "success_idempotent", "task_type": task.type}
                    )
                    return task
                TASK_EVENTS.inc({"event": "lease_lost", "task_type": task.type})
                raise LeaseLostError("任务已完成，拒绝覆盖最终结果")
            task = self._ensure_lease(task, owner, now)
            task.status = TaskStatus.SUCCEEDED.value
            task.result_ref = result_ref
            task.error_code = None
            task.lease_expires_at = None
            task.updated_at = now
            session.flush()
            TASK_EVENTS.inc({"event": "success", "task_type": task.type})
            return task

    def fail(
        self,
        task_id: UUID,
        *,
        owner: str,
        error_code: str,
        retryable: bool = True,
    ) -> Task:
        """报告失败：未达上限且可重试则重排队，否则终结为 failed。

        ``error_code`` 是稳定错误码；重排队时保留最近一次错误码，便于诊断。
        """
        _require_short_text(owner, field="owner", limit=128)
        _require_short_text(error_code, field="error_code", limit=64)
        now = self._now()

        with self._session() as session:
            task = self._load_locked(session, task_id)
            if (
                task is not None
                and task.status == TaskStatus.FAILED.value
                and task.error_code == error_code
            ):
                # 同一失败报告的重试：幂等返回，不覆盖其他终态。
                TASK_EVENTS.inc({"event": "fail_idempotent", "task_type": task.type})
                return task
            task = self._ensure_lease(task, owner, now)
            next_status = next_status_after_failure(
                task.attempts, self._max_attempts, retryable
            )
            task.status = next_status.value
            task.error_code = error_code
            task.lease_expires_at = None
            if next_status is TaskStatus.QUEUED:
                task.lease_owner = None
            task.updated_at = now
            session.flush()
            TASK_EVENTS.inc(
                {
                    "event": "retry" if next_status is TaskStatus.QUEUED else "fail",
                    "task_type": task.type,
                }
            )
            return task
