"""M6 数据集构建与冻结（T26，#27）的离线验收测试。

这些测试只验证公开 seam：``build_dataset``（内存中确定性构建）、
``write_dataset``/``verify_dataset``（落盘与冻结校验）与 CLI ``--help``。
不连接数据库、不调用模型、不访问网络：

- 规模：检索查询 ≥300、质检会话 ≥180，三场景均衡；
- 困难样本：每个场景 ≥20%，六类齐全（无答案/信息不足/过期知识/否定/引用/歧义）；
- 拆分：按组 60/20/20，组不跨 split，按 item 不重复；
- 冻结：manifest 字段符合契约 §6，文件哈希可复算，同种子两次构建哈希一致；
- 标注：如实记录“开发者单人标注”，不声称人工/业务金标，自一致性不算 IAA。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bank_assistant.datasets.build import (
    DATASET_ID,
    HARD_CATEGORIES,
    SCENARIOS,
    SPLITS,
    DatasetBuild,
    FrozenSplitError,
    build_dataset,
    verify_dataset,
    write_dataset,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXED_CREATED_AT = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def build() -> DatasetBuild:
    return build_dataset(seed=42, created_at=FIXED_CREATED_AT)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _records(build: DatasetBuild, split: str) -> list[dict]:
    return [json.loads(line) for line in build.file_bytes[f"{split}.jsonl"].splitlines()]


def test_retrieval_and_inspection_sizes_meet_plan_targets(build: DatasetBuild) -> None:
    records = [record for split in SPLITS for record in _records(build, split)]
    retrieval = [record for record in records if record["task"] == "retrieval"]
    inspection = [record for record in records if record["task"] == "inspection"]
    assert len(retrieval) >= 300
    assert len(inspection) >= 180
    assert build.manifest["counts"]["total"] == len(records)
    assert build.manifest["counts"]["by_task"]["retrieval"] == len(retrieval)
    assert build.manifest["counts"]["by_task"]["inspection"] == len(inspection)


def test_scenarios_are_balanced(build: DatasetBuild) -> None:
    by_scenario = build.manifest["counts"]["by_scenario"]
    assert set(by_scenario) == set(SCENARIOS)
    counts = list(by_scenario.values())
    # 三场景各约占 1/3：最大与最小相差不超过 5%。
    assert max(counts) - min(counts) <= 0.05 * sum(counts)
    for scenario in SCENARIOS:
        task_counts = {
            task: sum(
                1
                for record in _records(build, "train")
                + _records(build, "dev")
                + _records(build, "test")
                if record["scenario"] == scenario and record["task"] == task
            )
            for task in ("retrieval", "inspection")
        }
        assert task_counts["retrieval"] == 110
        assert task_counts["inspection"] == 62


def test_hard_sample_ratio_at_least_20pct_per_scenario(build: DatasetBuild) -> None:
    hard = build.manifest["counts"]["hard_samples"]
    for scenario in SCENARIOS:
        assert hard[scenario]["pct"] >= 20.0, (scenario, hard[scenario])
        assert hard[scenario]["count"] >= 1
    # 每个场景的每个 split 里困难样本也不低于 20%（分组拆分会天然保持比例）。
    for scenario in SCENARIOS:
        for split in SPLITS:
            total = build.manifest["counts"]["by_scenario_split"][scenario][split]
            count = build.manifest["counts"]["hard_by_scenario_split"][scenario][split]
            assert count / total >= 0.20, (scenario, split, count, total)


def test_all_six_hard_categories_present_per_scenario(build: DatasetBuild) -> None:
    categories = build.manifest["counts"]["hard_category_counts"]
    for scenario in SCENARIOS:
        assert set(categories[scenario]) == set(HARD_CATEGORIES)
        for category, count in categories[scenario].items():
            assert count >= 1, (scenario, category)
    seen = {
        category
        for split in SPLITS
        for record in _records(build, split)
        for category in record["hard_categories"]
    }
    assert seen == set(HARD_CATEGORIES)


def test_every_item_is_marked_synthetic(build: DatasetBuild) -> None:
    for split in SPLITS:
        for record in _records(build, split):
            assert record["synthetic"] is True
            assert record["label_origin"] == "synthetic_by_construction"
            assert record["dataset_id"] == DATASET_ID


def test_retrieval_labels_are_verifiable_doc_and_chunk_ids(build: DatasetBuild) -> None:
    for split in SPLITS:
        for record in _records(build, split):
            if record["task"] != "retrieval":
                continue
            labels = record["labels"]
            if labels["answerable"]:
                assert labels["relevant_doc_ids"]
                assert labels["relevant_chunk_ids"]
                for chunk in labels["chunk_spans"]:
                    assert chunk["chunk_id"].startswith(chunk["doc_id"] + "#")
                    assert 0 <= chunk["start"] < chunk["end"]
                assert labels["unanswerable_reason"] is None
            else:
                assert not labels["relevant_doc_ids"]
                assert labels["unanswerable_reason"] in {
                    "no_answer",
                    "insufficient_info",
                    "expired_knowledge",
                }
                if "insufficient_info" in record["hard_categories"]:
                    assert labels["unanswerable_reason"] == "insufficient_info"
                if "no_answer" in record["hard_categories"]:
                    assert labels["unanswerable_reason"] == "no_answer"


def test_expired_knowledge_queries_exclude_stale_sources(build: DatasetBuild) -> None:
    stale = [
        record
        for split in SPLITS
        for record in _records(build, split)
        if record["task"] == "retrieval"
        and "expired_knowledge" in record["hard_categories"]
    ]
    assert stale
    for record in stale:
        labels = record["labels"]
        assert labels["answerable"] is False
        assert labels["excluded_doc_ids"], record["item_id"]
        assert labels["unanswerable_reason"] == "expired_knowledge"


def test_inspection_labels_cover_every_demo_rule(build: DatasetBuild) -> None:
    rule_codes = set(build.manifest["rule_codes"])
    assert len(rule_codes) == 6
    for split in SPLITS:
        for record in _records(build, split):
            if record["task"] != "inspection":
                continue
            per_rule = record["labels"]["per_rule"]
            assert set(per_rule) == rule_codes
            turns = {turn["turn_id"]: turn["content"] for turn in record["conversation"]["turns"]}
            for rule_code, label in per_rule.items():
                assert label["verdict"] in {
                    "hit",
                    "clear",
                    "insufficient_evidence",
                    "not_applicable",
                }
                assert label["rationale"]
                if label["verdict"] == "hit":
                    assert label["evidence"], rule_code
                for evidence in label["evidence"]:
                    assert evidence["turn_id"] in turns
                    text = turns[evidence["turn_id"]]
                    assert text[evidence["start"] : evidence["end"]] == evidence["quote"]


def test_no_duplicate_queries_conversations_or_ids(build: DatasetBuild) -> None:
    queries: list[str] = []
    conversations: list[tuple[str, ...]] = []
    item_ids: list[str] = []
    for split in SPLITS:
        for record in _records(build, split):
            item_ids.append(record["item_id"])
            if record["task"] == "retrieval":
                queries.append(record["query"])
            else:
                conversations.append(
                    tuple(turn["content"] for turn in record["conversation"]["turns"])
                )
    assert len(item_ids) == len(set(item_ids))
    assert len(queries) == len(set(queries))
    assert len(conversations) == len(set(conversations))


def test_groups_encode_source_template_family_and_intent(build: DatasetBuild) -> None:
    groups = {group["group_id"]: group for group in build.manifest["groups"]}
    assert groups
    for group_id, group in groups.items():
        assert group_id.startswith(("ret:", "ins:"))
        assert group["task"] in {"retrieval", "inspection"}
        assert group["scenario"] in SCENARIOS
        assert group["intent_cluster"] in group_id
        assert group["split"] in SPLITS
        assert group["size"] >= 1
        if group["template_family"] == "retrieval_paraphrase":
            assert group["knowledge_sources"]
            assert group["knowledge_sources"][0] in group_id
        assert group["template_family"]


def test_split_is_by_group_and_splits_never_overlap(build: DatasetBuild) -> None:
    group_split = {group["group_id"]: group["split"] for group in build.manifest["groups"]}
    seen_item_ids: set[str] = set()
    seen_groups: dict[str, str] = {}
    for split in SPLITS:
        for record in _records(build, split):
            assert record["item_id"] not in seen_item_ids
            seen_item_ids.add(record["item_id"])
            assert group_split[record["group_id"]] == split
            previous = seen_groups.setdefault(record["group_id"], split)
            assert previous == split
    # 每个组的所有 item 必须落在同一个 split。
    for split in SPLITS:
        for record in _records(build, split):
            same_group = [
                other
                for other in _records(build, split)
                if other["group_id"] == record["group_id"]
            ]
            assert len(same_group) == group_split_size(build, record["group_id"])


def group_split_size(build: DatasetBuild, group_id: str) -> int:
    return next(
        group["size"] for group in build.manifest["groups"] if group["group_id"] == group_id
    )


def test_split_ratio_is_about_60_20_20(build: DatasetBuild) -> None:
    by_split = build.manifest["counts"]["by_split"]
    total = build.manifest["counts"]["total"]
    assert 0.55 <= by_split["train"] / total <= 0.68
    assert 0.13 <= by_split["dev"] / total <= 0.28
    assert 0.13 <= by_split["test"] / total <= 0.28


def test_same_seed_builds_identical_hashes_and_bytes(build: DatasetBuild) -> None:
    again = build_dataset(seed=42, created_at=FIXED_CREATED_AT)
    assert again.manifest["dataset_manifest_sha256"] == build.manifest["dataset_manifest_sha256"]
    assert again.manifest["split_manifest_sha256"] == build.manifest["split_manifest_sha256"]
    assert again.manifest["frozen"] == build.manifest["frozen"]
    assert again.file_bytes == build.file_bytes
    assert again.manifest == build.manifest


def test_different_seed_moves_groups_without_changing_scale() -> None:
    base = build_dataset(seed=42, created_at=FIXED_CREATED_AT)
    other = build_dataset(seed=7, created_at=FIXED_CREATED_AT)
    assert other.manifest["counts"]["total"] == base.manifest["counts"]["total"]
    assert other.manifest["dataset_manifest_sha256"] != base.manifest[
        "dataset_manifest_sha256"
    ]
    base_groups = {(g["group_id"], g["split"]) for g in base.manifest["groups"]}
    other_groups = {(g["group_id"], g["split"]) for g in other.manifest["groups"]}
    assert base_groups != other_groups


def test_manifest_fields_follow_contract_section_6(build: DatasetBuild) -> None:
    manifest = build.manifest
    required = {
        "dataset_id",
        "dataset_version",
        "dataset_manifest_sha256",
        "split_manifest_sha256",
        "code_sha",
        "worktree_clean",
        "mode",
        "seed",
        "knowledge_snapshot",
        "rule_snapshot",
        "created_at",
        "counts",
        "groups",
        "files",
        "annotation",
        "self_consistency",
        "frozen",
    }
    assert required <= set(manifest)
    assert manifest["dataset_id"] == DATASET_ID
    assert manifest["dataset_version"] == "v1"
    assert manifest["seed"] == 42
    assert manifest["mode"] == "synthetic_offline"
    assert len(manifest["dataset_manifest_sha256"]) == 64
    assert len(manifest["split_manifest_sha256"]) == 64
    assert len(manifest["knowledge_snapshot"]) == 64
    assert len(manifest["rule_snapshot"]) == 64
    assert manifest["created_at"] == FIXED_CREATED_AT.isoformat()
    # 每个文件都要有可复算的 sha256 与行数。
    for entry in manifest["files"]:
        assert entry["path"] in {"train.jsonl", "dev.jsonl", "test.jsonl"}
        assert entry["rows"] == len(build.file_bytes[entry["path"]].splitlines())
        assert entry["sha256"] == _sha256_bytes(build.file_bytes[entry["path"]])
    assert {entry["split"] for entry in manifest["files"]} == set(SPLITS)
    # 冻结块必须绑定测试集内容哈希。
    test_sha = _sha256_bytes(build.file_bytes["test.jsonl"])
    assert manifest["frozen"]["test_split_sha256"] == test_sha
    assert manifest["frozen"]["frozen"] is True


def test_annotation_block_is_honest(build: DatasetBuild) -> None:
    annotation = build.manifest["annotation"]
    assert annotation["method"] == "developer_single_annotator"
    assert annotation["double_annotated_pct"] == 0
    assert annotation["human_gold"] is False
    assert annotation["business_gold"] is False
    assert annotation["label_origin"] == "synthetic_by_construction"
    assert annotation["annotator_count"] == 1
    self_consistency = build.manifest["self_consistency"]
    assert self_consistency["sampled_pct"] == 10.0
    assert self_consistency["passes"] == 2
    assert 0.0 <= self_consistency["agreement"] <= 1.0
    assert self_consistency["not_inter_annotator_agreement"] is True
    assert self_consistency["sampled_items"] >= 1


def test_write_dataset_is_idempotent_and_freezes_test_split(
    build: DatasetBuild, tmp_path: Path
) -> None:
    out = tmp_path / "dataset"
    manifest = write_dataset(build, out)
    assert manifest["dataset_manifest_sha256"] == build.manifest["dataset_manifest_sha256"]
    for split in SPLITS:
        path = out / f"{split}.jsonl"
        assert path.read_bytes() == build.file_bytes[f"{split}.jsonl"]
    assert (out / "manifest.json").exists()
    assert json.loads((out / "manifest.json").read_text("utf-8")) == manifest
    for entry in manifest["files"]:
        assert _sha256_path(out / entry["path"]) == entry["sha256"]
    assert verify_dataset(out)["ok"] is True

    # 相同种子重复写入：字节与 manifest 都不变（幂等）。
    again = write_dataset(build, out)
    assert again == manifest
    assert json.loads((out / "manifest.json").read_text("utf-8")) == manifest

    # 冻结后篡改测试集：再次写入必须显式失败，不能静默覆盖。
    test_path = out / "test.jsonl"
    original = test_path.read_bytes()
    test_path.write_bytes(original + b"\n")
    with pytest.raises(FrozenSplitError):
        write_dataset(build, out)
    test_path.write_bytes(original)
    assert verify_dataset(out)["ok"] is True


def test_verify_detects_tampered_split(build: DatasetBuild, tmp_path: Path) -> None:
    out = tmp_path / "dataset"
    write_dataset(build, out)
    train = out / "train.jsonl"
    train.write_bytes(train.read_bytes() + b"\n")
    report = verify_dataset(out)
    assert report["ok"] is False
    assert any("sha256" in error for error in report["errors"])


def test_builder_imports_and_runs_without_database(monkeypatch) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    code = (
        "from bank_assistant.datasets.build import build_dataset;"
        "b = build_dataset(seed=11, created_at=__import__('datetime').datetime(2026,1,1,tzinfo=__import__('datetime').UTC));"
        "print(b.manifest['counts']['total'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip().splitlines()[-1]) >= 480


def test_cli_help_is_available_offline() -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "scripts.build_dataset", "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    for flag in ("--seed", "--artifact-root", "--dataset-version", "--verify"):
        assert flag in result.stdout


def test_cli_writes_manifest_and_committed_sample(tmp_path: Path) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "SOURCE_DATE_EPOCH": "1762948800",  # 2025-11-12T12:00:00Z，固定构建时间
    }
    artifact_root = tmp_path / "artifacts"
    committed = tmp_path / "committed"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.build_dataset",
            "--artifact-root",
            str(artifact_root),
            "--committed-dir",
            str(committed),
            "--seed",
            "42",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    manifest_paths = list(artifact_root.glob("data/*/*/manifest.json"))
    assert len(manifest_paths) == 1
    manifest = json.loads(manifest_paths[0].read_text("utf-8"))
    assert manifest["counts"]["total"] >= 480
    summary = json.loads(result.stdout)
    assert summary["dataset_manifest_sha256"] == manifest["dataset_manifest_sha256"]

    sample_dir = committed / DATASET_ID / "v1" / "samples"
    for split in SPLITS:
        sample_path = sample_dir / f"{split}.sample.jsonl"
        lines = sample_path.read_text("utf-8").splitlines()
        assert len(lines) == 5
        for line in lines:
            assert json.loads(line)["synthetic"] is True
    assert (committed / DATASET_ID / "v1" / "manifest.json").exists()

    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.build_dataset",
            "--verify",
            str(artifact_root / "data" / DATASET_ID / "v1"),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["ok"] is True
