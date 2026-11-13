"""标注包结构完整性（T42，#42）。

不判断标签值（负责人可能已开始填写），只校验：CSV 列、≥60 条引用、三场景、
无证据对照、id 唯一、引用原文确实来自演示知识且版本号匹配。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from bank_assistant.datasets.corpus import load_corpus
from bank_assistant.evaluation.citation_support import (
    ALLOWED_SCENARIOS,
    REQUIRED_COLUMNS,
)

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "labels" / "citation-support.csv"
PACK_PATH = ROOT / "docs" / "eval" / "citation-support-pack.md"
_TITLE_VERSION = re.compile(r"^(?P<title>.+)（v(?P<version>\d+)）$")


def _rows() -> list[dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == REQUIRED_COLUMNS
        return list(reader)


def test_pack_has_required_columns_counts_and_scenarios() -> None:
    rows = _rows()
    citations = [row for row in rows if row["citation_quote"]]
    no_evidence = [row for row in rows if not row["citation_quote"]]

    assert len(citations) >= 60
    assert {row["scenario"] for row in citations} == set(ALLOWED_SCENARIOS)
    assert no_evidence
    assert len({row["id"] for row in rows}) == len(rows)
    assert PACK_PATH.is_file()
    pack_text = PACK_PATH.read_text(encoding="utf-8")
    assert "WAITING_FOR_HUMAN" in pack_text
    assert "禁止预填" in pack_text
    assert "score_citation_support.py" in pack_text


def test_every_quote_is_from_the_demo_knowledge() -> None:
    corpus = load_corpus()
    by_title = {entry.title: entry for entry in corpus.knowledge}
    for row in _rows():
        if not row["citation_quote"]:
            assert row["knowledge_title"] == ""
            continue
        match = _TITLE_VERSION.match(row["knowledge_title"])
        assert match, row["id"]
        entry = by_title[match.group("title")]
        assert int(match.group("version")) == entry.version_no
        assert row["citation_quote"] in entry.content, row["id"]
        assert row["query"]
        assert row["answer_excerpt"]
