"""显式回放用的确定性模型响应（T25，M5-04）。

目的只有一个：在没有云模型、也不烧钱的前提下，让浏览器端到端与演示脚本走完
**真实的业务链路**（检索、引用校验、质检规则快照、worker、复核、导出）。

边界与诚实性要求：

- 回放只在 ``MODEL_MODE=replay`` 或显式构造 ``ModelGateway(replay_responder=...)``
  时启用；结果与 ``model_runs.mode`` 都标记 ``replay``；
- 回放内容不是模型质量证据，也不是真实银行结论；报告与界面必须如实标注；
- 质检回放里对 S3 风险会话的「你别再打了」刻意给出一次**误报**（判为侮辱性
  表达），用于演示人工驳回误报；这是演示夹具，不代表真实模型表现；
- 引用只使用本次召回里真实存在的 ``chunk_id`` / ``version_id`` 与逐字原文，
  仍然经过后端的引用校验与证据回验。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Optional, Type

from pydantic import BaseModel

from bank_assistant.assistants.candidate_answer import CandidateAnswerPayload
from bank_assistant.assistants.suggestions import CoreIntentPayload
from bank_assistant.inspections.schema import RuleJudgmentPayload

__all__ = ["DemoReplayResponder"]

_KEYWORDS = {
    "lost_card": ("挂失", "银行卡", "验证码", "手机银行"),
    "credit_card_fee": ("年费", "费用", "还款", "减免", "账单"),
    "complaint": ("投诉", "受理", "渠道", "处理时限"),
}

_HIT_PATTERNS: dict[str, tuple[str, ...]] = {
    "DEMO-PWD-01": ("短信验证码报给我", "把验证码告诉我", "密码告诉我"),
    "DEMO-CLAIM-01": ("已经帮您办理成功", "已经办理成功", "已经帮您办好了"),
    "DEMO-FEE-01": ("我给您免了", "给您减免", "保证给您免", "直接给您免"),
    "DEMO-FEE-02": ("永久免年费",),
    "DEMO-CMP-01": ("不可能处理", "投诉也没用", "不予受理", "随便您投诉"),
    # 最后一项是刻意的回放误报：S3 风险会话的“你别再打了”会被判成侮辱性表达，
    # 演示时由质检员驳回。它不在规则的正面示例里，也不代表模型真实能力。
    "DEMO-ATT-01": ("这么笨", "废物", "闭嘴", "滚出去", "你别再打了"),
}

_AGENT_SPEAKERS = {"坐席", "客服", "agent", "Agent"}


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start < 0:
        raise ValueError("回放输入缺少 JSON 数据区")
    payload = json.loads(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("回放输入数据区不是 JSON 对象")
    return payload


def _find_quote(
    turns: Sequence[Mapping[str, Any]], patterns: Sequence[str]
) -> tuple[dict[str, Any], str] | None:
    for turn in turns:
        if str(turn.get("speaker", "")) not in _AGENT_SPEAKERS:
            continue
        content = str(turn.get("content", ""))
        for pattern in patterns:
            if pattern in content:
                return dict(turn), pattern
    return None


def _judgment(rule_code: str, turns: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    patterns = _HIT_PATTERNS.get(rule_code)
    if patterns is None:
        return {
            "verdict": "insufficient_evidence",
            "rationale": "回放没有该规则的判定脚本，按证据不足处理。",
            "evidence": [],
            "knowledge_refs": [],
        }
    hit = _find_quote(turns, patterns)
    if hit is None:
        return {
            "verdict": "clear",
            "rationale": "回放：未发现规则要求的坐席行为，判为未命中。",
            "evidence": [],
            "knowledge_refs": [],
        }
    turn, quote = hit
    content = str(turn["content"])
    start = content.find(quote)
    return {
        "verdict": "hit",
        "rationale": f"回放：坐席原文出现“{quote}”，命中演示规则 {rule_code}。",
        "evidence": [
            {
                "turn_id": str(turn["turn_id"]),
                "start": start,
                "end": start + len(quote),
                "quote": quote,
            }
        ],
        "knowledge_refs": [],
    }


def _candidate_answer(payload: Mapping[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", ""))
    business_type = str(payload.get("business_type") or "")
    knowledge = payload.get("knowledge")
    if not isinstance(knowledge, list) or not knowledge:
        return {
            "status": "needs_human",
            "answer": "",
            "citations": [],
            "missing_information": ["本次没有可引用的有效知识"],
        }

    keywords = _KEYWORDS.get(business_type, ())
    scored: list[tuple[int, Mapping[str, Any]]] = []
    for chunk in knowledge:
        if not isinstance(chunk, Mapping):
            continue
        text = str(chunk.get("text", ""))
        if not text.strip():
            continue
        score = sum(1 for keyword in keywords if keyword in query and keyword in text)
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [chunk for _, chunk in scored[:2]]
    if not selected:
        return {
            "status": "needs_human",
            "answer": "",
            "citations": [],
            "missing_information": ["召回结果没有可用原文"],
        }

    excerpts = [str(chunk["text"]).strip()[:160] for chunk in selected]
    answer = (
        "根据本次召回的演示知识："
        + " ".join(excerpts)
        + "（回放模式，仅供演示；具体以官方渠道与有效条款为准。）"
    )
    citations = [
        {
            "version_id": str(chunk["version_id"]),
            "chunk_id": str(chunk["chunk_id"]),
            "quote": str(chunk["text"])[:200],
        }
        for chunk in selected
    ]
    return {
        "status": "answered",
        "answer": answer,
        "citations": citations,
        "missing_information": [],
    }


class DemoReplayResponder:
    """``ModelGateway`` 的可调用回放处理器；按 schema 返回确定性 JSON 字符串。"""

    def __call__(
        self,
        messages: Sequence[Mapping[str, str]],
        schema: Optional[Type[BaseModel]],
    ) -> str:
        user = messages[-1]["content"] if messages else ""
        if schema is CoreIntentPayload:
            return json.dumps({"core_query": user.strip() or "未提供问题"}, ensure_ascii=False)
        if schema is CandidateAnswerPayload:
            return json.dumps(
                _candidate_answer(_extract_json(user)), ensure_ascii=False
            )
        if schema is RuleJudgmentPayload:
            data = _extract_json(user)
            rule = data.get("rule")
            if not isinstance(rule, Mapping):
                raise ValueError("回放输入缺少 rule")
            turns = data.get("conversation")
            if not isinstance(turns, list):
                raise ValueError("回放输入缺少 conversation")
            return json.dumps(
                _judgment(str(rule.get("rule_code", "")), turns),
                ensure_ascii=False,
            )
        raise ValueError(
            f"demo replay 不支持 schema={getattr(schema, '__name__', schema)!r}"
        )
