"""确定性否定/转述检查的单元测试（T19，#20；M4-03「否定与引用他人话语不能按关键词直接判定」）。

seam：``bank_assistant.inspections.deterministic`` 的公开函数。这里固定的是
**确定性检查的边界**，不是通用中文语法分析：

- 否定标记必须落在风险动作之前（同一分句内）才否定该动作；所以
  “投诉也没用”里的“没用”不推翻“投诉”这个阻碍投诉的行为；
- 引用/转述标记（“客户说”“他说”“有人告诉他”“听说”等）使紧随其后的
  风险表述不算坐席自身行为；
- 命中证据若落在否定或转述语境，不能写入 ``hit``。
"""

from __future__ import annotations

import pytest

from bank_assistant.inspections.deterministic import (
    actionable_occurrences,
    assess_context,
    scan_keywords,
)


def test_keyword_inside_a_negation_is_not_actionable() -> None:
    text = "我们明确告知客户，没有收取年费。"
    occurrences = scan_keywords(text, ["收取年费"])

    assert len(occurrences) == 1
    assert occurrences[0].negated is True
    assert occurrences[0].supported is False
    assert actionable_occurrences(text, ["收取年费"]) == []


def test_negated_span_starting_with_the_marker_is_not_actionable() -> None:
    text = "没有收取年费"
    assessment = assess_context(text, 0, len(text))

    assert assessment.negated is True
    assert assessment.supported is False


def test_keyword_inside_quoted_speech_is_not_actionable() -> None:
    text = "客户说有人告诉他，可以免年费。"
    occurrences = scan_keywords(text, ["免年费"])

    assert occurrences[0].attributed is True
    assert occurrences[0].supported is False
    assert actionable_occurrences(text, ["免年费"]) == []

    # 引用语常常在逗号后继续：转述判断要跨越分句，不能只看逗号后的片段。
    assessment = assess_context(text, text.index("可以"), len(text) - 1)
    assert assessment.attributed is True
    assert assessment.supported is False


def test_verbs_of_speech_do_not_turn_a_direct_promise_into_attribution() -> None:
    # “说明/告知”是坐席自己的说明，不是引用他人；不能被当成转述标记。
    text = "坐席说明减免需审批，我帮您提交申请。"
    occurrences = scan_keywords(text, ["提交申请"])

    assert occurrences[0].attributed is False
    assert occurrences[0].supported is True


def test_attribution_marker_from_a_third_party_is_detected() -> None:
    text = "他说已经办理成功，您放心。"
    occurrences = scan_keywords(text, ["办理成功"])
    assert occurrences[0].attributed is True

    text = "客户听说可以免年费，来问是不是真的。"
    occurrences = scan_keywords(text, ["免年费"])
    assert occurrences[0].attributed is True


def test_direct_risk_action_stays_actionable() -> None:
    text = "这笔年费我给您免了，不用还。"
    occurrences = scan_keywords(text, ["免了"])

    assert occurrences[0].negated is False
    assert occurrences[0].attributed is False
    assert occurrences[0].supported is True

    # 阻碍投诉的“没用”在关键词之后，不否定“投诉”这个行为本身。
    complaint = "投诉也没用，你别再打了。"
    occurrences = scan_keywords(complaint, ["投诉"])
    assert occurrences[0].supported is True


def test_negation_that_precedes_the_action_is_detected() -> None:
    text = "不会向您索要验证码，请自行输入。"
    assert actionable_occurrences(text, ["索要验证码"]) == []
    assert actionable_occurrences(text, ["自行输入"])[0].supported is True


def test_negation_is_scoped_to_the_current_clause() -> None:
    # 前一分句的否定不跨逗号否定后一分句的动作。
    text = "不是说您，我给您免了。"
    occurrences = scan_keywords(text, ["免了"])

    assert occurrences[0].negated is False
    assert occurrences[0].supported is True


def test_scan_reports_every_occurrence_with_code_point_offsets() -> None:
    text = "免年费可以，但免年费要审批。"
    occurrences = scan_keywords(text, ["免年费"])

    assert [occurrence.start for occurrence in occurrences] == [0, 7]
    assert [occurrence.end for occurrence in occurrences] == [3, 10]
    assert all(text[o.start : o.end] == "免年费" for o in occurrences)


def test_assess_context_rejects_invalid_ranges() -> None:
    text = "免年费"
    for start, end in ((-1, 2), (2, 1), (0, 99)):
        with pytest.raises(ValueError):
            assess_context(text, start, end)
