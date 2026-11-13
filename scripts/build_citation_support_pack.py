#!/usr/bin/env python3
"""生成引用支持度人工标注包（T42，#42）。

离线、确定性，不连接应用数据库、不调用模型 API、不联网：

- **live 来源**：读取 ``data/eval/citation-support/live-suggestions.json``（T37
  生产端到端真实 DeepSeek + BGE 的两条候选答复，共 5 条引用）；
- **replay 来源**：用冻结 test/dev 检索样本 + 仓库内演示语料，跑
  ``RetrievalHarness``（replay 词法 embedding）与 ``DemoReplayResponder``
  （显式 replay 替身），取每条答复的第 1 条引用；replay 只验证包结构，不是
  模型质量证据；
- **无证据对照**：取 no_answer / insufficient_info 样本，以空知识调用同一
  replay 替身，确认返回 ``needs_human`` 且不产生引用；这些行不进入支持率分母。

输出：

- ``docs/eval/citation-support-pack.md``（人读）
- ``labels/citation-support.csv``（label/note 留空，由负责人填写）

生成器**不写任何标签**，也不调用任何自动判定。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

# 这些 import 会触发 settings；显式给离线测试值，绝不读取 .env 或应用数据库。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MODEL_MODE", "replay")
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "citation-pack-offline")

from bank_assistant.assistants.candidate_answer import CandidateAnswerPayload
from bank_assistant.datasets.build import build_dataset
from bank_assistant.datasets.corpus import load_corpus
from bank_assistant.demo.replay import DemoReplayResponder
from bank_assistant.evaluation.embedding import ReplayLexicalEmbedding
from bank_assistant.evaluation.retrieval import (
    RetrievalHarness,
    RetrievalItem,
    parse_retrieval_items,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = REPO_ROOT / "docs" / "eval" / "citation-support-pack.md"
DEFAULT_CSV = REPO_ROOT / "labels" / "citation-support.csv"
DEFAULT_LIVE = REPO_ROOT / "data" / "eval" / "citation-support" / "live-suggestions.json"

CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "scenario",
    "query",
    "answer_excerpt",
    "citation_quote",
    "knowledge_title",
    "label",
    "note",
)
SCENARIO_ORDER: tuple[str, ...] = (
    "lost_card",
    "credit_card_fee",
    "complaint",
)
SCENARIO_LABELS: dict[str, str] = {
    "lost_card": "挂失咨询",
    "credit_card_fee": "信用卡费用",
    "complaint": "投诉受理",
}
KIND_ORDER: dict[str, int] = {"live": 0, "replay": 1, "no_evidence": 2}

# 目标：test 全部可答复样本（35 条）+ dev 均衡抽样 27 条 + live 5 条 = 67 条引用。
DEV_TARGET = {"complaint": 10, "credit_card_fee": 8, "lost_card": 9}

# 无证据/证据不足对照：5 条 dev insufficient_info + 2 条 test no_answer +
# 1 条 dev no_answer（fee）+ 1 条 test complaint 低相似度样本。
NO_EVIDENCE_INSUFFICIENT = 5
NO_EVIDENCE_TEST_NO_ANSWER = 2
NO_EVIDENCE_DEV_NO_ANSWER = 1


@dataclass(slots=True)
class PackItem:
    kind: str
    scenario: str
    query: str
    answer_excerpt: str
    citation_quote: str
    title: str
    version_no: int | None
    source: str
    source_detail: str
    label: str = ""
    note: str = ""
    id: str = field(default="")

    @property
    def knowledge_title(self) -> str:
        if not self.title:
            return ""
        if self.version_no is None:
            return self.title
        return f"{self.title}（v{self.version_no}）"


def _load_live(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 live 夹具 {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "citation-support-live-suggestions-v1":
        raise ValueError(f"{path}: schema 必须是 citation-support-live-suggestions-v1")
    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list) or not suggestions:
        raise ValueError(f"{path}: suggestions 必须是非空列表")
    return suggestions


def _evenly_spaced(items: list[RetrievalItem], count: int) -> list[RetrievalItem]:
    if count <= 0:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[0]]
    indexes = sorted({round(index * (len(items) - 1) / (count - 1)) for index in range(count)})
    if len(indexes) != count:
        raise ValueError(f"无法从 {len(items)} 条中均匀抽取 {count} 条")
    return [items[index] for index in indexes]


class ReplayWorld:
    """离线 replay 检索 + 替身答复；不落库、不联网。"""

    def __init__(self) -> None:
        self.corpus = load_corpus()
        self.harness = RetrievalHarness(
            corpus=self.corpus,
            provider=ReplayLexicalEmbedding(),
            top_k=5,
            min_similarity=0.10,
            business_filter="hard",
        )
        self.responder = DemoReplayResponder()
        self._chunk_by_key = {chunk.key: chunk for chunk in self.harness.chunks}
        self._entry_by_doc_id = {entry.doc_id: entry for entry in self.corpus.knowledge}
        self.dataset = build_dataset()

    def run(self, item: RetrievalItem) -> tuple[tuple[Any, ...], bool, dict[str, Any]]:
        hits = self.harness.query(
            item.query,
            business_type=item.business_type,
            require_active=True,
            scenario=item.scenario,
        )
        no_answer = self.harness.is_no_answer(hits, scenario=item.scenario)
        knowledge: list[dict[str, str]] = []
        if not no_answer and item.answerable:
            knowledge = self.knowledge_for(hits)
        payload = self.answer(item.query, item.business_type, knowledge)
        return hits, no_answer, payload

    def knowledge_for(self, hits: tuple[Any, ...]) -> list[dict[str, str]]:
        knowledge: list[dict[str, str]] = []
        for hit in hits:
            chunk = self._chunk_by_key[hit.chunk_key]
            entry = self._entry_by_doc_id[chunk.doc_id]
            knowledge.append(
                {
                    "chunk_id": chunk.key,
                    "version_id": str(
                        uuid5(NAMESPACE_URL, f"citation-pack/{entry.doc_id}/v{entry.version_no}")
                    ),
                    "text": chunk.text,
                    "title": chunk.title,
                }
            )
        return knowledge

    def answer(
        self, query: str, business_type: str, knowledge: list[dict[str, str]]
    ) -> dict[str, Any]:
        user = json.dumps(
            {"query": query, "business_type": business_type, "knowledge": knowledge},
            ensure_ascii=False,
        )
        raw = self.responder(
            [
                {"role": "system", "content": "T42 citation pack offline replay"},
                {"role": "user", "content": user},
            ],
            CandidateAnswerPayload,
        )
        return json.loads(raw)

    def chunk(self, chunk_id: str) -> Any:
        return self._chunk_by_key[chunk_id]

    def entry(self, doc_id: str) -> Any:
        return self._entry_by_doc_id[doc_id]

    def entry_by_title(self, title: str) -> Any:
        matches = [entry for entry in self.corpus.knowledge if entry.title == title]
        if len(matches) != 1:
            raise ValueError(f"知识标题无法唯一匹配（{len(matches)} 条）：{title!r}")
        return matches[0]


def _replay_items(world: ReplayWorld) -> tuple[list[PackItem], list[RetrievalItem]]:
    test_items = parse_retrieval_items(world.dataset.records("test"))
    dev_items = parse_retrieval_items(world.dataset.records("dev"))

    answered_test: list[tuple[RetrievalItem, dict[str, Any]]] = []
    answered_dev: list[tuple[RetrievalItem, dict[str, Any]]] = []
    no_evidence_candidates: list[tuple[str, RetrievalItem, bool, dict[str, Any]]] = []

    for split, items, answered in (
        ("test", test_items, answered_test),
        ("dev", dev_items, answered_dev),
    ):
        for item in items:
            hits, no_answer, payload = world.run(item)
            if payload.get("status") == "answered":
                answered.append((item, payload))
            else:
                no_evidence_candidates.append((split, item, no_answer, payload))

    if len(answered_test) < 30:
        raise ValueError(f"test 可答复样本过少：{len(answered_test)}")

    selected: list[tuple[str, RetrievalItem, dict[str, Any]]] = [
        ("test", item, payload) for item, payload in answered_test
    ]
    for scenario, target in DEV_TARGET.items():
        candidates = [
            (item, payload)
            for item, payload in answered_dev
            if item.scenario == scenario
        ]
        candidates.sort(key=lambda pair: pair[0].item_id)
        chosen = _evenly_spaced([item for item, _ in candidates], target)
        payload_by_id = {item.item_id: payload for item, payload in candidates}
        selected.extend(("dev", item, payload_by_id[item.item_id]) for item in chosen)

    items: list[PackItem] = []
    for split, item, payload in selected:
        citations = payload.get("citations") or []
        if not citations:
            raise ValueError(f"replay 答复没有引用：{item.item_id}")
        citation = citations[0]
        chunk = world.chunk(citation["chunk_id"])
        entry = world.entry(chunk.doc_id)
        excerpt = chunk.text.strip()[:160]
        if excerpt not in payload["answer"]:
            raise ValueError(f"答复节选不在 replay 答复中：{item.item_id}")
        if not str(citation["quote"]).startswith(excerpt):
            raise ValueError(f"引用片段未覆盖答复节选：{item.item_id}")
        items.append(
            PackItem(
                kind="replay",
                scenario=item.scenario,
                query=item.query,
                answer_excerpt=excerpt,
                citation_quote=str(citation["quote"]),
                title=chunk.title,
                version_no=entry.version_no,
                source=f"replay:{split}/{item.item_id}",
                source_detail=(
                    f"{split} split 检索样本 {item.item_id}；"
                    "replay 词法 embedding + DemoReplayResponder 第 1 条引用"
                ),
            )
        )
    return items, no_evidence_candidates


def _live_items(world: ReplayWorld, suggestions: list[dict[str, Any]]) -> list[PackItem]:
    items: list[PackItem] = []
    for suggestion in suggestions:
        query = str(suggestion["query"])
        scenario = str(suggestion["business_type"])
        provenance = str(suggestion.get("provenance", "live/T37"))
        for citation in suggestion.get("citations") or []:
            title = str(citation["title"])
            quote = str(citation["quote"])
            excerpt = str(citation["answer_excerpt"])
            entry = world.entry_by_title(title)
            if quote not in entry.content:
                raise ValueError(f"live 引用不是知识原文子串：{title!r} / {quote[:40]!r}")
            if excerpt and excerpt not in str(suggestion["answer"]):
                raise ValueError(f"live 答复节选不在答复中：{title!r}")
            items.append(
                PackItem(
                    kind="live",
                    scenario=scenario,
                    query=query,
                    answer_excerpt=excerpt,
                    citation_quote=quote,
                    title=title,
                    version_no=entry.version_no,
                    source=f"live:T37/{provenance.split(';')[0].replace('T37 ', '').strip()}",
                    source_detail=provenance,
                )
            )
    return items


def _no_evidence_items(
    world: ReplayWorld,
    candidates: list[tuple[str, RetrievalItem, bool, dict[str, Any]]],
) -> list[PackItem]:
    insufficient = [
        (split, item, no_answer, payload)
        for split, item, no_answer, payload in candidates
        if item.unanswerable_reason == "insufficient_info"
    ][:NO_EVIDENCE_INSUFFICIENT]
    test_no_answer = [
        (split, item, no_answer, payload)
        for split, item, no_answer, payload in candidates
        if split == "test" and item.unanswerable_reason == "no_answer"
    ][:NO_EVIDENCE_TEST_NO_ANSWER]
    dev_no_answer = [
        (split, item, no_answer, payload)
        for split, item, no_answer, payload in candidates
        if split == "dev" and item.unanswerable_reason == "no_answer"
    ][:NO_EVIDENCE_DEV_NO_ANSWER]
    complaint_low = [
        (split, item, no_answer, payload)
        for split, item, no_answer, payload in candidates
        if split == "test" and item.scenario == "complaint"
    ][:1]

    selected = insufficient + test_no_answer + dev_no_answer + complaint_low
    items: list[PackItem] = []
    for split, item, no_answer, payload in selected:
        hits, _, _ = world.run(item)
        max_similarity = max((hit.similarity for hit in hits), default=0.0)
        missing = "；".join(str(value) for value in payload.get("missing_information") or [])
        reason = item.unanswerable_reason or ("no_answer" if no_answer else "insufficient_evidence")
        excerpt = (
            "（系统返回 needs_human："
            + (missing or "本次没有可引用的有效知识")
            + "）"
        )
        items.append(
            PackItem(
                kind="no_evidence",
                scenario=item.scenario,
                query=item.query,
                answer_excerpt=excerpt,
                citation_quote="",
                title="",
                version_no=None,
                source=f"replay-control:{split}/{item.item_id}",
                source_detail=(
                    f"{split} split {item.item_id}；reason={reason}；"
                    f"max_similarity={max_similarity:.3f}；"
                    "以空知识调用 DemoReplayResponder，验证 needs_human/无引用"
                ),
            )
        )
    return items


def _sort_items(items: list[PackItem]) -> list[PackItem]:
    scenario_index = {scenario: index for index, scenario in enumerate(SCENARIO_ORDER)}

    def key(item: PackItem) -> tuple[int, int, str, str]:
        return (
            scenario_index[item.scenario],
            KIND_ORDER[item.kind],
            item.query,
            item.source,
        )

    return sorted(items, key=key)


def _assign_ids(items: list[PackItem]) -> None:
    counters: Counter[str] = Counter()
    for item in items:
        counters[item.kind] += 1
        if item.kind == "live":
            item.id = f"cs-live-{counters[item.kind]:03d}"
        elif item.kind == "replay":
            item.id = f"cs-replay-{counters[item.kind]:03d}"
        else:
            item.id = f"cs-noev-{counters[item.kind]:03d}"


def _md_cell(value: str) -> str:
    text = value.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r\n", "<br>").replace("\n", "<br>")


def _counts(items: list[PackItem]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {
        scenario: {"citations": 0, "no_evidence": 0} for scenario in SCENARIO_ORDER
    }
    for item in items:
        key = "no_evidence" if item.kind == "no_evidence" else "citations"
        counts[item.scenario][key] += 1
    return counts


def _write_csv(path: Path, items: list[PackItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "id": item.id,
                    "scenario": item.scenario,
                    "query": item.query,
                    "answer_excerpt": item.answer_excerpt,
                    "citation_quote": item.citation_quote,
                    "knowledge_title": item.knowledge_title,
                    "label": "",
                    "note": "",
                }
            )


def _has_existing_annotations(path: Path) -> bool:
    """已有 label/note 时拒绝覆盖，避免重生成把负责人标注清空。"""
    if not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if str(row.get("label") or "").strip() or str(row.get("note") or "").strip():
                    return True
    except (OSError, csv.Error) as exc:
        raise ValueError(f"无法读取已有 CSV {path}，拒绝覆盖：{exc}") from exc
    return False


def _write_pack(
    path: Path,
    items: list[PackItem],
    *,
    live_count: int,
    live_suggestions: int,
) -> None:
    counts = _counts(items)
    citation_total = sum(row["citations"] for row in counts.values())
    no_evidence_total = sum(row["no_evidence"] for row in counts.values())
    generated_on = datetime.now(UTC).date().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# 引用支持度人工标注包（T42 / #42）",
        "",
        f"生成日期：{generated_on}　|　状态：**等待负责人标注**",
        "",
        "本文件与 [`labels/citation-support.csv`](../../labels/citation-support.csv) 是"
        "**人工标注材料**，不是支持度报告：CSV 的 `label` / `note` 全部为空，"
        "在负责人填写并运行计分脚本之前，不得给出任何支持率或宣称 §7 达标。",
        "",
        f"本包共 **{citation_total} 条引用** + **{no_evidence_total} 条无证据/证据不足对照**，"
        f"覆盖 {len(SCENARIO_ORDER)} 个场景。计划 §7 要求人工抽检 **≥50 条**；"
        "未标注/未填写的条目不进入计分分母，脚本会报告实际分母。",
        "",
        "## 1. 来源与口径（必须一起读）",
        "",
        "| 来源 | 条数 | 说明 |",
        "|---|---:|---|",
        f"| live（T37 生产端到端） | {live_count} | {live_suggestions} 条真实 DeepSeek + BGE 候选答复；"
        "索引与创建时间见 §7；这是本包唯一的真实模型输出 |",
        f"| replay（test/dev 冻结样本） | {citation_total - live_count} | "
        "`ReplayLexicalEmbedding` + `DemoReplayResponder`（`mode=replay`）；"
        "**抽取式答复，不是模型质量证据**，只用于补足三场景与结构覆盖 |",
        f"| 无证据对照 | {no_evidence_total} | no_answer / insufficient_info 样本以空知识调用同一 replay 替身；"
        "只检查是否拒答/转人工，不参与引用支持率 |",
        "",
        "诚实性边界：",
        "",
        "- live 条目只有 "
        f"{live_count} 条、且都在 `lost_card`；另外两个场景的多数条目来自 replay。"
        "因此**即使负责人把本包全部标成 SUPPORTED，也不能据此宣称 §7 的模型引用支持度达标**。"
        "要用于门槛判定，应在生产上用 ≥50 条 live 候选答复重新抽样。",
        "- replay 答复是“从引用片段抄出节选”，引用支持度会天然偏高；它证明的是"
        "包/计分链路可用，不是模型会引用。",
        "- 本包只判断**引用是否支持答复节选**，不评价答复是否切题、是否正确、是否应拒答；"
        "后者不是本包的标注对象。",
        "- replay 答复可能带 2 条引用；本包每条答复只取**第 1 条引用**与它生成的答复片段，"
        "避免同一答复重复计数，第 2 条引用不在本包内。",
        "- live 答复可能含多条事实；每条引用只与人工挑选的对应答复句配对，"
        "未引用的答复句不在该行的判断范围内（完整答复见 `data/eval/citation-support/live-suggestions.json`）。",
        "",
        "## 2. 计数",
        "",
        "| 场景 | 引用条数 | 无证据/证据不足对照 |",
        "|---|---:|---:|",
    ]
    for scenario in SCENARIO_ORDER:
        row = counts[scenario]
        lines.append(
            f"| `{scenario}`（{SCENARIO_LABELS[scenario]}） | {row['citations']} | {row['no_evidence']} |"
        )
    lines.extend(
        [
            f"| **合计** | **{citation_total}** | **{no_evidence_total}** |",
            "",
            "## 3. 标注规范（负责人填写 CSV 前必读）",
            "",
            "只填写 `label` 与 `note` 两列，**不要改** `id` / `scenario` / `query` / "
            "`answer_excerpt` / `citation_quote` / `knowledge_title`。",
            "",
            "| 标签 | 判定标准 |",
            "|---|---|",
            "| `SUPPORTED` | 引用原文**包含或蕴含**答复节选中使用的关键事实、数字、时限、条件或结论。"
            "允许同义改写，但关键要素必须能在引用中找到依据。 |",
            "| `NOT_SUPPORTED` | 答复节选陈述了引用**没有**支持的事实/数字/条件（包括与引用矛盾、"
            "引用只覆盖复合陈述的一部分、或把“可能”写成“一定”）。 |",
            "| `UNKNOWN` | 引用与答复节选无关、信息不足、或仅凭给出的片段无法判断；"
            "无法判断时**不要猜**，填 `UNKNOWN` 并在 `note` 说明缺什么。 |",
            "",
            "硬性要求：",
            "",
            "1. **禁止预填**：本 CSV 的 `label`/`note` 必须为空；执行模型不得代填、"
            "不得用模型判断冒充人工标注。",
            "2. **只判引用支持**：不判答复是否切题、是否合规、是否应拒答；"
            "答复答非所问但引用确实支持该答复节选时，仍应标 `SUPPORTED`。",
            "3. **不要用“引用合法”冒充“引用支持”**：引用来自授权快照、quote 逐字匹配，"
            "只说明引用合法；是否支持答复是语义判断。",
            "4. **无证据对照行**（`citation_quote` 为空，`id` 以 `cs-noev-` 开头）"
            "不参与支持率；`label` 保持为空。如果系统在应拒答时编造了答复，"
            "请在 `note` 里记录事实，供负责人另案处理，不填 `SUPPORTED`。",
            "5. **逐条独立判断**：不要参考其他行，也不要参考生成器的任何提示；"
            "`note` 可写理由或缺失条件，可为空。",
            "",
            "## 4. 计分命令与输出",
            "",
            "```bash",
            "uv run python scripts/score_citation_support.py \\",
            "  --labels labels/citation-support.csv",
            "```",
            "",
            "脚本只读 CSV：统计已标注引用（分母不含未填写项）、支持率、`UNKNOWN` 比例、"
            "分场景统计、Wilson 95% 区间，并按 ≥0.95 门槛给出 `PASS` / `FAIL` / "
            "`INSUFFICIENT_SAMPLE`。若一条引用都没标，只打印 `WAITING_FOR_HUMAN` 并以 0 退出，"
            "不产生任何分数。",
            "",
            "## 5. 逐条清单",
            "",
            "| id | 场景 | 来源 | 用户问题 | 答复节选 | 引用原文片段 | 知识标题（版本） | 待判断 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in items:
        judge = (
            "对照项：系统是否应拒答/转人工？（label 留空）"
            if item.kind == "no_evidence"
            else "引用是否包含/蕴含答复节选的关键事实？"
        )
        lines.append(
            "| {id} | `{scenario}` | `{source}` | {query} | {answer} | {quote} | {title} | {judge} |".format(
                id=_md_cell(item.id),
                scenario=item.scenario,
                source=_md_cell(item.source),
                query=_md_cell(item.query),
                answer=_md_cell(item.answer_excerpt),
                quote=_md_cell(item.citation_quote) if item.citation_quote else "—",
                title=_md_cell(item.knowledge_title) if item.knowledge_title else "—",
                judge=_md_cell(judge),
            )
        )
    lines.extend(
        [
            "",
            "## 6. 生成与复核",
            "",
            "```bash",
            "uv run python scripts/build_citation_support_pack.py",
            "```",
            "",
            "生成器离线运行：只读仓库内演示语料、冻结数据集构建器与 "
            "`data/eval/citation-support/live-suggestions.json`；不连接数据库、"
            "不调用模型 API、不联网、不写标签。生成后请用 "
            "`uv run python scripts/score_citation_support.py --labels labels/citation-support.csv` "
            "确认输出 `WAITING_FOR_HUMAN`。**CSV 一旦已有 `label`/`note`，生成器默认拒绝覆盖**；"
            "只有显式 `--force` 才会重写并清空标注。",
            "",
            "## 7. 逐条来源详情",
            "",
            "| id | 来源详情 |",
            "|---|---|",
        ]
    )
    for item in items:
        lines.append(f"| {item.id} | {_md_cell(item.source_detail)} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_pack(
    *,
    pack_path: Path = DEFAULT_PACK,
    csv_path: Path = DEFAULT_CSV,
    live_path: Path = DEFAULT_LIVE,
    force: bool = False,
) -> tuple[list[PackItem], dict[str, int]]:
    if not force and _has_existing_annotations(csv_path):
        raise ValueError(
            f"{csv_path} 已有 label/note，拒绝覆盖；如确实要重生成，显式加 --force"
        )
    world = ReplayWorld()
    live_suggestions = _load_live(live_path)
    live_items = _live_items(world, live_suggestions)
    replay_items, candidates = _replay_items(world)
    no_evidence_items = _no_evidence_items(world, candidates)

    items = _sort_items(live_items + replay_items + no_evidence_items)
    _assign_ids(items)
    _write_csv(csv_path, items)
    _write_pack(
        pack_path,
        items,
        live_count=len(live_items),
        live_suggestions=len(live_suggestions),
    )
    counts = _counts(items)
    return items, {
        "citations": sum(row["citations"] for row in counts.values()),
        "no_evidence": sum(row["no_evidence"] for row in counts.values()),
        "live": len(live_items),
        "replay": len(replay_items),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/build_citation_support_pack.py",
        description="离线生成引用支持度人工标注包（Markdown + 空标签 CSV）。",
    )
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK, help="Markdown 输出路径")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="标签 CSV 输出路径")
    parser.add_argument(
        "--live", type=Path, default=DEFAULT_LIVE, help="T37 live 候选答复夹具路径"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使 CSV 已有 label/note 也重写（会清空标注，默认拒绝）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        items, counts = build_pack(
            pack_path=args.pack,
            csv_path=args.csv,
            live_path=args.live,
            force=args.force,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    scenario_counts = Counter(item.scenario for item in items if item.kind != "no_evidence")
    print(
        json.dumps(
            {
                "pack": str(args.pack),
                "csv": str(args.csv),
                "citations": counts["citations"],
                "no_evidence": counts["no_evidence"],
                "by_scenario": {
                    scenario: scenario_counts.get(scenario, 0)
                    for scenario in SCENARIO_ORDER
                },
                "labels": "EMPTY (owner must fill)",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
