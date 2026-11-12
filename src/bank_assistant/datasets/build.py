"""M6 合成数据集构建、去重分组、拆分与冻结（T26，#27）。

设计口径（对应计划 M6-01/M6-02/M6-03 与契约 §6）：

- **确定性**：不使用 ``random``，所有排序/分桶都用 sha256 的稳定整数；同一种子
  在任意机器、任意时间构建出相同行、相同哈希。``created_at`` 是唯一的时间戳，
  不参与数据哈希，因此“同一份数据”的哈希不会被构建时间改变。
- **规模**：每场景 110 条检索查询 + 62 段质检会话（合计 516），检索 ≥300、
  质检 ≥180，三场景均衡；每场景困难样本 48 条（27.9%）。
- **分组**：检索按 ``知识来源 + 意图``，质检按 ``模板家族 + 意图``；同源改写
  永远同组，拆分**只按组**，组不跨 train/dev/test。
- **冻结**：test 文件与 manifest 绑定 sha256；已存在且数据哈希不同就拒绝覆盖，
  必须提升 ``dataset_version``。``--verify`` 可复算所有文件哈希。
- **标注**：全部标签由合成模板按构造产生，manifest 如实记录为
  ``developer_single_annotator``、``double_annotated_pct: 0``，不声称人工/业务
  金标；``self_consistency`` 明确标注“不是标注者间一致性”。

本模块不连接数据库、不调用模型、不访问网络。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from bank_assistant.datasets.catalog import (
    FRAMES,
    HARD_CATEGORIES,
    SCENARIO_SPECS,
    VARIANT_TAILS,
    HardQuery,
    InspectionTemplate,
    LabelSpec,
    ScenarioSpec,
)
from bank_assistant.datasets.corpus import (
    CORPUS_AS_OF,
    REPO_ROOT,
    Corpus,
    load_corpus,
)
from bank_assistant.datasets.selfcheck import check_self_consistency

__all__ = [
    "DATASET_ID",
    "DEFAULT_SEED",
    "DatasetBuild",
    "DatasetBuildError",
    "FrozenSplitError",
    "HARD_CATEGORIES",
    "SCENARIOS",
    "SPLITS",
    "build_dataset",
    "verify_dataset",
    "write_committed_sample",
    "write_dataset",
]

DATASET_ID = "bank-cs-m6-synthetic"
DEFAULT_SEED = 42
SPLITS: tuple[str, ...] = ("train", "dev", "test")
SCENARIOS = SCENARIO_SPECS

# 每个场景的规模：80 正常 + 30 困难检索；44 正常 + 18 困难质检。
RETRIEVAL_VARIANTS = 4
INSPECTION_VARIANTS = 4
HARD_RETRIEVAL_VARIANTS = 5
HARD_INSPECTION_VARIANTS = 3

_UUID_NAMESPACE = f"bank-cs-m6/{DATASET_ID}"


class DatasetBuildError(ValueError):
    """数据集构建违反不变量（重复样本、标签与证据不符等）。"""


class FrozenSplitError(RuntimeError):
    """已冻结的数据集与本次构建不一致；必须提升 dataset_version。"""


@dataclass(frozen=True, slots=True)
class Item:
    item_id: str
    task: str
    scenario: str
    business_type: str
    group_id: str
    intent_cluster: str
    template_family: str
    hard_categories: tuple[str, ...]
    query: str | None
    conversation: dict[str, Any] | None
    labels: dict[str, Any]
    generation: dict[str, Any]

    def to_record(self, *, dataset_id: str, dataset_version: str) -> dict[str, Any]:
        record: dict[str, Any] = {
            "item_id": self.item_id,
            "task": self.task,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "scenario": self.scenario,
            "business_type": self.business_type,
            "group_id": self.group_id,
            "intent_cluster": self.intent_cluster,
            "template_family": self.template_family,
            "hard_categories": list(self.hard_categories),
            "synthetic": True,
            "label_origin": "synthetic_by_construction",
        }
        if self.task == "retrieval":
            record["query"] = self.query
            record["conversation"] = None
        else:
            record["query"] = None
            record["conversation"] = self.conversation
        record["labels"] = self.labels
        record["generation"] = self.generation
        return record


@dataclass(slots=True)
class Group:
    group_id: str
    task: str
    scenario: str
    template_family: str
    intent_cluster: str
    knowledge_sources: tuple[str, ...]
    hard_categories: tuple[str, ...]
    item_ids: list[str] = field(default_factory=list)
    split: str = ""

    @property
    def size(self) -> int:
        return len(self.item_ids)

    @property
    def is_hard(self) -> bool:
        return bool(self.hard_categories)

    def to_record(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "task": self.task,
            "scenario": self.scenario,
            "split": self.split,
            "size": self.size,
            "knowledge_sources": list(self.knowledge_sources),
            "template_family": self.template_family,
            "intent_cluster": self.intent_cluster,
            "hard_categories": list(self.hard_categories),
        }


@dataclass(frozen=True, slots=True)
class DatasetBuild:
    items: tuple[Item, ...]
    groups: tuple[Group, ...]
    manifest: dict[str, Any]
    file_bytes: Mapping[str, bytes]

    def records(self, split: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.file_bytes[f"{split}.jsonl"].splitlines()]

    def sample_records(self, split: str, per_split: int = 5) -> list[dict[str, Any]]:
        records = self.records(split)
        if per_split <= 0:
            raise ValueError("per_split 必须为正整数")
        if per_split >= len(records):
            return records
        if per_split == 1:
            return [records[0]]
        indexes = sorted(
            {round(index * (len(records) - 1) / (per_split - 1)) for index in range(per_split)}
        )
        return [records[index] for index in indexes]


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def _stable_int(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _turn_id(item_id: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_UUID_NAMESPACE}/{item_id}/turn/{index}"))


def _conversation_id(item_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{_UUID_NAMESPACE}/{item_id}/conversation"))


def _retrieval_labels(
    corpus: Corpus, doc_ids: Sequence[str], *, answerable: bool, reason: str | None,
    excluded_doc_ids: Sequence[str] = (),
) -> dict[str, Any]:
    chunk_spans: list[dict[str, Any]] = []
    relevant_chunk_ids: list[str] = []
    for doc_id in excluded_doc_ids:
        entry = corpus.entry(doc_id)
        if entry.is_active():
            raise DatasetBuildError(
                f"{doc_id} 在 {CORPUS_AS_OF.isoformat()} 仍然有效，不能标成过期知识"
            )
    for doc_id in doc_ids:
        entry = corpus.entry(doc_id)
        if not entry.is_active():
            raise DatasetBuildError(f"{doc_id} 在 {CORPUS_AS_OF.isoformat()} 不是有效知识")
        for chunk in entry.chunks():
            chunk_spans.append(chunk.to_record())
            relevant_chunk_ids.append(chunk.chunk_id)
    if answerable and not relevant_chunk_ids:
        raise DatasetBuildError(f"有答案样本缺少分块标签：{doc_ids}")
    return {
        "relevant_doc_ids": list(doc_ids),
        "relevant_chunk_ids": relevant_chunk_ids,
        "chunk_spans": chunk_spans,
        "answerable": answerable,
        "excluded_doc_ids": list(excluded_doc_ids),
        "unanswerable_reason": None if answerable else (reason or "no_answer"),
    }


def _generate_retrieval(corpus: Corpus, spec: ScenarioSpec) -> list[Item]:
    items: list[Item] = []
    for intent_index, intent in enumerate(spec.intents):
        entry = corpus.entry(intent.doc_id)
        if entry.business_type != spec.business_type or not entry.is_active():
            raise DatasetBuildError(
                f"{intent.doc_id} 不是 {spec.business_type} 的有效知识，不能作为检索来源"
            )
        labels = _retrieval_labels(corpus, (intent.doc_id,), answerable=True, reason=None)
        group_id = f"ret:{spec.key}:{intent.doc_id}:{intent.slug}"
        for variant in range(RETRIEVAL_VARIANTS):
            query = FRAMES[(intent_index + variant) % len(FRAMES)].format(phrase=intent.phrase)
            item_id = f"rq-{spec.key}-{intent_index + 1:02d}-{variant + 1:02d}"
            items.append(
                Item(
                    item_id=item_id,
                    task="retrieval",
                    scenario=spec.key,
                    business_type=spec.business_type,
                    group_id=group_id,
                    intent_cluster=intent.slug,
                    template_family="retrieval_paraphrase",
                    hard_categories=(),
                    query=query,
                    conversation=None,
                    labels=labels,
                    generation={
                        "seed": None,  # 在 build_dataset 里统一回填
                        "kind": "retrieval_paraphrase",
                        "intent": intent.slug,
                        "variant": variant,
                    },
                )
            )

    for category_index, category in enumerate(HARD_CATEGORIES):
        queries = spec.hard_queries[category]
        if len(queries) != HARD_RETRIEVAL_VARIANTS:
            raise DatasetBuildError(
                f"{spec.key}/{category} 困难检索样本应为 {HARD_RETRIEVAL_VARIANTS} 条"
            )
        for index, hard in enumerate(queries):
            if hard.doc_ids:
                source = hard.doc_ids[0]
            elif hard.excluded_doc_ids:
                source = hard.excluded_doc_ids[0]
            else:
                source = "none"
            answerable = not hard.excluded_doc_ids and bool(hard.doc_ids)
            reason = hard.reason
            if not answerable and reason is None:
                # 无答案型必须区分“语料里没有”与“问题本身信息不足”。
                reason = "insufficient_info" if category == "insufficient_info" else "no_answer"
            labels = _retrieval_labels(
                corpus,
                hard.doc_ids,
                answerable=answerable,
                reason=reason,
                excluded_doc_ids=hard.excluded_doc_ids,
            )
            item_id = f"rq-{spec.key}-h{category_index + 1:02d}-{index + 1:02d}"
            items.append(
                Item(
                    item_id=item_id,
                    task="retrieval",
                    scenario=spec.key,
                    business_type=spec.business_type,
                    group_id=f"ret:{spec.key}:{source}:hard_{category}",
                    intent_cluster=f"hard_{category}",
                    template_family="retrieval_hard",
                    hard_categories=(category,),
                    query=hard.query,
                    conversation=None,
                    labels=labels,
                    generation={
                        "seed": None,
                        "kind": "retrieval_hard",
                        "hard_category": category,
                        "variant": index,
                    },
                )
            )
    return items


def _rule_label(
    corpus: Corpus,
    spec: ScenarioSpec,
    template: InspectionTemplate,
    turns: Sequence[tuple[str, str]],
    turn_ids: Sequence[str],
    rule_code: str,
) -> dict[str, Any]:
    if rule_code not in template.labels:
        raise DatasetBuildError(f"模板 {template.slug} 缺少规则 {rule_code} 的标签")
    label: LabelSpec = template.labels[rule_code]
    evidence: list[dict[str, Any]] = []
    if label.evidence is not None:
        turn_index, quote = label.evidence
        if turn_index >= len(turns):
            raise DatasetBuildError(f"模板 {template.slug} 的证据轮次越界")
        speaker, content = turns[turn_index]
        position = content.find(quote)
        if position < 0:
            raise DatasetBuildError(
                f"模板 {template.slug} 的证据 {quote!r} 不在轮次原文中"
            )
        if label.verdict == "hit" and speaker != "坐席":
            raise DatasetBuildError(f"模板 {template.slug} 的命中证据必须来自坐席发言")
        evidence.append(
            {
                "turn_id": turn_ids[turn_index],
                "start": position,
                "end": position + len(quote),
                "quote": quote,
            }
        )
    if label.verdict == "hit" and not evidence:
        raise DatasetBuildError(f"模板 {template.slug} 的命中标签缺少证据")
    return {
        "verdict": label.verdict,
        "rationale": label.rationale,
        "evidence": evidence,
    }


def _generate_inspection(corpus: Corpus, spec: ScenarioSpec) -> list[Item]:
    items: list[Item] = []
    families: list[tuple[InspectionTemplate, int, bool]] = [
        (template, INSPECTION_VARIANTS, False) for template in spec.normal_templates
    ] + [
        (template, HARD_INSPECTION_VARIANTS, True) for template in spec.hard_templates
    ]
    for family_index, (template, variants, is_hard) in enumerate(families, start=1):
        for variant in range(variants):
            if variant >= len(VARIANT_TAILS):
                raise DatasetBuildError("变体数量超过可用收尾措辞")
            item_id = f"ins-{spec.key}-{family_index:02d}-{variant + 1:02d}"
            turns = list(template.turns) + [("客户", VARIANT_TAILS[variant])]
            turn_ids = [_turn_id(item_id, index) for index in range(len(turns))]
            conversation = {
                "conversation_id": _conversation_id(item_id),
                "business_type": spec.business_type,
                "scenario": spec.key,
                "channel": "phone",
                "turns": [
                    {
                        "turn_id": turn_ids[index],
                        "ordinal": index + 1,
                        "speaker": speaker,
                        "content": content,
                    }
                    for index, (speaker, content) in enumerate(turns)
                ],
            }
            labels: dict[str, Any] = {}
            for rule_code in spec.rule_codes:
                labels[rule_code] = _rule_label(
                    corpus, spec, template, turns, turn_ids, rule_code
                )
            for rule in corpus.rules:
                if rule.rule_code in labels:
                    continue
                labels[rule.rule_code] = {
                    "verdict": "not_applicable",
                    "rationale": (
                        f"会话业务类型为 {spec.business_type}，规则 {rule.rule_code} 的"
                        f"适用范围是 {'/'.join(rule.applicability.get('business_types', []))}，"
                        "不满足业务范围条件。"
                    ),
                    "evidence": [],
                }
            items.append(
                Item(
                    item_id=item_id,
                    task="inspection",
                    scenario=spec.key,
                    business_type=spec.business_type,
                    group_id=f"ins:{spec.key}:{template.slug}:{template.intent_cluster}",
                    intent_cluster=template.intent_cluster,
                    template_family=template.slug,
                    hard_categories=tuple(template.hard_categories),
                    query=None,
                    conversation=conversation,
                    labels={"per_rule": dict(sorted(labels.items()))},
                    generation={
                        "seed": None,
                        "kind": "inspection_template",
                        "template_family": template.slug,
                        "variant": variant,
                        "hard_category": (
                            template.hard_categories[0] if template.hard_categories else None
                        ),
                    },
                )
            )
    return items


def _deduplicate(items: Iterable[Item]) -> list[Item]:
    seen_items: set[str] = set()
    seen_queries: dict[str, str] = {}
    seen_conversations: dict[tuple[str, ...], str] = {}
    for item in items:
        if item.item_id in seen_items:
            raise DatasetBuildError(f"重复 item_id：{item.item_id}")
        seen_items.add(item.item_id)
        if item.query is not None:
            if item.query in seen_queries:
                raise DatasetBuildError(
                    f"重复查询：{item.query!r}（{seen_queries[item.query]} 与 {item.item_id}）"
                )
            seen_queries[item.query] = item.item_id
        if item.conversation is not None:
            signature = tuple(
                turn["content"] for turn in item.conversation["turns"]
            )
            if signature in seen_conversations:
                raise DatasetBuildError(
                    "重复会话："
                    f"{seen_conversations[signature]} 与 {item.item_id}"
                )
            seen_conversations[signature] = item.item_id
    return list(items)


def _build_groups(items: Sequence[Item]) -> list[Group]:
    groups: dict[str, Group] = {}
    for item in items:
        group = groups.get(item.group_id)
        if group is None:
            group = Group(
                group_id=item.group_id,
                task=item.task,
                scenario=item.scenario,
                template_family=item.template_family,
                intent_cluster=item.intent_cluster,
                knowledge_sources=(
                    (item.labels["relevant_doc_ids"] or item.labels["excluded_doc_ids"])
                    if item.task == "retrieval"
                    else ()
                ),
                hard_categories=item.hard_categories,
            )
            groups[item.group_id] = group
        else:
            if group.task != item.task or group.scenario != item.scenario:
                raise DatasetBuildError(f"同组跨任务/场景：{item.group_id}")
        group.item_ids.append(item.item_id)
    return [groups[key] for key in sorted(groups)]


def _split_targets(total: int) -> dict[str, int]:
    train = int(round(total * 0.6))
    dev = int(round(total * 0.2))
    return {"train": train, "dev": dev, "test": total - train - dev}


def _assign_splits(groups: Sequence[Group], *, seed: int) -> None:
    """按（场景 × 正常/困难）分层、只按组做 60/20/20 贪心分配。

    先按稳定哈希排序组，再逐个放进“距离目标缺口最大”的 split；同缺口时优先
    train。组从不拆开，因此同一模板家族/同源改写不会跨 split。
    """
    for scenario in SCENARIOS:
        for stratum in (False, True):
            subset = [
                group
                for group in groups
                if group.scenario == scenario and group.is_hard == stratum
            ]
            if not subset:
                continue
            ordered = sorted(
                subset, key=lambda group: _stable_int(seed, "split", group.group_id)
            )
            targets = _split_targets(sum(group.size for group in ordered))
            assigned = {split: 0 for split in SPLITS}
            for group in ordered:
                split = max(
                    SPLITS,
                    key=lambda candidate: (
                        targets[candidate] - assigned[candidate],
                        -SPLITS.index(candidate),
                    ),
                )
                group.split = split
                assigned[split] += group.size


# ---------------------------------------------------------------------------
# manifest 与哈希
# ---------------------------------------------------------------------------


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


# 数据哈希只覆盖“数据 + 标签 + 拆分 + 标注口径”，不覆盖构建时间与代码检出状态：
# 否则同一份数据在 Mac 的脏 worktree 与 Ubuntu 的干净 checkout 上会得到不同哈希，
# 跨机器复现就失去意义。代码 SHA 仍记录在 manifest，供审计与运行 manifest 引用。
_DATASET_HASH_EXCLUDED: frozenset[str] = frozenset(
    {"created_at", "dataset_manifest_sha256", "code_sha", "worktree_clean"}
)


def _dataset_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in _DATASET_HASH_EXCLUDED
    }


def _split_hash(manifest: Mapping[str, Any], groups: Sequence[Mapping[str, Any]]) -> str:
    projection = {
        "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"],
        "seed": manifest["seed"],
        "splits": {
            split: {
                "groups": sorted(
                    group["group_id"] for group in groups if group["split"] == split
                ),
                "items": manifest["counts"]["by_split"][split],
            }
            for split in SPLITS
        },
    }
    return _sha256_bytes(_canonical_bytes(projection))


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _counts(
    items: Sequence[Item], groups: Sequence[Group]
) -> dict[str, Any]:
    group_split = {group.group_id: group.split for group in groups}
    item_split = {item.item_id: group_split[item.group_id] for item in items}
    by_task = Counter(item.task for item in items)
    by_split = Counter(item_split.values())
    by_scenario = Counter(item.scenario for item in items)
    by_scenario_split = {
        scenario: {
            split: sum(
                1
                for item in items
                if item.scenario == scenario and item_split[item.item_id] == split
            )
            for split in SPLITS
        }
        for scenario in SCENARIOS
    }
    by_task_split = {
        task: {
            split: sum(
                1
                for item in items
                if item.task == task and item_split[item.item_id] == split
            )
            for split in SPLITS
        }
        for task in ("retrieval", "inspection")
    }
    hard_samples = {
        scenario: _ratio(
            sum(1 for item in items if item.scenario == scenario and item.hard_categories),
            sum(1 for item in items if item.scenario == scenario),
        )
        for scenario in SCENARIOS
    }
    hard_by_scenario_split = {
        scenario: {
            split: sum(
                1
                for item in items
                if item.scenario == scenario
                and item.hard_categories
                and item_split[item.item_id] == split
            )
            for split in SPLITS
        }
        for scenario in SCENARIOS
    }
    hard_category_counts = {
        scenario: {
            category: sum(
                1
                for item in items
                if item.scenario == scenario and category in item.hard_categories
            )
            for category in HARD_CATEGORIES
        }
        for scenario in SCENARIOS
    }
    return {
        "total": len(items),
        "by_task": dict(sorted(by_task.items())),
        "by_split": {split: by_split.get(split, 0) for split in SPLITS},
        "by_task_split": by_task_split,
        "by_scenario": {scenario: by_scenario.get(scenario, 0) for scenario in SCENARIOS},
        "by_scenario_split": by_scenario_split,
        "hard_samples": hard_samples,
        "hard_by_scenario_split": hard_by_scenario_split,
        "hard_category_counts": hard_category_counts,
        "group_count": len(groups),
    }


def _ratio(count: int, total: int) -> dict[str, Any]:
    return {
        "count": count,
        "total": total,
        "pct": round(100.0 * count / total, 2) if total else 0.0,
    }


def _annotation() -> dict[str, Any]:
    return {
        "method": "developer_single_annotator",
        "double_annotated_pct": 0,
        "annotator_count": 1,
        "human_gold": False,
        "business_gold": False,
        "label_origin": "synthetic_by_construction",
        "reviewed_sample_pct": 10,
        "note": (
            "数据与标签全部由确定性合成模板产生；唯一开发者做过抽样复核，"
            "但这不是人工金标或业务金标，也没有第二标注人，不能声称双人一致性。"
        ),
    }


def _freeze_block(groups: Sequence[Group], file_bytes: Mapping[str, bytes]) -> dict[str, Any]:
    return {
        "frozen": True,
        "test_split_sha256": _sha256_bytes(file_bytes["test.jsonl"]),
        "test_group_ids": sorted(group.group_id for group in groups if group.split == "test"),
        "test_items": sum(1 for group in groups if group.split == "test" for _ in group.item_ids),
        "rule": "同一 dataset_version 下 test 不可改写；变更数据必须提升版本。",
    }


def _created_at(payload: datetime | None) -> datetime:
    if payload is None:
        return datetime.now(UTC)
    if payload.tzinfo is None:
        return payload.replace(tzinfo=UTC)
    return payload.astimezone(UTC)


def build_dataset(
    *,
    seed: int = DEFAULT_SEED,
    dataset_version: str = "v1",
    dataset_id: str = DATASET_ID,
    created_at: datetime | None = None,
    code_sha: str | None = None,
) -> DatasetBuild:
    """构建内存数据集；同参数必须得到逐字节相同的结果。"""
    if not dataset_version.strip():
        raise DatasetBuildError("dataset_version 不能为空")
    corpus = load_corpus()
    created = _created_at(created_at)

    items: list[Item] = []
    for spec in SCENARIO_SPECS.values():
        items.extend(_generate_retrieval(corpus, spec))
        items.extend(_generate_inspection(corpus, spec))
    items = [
        _with_seed(item, seed) for item in _deduplicate(items)
    ]
    groups = _build_groups(items)
    _assign_splits(groups, seed=seed)

    ordered_items = sorted(
        items,
        key=lambda item: (
            list(SCENARIOS).index(item.scenario),
            0 if item.task == "retrieval" else 1,
            item.item_id,
        ),
    )
    group_split = {group.group_id: group.split for group in groups}
    file_bytes: dict[str, bytes] = {}
    files: list[dict[str, Any]] = []
    for split in SPLITS:
        rows = [
            item.to_record(dataset_id=dataset_id, dataset_version=dataset_version)
            for item in ordered_items
            if group_split[item.group_id] == split
        ]
        payload = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        ).encode("utf-8")
        file_bytes[f"{split}.jsonl"] = payload
        files.append(
            {
                "path": f"{split}.jsonl",
                "split": split,
                "rows": len(rows),
                "sha256": _sha256_bytes(payload),
            }
        )

    counts = _counts(items, groups)
    split_groups = [group.to_record() for group in groups]
    manifest: dict[str, Any] = {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "mode": "synthetic_offline",
        "synthetic": True,
        "contains_real_customer_data": False,
        "created_at": created.isoformat(),
        "seed": seed,
        "code_sha": code_sha or _git("rev-parse", "HEAD") or "unknown",
        "worktree_clean": _git("status", "--porcelain", "--untracked-files=no") == "",
        "knowledge_snapshot": corpus.knowledge_snapshot(),
        "rule_snapshot": corpus.rule_snapshot(),
        "knowledge_as_of": CORPUS_AS_OF.isoformat(),
        "rule_codes": sorted(rule.rule_code for rule in corpus.rules),
        "scenario_labels": {
            key: {"label": spec.label, "business_type": spec.business_type}
            for key, spec in SCENARIO_SPECS.items()
        },
        "counts": counts,
        "groups": split_groups,
        "files": files,
        "split_manifest_sha256": _split_hash({"counts": counts, "dataset_id": dataset_id,
                                               "dataset_version": dataset_version,
                                               "seed": seed}, split_groups),
        "annotation": _annotation(),
        "self_consistency": check_self_consistency(items, corpus=corpus, seed=seed),
        "frozen": _freeze_block(groups, file_bytes),
    }
    manifest["dataset_manifest_sha256"] = _sha256_bytes(
        _canonical_bytes(_dataset_projection(manifest))
    )
    return DatasetBuild(
        items=tuple(ordered_items),
        groups=tuple(groups),
        manifest=manifest,
        file_bytes=file_bytes,
    )


def _with_seed(item: Item, seed: int) -> Item:
    generation = dict(item.generation)
    generation["seed"] = seed
    return Item(
        item_id=item.item_id,
        task=item.task,
        scenario=item.scenario,
        business_type=item.business_type,
        group_id=item.group_id,
        intent_cluster=item.intent_cluster,
        template_family=item.template_family,
        hard_categories=item.hard_categories,
        query=item.query,
        conversation=item.conversation,
        labels=item.labels,
        generation=generation,
    )


# ---------------------------------------------------------------------------
# 落盘、冻结与校验
# ---------------------------------------------------------------------------


def _verify_files(
    manifest: Mapping[str, Any], directory: Path
) -> list[str]:
    errors: list[str] = []
    for entry in manifest["files"]:
        path = directory / entry["path"]
        if not path.exists():
            errors.append(f"缺少文件：{entry['path']}")
            continue
        payload = path.read_bytes()
        actual = _sha256_bytes(payload)
        if actual != entry["sha256"]:
            errors.append(
                f"{entry['path']} sha256 不匹配：manifest={entry['sha256']} 实际={actual}"
            )
        rows = len(payload.splitlines())
        if rows != entry["rows"]:
            errors.append(
                f"{entry['path']} 行数不匹配：manifest={entry['rows']} 实际={rows}"
            )
    test_file = directory / "test.jsonl"
    if test_file.exists():
        frozen_sha = manifest["frozen"]["test_split_sha256"]
        if _sha256_bytes(test_file.read_bytes()) != frozen_sha:
            errors.append("test.jsonl 与冻结哈希不一致")
    return errors


def write_dataset(build: DatasetBuild, directory: str | Path) -> dict[str, Any]:
    """写入拆分与 manifest；已冻结且数据不同则显式失败。"""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"

    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text("utf-8"))
        if existing.get("dataset_manifest_sha256") != build.manifest["dataset_manifest_sha256"]:
            raise FrozenSplitError(
                f"数据集目录已冻结，内容与本次构建不同：{out}；"
                f"请提升 dataset_version（当前 {build.manifest['dataset_version']}）后重试。"
            )
        errors = _verify_files(existing, out)
        if errors:
            raise FrozenSplitError(
                "已冻结数据集文件被改动，拒绝覆盖：" + "；".join(errors)
            )
        return existing

    test_path = out / "test.jsonl"
    if test_path.exists() and test_path.read_bytes() != build.file_bytes["test.jsonl"]:
        raise FrozenSplitError(
            f"test.jsonl 已存在且内容不同：{test_path}；不允许静默覆盖冻结测试集。"
        )

    for split in SPLITS:
        payload = build.file_bytes[f"{split}.jsonl"]
        (out / f"{split}.jsonl").write_bytes(payload)
    manifest_path.write_text(
        json.dumps(build.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return build.manifest


def verify_dataset(directory: str | Path) -> dict[str, Any]:
    """复算 manifest 与文件哈希；返回结构化校验报告。"""
    out = Path(directory)
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        return {
            "ok": False,
            "errors": [f"找不到 manifest.json：{out}"],
            "dataset_manifest_sha256": None,
            "files_checked": 0,
        }
    manifest = json.loads(manifest_path.read_text("utf-8"))
    errors = _verify_files(manifest, out)
    recomputed = _sha256_bytes(_canonical_bytes(_dataset_projection(manifest)))
    if recomputed != manifest.get("dataset_manifest_sha256"):
        errors.append(
            "dataset_manifest_sha256 无法复算："
            f"manifest={manifest.get('dataset_manifest_sha256')} 复算={recomputed}"
        )
    split_hash = _split_hash(manifest, manifest["groups"])
    if split_hash != manifest.get("split_manifest_sha256"):
        errors.append(
            "split_manifest_sha256 无法复算："
            f"manifest={manifest.get('split_manifest_sha256')} 复算={split_hash}"
        )
    return {
        "ok": not errors,
        "errors": errors,
        "dataset_manifest_sha256": manifest.get("dataset_manifest_sha256"),
        "split_manifest_sha256": manifest.get("split_manifest_sha256"),
        "files_checked": len(manifest.get("files", [])),
    }


def write_committed_sample(
    build: DatasetBuild, committed_dir: str | Path, *, per_split: int = 5
) -> dict[str, str]:
    """把 manifest 与每个 split 的少量样本写到可提交的小目录。"""
    base = (
        Path(committed_dir)
        / build.manifest["dataset_id"]
        / build.manifest["dataset_version"]
    )
    samples = base / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(
        json.dumps(build.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    written = {"manifest": str(base / "manifest.json")}
    for split in SPLITS:
        rows = build.sample_records(split, per_split)
        path = samples / f"{split}.sample.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        written[f"{split}_sample"] = str(path)
    return written
