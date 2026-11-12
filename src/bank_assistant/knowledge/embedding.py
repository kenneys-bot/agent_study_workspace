"""embedding 提供方抽象：真实 fastembed 与离线确定性假实现（M3，ADR-0005）。

ADR-0004 决定 embedding 自托管 BGE；ADR-0005 进一步确定运行时选型：
``fastembed``（ONNX Runtime，CPU，无 torch）+ ``BAAI/bge-small-zh-v1.5``
（512 维）。选它的理由是两机可跑、无需 CUDA/torch、依赖可锁定；``bge-m3``
留给 M6 做对照，不改变本模块的接口。

生产代码只依赖 ``EmbeddingProvider`` 协议；测试与离线重建使用
``FakeEmbeddingProvider``（确定性哈希向量，不联网、不下载模型）。任何实现
都必须给出稳定的 ``model_version``，与索引清单里的
``embedding_model_version`` 一起自证索引身份。
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

# 固定校准串：只用于生成“校准向量”，自证 embedding 模型未漂移（ADR-0004 §3）。
# 它不承载业务语义，也不包含任何真实数据。
CALIBRATION_TEXT = (
    "bank-assistant calibration text: 挂失 / 信用卡费用 / 投诉受理（synthetic demo only）"
)

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_DIMENSION = 512
FAKE_MODEL_VERSION = "fake-sha256@512"


class EmbeddingError(Exception):
    """embedding 能力失败的基类；调用方必须报错，不能退回随机向量。"""


class EmbeddingUnavailable(EmbeddingError):
    """依赖缺失、模型无法加载或下载失败（网络/缓存不可用）。"""


class EmbeddingDimensionMismatch(EmbeddingError):
    """向量维度与声明的 ``dimension`` 不一致，索引不可信。"""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """embedding 提供方的最小接口。"""

    @property
    def model_version(self) -> str:
        """稳定的模型标识，写入索引清单与 chunk 行。"""
        ...

    @property
    def dimension(self) -> int:
        """向量维度；不同维度的索引不能混用。"""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把一批文本转成同序向量；失败必须抛 ``EmbeddingError``。"""
        ...


def _deterministic_vector(text: str, model_version: str, dimension: int) -> list[float]:
    """由 (model_version, text) 派生的确定性向量，取值在 [-1, 1]。

    不使用随机数：同一输入在任何进程、任何机器上都得到同一向量，离线测试
    才能断言“重建后检索结果一致”。把 ``model_version`` 混入种子，使伪造的
    “模型变更”也能改变校准哈希。
    """
    seed = f"{model_version}\x00{text}".encode("utf-8")
    values: list[float] = []
    counter = 0
    block = hashlib.sha256(seed).digest()
    while len(values) < dimension:
        digest = hashlib.sha256(block + counter.to_bytes(4, "big")).digest()
        values.extend(byte / 127.5 - 1.0 for byte in digest)
        counter += 1
    return values[:dimension]


class FakeEmbeddingProvider:
    """离线确定性实现：供单元测试与无模型环境的重建演练使用。

    ``model_version`` 默认带 ``fake-`` 前缀，避免与真实索引身份混淆。
    """

    def __init__(
        self,
        *,
        model_version: str = FAKE_MODEL_VERSION,
        dimension: int = EMBEDDING_DIMENSION,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension 必须为正整数")
        self._model_version = model_version
        self._dimension = dimension

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            _deterministic_vector(text, self._model_version, self._dimension)
            for text in texts
        ]


class FastEmbedProvider:
    """真实 embedding：``fastembed`` + ``BAAI/bge-small-zh-v1.5``（512 维）。

    ONNX Runtime CPU 推理，不依赖 torch；模型在首次使用时由 fastembed 下载到
    其缓存目录。依赖缺失、模型不可用或下载失败统一抛 ``EmbeddingUnavailable``，
    调用方必须如实失败，不能静默换成随机向量。
    """

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        dimension: int = EMBEDDING_DIMENSION,
        cache_dir: str | None = None,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension 必须为正整数")
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - 依赖总是随 uv.lock 安装
            raise EmbeddingUnavailable(
                "fastembed 未安装；请先 uv sync（ADR-0005）"
            ) from exc
        try:
            self._text_embedding = TextEmbedding(
                model_name=model_name, cache_dir=cache_dir
            )
        except Exception as exc:  # noqa: BLE001 - 下载/加载失败的形态由上游决定
            raise EmbeddingUnavailable(
                f"fastembed 模型 {model_name} 不可用（下载或加载失败）：{exc}"
            ) from exc
        self._model_version = f"{model_name}@{dimension}"
        self._dimension = dimension

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            vectors = [
                [float(value) for value in vector]
                for vector in self._text_embedding.embed(list(texts))
            ]
        except Exception as exc:  # noqa: BLE001 - 推理失败同样必须显式失败
            raise EmbeddingUnavailable(f"fastembed 推理失败：{exc}") from exc
        for vector in vectors:
            if len(vector) != self._dimension:
                raise EmbeddingDimensionMismatch(
                    f"模型返回 {len(vector)} 维，声明为 {self._dimension} 维"
                )
        return vectors


def ensure_dimension(provider: EmbeddingProvider, vectors: Sequence[Sequence[float]]) -> None:
    """校验向量维度；维度不一致时索引不可信，必须失败。"""
    for vector in vectors:
        if len(vector) != provider.dimension:
            raise EmbeddingDimensionMismatch(
                f"向量维度 {len(vector)} 与声明的 {provider.dimension} 不一致"
            )


def calibration_vector_sha256(provider: EmbeddingProvider) -> str:
    """固定校准串向量的 SHA-256（hex），写入索引清单并用于加载前校验。

    浮点按 IEEE-754 双精度大端序打包后再哈希，跨平台稳定；不把
    ``model_version`` 混进哈希——它由清单单独记录，哈希只证明“向量本身没变”。
    """
    vectors = provider.embed([CALIBRATION_TEXT])
    if len(vectors) != 1:
        raise EmbeddingDimensionMismatch("校准串必须恰好返回一个向量")
    vector = vectors[0]
    ensure_dimension(provider, [vector])
    payload = b"".join(struct.pack(">d", float(value)) for value in vector)
    return hashlib.sha256(payload).hexdigest()


def create_embedding_provider(
    kind: str = "fastembed", *, model_name: str | None = None
) -> EmbeddingProvider:
    """按名字构造 provider；脚本用 ``EMBEDDING_PROVIDER=fake`` 做离线重建。"""
    normalized = kind.strip().lower()
    if normalized == "fake":
        return FakeEmbeddingProvider()
    if normalized == "fastembed":
        return FastEmbedProvider(model_name=model_name or DEFAULT_EMBEDDING_MODEL)
    raise ValueError(f"未知的 embedding provider：{kind!r}（可选 fastembed / fake）")


__all__ = [
    "CALIBRATION_TEXT",
    "DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "FAKE_MODEL_VERSION",
    "EmbeddingDimensionMismatch",
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "FakeEmbeddingProvider",
    "FastEmbedProvider",
    "calibration_vector_sha256",
    "create_embedding_provider",
    "ensure_dimension",
]
