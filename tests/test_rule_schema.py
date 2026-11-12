"""规则内容与证据 JSON 的校验（契约 §2 RuleVersion、§4 证据格式）。

``examples`` 的每一项都必须是契约 §4 的证据 JSON；演示规则额外带一组合成
``sample_turns``，让每个证据区间能在原文里被独立复核（码点计数、start 含、
end 不含）。这里把校验函数当公共 seam 固定下来：API 与演示种子共用同一套
规则，避免“JSON 看起来像证据”却无法定位原文。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from bank_assistant.rules.schema import (
    RuleApplicability,
    RuleCriteria,
    RuleExamples,
    RuleSchemaError,
    RuleSourceRef,
    validate_rule_examples,
    validate_source_refs,
)

TURN_HIT = UUID("00000000-0000-0000-0000-000000000001")
TURN_CLEAR = UUID("00000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _example(
    *,
    verdict: str,
    turn_id: UUID = TURN_HIT,
    start: int = 2,
    end: int = 7,
    quote: str = "短信验证码",
    rule_id: str = "DEMO-PWD-01",
    rule_version: str = "1",
    severity: str = "high",
    rationale: str = "坐席主动索取验证码",
    evidence: bool = True,
) -> dict:
    payload: dict = {
        "rule_id": rule_id,
        "rule_version": rule_version,
        "verdict": verdict,
        "severity": severity,
        "evidence": [],
        "rationale": rationale,
        "knowledge_refs": [],
    }
    if evidence:
        payload["evidence"] = [
            {
                "turn_id": str(turn_id),
                "start": start,
                "end": end,
                "quote": quote,
            }
        ]
    return payload


def _examples(**overrides) -> dict:
    payload = {
        "sample_turns": [
            {
                "turn_id": str(TURN_HIT),
                "speaker": "坐席",
                "content": "您把短信验证码告诉我，我帮您核对。",
            },
            {
                "turn_id": str(TURN_CLEAR),
                "speaker": "坐席",
                "content": "请不要把验证码告诉任何人。",
            },
        ],
        "positive": [_example(verdict="hit")],
        "negative": [
            _example(
                verdict="clear",
                turn_id=TURN_CLEAR,
                start=3,
                end=12,
                quote="把验证码告诉任何人",
                rationale="坐席明确提示不要提供验证码",
            )
        ],
        "insufficient_evidence": [
            _example(
                verdict="insufficient_evidence",
                evidence=False,
                rationale="当前会话缺少坐席索取验证码的原文",
            )
        ],
        "not_applicable": [
            _example(
                verdict="not_applicable",
                evidence=False,
                rationale="本次会话业务范围不是挂失咨询",
            )
        ],
    }
    payload.update(overrides)
    return payload


def test_contract_evidence_format_is_accepted_and_preserved() -> None:
    examples = RuleExamples.model_validate(_examples())
    validate_rule_examples(
        examples, rule_code="DEMO-PWD-01", version_no=1, severity="high"
    )

    dumped = examples.model_dump(mode="json")
    for bucket in ("positive", "negative", "insufficient_evidence", "not_applicable"):
        for item in dumped[bucket]:
            # 契约 §4 的键必须全部存在；unknown 键被拒绝（extra=forbid）。
            assert set(item) == {
                "rule_id",
                "rule_version",
                "verdict",
                "severity",
                "evidence",
                "rationale",
                "knowledge_refs",
            }
    assert dumped["positive"][0]["evidence"][0] == {
        "turn_id": str(TURN_HIT),
        "start": 2,
        "end": 7,
        "quote": "短信验证码",
    }


def test_bucket_verdicts_are_enforced() -> None:
    examples = RuleExamples.model_validate(
        _examples(positive=[_example(verdict="clear")])
    )
    with pytest.raises(RuleSchemaError, match="positive"):
        validate_rule_examples(
            examples, rule_code="DEMO-PWD-01", version_no=1, severity="high"
        )


def test_hit_example_needs_at_least_one_evidence_span() -> None:
    examples = RuleExamples.model_validate(
        _examples(positive=[_example(verdict="hit", evidence=False)])
    )
    with pytest.raises(RuleSchemaError, match="证据"):
        validate_rule_examples(
            examples, rule_code="DEMO-PWD-01", version_no=1, severity="high"
        )


def test_quote_must_match_the_sample_turn_code_point_range() -> None:
    examples = RuleExamples.model_validate(
        _examples(positive=[_example(verdict="hit", start=2, end=7, quote="短信验证")])
    )
    with pytest.raises(RuleSchemaError, match="原文"):
        validate_rule_examples(
            examples, rule_code="DEMO-PWD-01", version_no=1, severity="high"
        )


def test_unknown_sample_turn_is_rejected() -> None:
    examples = RuleExamples.model_validate(
        _examples(
            positive=[
                _example(
                    verdict="hit",
                    turn_id=UUID("00000000-0000-0000-0000-0000000000ff"),
                )
            ]
        )
    )
    with pytest.raises(RuleSchemaError, match="turn_id"):
        validate_rule_examples(
            examples, rule_code="DEMO-PWD-01", version_no=1, severity="high"
        )


def test_empty_or_reversed_span_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValueError):
        RuleExamples.model_validate(
            _examples(positive=[_example(verdict="hit", start=5, end=5)])
        )
    with pytest.raises(ValueError):
        RuleExamples.model_validate(
            _examples(positive=[_example(verdict="hit", start=9, end=3)])
        )


def test_unknown_verdict_and_extra_keys_are_rejected() -> None:
    with pytest.raises(ValueError):
        RuleExamples.model_validate(
            _examples(positive=[_example(verdict="violation")])
        )
    broken = _example(verdict="hit")
    broken["confidence"] = 0.99
    with pytest.raises(ValueError):
        RuleExamples.model_validate(_examples(positive=[broken]))
    broken_evidence = _example(verdict="hit")
    broken_evidence["evidence"][0]["offset_unit"] = "utf16"
    with pytest.raises(ValueError):
        RuleExamples.model_validate(_examples(positive=[broken_evidence]))


def test_example_rule_identity_and_severity_must_match_the_version() -> None:
    for overrides, code, version, severity in (
        ({"rule_id": "DEMO-OTHER-01"}, "DEMO-PWD-01", 1, "high"),
        ({"rule_version": "2"}, "DEMO-PWD-01", 1, "high"),
        ({"severity": "low"}, "DEMO-PWD-01", 1, "high"),
    ):
        examples = RuleExamples.model_validate(
            _examples(positive=[_example(verdict="hit", **overrides)])
        )
        with pytest.raises(RuleSchemaError):
            validate_rule_examples(
                examples, rule_code=code, version_no=version, severity=severity
            )


def test_applicability_requires_business_types_and_forbids_window_keys() -> None:
    applicability = RuleApplicability.model_validate(
        {
            "business_types": ["lost_card"],
            "channels": ["phone"],
            "conditions": ["坐席主动索取验证码"],
        }
    )
    assert applicability.business_types == ["lost_card"]

    with pytest.raises(ValueError):
        RuleApplicability.model_validate({"business_types": []})
    # 时间窗口由服务端列维护，客户端不能塞进 applicability JSON 冒充。
    with pytest.raises(ValueError):
        RuleApplicability.model_validate(
            {"business_types": ["lost_card"], "valid_from": "2025-01-01T00:00:00Z"}
        )


def test_criteria_require_evidence_requirements() -> None:
    criteria = RuleCriteria.model_validate(
        {
            "evidence_requirements": ["定位坐席索取验证码的原文轮次"],
            "decision_rules": ["仅当坐席主动索取时命中"],
        }
    )
    assert criteria.evidence_requirements
    with pytest.raises(ValueError):
        RuleCriteria.model_validate({"decision_rules": ["缺少证据要求"]})


def test_demo_source_refs_must_stay_on_demo_or_public_hosts() -> None:
    demo_refs = [
        RuleSourceRef.model_validate(
            {
                "kind": "demo_policy",
                "ref": "https://demo.invalid/bank-assistant/demo-policy/lost-card",
            }
        )
    ]
    validate_source_refs(demo_refs, demo_only=True)

    with pytest.raises(RuleSchemaError):
        validate_source_refs(
            [
                RuleSourceRef.model_validate(
                    {"kind": "demo_policy", "ref": "https://bank.example.com/rule"}
                )
            ],
            demo_only=True,
        )
    # 非演示规则不得引用 demo.invalid 的虚构来源。
    with pytest.raises(RuleSchemaError):
        validate_source_refs(demo_refs, demo_only=False)
    # 至少一条来源。
    with pytest.raises(RuleSchemaError):
        validate_source_refs([], demo_only=True)


def test_examples_without_sample_turns_only_get_structural_validation() -> None:
    """API 作者可只提交契约证据对象；没有 sample_turns 时不做原文回验。"""
    payload = _examples(sample_turns=[])
    examples = RuleExamples.model_validate(payload)
    validate_rule_examples(
        examples, rule_code="DEMO-PWD-01", version_no=1, severity="high"
    )
