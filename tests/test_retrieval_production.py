"""T37（#37）：title_chunk 索引文本 + 确定性重排落到**生产**链路的回归测试。

T36（#36）只在评测 ``RetrievalHarness`` 上验证了改进；本文件盯住生产：

(a) 分块原文与码点区间不变——``embedding_text=title_chunk`` 只改 embedding
    输入，``KnowledgeChunk.text``/向量库文档仍是 ``content[start:end]``；
(b) embedding 输入包含标题（生产默认形态），查询向量仍用原问题；
(c) 重建产生新 ``index_version`` + 校准哈希，且内容身份（``index_content_sha256``）
    与检索排序两次一致（ADR-0005：版本唯一，内容可复现）；
(d) 候选答复链路按生产默认值应用重排，并把 ``rerank_status``/参数写进
    ``retrieval_snapshot``；
(e) 重排不破引用不变式：引用只能来自本次授权召回，quote 必须匹配原文。

全部离线：SQLite 内存库 + ``MemoryVectorStore`` + ``FakeEmbeddingProvider`` +
``httpx.MockTransport`` 替身模型。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from bank_assistant.assistants.candidate_answer import CandidateAnswerStatus
from bank_assistant.infra.db import Base
from bank_assistant.infra.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeVersion,
    User,
)
from bank_assistant.knowledge.embedding import (
    CALIBRATION_TEXT,
    FakeEmbeddingProvider,
    calibration_vector_sha256,
)
from bank_assistant.knowledge.indexing import (
    ChunkRecord,
    build_index,
    collection_name,
    manifest_path,
    read_manifest,
    search_index,
)
from bank_assistant.knowledge.lifecycle import KnowledgeStatus, hash_content
from tests.index_helpers import MemoryVectorStore
from tests.test_suggestions import START, World, model_gateway, respond

TITLE = "挂失渠道与补卡说明"
CONTENT = (
    "客户可通过手机银行、客服电话或柜面办理挂失。\n"
    "挂失受理后，客户需在七个工作日内携带身份证件补卡，工本费以柜台公示为准。"
)
QUERY = "挂失可以通过哪些渠道办理"


class RecordingProvider:
    """记录每次 embedding 输入的确定性 provider（用于断言索引文本形态）。"""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self._inner = FakeEmbeddingProvider()

    @property
    def model_version(self) -> str:
        return self._inner.model_version

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def embed(self, texts):  # noqa: ANN001 - 与协议签名一致
        self.inputs.extend(texts)
        return self._inner.embed(texts)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session


def _seed(
    session: Session,
) -> tuple[User, KnowledgeDocument, KnowledgeVersion]:
    scope = f"demo-scope-{uuid4().hex[:8]}"
    author = User(
        user_id=uuid4(),
        username=f"author-{uuid4().hex[:8]}",
        password_hash="$argon2id$unit-test-placeholder",
        role="agent",
        scope_id=scope,
    )
    document = KnowledgeDocument(
        document_id=uuid4(),
        title=TITLE,
        source_type="demo_policy",
        source_uri=f"https://demo.invalid/{uuid4().hex}",
        business_type="lost_card",
        scope_id=scope,
    )
    session.add_all([author, document])
    session.flush()
    version = KnowledgeVersion(
        version_id=uuid4(),
        document_id=document.document_id,
        version_no=1,
        content=CONTENT,
        content_hash=hash_content(CONTENT),
        status=KnowledgeStatus.PUBLISHED.value,
        valid_from=START,
        valid_to=None,
        author_id=author.user_id,
        revision=1,
    )
    session.add(version)
    session.flush()
    return author, document, version


def _active_index_version(index_dir: Path) -> str:
    pointer = json.loads((index_dir / "active.json").read_text("utf-8"))
    return pointer["index_version"]


def _records(store: MemoryVectorStore, index_version: str) -> list[ChunkRecord]:
    return list(store.collections[collection_name(index_version)].values())


def test_title_chunk_keeps_original_text_and_offsets(
    session: Session, tmp_path: Path
) -> None:
    """(a) 只改 embed 输入：分块原文、码点区间与向量库文档都不变。"""
    _, document, _ = _seed(session)
    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()

    result = build_index(
        session,
        provider=provider,
        store=store,
        scope_id=document.scope_id,
        index_dir=tmp_path,
        at=START,
        embedding_text="title_chunk",
    )

    records = _records(store, result.index_version)
    assert records
    rows = list(
        session.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.index_version == result.index_version
            )
        )
    )
    assert len(rows) == len(records) == result.chunk_count
    for record, row in zip(
        sorted(records, key=lambda item: (item.start_offset, item.end_offset)),
        sorted(rows, key=lambda item: (item.start_offset, item.end_offset)),
        strict=True,
    ):
        # Chroma 的 document 与 knowledge_chunks.text 都必须仍是原文区间。
        assert record.text == CONTENT[record.start_offset : record.end_offset]
        assert row.text == record.text
        assert (row.start_offset, row.end_offset) == (
            record.start_offset,
            record.end_offset,
        )
        assert TITLE not in record.text
        assert row.embedding_model_version == provider.model_version


def test_embedding_input_includes_title_but_query_is_raw(
    session: Session, tmp_path: Path
) -> None:
    """(b) 索引 embedding 输入含标题；查询只 embed 原问题，不拼标题。"""
    _, document, _ = _seed(session)
    provider = RecordingProvider()
    store = MemoryVectorStore()

    build_index(
        session,
        provider=provider,
        store=store,
        scope_id=document.scope_id,
        index_dir=tmp_path,
        at=START,
        embedding_text="title_chunk",
    )

    chunk_inputs = [text for text in provider.inputs if text != CALIBRATION_TEXT]
    assert chunk_inputs, "索引必须真的 embed 分块"
    for text in chunk_inputs:
        assert text.startswith(f"{TITLE}\n")
        assert text.split("\n", 1)[1] in CONTENT

    provider.inputs.clear()
    search_index(
        store,
        provider,
        index_version=_active_index_version(tmp_path),
        query=QUERY,
        top_k=2,
    )
    assert provider.inputs == [QUERY]


def test_rebuild_is_deterministic_in_content_identity_and_ranking(
    session: Session, tmp_path: Path
) -> None:
    """(c) 新 index_version + 校准哈希，内容身份与排序两次一致。"""
    _, document, _ = _seed(session)
    provider = FakeEmbeddingProvider()
    store = MemoryVectorStore()

    def build(embedding_text: str):
        return build_index(
            session,
            provider=provider,
            store=store,
            scope_id=document.scope_id,
            index_dir=tmp_path,
            at=START,
            embedding_text=embedding_text,
        )

    first = build("title_chunk")
    first_manifest = read_manifest(manifest_path(tmp_path, first.index_version))
    first_hits = search_index(
        store, provider, index_version=first.index_version, query=QUERY, top_k=5
    )

    second = build("title_chunk")
    second_manifest = read_manifest(manifest_path(tmp_path, second.index_version))
    second_hits = search_index(
        store, provider, index_version=second.index_version, query=QUERY, top_k=5
    )

    # ADR-0005：每次构建都是新版本，旧集合保留（可回滚）。
    assert second.index_version != first.index_version
    assert collection_name(first.index_version) in store.collection_names()
    assert collection_name(second.index_version) in store.collection_names()

    # 同一语料/provider/参数：校准哈希与内容身份一致，排序一致。
    assert first_manifest["embedding_text"] == "title_chunk"
    assert second_manifest["embedding_text"] == "title_chunk"
    assert first_manifest["calibration_vector_sha256"] == calibration_vector_sha256(
        provider
    )
    assert (
        first_manifest["index_content_sha256"]
        == second_manifest["index_content_sha256"]
    )
    assert (
        first_manifest["calibration_vector_sha256"]
        == second_manifest["calibration_vector_sha256"]
    )
    signature = lambda hits: [  # noqa: E731 - 测试内小工具
        (hit.version_id, hit.start_offset, hit.end_offset) for hit in hits
    ]
    assert signature(second_hits) == signature(first_hits)

    # 换索引文本形态必须改变内容身份（标题参与索引的证据）。
    body_only = build("chunk")
    body_manifest = read_manifest(manifest_path(tmp_path, body_only.index_version))
    assert (
        body_manifest["index_content_sha256"]
        != first_manifest["index_content_sha256"]
    )


def test_production_defaults_match_adopted_eval_config() -> None:
    """生产配置默认值与 T36 采用的评测配置同义（title_chunk + α=0.2）。"""
    from bank_assistant.config.settings import settings
    from bank_assistant.evaluation.config import load_retrieval_options

    options = load_retrieval_options(Path("configs/eval/replay.yaml"))
    assert settings.RETRIEVAL_EMBEDDING_TEXT == options.embedding_text == "title_chunk"
    assert settings.RETRIEVAL_RERANK_ALPHA == pytest.approx(options.rerank_alpha) == 0.2
    assert settings.RETRIEVAL_RERANK_POOL == options.rerank_pool


def test_suggestion_snapshot_records_rerank_params_and_order(tmp_path: Path) -> None:
    """(d) 生产候选答复链路应用重排并记录状态/参数/前后顺序。"""
    world = World(tmp_path)
    world.version(content="挂失渠道包括手机银行、客服电话与柜面，补卡需身份证件。")
    world.version(content="信用卡年费按卡片等级收取，首年免收，以账单为准。")
    world.build_index()
    assert world.index_version is not None

    payload = {
        "status": "needs_clarification",
        "answer": "",
        "citations": [],
        "missing_information": ["客户具体诉求"],
    }
    outcome = world.service(
        model_gateway(respond(payload)),
        max_distance=2.0,
        rerank_alpha=0.2,
        rerank_pool=20,
    ).create_suggestion(
        world.session,
        identity=world.identity,
        query="挂失渠道有哪些",
        business_type="lost_card",
    )

    snapshot = outcome.suggestion.retrieval_snapshot
    assert snapshot["rerank_status"] == "applied"
    rerank = snapshot["rerank"]
    assert rerank["alpha"] == pytest.approx(0.2)
    assert rerank["pool"] == 20
    assert rerank["mode"] == "lexical_char_ngram"
    assert rerank["candidate_count"] == len(snapshot["chunks"]) == 2
    # 交付顺序 = 重排后顺序，且是原候选集合的一个排列（不新增候选）。
    assert rerank["delivered"] == [chunk["chunk_id"] for chunk in snapshot["chunks"]]
    assert sorted(rerank["delivered"]) == sorted(rerank["before"])
    assert len(rerank["delivered"]) <= 5

    # 关掉 α 时链路显式记录 disabled，且不发生重排（可单独回退）。
    world2 = World(tmp_path / "no-rerank")
    world2.version(content="挂失渠道包括手机银行、客服电话与柜面。")
    world2.build_index()
    outcome2 = world2.service(
        model_gateway(respond(payload)), max_distance=2.0, rerank_alpha=0.0
    ).create_suggestion(
        world2.session,
        identity=world2.identity,
        query="挂失渠道有哪些",
        business_type="lost_card",
    )
    snapshot2 = outcome2.suggestion.retrieval_snapshot
    assert snapshot2["rerank_status"] == "disabled"
    assert snapshot2["rerank"]["before"] == snapshot2["rerank"]["delivered"]


def test_rerank_keeps_citation_invariant_and_quotes_match_original_text(
    tmp_path: Path,
) -> None:
    """(e) 重排不破引用不变式：只看授权召回，quote 逐字匹配原文区间。"""
    world = World(tmp_path)
    cited_version = world.version(content="挂失受理后需在七个工作日内补卡。")
    other_version = world.version(content="信用卡年费首年免收，以账单为准。")
    world.build_index()
    cited_chunk = world.chunks_for(cited_version)[0]
    other_chunk = world.chunks_for(other_version)[0]

    # 模型引用一个存在但**未被本次召回**的分块：必须被拒绝。
    invalid = {
        "status": "answered",
        "answer": "请按制度办理。",
        "citations": [
            {
                "version_id": str(other_chunk.version_id),
                "chunk_id": str(other_chunk.chunk_id),
                "quote": other_chunk.text,
            }
        ],
        "missing_information": [],
    }
    outcome = world.service(
        model_gateway(respond(invalid)), rerank_alpha=0.2
    ).create_suggestion(
        world.session,
        identity=world.identity,
        query=cited_chunk.text,
        business_type="lost_card",
    )

    snapshot = outcome.suggestion.retrieval_snapshot
    assert snapshot["rerank_status"] in {"applied", "insufficient_candidates"}
    assert [chunk["chunk_id"] for chunk in snapshot["chunks"]] == [
        str(cited_chunk.chunk_id)
    ]
    rejected = snapshot["rejected_citations"]
    assert [item["reason"] for item in rejected] == ["citation_not_in_recall"]
    assert outcome.suggestion.citations == []
    assert outcome.status is CandidateAnswerStatus.NEEDS_HUMAN

    # 有效引用：quote 必须逐字出现在原文区间（不是拼了标题的 embedding 输入）。
    valid = {
        "status": "answered",
        "answer": "请客户在七个工作日内补卡。",
        "citations": [
            {
                "version_id": str(cited_chunk.version_id),
                "chunk_id": str(cited_chunk.chunk_id),
                "quote": cited_chunk.text,
            }
        ],
        "missing_information": [],
    }
    outcome2 = world.service(
        model_gateway(respond(valid)), rerank_alpha=0.2
    ).create_suggestion(
        world.session,
        identity=world.identity,
        query=cited_chunk.text,
        business_type="lost_card",
    )
    assert outcome2.status is CandidateAnswerStatus.ANSWERED
    assert outcome2.suggestion.citations[0]["chunk_id"] == str(cited_chunk.chunk_id)
    assert cited_chunk.text == cited_version.content[
        cited_chunk.start_offset : cited_chunk.end_offset
    ]
    assert outcome2.suggestion.citations[0]["quote"] == cited_chunk.text
    assert TITLE not in outcome2.suggestion.citations[0]["quote"]
