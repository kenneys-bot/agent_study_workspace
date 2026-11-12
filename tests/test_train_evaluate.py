"""意图指标与冻结 test 评测纯函数（T28，#29）。

不训练、不加载模型；验证 accuracy/macro-F1/逐标签/混淆矩阵/无效输出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bank_assistant.training.dataset import load_intent_split
from bank_assistant.training.evaluate import (
    IntentPrediction,
    build_intent_prompt,
    compare_metrics,
    compute_intent_metrics,
    majority_baseline,
    parse_predicted_label,
)


def _row(expected: str, predicted: str | None, *, hard: bool = False) -> IntentPrediction:
    return IntentPrediction(
        item_id="x",
        text="t",
        expected=expected,
        predicted=predicted,
        hard=hard,
    )


def test_metrics_accuracy_macro_f1_and_confusion() -> None:
    rows = [
        _row("a", "a"),
        _row("b", "a"),
        _row("b", "b"),
        _row("a", "b"),
    ]
    metrics = compute_intent_metrics(rows, labels=["a", "b"])
    assert metrics.n == 4
    assert metrics.correct == 2
    assert metrics.accuracy == 0.5
    assert metrics.macro_f1 == 0.5
    assert metrics.per_label["a"]["tp"] == 1
    assert metrics.per_label["a"]["fp"] == 1
    assert metrics.per_label["a"]["fn"] == 1
    assert metrics.confusion["a"]["b"] == 1
    assert metrics.confusion["b"]["a"] == 1


def test_invalid_output_counts_as_error_not_dropped() -> None:
    rows = [_row("a", "a"), _row("b", None), _row("b", "nonsense")]
    metrics = compute_intent_metrics(rows, labels=["a", "b"])
    assert metrics.n == 3
    assert metrics.invalid == 1
    assert metrics.correct == 1
    assert metrics.confusion["b"]["__invalid__"] == 2
    assert metrics.per_label["b"]["recall"] == 0.0


def test_hard_subset_accuracy() -> None:
    rows = [
        _row("a", "a", hard=True),
        _row("b", "a", hard=True),
        _row("a", "a", hard=False),
    ]
    metrics = compute_intent_metrics(rows, labels=["a", "b"])
    assert metrics.hard_n == 2
    assert metrics.hard_correct == 1
    assert metrics.hard_accuracy == 0.5


def test_empty_predictions_are_rejected() -> None:
    with pytest.raises(ValueError, match="没有可评测"):
        compute_intent_metrics([], labels=["a"])


def test_parse_predicted_label_exact_and_substring() -> None:
    labels = ["lost_card", "credit_card_fee", "complaint"]
    assert parse_predicted_label("lost_card", labels) == "lost_card"
    assert parse_predicted_label("标签：credit_card_fee\n", labels) == "credit_card_fee"
    assert parse_predicted_label("我觉得是 complaint。", labels) == "complaint"
    assert parse_predicted_label("完全无关", labels) is None
    assert parse_predicted_label("", labels) is None


def test_prompt_contains_all_labels_and_is_deterministic() -> None:
    labels = ["complaint", "lost_card"]
    first = build_intent_prompt(labels, "我的卡丢了")
    second = build_intent_prompt(labels, "我的卡丢了")
    assert first == second
    assert "complaint" in first and "lost_card" in first
    assert "我的卡丢了" in first


def test_majority_baseline_reference_on_real_frozen_splits(
    frozen_dataset_dir: Path,
) -> None:
    def _load(split: str, purpose: str):
        return load_intent_split(
            dataset_dir=frozen_dataset_dir,
            dataset_id="bank-cs-m6-synthetic",
            dataset_version="v1",
            split=split,
            purpose=purpose,
            task="retrieval",
            text_field="query",
            label_field="business_type",
        )

    train = _load("train", "train")
    test = _load("test", "eval")
    predictions, metrics = majority_baseline(train, test)
    assert len(predictions) == 50
    assert {row.predicted for row in predictions} == {"complaint"}
    assert metrics.accuracy == pytest.approx(0.3)
    assert metrics.macro_f1 == pytest.approx(0.153846, abs=1e-6)


def test_compare_metrics_detects_improvement_and_no_improvement() -> None:
    base = compute_intent_metrics([_row("a", "a"), _row("b", "a")], labels=["a", "b"])
    better = compute_intent_metrics([_row("a", "a"), _row("b", "b")], labels=["a", "b"])
    worse = compute_intent_metrics([_row("a", "b"), _row("b", "b")], labels=["a", "b"])

    improved = compare_metrics(base, better)
    assert improved["delta"]["accuracy"] == 0.5
    assert improved["decision"] == "improved"

    declined = compare_metrics(base, worse)
    assert declined["delta"]["accuracy"] == 0.0
    assert declined["decision"] == "no_improvement"
