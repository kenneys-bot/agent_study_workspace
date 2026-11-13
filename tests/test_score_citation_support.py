"""引用支持度计分（T42，#42）的离线测试。

覆盖：全支持/全不支持/混合/仅 UNKNOWN/零标注/样本不足/无证据行/坏标签/
缺列/重复 id/BOM 与空白，以及一个 10 行合成夹具上的 Wilson 数学。
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from bank_assistant.evaluation.citation_support import (
    ALLOWED_LABELS,
    ALLOWED_SCENARIOS,
    REQUIRED_COLUMNS,
    CitationSupportError,
    load_citation_rows,
    score_citation_support,
    wilson_interval,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "score_citation_support.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "citation-support-synthetic.csv"


def _row(index: int, *, label: str = "", scenario: str = "lost_card", quote: str | None = None) -> dict[str, str]:
    if quote is None:
        quote = f"引用原文 {index}"
    return {
        "id": f"row-{index:03d}",
        "scenario": scenario,
        "query": f"问题 {index}",
        "answer_excerpt": f"答复节选 {index}",
        "citation_quote": quote,
        "knowledge_title": f"知识标题（v1）",
        "label": label,
        "note": "",
    }


def _write_csv(path: Path, rows: list[dict[str, str]], *, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_wilson_interval_known_and_edges() -> None:
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(9, 10)
    assert low == pytest.approx(0.5958, abs=1e-4)
    assert high == pytest.approx(0.9821, abs=1e-4)
    zero_low, zero_high = wilson_interval(0, 10)
    assert zero_low == 0.0
    assert zero_high == pytest.approx(0.2775, abs=1e-4)
    full_low, full_high = wilson_interval(10, 10)
    assert full_low == pytest.approx(0.7225, abs=1e-4)
    assert full_high == 1.0


def test_tiny_synthetic_fixture_math() -> None:
    result = _run("--labels", str(FIXTURE))
    assert result.returncode == 0, result.stderr
    assert "labeled=10" in result.stdout
    assert "support_rate=0.9000 (9/10)" in result.stdout
    assert "unknown_share=0.0000 (0/10)" in result.stdout
    assert "wilson_95_ci=[0.5958, 0.9821]" in result.stdout
    # 10 < 50：点估计可看，但不能给 §7 的 PASS/FAIL 结论。
    assert "verdict=INSUFFICIENT_SAMPLE" in result.stdout


def test_waiting_for_human_when_no_labeled_rows(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "labels.csv", [_row(i) for i in range(1, 6)])
    result = _run("--labels", str(path))
    assert result.returncode == 0, result.stderr
    assert "labeled=0" in result.stdout
    assert "WAITING_FOR_HUMAN" in result.stdout
    assert "support_rate" not in result.stdout
    assert "verdict=WAITING_FOR_HUMAN" not in result.stdout  # 只输出等待行


def test_waiting_json_has_no_score(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "labels.csv", [_row(i) for i in range(1, 4)])
    result = _run("--labels", str(path), "--json")
    assert result.returncode == 0, result.stderr
    payload = result.stdout
    assert '"status": "WAITING_FOR_HUMAN"' in payload
    assert '"support_rate": null' in payload
    assert '"wilson_95_ci": null' in payload


def test_all_supported_pass(tmp_path: Path) -> None:
    rows = [_row(i, label="SUPPORTED") for i in range(1, 61)]
    path = _write_csv(tmp_path / "labels.csv", rows)
    result = _run("--labels", str(path))
    assert result.returncode == 0, result.stderr
    assert "support_rate=1.0000 (60/60)" in result.stdout
    assert "support_rate" in result.stdout
    assert "verdict=PASS" in result.stdout


def test_all_unsupported_fail(tmp_path: Path) -> None:
    rows = [_row(i, label="NOT_SUPPORTED") for i in range(1, 61)]
    path = _write_csv(tmp_path / "labels.csv", rows)
    result = _run("--labels", str(path))
    assert result.returncode == 0, result.stderr
    assert "support_rate=0.0000 (0/60)" in result.stdout
    assert "verdict=FAIL" in result.stdout


def test_mixed_unknown_and_threshold(tmp_path: Path) -> None:
    rows = (
        [_row(i, label="SUPPORTED") for i in range(1, 58)]
        + [_row(i, label="NOT_SUPPORTED") for i in range(58, 60)]
        + [_row(60, label="UNKNOWN")]
    )
    path = _write_csv(tmp_path / "labels.csv", rows)
    report = score_citation_support(load_citation_rows(path))
    assert report.labeled == 60
    assert report.supported == 57
    assert report.not_supported == 2
    assert report.unknown == 1
    assert report.support_rate == pytest.approx(57 / 60)
    assert report.unknown_share == pytest.approx(1 / 60)
    assert report.verdict == "PASS"


def test_unknown_only_is_fail(tmp_path: Path) -> None:
    rows = [_row(i, label="UNKNOWN") for i in range(1, 11)]
    path = _write_csv(tmp_path / "labels.csv", rows)
    result = _run("--labels", str(path), "--min-labeled", "10")
    assert result.returncode == 0, result.stderr
    assert "support_rate=0.0000 (0/10)" in result.stdout
    assert "unknown_share=1.0000 (10/10)" in result.stdout
    assert "verdict=FAIL" in result.stdout


def test_no_evidence_labeled_rows_are_excluded(tmp_path: Path) -> None:
    rows = [_row(i, label="SUPPORTED") for i in range(1, 6)]
    rows.append(_row(6, label="UNKNOWN", quote="", scenario="complaint"))
    rows.append(_row(7, label="SUPPORTED", quote="", scenario="credit_card_fee"))
    path = _write_csv(tmp_path / "labels.csv", rows)
    report = score_citation_support(load_citation_rows(path))
    assert report.labeled == 5
    assert report.citation_rows == 5
    assert report.no_evidence_rows == 2
    assert report.ignored_no_citation_labeled == 2
    assert report.support_rate == 1.0


def test_scenario_breakdown(tmp_path: Path) -> None:
    rows = [
        _row(1, label="SUPPORTED", scenario="lost_card"),
        _row(2, label="NOT_SUPPORTED", scenario="lost_card"),
        _row(3, label="SUPPORTED", scenario="credit_card_fee"),
        _row(4, label="UNKNOWN", scenario="complaint"),
    ]
    path = _write_csv(tmp_path / "labels.csv", rows)
    report = score_citation_support(load_citation_rows(path))
    by_scenario = {item.scenario: item for item in report.per_scenario}
    assert by_scenario["lost_card"].labeled == 2
    assert by_scenario["lost_card"].support_rate == 0.5
    assert by_scenario["credit_card_fee"].support_rate == 1.0
    assert by_scenario["complaint"].support_rate == 0.0
    assert by_scenario["complaint"].unknown_share == 1.0
    assert by_scenario["complaint"].wilson_95_ci is not None


def test_malformed_label_fails_with_row_id(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "bad.csv", [_row(1, label="MAYBE")])
    result = _run("--labels", str(path))
    assert result.returncode == 2
    assert "row-001" in result.stderr
    assert "MAYBE" in result.stderr


def test_missing_column_fails(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    path.write_text("id,scenario,query\nrow-1,lost_card,问题\n", encoding="utf-8")
    result = _run("--labels", str(path))
    assert result.returncode == 2
    assert "缺少必需列" in result.stderr


def test_duplicate_id_fails(tmp_path: Path) -> None:
    rows = [_row(1), _row(1)]
    path = _write_csv(tmp_path / "dup.csv", rows)
    result = _run("--labels", str(path))
    assert result.returncode == 2
    assert "id 重复" in result.stderr


def test_bom_and_whitespace_label_normalization(tmp_path: Path) -> None:
    rows = [_row(i, label=" supported ") for i in range(1, 4)]
    path = _write_csv(tmp_path / "bom.csv", rows, encoding="utf-8-sig")
    loaded = load_citation_rows(path)
    assert [row.label for row in loaded] == ["SUPPORTED", "SUPPORTED", "SUPPORTED"]
    report = score_citation_support(loaded, min_labeled=3)
    assert report.verdict == "PASS"
    assert ALLOWED_LABELS == ("SUPPORTED", "NOT_SUPPORTED", "UNKNOWN")
    assert set(ALLOWED_SCENARIOS) == {"lost_card", "credit_card_fee", "complaint"}
