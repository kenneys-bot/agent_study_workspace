"""worker 侧的一次质检执行与幂等收尾（T18+T19，M4-03；契约 §3/§4，CT-09/CT-11）。

执行顺序固定，且严格遵守契约 §3「幂等与事务」：

1. **短事务**读取运行、会话、规则快照；``conversation_hash`` 复核不可变原文；
2. **短事务**把运行置为 ``running``，同时写一行 ``model_runs`` 追溯记录；
3. **事务外**逐条规则调用模型（系统提示词 + 会话轮次），每条判断前续租；
4. **短事务**收尾：``SELECT ... FOR UPDATE`` 锁任务行并复核租约，再做
   ``UPDATE inspection_runs SET status='succeeded' WHERE status IN ('queued',
   'running')`` 的条件更新；只有条件更新命中（rowcount==1）才插入 findings。
   因此同一质检**只会产生一次最终结果**：重复投递、旧 worker、并发收尾都写不进
   第二份 findings（``(inspection_id, rule_version_id)`` 还另有唯一约束兜底）。
5. 收尾后 :meth:`TaskQueue.complete` 写任务终态；租约已失效时抛
   :class:`LeaseLostError`，本次写入被放弃（findings 已由合法持有者写入或将被
   新持有者幂等补写）。

写入前还会再核验一次：``hit`` 的证据必须能按码点逐字回验且不在否定/转述
语境，知识引用必须能在当前 ``scope_id`` 内解析到分块（CT-09），否则整次运行
以 ``EVIDENCE_INVALID`` 失败而不写 findings。

失败处理：模型/校验失败先写 ``error_code`` 与安全诊断，再由任务队列决定
重排队（默认上限 3）还是终结为 ``failed``。任务一旦终结为 ``failed``，运行也
在同一流程里置为 ``failed``；worker 崩溃留下的“任务已 failed 但运行仍 running”
由 :meth:`InspectionRunner.reconcile` 收口（CT-11：不会永久 running）。
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from bank_assistant.infra.db import get_session_factory
from bank_assistant.infra.models import Finding, InspectionRun, ModelRun, Task
from bank_assistant.infra.task_queue import (
    LeaseLostError,
    TaskQueue,
    TaskStatus,
    is_lease_active,
)
from bank_assistant.inspections.deterministic import assess_context
from bank_assistant.inspections.evidence import (
    EvidenceError,
    ensure_hit_evidence_valid,
)
from bank_assistant.inspections.schema import (
    INSPECTION_PROMPT_VERSION,
    InspectionErrorCode,
    InspectionStatus,
    JudgmentValidationError,
    RuleJudgment,
    RuleJudgmentPayload,
    RuleSnapshot,
    RuleSnapshotEntry,
    build_inspection_messages,
    model_status_error_code,
    parse_judgment,
    validate_rule_snapshot,
)
from bank_assistant.core.model_gateway import ModelGateway, ModelResult
from bank_assistant.services.conversations import load_conversation

logger = logging.getLogger(__name__)

__all__ = ["ExecutionOutcome", "InspectionRunner", "current_code_sha"]

_MAX_DIAGNOSTIC_CHARS = 500


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """一次任务处理的显式结果；``status`` 是运行或任务的最新状态。"""

    inspection_id: UUID | None
    status: str
    error_code: str | None = None
    findings_count: int = 0
    attempts: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == InspectionStatus.SUCCEEDED.value

    @property
    def retry_queued(self) -> bool:
        return self.status == TaskStatus.QUEUED.value


def current_code_sha() -> str:
    """当前代码 SHA：优先 ``CODE_SHA``（部署注入），否则只读 git HEAD。"""
    value = os.environ.get("CODE_SHA", "").strip()
    if value:
        return value[:64]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip()[:64] or "unknown"


def _short(value: object, limit: int = _MAX_DIAGNOSTIC_CHARS) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"


class InspectionRunner:
    """执行一条 inspection 任务；不负责循环与领取（见 :mod:`bank_assistant.worker`）。"""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        session_factory: sessionmaker[Session] | None = None,
        now: Callable[[], datetime] | None = None,
        lease_seconds: int = 60,
        code_sha: str | None = None,
    ) -> None:
        self.gateway = gateway
        self._session_factory = session_factory or get_session_factory()
        self._now = now or (lambda: datetime.now(UTC))
        self.lease_seconds = lease_seconds
        self.code_sha = code_sha or current_code_sha()

    # ------------------------------------------------------------------ 入口
    def process(self, task: Task, *, owner: str, queue: TaskQueue) -> ExecutionOutcome:
        """执行一条任务；所有失败都转成显式 ``error_code``，不静默丢问题。"""
        from bank_assistant.inspections.service import (
            invalid_knowledge_refs,
            parse_inspection_payload_ref,
        )

        inspection_id = parse_inspection_payload_ref(task.payload_ref)
        if inspection_id is None:
            return self.record_failure(
                task_id=task.task_id,
                inspection_id=None,
                owner=owner,
                queue=queue,
                error_code=InspectionErrorCode.TASK_PAYLOAD_INVALID,
                message="任务缺少有效的 inspection payload_ref",
                retryable=False,
            )

        try:
            prepared = self._prepare(inspection_id)
        except LeaseLostError:
            return self._lease_lost(inspection_id, task.attempts)
        except _InspectionFailure as exc:
            return self.record_failure(
                task_id=task.task_id,
                inspection_id=inspection_id,
                owner=owner,
                queue=queue,
                error_code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            )

        if isinstance(prepared, ExecutionOutcome):
            # 运行已终态：不重跑，只把任务状态对齐（重复投递不会产生第二份结果）。
            return self._apply_terminal(prepared, task=task, owner=owner, queue=queue)

        if not self._mark_running(inspection_id):
            terminal = self._terminal_outcome(inspection_id)
            if terminal is not None:
                return self._apply_terminal(terminal, task=task, owner=owner, queue=queue)
            return self._lease_lost(inspection_id, task.attempts)

        judgments: list[tuple[RuleSnapshotEntry, RuleJudgment]] = []
        turn_contents = {
            UUID(str(turn["turn_id"])): str(turn["content"])
            for turn in prepared.turns
        }
        last_result: ModelResult | None = None
        for rule in prepared.snapshot.rules:
            try:
                # 长模型调用会跨越租约窗口：每条规则判断前续租，失约即放弃写入。
                queue.renew(task.task_id, owner=owner, lease_seconds=self.lease_seconds)
            except LeaseLostError:
                return self._lease_lost(inspection_id, task.attempts)

            messages = build_inspection_messages(
                rule=rule,
                turns=prepared.turns,
                business_type=prepared.snapshot.business_type,
            )
            result = self.gateway.structured(messages, schema=RuleJudgmentPayload)
            last_result = result
            if not result.ok:
                return self.record_failure(
                    task_id=task.task_id,
                    inspection_id=inspection_id,
                    owner=owner,
                    queue=queue,
                    error_code=model_status_error_code(result.status),
                    message=(
                        f"规则 {rule.rule_code} 模型调用失败："
                        f"{_short(result.error or result.status.value)}"
                    ),
                    retryable=True,
                    rule_code=rule.rule_code,
                    model_result=result,
                )
            try:
                judgment = parse_judgment(
                    result.data,
                    turns=turn_contents,
                    rule_code=rule.rule_code,
                    applicability=rule.applicability,
                    business_type=prepared.snapshot.business_type,
                )
            except ValidationError as exc:
                return self.record_failure(
                    task_id=task.task_id,
                    inspection_id=inspection_id,
                    owner=owner,
                    queue=queue,
                    error_code=InspectionErrorCode.INVALID_MODEL_OUTPUT,
                    message=f"规则 {rule.rule_code} 输出结构不合法：{_short(exc)}",
                    retryable=True,
                    rule_code=rule.rule_code,
                    model_result=result,
                )
            except JudgmentValidationError as exc:
                return self.record_failure(
                    task_id=task.task_id,
                    inspection_id=inspection_id,
                    owner=owner,
                    queue=queue,
                    error_code=exc.code,
                    message=f"规则 {rule.rule_code} 判断未通过校验：{exc}",
                    retryable=True,
                    rule_code=rule.rule_code,
                    model_result=result,
                )
            if judgment.knowledge_refs:
                with self._session() as session:
                    invalid_refs = invalid_knowledge_refs(
                        session,
                        judgment.knowledge_refs,
                        scope_id=prepared.scope_id,
                    )
                if invalid_refs:
                    return self.record_failure(
                        task_id=task.task_id,
                        inspection_id=inspection_id,
                        owner=owner,
                        queue=queue,
                        error_code=InspectionErrorCode.EVIDENCE_INVALID,
                        message=(
                            f"规则 {rule.rule_code} 的知识引用不在当前范围内："
                            + ", ".join(str(ref) for ref in invalid_refs[:3])
                        ),
                        retryable=True,
                        rule_code=rule.rule_code,
                        model_result=result,
                    )
            judgments.append((rule, judgment))

        return self._finalize_success(
            task_id=task.task_id,
            owner=owner,
            queue=queue,
            inspection_id=inspection_id,
            judgments=judgments,
            turn_contents=turn_contents,
            model_result=last_result,
            attempts=task.attempts,
        )

    # ------------------------------------------------------------------ 准备
    def _prepare(
        self, inspection_id: UUID
    ) -> "_PreparedRun | ExecutionOutcome":
        """短事务读取运行所需数据；运行已终态时直接返回对应结果。"""
        with self._session() as session:
            run = session.get(InspectionRun, inspection_id)
            if run is None:
                return ExecutionOutcome(
                    inspection_id=None,
                    status=InspectionStatus.FAILED.value,
                    error_code=InspectionErrorCode.INSPECTION_NOT_FOUND,
                )
            if run.status == InspectionStatus.SUCCEEDED.value:
                return ExecutionOutcome(
                    inspection_id=inspection_id,
                    status=InspectionStatus.SUCCEEDED.value,
                    findings_count=self._count_findings(session, inspection_id),
                )
            if run.status in (
                InspectionStatus.FAILED.value,
                InspectionStatus.CANCELLED.value,
            ):
                return ExecutionOutcome(
                    inspection_id=inspection_id,
                    status=run.status,
                    error_code=run.error_code,
                )
            conversation_id = run.conversation_id
            expected_hash = run.conversation_hash
            snapshot_data = run.rule_snapshot
            scope_id = run.scope_id

        try:
            snapshot = validate_rule_snapshot(snapshot_data)
            if not snapshot.rules:
                raise ValueError("规则快照为空")
        except ValueError as exc:
            raise _InspectionFailure(
                InspectionErrorCode.RULE_SNAPSHOT_INVALID,
                f"规则快照不合法：{_short(exc)}",
                retryable=False,
            ) from exc

        with self._session() as session:
            view = load_conversation(session, conversation_id)
            if view is None:
                raise _InspectionFailure(
                    InspectionErrorCode.CONVERSATION_NOT_FOUND,
                    "质检引用的会话不存在",
                    retryable=False,
                )
            actual_hash = view.conversation.content_hash
            turns = tuple(
                {
                    "turn_id": turn.turn_id,
                    "ordinal": turn.ordinal,
                    "speaker": turn.speaker,
                    "content": turn.content,
                }
                for turn in view.turns
            )
        if not turns:
            raise _InspectionFailure(
                InspectionErrorCode.CONVERSATION_EMPTY,
                "会话没有任何轮次，无法质检",
                retryable=False,
            )
        if actual_hash != expected_hash:
            raise _InspectionFailure(
                InspectionErrorCode.CONVERSATION_CHANGED,
                "会话原文哈希与创建质检时不一致",
                retryable=False,
            )
        return _PreparedRun(snapshot=snapshot, turns=turns, scope_id=scope_id)

    def _mark_running(self, inspection_id: UUID) -> bool:
        """短事务：running + 本次尝试的 model_runs 追溯行。"""
        now = self._now()
        with self._session() as session:
            trace = ModelRun(
                run_id=uuid4(),
                provider="none",
                model="none",
                model_version="none",
                prompt_version=INSPECTION_PROMPT_VERSION,
                # 规则快照在 inspection_runs.rule_snapshot；这里只记录模型运行元数据。
                knowledge_snapshot=[],
                index_version=None,
                code_sha=self.code_sha,
                mode=self.gateway.mode,
                created_at=now,
            )
            session.add(trace)
            session.flush()
            result = session.execute(
                update(InspectionRun)
                .where(
                    InspectionRun.inspection_id == inspection_id,
                    InspectionRun.status.in_(
                        [
                            InspectionStatus.QUEUED.value,
                            InspectionStatus.RUNNING.value,
                        ]
                    ),
                )
                .values(
                    status=InspectionStatus.RUNNING.value,
                    started_at=func.coalesce(InspectionRun.started_at, now),
                    run_id=trace.run_id,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                # 运行已被并发收尾：本次尝试的追溯行不应留下。
                session.delete(trace)
            session.commit()
            return result.rowcount == 1

    # ------------------------------------------------------------------ 收尾
    def _finalize_success(
        self,
        *,
        task_id: UUID,
        owner: str,
        queue: TaskQueue,
        inspection_id: UUID,
        judgments: list[tuple[RuleSnapshotEntry, RuleJudgment]],
        turn_contents: dict[UUID, str],
        model_result: ModelResult | None,
        attempts: int,
    ) -> ExecutionOutcome:
        """幂等最终化：条件更新命中才写 findings，重复/并发收尾不会写第二份。"""
        # 写入前最后一道证据闸门：hit 必须有能逐字回验、且不在否定/转述语境的
        # 原句。正常情况下 parse_judgment 已保证；这里防止未来代码路径绕过它。
        for rule, judgment in judgments:
            if judgment.verdict != "hit":
                continue
            try:
                ensure_hit_evidence_valid(
                    judgment.evidence, turn_contents=turn_contents
                )
            except EvidenceError as exc:
                return self.record_failure(
                    task_id=task_id,
                    inspection_id=inspection_id,
                    owner=owner,
                    queue=queue,
                    error_code=InspectionErrorCode.EVIDENCE_INVALID,
                    message=f"规则 {rule.rule_code} 证据未通过写入前复核：{exc}",
                    retryable=False,
                    rule_code=rule.rule_code,
                    model_result=model_result,
                )
            if not any(
                assess_context(
                    turn_contents[span.turn_id], span.start, span.end
                ).supported
                for span in judgment.evidence
            ):
                return self.record_failure(
                    task_id=task_id,
                    inspection_id=inspection_id,
                    owner=owner,
                    queue=queue,
                    error_code=InspectionErrorCode.EVIDENCE_INVALID,
                    message=(
                        f"规则 {rule.rule_code} 的命中证据落在否定或引用他人话语的语境"
                    ),
                    retryable=False,
                    rule_code=rule.rule_code,
                    model_result=model_result,
                )
        now = self._now()
        with self._session() as session:
            task_row = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if not _holds_lease(task_row, owner, now):
                raise LeaseLostError("租约已失效，拒绝写入最终结果")

            result = session.execute(
                update(InspectionRun)
                .where(
                    InspectionRun.inspection_id == inspection_id,
                    InspectionRun.status.in_(
                        [
                            InspectionStatus.QUEUED.value,
                            InspectionStatus.RUNNING.value,
                        ]
                    ),
                )
                .values(
                    status=InspectionStatus.SUCCEEDED.value,
                    finished_at=now,
                    error_code=None,
                    diagnostics={},
                    updated_at=now,
                )
            )
            finalized = result.rowcount == 1
            if finalized:
                self._update_trace(session, inspection_id, model_result)
                for rule, judgment in judgments:
                    session.add(
                        Finding(
                            finding_id=uuid4(),
                            inspection_id=inspection_id,
                            rule_version_id=rule.rule_version_id,
                            machine_verdict=judgment.verdict,
                            # severity 来自规则版本，模型不能改写。
                            severity=rule.severity,
                            rationale=judgment.rationale,
                            evidence=[
                                span.model_dump(mode="json")
                                for span in judgment.evidence
                            ],
                            knowledge_refs=[
                                ref.model_dump(mode="json")
                                for ref in judgment.knowledge_refs
                            ],
                            created_at=now,
                        )
                    )
            session.commit()

        if finalized:
            try:
                queue.complete(
                    task_id,
                    owner=owner,
                    result_ref=f"inspection:{inspection_id}",
                )
            except LeaseLostError:
                # findings 已由合法持有者写入；任务会被新持有者幂等补齐。
                return ExecutionOutcome(
                    inspection_id=inspection_id,
                    status="lease_lost",
                    error_code="LEASE_LOST",
                    findings_count=len(judgments),
                    attempts=attempts,
                )
            return ExecutionOutcome(
                inspection_id=inspection_id,
                status=InspectionStatus.SUCCEEDED.value,
                findings_count=len(judgments),
                attempts=attempts,
            )

        # 条件更新未命中：另一个合法持有者已写入终态，不重写、也不追加 findings。
        terminal = self._terminal_outcome(inspection_id)
        if terminal is None:
            return self._lease_lost(inspection_id, attempts)
        if terminal.status == InspectionStatus.SUCCEEDED.value:
            try:
                queue.complete(
                    task_id,
                    owner=owner,
                    result_ref=f"inspection:{inspection_id}",
                )
            except LeaseLostError:
                return self._lease_lost(inspection_id, attempts)
            return ExecutionOutcome(
                inspection_id=inspection_id,
                status=InspectionStatus.SUCCEEDED.value,
                findings_count=terminal.findings_count,
                attempts=attempts,
            )
        try:
            queue.fail(
                task_id,
                owner=owner,
                error_code=terminal.error_code
                or InspectionErrorCode.TASK_TERMINATED_WITHOUT_RESULT,
                retryable=False,
            )
        except LeaseLostError:
            return self._lease_lost(inspection_id, attempts)
        return ExecutionOutcome(
            inspection_id=inspection_id,
            status=InspectionStatus.FAILED.value,
            error_code=terminal.error_code,
            attempts=attempts,
        )

    def record_failure(
        self,
        *,
        task_id: UUID,
        inspection_id: UUID | None,
        owner: str,
        queue: TaskQueue,
        error_code: str,
        message: str,
        retryable: bool,
        rule_code: str | None = None,
        model_result: ModelResult | None = None,
    ) -> ExecutionOutcome:
        """记录失败：先写运行诊断，再由任务队列决定重排队或终结。"""
        now = self._now()
        diagnostics = {
            "error_code": error_code,
            "message": _short(message),
            "rule_code": rule_code,
            "model_status": model_result.status.value if model_result else None,
        }
        if inspection_id is not None:
            with self._session() as session:
                session.execute(
                    update(InspectionRun)
                    .where(
                        InspectionRun.inspection_id == inspection_id,
                        InspectionRun.status.in_(
                            [
                                InspectionStatus.QUEUED.value,
                                InspectionStatus.RUNNING.value,
                            ]
                        ),
                    )
                    .values(
                        error_code=error_code,
                        diagnostics=diagnostics,
                        updated_at=now,
                    )
                )
                if model_result is not None:
                    self._update_trace(session, inspection_id, model_result)
                session.commit()

        try:
            task = queue.fail(
                task_id,
                owner=owner,
                error_code=error_code,
                retryable=retryable,
            )
        except LeaseLostError:
            return ExecutionOutcome(
                inspection_id=inspection_id,
                status="lease_lost",
                error_code="LEASE_LOST",
            )

        if task.status == TaskStatus.FAILED.value:
            if inspection_id is not None:
                with self._session() as session:
                    session.execute(
                        update(InspectionRun)
                        .where(
                            InspectionRun.inspection_id == inspection_id,
                            InspectionRun.status.in_(
                                [
                                    InspectionStatus.QUEUED.value,
                                    InspectionStatus.RUNNING.value,
                                ]
                            ),
                        )
                        .values(
                            status=InspectionStatus.FAILED.value,
                            error_code=error_code,
                            diagnostics=diagnostics,
                            finished_at=now,
                            updated_at=now,
                        )
                    )
                    session.commit()
            return ExecutionOutcome(
                inspection_id=inspection_id,
                status=InspectionStatus.FAILED.value,
                error_code=error_code,
                attempts=task.attempts,
            )
        return ExecutionOutcome(
            inspection_id=inspection_id,
            status=task.status,
            error_code=error_code,
            attempts=task.attempts,
        )

    # ------------------------------------------------------------------ 对账
    def reconcile(self) -> int:
        """把「任务已终态、运行仍非终态」的运行收口，杜绝永久 running（CT-11）。"""
        now = self._now()
        fixed = 0
        with self._session() as session:
            rows = session.execute(
                select(InspectionRun, Task)
                .join(Task, Task.task_id == InspectionRun.task_id)
                .where(
                    InspectionRun.status.in_(
                        [
                            InspectionStatus.QUEUED.value,
                            InspectionStatus.RUNNING.value,
                        ]
                    ),
                    Task.status.in_(
                        [
                            TaskStatus.FAILED.value,
                            TaskStatus.CANCELLED.value,
                            TaskStatus.SUCCEEDED.value,
                        ]
                    ),
                )
                .with_for_update()
            ).all()
            for run, task in rows:
                if task.status == TaskStatus.CANCELLED.value:
                    run.status = InspectionStatus.CANCELLED.value
                    run.error_code = task.error_code or "TASK_CANCELLED"
                else:
                    run.status = InspectionStatus.FAILED.value
                    run.error_code = (
                        task.error_code
                        or InspectionErrorCode.TASK_TERMINATED_WITHOUT_RESULT
                    )
                run.finished_at = now
                run.updated_at = now
                run.diagnostics = {
                    "error_code": run.error_code,
                    "message": f"任务已进入终态 {task.status}，运行按对账收口",
                    "task_status": task.status,
                }
                fixed += 1
            session.commit()
        if fixed:
            logger.warning("inspection_reconcile fixed=%d", fixed)
        return fixed

    # ------------------------------------------------------------------ 内部
    def _terminal_outcome(self, inspection_id: UUID) -> ExecutionOutcome | None:
        with self._session() as session:
            run = session.get(InspectionRun, inspection_id)
            if run is None:
                return ExecutionOutcome(
                    inspection_id=None,
                    status=InspectionStatus.FAILED.value,
                    error_code=InspectionErrorCode.INSPECTION_NOT_FOUND,
                )
            if run.status == InspectionStatus.SUCCEEDED.value:
                return ExecutionOutcome(
                    inspection_id=inspection_id,
                    status=run.status,
                    findings_count=self._count_findings(session, inspection_id),
                )
            if run.status in (
                InspectionStatus.FAILED.value,
                InspectionStatus.CANCELLED.value,
            ):
                return ExecutionOutcome(
                    inspection_id=inspection_id,
                    status=run.status,
                    error_code=run.error_code,
                )
        return None

    def _apply_terminal(
        self,
        outcome: ExecutionOutcome,
        *,
        task: Task,
        owner: str,
        queue: TaskQueue,
    ) -> ExecutionOutcome:
        """运行已是终态：只对齐任务状态，绝不重写运行或追加 findings。"""
        if outcome.status == InspectionStatus.SUCCEEDED.value:
            try:
                queue.complete(
                    task.task_id,
                    owner=owner,
                    result_ref=f"inspection:{outcome.inspection_id}",
                )
            except LeaseLostError:
                return ExecutionOutcome(
                    inspection_id=outcome.inspection_id,
                    status="lease_lost",
                    error_code="LEASE_LOST",
                )
            return outcome
        error_code = (
            outcome.error_code
            or InspectionErrorCode.TASK_TERMINATED_WITHOUT_RESULT
        )
        try:
            queue.fail(
                task.task_id,
                owner=owner,
                error_code=error_code,
                retryable=False,
            )
        except LeaseLostError:
            return ExecutionOutcome(
                inspection_id=outcome.inspection_id,
                status="lease_lost",
                error_code="LEASE_LOST",
            )
        return ExecutionOutcome(
            inspection_id=outcome.inspection_id,
            status=InspectionStatus.FAILED.value,
            error_code=error_code,
        )

    def _update_trace(
        self,
        session: Session,
        inspection_id: UUID,
        model_result: ModelResult | None,
    ) -> None:
        if model_result is None:
            return
        run = session.get(InspectionRun, inspection_id)
        if run is None or run.run_id is None:
            return
        trace = session.get(ModelRun, run.run_id)
        if trace is None:
            return
        trace.provider = (
            model_result.provider.value if model_result.provider else "none"
        )
        trace.model = model_result.model or "none"
        trace.model_version = model_result.model or "none"
        trace.mode = model_result.mode or self.gateway.mode

    @staticmethod
    def _count_findings(session: Session, inspection_id: UUID) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Finding)
                .where(Finding.inspection_id == inspection_id)
            )
            or 0
        )

    def _lease_lost(self, inspection_id: UUID | None, attempts: int) -> ExecutionOutcome:
        logger.warning("inspection_lease_lost inspection_id=%s", inspection_id)
        return ExecutionOutcome(
            inspection_id=inspection_id,
            status="lease_lost",
            error_code="LEASE_LOST",
            attempts=attempts,
        )

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


@dataclass(frozen=True, slots=True)
class _PreparedRun:
    snapshot: RuleSnapshot
    turns: tuple[dict[str, object], ...]
    scope_id: str


class _InspectionFailure(Exception):
    """内部显式的非租约失败；``process`` 之外统一转成任务失败。"""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def _holds_lease(task: Task | None, owner: str, now: datetime) -> bool:
    return (
        task is not None
        and task.status == TaskStatus.RUNNING.value
        and task.lease_owner == owner
        and is_lease_active(task.lease_expires_at, now)
    )
