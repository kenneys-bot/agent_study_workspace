"""检索文本与确定性重排的共享原语（T36 评测 → T37 生产落地）。

T36（#36）在评测层验证了两项改进：

1. ``embedding_text=title_chunk``：把知识标题拼入 **embedding 输入**，分块
   正文与码点区间不变（引用仍可逐字回验）；
2. 候选池内的确定性词面重排（``rerank_alpha=0.2``）：用查询字符二元组覆盖率
   修正排序，不调用模型、不联网。

T37（#37）要把同一套东西落到生产。为避免「评测里一个公式、线上另一个公式」，
两个调用方（评测 ``RetrievalHarness`` 与生产 ``build_index`` /
``SuggestionService``）都从这里取实现。本模块不依赖数据库、模型与网络，可
离线单测。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_EMBEDDING_TEXT",
    "DEFAULT_RERANK_ALPHA",
    "DEFAULT_RERANK_POOL",
    "EMBEDDING_TEXT_CHUNK",
    "EMBEDDING_TEXT_MODES",
    "EMBEDDING_TEXT_TITLE_CHUNK",
    "EMBEDDING_TEXT_TITLE_SOURCE_CHUNK",
    "RerankCandidate",
    "build_embedding_input",
    "char_grams",
    "lexical_coverage",
    "rerank_candidates",
]

# 三种索引文本形态；改变它就改变向量，必须新建 index_version（ADR-0005）。
EMBEDDING_TEXT_CHUNK = "chunk"
EMBEDDING_TEXT_TITLE_CHUNK = "title_chunk"
EMBEDDING_TEXT_TITLE_SOURCE_CHUNK = "title_source_chunk"
EMBEDDING_TEXT_MODES: frozenset[str] = frozenset(
    {EMBEDDING_TEXT_CHUNK, EMBEDDING_TEXT_TITLE_CHUNK, EMBEDDING_TEXT_TITLE_SOURCE_CHUNK}
)

# 生产默认与 T36 最终采用的评测配置一致（configs/eval/replay.yaml:retrieval）：
# 标题入索引、候选池内 α=0.2 确定性重排。两个开关都可显式关掉回退。
DEFAULT_EMBEDDING_TEXT = EMBEDDING_TEXT_TITLE_CHUNK
DEFAULT_RERANK_ALPHA = 0.2
DEFAULT_RERANK_POOL = 20

_CJK_OR_LATIN = re.compile(r"[0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def build_embedding_input(
    text: str,
    *,
    title: str = "",
    source_type: str = "",
    mode: str = DEFAULT_EMBEDDING_TEXT,
) -> str:
    """构造 embedding 输入；``text`` 必须是原文区间，**不修改**它。

    ``chunk`` 只用正文；``title_chunk`` 前置标题；``title_source_chunk`` 再
    前置来源类型。未知模式显式失败，不能静默退回正文（那会让索引身份撒谎）。
    """
    if mode not in EMBEDDING_TEXT_MODES:
        raise ValueError(f"embedding_text 只能是 {sorted(EMBEDDING_TEXT_MODES)}")
    if mode == EMBEDDING_TEXT_CHUNK:
        return text
    if mode == EMBEDDING_TEXT_TITLE_CHUNK:
        return f"{title}\n{text}"
    return f"{title}\n来源类型：{source_type}\n{text}"


def char_grams(text: str) -> frozenset[str]:
    """字符二元组集合；用于确定性的本地词面重排，不引入外部模型/网络。"""
    cleaned = "".join(_CJK_OR_LATIN.findall(text.lower()))
    if len(cleaned) <= 1:
        return frozenset({cleaned}) if cleaned else frozenset()
    return frozenset(cleaned[index : index + 2] for index in range(len(cleaned) - 1))


def lexical_coverage(query: str, *, title: str, text: str) -> float:
    """查询字符二元组被「标题 + \n + 正文」覆盖的比例（0~1）。"""
    query_grams = char_grams(query)
    if not query_grams:
        return 0.0
    chunk_grams = char_grams(f"{title}\n{text}")
    return len(query_grams & chunk_grams) / len(query_grams)


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """一条待重排候选；``key`` 是稳定去重键（生产为 chunk_id，评测为 chunk_key）。"""

    key: str
    title: str
    text: str
    score: float


def rerank_candidates(
    query: str,
    candidates: Sequence[RerankCandidate],
    *,
    alpha: float,
    pool: int,
) -> list[RerankCandidate]:
    """在候选池内按 ``score + alpha * 词面覆盖率`` 重排，返回全部候选。

    - 只重排前 ``pool`` 条，池外候选保持原有相对顺序接在后面：重排不能把
      本来不合格的候选“补”进来，也不改变候选集合；
    - 同分用 ``key`` 升序做稳定 tie-break，两次运行结果一致；
    - ``alpha <= 0`` 或候选不足 2 条时原序返回（可单独回退）。
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha 必须在 [0, 1] 内")
    if pool <= 0:
        raise ValueError("pool 必须为正整数")
    ordered = list(candidates)
    if alpha <= 0.0 or len(ordered) <= 1:
        return ordered
    window = ordered[:pool]
    rescored = [
        (candidate.score + alpha * lexical_coverage(
            query, title=candidate.title, text=candidate.text
        ), candidate)
        for candidate in window
    ]
    rescored.sort(key=lambda pair: (-pair[0], pair[1].key))
    return [candidate for _, candidate in rescored] + ordered[pool:]
