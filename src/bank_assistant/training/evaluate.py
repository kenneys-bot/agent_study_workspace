"""意图分类指标与冻结 test 评测（T28，#29）。

评测口径与 T27（#28）保持一致的部分：

- 只接受冻结 ``test`` split（``require_frozen_test_split``）；
- 数据集先经 T26 的 ``verify_dataset`` 复算哈希；
- 产物写独立 run 目录，manifest 记录 code sha、数据哈希、seed、硬件与状态；
- 不调参、不挑 checkpoint：test 只在训练结束后评一次。

意图分类指标用纯函数实现，便于离线单测：accuracy、macro-F1（只在有支持的
标签上平均）、逐标签 Precision/Recall/F1/support、混淆矩阵、无效输出计数。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from bank_assistant.training.dataset import IntentDataset, IntentExample

__all__ = [
    "IntentMetrics",
    "IntentPrediction",
    "build_intent_prompt",
    "compare_metrics",
    "compute_intent_metrics",
    "evaluate_examples",
    "majority_baseline",
    "parse_predicted_label",
]

LABEL_DESCRIPTIONS: Mapping[str, str] = {
    "lost_card": "银行卡挂失、补卡、盗刷/丢失后的处理",
    "credit_card_fee": "信用卡费用、利息、年费、分期、还款相关",
    "complaint": "投诉、升级、调解、处理时限与投诉渠道",
}


@dataclass(frozen=True, slots=True)
class IntentPrediction:
    item_id: str
    text: str
    expected: str
    predicted: str | None
    raw_output: str = ""
    hard: bool = False

    @property
    def correct(self) -> bool:
        return self.predicted == self.expected


@dataclass(frozen=True, slots=True)
class IntentMetrics:
    label_names: tuple[str, ...]
    n: int
    correct: int
    invalid: int
    accuracy: float
    macro_f1: float
    macro_f1_label_count: int
    per_label: Mapping[str, Mapping[str, float | int]]
    confusion: Mapping[str, Mapping[str, int]]
    hard_n: int
    hard_correct: int
    hard_accuracy: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_names": list(self.label_names),
            "n": self.n,
            "correct": self.correct,
            "invalid": self.invalid,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "macro_f1_label_count": self.macro_f1_label_count,
            "per_label": {key: dict(value) for key, value in self.per_label.items()},
            "confusion": {
                key: dict(value) for key, value in self.confusion.items()
            },
            "hard_n": self.hard_n,
            "hard_correct": self.hard_correct,
            "hard_accuracy": self.hard_accuracy,
        }


def build_intent_prompt(labels: Sequence[str], text: str) -> str:
    """固定 prompt；baseline 与 tuned 必须逐字相同。"""
    allowed = "、".join(labels)
    descriptions = "；".join(
        f"{label}={LABEL_DESCRIPTIONS.get(label, label)}" for label in labels
    )
    return (
        "你是银行客服意图路由分类器。\n"
        f"只允许输出以下标签之一：{allowed}。\n"
        f"标签含义：{descriptions}。\n"
        "只输出标签本身，不要解释，不要加标点。\n\n"
        f"客户问题：{text}\n"
        "标签："
    )


def parse_predicted_label(
    raw_output: str, labels: Sequence[str]
) -> str | None:
    """从生成文本里取标签；优先整行精确匹配，其次最长标签子串匹配。"""
    text = (raw_output or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip().strip("。.,，:：\"' ")
    if first_line in labels:
        return first_line
    matches = [label for label in labels if label in text]
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    return matches[0]


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def compute_intent_metrics(
    predictions: Iterable[IntentPrediction],
    *,
    labels: Sequence[str] | None = None,
) -> IntentMetrics:
    rows = list(predictions)
    if not rows:
        raise ValueError("没有可评测的预测结果")
    label_names = tuple(labels) if labels is not None else tuple(
        sorted({row.expected for row in rows})
    )
    label_set = set(label_names)
    if any(row.expected not in label_set for row in rows):
        raise ValueError("预测中出现标签集合之外的期望标签")

    confusion: dict[str, dict[str, int]] = {
        expected: {predicted: 0 for predicted in (*label_names, "__invalid__")}
        for expected in label_names
    }
    correct = 0
    invalid = 0
    for row in rows:
        key = row.predicted if row.predicted in label_set else "__invalid__"
        confusion[row.expected][key] += 1
        if row.predicted is None:
            invalid += 1
        if row.correct:
            correct += 1

    per_label: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for label in label_names:
        tp = confusion[label][label]
        fp = sum(
            confusion[other][label]
            for other in label_names
            if other != label
        )
        fn = sum(
            count
            for predicted, count in confusion[label].items()
            if predicted != label
        )
        support = tp + fn
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        f1 = _safe_div(2 * precision * recall, precision + recall)
        if support or (tp + fp):
            f1_values.append(f1)
        per_label[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support": support,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }

    hard_rows = [row for row in rows if row.hard]
    hard_correct = sum(1 for row in hard_rows if row.correct)

    return IntentMetrics(
        label_names=label_names,
        n=len(rows),
        correct=correct,
        invalid=invalid,
        accuracy=round(_safe_div(correct, len(rows)), 6),
        macro_f1=round(_safe_div(sum(f1_values), len(f1_values)), 6),
        macro_f1_label_count=len(f1_values),
        per_label=per_label,
        confusion=confusion,
        hard_n=len(hard_rows),
        hard_correct=hard_correct,
        hard_accuracy=(
            round(_safe_div(hard_correct, len(hard_rows)), 6) if hard_rows else None
        ),
    )


def majority_baseline(
    train: IntentDataset, test: IntentDataset
) -> tuple[tuple[IntentPrediction, ...], IntentMetrics]:
    """多数类基线：训练集里最多的标签，原样用于 test。

    这是**参考下限**，不是"基线模型"；真正的基线是同一个基础模型的 zero-shot。
    即使训练因资源不足未运行，这个下限也能诚实给出。
    """
    if not train.label_counts:
        raise ValueError("训练集缺少标签计数")
    majority = max(
        sorted(train.label_counts),
        key=lambda label: (train.label_counts[label], label),
    )
    predictions = tuple(
        IntentPrediction(
            item_id=example.item_id,
            text=example.text,
            expected=example.label,
            predicted=majority,
            raw_output="<majority-baseline>",
            hard=example.is_hard,
        )
        for example in test.examples
    )
    return predictions, compute_intent_metrics(
        predictions, labels=test.label_names
    )


def evaluate_examples(
    predictor: Callable[[IntentExample], str],
    examples: Sequence[IntentExample],
    *,
    labels: Sequence[str],
) -> tuple[tuple[IntentPrediction, ...], IntentMetrics]:
    predictions: list[IntentPrediction] = []
    for example in examples:
        raw = predictor(example)
        predictions.append(
            IntentPrediction(
                item_id=example.item_id,
                text=example.text,
                expected=example.label,
                predicted=parse_predicted_label(raw, labels),
                raw_output=raw,
                hard=example.is_hard,
            )
        )
    return tuple(predictions), compute_intent_metrics(predictions, labels=labels)


def compare_metrics(
    baseline: IntentMetrics, tuned: IntentMetrics
) -> dict[str, Any]:
    """基线 vs 微调；没有改善就如实写 no_improvement。"""
    accuracy_delta = round(tuned.accuracy - baseline.accuracy, 6)
    macro_f1_delta = round(tuned.macro_f1 - baseline.macro_f1, 6)
    improved = accuracy_delta > 0 or macro_f1_delta > 0
    return {
        "baseline": baseline.to_dict(),
        "tuned": tuned.to_dict(),
        "delta": {
            "accuracy": accuracy_delta,
            "macro_f1": macro_f1_delta,
        },
        "decision": "improved" if improved else "no_improvement",
        "note": (
            "仅在冻结 test split 上比较；test 未参与训练或 checkpoint 选择。"
            "样本量小（n<100），区间很宽，不宣称统计显著。"
        ),
    }


def dump_predictions(predictions: Iterable[IntentPrediction]) -> str:
    return "".join(
        json.dumps(
            {
                "item_id": row.item_id,
                "text": row.text,
                "expected": row.expected,
                "predicted": row.predicted,
                "raw_output": row.raw_output,
                "correct": row.correct,
                "hard": row.hard,
            },
            ensure_ascii=False,
        )
        + "\n"
        for row in predictions
    )
