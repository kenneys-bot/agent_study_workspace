"""规则内容与契约 §4 证据 JSON 的校验（M4，契约 §2/§4）。

规则版本的内容由 ``applicability``（非时间部分）、``criteria``、
``exceptions``、``severity``、``source_refs`` 与 ``examples`` 组成；时间窗口
由服务端列 ``valid_from``/``valid_to`` 维护，客户端不能塞进 ``applicability``。

``examples`` 的每个桶（正例/反例/证据不足/不适用）里，每一项都必须是契约 §4
的证据对象：``rule_id``、``rule_version``、``verdict``、``severity``、
``evidence``（码点区间 + 原文）、``rationale``、``knowledge_refs``。演示规则
另带 ``sample_turns``（合成示例原文），使每个证据区间都能被独立回验：
``quote == content[start:end]``（Unicode 码点，start 含、end 不含）。

本模块是 API 与演示种子的共用校验层；纯逻辑，不依赖数据库。
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bank_assistant.knowledge.lifecycle import (
    DEMO_MARKER,
    DEMO_SOURCE_HOST,
    PUBLIC_SOURCE_HOSTS,
)
from bank_assistant.rules.lifecycle import validate_severity

Severity = Literal["low", "medium", "high"]
Verdict = Literal["hit", "clear", "insufficient_evidence", "not_applicable"]
SourceKind = Literal["demo_policy", "public_regulation", "demo_rule_set"]

# 演示规则类来源只允许占位域名：.invalid 是保留 TLD，永不解析。
DEMO_SOURCE_KINDS: frozenset[str] = frozenset({"demo_policy", "demo_rule_set"})

# examples 的桶 → 契约 §3 的判定值；顺序即种子与测试的检查顺序。
EXAMPLES_BUCKETS: tuple[str, ...] = (
    "positive",
    "negative",
    "insufficient_evidence",
    "not_applicable",
)

BUCKET_VERDICTS: dict[str, str] = {
    "positive": "hit",
    "negative": "clear",
    "insufficient_evidence": "insufficient_evidence",
    "not_applicable": "not_applicable",
}


class RuleSchemaError(ValueError):
    """规则内容或示例证据不合法；种子脚本与 API 都应显式失败。"""


class EvidenceSpan(BaseModel):
    """契约 §4 的单个证据区间：码点计数，start 含、end 不含。"""

    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)
    quote: str = Field(..., min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _ordered(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("证据区间 end 必须大于 start")
        return self


class KnowledgeRef(BaseModel):
    """契约 §4 的知识引用：版本与分块都必须来自授权范围。"""

    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    chunk_id: UUID


class RuleExample(BaseModel):
    """一条契约 §4 证据对象；``evidence`` 为空只允许用于非命中判定。"""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(..., min_length=1, max_length=64)
    rule_version: str = Field(..., min_length=1, max_length=16)
    verdict: Verdict
    severity: Severity
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    rationale: str = Field(..., min_length=1, max_length=2000)
    knowledge_refs: list[KnowledgeRef] = Field(default_factory=list)


class SampleTurn(BaseModel):
    """示例原文轮次；只用于回验证据区间，不是真实会话数据。"""

    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    speaker: str = Field(..., min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=4000)


class RuleExamples(BaseModel):
    """正例/反例/证据不足/不适用四类示例；正反例必填（M4-02 验收）。"""

    model_config = ConfigDict(extra="forbid")

    sample_turns: list[SampleTurn] = Field(default_factory=list)
    positive: list[RuleExample] = Field(..., min_length=1)
    negative: list[RuleExample] = Field(..., min_length=1)
    insufficient_evidence: list[RuleExample] = Field(default_factory=list)
    not_applicable: list[RuleExample] = Field(default_factory=list)


class RuleApplicability(BaseModel):
    """规则的适用范围（非时间部分）；时间窗口由服务端列维护。"""

    model_config = ConfigDict(extra="forbid")

    business_types: list[str] = Field(..., min_length=1)
    channels: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)

    @field_validator("business_types", "channels", "conditions")
    @classmethod
    def _non_empty_unique(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("适用范围中的条目必须是非空字符串")
            if item in cleaned:
                raise ValueError("适用范围中的条目不得重复")
            cleaned.append(item)
        return cleaned


class RuleCriteria(BaseModel):
    """判断条件：至少要写明证据要求，模型才能定位原文。"""

    model_config = ConfigDict(extra="forbid")

    evidence_requirements: list[str] = Field(..., min_length=1)
    decision_rules: list[str] = Field(default_factory=list)

    @field_validator("evidence_requirements", "decision_rules")
    @classmethod
    def _non_empty_items(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("判断条件中的条目必须是非空字符串")
        return value


class RuleSourceRef(BaseModel):
    """规则来源：演示制度/演示规则集用占位域名，公开条文用公开链接。"""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    ref: str = Field(..., min_length=1, max_length=512)
    note: str | None = Field(default=None, max_length=500)


def validate_source_refs(
    refs: list[RuleSourceRef], *, demo_only: bool
) -> None:
    """校验来源引用与演示标记一致，且至少一条来源。"""
    if not refs:
        raise RuleSchemaError("规则必须至少提供一条 source_refs")
    for ref in refs:
        parsed = urlparse(ref.ref)
        host = (parsed.hostname or "").lower()
        if ref.kind in DEMO_SOURCE_KINDS:
            if parsed.scheme not in {"http", "https"} or not (
                host == DEMO_SOURCE_HOST or host.endswith(".invalid")
            ):
                raise RuleSchemaError(
                    f"来源 {ref.ref!r} 是演示类来源，必须使用 {DEMO_SOURCE_HOST} 占位域名"
                )
            if not demo_only:
                raise RuleSchemaError("非演示规则不得引用 demo.invalid 的虚构来源")
        else:
            if parsed.scheme not in {"http", "https"} or host not in PUBLIC_SOURCE_HOSTS:
                raise RuleSchemaError(
                    f"公开监管条文来源必须是公开链接（允许域名 "
                    f"{sorted(PUBLIC_SOURCE_HOSTS)}），实际 {ref.ref!r}"
                )


def validate_rule_examples(
    examples: RuleExamples,
    *,
    rule_code: str,
    version_no: int,
    severity: str,
) -> None:
    """把示例证据与规则版本身份、严重级别和示例原文对齐。

    ``sample_turns`` 为空时只做结构校验；非空时每个证据区间都必须落到某条
    示例原文上，且 ``quote`` 与码点区间完全一致。
    """
    validate_severity(severity)
    sample_turns: dict[UUID, SampleTurn] = {}
    for turn in examples.sample_turns:
        if turn.turn_id in sample_turns:
            raise RuleSchemaError(f"sample_turns 中 turn_id {turn.turn_id} 重复")
        sample_turns[turn.turn_id] = turn

    for bucket, expected_verdict in BUCKET_VERDICTS.items():
        for index, example in enumerate(getattr(examples, bucket), start=1):
            label = f"examples.{bucket}[{index}]"
            if example.verdict != expected_verdict:
                raise RuleSchemaError(
                    f"{label} 的 verdict 必须是 {expected_verdict}，"
                    f"实际 {example.verdict}"
                )
            if example.rule_id != rule_code:
                raise RuleSchemaError(
                    f"{label} 的 rule_id 必须是 {rule_code}，实际 {example.rule_id}"
                )
            if example.rule_version != str(version_no):
                raise RuleSchemaError(
                    f"{label} 的 rule_version 必须是 {version_no}，"
                    f"实际 {example.rule_version}"
                )
            if example.severity != severity:
                raise RuleSchemaError(
                    f"{label} 的 severity 必须与规则一致（{severity}），"
                    f"实际 {example.severity}"
                )
            if expected_verdict in {"hit", "clear"} and not example.evidence:
                raise RuleSchemaError(f"{label} 必须至少包含一条证据区间")
            for span in example.evidence:
                turn = sample_turns.get(span.turn_id)
                if sample_turns and turn is None:
                    raise RuleSchemaError(
                        f"{label} 的 turn_id {span.turn_id} 不在 sample_turns 中"
                    )
                if turn is None:
                    continue
                # Python 字符串切片就是 Unicode 码点计数（契约 §4）。
                actual = turn.content[span.start : span.end]
                if actual != span.quote:
                    raise RuleSchemaError(
                        f"{label} 的证据区间原文不匹配："
                        f"实际 {actual!r}，声明 {span.quote!r}"
                    )


def demo_marker_present(description: str) -> bool:
    """演示规则必须显式标注为虚构演示内容（GLOSSARY「演示制度」）。"""
    return DEMO_MARKER in description


__all__ = [
    "BUCKET_VERDICTS",
    "EXAMPLES_BUCKETS",
    "DEMO_SOURCE_KINDS",
    "EvidenceSpan",
    "KnowledgeRef",
    "RuleApplicability",
    "RuleCriteria",
    "RuleExample",
    "RuleExamples",
    "RuleSchemaError",
    "RuleSourceRef",
    "SampleTurn",
    "Severity",
    "SourceKind",
    "Verdict",
    "demo_marker_present",
    "validate_rule_examples",
    "validate_source_refs",
]
