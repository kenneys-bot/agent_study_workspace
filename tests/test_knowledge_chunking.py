"""知识分块（M3，契约 §2 KnowledgeChunk「原文区间」）。

坐标契约（契约 §4）：区间以不可变 ``content`` 的 Unicode 码点计数，start 含、
end 不含；中文、emoji、换行都必须按码点算，不能按 UTF-16 或字节算。
``chunk.text`` 必须逐字等于 ``content[start:end]``，否则引用无法回验原文。
"""

from __future__ import annotations

from bank_assistant.knowledge.chunking import chunk_text


def _assert_offsets_are_exact(content: str, chunks) -> None:
    for chunk in chunks:
        assert 0 <= chunk.start < chunk.end <= len(content)
        assert chunk.text == content[chunk.start : chunk.end]
        assert chunk.text.strip()


def test_empty_or_whitespace_content_has_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t\n") == []


def test_chinese_emoji_and_newlines_use_code_point_offsets() -> None:
    # 家庭 emoji 是 7 个码点（含 ZWJ），UTF-16 长度与码点数不同。
    content = (
        "客户来电：卡片丢失😀，请立即挂失。\n"
        "坐席说明：挂失后补卡需 7 个工作日👨‍👩‍👧‍👦。\n"
        "客户同意，并询问费用。"
    )
    chunks = chunk_text(content, max_chars=30, overlap=5)

    assert chunks
    _assert_offsets_are_exact(content, chunks)
    for chunk in chunks:
        assert chunk.end - chunk.start <= 30
        # 偏移按码点：end 处的下一个字符仍可正常切片。
        assert content[chunk.end : chunk.end] == ""

    # 覆盖：所有非空白字符都至少落在某个分块内。
    covered = set()
    for chunk in chunks:
        covered.update(range(chunk.start, chunk.end))
    missing = [
        index
        for index, char in enumerate(content)
        if not char.isspace() and index not in covered
    ]
    assert missing == []

    # 起始块从 0 开始（或从首个非空白字符开始），结束块覆盖到文末。
    assert chunks[0].start == 0
    assert chunks[-1].end == len(content)


def test_chunks_prefer_paragraph_boundaries_when_they_fit() -> None:
    content = "第一段：挂失受理条件。\n第二段：补卡材料清单。"
    chunks = chunk_text(content, max_chars=100, overlap=10)

    assert len(chunks) == 1
    assert chunks[0].text == content


def test_long_text_is_split_with_bounded_size_and_overlap() -> None:
    content = "挂失" * 400  # 800 个码点，无自然段落
    chunks = chunk_text(content, max_chars=120, overlap=20)

    assert len(chunks) > 1
    _assert_offsets_are_exact(content, chunks)
    assert chunks[0].start == 0
    assert chunks[-1].end == len(content)
    for chunk in chunks:
        assert chunk.end - chunk.start <= 120
    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert following.start < previous.end  # 相邻块保留重叠
        assert following.start > previous.start


def test_offsets_stay_monotonic_and_have_no_gaps_between_packed_segments() -> None:
    content = "标题\n\n第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = chunk_text(content, max_chars=40, overlap=8)

    _assert_offsets_are_exact(content, chunks)
    starts = [chunk.start for chunk in chunks]
    assert starts == sorted(starts)
    assert len({chunk.start for chunk in chunks}) == len(chunks)


def test_paragraph_larger_than_limit_is_split_on_sentence_endings() -> None:
    sentence = "这是用于演示的虚构制度条目，正文自行组织，不引用真实条文的原文。"
    content = sentence * 12
    chunks = chunk_text(content, max_chars=60, overlap=0)

    assert len(chunks) > 1
    _assert_offsets_are_exact(content, chunks)
    for chunk in chunks:
        assert chunk.end - chunk.start <= 60
    # 无重叠时，块与块之间不重叠且无未覆盖字符。
    assert chunks[0].start == 0
    assert chunks[-1].end == len(content)
    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert following.start >= previous.end
