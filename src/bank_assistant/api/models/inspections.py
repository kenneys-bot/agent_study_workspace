"""质检 API 的请求/响应模型（契约 §5 ``POST /inspections`` / ``GET /inspections/{id}``）。

- 请求只接受 ``conversation_id``、可选业务类型与规则范围（``rule_ids``）；
  状态、规则快照、任务与错误码全部由服务端生成，客户端不能指定。
- 响应给出状态、逐规则判断（含证据 JSON 与知识引用）以及失败原因
  （``error_code`` + 安全诊断信息）。诊断只含错误码/规则编码/校验原因，
  不含会话正文、模型原始输出或凭据（契约 §1）。T19 起证据在读取时重新按
  码点回验，并补上 ``turn_ordinal`` 与 ``validated``；无效证据的 hit 绝不
  以 hit 返回（``raw_machine_verdict`` 保留原始机器判断供审计）。
- 机器判断 ``machine_verdict`` 只读；人工复核走后续票的 ReviewDecision，
  不会覆盖这里的判断（契约 §2）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bank_assistant.infra.models import Finding, InspectionRun, Task
from bank_assistant.inspections.evidence import annotate_evidence
from bank_assistant.inspections.service import (
    InspectionView,
    finding_evidence_valid,
)


class InspectionCreateRequest(BaseModel):
    """``POST /inspections`` 输入；规则范围可选，缺省为当前范围全部适用规则。"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID = Field(..., description="待检会话 ID")
    business_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="业务类型过滤（lost_card / credit_card_fee / complaint 等），不传则不过滤",
    )
    rule_ids: list[UUID] | None = Field(
        default=None,
        max_length=100,
        description="规则范围：只使用这些规则身份（rule_id）的已发布版本",
    )


class InspectionAccepted(BaseModel):
    """202 响应：运行与任务 ID；客户端用 ``GET /tasks/{id}`` 或本接口查进度。"""

    inspection_id: UUID
    task_id: UUID
    conversation_id: UUID
    status: str
    rule_count: int
    created_at: datetime


class FindingPublic(BaseModel):
    """一条逐规则机器判断；``machine_verdict`` 写入后不可覆盖。"""

    finding_id: UUID
    rule_version_id: UUID
    rule_id: UUID | None = None
    rule_code: str | None = None
    version_no: int | None = None
    machine_verdict: str
    severity: str
    rationale: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_refs: list[dict[str, Any]] = Field(default_factory=list)
    # T19：证据是否全部通过码点回验；无效证据的 hit 不能以 hit 暴露。
    evidence_valid: bool = True
    # 仅在读取路径发现无效证据、把展示判断降级时保留原始机器判断。
    raw_machine_verdict: str | None = None
    created_at: datetime


class InspectionDetail(BaseModel):
    """质检详情：状态、逐规则判断、证据与失败原因。"""

    inspection_id: UUID
    conversation_id: UUID
    conversation_hash: str
    status: str
    error_code: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    rule_count: int
    task_id: UUID | None = None
    parent_run_id: UUID | None = None
    run_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    findings: list[FindingPublic] = Field(default_factory=list)


def to_accepted(run: InspectionRun, task: Task) -> InspectionAccepted:
    return InspectionAccepted(
        inspection_id=run.inspection_id,
        task_id=task.task_id,
        conversation_id=run.conversation_id,
        status=run.status,
        rule_count=len(run.rule_snapshot.get("rules", [])),
        created_at=run.created_at,
    )


def _evidence_valid(
    finding: Finding, view: InspectionView
) -> bool:
    """读取路径重新回验证据（与人工复核共用同一实现）。"""
    return finding_evidence_valid(finding, view)


def _finding_public(
    finding: Finding, view: InspectionView
) -> FindingPublic:
    rule = view.rules_by_version.get(finding.rule_version_id)
    evidence_valid = _evidence_valid(finding, view)
    machine_verdict = finding.machine_verdict
    raw_machine_verdict: str | None = None
    if machine_verdict == "hit" and not evidence_valid:
        # 契约 §3/§7：没有可回验证据的 hit 绝不能作为命中展示；保留原始判断供审计。
        raw_machine_verdict = machine_verdict
        machine_verdict = "insufficient_evidence"
    return FindingPublic(
        finding_id=finding.finding_id,
        rule_version_id=finding.rule_version_id,
        rule_id=rule.rule_id if rule is not None else None,
        rule_code=rule.rule_code if rule is not None else None,
        version_no=rule.version_no if rule is not None else None,
        machine_verdict=machine_verdict,
        severity=finding.severity,
        rationale=finding.rationale,
        evidence=annotate_evidence(
            list(finding.evidence or []),
            turn_contents=view.turn_contents,
            turn_ordinals=view.turn_ordinals,
        ),
        knowledge_refs=list(finding.knowledge_refs or []),
        evidence_valid=evidence_valid,
        raw_machine_verdict=raw_machine_verdict,
        created_at=finding.created_at,
    )


def to_detail(view: InspectionView) -> InspectionDetail:
    run = view.run
    # 按快照里的规则顺序（rule_code）稳定输出，不依赖插入顺序或 UUID 排序。
    ordered = sorted(
        view.findings,
        key=lambda finding: (
            view.rules_by_version[finding.rule_version_id].rule_code
            if finding.rule_version_id in view.rules_by_version
            else "",
            str(finding.finding_id),
        ),
    )
    return InspectionDetail(
        inspection_id=run.inspection_id,
        conversation_id=run.conversation_id,
        conversation_hash=run.conversation_hash,
        status=run.status,
        error_code=run.error_code,
        diagnostics=dict(run.diagnostics or {}),
        rule_count=view.rule_count,
        task_id=run.task_id,
        parent_run_id=run.parent_run_id,
        run_id=run.run_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        findings=[_finding_public(finding, view) for finding in ordered],
    )


__all__ = [
    "FindingPublic",
    "InspectionAccepted",
    "InspectionCreateRequest",
    "InspectionDetail",
    "to_accepted",
    "to_detail",
]
