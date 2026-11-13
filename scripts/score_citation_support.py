#!/usr/bin/env python3
"""引用支持度计分 CLI（T42，#42）。

用法::

    uv run python scripts/score_citation_support.py \
        --labels labels/citation-support.csv

只读人工填写后的 CSV，不调用模型、不联网、不读数据库。``label`` 允许值：
``SUPPORTED`` / ``NOT_SUPPORTED`` / ``UNKNOWN``；未填写的引用不进入分母。
没有任何已标注引用时只打印 ``WAITING_FOR_HUMAN`` 并以 0 退出，不产生分数。

退出码：0 = 正常（包括等待人工标注、样本不足、未达门槛）；2 = CSV 结构/标签错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bank_assistant.evaluation.citation_support import (
    DEFAULT_MIN_LABELED,
    DEFAULT_THRESHOLD,
    CitationSupportError,
    load_citation_rows,
    render_report,
    report_to_dict,
    score_citation_support,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/score_citation_support.py",
        description=(
            "统计人工标注的引用支持度（仅统计已标注项，报告分母、分场景、"
            "Wilson 95% 区间与 ≥0.95 门槛判定）。"
        ),
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path("labels/citation-support.csv"),
        help="标注 CSV 路径（默认 labels/citation-support.csv）",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"支持率门槛（默认 {DEFAULT_THRESHOLD}）",
    )
    parser.add_argument(
        "--min-labeled",
        type=int,
        default=DEFAULT_MIN_LABELED,
        help=f"计划要求的最少标注条数（默认 {DEFAULT_MIN_LABELED}）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出机器可读 JSON（零标注时 support_rate 为 null）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rows = load_citation_rows(args.labels)
        report = score_citation_support(
            rows, threshold=args.threshold, min_labeled=args.min_labeled
        )
    except (CitationSupportError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, sort_keys=True))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
