"""embedding 抽象与校准向量（M3，ADR-0005）。

离线可复现的要求：``FakeEmbeddingProvider`` 必须是确定性的（同文本同向量），
并且不同 ``model_version`` 给出不同向量——这样校准哈希才能在测试里证明
“模型漂移会被发现”，而不是无条件通过。
"""

from __future__ import annotations

import pytest

from bank_assistant.knowledge.embedding import (
    CALIBRATION_TEXT,
    EMBEDDING_DIMENSION,
    EmbeddingDimensionMismatch,
    FakeEmbeddingProvider,
    calibration_vector_sha256,
    create_embedding_provider,
)


def test_fake_provider_is_deterministic_and_has_configured_dimension() -> None:
    provider = FakeEmbeddingProvider()

    first = provider.embed(["挂失信用卡"])
    second = provider.embed(["挂失信用卡"])

    assert first == second
    assert len(first) == 1
    assert len(first[0]) == EMBEDDING_DIMENSION
    assert all(isinstance(value, float) for value in first[0])
    assert provider.dimension == EMBEDDING_DIMENSION
    assert provider.model_version


def test_fake_provider_vectors_differ_by_text_and_model_version() -> None:
    provider = FakeEmbeddingProvider(model_version="fake-model-a@512")
    other_model = FakeEmbeddingProvider(model_version="fake-model-b@512")

    vector_a, vector_b = provider.embed(["挂失", "信用卡费用"])
    vector_a_other_model = other_model.embed(["挂失"])[0]

    assert vector_a != vector_b
    assert vector_a != vector_a_other_model


def test_calibration_hash_is_stable_for_same_model_and_changes_on_drift() -> None:
    provider = FakeEmbeddingProvider(model_version="fake-model-a@512")
    same_model = FakeEmbeddingProvider(model_version="fake-model-a@512")
    drifted = FakeEmbeddingProvider(model_version="fake-model-a+drift@512")

    baseline = calibration_vector_sha256(provider)

    assert len(baseline) == 64
    assert int(baseline, 16) >= 0  # 合法 hex
    assert calibration_vector_sha256(same_model) == baseline
    assert calibration_vector_sha256(drifted) != baseline


def test_calibration_text_is_a_fixed_non_business_string() -> None:
    # 校准串只用于自证模型未漂移；不包含任何真实业务数据。
    assert CALIBRATION_TEXT.strip()
    assert "真实" not in CALIBRATION_TEXT


def test_calibration_rejects_provider_with_wrong_dimension() -> None:
    class BrokenProvider:
        model_version = "broken@3"
        dimension = 3

        def embed(self, texts):
            return [[0.1, 0.2] for _ in texts]

    with pytest.raises(EmbeddingDimensionMismatch):
        calibration_vector_sha256(BrokenProvider())


def test_factory_supports_fake_and_rejects_unknown_kind() -> None:
    assert isinstance(create_embedding_provider("fake"), FakeEmbeddingProvider)
    assert isinstance(
        create_embedding_provider("faKe"), FakeEmbeddingProvider
    )
    with pytest.raises(ValueError, match="未知的 embedding provider"):
        create_embedding_provider("torch-hub")
