"""quote-first 证据定位的离线测试（T40，#40；R-19；契约 §4）。

seam：``bank_assistant.inspections.locator`` 的公开函数。这里固定定位器的边界：

- 精确子串优先，命中区间就是原码点区间；
- 归一化匹配（NFKC + 空白/标点折叠）必须把区间严格映射回**原码点**，且持久化
  的 quote 取原文切片；
- 中文、emoji、换行、全角字符都按 Python 码点处理；
- 跨轮次不拼接；找不到返回 ``None``（调用方降级 finding，而不是失败 run）；
- 模型给的 turn/offset 只是提示。
"""

from __future__ import annotations

from uuid import uuid4

from bank_assistant.inspections.locator import locate_quote, normalize_for_match

TURN = uuid4()
OTHER = uuid4()


def _locate(quote: str, **kwargs):
    return locate_quote(quote, turns={TURN: "年费我给您免了"}, **kwargs)


def test_exact_substring_uses_code_point_range_and_original_quote() -> None:
    content = "这笔年费我给您免了"
    located = locate_quote("我给您免了", turns={TURN: content})

    assert located is not None
    assert (located.turn_id, located.start, located.end) == (TURN, 4, 9)
    assert located.quote == content[4:9]
    assert located.match == "exact"


def test_normalized_match_maps_back_to_original_code_points() -> None:
    content = "这笔年费，我给您免了。"
    located = locate_quote("这笔年费我给您免了", turns={TURN: content})

    assert located is not None
    assert located.match == "normalized"
    assert (located.start, located.end) == (0, 10)
    # 持久化 quote 取原文切片：包含原文逗号，不包含 quote 里没有的句号。
    assert located.quote == content[0:10]
    assert normalize_for_match(located.quote).text == normalize_for_match(
        "这笔年费我给您免了"
    ).text


def test_fullwidth_digits_are_folded_by_nfkc() -> None:
    content = "年费１２３元"
    located = locate_quote("年费123元", turns={TURN: content})

    assert located is not None
    assert located.match == "normalized"
    assert located.quote == content
    assert located.quote == "年费１２３元"


def test_emoji_is_one_code_point_and_newline_difference_is_normalized() -> None:
    content = "这笔年费我给您免了😀\n不用还"
    located = locate_quote("免了😀 不用", turns={TURN: content})

    assert located is not None
    assert located.match == "normalized"
    assert (located.start, located.end) == (7, 13)
    assert located.quote == content[7:13]
    assert located.quote.endswith("😀\n不用")


def test_newline_inside_quote_is_normalized_not_rewritten() -> None:
    content = "第一行\n第二行"
    located = locate_quote("第一行第二行", turns={TURN: content})

    assert located is not None
    assert located.quote == content
    assert "\n" in located.quote


def test_quote_spanning_two_turns_is_not_located() -> None:
    turns = {TURN: "第一行\n", OTHER: "第二行"}

    # 拼两轮才能得到的片段：不跨轮次拼接，直接视为找不到。
    assert locate_quote("行\n第", turns=turns) is None
    assert locate_quote("第一行\n第二行", turns=turns) is None
    # 单轮内的片段仍然能定位。
    assert locate_quote("第二行", turns=turns).turn_id == OTHER


def test_not_found_returns_none_instead_of_raising() -> None:
    assert locate_quote("原文里没有这句", turns={TURN: "年费我给您免了"}) is None
    assert locate_quote("", turns={TURN: "年费我给您免了"}) is None
    assert locate_quote("年费", turns={}) is None


def test_turn_hints_only_prioritize_never_override_the_original() -> None:
    turns = {TURN: "免了", OTHER: "年费"}

    # 错误提示不会让定位失败，也不会把 quote 安到错误的轮次。
    located = locate_quote("年费", turns=turns, turn_id=TURN)
    assert located is not None and located.turn_id == OTHER

    # ordinal 提示同样只是优先搜索。
    located = locate_quote(
        "年费", turns=turns, turn_ordinals={TURN: 1, OTHER: 2}, turn_ordinal=1
    )
    assert located is not None and located.turn_id == OTHER


def test_start_hint_disambiguates_repeated_occurrences() -> None:
    content = "免了。免了。"
    first = locate_quote("免了", turns={TURN: content})
    second = locate_quote("免了", turns={TURN: content}, start=3)

    assert first is not None and first.start == 0
    assert second is not None and second.start == 3
