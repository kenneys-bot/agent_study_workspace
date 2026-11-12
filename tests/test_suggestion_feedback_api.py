"""``POST /suggestions/{id}/feedback`` 与 ``GET /suggestions/{id}`` HTTP 契约
（契约 §5，M3-05/T16）。

服务层行为在 ``tests/test_suggestion_feedback.py``。这里验证路由接线、认证、
422/404 错误契约，以及“建议 + 反馈史”读路径（选择扩展单条查询而不是新增
反馈列表接口：一次请求就能看到具体建议记录及其全部反馈）。

反馈**不是正确性标签**：读响应只有建议内容与反馈动作/文本/理由，没有
is_correct / verdict 之类的标签字段，adopt 也不改写建议状态。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import bank_assistant.api.errors  # noqa: F401  # 先初始化 api 包，避免循环导入
from bank_assistant.infra.db import Base, get_db_session
from bank_assistant.infra.models import ModelRun, Suggestion, SuggestionFeedback, User
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity


class ApiWorld:
    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.scope_id = f"demo-scope-{uuid4().hex[:8]}"
        self.author = self._user("agent", self.scope_id)
        self.outsider = self._user("agent", "another-scope")
        self.identity = Identity(
            user_id=self.author.user_id,
            username=self.author.username,
            role=self.author.role,
            scope_id=self.author.scope_id,
        )
        self.app = self._build_app()

    def _user(self, role: str, scope_id: str) -> User:
        user = User(
            user_id=uuid4(),
            username=f"user-{uuid4().hex[:8]}",
            password_hash="$argon2id$unit-test-placeholder",
            role=role,
            scope_id=scope_id,
        )
        self.session.add(user)
        self.session.commit()
        return user

    def suggestion(self, *, scope_id: str | None = None) -> Suggestion:
        run = ModelRun(
            run_id=uuid4(),
            provider="none",
            model="none",
            model_version="none",
            prompt_version="suggestion-v1-test",
            knowledge_snapshot=[],
            index_version=None,
            code_sha="test-sha",
            mode="live",
        )
        suggestion = Suggestion(
            suggestion_id=uuid4(),
            conversation_id=None,
            run_id=run.run_id,
            scope_id=scope_id or self.scope_id,
            query="挂失后多久补卡？",
            rewritten_query=None,
            business_type="lost_card",
            status="answered",
            answer="请在七个工作日内携带身份证件补卡。",
            citations=[],
            missing_information=[],
            retrieval_snapshot={},
            mode="live",
        )
        self.session.add(run)
        self.session.add(suggestion)
        self.session.commit()
        return suggestion

    def _build_app(self):
        from bank_assistant.main import app

        def override_session():
            yield self.session

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_identity] = lambda: self.identity
        return app

    def client(self) -> TestClient:
        return TestClient(self.app)


@pytest.fixture
def api_world():
    world = ApiWorld()
    yield world
    world.app.dependency_overrides.clear()
    world.session.close()


def test_post_feedback_adopt_is_recorded(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()

    response = api_world.client().post(
        f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback",
        json={"action": "adopt", "reason": "坐席确认可直接使用"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["suggestion_id"] == str(suggestion.suggestion_id)
    assert body["action"] == "adopt"
    assert body["reason"] == "坐席确认可直接使用"
    assert body["feedback_id"]
    stored = api_world.session.get(SuggestionFeedback, UUID(body["feedback_id"]))
    assert stored is not None
    assert stored.actor_id == api_world.author.user_id


def test_post_feedback_edit_keeps_edited_text(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()

    response = api_world.client().post(
        f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback",
        json={
            "action": "edit",
            "edited_text": "请在五个工作日内携带身份证件补卡。",
            "reason": "按最新制度补充时限",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["action"] == "edit"
    assert body["edited_text"] == "请在五个工作日内携带身份证件补卡。"
    assert body["reason"] == "按最新制度补充时限"


def test_post_feedback_edit_without_text_is_422(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()

    response = api_world.client().post(
        f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback",
        json={"action": "edit", "reason": "忘了填文本"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "FEEDBACK_INVALID_INPUT"
    assert api_world.session.scalar(select(SuggestionFeedback)) is None


def test_post_feedback_reject_without_reason_is_422(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()

    response = api_world.client().post(
        f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback",
        json={"action": "reject"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "FEEDBACK_INVALID_INPUT"
    assert api_world.session.scalar(select(SuggestionFeedback)) is None


def test_post_feedback_invalid_action_is_422(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()

    response = api_world.client().post(
        f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback",
        json={"action": "approve"},
    )

    assert response.status_code == 422
    assert response.json()["trace_id"]
    assert api_world.session.scalar(select(SuggestionFeedback)) is None


def test_post_feedback_foreign_and_missing_suggestion_are_both_404(
    api_world: ApiWorld,
) -> None:
    foreign = api_world.suggestion(scope_id="another-scope")
    client = api_world.client()

    foreign_response = client.post(
        f"/api/v1/suggestions/{foreign.suggestion_id}/feedback",
        json={"action": "adopt"},
    )
    missing_response = client.post(
        f"/api/v1/suggestions/{uuid4()}/feedback",
        json={"action": "adopt"},
    )

    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404
    assert foreign_response.json()["code"] == "NOT_FOUND"
    assert missing_response.json()["code"] == "NOT_FOUND"
    assert foreign_response.json()["message"] == missing_response.json()["message"]
    assert api_world.session.scalar(select(SuggestionFeedback)) is None


def test_post_feedback_requires_authentication(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()
    api_world.app.dependency_overrides.pop(get_current_identity, None)

    response = TestClient(api_world.app).post(
        f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback",
        json={"action": "adopt"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_get_suggestion_returns_feedback_history(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()
    client = api_world.client()
    for payload in (
        {"action": "adopt", "reason": "先采用"},
        {"action": "edit", "edited_text": "改写后的答复", "reason": "补充说明"},
        {"action": "reject", "reason": "后续发现引用过期"},
    ):
        response = client.post(
            f"/api/v1/suggestions/{suggestion.suggestion_id}/feedback", json=payload
        )
        assert response.status_code == 201, response.text

    response = client.get(f"/api/v1/suggestions/{suggestion.suggestion_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggestion_id"] == str(suggestion.suggestion_id)
    assert body["status"] == "answered"
    assert body["answer"] == suggestion.answer
    assert [item["action"] for item in body["feedback"]] == [
        "adopt",
        "edit",
        "reject",
    ]
    assert body["feedback"][1]["edited_text"] == "改写后的答复"
    assert body["feedback"][2]["reason"] == "后续发现引用过期"
    # 反馈不是正确性标签：响应里没有 is_correct / verdict 之类的字段。
    assert "is_correct" not in body
    assert "verdict" not in body["feedback"][0]


def test_get_suggestion_foreign_and_missing_are_both_404(api_world: ApiWorld) -> None:
    foreign = api_world.suggestion(scope_id="another-scope")
    client = api_world.client()

    foreign_response = client.get(f"/api/v1/suggestions/{foreign.suggestion_id}")
    missing_response = client.get(f"/api/v1/suggestions/{uuid4()}")

    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404
    assert foreign_response.json()["code"] == "NOT_FOUND"
    assert missing_response.json()["code"] == "NOT_FOUND"


def test_get_suggestion_requires_authentication(api_world: ApiWorld) -> None:
    suggestion = api_world.suggestion()
    api_world.app.dependency_overrides.pop(get_current_identity, None)

    response = TestClient(api_world.app).get(
        f"/api/v1/suggestions/{suggestion.suggestion_id}"
    )

    assert response.status_code == 401
