"""采纳反馈记录（T16，M3-05，契约 §2/§3/§5）。

seam：
- ``SuggestionFeedbackService.record_feedback``：登记一条坐席反馈；
- ``SuggestionFeedbackService.suggestion_history``：读回某条建议及其反馈史。

替身：SQLite 内存库；建议行由测试直接写入（反馈用例不依赖模型网关）。
反馈是**反馈**，不是正确性标签；本文件显式验证采纳不会改写建议状态/答复，
也不存在任何 label/correctness 字段。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bank_assistant.assistants import suggestion_feedback as feedback_module
from bank_assistant.assistants.suggestion_feedback import (
    SuggestionFeedbackInputError,
    SuggestionFeedbackService,
)
from bank_assistant.assistants.suggestions import SuggestionNotFound
from bank_assistant.infra.db import Base
from bank_assistant.infra.models import (
    FeedbackAppendOnly,
    ModelRun,
    Suggestion,
    SuggestionFeedback,
    User,
)
from bank_assistant.security.identity import Identity

NOW = datetime(2026, 7, 1, tzinfo=UTC)


class World:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.scope_id = "demo-scope"
        self._now = NOW
        self.author = self._user("agent")
        self.identity = Identity(
            user_id=self.author.user_id,
            username=self.author.username,
            role=self.author.role,
            scope_id=self.author.scope_id,
        )

    def _user(self, role: str, scope_id: str | None = None) -> User:
        user = User(
            user_id=uuid4(),
            username=f"user-{uuid4().hex[:8]}",
            password_hash="$argon2id$unit-test-placeholder",
            role=role,
            scope_id=scope_id or self.scope_id,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def suggestion(self, *, scope_id: str | None = None, status: str = "answered") -> Suggestion:
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
            status=status,
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

    def _clock(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now

    def service(self) -> SuggestionFeedbackService:
        return SuggestionFeedbackService(clock=self._clock)


def test_adopt_feedback_is_persisted_with_reason() -> None:
    world = World()
    suggestion = world.suggestion()

    feedback = world.service().record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="adopt",
        reason="直接可用",
    )

    assert isinstance(feedback.feedback_id, UUID)
    assert feedback.suggestion_id == suggestion.suggestion_id
    assert feedback.actor_id == world.author.user_id
    assert feedback.action == "adopt"
    assert feedback.edited_text is None
    assert feedback.reason == "直接可用"
    stored = world.session.get(SuggestionFeedback, feedback.feedback_id)
    assert stored is not None and stored.action == "adopt"


def test_edit_feedback_keeps_edited_text_and_reason() -> None:
    world = World()
    suggestion = world.suggestion()

    feedback = world.service().record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="edit",
        edited_text="请在五个工作日内携带身份证件补卡。",
        reason="制度时限需按最新版本表述",
    )

    assert feedback.action == "edit"
    assert feedback.edited_text == "请在五个工作日内携带身份证件补卡。"
    assert feedback.reason == "制度时限需按最新版本表述"
    assert world.session.get(SuggestionFeedback, feedback.feedback_id).edited_text == (
        "请在五个工作日内携带身份证件补卡。"
    )


def test_edit_without_edited_text_is_rejected() -> None:
    world = World()
    suggestion = world.suggestion()

    with pytest.raises(SuggestionFeedbackInputError) as excinfo:
        world.service().record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action="edit",
            reason="想改但没填文本",
        )

    assert excinfo.value.code == "FEEDBACK_INVALID_INPUT"
    assert excinfo.value.http_status == 422
    assert world.session.scalar(select(SuggestionFeedback)) is None


def test_edit_with_blank_edited_text_is_rejected() -> None:
    world = World()
    suggestion = world.suggestion()

    with pytest.raises(SuggestionFeedbackInputError):
        world.service().record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action="edit",
            edited_text="   ",
        )

    assert world.session.scalar(select(SuggestionFeedback)) is None


def test_reject_records_reason_and_requires_it() -> None:
    world = World()
    suggestion = world.suggestion()

    feedback = world.service().record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="reject",
        reason="引用的制度已过期",
    )
    assert feedback.action == "reject"
    assert feedback.reason == "引用的制度已过期"
    assert feedback.edited_text is None

    with pytest.raises(SuggestionFeedbackInputError):
        world.service().record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action="reject",
        )

    with pytest.raises(SuggestionFeedbackInputError):
        world.service().record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action="reject",
            reason="  ",
        )


def test_unknown_action_is_rejected() -> None:
    world = World()
    suggestion = world.suggestion()

    with pytest.raises(SuggestionFeedbackInputError):
        world.service().record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action="approve",
        )

    assert world.session.scalar(select(SuggestionFeedback)) is None


def test_adopt_cannot_carry_edited_text() -> None:
    """adopt 是原样采用；改写文本只属于 edit，避免把两种语义混在一起。"""
    world = World()
    suggestion = world.suggestion()

    with pytest.raises(SuggestionFeedbackInputError):
        world.service().record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action="adopt",
            edited_text="被偷偷改过的文本",
        )


def test_foreign_or_missing_suggestion_is_not_found() -> None:
    world = World()
    foreign = world.suggestion(scope_id="another-scope")
    service = world.service()

    with pytest.raises(SuggestionNotFound):
        service.record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=foreign.suggestion_id,
            action="adopt",
        )
    with pytest.raises(SuggestionNotFound):
        service.record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=uuid4(),
            action="adopt",
        )
    assert world.session.scalar(select(SuggestionFeedback)) is None


def test_second_feedback_is_appended_without_mutating_the_first() -> None:
    world = World()
    suggestion = world.suggestion()
    service = world.service()

    first = service.record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="adopt",
        reason="可直接使用",
    )
    first_snapshot = (first.action, first.edited_text, first.reason)

    second = service.record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="edit",
        edited_text="修改后的答复",
        reason="补充时限",
    )

    assert second.feedback_id != first.feedback_id
    world.session.expire_all()
    stored_first = world.session.get(SuggestionFeedback, first.feedback_id)
    assert (stored_first.action, stored_first.edited_text, stored_first.reason) == (
        first_snapshot
    )
    assert len(
        list(
            world.session.scalars(
                select(SuggestionFeedback).where(
                    SuggestionFeedback.suggestion_id == suggestion.suggestion_id
                )
            )
        )
    ) == 2


def test_feedback_cannot_be_updated_after_it_is_written() -> None:
    world = World()
    suggestion = world.suggestion()
    feedback = world.service().record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="adopt",
        reason="直接使用",
    )

    feedback.reason = "事后改写理由"
    with pytest.raises(FeedbackAppendOnly):
        world.session.commit()
    world.session.rollback()

    world.session.expire_all()
    stored = world.session.get(SuggestionFeedback, feedback.feedback_id)
    assert stored.reason == "直接使用"


def test_adopt_does_not_turn_the_suggestion_into_a_correctness_label() -> None:
    """采纳只记录坐席动作：建议的状态、答复与内容都不因此改变。"""
    world = World()
    suggestion = world.suggestion()
    before = (
        suggestion.status,
        suggestion.answer,
        suggestion.citations,
        suggestion.updated_at,
    )

    world.service().record_feedback(
        world.session,
        identity=world.identity,
        suggestion_id=suggestion.suggestion_id,
        action="adopt",
        reason="坐席采用",
    )

    world.session.expire_all()
    reloaded = world.session.get(Suggestion, suggestion.suggestion_id)
    assert (
        reloaded.status,
        reloaded.answer,
        reloaded.citations,
        reloaded.updated_at,
    ) == before
    assert not hasattr(SuggestionFeedback, "verdict")
    assert not hasattr(SuggestionFeedback, "is_correct")


def test_docs_state_feedback_is_not_a_correctness_label() -> None:
    """把“反馈不是正确性标签”的产品约束钉在文档与代码里。"""
    model_doc = SuggestionFeedback.__doc__ or ""
    service_doc = feedback_module.__doc__ or ""

    assert "不是正确性标签" in model_doc
    assert "ground-truth" in model_doc
    assert "adopt" in model_doc
    assert "不是正确性标签" in service_doc
    assert "ground-truth" in service_doc


def test_history_returns_feedback_in_creation_order() -> None:
    world = World()
    suggestion = world.suggestion()
    service = world.service()
    for action, kwargs in (
        ("adopt", {"reason": "先采用"}),
        ("edit", {"edited_text": "改写后的答复", "reason": "补充说明"}),
        ("reject", {"reason": "后续发现引用过期"}),
    ):
        service.record_feedback(
            world.session,
            identity=world.identity,
            suggestion_id=suggestion.suggestion_id,
            action=action,
            **kwargs,
        )

    stored, history = service.suggestion_history(
        world.session, identity=world.identity, suggestion_id=suggestion.suggestion_id
    )

    assert stored.suggestion_id == suggestion.suggestion_id
    assert [item.action for item in history] == ["adopt", "edit", "reject"]
    assert history[1].edited_text == "改写后的答复"
    assert history[2].reason == "后续发现引用过期"
