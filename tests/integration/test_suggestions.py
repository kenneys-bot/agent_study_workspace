"""候选答复在真实 PostgreSQL + 演示知识上的端到端验证（T15/T16，M3-04/M3-05）。

由 ``make test-integration`` 在空库迁移后运行。本文件验证：

- ``0007_suggestions`` / ``0008_suggestion_feedback`` 迁移后
  ``suggestions`` / ``model_runs`` / ``suggestion_feedback`` 真实存在；
- 使用 T14 的 32 条演示知识与版本化 Chroma 索引，``POST /api/v1/suggestions``
  返回 answered 且引用来自本次召回；
- 无效引用/无证据/模型失败/生成期间退役分别落到正确状态，失败不会被伪装成
  业务状态；
- 每次请求都留下 ``model_runs`` 与 ``suggestions`` 记录；
- 坐席采纳/编辑/拒绝反馈可追加、可读回；反馈不是正确性标签，adopt 不改写
  建议状态；二次反馈不改写历史。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from bank_assistant.api.routers.suggestions import (
    get_embedding_provider,
    get_index_dir,
    get_model_gateway,
    get_query_rewriter,
    get_vector_store,
)
from bank_assistant.core.model_gateway import (
    ModelGateway,
    ModelProvider,
    ProviderSpec,
)
from bank_assistant.infra.db import get_engine, session_scope
from bank_assistant.infra.models import (
    KnowledgeChunk,
    KnowledgeVersion,
    ModelRun,
    Suggestion,
    SuggestionFeedback,
    User,
)
from bank_assistant.knowledge.demo_seed import import_demo_knowledge, load_demo_entries
from bank_assistant.knowledge.embedding import FakeEmbeddingProvider
from bank_assistant.knowledge.indexing import (
    ChromaVectorStore,
    build_index,
    read_manifest,
    manifest_path,
)
from bank_assistant.knowledge.retrieval import fetch_retrievable_versions
from bank_assistant.knowledge.service import (
    create_draft_version,
    publish_version,
    submit_version,
)
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import hash_password
from bank_assistant.security.tokens import create_access_token

pytestmark = pytest.mark.integration

SUFFIX = uuid4().hex[:8]
SCOPE = f"demo-suggest-{SUFFIX}"
OTHER_SCOPE = f"demo-suggest-other-{SUFFIX}"
NOW = datetime.now(UTC)
DEMO_PASSWORD = "demo-only-correct-horse-battery"


def completion(content: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def model_gateway(handler) -> ModelGateway:
    spec = ProviderSpec(
        provider=ModelProvider.DEEPSEEK,
        base_url="https://api.deepseek.com/v1",
        api_key="test-key",
        model="deepseek-chat",
    )
    return ModelGateway(
        providers={ModelProvider.DEEPSEEK: spec},
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


@pytest.fixture(scope="module")
def client() -> TestClient:
    from bank_assistant.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def actors() -> dict[str, dict[str, object]]:
    specs = {
        "author": ("agent", SCOPE),
        "reviewer": ("reviewer", SCOPE),
        "outsider": ("agent", OTHER_SCOPE),
    }
    created: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        for key, (role, scope_id) in specs.items():
            user = User(
                user_id=uuid4(),
                username=f"{key}-{SUFFIX}",
                password_hash=hash_password(DEMO_PASSWORD),
                role=role,
                scope_id=scope_id,
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
                "identity": identity,
                "scope_id": scope_id,
                "token": create_access_token(identity),
            }
    return created


def _headers(actors, key: str = "author") -> dict[str, str]:
    return {"Authorization": f"Bearer {actors[key]['token']}"}


@pytest.fixture(scope="module")
def seeded(actors) -> None:
    entries = load_demo_entries()
    with session_scope() as session:
        report = import_demo_knowledge(
            session,
            entries,
            author=actors["author"]["identity"],
            reviewer=actors["reviewer"]["identity"],
            scope_id=SCOPE,
        )
    assert report.published_versions == len(entries)


def _build_index(tmp_path) -> SimpleNamespace:
    provider = FakeEmbeddingProvider()
    store = ChromaVectorStore(tmp_path / "chroma")
    with session_scope() as session:
        built = build_index(
            session,
            provider=provider,
            store=store,
            scope_id=SCOPE,
            index_dir=tmp_path,
            at=NOW,
        )
    with session_scope() as session:
        version = next(
            version
            for version in fetch_retrievable_versions(
                session, scope_id=SCOPE, business_type="lost_card", at=NOW
            )
        )
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.version_id == version.version_id
            )
        )
    assert chunk is not None
    return SimpleNamespace(
        provider=provider,
        store=store,
        index_dir=tmp_path,
        built=built,
        version=version,
        chunk=chunk,
    )


@pytest.fixture(scope="module")
def index(seeded, actors, tmp_path_factory) -> SimpleNamespace:
    return _build_index(tmp_path_factory.mktemp("index"))


@pytest.fixture(scope="module", autouse=True)
def app_overrides(client: TestClient, index: SimpleNamespace):
    app = client.app
    app.dependency_overrides[get_embedding_provider] = lambda: index.provider
    app.dependency_overrides[get_vector_store] = lambda: index.store
    app.dependency_overrides[get_index_dir] = lambda: index.index_dir
    app.dependency_overrides[get_query_rewriter] = lambda: None
    yield
    for dependency in (
        get_embedding_provider,
        get_vector_store,
        get_index_dir,
        get_query_rewriter,
        get_model_gateway,
    ):
        app.dependency_overrides.pop(dependency, None)


def _use_gateway(client: TestClient, gateway: ModelGateway) -> None:
    client.app.dependency_overrides[get_model_gateway] = lambda: gateway


def _answer_citing(chunk: KnowledgeChunk, answer: str = "请客户按演示制度办理。") -> dict:
    return {
        "status": "answered",
        "answer": answer,
        "citations": [
            {
                "version_id": str(chunk.version_id),
                "chunk_id": str(chunk.chunk_id),
                "quote": chunk.text,
            }
        ],
        "missing_information": [],
    }


def _respond(payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=completion(json.dumps(payload, ensure_ascii=False))
        )

    return handler


def test_suggestions_tables_exist_after_migration() -> None:
    tables = set(inspect(get_engine()).get_table_names())
    assert {"suggestions", "model_runs"} <= tables


def test_post_suggestion_answered_with_valid_citation(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    _use_gateway(client, model_gateway(_respond(_answer_citing(index.chunk))))

    response = client.post(
        "/api/v1/suggestions",
        json={"query": index.chunk.text, "business_type": "lost_card"},
        headers=_headers(actors),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["citations"] == [
        {
            "version_id": str(index.version.version_id),
            "chunk_id": str(index.chunk.chunk_id),
            "quote": index.chunk.text,
        }
    ]
    snapshot = body["retrieval_snapshot"]
    assert snapshot["index_version"] == index.built.index_version
    manifest = read_manifest(
        manifest_path(index.index_dir, index.built.index_version)
    )
    assert snapshot["calibration_vector_sha256"] == manifest["calibration_vector_sha256"]
    assert snapshot["chunks"][0]["chunk_id"] == str(index.chunk.chunk_id)
    assert snapshot["chunks"][0]["version_id"] == str(index.version.version_id)

    with session_scope() as session:
        suggestion = session.get(Suggestion, UUID(body["suggestion_id"]))
        run = session.get(ModelRun, UUID(body["run_id"]))
    assert suggestion is not None and suggestion.status == "answered"
    assert run is not None
    assert run.model == "deepseek-chat"
    assert run.index_version == index.built.index_version
    assert run.code_sha
    assert run.knowledge_snapshot


def test_post_suggestion_rejects_unknown_chunk_citation(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    payload = _answer_citing(index.chunk)
    payload["citations"][0]["chunk_id"] = str(uuid4())
    _use_gateway(client, model_gateway(_respond(payload)))

    response = client.post(
        "/api/v1/suggestions",
        json={"query": index.chunk.text, "business_type": "lost_card"},
        headers=_headers(actors),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_human"
    assert body["answer"] == ""
    assert body["citations"] == []
    assert body["retrieval_snapshot"]["rejected_citations"][0]["reason"] == (
        "citation_not_in_recall"
    )


def test_post_suggestion_without_evidence_is_needs_human(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    calls: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=completion("{}"))

    _use_gateway(client, model_gateway(handler))

    response = client.post(
        "/api/v1/suggestions",
        json={"query": "如何申请火星移民签证"},
        headers=_headers(actors),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_human"
    assert body["citations"] == []
    assert body["retrieval_snapshot"]["error"] == "no_evidence"
    assert calls == []


def test_post_suggestion_model_failure_is_5xx_with_failed_row(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "down"}})

    _use_gateway(client, model_gateway(handler))

    response = client.post(
        "/api/v1/suggestions",
        json={"query": index.chunk.text, "business_type": "lost_card"},
        headers=_headers(actors),
    )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "MODEL_UNAVAILABLE"
    assert body["details"]["status"] == "failed"
    with session_scope() as session:
        stored = session.get(Suggestion, UUID(body["details"]["suggestion_id"]))
    assert stored is not None and stored.status == "failed"
    assert stored.answer == ""


def test_post_suggestion_other_scope_gets_no_cross_scope_knowledge(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    _use_gateway(client, model_gateway(_respond(_answer_citing(index.chunk))))

    response = client.post(
        "/api/v1/suggestions",
        json={"query": index.chunk.text, "business_type": "lost_card"},
        headers=_headers(actors, "outsider"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "needs_human"
    assert body["citations"] == []
    assert body["retrieval_snapshot"]["error"] == "scope_mismatch"


def test_post_suggestion_knowledge_retired_during_generation(
    client: TestClient, actors, index: SimpleNamespace, tmp_path
) -> None:
    """生成期间退役：不交付旧引用；无剩余证据时转 needs_human（CT-02）。"""
    with session_scope() as session:
        document, version = create_draft_version(
            session,
            actors["author"]["identity"],
            title=f"退役测试-{SUFFIX}",
            source_type="demo_policy",
            source_uri=f"https://demo.invalid/ct02/{SUFFIX}",
            business_type="lost_card",
            content="【虚构演示制度】生成期间会被退役的挂失知识，不得继续引用。",
            valid_from=NOW - timedelta(days=1),
        )
        submit_version(
            session,
            actors["author"]["identity"],
            version.version_id,
            expected_revision=version.revision,
        )
        publish_version(
            session,
            actors["reviewer"]["identity"],
            version.version_id,
            expected_revision=version.revision,
            review_reason="CT-02 集成测试",
        )
    local = _build_index(tmp_path)
    # 只召回这条新版本：用它的分块文本作为问题。
    with session_scope() as session:
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.version_id == version.version_id
            )
        )
    assert chunk is not None
    target_version_id = version.version_id

    def retire():
        with session_scope() as session:
            stored = session.get(KnowledgeVersion, target_version_id)
            stored.status = "retired"
            stored.retire_reason = "集成测试：生成期间退役"

    def handler(request: httpx.Request) -> httpx.Response:
        retire()
        return httpx.Response(
            200,
            json=completion(
                json.dumps(_answer_citing(chunk), ensure_ascii=False)
            ),
        )

    app = client.app
    app.dependency_overrides[get_embedding_provider] = lambda: local.provider
    app.dependency_overrides[get_vector_store] = lambda: local.store
    app.dependency_overrides[get_index_dir] = lambda: local.index_dir
    app.dependency_overrides[get_model_gateway] = lambda: model_gateway(handler)
    try:
        response = client.post(
            "/api/v1/suggestions",
            json={"query": chunk.text, "business_type": "lost_card"},
            headers=_headers(actors),
        )
    finally:
        app.dependency_overrides[get_embedding_provider] = lambda: index.provider
        app.dependency_overrides[get_vector_store] = lambda: index.store
        app.dependency_overrides[get_index_dir] = lambda: index.index_dir
        app.dependency_overrides.pop(get_model_gateway, None)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "needs_human"
    assert body["answer"] == ""
    assert body["citations"] == []
    assert body["retrieval_snapshot"]["error"] == (
        "knowledge_retired_during_generation"
    )


# ---------------------------------------------------------------- T16 反馈记录

def _create_suggestion(client: TestClient, actors, index: SimpleNamespace) -> dict:
    _use_gateway(client, model_gateway(_respond(_answer_citing(index.chunk))))
    response = client.post(
        "/api/v1/suggestions",
        json={"query": index.chunk.text, "business_type": "lost_card"},
        headers=_headers(actors),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_suggestion_feedback_table_exists_after_migration() -> None:
    tables = set(inspect(get_engine()).get_table_names())
    assert "suggestion_feedback" in tables
    columns = {
        column["name"]
        for column in inspect(get_engine()).get_columns("suggestion_feedback")
    }
    assert {
        "feedback_id",
        "suggestion_id",
        "actor_id",
        "action",
        "edited_text",
        "reason",
        "created_at",
    } <= columns


def test_feedback_adopt_edit_reject_is_recorded_and_history_readable(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    suggestion = _create_suggestion(client, actors, index)
    suggestion_id = suggestion["suggestion_id"]
    url = f"/api/v1/suggestions/{suggestion_id}/feedback"

    adopt = client.post(
        url, json={"action": "adopt", "reason": "坐席确认可直接使用"},
        headers=_headers(actors),
    )
    assert adopt.status_code == 201, adopt.text
    assert adopt.json()["action"] == "adopt"
    assert adopt.json()["reason"] == "坐席确认可直接使用"

    edit = client.post(
        url,
        json={
            "action": "edit",
            "edited_text": "请在五个工作日内携带身份证件补卡。",
            "reason": "补充演示制度时限",
        },
        headers=_headers(actors),
    )
    assert edit.status_code == 201, edit.text
    assert edit.json()["edited_text"] == "请在五个工作日内携带身份证件补卡。"

    reject = client.post(
        url, json={"action": "reject", "reason": "引用的制度已过期"},
        headers=_headers(actors),
    )
    assert reject.status_code == 201, reject.text
    assert reject.json()["reason"] == "引用的制度已过期"

    detail = client.get(f"/api/v1/suggestions/{suggestion_id}", headers=_headers(actors))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["suggestion_id"] == suggestion_id
    # 采纳没有把建议改写成“正确”：状态与答复保持生成时的值。
    assert body["status"] == suggestion["status"] == "answered"
    assert body["answer"] == suggestion["answer"]
    assert [item["action"] for item in body["feedback"]] == [
        "adopt",
        "edit",
        "reject",
    ]
    assert body["feedback"][1]["edited_text"] == "请在五个工作日内携带身份证件补卡。"
    assert body["feedback"][2]["reason"] == "引用的制度已过期"
    assert "is_correct" not in body

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(SuggestionFeedback)
                .where(SuggestionFeedback.suggestion_id == UUID(suggestion_id))
                .order_by(SuggestionFeedback.created_at)
            )
        )
        assert [row.action for row in rows] == ["adopt", "edit", "reject"]
        assert rows[0].actor_id is not None


def test_second_feedback_appends_without_mutating_the_first(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    suggestion = _create_suggestion(client, actors, index)
    url = f"/api/v1/suggestions/{suggestion['suggestion_id']}/feedback"

    first = client.post(
        url, json={"action": "adopt", "reason": "第一次采纳"},
        headers=_headers(actors),
    ).json()
    client.post(
        url,
        json={"action": "reject", "reason": "复核后认为引用过期"},
        headers=_headers(actors),
    )

    detail = client.get(
        f"/api/v1/suggestions/{suggestion['suggestion_id']}",
        headers=_headers(actors),
    ).json()
    by_id = {item["feedback_id"]: item for item in detail["feedback"]}
    assert by_id[first["feedback_id"]]["action"] == "adopt"
    assert by_id[first["feedback_id"]]["reason"] == "第一次采纳"
    assert len(detail["feedback"]) == 2


def test_feedback_validation_and_scope_are_enforced(
    client: TestClient, actors, index: SimpleNamespace
) -> None:
    suggestion = _create_suggestion(client, actors, index)
    url = f"/api/v1/suggestions/{suggestion['suggestion_id']}/feedback"

    edit_without_text = client.post(
        url, json={"action": "edit"}, headers=_headers(actors)
    )
    assert edit_without_text.status_code == 422
    assert edit_without_text.json()["code"] == "FEEDBACK_INVALID_INPUT"

    reject_without_reason = client.post(
        url, json={"action": "reject"}, headers=_headers(actors)
    )
    assert reject_without_reason.status_code == 422
    assert reject_without_reason.json()["code"] == "FEEDBACK_INVALID_INPUT"

    invalid_action = client.post(
        url, json={"action": "approve"}, headers=_headers(actors)
    )
    assert invalid_action.status_code == 422

    foreign = client.post(
        url, json={"action": "adopt"}, headers=_headers(actors, "outsider")
    )
    missing = client.post(
        f"/api/v1/suggestions/{uuid4()}/feedback",
        json={"action": "adopt"},
        headers=_headers(actors),
    )
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "NOT_FOUND"

    foreign_detail = client.get(
        f"/api/v1/suggestions/{suggestion['suggestion_id']}",
        headers=_headers(actors, "outsider"),
    )
    assert foreign_detail.status_code == 404

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(SuggestionFeedback).where(
                    SuggestionFeedback.suggestion_id == UUID(suggestion["suggestion_id"])
                )
            )
        )
    assert rows == []
