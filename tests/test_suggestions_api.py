"""``POST /api/v1/suggestions`` HTTP 契约（契约 §5，M3-04/T15）。

服务层行为在 ``tests/test_suggestions.py``；这里只验证路由接线、认证与错误
契约：业务状态 201 + 持久化，系统失败 5xx 且保留 failed 行，跨范围会话 404，
输入缺失 422。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import bank_assistant.api.errors  # noqa: F401  # 先初始化 api 包，避免 authorization 循环导入
from bank_assistant.assistants.candidate_answer import CandidateAnswerStatus
from bank_assistant.assistants.suggestions import SuggestionService
from bank_assistant.core.model_gateway import (
    ModelGateway,
    ModelProvider,
    ProviderSpec,
)
from bank_assistant.infra.db import Base, get_db_session
from bank_assistant.infra.models import (
    Conversation,
    KnowledgeChunk,
    Suggestion,
    User,
)
from bank_assistant.knowledge.embedding import FakeEmbeddingProvider
from bank_assistant.knowledge.indexing import build_index
from bank_assistant.knowledge.lifecycle import hash_content
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity
from tests.index_helpers import MemoryVectorStore
from tests.test_suggestions import (
    LOST_CARD_CONTENT,
    START,
    completion,
    model_gateway,
    respond,
)

def _completion(content: str) -> dict:
    return completion(content)


class ApiWorld:
    def __init__(self, tmp_path: Path) -> None:
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.scope_id = f"demo-scope-{uuid4().hex[:8]}"
        self.author = User(
            user_id=uuid4(),
            username=f"author-{uuid4().hex[:8]}",
            password_hash="$argon2id$unit-test-placeholder",
            role="agent",
            scope_id=self.scope_id,
        )
        self.reviewer = User(
            user_id=uuid4(),
            username=f"reviewer-{uuid4().hex[:8]}",
            password_hash="$argon2id$unit-test-placeholder",
            role="reviewer",
            scope_id=self.scope_id,
        )
        self.session.add_all([self.author, self.reviewer])
        self.session.flush()
        self.identity = Identity(
            user_id=self.author.user_id,
            username=self.author.username,
            role=self.author.role,
            scope_id=self.author.scope_id,
        )
        self.provider = FakeEmbeddingProvider()
        self.store = MemoryVectorStore()
        self.index_dir = tmp_path
        from datetime import timedelta

        from bank_assistant.infra.models import (
            KnowledgeDocument,
            KnowledgeVersion,
        )
        from bank_assistant.knowledge.lifecycle import KnowledgeStatus

        document = KnowledgeDocument(
            document_id=uuid4(),
            title="演示制度：挂失补卡",
            source_type="demo_policy",
            source_uri=f"https://demo.invalid/{uuid4().hex}",
            business_type="lost_card",
            scope_id=self.scope_id,
        )
        self.session.add(document)
        self.session.flush()
        self.version = KnowledgeVersion(
            version_id=uuid4(),
            document_id=document.document_id,
            version_no=1,
            content=LOST_CARD_CONTENT,
            content_hash=hash_content(LOST_CARD_CONTENT),
            status=KnowledgeStatus.PUBLISHED.value,
            valid_from=START - timedelta(days=10),
            valid_to=None,
            author_id=self.author.user_id,
            reviewer_id=self.reviewer.user_id,
            revision=1,
        )
        self.session.add(self.version)
        self.session.flush()
        build_index(
            self.session,
            provider=self.provider,
            store=self.store,
            scope_id=self.scope_id,
            index_dir=self.index_dir,
            at=START,
        )
        self.chunk: KnowledgeChunk = self.session.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.version_id == self.version.version_id
            )
        ).first()

        self.app = self._build_app()

    def _build_app(self):
        from bank_assistant.main import app
        from bank_assistant.api.routers.suggestions import (
            get_embedding_provider,
            get_index_dir,
            get_model_gateway,
            get_query_rewriter,
            get_vector_store,
        )

        def override_session():
            yield self.session

        self.gateway = model_gateway(respond(self._answer()))
        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_identity] = lambda: self.identity
        app.dependency_overrides[get_model_gateway] = lambda: self.gateway
        app.dependency_overrides[get_embedding_provider] = lambda: self.provider
        app.dependency_overrides[get_vector_store] = lambda: self.store
        app.dependency_overrides[get_index_dir] = lambda: self.index_dir
        app.dependency_overrides[get_query_rewriter] = lambda: None
        return app

    def _answer(self) -> dict:
        return {
            "status": "answered",
            "answer": "请客户在七个工作日内携带身份证件补卡。",
            "citations": [
                {
                    "version_id": str(self.version.version_id),
                    "chunk_id": str(self.chunk.chunk_id),
                    "quote": self.chunk.text,
                }
            ],
            "missing_information": [],
        }

    def client(self) -> TestClient:
        return TestClient(self.app)

    def set_gateway(self, gateway: ModelGateway) -> None:
        from bank_assistant.api.routers.suggestions import get_model_gateway

        self.gateway = gateway
        self.app.dependency_overrides[get_model_gateway] = lambda: gateway


@pytest.fixture
def api_world(tmp_path: Path):
    world = ApiWorld(tmp_path)
    yield world
    world.app.dependency_overrides.clear()


def test_post_suggestion_returns_answered_with_validated_citation(
    api_world: ApiWorld,
) -> None:
    response = api_world.client().post(
        "/api/v1/suggestions",
        json={
            "query": api_world.chunk.text,
            "business_type": "lost_card",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "answered"
    assert body["citations"][0]["chunk_id"] == str(api_world.chunk.chunk_id)
    assert body["retrieval_snapshot"]["index_version"]
    assert body["mode"] == "live"
    assert body["run_id"]
    stored = api_world.session.get(Suggestion, UUID(body["suggestion_id"]))
    assert stored is not None and stored.status == "answered"


def test_post_suggestion_requires_conversation_or_query(
    api_world: ApiWorld,
) -> None:
    response = api_world.client().post("/api/v1/suggestions", json={})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["trace_id"]


def test_post_suggestion_cross_scope_conversation_is_404(
    api_world: ApiWorld,
) -> None:
    other = Conversation(
        conversation_id=uuid4(),
        scope_id="another-scope",
        owner_id=api_world.author.user_id,
        source_type="text",
        raw_text="其他范围",
        content_hash=hash_content("其他范围"),
        data_classification="synthetic_demo",
    )
    api_world.session.add(other)
    api_world.session.commit()

    response = api_world.client().post(
        "/api/v1/suggestions",
        json={"conversation_id": str(other.conversation_id)},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_post_suggestion_model_failure_returns_5xx_and_keeps_failed_row(
    api_world: ApiWorld,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    api_world.set_gateway(model_gateway(handler))

    response = api_world.client().post(
        "/api/v1/suggestions",
        json={"query": api_world.chunk.text, "business_type": "lost_card"},
    )

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "MODEL_TIMEOUT"
    assert body["details"]["status"] == "failed"
    stored = api_world.session.get(
        Suggestion, UUID(body["details"]["suggestion_id"])
    )
    assert stored is not None
    assert stored.status == "failed"
    assert stored.status != CandidateAnswerStatus.ANSWERED.value
    assert body["trace_id"]


def test_post_suggestion_requires_authentication(
    api_world: ApiWorld,
) -> None:
    api_world.app.dependency_overrides.pop(get_current_identity, None)

    response = TestClient(api_world.app).post(
        "/api/v1/suggestions", json={"query": "挂失"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
