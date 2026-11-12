"""演示会话种子与回放处理器的单元测试（T25）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bank_assistant.assistants.candidate_answer import CandidateAnswerPayload
from bank_assistant.assistants.suggestions import CoreIntentPayload
from bank_assistant.demo.conversations import (
    DemoConversationError,
    load_demo_conversations,
)
from bank_assistant.demo.replay import DemoReplayResponder
from bank_assistant.inspections.deterministic import assess_context
from bank_assistant.inspections.schema import RuleJudgmentPayload

def _conversation_turns(content: str):
    from bank_assistant.assistants.quality_inspector.conversation_parser import (
        ConversationParser,
    )

    parsed = ConversationParser().parse_conversation(content, "text")
    return [
        {
            "turn_id": f"00000000-0000-0000-0000-0000000000{index:02d}",
            "ordinal": index,
            "speaker": turn.speaker,
            "content": turn.content,
        }
        for index, turn in enumerate(parsed.turns, start=1)
    ]


def _inspection_messages(rule_code: str, content: str) -> list[dict[str, str]]:
    payload = {
        "rule": {"rule_code": rule_code, "version_no": 1, "business_type": "complaint"},
        "allowed_verdicts": ["hit", "clear", "insufficient_evidence", "not_applicable"],
        "conversation": _conversation_turns(content),
    }
    return [
        {"role": "system", "content": "请按规则判断"},
        {"role": "user", "content": "会话数据：\n" + json.dumps(payload, ensure_ascii=False)},
    ]


def test_demo_conversations_cover_three_stories_normal_and_risk():
    entries = load_demo_conversations()
    assert len(entries) == 6
    pairs = {(entry.story, entry.scenario) for entry in entries}
    assert pairs == {
        ("S1", "normal"),
        ("S1", "risk"),
        ("S2", "normal"),
        ("S2", "risk"),
        ("S3", "normal"),
        ("S3", "risk"),
    }
    assert all(entry.source_ref.startswith("demo://conversations/") for entry in entries)
    assert all(entry.business_type in {"lost_card", "credit_card_fee", "complaint"} for entry in entries)
    # 全部为合成数据：正文刻意不含任何数字，避免真实号码形态。
    assert all(not any(character.isdigit() for character in entry.content) for entry in entries)
    # 开发检查：所有风险会话都要求生成待复核质检任务。
    assert {entry.entry_id for entry in entries if entry.create_inspection} == {
        "s1-lost-card-risk",
        "s2-fee-risk",
        "s3-complaint-risk",
    }


def test_load_demo_conversations_rejects_missing_scenario(tmp_path: Path):
    source = json.loads(
        (Path("data/demo/conversations/s1-lost-card-normal.json")).read_text(
            encoding="utf-8"
        )
    )
    (tmp_path / "one.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(DemoConversationError, match="缺少演示会话"):
        load_demo_conversations(tmp_path)


def test_load_demo_conversations_rejects_digits(tmp_path: Path):
    source = json.loads(
        (Path("data/demo/conversations/s1-lost-card-normal.json")).read_text(
            encoding="utf-8"
        )
    )
    source["content"] = source["content"] + "\n客户：我的卡号是 1234567890123456。"
    (tmp_path / "one.json").write_text(
        json.dumps(source, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(DemoConversationError, match="不得包含数字"):
        load_demo_conversations(tmp_path)


def test_replay_rewrite_returns_json():
    responder = DemoReplayResponder()
    raw = responder(
        [{"role": "user", "content": "银行卡丢了怎么挂失？"}],
        CoreIntentPayload,
    )
    assert json.loads(raw) == {"core_query": "银行卡丢了怎么挂失？"}


def test_replay_candidate_answer_uses_recalled_chunks_only():
    responder = DemoReplayResponder()
    payload = {
        "query": "银行卡丢了怎么挂失？",
        "business_type": "lost_card",
        "knowledge": [
            {
                "chunk_id": "00000000-0000-0000-0000-000000000201",
                "version_id": "00000000-0000-0000-0000-000000000301",
                "title": "挂失渠道",
                "source_type": "demo_policy",
                "business_type": "lost_card",
                "text": "请通过官方手机银行或客服热线办理挂失。",
            }
        ],
    }
    raw = responder(
        [
            {"role": "system", "content": "只依据知识"},
            {"role": "user", "content": "请生成候选答复\n" + json.dumps(payload, ensure_ascii=False)},
        ],
        CandidateAnswerPayload,
    )
    answer = json.loads(raw)
    assert answer["status"] == "answered"
    assert answer["citations"] == [
        {
            "version_id": "00000000-0000-0000-0000-000000000301",
            "chunk_id": "00000000-0000-0000-0000-000000000201",
            "quote": "请通过官方手机银行或客服热线办理挂失。",
        }
    ]


def test_replay_candidate_without_knowledge_is_needs_human():
    responder = DemoReplayResponder()
    payload = {"query": "银行卡丢了", "business_type": "lost_card", "knowledge": []}
    raw = responder(
        [{"role": "user", "content": "请生成候选答复\n" + json.dumps(payload, ensure_ascii=False)}],
        CandidateAnswerPayload,
    )
    answer = json.loads(raw)
    assert answer["status"] == "needs_human"
    assert answer["citations"] == []


def test_replay_inspection_hit_has_exact_evidence_span():
    responder = DemoReplayResponder()
    messages = _inspection_messages(
        "DEMO-CMP-01",
        "客户：我要投诉。\n坐席：不可能处理，投诉也没用。",
    )
    raw = responder(messages, RuleJudgmentPayload)
    judgment = json.loads(raw)
    assert judgment["verdict"] == "hit"
    span = judgment["evidence"][0]
    content = _conversation_turns("客户：我要投诉。\n坐席：不可能处理，投诉也没用。")[1]["content"]
    assert content[span["start"] : span["end"]] == span["quote"]
    assert span["quote"] == "不可能处理"


def test_replay_inspection_clear_when_no_rule_behavior():
    responder = DemoReplayResponder()
    messages = _inspection_messages(
        "DEMO-CMP-01",
        "客户：我要投诉。\n坐席：您可以拨打官方客服热线提出投诉。",
    )
    judgment = json.loads(responder(messages, RuleJudgmentPayload))
    assert judgment["verdict"] == "clear"


def test_replay_att_false_positive_is_context_supported():
    """演示夹具里的误报必须能通过证据语境校验，才轮到人工驳回。"""

    content = "客户：我要投诉。\n坐席：不可能处理，你别再打了。"
    messages = _inspection_messages("DEMO-ATT-01", content)
    judgment = json.loads(DemoReplayResponder()(messages, RuleJudgmentPayload))
    assert judgment["verdict"] == "hit"
    span = judgment["evidence"][0]
    turn = _conversation_turns(content)[1]
    assert span["quote"] == "你别再打了"
    assert turn["content"][span["start"] : span["end"]] == span["quote"]
    assert assess_context(turn["content"], span["start"], span["end"]).supported is True
