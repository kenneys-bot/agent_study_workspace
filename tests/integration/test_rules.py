"""规则版本与演示规则集集成测试：真实 PostgreSQL + 真实空库迁移（M4-01，#18）。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 起停一次性
PostgreSQL，并在空库上执行 ``alembic upgrade head``；本文件只假设迁移已完成。

seam：
- HTTP：``POST /api/v1/rules``、``.../versions/{id}/submit|publish|retire``、
  ``GET /api/v1/rules/{rule_id}``；
- 服务层：演示规则集幂等导入（作者提交、另一审核人发布）；
- 数据库行：已发布规则内容不可覆盖（数据库层兜底）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import RuleSet, RuleVersion, User
from bank_assistant.knowledge.demo_seed import DEMO_BUSINESS_TYPES
from bank_assistant.rules.demo_seed import import_demo_rules, load_demo_rules
from bank_assistant.rules.lifecycle import (
    DEFAULT_RULE_CATEGORIES,
    PublishedRuleImmutable,
    RuleStatus,
)
from bank_assistant.rules.schema import EXAMPLES_BUCKETS
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SUFFIX = uuid4().hex[:8]
SCOPE_A = f"rules-scope-a-{SUFFIX}"
SCOPE_B = f"rules-scope-b-{SUFFIX}"
DEMO_PASSWORD = "demo-only-correct-horse-battery"
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def actors() -> dict[str, dict[str, object]]:
    specs = {
        "author": ("agent", SCOPE_A),
        "reviewer": ("reviewer", SCOPE_A),
        "author_b": ("agent", SCOPE_B),
        "reviewer_b": ("reviewer", SCOPE_B),
    }
    created: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        for key, (role, scope) in specs.items():
            user = User(
                user_id=uuid4(),
                username=f"rules-{key}-{SUFFIX}",
                password_hash=hash_password(DEMO_PASSWORD),
                role=role,
                scope_id=scope,
                active=True,
            )
            session.add(user)
            session.flush()
            identity = Identity(
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                scope_id=user.scope_id,
            )
            created[key] = {
                "user_id": user.user_id,
                "role": role,
                "scope_id": scope,
                "token": create_access_token(identity),
                "identity": identity,
            }
    return created


def _headers(actors: dict[str, dict[str, object]], key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {actors[key]['token']}"}


def _identity(actors: dict[str, dict[str, object]], key: str) -> Identity:
    identity = actors[key]["identity"]
    assert isinstance(identity, Identity)
    return identity


def _examples(
    rule_code: str,
    *,
    version_no: int = 1,
    severity: str = "high",
) -> dict[str, object]:
    turn_hit = uuid4()
    turn_clear = uuid4()
    return {
        "sample_turns": [
            {
                "turn_id": str(turn_hit),
                "speaker": "坐席",
                "content": "您把短信验证码报给我，我帮您核对。",
            },
            {
                "turn_id": str(turn_clear),
                "speaker": "坐席",
                "content": "验证码请自己输入，不要告诉我。",
            },
        ],
        "positive": [
            {
                "rule_id": rule_code,
                "rule_version": str(version_no),
                "verdict": "hit",
                "severity": severity,
                "evidence": [
                    {
                        "turn_id": str(turn_hit),
                        "start": 2,
                        "end": 7,
                        "quote": "短信验证码",
                    }
                ],
                "rationale": "坐席主动索取短信验证码",
                "knowledge_refs": [],
            }
        ],
        "negative": [
            {
                "rule_id": rule_code,
                "rule_version": str(version_no),
                "verdict": "clear",
                "severity": severity,
                "evidence": [
                    {
                        "turn_id": str(turn_clear),
                        "start": 0,
                        "end": 6,
                        "quote": "验证码请自己",
                    }
                ],
                "rationale": "坐席提示客户自行输入，属于防护性表述",
                "knowledge_refs": [],
            }
        ],
        "insufficient_evidence": [
            {
                "rule_id": rule_code,
                "rule_version": str(version_no),
                "verdict": "insufficient_evidence",
                "severity": severity,
                "evidence": [],
                "rationale": "缺少坐席索取验证码的原文",
                "knowledge_refs": [],
            }
        ],
        "not_applicable": [
            {
                "rule_id": rule_code,
                "rule_version": str(version_no),
                "verdict": "not_applicable",
                "severity": severity,
                "evidence": [],
                "rationale": "会话业务范围不匹配",
                "knowledge_refs": [],
            }
        ],
    }


def _payload(
    *,
    rule_code: str,
    business_type: str = "lost_card",
    category: str = "password_or_code_request",
    severity: str = "high",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    version_no: int = 1,
    rule_id: UUID | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "rule_code": rule_code,
        "title": f"演示规则 {rule_code}",
        "category": category,
        "description": "虚构演示制度：集成测试用演示质检规则，不对应任何真实机构。",
        "demo_only": True,
        "business_type": business_type,
        "applicability": {
            "business_types": [business_type],
            "channels": ["phone"],
            "conditions": ["坐席引导客户核验身份"],
        },
        "criteria": {
            "evidence_requirements": ["定位坐席索取验证码的原文轮次"],
            "decision_rules": ["否定与转述不能按关键词命中"],
        },
        "exceptions": ["坐席提示客户在官方 App 内自行输入验证码"],
        "severity": severity,
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
        "examples": _examples(rule_code, version_no=version_no, severity=severity),
        "valid_from": (valid_from or (NOW - timedelta(hours=1))).isoformat(),
        "valid_to": valid_to.isoformat() if valid_to else None,
    }
    if rule_id is not None:
        # 追加版本禁止提交规则身份字段。
        for name in ("rule_code", "title", "category", "description", "demo_only"):
            payload.pop(name)
        payload["rule_id"] = str(rule_id)
    return payload


def _create(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    *,
    key: str = "author",
    **kwargs,
):
    rule_code = kwargs.pop("rule_code", f"DEMO-IT-{uuid4().hex[:6].upper()}")
    return client.post(
        "/api/v1/rules",
        json=_payload(rule_code=rule_code, **kwargs),
        headers=_headers(actors, key),
    )


def _transition(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    rule_version_id: str,
    action: str,
    *,
    key: str,
    revision: int,
    **extra: object,
):
    return client.post(
        f"/api/v1/rules/versions/{rule_version_id}/{action}",
        json={"revision": revision, **extra},
        headers=_headers(actors, key),
    )


def _submit(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    rule_version_id: str,
    *,
    revision: int,
    key: str = "author",
) -> dict[str, object]:
    response = _transition(
        client, actors, rule_version_id, "submit", key=key, revision=revision
    )
    assert response.status_code == 200, response.text
    return response.json()


def _publish(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    rule_version_id: str,
    *,
    revision: int,
    key: str = "reviewer",
    decision: str = "approve",
) -> dict[str, object]:
    response = _transition(
        client,
        actors,
        rule_version_id,
        "publish",
        key=key,
        revision=revision,
        review_reason="来源、适用范围与示例已核对",
        decision=decision,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _publish_rule(
    client: TestClient,
    actors: dict[str, dict[str, object]],
    *,
    key: str = "author",
    **kwargs,
) -> dict[str, object]:
    created = _create(client, actors, key=key, **kwargs)
    assert created.status_code == 201, created.text
    draft = created.json()
    submitted = _submit(
        client,
        actors,
        draft["rule_version_id"],
        revision=draft["revision"],
        key=key,
    )
    return _publish(
        client,
        actors,
        draft["rule_version_id"],
        revision=submitted["revision"],
    )


# ---- 迁移 ----


def test_rules_revision_extends_the_current_head() -> None:
    engine = get_engine()
    assert engine.dialect.name == "postgresql"

    with engine.connect() as connection:
        current = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    revisions = {revision.revision for revision in script.walk_revisions()}
    assert "0008_rules" in revisions
    assert script.get_revision("0008_rules").down_revision == "0007_suggestions"
    assert current in revisions

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"rule_sets", "rule_versions"} <= tables

    assert {
        "rule_id",
        "rule_code",
        "title",
        "category",
        "description",
        "scope_id",
        "demo_only",
        "created_at",
        "updated_at",
    } <= {column["name"] for column in inspector.get_columns("rule_sets")}

    assert {
        "rule_version_id",
        "rule_id",
        "version_no",
        "business_type",
        "applicability",
        "criteria",
        "exceptions",
        "severity",
        "source_refs",
        "examples",
        "content_hash",
        "status",
        "demo_only",
        "valid_from",
        "valid_to",
        "author_id",
        "reviewer_id",
        "review_reason",
        "retire_reason",
        "revision",
    } <= {column["name"] for column in inspector.get_columns("rule_versions")}


# ---- 生命周期与授权 ----


def test_create_draft_submit_publish_and_read_published_rule(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    rule_code = f"DEMO-IT-{uuid4().hex[:6].upper()}"
    created = _create(
        client,
        actors,
        rule_code=rule_code,
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=30),
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == RuleStatus.DRAFT.value
    assert draft["version_no"] == 1
    assert draft["revision"] == 1
    assert draft["demo_only"] is True

    # 客户端自报的 scope/author/status 不能出现在创建请求里影响服务端结果。
    with session_scope() as session:
        stored = session.get(RuleVersion, UUID(draft["rule_version_id"]))
        assert stored is not None
        assert stored.author_id == actors["author"]["user_id"]
        assert stored.status == RuleStatus.DRAFT.value
        rule = session.get(RuleSet, stored.rule_id)
        assert rule is not None
        assert rule.scope_id == SCOPE_A
        assert rule.demo_only is True

    submitted = _submit(
        client, actors, draft["rule_version_id"], revision=draft["revision"]
    )
    assert submitted["status"] == RuleStatus.IN_REVIEW.value
    assert submitted["revision"] == 2

    published = _publish(
        client, actors, draft["rule_version_id"], revision=submitted["revision"]
    )
    assert published["status"] == RuleStatus.PUBLISHED.value
    assert published["outcome"] == "published"
    assert published["superseded_rule_version_ids"] == []
    assert published["reviewer_id"] == str(actors["reviewer"]["user_id"])
    assert published["rule_code"] == rule_code
    assert published["severity"] == "high"

    # GET 返回生效的已发布版本与 applicability（含半开窗口）。
    fetched = client.get(
        f"/api/v1/rules/{draft['rule_id']}", headers=_headers(actors, "author")
    )
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["rule_version_id"] == draft["rule_version_id"]
    assert body["status"] == RuleStatus.PUBLISHED.value
    assert body["demo_only"] is True
    assert body["applicability"]["business_types"] == ["lost_card"]
    assert body["applicability"]["valid_from"].startswith(
        (NOW - timedelta(days=1)).date().isoformat()
    )
    assert body["applicability"]["valid_to"] is not None
    assert body["exceptions"]
    assert body["criteria"]["evidence_requirements"]
    # 示例必须是契约 §4 证据 JSON（四个桶，每一项键固定）。
    for bucket in EXAMPLES_BUCKETS:
        for example in body["examples"][bucket]:
            assert set(example) == {
                "rule_id",
                "rule_version",
                "verdict",
                "severity",
                "evidence",
                "rationale",
                "knowledge_refs",
            }
            assert example["rule_id"] == rule_code
            assert example["rule_version"] == "1"


def test_author_cannot_publish_their_own_rule(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    # reviewer 也可以创建草稿；自审在服务层被拒绝（403 SELF_REVIEW_FORBIDDEN）。
    rule_code = f"DEMO-IT-{uuid4().hex[:6].upper()}"
    created = _create(client, actors, key="reviewer", rule_code=rule_code)
    assert created.status_code == 201, created.text
    draft = created.json()
    submitted = _submit(
        client,
        actors,
        draft["rule_version_id"],
        revision=draft["revision"],
        key="reviewer",
    )

    self_publish = _transition(
        client,
        actors,
        draft["rule_version_id"],
        "publish",
        key="reviewer",
        revision=submitted["revision"],
        review_reason="自查",
        decision="approve",
    )
    assert self_publish.status_code == 403, self_publish.text
    assert self_publish.json()["code"] == "SELF_REVIEW_FORBIDDEN"

    # agent 没有审核角色：角色校验先于服务层返回 403 FORBIDDEN。
    agent_publish = _transition(
        client,
        actors,
        draft["rule_version_id"],
        "publish",
        key="author",
        revision=submitted["revision"],
        review_reason="代审",
        decision="approve",
    )
    assert agent_publish.status_code == 403
    assert agent_publish.json()["code"] == "FORBIDDEN"


def test_published_rule_content_is_immutable(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    published = _publish_rule(client, actors)
    rule_version_id = UUID(str(published["rule_version_id"]))
    original_examples = published["examples"]

    with pytest.raises(PublishedRuleImmutable):
        with session_scope() as session:
            version = session.get(RuleVersion, rule_version_id)
            assert version is not None
            version.examples = {**version.examples, "positive": []}

    with session_scope() as session:
        stored = session.get(RuleVersion, rule_version_id)
        assert stored is not None
        # 数据库层拒绝了改写：示例仍是发布时的内容。
        assert stored.examples == original_examples
        assert stored.status == RuleStatus.PUBLISHED.value


def test_overlapping_applicability_window_explicitly_retires_the_old_version(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    rule_code = f"DEMO-IT-{uuid4().hex[:6].upper()}"
    first = _publish_rule(
        client,
        actors,
        rule_code=rule_code,
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=30),
    )
    assert first["superseded_rule_version_ids"] == []

    second_draft = _create(
        client,
        actors,
        rule_code=rule_code,
        rule_id=UUID(str(first["rule_id"])),
        business_type="lost_card",
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=60),
        version_no=2,
    )
    assert second_draft.status_code == 201, second_draft.text
    second = second_draft.json()
    assert second["version_no"] == 2

    submitted = _submit(
        client, actors, second["rule_version_id"], revision=second["revision"]
    )
    published = _publish(
        client, actors, second["rule_version_id"], revision=submitted["revision"]
    )

    # 决策：窗口重叠时旧版本被显式退役，并在响应里回报被取代的版本 ID。
    assert published["superseded_rule_version_ids"] == [first["rule_version_id"]]
    assert published["version_no"] == 2

    with session_scope() as session:
        old = session.get(RuleVersion, UUID(str(first["rule_version_id"])))
        assert old is not None
        assert old.status == RuleStatus.RETIRED.value
        assert old.retire_reason is not None
        assert "重叠" in old.retire_reason or "取代" in old.retire_reason

    fetched = client.get(
        f"/api/v1/rules/{first['rule_id']}", headers=_headers(actors, "author")
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["rule_version_id"] == second["rule_version_id"]

    # 显式退役新版本后，规则不再有生效版本：404 RULE_NOT_EFFECTIVE。
    retired = _transition(
        client,
        actors,
        second["rule_version_id"],
        "retire",
        key="reviewer",
        revision=published["revision"],
        reason="演示规则停用",
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == RuleStatus.RETIRED.value
    missing = client.get(
        f"/api/v1/rules/{first['rule_id']}", headers=_headers(actors, "author")
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "RULE_NOT_EFFECTIVE"


def test_non_overlapping_window_does_not_retire_the_previous_version(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    rule_code = f"DEMO-IT-{uuid4().hex[:6].upper()}"
    first = _publish_rule(
        client,
        actors,
        rule_code=rule_code,
        valid_from=NOW - timedelta(days=10),
        valid_to=NOW - timedelta(days=5),
    )

    second_draft = _create(
        client,
        actors,
        rule_code=rule_code,
        rule_id=UUID(str(first["rule_id"])),
        valid_from=NOW - timedelta(days=1),
        valid_to=NOW + timedelta(days=10),
        version_no=2,
    )
    assert second_draft.status_code == 201, second_draft.text
    second = second_draft.json()
    submitted = _submit(
        client, actors, second["rule_version_id"], revision=second["revision"]
    )
    published = _publish(
        client, actors, second["rule_version_id"], revision=submitted["revision"]
    )

    assert published["superseded_rule_version_ids"] == []
    with session_scope() as session:
        old = session.get(RuleVersion, UUID(str(first["rule_version_id"])))
        assert old is not None
        assert old.status == RuleStatus.PUBLISHED.value

    # 历史窗口仍可查询；默认当前窗口返回第二版。
    at_first = (NOW - timedelta(days=7)).isoformat()
    historical = client.get(
        f"/api/v1/rules/{first['rule_id']}",
        params={"at": at_first},
        headers=_headers(actors, "author"),
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["rule_version_id"] == first["rule_version_id"]


def test_cross_scope_and_invalid_state_paths(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    published = _publish_rule(client, actors)

    cross_scope = client.get(
        f"/api/v1/rules/{published['rule_id']}", headers=_headers(actors, "author_b")
    )
    assert cross_scope.status_code == 404
    assert cross_scope.json()["code"] == "NOT_FOUND"

    # 跨范围用户也不能提交该规则版本。
    cross_submit = _transition(
        client,
        actors,
        str(published["rule_version_id"]),
        "submit",
        key="author_b",
        revision=2,
    )
    assert cross_submit.status_code == 404

    # 已发布版本不能再 submit：409 RULE_STATE_CONFLICT。
    invalid = _transition(
        client,
        actors,
        str(published["rule_version_id"]),
        "submit",
        key="author",
        revision=int(published["revision"]),
    )
    assert invalid.status_code == 409, invalid.text
    assert invalid.json()["code"] == "RULE_STATE_CONFLICT"

    # revision 过期：409 REVISION_CONFLICT。
    stale = _transition(
        client,
        actors,
        str(published["rule_version_id"]),
        "retire",
        key="reviewer",
        revision=int(published["revision"]) - 1,
        reason="过期修订号",
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"


def test_invalid_rule_content_is_rejected_with_422(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    rule_code = f"DEMO-IT-{uuid4().hex[:6].upper()}"
    payload = _payload(rule_code=rule_code)
    # 示例的 rule_id 与规则编码不一致：结构合法但不是同一规则。
    examples = payload["examples"]
    assert isinstance(examples, dict)
    examples["positive"][0]["rule_id"] = "DEMO-MISMATCH-01"

    response = client.post(
        "/api/v1/rules", json=payload, headers=_headers(actors, "author")
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "rule_id" in body["details"]["reason"]


def test_demo_rule_set_import_is_idempotent_and_published(
    client: TestClient, actors: dict[str, dict[str, object]]
) -> None:
    entries = load_demo_rules()
    assert len(entries) >= 6
    assert {entry.business_type for entry in entries} == set(DEMO_BUSINESS_TYPES)
    assert {entry.category for entry in entries} == set(DEFAULT_RULE_CATEGORIES)

    def _import() -> object:
        with session_scope() as session:
            return import_demo_rules(
                session,
                entries,
                author=_identity(actors, "author"),
                reviewer=_identity(actors, "reviewer"),
                scope_id=SCOPE_A,
            )

    first = _import()
    second = _import()
    assert first.created_rules == len(entries)
    assert first.published_versions == len(entries)
    assert second.created_rules == 0
    assert second.published_versions == 0
    assert second.skipped_versions == len(entries)

    with session_scope() as session:
        rule_ids = session.scalars(
            select(RuleSet.rule_id).where(
                RuleSet.scope_id == SCOPE_A,
                RuleSet.rule_code.in_([entry.rule_code for entry in entries]),
            )
        ).all()
        assert len(rule_ids) == len(entries)
        version_count = session.scalar(
            select(func.count())
            .select_from(RuleVersion)
            .where(RuleVersion.rule_id.in_(rule_ids))
        )
        assert version_count == len(entries)
        statuses = session.scalars(
            select(RuleVersion.status).where(RuleVersion.rule_id.in_(rule_ids))
        ).all()
        assert set(statuses) == {RuleStatus.PUBLISHED.value}
        assert session.scalars(
            select(RuleVersion.demo_only).where(RuleVersion.rule_id.in_(rule_ids))
        ).all() == [True] * len(entries)
        high_severity = session.scalars(
            select(RuleVersion.severity).where(
                RuleVersion.rule_id.in_(rule_ids), RuleVersion.severity == "high"
            )
        ).all()
        assert high_severity

    # GET 一条演示规则：published 版本 + applicability + 契约证据格式示例。
    fee_rule = next(entry for entry in entries if entry.rule_code == "DEMO-FEE-01")
    with session_scope() as session:
        fee = session.scalar(
            select(RuleSet).where(
                RuleSet.scope_id == SCOPE_A, RuleSet.rule_code == fee_rule.rule_code
            )
        )
        assert fee is not None
        fee_rule_id = fee.rule_id

    fetched = client.get(
        f"/api/v1/rules/{fee_rule_id}", headers=_headers(actors, "author")
    )
    assert fetched.status_code == 200, fetched.text
    demo = fetched.json()
    assert demo["status"] == RuleStatus.PUBLISHED.value
    assert demo["demo_only"] is True
    assert demo["severity"] == "high"
    assert demo["applicability"]["business_types"] == ["credit_card_fee"]
    assert demo["applicability"]["valid_from"].startswith("2025-01-01")
    assert demo["examples"]["sample_turns"]
    for bucket in EXAMPLES_BUCKETS:
        assert demo["examples"][bucket]
    for example in demo["examples"]["positive"]:
        assert example["verdict"] == "hit"
        assert example["rule_id"] == "DEMO-FEE-01"
        assert example["rule_version"] == "1"
        # 证据区间可按码点回验（契约 §4）。
        turns = {turn["turn_id"]: turn["content"] for turn in demo["examples"]["sample_turns"]}
        for span in example["evidence"]:
            assert turns[span["turn_id"]][span["start"] : span["end"]] == span["quote"]
