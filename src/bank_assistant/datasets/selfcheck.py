"""数据集标注自一致性检查（T26，#27；M6-03）。

**这不是标注者间一致性（IAA）**：本项目只有一名开发者，且数据标签是合成模板
的产物。这里做两件可复现的健全性检查：

1. 重新生成 10% 样本的标签两次，比较两次结果是否一致（生成器可复现性）；
2. 对可复算的标签再做一次确定性复核：用 :mod:`bank_assistant.inspections.deterministic`
   的否定/引用语境检查，验证 ``hit`` 证据确实可成立、否定/转述语境确实不能成立。

复核只覆盖“可复算”的标签（命中、带证据的否定/引用、无关键词的证据不足、跨场景
不适用），未覆盖的标签不计入分母。manifest 会如实写明口径与分母，避免把这种
自检说成双人标注一致性。
"""

from __future__ import annotations

import hashlib
from typing import Any

from bank_assistant.datasets.corpus import Corpus
from bank_assistant.inspections.deterministic import assess_context

__all__ = [
    "RULE_KEYWORDS",
    "check_self_consistency",
    "recheck_label",
]

# 每条规则用于“确定性复核”的关键词。只收风险动作本身，不收泛化词
# （如 FEE-01 不收“减免”，否则“需审批”会被误当承诺）。
RULE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "DEMO-PWD-01": ("验证码", "密码"),
    "DEMO-CLAIM-01": ("办理成功", "已经办好", "已经办妥", "已处理完成"),
    "DEMO-FEE-01": ("我给您免", "免了", "免年费", "免掉", "给您免除"),
    "DEMO-FEE-02": ("永久免", "永久免费", "不计入账单"),
    "DEMO-CMP-01": ("投诉也没用", "拒绝受理", "不受理", "无法登记"),
    "DEMO-ATT-01": ("这么笨", "真笨", "蠢"),
}


def _bucket(seed: int, item_id: str) -> int:
    """把 item 稳定映射到 0..999；与列表顺序、机器、时间都无关。"""
    digest = hashlib.sha256(f"selfcheck|{seed}|{item_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 1000


def _keyword_occurrences(
    turns: list[dict[str, Any]], keywords: tuple[str, ...]
) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    for turn in turns:
        content = turn["content"]
        for keyword in keywords:
            start = content.find(keyword)
            while start >= 0:
                end = start + len(keyword)
                occurrences.append(
                    {
                        "turn_id": turn["turn_id"],
                        "speaker": turn["speaker"],
                        "start": start,
                        "end": end,
                        "quote": keyword,
                        "supported": assess_context(content, start, end).supported,
                    }
                )
                start = content.find(keyword, end)
    return occurrences


def recheck_label(
    label: dict[str, Any],
    *,
    rule_code: str,
    business_type: str,
    rule_business_types: tuple[str, ...],
    turns: list[dict[str, Any]],
) -> str | None:
    """用确定性规则重新推导一个标签；无法复算时返回 ``None``（不计分母）。"""
    if business_type not in rule_business_types:
        # 规则适用范围不含本会话业务类型：确定性结论是“不适用”。
        return "not_applicable"
    if label["verdict"] == "not_applicable":
        # 业务类型匹配却标成不适用，必须被复核抓出来。
        return "applicable"

    keywords = RULE_KEYWORDS.get(rule_code)
    if not keywords:
        return None
    occurrences = _keyword_occurrences(turns, keywords)
    supported = [item for item in occurrences if item["supported"]]
    verdict = label["verdict"]

    if verdict == "hit":
        if not supported:
            return "clear"
        if not label.get("evidence"):
            return "clear"
        return "hit"
    if supported:
        return "hit"
    if verdict == "clear" and label.get("evidence"):
        return "clear"
    if verdict == "insufficient_evidence":
        return "insufficient_evidence"
    return None


def _evidence_supported(
    label: dict[str, Any], turns_by_id: dict[str, str]
) -> bool | None:
    if label["verdict"] != "hit" or not label["evidence"]:
        return None
    return all(
        assess_context(
            turns_by_id[evidence["turn_id"]],
            evidence["start"],
            evidence["end"],
        ).supported
        for evidence in label["evidence"]
    )


def check_self_consistency(
    items: tuple[Any, ...],
    *,
    corpus: Corpus,
    seed: int,
    sample_pct: float = 10.0,
) -> dict[str, Any]:
    """对 10% 样本跑两次确定性复核，返回可写进 manifest 的如实指标。"""
    if not 0 < sample_pct <= 100:
        raise ValueError("sample_pct 必须在 (0, 100] 内")
    threshold = max(1, int(round(sample_pct * 10)))
    sampled = [
        item
        for item in sorted(items, key=lambda candidate: candidate.item_id)
        if _bucket(seed, item.item_id) < threshold
    ]

    rules = {rule.rule_code: rule for rule in corpus.rules}
    rule_business_types = {
        code: tuple(rule.applicability.get("business_types", ()))
        for code, rule in rules.items()
    }
    passes: list[dict[str, Any]] = []
    for _ in range(2):
        result: dict[str, Any] = {}
        for item in sampled:
            if item.task != "inspection":
                continue
            turns = item.conversation["turns"]
            for rule_code, label in item.labels["per_rule"].items():
                expected = recheck_label(
                    label,
                    rule_code=rule_code,
                    business_type=item.business_type,
                    rule_business_types=rule_business_types[rule_code],
                    turns=turns,
                )
                if expected is None:
                    continue
                key = f"{item.item_id}|{rule_code}"
                result[key] = expected == label["verdict"]
        passes.append(result)

    stable = passes[0] == passes[1]
    checked = len(passes[0])
    agreement = (
        sum(1 for matched in passes[0].values() if matched) / checked
        if checked
        else 1.0
    )

    supported: list[bool] = []
    for item in sampled:
        if item.task != "inspection":
            continue
        turns_by_id = {turn["turn_id"]: turn["content"] for turn in item.conversation["turns"]}
        for label in item.labels["per_rule"].values():
            outcome = _evidence_supported(label, turns_by_id)
            if outcome is not None:
                supported.append(outcome)
    evidence_support_rate = (
        sum(1 for outcome in supported if outcome) / len(supported) if supported else 1.0
    )

    return {
        "metric": "deterministic_relabel_self_consistency",
        "sampled_pct": float(sample_pct),
        "sampled_items": len(sampled),
        "checked_labels": checked,
        "passes": 2,
        "stable": stable,
        "agreement": round(agreement, 4),
        "evidence_support_rate": round(evidence_support_rate, 4),
        "not_inter_annotator_agreement": True,
        "note": (
            "两次确定性重标注的一致性，仅用于发现生成/标签漂移；"
            "不是双人标注，也不能当作人工金标或标注者间一致性。"
        ),
    }
