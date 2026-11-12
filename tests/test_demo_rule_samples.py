"""演示规则样例覆盖：正例/反例/否定或转述/证据不足（T19，#20；M4-03 验收）。

数据来自仓库里的合成演示规则（``data/demo/rules/``）；这些测试是规则内容的
**验收口径**，不是另一套规则实现：

- 每条演示规则都必须具备正例、反例、否定/转述样例与证据不足样例；
- 每条证据都能按契约 §4 的码点区间在 ``sample_turns`` 原文里逐字回验；
- 正例证据不能处在否定/引用语境，否定/转述样例则必须真的包含这类语境；
- ``insufficient_evidence`` 与 ``not_applicable`` 分开，不互相冒充。
"""

from __future__ import annotations

import pytest

from bank_assistant.inspections.deterministic import assess_context
from bank_assistant.inspections.evidence import validate_evidence
from bank_assistant.rules.demo_seed import load_demo_rules
from bank_assistant.rules.schema import BUCKET_VERDICTS, EXAMPLES_BUCKETS

REQUIRED_SAMPLE_BUCKETS = (
    "positive",
    "negative",
    "negation",
    "insufficient_evidence",
)


def _turn_contents(entry) -> dict:
    return {turn.turn_id: turn.content for turn in entry.examples.sample_turns}


def test_every_demo_rule_has_positive_negative_negation_and_insufficient_samples() -> None:
    entries = load_demo_rules()
    assert len(entries) >= 6
    for entry in entries:
        for bucket in REQUIRED_SAMPLE_BUCKETS:
            examples = getattr(entry.examples, bucket)
            assert examples, f"{entry.rule_code} 缺少 {bucket} 样例"
    # 覆盖 M4-02 的六个默认类别，防止以后只给部分规则补样例。
    assert len({entry.category for entry in entries}) == 6


def test_all_demo_rule_samples_are_code_point_verifiable() -> None:
    for entry in load_demo_rules():
        covers = _turn_contents(entry)
        for bucket in EXAMPLES_BUCKETS:
            for index, example in enumerate(getattr(entry.examples, bucket), start=1):
                validate_evidence(example.evidence, turn_contents=covers)
                if example.evidence:
                    # 每个 quote 都是原文切片，不是近似文本。
                    for span in example.evidence:
                        assert covers[span.turn_id][span.start : span.end] == span.quote


def test_positive_samples_are_supported_but_negation_samples_are_not() -> None:
    for entry in load_demo_rules():
        covers = _turn_contents(entry)
        assert entry.examples.positive, entry.rule_code
        for example in entry.examples.positive:
            assert example.verdict == "hit"
            assert any(
                assess_context(
                    covers[span.turn_id], span.start, span.end
                ).supported
                for span in example.evidence
            ), f"{entry.rule_code} 正例证据落在否定/转述语境"
        assert entry.examples.negation, entry.rule_code
        for example in entry.examples.negation:
            assert example.verdict == "clear"
            judgements = [
                assess_context(covers[span.turn_id], span.start, span.end)
                for span in example.evidence
            ]
            assert judgements, f"{entry.rule_code} 否定样例没有证据"
            assert any(not item.supported for item in judgements), (
                f"{entry.rule_code} 的 negation 样例其实不是否定/转述"
            )


def test_insufficient_and_not_applicable_are_recorded_separately() -> None:
    assert BUCKET_VERDICTS["insufficient_evidence"] == "insufficient_evidence"
    assert BUCKET_VERDICTS["not_applicable"] == "not_applicable"
    assert BUCKET_VERDICTS["insufficient_evidence"] != BUCKET_VERDICTS["not_applicable"]

    for entry in load_demo_rules():
        for bucket in ("insufficient_evidence", "not_applicable"):
            examples = getattr(entry.examples, bucket)
            assert examples, f"{entry.rule_code} 缺少 {bucket}"
            for example in examples:
                assert example.verdict == BUCKET_VERDICTS[bucket]
                assert not example.evidence
                assert example.rationale.strip()


def test_negation_samples_are_not_reused_from_the_positive_bucket() -> None:
    for entry in load_demo_rules():
        positive_quotes = {
            (span.turn_id, span.start, span.end)
            for example in entry.examples.positive
            for span in example.evidence
        }
        negation_quotes = {
            (span.turn_id, span.start, span.end)
            for example in entry.examples.negation
            for span in example.evidence
        }
        assert negation_quotes.isdisjoint(positive_quotes), entry.rule_code
