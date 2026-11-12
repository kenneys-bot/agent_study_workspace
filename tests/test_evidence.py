"""证据坐标与校验的契约测试（T19，#20；契约 §4/§7 CT-08/CT-09）。

seam：``bank_assistant.inspections.evidence`` 的公开函数。这里固定三件事：

- **码点坐标**：后端 ``start/end`` 按不可变原文的 Unicode 码点计数，
  ``start`` 含、``end`` 不含；中文、换行、emoji（UTF-16 代理对）都不得偏移；
- **前后端换算**：JavaScript 字符串索引是 UTF-16 code unit，必须显式换算，
  不能让前端把 UTF-16 下标直接当码点用；
- **严格校验**：轮次归属、区间边界与 ``quote`` 逐字匹配，任一不满足即拒绝，
  不做任何规范化（不 trim、不折叠空白）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bank_assistant.inspections.evidence import (
    EvidenceError,
    annotate_evidence,
    check_span,
    code_point_length,
    code_point_to_utf16_offset,
    ensure_hit_evidence_valid,
    slice_by_code_points,
    utf16_length,
    utf16_offset_to_code_point,
    validate_evidence,
)

TURN = uuid4()
OTHER_TURN = uuid4()
EMOJI_TEXT = "年费😀已免"
NEWLINE_TEXT = "第一行\n第二行"
CHINESE_TEXT = "没有收取年费"


def _span(**overrides):
    payload = {
        "turn_id": str(TURN),
        "start": 0,
        "end": 2,
        "quote": "年费",
    }
    payload.update(overrides)
    return payload


def test_code_point_length_counts_astral_characters_as_one() -> None:
    assert code_point_length(EMOJI_TEXT) == 5
    assert utf16_length(EMOJI_TEXT) == 6
    assert code_point_length(CHINESE_TEXT) == 6
    assert code_point_length(NEWLINE_TEXT) == 7


def test_code_point_and_utf16_offsets_round_trip_around_surrogate_pairs() -> None:
    # 契约 §4：后端坐标是码点；前端 JS 下标是 UTF-16 code unit。
    assert [
        code_point_to_utf16_offset(EMOJI_TEXT, offset) for offset in range(6)
    ] == [0, 1, 2, 4, 5, 6]
    assert [
        utf16_offset_to_code_point(EMOJI_TEXT, offset)
        for offset in (0, 1, 2, 4, 5, 6)
    ] == [0, 1, 2, 3, 4, 5]


def test_offset_conversion_rejects_out_of_range_and_mid_surrogate_offsets() -> None:
    for bad in (-1, 6, 99):
        with pytest.raises(ValueError):
            code_point_to_utf16_offset(EMOJI_TEXT, bad)
    for bad in (-1, 7, 99):
        with pytest.raises(ValueError):
            utf16_offset_to_code_point(EMOJI_TEXT, bad)
    # UTF-16 偏移 3 落在 emoji 的代理对中间：不能静默对齐到任意一侧。
    with pytest.raises(ValueError):
        utf16_offset_to_code_point(EMOJI_TEXT, 3)
    with pytest.raises(ValueError):
        code_point_to_utf16_offset(EMOJI_TEXT, True)  # type: ignore[arg-type]


def test_slice_by_code_points_handles_emoji_and_newline() -> None:
    assert slice_by_code_points(EMOJI_TEXT, 2, 3) == "😀"
    assert slice_by_code_points(EMOJI_TEXT, 2, 5) == "😀已免"
    assert slice_by_code_points(NEWLINE_TEXT, 2, 5) == "行\n第"
    with pytest.raises(ValueError):
        slice_by_code_points(EMOJI_TEXT, 4, 2)
    with pytest.raises(ValueError):
        slice_by_code_points(EMOJI_TEXT, 0, 6)


def test_span_validation_accepts_exact_code_point_quotes() -> None:
    contents = {TURN: EMOJI_TEXT, OTHER_TURN: NEWLINE_TEXT}
    for span, content in (
        (_span(start=2, end=3, quote="😀"), EMOJI_TEXT),
        (_span(start=0, end=2, quote="年费"), EMOJI_TEXT),
        (_span(turn_id=str(OTHER_TURN), start=3, end=4, quote="\n"), NEWLINE_TEXT),
    ):
        assert check_span(span, turn_contents=contents).valid, (span, content)
        validate_evidence([span], turn_contents=contents)


def test_span_validation_rejects_utf16_offsets_used_as_code_points() -> None:
    """CT-08：把 UTF-16 下标直接当码点，会切到 emoji 之后的位置，必须失败。"""
    # 前端按 UTF-16 取 end=5 表示 "年费😀已"；后端码点 5 是全文末尾。
    check = check_span(
        _span(start=0, end=6, quote="年费😀已"),
        turn_contents={TURN: EMOJI_TEXT},
    )
    assert not check.valid
    assert check.reason


def test_span_validation_rejects_range_and_quote_mismatches() -> None:
    contents = {TURN: EMOJI_TEXT}
    cases = (
        (_span(end=0, quote="年"), "区间"),
        (_span(start=4, end=2, quote="已"), "区间"),
        (_span(start=0, end=99, quote="年费"), "范围"),
        (_span(start=0, end=2, quote="年 费"), "原文"),
        (_span(start=0, end=2, quote=" 年费"), "原文"),
    )
    for span, reason in cases:
        check = check_span(span, turn_contents=contents)
        assert not check.valid
        assert reason in (check.reason or "")
        with pytest.raises(EvidenceError, match=reason):
            validate_evidence([span], turn_contents=contents)


def test_span_validation_rejects_turns_not_in_the_inspected_conversation() -> None:
    check = check_span(
        _span(turn_id=str(uuid4())),
        turn_contents={TURN: EMOJI_TEXT},
    )
    assert not check.valid
    assert "轮次" in (check.reason or "")
    with pytest.raises(EvidenceError):
        validate_evidence(
            [_span(turn_id=str(uuid4()))], turn_contents={TURN: EMOJI_TEXT}
        )


def test_span_validation_rejects_malformed_stored_evidence() -> None:
    for broken in (
        {},
        {"turn_id": "not-a-uuid", "start": 0, "end": 2, "quote": "年费"},
        {"turn_id": str(TURN), "start": "0", "end": 2, "quote": "年费"},
        {"turn_id": str(TURN), "start": 0, "end": 2, "quote": ""},
    ):
        check = check_span(broken, turn_contents={TURN: EMOJI_TEXT})
        assert not check.valid


def test_annotate_evidence_exposes_ordinal_validated_and_reason() -> None:
    contents = {TURN: EMOJI_TEXT}
    annotated = annotate_evidence(
        [
            _span(start=2, end=3, quote="😀"),
            _span(start=2, end=3, quote="已"),  # 原文不匹配
        ],
        turn_contents=contents,
        turn_ordinals={TURN: 7},
    )

    assert annotated[0] == {
        "turn_id": str(TURN),
        "turn_ordinal": 7,
        "start": 2,
        "end": 3,
        "quote": "😀",
        "validated": True,
    }
    assert annotated[1]["validated"] is False
    assert annotated[1]["turn_ordinal"] == 7
    assert annotated[1]["validation_error"]


def test_hit_evidence_requires_at_least_one_exact_span() -> None:
    with pytest.raises(EvidenceError):
        ensure_hit_evidence_valid([], turn_contents={TURN: EMOJI_TEXT})
    with pytest.raises(EvidenceError):
        ensure_hit_evidence_valid(
            [_span(start=0, end=2, quote="年 费")],
            turn_contents={TURN: EMOJI_TEXT},
        )
    ensure_hit_evidence_valid(
        [_span(start=0, end=2, quote="年费")],
        turn_contents={TURN: EMOJI_TEXT},
    )
