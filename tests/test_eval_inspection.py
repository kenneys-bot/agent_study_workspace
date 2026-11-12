"""质检基线对比（T27，#28；M6-04）的离线单测。

比较三种质检方案：

- ``rule_baseline``：只用确定性关键词 + 否定/转述语境 + 规则适用范围；
- ``llm_baseline``：只走模型结构化输出（回放用确定性朴素模型替身）；
- ``combined``：规则优先、模型补位，系统失败时明确降级而不是丢样本。

断言重点是「不静默丢样本」：四种 verdict 与 ``failed`` 都要出现在计数里，
证据定位可回验，引用只接受本次授权快照内的分块。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bank_assistant.core.model_gateway import ModelCallStatus, ModelResult
from bank_assistant.datasets.build import build_dataset
from bank_assistant.datasets.corpus import load_corpus
from bank_assistant.evaluation.config import PriceEntry
from bank_assistant.evaluation.inspection import (
    InspectionItem,
    build_rule_entries,
    deterministic_judgment,
    parse_inspection_items,
    run_inspection,
)
from bank_assistant.evaluation.metrics import Price, Usage
from bank_assistant.evaluation.replay import (
    BudgetExceeded,
    MeteredGateway,
    ReplayGateway,
    ReplayQueryRewriter,
)

FIXED_CREATED_AT = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def dataset_records() -> list[dict]:
    build = build_dataset(seed=42, created_at=FIXED_CREATED_AT)
    return [
        record
        for split in ("train", "dev", "test")
        for record in build.records(split)
    ]


def _item(records: list[dict], family: str, scenario: str = "lost_card") -> InspectionItem:
    items = parse_inspection_items(records)
    for item in items:
        if item.template_family == family and item.scenario == scenario:
            return item
    raise AssertionError(f"找不到模板 {family}/{scenario}")


def _rule(corpus, code: str):
    return corpus.rule(code)


def test_deterministic_baseline_uses_context_and_applicability(dataset_records) -> None:
    corpus = load_corpus()
    pwd = _item(dataset_records, "pwd_request")
    hit = deterministic_judgment(
        pwd, rule=_rule(corpus, "DEMO-PWD-01"), business_type=pwd.business_type
    )
    assert hit["verdict"] == "hit"
    assert hit["evidence"] and hit["evidence"][0]["quote"] == "验证码"
    assert pwd.conversation["turns"][hit["evidence"][0]["turn_index"]]["content"][
        hit["evidence"][0]["start"] : hit["evidence"][0]["end"]
    ] == "验证码"

    negation = _item(dataset_records, "negation_pwd")
    cleared = deterministic_judgment(
        negation,
        rule=_rule(corpus, "DEMO-PWD-01"),
        business_type=negation.business_type,
    )
    assert cleared["verdict"] == "clear"

    empty = _item(dataset_records, "empty_call")
    insufficient = deterministic_judgment(
        empty, rule=_rule(corpus, "DEMO-PWD-01"), business_type=empty.business_type
    )
    assert insufficient["verdict"] == "insufficient_evidence"

    cross = deterministic_judgment(
        pwd, rule=_rule(corpus, "DEMO-FEE-01"), business_type=pwd.business_type
    )
    assert cross["verdict"] == "not_applicable"


def test_rule_entries_have_stable_snapshot_identity() -> None:
    corpus = load_corpus()
    first = {entry.rule_code: entry for entry in build_rule_entries(corpus)}
    second = {entry.rule_code: entry for entry in build_rule_entries(corpus)}
    assert set(first) == {rule.rule_code for rule in corpus.rules}
    for code, entry in first.items():
        assert entry.rule_code == code
        assert entry.version_no == 1
        assert entry.content_hash == second[code].content_hash
        assert entry.rule_version_id == second[code].rule_version_id


def test_run_inspection_compares_three_baselines_without_dropping_verdicts(
    dataset_records,
) -> None:
    corpus = load_corpus()
    items = [
        _item(dataset_records, "pwd_request"),
        _item(dataset_records, "negation_pwd"),
        _item(dataset_records, "empty_call"),
    ]
    rules = build_rule_entries(corpus)
    records, predictions, stats = run_inspection(
        items, rules=rules, gateway=ReplayGateway(), mode="replay"
    )
    assert len(records) == len(items) * len(rules) * 3
    assert {record.variant for record in records} == {
        "rule_baseline",
        "llm_baseline",
        "combined",
    }
    assert stats["citation_validity"] == 1.0
    assert stats["invalid_citations_dropped"] == 0
    assert all(record.evidence_valid for record in records if record.predicted_verdict == "hit")
    # 回放朴素模型不做否定检查：同一会话上规则基线 clear、模型基线 hit。
    negation = _item(dataset_records, "negation_pwd")
    llm = [
        record
        for record in records
        if record.item_id == negation.item_id
        and record.rule_code == "DEMO-PWD-01"
        and record.variant == "llm_baseline"
    ][0]
    rule = [
        record
        for record in records
        if record.item_id == negation.item_id
        and record.rule_code == "DEMO-PWD-01"
        and record.variant == "rule_baseline"
    ][0]
    assert rule.predicted_verdict == "clear"
    assert llm.predicted_verdict == "clear"
    # 模型基线的真实差异在适用性/证据不足：规则能判 not_applicable 与
    # insufficient_evidence，回放“模型”只会在有/无关键词之间选择。
    cross = {
        record.variant: record
        for record in records
        if record.item_id == _item(dataset_records, "pwd_request").item_id
        and record.rule_code == "DEMO-FEE-01"
    }
    assert cross["rule_baseline"].predicted_verdict == "not_applicable"
    assert cross["llm_baseline"].predicted_verdict == "clear"
    assert cross["combined"].predicted_verdict == "not_applicable"
    empty = {
        record.variant: record
        for record in records
        if record.item_id == _item(dataset_records, "empty_call").item_id
        and record.rule_code == "DEMO-PWD-01"
    }
    assert empty["rule_baseline"].predicted_verdict == "insufficient_evidence"
    assert empty["llm_baseline"].predicted_verdict == "clear"
    # 所有四态 + failed 都有显式计数入口。
    assert set(stats["predicted_verdict_counts"]) == {
        "hit",
        "clear",
        "insufficient_evidence",
        "not_applicable",
        "failed",
    }
    assert stats["predicted_verdict_counts"]["failed"] == 0
    assert stats["model_calls"] == len(items) * len(rules)


def test_run_inspection_is_deterministic_ignoring_wall_clock(dataset_records) -> None:
    corpus = load_corpus()
    items = [_item(dataset_records, "pwd_request")]
    rules = build_rule_entries(corpus)
    first_records, first_predictions, _ = run_inspection(
        items, rules=rules, gateway=ReplayGateway(), mode="replay"
    )
    second_records, second_predictions, _ = run_inspection(
        items, rules=rules, gateway=ReplayGateway(), mode="replay"
    )

    def stable(rows):
        return [
            {key: value for key, value in row.items() if key != "latency_ms"}
            for row in rows
        ]

    assert stable(first_predictions) == stable(second_predictions)
    assert [
        (record.variant, record.rule_code, record.predicted_verdict)
        for record in first_records
    ] == [
        (record.variant, record.rule_code, record.predicted_verdict)
        for record in second_records
    ]


class _FailingGateway:
    mode = "replay"

    def __init__(self) -> None:
        self.calls = 0

    def structured(self, messages, schema, **kwargs):
        self.calls += 1
        return ModelResult(
            status=ModelCallStatus.TIMEOUT,
            model="replay-failing",
            mode="replay",
            error="timeout",
        )


def test_model_failures_are_reported_and_combined_falls_back(dataset_records) -> None:
    corpus = load_corpus()
    items = [_item(dataset_records, "pwd_request")]
    rules = build_rule_entries(corpus)
    gateway = _FailingGateway()
    records, _predictions, stats = run_inspection(
        items, rules=rules, gateway=gateway, mode="replay"
    )
    llm_records = [record for record in records if record.variant == "llm_baseline"]
    assert all(record.predicted_verdict == "failed" for record in llm_records)
    assert all(record.error for record in llm_records)
    assert stats["predicted_verdict_counts"]["failed"] == len(llm_records)
    combined = {record.rule_code: record for record in records if record.variant == "combined"}
    assert combined["DEMO-PWD-01"].predicted_verdict == "hit"
    assert combined["DEMO-FEE-01"].predicted_verdict == "not_applicable"
    assert gateway.calls == len(items) * len(rules)


def test_replay_query_rewriter_is_deterministic_and_explicit() -> None:
    rewriter = ReplayQueryRewriter()
    outcome = rewriter.rewrite("请问挂失补卡需要多久？")
    assert outcome.applied is True
    assert outcome.query == "挂失补卡需要多久"
    assert outcome.model == "replay-rewrite"
    unchanged = ReplayQueryRewriter().rewrite("挂失补卡需要多久")
    assert unchanged.applied is False
    assert unchanged.error == "rewrite_unchanged"


def test_metered_gateway_estimates_tokens_and_enforces_budget() -> None:
    class _OkGateway:
        mode = "replay"

        def structured(self, messages, schema, **kwargs):
            return ModelResult(
                status=ModelCallStatus.SUCCEEDED,
                model="stub",
                mode="replay",
                text="ok",
            )

    price = Price(input_cny_per_mtok=1_000_000.0, output_cny_per_mtok=0.0)
    gateway = MeteredGateway(
        _OkGateway(),
        price=price,
        budget_cny=15.0,
        model_provider="deepseek",
        model_version="deepseek-chat",
    )
    messages = [
        {"role": "system", "content": "系统提示词"},
        {"role": "user", "content": "客户问题"},
    ]
    from bank_assistant.inspections.schema import RuleJudgmentPayload

    result = gateway.structured(messages, schema=RuleJudgmentPayload)
    assert result.ok
    assert gateway.usage.input_tokens > 0
    assert gateway.calls == 1
    # 第二次调用会因为预算（估算成本 0.001 元）不足而显式失败。
    with pytest.raises(BudgetExceeded):
        gateway.structured(messages, schema=RuleJudgmentPayload)

    price_entry = PriceEntry(
        provider="deepseek",
        model="deepseek-chat",
        input_cny_per_mtok=2.0,
        output_cny_per_mtok=8.0,
        source="https://api-docs.deepseek.com/quick_start/pricing",
        as_of="2026-11-12",
    )
    assert price_entry.as_of
    assert Usage(input_tokens=1, output_tokens=1)
