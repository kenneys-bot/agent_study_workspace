"""演示规则集：三场景覆盖、展示标记与幂等导入（M4-01，#18）。

演示规则必须全部为虚构内容（``demo_only=true``，说明含「虚构演示制度」），
覆盖挂失/费用/投诉三场景与 M4-02 的六个默认类别，至少一条高风险规则；每条
规则的示例证据必须能按契约 §4 的码点区间在示例原文里回验。

导入走真实生命周期（作者提交 → 另一审核人发布），重复执行不新增版本；
内容变化必须递增 version_no，不能覆盖已发布版本。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from bank_assistant.infra.db import Base
from bank_assistant.infra.models import RuleSet, RuleVersion, User
from bank_assistant.knowledge.demo_seed import DEMO_BUSINESS_TYPES
from bank_assistant.rules.demo_seed import (
    DEMO_RULES_DIR,
    DemoRuleSeedError,
    import_demo_rules,
    load_demo_rules,
)
from bank_assistant.rules.lifecycle import DEFAULT_RULE_CATEGORIES, RuleStatus
from bank_assistant.rules.schema import (
    EXAMPLES_BUCKETS,
    RuleExamples,
    RuleSchemaError,
    demo_marker_present,
)
from bank_assistant.security.identity import Identity

SCOPE = "demo-bank"


def _example(
    verdict: str,
    *,
    rule_code: str,
    rule_version: str = "1",
    severity: str = "high",
    turn_id: str = "00000000-0000-0000-0000-000000000001",
    start: int = 2,
    end: int = 7,
    quote: str = "短信验证码",
    evidence: bool = True,
) -> dict:
    return {
        "rule_id": rule_code,
        "rule_version": rule_version,
        "verdict": verdict,
        "severity": severity,
        "evidence": (
            [{"turn_id": turn_id, "start": start, "end": end, "quote": quote}]
            if evidence
            else []
        ),
        "rationale": f"演示示例：{verdict}",
        "knowledge_refs": [],
    }


def _entry(rule_code: str = "DEMO-PWD-01", **overrides) -> dict:
    """一条最小可用的演示规则；负例用 overrides 逐项破坏。"""
    severity = overrides.get("severity", "high")
    payload = {
        "id": rule_code.lower(),
        "rule_code": rule_code,
        "title": f"演示规则 {rule_code}",
        "category": "password_or_code_request",
        "description": "虚构演示制度：演示质检规则，不针对任何真实机构。",
        "demo_only": True,
        "business_type": "lost_card",
        "version_no": 1,
        "severity": severity,
        "valid_from": "2025-01-01T00:00:00+00:00",
        "valid_to": None,
        "applicability": {
            "business_types": ["lost_card"],
            "channels": ["phone"],
            "conditions": ["坐席主动索取验证码"],
        },
        "criteria": {
            "evidence_requirements": ["定位坐席索取验证码的原文轮次"],
            "decision_rules": ["客户主动报出验证码时不按本规则命中"],
        },
        "exceptions": ["坐席提示客户在官方 App 内自行输入验证码"],
        "source_refs": [
            {
                "kind": "demo_policy",
                "ref": "https://demo.invalid/bank-assistant/demo-policy/lost-card",
            },
            {
                "kind": "demo_rule_set",
                "ref": "https://demo.invalid/bank-assistant/rules/demo-rule-set-v1",
            },
        ],
        "examples": {
            "sample_turns": [
                {
                    "turn_id": "00000000-0000-0000-0000-000000000001",
                    "speaker": "坐席",
                    "content": "您把短信验证码告诉我，我帮您核对。",
                }
            ],
            "positive": [_example("hit", rule_code=rule_code, severity=severity)],
            "negative": [
                _example(
                    "clear",
                    rule_code=rule_code,
                    severity=severity,
                    start=0,
                    end=2,
                    quote="您把",
                )
            ],
            "insufficient_evidence": [
                _example("insufficient_evidence", rule_code=rule_code, severity=severity, evidence=False)
            ],
            "not_applicable": [
                _example("not_applicable", rule_code=rule_code, severity=severity, evidence=False)
            ],
        },
    }
    payload.update(overrides)
    return payload


def _corpus(entries: list[dict], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "rules.json"
    path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def _six_entries() -> list[dict]:
    """覆盖三场景与六类别的最小合规语料。"""
    spec = (
        ("DEMO-PWD-01", "password_or_code_request", "lost_card"),
        ("DEMO-CLAIM-01", "unverified_completion_claim", "lost_card"),
        ("DEMO-FEE-01", "unverified_fee_waiver", "credit_card_fee"),
        ("DEMO-FEE-02", "fee_conflict_with_policy", "credit_card_fee"),
        ("DEMO-CMP-01", "complaint_obstruction", "complaint"),
        ("DEMO-ATT-01", "insulting_language", "complaint"),
    )
    entries = []
    for code, category, business_type in spec:
        entry = _entry(code)
        entry["category"] = category
        entry["business_type"] = business_type
        entry["applicability"] = {
            **entry["applicability"],
            "business_types": [business_type],
        }
        entries.append(entry)
    return entries


def test_demo_rule_corpus_covers_three_scenarios_and_six_default_categories() -> None:
    entries = load_demo_rules()

    assert len(entries) >= 6
    assert {entry.business_type for entry in entries} == set(DEMO_BUSINESS_TYPES)
    assert {entry.category for entry in entries} == set(DEFAULT_RULE_CATEGORIES)
    assert all(entry.demo_only for entry in entries)
    assert all(demo_marker_present(entry.description) for entry in entries)
    assert any(entry.severity == "high" for entry in entries)
    assert len({entry.rule_code for entry in entries}) == len(entries)
    assert all(entry.version_no == 1 for entry in entries)
    assert all(entry.exceptions for entry in entries)


def test_demo_rule_examples_are_contract_evidence_with_verifiable_quotes() -> None:
    for entry in load_demo_rules():
        turns = {turn.turn_id: turn.content for turn in entry.examples.sample_turns}
        assert entry.examples.positive and entry.examples.negative
        for bucket in EXAMPLES_BUCKETS:
            for example in getattr(entry.examples, bucket):
                assert example.rule_id == entry.rule_code
                assert example.rule_version == str(entry.version_no)
                assert example.severity == entry.severity
                for span in example.evidence:
                    # 独立回验：契约 §4 的 start/end 是码点区间。
                    assert turns[span.turn_id][span.start : span.end] == span.quote


def test_demo_rules_are_loaded_from_the_repository_corpus_directory() -> None:
    assert DEMO_RULES_DIR.is_dir()
    assert list(DEMO_RULES_DIR.glob("*.json"))


def test_corpus_with_fewer_than_six_rules_is_rejected(tmp_path: Path) -> None:
    _corpus(_six_entries()[:5], tmp_path)
    with pytest.raises(DemoRuleSeedError, match="6"):
        load_demo_rules(tmp_path)


def test_corpus_missing_a_scenario_is_rejected(tmp_path: Path) -> None:
    entries = [entry for entry in _six_entries() if entry["business_type"] != "complaint"]
    while len(entries) < 6:
        extra = _entry(f"DEMO-CMP-{len(entries) + 10:02d}")
        extra["category"] = "complaint_obstruction"
        extra["business_type"] = "lost_card"
        entries.append(extra)
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError, match="场景"):
        load_demo_rules(tmp_path)


def test_duplicate_rule_code_is_rejected(tmp_path: Path) -> None:
    entries = _six_entries()
    entries.append(_entry("DEMO-PWD-01"))
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError, match="重复"):
        load_demo_rules(tmp_path)


def test_non_demo_rule_is_rejected(tmp_path: Path) -> None:
    entries = _six_entries()
    entries[0]["demo_only"] = False
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError, match="demo_only"):
        load_demo_rules(tmp_path)


def test_unknown_category_is_rejected(tmp_path: Path) -> None:
    entries = _six_entries()
    entries[0]["category"] = "made_up_category"
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError, match="category"):
        load_demo_rules(tmp_path)


def test_demo_description_must_carry_the_demo_marker(tmp_path: Path) -> None:
    entries = _six_entries()
    entries[0]["description"] = "某银行真实内部规则"
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError, match="虚构演示制度"):
        load_demo_rules(tmp_path)


def test_rule_must_list_exemptions(tmp_path: Path) -> None:
    entries = _six_entries()
    entries[0]["exceptions"] = []
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError, match="豁免"):
        load_demo_rules(tmp_path)


def test_demo_source_refs_must_not_point_at_real_bank_hosts(tmp_path: Path) -> None:
    entries = _six_entries()
    entries[0]["source_refs"] = [
        {"kind": "demo_policy", "ref": "https://bank.example.com/internal/rule"}
    ]
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError):
        load_demo_rules(tmp_path)


def test_broken_evidence_span_is_rejected(tmp_path: Path) -> None:
    entries = _six_entries()
    entries[0]["examples"]["positive"][0]["evidence"][0]["quote"] = "验证码告诉我"
    _corpus(entries, tmp_path)
    with pytest.raises(DemoRuleSeedError) as excinfo:
        load_demo_rules(tmp_path)
    assert "原文" in str(excinfo.value)


# ---- 幂等导入（SQLite 单元测试；真实 PostgreSQL 的同一路径在集成测试复跑） ----


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def actors(session: Session) -> tuple[Identity, Identity]:
    author_user = User(
        user_id=uuid4(),
        username="demo-rule-author",
        password_hash="$argon2id$unit-test-placeholder",
        role="agent",
        scope_id=SCOPE,
    )
    reviewer_user = User(
        user_id=uuid4(),
        username="demo-rule-reviewer",
        password_hash="$argon2id$unit-test-placeholder",
        role="reviewer",
        scope_id=SCOPE,
    )
    session.add_all([author_user, reviewer_user])
    session.flush()
    return (
        Identity(
            user_id=author_user.user_id,
            username=author_user.username,
            role=author_user.role,
            scope_id=author_user.scope_id,
        ),
        Identity(
            user_id=reviewer_user.user_id,
            username=reviewer_user.username,
            role=reviewer_user.role,
            scope_id=reviewer_user.scope_id,
        ),
    )


def _import(session: Session, actors, entries=None):
    author, reviewer = actors
    return import_demo_rules(
        session,
        entries or load_demo_rules(),
        author=author,
        reviewer=reviewer,
        scope_id=SCOPE,
    )


def test_import_publishes_every_demo_rule_through_the_real_lifecycle(
    session: Session, actors
) -> None:
    report = _import(session, actors)

    assert report.total_rules == len(load_demo_rules())
    assert report.created_rules == report.total_rules
    assert report.published_versions == report.total_rules

    rule_sets = session.scalars(select(RuleSet)).all()
    assert len(rule_sets) == report.total_rules
    assert {rule.demo_only for rule in rule_sets} == {True}
    assert {rule.scope_id for rule in rule_sets} == {SCOPE}

    versions = session.scalars(select(RuleVersion)).all()
    assert {version.status for version in versions} == {RuleStatus.PUBLISHED.value}
    assert {version.demo_only for version in versions} == {True}
    assert all(version.author_id != version.reviewer_id for version in versions)
    assert {version.severity for version in versions} <= {"low", "medium", "high"}
    assert any(version.severity == "high" for version in versions)


def test_import_is_idempotent_and_never_duplicates_versions(
    session: Session, actors
) -> None:
    first = _import(session, actors)
    second = _import(session, actors)

    assert second.created_rules == 0
    assert second.published_versions == 0
    assert second.skipped_versions == first.total_rules
    assert session.scalar(select(func.count()).select_from(RuleVersion)) == first.total_rules
    assert session.scalar(select(func.count()).select_from(RuleSet)) == first.total_rules


def test_import_refuses_to_overwrite_a_changed_rule_at_the_same_version(
    session: Session, actors
) -> None:
    entries = load_demo_rules()
    _import(session, actors, entries)

    changed = list(entries)
    changed[0] = replace(
        changed[0],
        criteria=changed[0].criteria.model_copy(
            update={"decision_rules": ["改过的条件"]}
        ),
    )
    author, reviewer = actors
    with pytest.raises(DemoRuleSeedError, match="version_no"):
        import_demo_rules(
            session, changed, author=author, reviewer=reviewer, scope_id=SCOPE
        )
