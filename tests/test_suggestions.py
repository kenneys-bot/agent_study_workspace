"""候选答复端到端：过滤 → 检索 → 生成 → 引用校验 → 状态机（M3，契约 §3/§4）。

seam：
- ``SuggestionService.create_suggestion``：一次请求的公开入口；测试观察持久化
  的 ``suggestions`` / ``model_runs`` 行与返回状态，不直接调用内部校验函数。
- ``POST /api/v1/suggestions``：HTTP 契约（见 ``tests/integration``）。

替身：SQLite 内存库 + ``MemoryVectorStore`` + ``FakeEmbeddingProvider`` +
``httpx.MockTransport`` 假模型网关。全部离线，不联网、不读真实凭据。

场景（计划 M3 验收）：正常、知识缺失、过期版本、条件不足、冲突知识；每个
场景至少 5 条用例。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bank_assistant.assistants.candidate_answer import CandidateAnswerStatus
from bank_assistant.assistants.suggestions import SuggestionService
from bank_assistant.core.model_gateway import (
    ModelCallStatus,
    ModelGateway,
    ModelProvider,
    ProviderSpec,
)
from bank_assistant.infra.db import Base
from bank_assistant.infra.models import (
    Conversation,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
    ModelRun,
    Suggestion,
    Turn,
    User,
)
from bank_assistant.knowledge.embedding import FakeEmbeddingProvider
from bank_assistant.knowledge.indexing import build_index
from bank_assistant.knowledge.lifecycle import KnowledgeStatus, hash_content
from bank_assistant.security.identity import Identity
from tests.index_helpers import MemoryVectorStore

START = datetime(2026, 6, 1, tzinfo=UTC)


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


def respond(payload: dict, finish_reason: str = "stop"):
    """模型返回结构化 JSON 的 MockTransport handler。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=completion(json.dumps(payload, ensure_ascii=False), finish_reason)
        )

    return handler


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


class World:
    """一个隔离的 SQLite 世界：账号、知识、索引、时钟。"""

    def __init__(self, tmp_path: Path) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.scope_id = f"demo-scope-{uuid4().hex[:8]}"
        self.author = self._user("agent", "author")
        self.reviewer = self._user("reviewer", "reviewer")
        self.identity = Identity(
            user_id=self.author.user_id,
            username=self.author.username,
            role=self.author.role,
            scope_id=self.author.scope_id,
        )
        self.provider = FakeEmbeddingProvider()
        self.store = MemoryVectorStore()
        self.index_dir = tmp_path
        self.now = START
        self.index_version: str | None = None

    def _user(self, role: str, label: str) -> User:
        user = User(
            user_id=uuid4(),
            username=f"{label}-{uuid4().hex[:8]}",
            password_hash="$argon2id$unit-test-placeholder",
            role=role,
            scope_id=self.scope_id,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def document(
        self,
        *,
        business_type: str = "lost_card",
        scope_id: str | None = None,
        title: str | None = None,
    ) -> KnowledgeDocument:
        document = KnowledgeDocument(
            document_id=uuid4(),
            title=title or f"演示制度-{uuid4().hex[:8]}",
            source_type="demo_policy",
            source_uri=f"https://demo.invalid/{uuid4().hex}",
            business_type=business_type,
            scope_id=scope_id or self.scope_id,
        )
        self.session.add(document)
        self.session.flush()
        return document

    def version(
        self,
        document: KnowledgeDocument | None = None,
        *,
        content: str,
        status: KnowledgeStatus = KnowledgeStatus.PUBLISHED,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        version_no: int = 1,
    ) -> KnowledgeVersion:
        document = document or self.document()
        version = KnowledgeVersion(
            version_id=uuid4(),
            document_id=document.document_id,
            version_no=version_no,
            content=content,
            content_hash=hash_content(content),
            status=status.value,
            valid_from=valid_from or (START - timedelta(days=30)),
            valid_to=valid_to,
            author_id=self.author.user_id,
            reviewer_id=self.reviewer.user_id,
            revision=1,
        )
        self.session.add(version)
        self.session.flush()
        return version

    def build_index(self, *, business_type: str | None = None) -> str:
        result = build_index(
            self.session,
            provider=self.provider,
            store=self.store,
            scope_id=self.scope_id,
            index_dir=self.index_dir,
            at=self.now,
            business_type=business_type,
        )
        self.index_version = result.index_version
        return result.index_version

    def chunks_for(self, version: KnowledgeVersion) -> list[KnowledgeChunk]:
        return list(
            self.session.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.version_id == version.version_id
                )
            )
        )

    def service(
        self,
        gateway: ModelGateway,
        *,
        rewriter=None,
        clock=None,
        max_distance: float = 0.55,
    ) -> SuggestionService:
        return SuggestionService(
            gateway=gateway,
            embedding_provider=self.provider,
            vector_store=self.store,
            index_dir=self.index_dir,
            rewriter=rewriter,
            clock=clock or (lambda: self.now),
            max_distance=max_distance,
        )

    def conversation(self, contents: list[str]) -> Conversation:
        conversation = Conversation(
            conversation_id=uuid4(),
            scope_id=self.scope_id,
            owner_id=self.author.user_id,
            source_type="text",
            raw_text="\n".join(contents),
            content_hash=hash_content("\n".join(contents)),
            data_classification="synthetic_demo",
        )
        self.session.add(conversation)
        for ordinal, content in enumerate(contents, start=1):
            self.session.add(
                Turn(
                    turn_id=uuid4(),
                    conversation_id=conversation.conversation_id,
                    ordinal=ordinal,
                    speaker="客户" if ordinal % 2 else "坐席",
                    content=content,
                    timestamp=START,
                )
            )
        self.session.commit()
        return conversation

    def last_suggestion(self) -> Suggestion:
        return self.session.scalars(
            select(Suggestion).order_by(Suggestion.created_at.desc())
        ).first()

    def last_run(self) -> ModelRun:
        return self.session.scalars(
            select(ModelRun).order_by(ModelRun.created_at.desc())
        ).first()


LOST_CARD_CONTENT = "【虚构演示制度】挂失受理后，客户需在七个工作日内携带身份证件补卡。"


def _answer_citing(chunk: KnowledgeChunk, quote: str | None = None) -> dict:
    return {
        "status": "answered",
        "answer": "请客户在七个工作日内携带身份证件补卡。",
        "citations": [
            {
                "version_id": str(chunk.version_id),
                "chunk_id": str(chunk.chunk_id),
                "quote": chunk.text if quote is None else quote,
            }
        ],
        "missing_information": [],
    }


class TestNormalAnswered:
    def test_answered_with_valid_citation_is_persisted(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]

        outcome = world.service(model_gateway(respond(_answer_citing(chunk)))).create_suggestion(
            world.session,
            identity=world.identity,
            query=chunk.text,
            business_type="lost_card",
        )

        assert outcome.status is CandidateAnswerStatus.ANSWERED
        assert outcome.suggestion.status == "answered"
        assert outcome.suggestion.answer
        assert [citation["chunk_id"] for citation in outcome.suggestion.citations] == [
            str(chunk.chunk_id)
        ]
        snapshot = outcome.suggestion.retrieval_snapshot
        assert snapshot["index_version"] == world.index_version
        assert snapshot["chunks"][0]["chunk_id"] == str(chunk.chunk_id)
        assert snapshot["chunks"][0]["version_id"] == str(version.version_id)
        assert "score" in snapshot["chunks"][0]
        assert outcome.suggestion.mode == "live"

        run = world.last_run()
        assert run.run_id == outcome.suggestion.run_id
        assert run.model == "deepseek-chat"
        assert run.model_version
        assert run.prompt_version
        assert run.index_version == world.index_version
        assert run.knowledge_snapshot
        assert run.code_sha


NORMAL_CASES = [
    (
        "lost_card",
        "【虚构演示制度】挂失受理后，客户需在七个工作日内携带身份证件补卡。",
    ),
    (
        "lost_card",
        "【虚构演示制度】临时挂失有效期为五天，逾期自动解除，需重新申请。",
    ),
    (
        "credit_card_fee",
        "【虚构演示制度】信用卡年费在首年免收，次年按卡片等级收取，以账单为准。",
    ),
    (
        "credit_card_fee",
        "【虚构演示制度】最低还款额通常为账单金额的百分之十，不含分期本金。",
    ),
    (
        "complaint",
        "【虚构演示制度】投诉受理后三个工作日内首次联系客户，并记录受理编号。",
    ),
]


class TestNormalScenario:
    @pytest.mark.parametrize(("business_type", "content"), NORMAL_CASES)
    def test_answered_with_citation_from_this_recall(
        self, tmp_path: Path, business_type: str, content: str
    ) -> None:
        world = World(tmp_path)
        document = world.document(business_type=business_type)
        version = world.version(document, content=content)
        world.build_index(business_type=business_type)
        chunk = world.chunks_for(version)[0]

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk)))
        ).create_suggestion(
            world.session,
            identity=world.identity,
            query=chunk.text,
            business_type=business_type,
        )

        assert outcome.status is CandidateAnswerStatus.ANSWERED
        assert outcome.suggestion.status == "answered"
        assert outcome.suggestion.citations == [
            {
                "version_id": str(version.version_id),
                "chunk_id": str(chunk.chunk_id),
                "quote": chunk.text,
            }
        ]
        assert outcome.suggestion.business_type == business_type


class TestCitationValidation:
    def test_unknown_chunk_is_rejected_and_answer_not_delivered(
        self, tmp_path: Path
    ) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        unknown = {
            "status": "answered",
            "answer": "凭空回答。",
            "citations": [
                {
                    "version_id": str(version.version_id),
                    "chunk_id": str(uuid4()),
                    "quote": chunk.text,
                }
            ],
            "missing_information": [],
        }

        outcome = world.service(
            model_gateway(respond(unknown))
        ).create_suggestion(world.session, identity=world.identity, query=chunk.text)

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.suggestion.answer == ""
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["rejected_citations"][0][
            "reason"
        ] == "citation_not_in_recall"

    def test_wrong_version_id_for_recalled_chunk_is_rejected(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        payload = _answer_citing(chunk)
        payload["citations"][0]["version_id"] = str(uuid4())

        outcome = world.service(
            model_gateway(respond(payload))
        ).create_suggestion(world.session, identity=world.identity, query=chunk.text)

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["rejected_citations"][0][
            "reason"
        ] == "citation_version_mismatch"

    def test_quote_not_in_chunk_is_rejected(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk, quote="原文没有这句话")))
        ).create_suggestion(world.session, identity=world.identity, query=chunk.text)

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["rejected_citations"][0][
            "reason"
        ] == "quote_not_in_chunk"

    def test_invalid_uuid_citation_is_rejected(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        payload = _answer_citing(chunk)
        payload["citations"][0]["chunk_id"] = "not-a-uuid"

        outcome = world.service(
            model_gateway(respond(payload))
        ).create_suggestion(world.session, identity=world.identity, query=chunk.text)

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["rejected_citations"][0][
            "reason"
        ] == "invalid_id"

    def test_partially_valid_citations_keep_only_valid_ones(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        payload = _answer_citing(chunk)
        payload["citations"].append(
            {
                "version_id": str(version.version_id),
                "chunk_id": str(uuid4()),
                "quote": chunk.text,
            }
        )

        outcome = world.service(
            model_gateway(respond(payload))
        ).create_suggestion(world.session, identity=world.identity, query=chunk.text)

        assert outcome.status is CandidateAnswerStatus.ANSWERED
        assert len(outcome.suggestion.citations) == 1
        assert outcome.suggestion.citations[0]["chunk_id"] == str(chunk.chunk_id)
        assert len(outcome.suggestion.retrieval_snapshot["rejected_citations"]) == 1


class TestMissingKnowledgeScenario:
    @pytest.mark.parametrize(
        "query",
        [
            "如何申请提高信用卡永久额度",
            "境外取现手续费怎么算",
            "我想提前结清房贷",
            "贵金属账户怎么开",
            "如何修改预留手机号",
        ],
    )
    def test_no_evidence_returns_needs_human_without_model_call(
        self, tmp_path: Path, query: str
    ) -> None:
        world = World(tmp_path)
        world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        calls: list[object] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=completion("{}"))

        outcome = world.service(model_gateway(handler)).create_suggestion(
            world.session, identity=world.identity, query=query
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.error_code == "no_evidence"
        assert outcome.suggestion.answer == ""
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["chunks"] == []
        assert calls == []


class TestExpiredScenario:
    @pytest.mark.parametrize(
        "content",
        [
            "【虚构演示制度】过期知识一：挂失后三天内补卡。",
            "【虚构演示制度】过期知识二：挂失后五天内补卡。",
            "【虚构演示制度】过期知识三：挂失后七天内补卡。",
            "【虚构演示制度】过期知识四：挂失后十天内补卡。",
            "【虚构演示制度】过期知识五：挂失后十五天内补卡。",
        ],
    )
    def test_expired_version_is_never_cited(self, tmp_path: Path, content: str) -> None:
        world = World(tmp_path)
        version = world.version(
            content=content,
            valid_from=START - timedelta(days=1),
            valid_to=START + timedelta(days=1),
        )
        world.build_index()
        chunk = world.chunks_for(version)[0]
        world.now = START + timedelta(days=2)

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk)))
        ).create_suggestion(
            world.session, identity=world.identity, query=chunk.text
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.answer == ""
        dropped = outcome.suggestion.retrieval_snapshot["dropped_chunks"]
        assert dropped and dropped[0]["reason"] == "version_expired"


class TestInsufficientScenario:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (
                {
                    "status": "needs_clarification",
                    "answer": "",
                    "citations": [],
                    "missing_information": ["卡号后四位"],
                },
                CandidateAnswerStatus.NEEDS_CLARIFICATION,
            ),
            (
                {
                    "status": "needs_human",
                    "answer": "",
                    "citations": [],
                    "missing_information": ["需人工核实客户身份"],
                },
                CandidateAnswerStatus.NEEDS_HUMAN,
            ),
            (
                {
                    "status": "answered",
                    "answer": "没有引用的答复",
                    "citations": [],
                    "missing_information": [],
                },
                CandidateAnswerStatus.NEEDS_HUMAN,
            ),
            (
                {
                    "status": "needs_clarification",
                    "answer": "",
                    "citations": [],
                    "missing_information": ["客户具体诉求"],
                },
                CandidateAnswerStatus.NEEDS_CLARIFICATION,
            ),
            (
                {
                    "status": "needs_human",
                    "answer": "",
                    "citations": [],
                    "missing_information": ["缺少适用制度"],
                },
                CandidateAnswerStatus.NEEDS_HUMAN,
            ),
        ],
    )
    def test_insufficient_conditions_never_become_answered(
        self, tmp_path: Path, payload: dict, expected: CandidateAnswerStatus
    ) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]

        outcome = world.service(
            model_gateway(respond(payload))
        ).create_suggestion(
            world.session,
            identity=world.identity,
            query=chunk.text,
            business_type="lost_card",
        )

        assert outcome.status is expected
        assert outcome.status is not CandidateAnswerStatus.ANSWERED
        assert outcome.suggestion.answer == ""

    def test_ambiguous_business_type_asks_for_clarification(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        shared = "【虚构演示制度】同一段文字覆盖两个业务，需坐席确认业务类型。"
        lost = world.document(business_type="lost_card")
        fee = world.document(business_type="credit_card_fee")
        world.version(lost, content=shared)
        world.version(fee, content=shared)
        world.build_index()

        outcome = world.service(model_gateway(respond({}))).create_suggestion(
            world.session, identity=world.identity, query=shared
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_CLARIFICATION
        assert outcome.error_code == "ambiguous_business_type"
        assert outcome.suggestion.citations == []


class TestConflictingScenario:
    @pytest.mark.parametrize(
        "content",
        [
            "【虚构演示制度】冲突知识一：补卡需要三个工作日。",
            "【虚构演示制度】冲突知识二：补卡需要五个工作日。",
            "【虚构演示制度】冲突知识三：补卡需要七个工作日。",
            "【虚构演示制度】冲突知识四：补卡需要十个工作日。",
            "【虚构演示制度】冲突知识五：补卡需要十五个工作日。",
        ],
    )
    def test_conflicting_valid_versions_need_clarification(
        self, tmp_path: Path, content: str
    ) -> None:
        world = World(tmp_path)
        document = world.document()
        world.version(document, content=content, version_no=1)
        current = world.version(
            document, content=content + "（修订版）", version_no=2
        )
        world.build_index()
        chunk = world.chunks_for(current)[0]

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk)))
        ).create_suggestion(
            world.session, identity=world.identity, query=chunk.text
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_CLARIFICATION
        assert outcome.error_code == "conflicting_knowledge"
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.answer == ""
        conflict = outcome.suggestion.retrieval_snapshot["conflict"][0]
        assert len(conflict["version_ids"]) == 2


class TestModelFailureScenario:
    @pytest.mark.parametrize(
        "handler",
        [
            lambda request: (_ for _ in ()).throw(httpx.TimeoutException("timed out")),
            lambda request: httpx.Response(503, json={"error": {"message": "down"}}),
            lambda request: httpx.Response(200, json=completion("不是 JSON")),
            lambda request: httpx.Response(
                200, json=completion("", finish_reason="length")
            ),
            lambda request: httpx.Response(
                200, json=completion('{"status": "answered", "answer": "  "}')
            ),
        ],
    )
    def test_model_failure_is_failed_not_answered(self, tmp_path: Path, handler) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]

        outcome = world.service(model_gateway(handler)).create_suggestion(
            world.session,
            identity=world.identity,
            query=chunk.text,
            business_type="lost_card",
        )

        assert outcome.status is CandidateAnswerStatus.FAILED
        assert outcome.error_code == "model_failure"
        assert outcome.suggestion.status == "failed"
        assert outcome.suggestion.answer == ""
        assert outcome.suggestion.citations == []
        assert outcome.model_status is not ModelCallStatus.SUCCEEDED
        assert world.last_run().run_id == outcome.suggestion.run_id


class StaticRewriter:
    """确定性改写替身，避免测试依赖真实模型。"""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[str] = []

    def rewrite(self, query: str):
        from bank_assistant.assistants.suggestions import RewriteOutcome

        self.calls.append(query)
        if isinstance(self.outcome, str):
            return RewriteOutcome(applied=True, query=self.outcome)
        return self.outcome


class TestRetrievalAndRewrite:
    def test_rewrite_records_original_and_rewritten_query(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        rewriter = StaticRewriter(chunk.text)

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk))), rewriter=rewriter
        ).create_suggestion(
            world.session,
            identity=world.identity,
            query="我卡丢了怎么办呀",
            business_type="lost_card",
        )

        assert outcome.status is CandidateAnswerStatus.ANSWERED
        assert outcome.suggestion.query == "我卡丢了怎么办呀"
        assert outcome.suggestion.rewritten_query == chunk.text
        snapshot = outcome.suggestion.retrieval_snapshot
        assert snapshot["query_strategy"] == "rewrite"
        assert snapshot["retrieval_query"] == chunk.text
        assert snapshot["rewrite"]["applied"] is True
        assert rewriter.calls == ["我卡丢了怎么办呀"]

    def test_rewrite_failure_falls_back_to_baseline_explicitly(
        self, tmp_path: Path
    ) -> None:
        from bank_assistant.assistants.suggestions import RewriteOutcome

        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        rewriter = StaticRewriter(RewriteOutcome(applied=False, error="model down"))

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk))), rewriter=rewriter
        ).create_suggestion(
            world.session, identity=world.identity, query=chunk.text
        )

        assert outcome.suggestion.rewritten_query is None
        snapshot = outcome.suggestion.retrieval_snapshot
        assert snapshot["query_strategy"] == "baseline"
        assert snapshot["rewrite"]["applied"] is False
        assert snapshot["rewrite"]["error"] == "model down"

    def test_query_derived_from_conversation_last_turn(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        conversation = world.conversation(["你好", chunk.text])

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk)))
        ).create_suggestion(
            world.session,
            identity=world.identity,
            conversation_id=conversation.conversation_id,
            business_type="lost_card",
        )

        assert outcome.status is CandidateAnswerStatus.ANSWERED
        assert outcome.suggestion.conversation_id == conversation.conversation_id
        assert outcome.suggestion.query == chunk.text

    def test_cross_scope_conversation_is_not_found(self, tmp_path: Path) -> None:
        from bank_assistant.assistants.suggestions import SuggestionNotFound

        world = World(tmp_path)
        other = Conversation(
            conversation_id=uuid4(),
            scope_id="another-scope",
            owner_id=world.author.user_id,
            source_type="text",
            raw_text="其他范围会话",
            content_hash=hash_content("其他范围会话"),
            data_classification="synthetic_demo",
        )
        world.session.add(other)
        world.session.commit()

        with pytest.raises(SuggestionNotFound):
            world.service(model_gateway(respond({}))).create_suggestion(
                world.session,
                identity=world.identity,
                conversation_id=other.conversation_id,
            )

    def test_business_filter_rejects_other_business_knowledge(
        self, tmp_path: Path
    ) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk)))
        ).create_suggestion(
            world.session,
            identity=world.identity,
            query=chunk.text,
            business_type="complaint",
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["chunks"] == []


class TestIndexFailures:
    def test_missing_active_index_is_failed_not_answered(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        world.version(content=LOST_CARD_CONTENT)

        outcome = world.service(model_gateway(respond({}))).create_suggestion(
            world.session, identity=world.identity, query=LOST_CARD_CONTENT
        )

        assert outcome.status is CandidateAnswerStatus.FAILED
        assert outcome.error_code == "index_unavailable"
        assert outcome.suggestion.answer == ""
        assert outcome.suggestion.citations == []

    def test_embedding_drift_is_failed(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        drifted = FakeEmbeddingProvider(model_version="fake-drifted@512")
        service = SuggestionService(
            gateway=model_gateway(respond({})),
            embedding_provider=drifted,
            vector_store=world.store,
            index_dir=world.index_dir,
            clock=lambda: world.now,
        )

        outcome = service.create_suggestion(
            world.session, identity=world.identity, query=chunk.text
        )

        assert outcome.status is CandidateAnswerStatus.FAILED
        assert outcome.error_code == "index_unavailable"
        assert "不一致" in outcome.suggestion.retrieval_snapshot["detail"]

    def test_scope_mismatch_never_uses_other_scope_knowledge(
        self, tmp_path: Path
    ) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]
        other_identity = Identity(
            user_id=uuid4(),
            username="other",
            role="agent",
            scope_id="another-scope",
        )

        outcome = world.service(
            model_gateway(respond(_answer_citing(chunk)))
        ).create_suggestion(
            world.session, identity=other_identity, query=chunk.text
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.error_code == "scope_mismatch"
        assert outcome.suggestion.citations == []
        assert outcome.suggestion.retrieval_snapshot["chunks"] == []


class TestKnowledgeRetiredDuringGeneration:
    def test_only_cited_knowledge_retired_becomes_needs_human(
        self, tmp_path: Path
    ) -> None:
        world = World(tmp_path)
        version = world.version(content=LOST_CARD_CONTENT)
        world.build_index()
        chunk = world.chunks_for(version)[0]

        def handler(request: httpx.Request) -> httpx.Response:
            version.status = KnowledgeStatus.RETIRED.value
            world.session.commit()
            return httpx.Response(
                200,
                json=completion(json.dumps(_answer_citing(chunk), ensure_ascii=False)),
            )

        outcome = world.service(model_gateway(handler)).create_suggestion(
            world.session,
            identity=world.identity,
            query=chunk.text,
            business_type="lost_card",
        )

        assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN
        assert outcome.error_code == "knowledge_retired_during_generation"
        assert outcome.suggestion.answer == ""
        assert outcome.suggestion.citations == []
        ct02 = outcome.suggestion.retrieval_snapshot["ct02"]
        assert ct02["retired"][0]["reason"] == "version_not_published"

    def test_remaining_evidence_triggers_one_regeneration(self, tmp_path: Path) -> None:
        world = World(tmp_path)
        shared = "【虚构演示制度】挂失后七个工作日内凭身份证件补卡。"
        first = world.version(content=shared)
        second = world.version(content=shared)
        world.build_index()
        first_chunk = world.chunks_for(first)[0]
        second_chunk = world.chunks_for(second)[0]
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                first.status = KnowledgeStatus.RETIRED.value
                world.session.commit()
                payload = _answer_citing(first_chunk)
            else:
                payload = _answer_citing(second_chunk)
            return httpx.Response(
                200,
                json=completion(json.dumps(payload, ensure_ascii=False)),
            )

        outcome = world.service(model_gateway(handler)).create_suggestion(
            world.session, identity=world.identity, query=shared
        )

        assert len(calls) == 2
        assert outcome.status is CandidateAnswerStatus.ANSWERED
        assert [c["chunk_id"] for c in outcome.suggestion.citations] == [
            str(second_chunk.chunk_id)
        ]
        ct02 = outcome.suggestion.retrieval_snapshot["ct02"]
        assert ct02["re_retrieved"] is True
        assert ct02["regenerated"] is True
        assert ct02["retired"][0]["chunk_id"] == str(first_chunk.chunk_id)


def test_replay_mode_is_recorded_explicitly(tmp_path: Path) -> None:
    """显式回放必须标记 mode=replay，不能与真实模型结论混同（ADR-0004）。"""
    world = World(tmp_path)
    version = world.version(content=LOST_CARD_CONTENT)
    world.build_index()
    chunk = world.chunks_for(version)[0]
    gateway = ModelGateway(
        replay_responses=[json.dumps(_answer_citing(chunk), ensure_ascii=False)]
    )

    outcome = world.service(gateway).create_suggestion(
        world.session,
        identity=world.identity,
        query=chunk.text,
        business_type="lost_card",
    )

    assert outcome.status is CandidateAnswerStatus.ANSWERED
    assert outcome.suggestion.mode == "replay"
    assert outcome.run.mode == "replay"
    assert outcome.suggestion.retrieval_snapshot["index_version"] == world.index_version
