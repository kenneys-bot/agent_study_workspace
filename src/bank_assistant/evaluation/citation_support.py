"""引用支持度人工标注计分（T42，#42）。

本模块只处理**人工标签**，不产生也不推断标签：

- ``label`` 只允许 ``SUPPORTED`` / ``NOT_SUPPORTED`` / ``UNKNOWN``；
- 未填写 ``label`` 的引用不进入分母，报告里单独计为 ``unlabeled``；
- ``citation_quote`` 为空的行是「无证据/证据不足」对照项（系统应拒答或转人工），
  不参与引用支持率；即使误填了标签也只计数并提示，不静默混入分母；
- 没有任何已标注引用时输出 ``WAITING_FOR_HUMAN``，不产生点估计或置信区间。

支持率的主分母是 **全部已标注引用**（含 ``UNKNOWN``），因为计划 §7 问的是
「关键事实被引用支持的比例」，无法判断不能被当作支持。另单独报告
``unknown_share``，便于区分“不支持”和“无法判断”。Wilson 95% 区间只用于
描述本次抽样的不确定性，不改变门槛判定。
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ALLOWED_LABELS",
    "ALLOWED_SCENARIOS",
    "DEFAULT_MIN_LABELED",
    "DEFAULT_THRESHOLD",
    "REQUIRED_COLUMNS",
    "CitationRow",
    "CitationSupportError",
    "CitationSupportReport",
    "ScenarioSupport",
    "load_citation_rows",
    "render_report",
    "report_to_dict",
    "score_citation_support",
    "wilson_interval",
]

REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "scenario",
    "query",
    "answer_excerpt",
    "citation_quote",
    "knowledge_title",
    "label",
    "note",
)
ALLOWED_LABELS: tuple[str, ...] = ("SUPPORTED", "NOT_SUPPORTED", "UNKNOWN")
ALLOWED_SCENARIOS: tuple[str, ...] = ("lost_card", "credit_card_fee", "complaint")
DEFAULT_MIN_LABELED = 50
DEFAULT_THRESHOLD = 0.95

WAITING = "WAITING_FOR_HUMAN"
SCORED = "SCORED"
PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class CitationSupportError(ValueError):
    """CSV 结构、行内容或标签不合法；调用方必须显式失败。"""


@dataclass(frozen=True, slots=True)
class CitationRow:
    """一条标注行；``label is None`` 表示负责人尚未标注。"""

    id: str
    scenario: str
    query: str
    answer_excerpt: str
    citation_quote: str
    knowledge_title: str
    label: str | None
    note: str
    line: int

    @property
    def has_citation(self) -> bool:
        return bool(self.citation_quote.strip())

    @property
    def is_labeled(self) -> bool:
        return self.has_citation and self.label is not None


@dataclass(frozen=True, slots=True)
class ScenarioSupport:
    scenario: str
    rows: int
    labeled: int
    supported: int
    not_supported: int
    unknown: int
    support_rate: float | None
    unknown_share: float | None
    wilson_95_ci: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class CitationSupportReport:
    status: str
    verdict: str
    total_rows: int
    citation_rows: int
    no_evidence_rows: int
    labeled: int
    unlabeled: int
    supported: int
    not_supported: int
    unknown: int
    ignored_no_citation_labeled: int
    support_rate: float | None
    unknown_share: float | None
    wilson_95_ci: tuple[float, float] | None
    threshold: float
    min_labeled: int
    per_scenario: tuple[ScenarioSupport, ...]


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _error(path: Path, line: int, message: str) -> CitationSupportError:
    return CitationSupportError(f"{path}:{line}: {message}")


def load_citation_rows(path: str | Path) -> list[CitationRow]:
    """读取并校验标注 CSV；任何结构/标签错误都带行号显式失败。"""
    labels_path = Path(path)
    if not labels_path.is_file():
        raise CitationSupportError(f"标注文件不存在：{labels_path}")

    rows: list[CitationRow] = []
    seen: set[str] = set()
    try:
        handle = labels_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CitationSupportError(f"无法读取标注文件 {labels_path}：{exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CitationSupportError(f"{labels_path}: 文件为空，缺少表头")
        actual = {_text(name): name for name in reader.fieldnames if name is not None}
        missing = [column for column in REQUIRED_COLUMNS if column not in actual]
        if missing:
            raise CitationSupportError(
                f"{labels_path}: 缺少必需列 {', '.join(missing)}；"
                f"实际列 {', '.join(str(name) for name in reader.fieldnames)}"
            )

        for line, raw in enumerate(reader, start=2):
            if raw is None:
                continue
            if not any(_text(value) for value in raw.values()):
                continue

            def get(column: str) -> str:
                return _text(raw.get(actual[column]))

            row_id = get("id")
            if not row_id:
                raise _error(labels_path, line, "id 不能为空")
            if row_id in seen:
                raise _error(labels_path, line, f"id 重复：{row_id!r}")
            seen.add(row_id)

            scenario = get("scenario")
            if scenario not in ALLOWED_SCENARIOS:
                raise _error(
                    labels_path,
                    line,
                    f"scenario 必须是 {ALLOWED_SCENARIOS} 之一，实际 {scenario!r}",
                )

            query = get("query")
            if not query:
                raise _error(labels_path, line, f"id={row_id!r} 的 query 不能为空")

            citation_quote = get("citation_quote")
            knowledge_title = get("knowledge_title")
            if citation_quote and not knowledge_title:
                raise _error(
                    labels_path,
                    line,
                    f"id={row_id!r} 有引用但 knowledge_title 为空",
                )

            raw_label = get("label")
            label = raw_label.upper() if raw_label else None
            if label is not None and label not in ALLOWED_LABELS:
                raise _error(
                    labels_path,
                    line,
                    f"id={row_id!r} 的 label 必须是 {ALLOWED_LABELS} 之一或留空，"
                    f"实际 {raw_label!r}",
                )

            rows.append(
                CitationRow(
                    id=row_id,
                    scenario=scenario,
                    query=query,
                    answer_excerpt=get("answer_excerpt"),
                    citation_quote=citation_quote,
                    knowledge_title=knowledge_title,
                    label=label,
                    note=get("note"),
                    line=line,
                )
            )
    return rows


def wilson_interval(
    successes: int, total: int, *, z: float = 1.96
) -> tuple[float, float] | None:
    """Wilson 95% 区间；``total == 0`` 时返回 ``None``，不伪造区间。"""
    if total < 0:
        raise ValueError("total 不能为负数")
    if not 0 <= successes <= total:
        raise ValueError("successes 必须在 [0, total] 内")
    if z <= 0:
        raise ValueError("z 必须为正数")
    if total == 0:
        return None
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1.0 - rate) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _scenario_support(scenario: str, rows: Sequence[CitationRow]) -> ScenarioSupport:
    scenario_rows = [row for row in rows if row.scenario == scenario]
    labeled = [row for row in scenario_rows if row.label is not None]
    supported = sum(1 for row in labeled if row.label == "SUPPORTED")
    not_supported = sum(1 for row in labeled if row.label == "NOT_SUPPORTED")
    unknown = sum(1 for row in labeled if row.label == "UNKNOWN")
    return ScenarioSupport(
        scenario=scenario,
        rows=len(scenario_rows),
        labeled=len(labeled),
        supported=supported,
        not_supported=not_supported,
        unknown=unknown,
        support_rate=_rate(supported, len(labeled)),
        unknown_share=_rate(unknown, len(labeled)),
        wilson_95_ci=wilson_interval(supported, len(labeled)),
    )


def score_citation_support(
    rows: Sequence[CitationRow],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_labeled: int = DEFAULT_MIN_LABELED,
) -> CitationSupportReport:
    """计算支持率/无法判断率/分场景/Wilson 区间与门槛判定。"""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold 必须在 [0, 1] 内")
    if min_labeled < 0:
        raise ValueError("min_labeled 不能为负数")

    citation_rows = [row for row in rows if row.has_citation]
    no_evidence_rows = [row for row in rows if not row.has_citation]
    labeled = [row for row in citation_rows if row.label is not None]
    ignored_no_citation_labeled = sum(
        1 for row in no_evidence_rows if row.label is not None
    )
    per_scenario = tuple(
        _scenario_support(scenario, citation_rows) for scenario in ALLOWED_SCENARIOS
    )

    if not labeled:
        return CitationSupportReport(
            status=WAITING,
            verdict=WAITING,
            total_rows=len(rows),
            citation_rows=len(citation_rows),
            no_evidence_rows=len(no_evidence_rows),
            labeled=0,
            unlabeled=len(citation_rows),
            supported=0,
            not_supported=0,
            unknown=0,
            ignored_no_citation_labeled=ignored_no_citation_labeled,
            support_rate=None,
            unknown_share=None,
            wilson_95_ci=None,
            threshold=threshold,
            min_labeled=min_labeled,
            per_scenario=per_scenario,
        )

    supported = sum(1 for row in labeled if row.label == "SUPPORTED")
    not_supported = sum(1 for row in labeled if row.label == "NOT_SUPPORTED")
    unknown = sum(1 for row in labeled if row.label == "UNKNOWN")
    support_rate = supported / len(labeled)
    if len(labeled) < min_labeled:
        verdict = INSUFFICIENT_SAMPLE
    elif support_rate >= threshold:
        verdict = PASS
    else:
        verdict = FAIL

    return CitationSupportReport(
        status=SCORED,
        verdict=verdict,
        total_rows=len(rows),
        citation_rows=len(citation_rows),
        no_evidence_rows=len(no_evidence_rows),
        labeled=len(labeled),
        unlabeled=len(citation_rows) - len(labeled),
        supported=supported,
        not_supported=not_supported,
        unknown=unknown,
        ignored_no_citation_labeled=ignored_no_citation_labeled,
        support_rate=support_rate,
        unknown_share=unknown / len(labeled),
        wilson_95_ci=wilson_interval(supported, len(labeled)),
        threshold=threshold,
        min_labeled=min_labeled,
        per_scenario=per_scenario,
    )


def render_report(report: CitationSupportReport) -> str:
    """人类可读的 stdout；零标注时只输出等待状态，不输出任何分数。"""
    lines = [
        f"rows={report.total_rows}",
        f"citation_rows={report.citation_rows}",
        f"no_evidence_rows={report.no_evidence_rows}",
        f"labeled={report.labeled}",
        f"unlabeled={report.unlabeled}",
    ]
    if report.ignored_no_citation_labeled:
        lines.append(
            "warning=no_evidence_rows_labeled_but_excluded:"
            f"{report.ignored_no_citation_labeled}"
        )
    if report.status == WAITING:
        lines.append(WAITING)
        return "\n".join(lines)

    assert report.support_rate is not None
    assert report.unknown_share is not None
    assert report.wilson_95_ci is not None
    low, high = report.wilson_95_ci
    lines.extend(
        [
            f"supported={report.supported}",
            f"not_supported={report.not_supported}",
            f"unknown={report.unknown}",
            f"support_rate={report.support_rate:.4f} "
            f"({report.supported}/{report.labeled})",
            f"unknown_share={report.unknown_share:.4f} "
            f"({report.unknown}/{report.labeled})",
            f"wilson_95_ci=[{low:.4f}, {high:.4f}]",
            "per_scenario:",
        ]
    )
    for scenario in report.per_scenario:
        if scenario.support_rate is None:
            lines.append(f"  {scenario.scenario}: labeled=0")
            continue
        assert scenario.wilson_95_ci is not None
        s_low, s_high = scenario.wilson_95_ci
        lines.append(
            f"  {scenario.scenario}: labeled={scenario.labeled} "
            f"supported={scenario.supported} "
            f"not_supported={scenario.not_supported} "
            f"unknown={scenario.unknown} "
            f"support_rate={scenario.support_rate:.4f} "
            f"unknown_share={scenario.unknown_share:.4f} "
            f"wilson_95_ci=[{s_low:.4f}, {s_high:.4f}]"
        )
    lines.append(f"threshold={report.threshold:.2f}")
    lines.append(
        f"verdict={report.verdict} (denominator={report.labeled}, "
        f"min_labeled={report.min_labeled})"
    )
    if report.verdict == INSUFFICIENT_SAMPLE:
        lines.append(
            f"note=已标注 {report.labeled} 条 < 计划要求的 {report.min_labeled} 条；"
            "点估计仅供参考，不构成 §7 达标/不达标结论"
        )
    return "\n".join(lines)


def report_to_dict(report: CitationSupportReport) -> dict[str, Any]:
    """稳定的 JSON 结构；零标注时 ``support_rate`` 等为 ``None``。"""
    return {
        "status": report.status,
        "verdict": report.verdict,
        "total_rows": report.total_rows,
        "citation_rows": report.citation_rows,
        "no_evidence_rows": report.no_evidence_rows,
        "labeled": report.labeled,
        "unlabeled": report.unlabeled,
        "supported": report.supported,
        "not_supported": report.not_supported,
        "unknown": report.unknown,
        "ignored_no_citation_labeled": report.ignored_no_citation_labeled,
        "support_rate": report.support_rate,
        "unknown_share": report.unknown_share,
        "wilson_95_ci": list(report.wilson_95_ci) if report.wilson_95_ci else None,
        "threshold": report.threshold,
        "min_labeled": report.min_labeled,
        "per_scenario": [
            {
                "scenario": scenario.scenario,
                "rows": scenario.rows,
                "labeled": scenario.labeled,
                "supported": scenario.supported,
                "not_supported": scenario.not_supported,
                "unknown": scenario.unknown,
                "support_rate": scenario.support_rate,
                "unknown_share": scenario.unknown_share,
                "wilson_95_ci": (
                    list(scenario.wilson_95_ci) if scenario.wilson_95_ci else None
                ),
            }
            for scenario in report.per_scenario
        ],
    }
