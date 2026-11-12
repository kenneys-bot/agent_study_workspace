"""回放用确定性词法 embedding（T27，#28）。

**这不是生产 embedding 模型。** 生产索引使用自托管 BGE（ADR-0004/0005）；
``mode: replay`` 只需要一个不联网、无模型下载、逐字节可复现的向量来源，用来
验证评测流水线本身。这里用「字符二元组 + 有符号哈希」构造 512 维向量：

- 同一文本在任何机器、任何进程都得到同一向量；
- 与纯随机假向量不同，共享词汇的查询/知识会有更高余弦相似度，回放报告
  因此还能暴露过滤/排序逻辑的错误（但仍不能当作模型质量结论）。

manifest 的 ``embedding_version`` 会明确写成 ``replay-lexical-*``，与真实
BGE 版本区分。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

__all__ = ["DIMENSION", "MODEL_VERSION", "ReplayLexicalEmbedding"]

DIMENSION = 512
MODEL_VERSION = "replay-lexical-char2gram-v1@512"

_MEANINGFUL = re.compile(r"[0-9a-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def _normalize(text: str) -> str:
    """只保留数字、拉丁字母与 CJK，去掉空白与标点，降低噪声。"""
    return "".join(_MEANINGFUL.findall(text.lower()))


class ReplayLexicalEmbedding:
    """确定性字符二元组哈希向量；实现 ``EmbeddingProvider`` 协议。"""

    def __init__(self, *, dimension: int = DIMENSION) -> None:
        if dimension <= 0:
            raise ValueError("dimension 必须为正整数")
        self._dimension = dimension

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        cleaned = _normalize(text)
        values = [0.0] * self._dimension
        if not cleaned:
            return values
        grams = (
            [cleaned]
            if len(cleaned) == 1
            else [cleaned[index : index + 2] for index in range(len(cleaned) - 1)]
        )
        for gram in grams:
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self._dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            return values
        return [value / norm for value in values]
