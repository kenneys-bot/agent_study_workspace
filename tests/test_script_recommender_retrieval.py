"""#5 检索闭环回归测试。

覆盖四个确认缺陷：嵌套查询文本、候选数超过 count、重排静默失败、
filters 在路由/业务层被丢弃。
"""

from bank_assistant.assistants.script_recommender.context_analyzer import (
    ConversationContext,
)
from bank_assistant.assistants.script_recommender.intent_recognizer import UserIntent
from bank_assistant.assistants.script_recommender.script_recommender import (
    RecommendedScript,
    ScriptRecommender,
)
from bank_assistant.core.vector_db import VectorDBClient


class StubVectorDB:
    """可记录调用参数的向量库替身；按 n_results 返回候选。"""

    def __init__(self, result_count=6):
        self.result_count = result_count
        self.calls = []

    def query_with_score(self, query_text, n_results=5, where=None):
        self.calls.append(
            {"query_text": query_text, "n_results": n_results, "where": where}
        )
        limit = min(n_results, self.result_count)
        return [
            {
                "id": f"id{i}",
                "document": f"账单查询推荐话术 {i}",
                "metadata": {"title": f"话术{i}"},
                "distance": 0.1 * i,
            }
            for i in range(limit)
        ]


def _context():
    return ConversationContext(topic="账单查询", stage="主阶段")


def _intent():
    return UserIntent(intent_type="查询")


def _recommender(stub, reranker=None):
    kwargs = {"llm_client": None, "vector_db": stub}
    if reranker is not None:
        kwargs["reranker"] = reranker
    return ScriptRecommender(**kwargs)


class _FakeCollection:
    """记录 Chroma 实际收到的查询参数。"""

    def __init__(self, documents):
        self.documents = documents
        self.queries = []

    def query(self, query_texts, n_results, where=None):
        self.queries.append(
            {"query_texts": query_texts, "n_results": n_results, "where": where}
        )
        docs = self.documents[:n_results]
        return {
            "documents": [docs],
            "metadatas": [[{} for _ in docs]],
            "ids": [[f"id{i}" for i in range(len(docs))]],
            "distances": [[0.1 * i for i in range(len(docs))]],
        }


def _vector_db_with(collection):
    db = object.__new__(VectorDBClient)
    db.collection_name = "test"
    db._client = None
    db._collection = collection
    return db


def test_query_with_score_flattens_nested_query_list():
    """query_with_score 必须让 Chroma 收到扁平的 list[str]，而不是嵌套列表。"""
    collection = _FakeCollection(["a"])
    db = _vector_db_with(collection)

    results = db.query_with_score(query_text=[["查询文本"]], n_results=1)

    assert collection.queries[0]["query_texts"] == ["查询文本"]
    assert results[0]["document"] == "a"


def test_recommend_scripts_never_returns_more_than_count():
    """count=3 时即使向量库按 count*2 返回 6 条，响应也只能有 3 条。"""
    stub = StubVectorDB(result_count=6)
    recommender = _recommender(stub)

    scripts = recommender.recommend_scripts(_context(), _intent(), count=3)

    assert len(scripts) == 3


class RecordingReranker:
    """记录收到的情境，并返回倒序以证明重排真的生效。"""

    def __init__(self):
        self.contexts = []

    def rerank(self, scripts, context):
        self.contexts.append(context)
        return list(reversed(scripts))


class ExplodingReranker:
    """模拟可替换重排组件执行失败。"""

    def rerank(self, scripts, context):
        raise RuntimeError("reranker unavailable")


def test_recommend_scripts_passes_real_context_to_reranker():
    """重排必须收到真实情境，而不是 None；显式记录 applied。"""
    stub = StubVectorDB(result_count=6)
    reranker = RecordingReranker()
    recommender = _recommender(stub, reranker)
    context = _context()

    scripts = recommender.recommend_scripts(context, _intent(), count=3)

    assert reranker.contexts == [context]
    assert recommender.last_rerank.status == "applied"
    assert len(scripts) == 3


def test_rank_scripts_skips_with_recorded_reason_when_context_missing():
    """传 None 不再抛异常后静默保持顺序，而是跳过并记录原因。"""
    recommender = _recommender(StubVectorDB())
    scripts = [RecommendedScript(script_id="a", content="A"),
               RecommendedScript(script_id="b", content="B")]

    result = recommender.rank_scripts(scripts, None)

    assert result == scripts
    assert recommender.last_rerank.status == "skipped"
    assert recommender.last_rerank.reason == "missing_context"


def test_rerank_failure_is_exposed_with_machine_readable_code():
    """重排失败要记录错误码并暴露，不能假装成功。"""
    recommender = _recommender(StubVectorDB(result_count=6), ExplodingReranker())

    scripts = recommender.recommend_scripts(_context(), _intent(), count=3)

    assert len(scripts) == 3
    assert recommender.last_rerank.status == "failed"
    assert recommender.last_rerank.error_code == "RERANK_INTERNAL_ERROR"


def _post_recommend(recommender, payload):
    from fastapi.testclient import TestClient

    from uuid import uuid4

    from bank_assistant.api.routers.script_recommender import (
        get_script_recommender,
    )
    from bank_assistant.main import app
    from bank_assistant.security.authorization import get_current_identity
    from bank_assistant.security.identity import Identity

    app.dependency_overrides[get_script_recommender] = lambda: recommender
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=uuid4(),
        username="legacy-api-test",
        role="agent",
        scope_id="legacy-api-scope",
    )
    try:
        return TestClient(app).post(
            "/api/v1/script-recommender/recommend-scripts", json=payload
        )
    finally:
        app.dependency_overrides.clear()


def test_api_forwards_filters_to_vector_query():
    """路由层不能丢弃 filters；它必须一路到达向量检索的 where。"""
    stub = StubVectorDB(result_count=6)
    recommender = _recommender(stub, RecordingReranker())
    filters = {"business_type": "信用卡", "status": "published"}

    response = _post_recommend(
        recommender,
        {
            "context": {"topic": "账单查询", "stage": "主阶段"},
            "intent": {"intent_type": "查询", "confidence": 0.9},
            "count": 3,
            "filters": filters,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["scripts"]) == 3
    assert stub.calls[0]["where"] == filters
    assert payload["rerank_status"] == "applied"
    assert payload["rerank_error_code"] is None


def test_api_exposes_rerank_failure_code_instead_of_pretending_success():
    """重排失败时响应仍是兼容结构，但必须带机器可读状态与错误码。"""
    recommender = _recommender(StubVectorDB(result_count=6), ExplodingReranker())

    response = _post_recommend(
        recommender,
        {
            "context": {"topic": "账单查询", "stage": "主阶段"},
            "intent": {"intent_type": "查询", "confidence": 0.9},
            "count": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["scripts"]) == 3
    assert payload["rerank_status"] == "failed"
    assert payload["rerank_error_code"] == "RERANK_INTERNAL_ERROR"


def test_recommend_scripts_end_to_end_sends_flat_text_to_chroma():
    """业务层 → VectorDBClient → Chroma 全链路都只传扁平 list[str]。"""
    collection = _FakeCollection([f"账单查询推荐话术 {i}" for i in range(6)])
    db = _vector_db_with(collection)
    recommender = ScriptRecommender(llm_client=None, vector_db=db)

    scripts = recommender.recommend_scripts(_context(), _intent(), count=3)

    sent = collection.queries[0]["query_texts"]
    assert isinstance(sent, list)
    assert len(sent) == 1
    assert all(isinstance(text, str) for text in sent)
    assert "账单查询" in sent[0]
    assert len(scripts) == 3
