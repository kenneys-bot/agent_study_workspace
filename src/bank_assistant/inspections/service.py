"""质检运行的创建与读取（M4-03/T18，#19；契约 §2/§3/§5）。

创建质检时**固化规则快照**：只取当前身份 ``scope_id`` 下、已发布且在有效期窗口
内的规则版本，并可按业务类型与规则范围（``rule_ids``）收窄。T12 的会话实体没有
``business_type`` 字段，因此请求里的 ``business_type`` 就是本次质检的规则适用
业务范围；不传则使用当前范围内全部已发布规则。worker 之后只按
快照判断，因此规则在运行期间被退役/发布新版本都不会改变本次质检的规则集，也
不会出现“判断时用的是哪一版规则”说不清的情况。

会话在创建时校验 ``scope_id``：跨范围与不存在一样抛
:class:`InspectionNotFound`，由路由映射为 404，不泄露存在性。``conversation_hash``
在创建时记录，worker 执行前复核（会话原文按 T12 不可变）。

质检与任务在同一短事务内创建：``tasks.payload_ref = "inspection:<id>"``，
``inspection_runs.task_id`` 唯一。幂等键由路由复用
:mod:`bank_assistant.infra.idempotency`，重复请求不会创建第二个运行或任务
（CT-05/CT-06）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import (
    Conversation,
    Finding,
    InspectionRun,
    RuleSet,
    RuleVersion,
    Task,
)
from bank_assistant.inspections.schema import (
    InspectionStatus,
    RuleSnapshot,
    RuleSnapshotEntry,
    validate_rule_snapshot,
)
from bank_assistant.rules.lifecycle import RuleStatus
from bank_assistant.rules.schema import (
    RuleApplicability,
    RuleCriteria,
    RuleSourceRef,
)
from bank_assistant.security.identity import Identity

__all__ = [
    "TASK_TYPE",
    "InspectionInputError",
    "InspectionNotFound",
    "InspectionView",
    "build_rule_snapshot",
    "create_inspection",
    "inspection_payload_ref",
    "load_inspection",
    "parse_inspection_payload_ref",
    "select_applicable_rule_versions",
]

# 质检任务在 tasks.type 上的稳定取值；worker 只领取这个类型。
TASK_TYPE = "inspection"


class InspectionNotFound(Exception):
    """质检或会话不存在/跨范围；路由统一映射为 404。"""


class InspectionInputError(Exception):
    """请求可解析但没有可执行的质检（如没有适用规则）；映射为 422。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InspectionView:
    """一次质检的读取视图：运行、逐规则判断与快照里的规则编号。"""

    run: InspectionRun
    findings: tuple[Finding, ...]
    rules_by_version: dict[UUID, RuleSnapshotEntry]

    @property
    def rule_count(self) -> int:
        return len(self.rules_by_version)


def inspection_payload_ref(inspection_id: UUID) -> str:
    """任务载荷引用：worker 据此找到待执行的质检运行。"""
    return f"inspection:{inspection_id}"


def parse_inspection_payload_ref(payload_ref: str | None) -> UUID | None:
    """解析 ``inspection:<uuid>``；非法/缺失返回 ``None``（显式失败，不猜）。"""
    if not payload_ref or not payload_ref.startswith("inspection:"):
        return None
    try:
        return UUID(payload_ref.split(":", 1)[1])
    except (ValueError, AttributeError):
        return None


def _entry_for(version: RuleVersion, rule: RuleSet) -> RuleSnapshotEntry:
    """把数据库行转成快照条目；内容不合法时显式报错，不静默跳过规则。"""
    return RuleSnapshotEntry(
        rule_version_id=version.rule_version_id,
        rule_id=rule.rule_id,
        rule_code=rule.rule_code,
        version_no=version.version_no,
        business_type=version.business_type,
        severity=version.severity,
        applicability=RuleApplicability.model_validate(version.applicability),
        criteria=RuleCriteria.model_validate(version.criteria),
        exceptions=list(version.exceptions or []),
        source_refs=[
            RuleSourceRef.model_validate(ref) for ref in (version.source_refs or [])
        ],
        content_hash=version.content_hash,
    )


def select_applicable_rule_versions(
    session: Session,
    *,
    scope_id: str,
    at: datetime,
    business_type: str | None = None,
    rule_ids: Iterable[UUID] | None = None,
) -> list[RuleSnapshotEntry]:
    """读取当前范围、在 ``at`` 时刻生效的已发布规则版本，按规则编码排序。"""
    statement = (
        select(RuleVersion, RuleSet)
        .join(RuleSet, RuleSet.rule_id == RuleVersion.rule_id)
        .where(
            RuleSet.scope_id == scope_id,
            RuleVersion.status == RuleStatus.PUBLISHED.value,
            RuleVersion.valid_from <= at,
            or_(RuleVersion.valid_to.is_(None), at < RuleVersion.valid_to),
        )
        .order_by(RuleSet.rule_code.asc(), RuleVersion.version_no.asc())
    )
    if business_type is not None:
        statement = statement.where(RuleVersion.business_type == business_type)
    selected_ids = list(rule_ids or ())
    if selected_ids:
        statement = statement.where(RuleSet.rule_id.in_(selected_ids))

    entries: list[RuleSnapshotEntry] = []
    for version, rule in session.execute(statement):
        try:
            entry = _entry_for(version, rule)
        except ValueError as exc:  # 已损坏的规则内容：不静默跳过
            raise InspectionInputError(
                "RULE_SNAPSHOT_INVALID",
                f"规则 {rule.rule_code} 的已发布内容不合法：{exc}",
            ) from exc
        if business_type is not None and business_type not in entry.applicability.business_types:
            continue
        entries.append(entry)
    return entries


def build_rule_snapshot(
    session: Session,
    *,
    scope_id: str,
    at: datetime,
    business_type: str | None = None,
    rule_ids: Sequence[UUID] | None = None,
) -> RuleSnapshot:
    """构建并校验规则快照；没有适用规则时显式拒绝，而不是静默空跑。"""
    entries = select_applicable_rule_versions(
        session,
        scope_id=scope_id,
        at=at,
        business_type=business_type,
        rule_ids=rule_ids,
    )
    if not entries:
        raise InspectionInputError(
            "NO_APPLICABLE_RULES",
            "当前范围内没有适用于该业务类型的已发布规则",
        )
    return RuleSnapshot(
        captured_at=at,
        scope_id=scope_id,
        business_type=business_type,
        rules=entries,
    )


def create_inspection(
    session: Session,
    identity: Identity,
    *,
    conversation_id: UUID,
    business_type: str | None = None,
    rule_ids: Sequence[UUID] | None = None,
    idempotency_key: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[InspectionRun, Task]:
    """创建 queued 质检运行 + 租约任务；不提交事务（调用方控制边界）。"""
    clock = now or (lambda: datetime.now(UTC))
    moment = clock()

    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.scope_id != identity.scope_id:
        raise InspectionNotFound("会话不存在")

    snapshot = build_rule_snapshot(
        session,
        scope_id=identity.scope_id,
        at=moment,
        business_type=business_type,
        rule_ids=rule_ids,
    )

    inspection = InspectionRun(
        inspection_id=uuid4(),
        conversation_id=conversation.conversation_id,
        conversation_hash=conversation.content_hash,
        rule_snapshot=snapshot.model_dump(mode="json"),
        status=InspectionStatus.QUEUED.value,
        scope_id=identity.scope_id,
        diagnostics={},
        created_at=moment,
        updated_at=moment,
    )
    session.add(inspection)
    session.flush()

    task = Task(
        task_id=uuid4(),
        type=TASK_TYPE,
        status=InspectionStatus.QUEUED.value,
        idempotency_key=idempotency_key,
        payload_ref=inspection_payload_ref(inspection.inspection_id),
        scope_id=identity.scope_id,
    )
    session.add(task)
    session.flush()

    inspection.task_id = task.task_id
    session.flush()
    return inspection, task


def load_inspection(
    session: Session,
    identity: Identity,
    inspection_id: UUID,
) -> InspectionView:
    """按身份读取质检；不存在或跨范围统一 :class:`InspectionNotFound`（404）。"""
    run = session.get(InspectionRun, inspection_id)
    if run is None or run.scope_id != identity.scope_id:
        raise InspectionNotFound("质检不存在")

    findings = tuple(
        session.scalars(
            select(Finding)
            .where(Finding.inspection_id == inspection_id)
            .order_by(Finding.created_at.asc(), Finding.finding_id.asc())
        ).all()
    )
    rules_by_version: dict[UUID, RuleSnapshotEntry] = {}
    try:
        snapshot = validate_rule_snapshot(run.rule_snapshot)
    except ValueError:
        # 读取路径不因历史快照损坏而 500；不泄露内部结构，按空映射展示。
        snapshot = None
    if snapshot is not None:
        rules_by_version = {rule.rule_version_id: rule for rule in snapshot.rules}
    return InspectionView(run=run, findings=findings, rules_by_version=rules_by_version)
