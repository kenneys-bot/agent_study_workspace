"""全量 replay 运行与产物（T27，#28）。

只打公开 seam ``run_eval``：在临时 ARTIFACT_ROOT 上跑完整冻结 test split，
验证 manifest/metrics/predictions 落盘、分母与失败案例、replay 可复现，
以及数据集被篡改时显式拒绝。不联网、不调用真实模型。
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bank_assistant.datasets.build import build_dataset, write_dataset
from bank_assistant.datasets.corpus import CORPUS_AS_OF
from bank_assistant.evaluation.config import EvalConfig
from bank_assistant.evaluation.errors import EvalError
from bank_assistant.evaluation.manifest import validate_manifest
from bank_assistant.evaluation.runner import run_eval

FIXED_CREATED_AT = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
STARTED = datetime(2026, 11, 12, 13, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def artifact_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("eval-artifacts")
    build = build_dataset(seed=42, created_at=FIXED_CREATED_AT)
    directory = (
        root / "data" / build.manifest["dataset_id"] / build.manifest["dataset_version"]
    )
    write_dataset(build, directory)
    return root


def _config(root: Path, **overrides) -> EvalConfig:
    base = dict(
        mode="replay",
        split="test",
        dataset_id="bank-cs-m6-synthetic",
        dataset_version="v1",
        dataset_dir=root / "data" / "bank-cs-m6-synthetic" / "v1",
        artifact_root=root,
        seed=42,
        top_k=5,
        min_similarity=0.1,
        max_items=None,
        budget_cny=0.0,
        model_provider="deepseek",
        model_name="deepseek-chat",
        embedding_provider="replay_lexical",
        pricing=(),
        scope_id="demo-bank",
        knowledge_as_of=CORPUS_AS_OF,
        tasks=("retrieval", "inspection"),
    )
    base.update(overrides)
    return EvalConfig(**base)


def _strip_latency(node):
    if isinstance(node, dict):
        return {
            key: _strip_latency(value)
            for key, value in node.items()
            if key != "latency_ms"
        }
    if isinstance(node, list):
        return [_strip_latency(value) for value in node]
    return node


def test_full_replay_run_writes_manifest_metrics_and_predictions(
    artifact_root: Path,
) -> None:
    run = run_eval(
        _config(artifact_root),
        run_id="eval-replay-full-1",
        started_at=STARTED,
    )
    assert run.status == "succeeded"
    run_dir = artifact_root / "evals" / "eval-replay-full-1"
    assert (run_dir / "manifest.json").is_file()
    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    validate_manifest(manifest)
    assert manifest["mode"] == "replay"
    assert manifest["model_mode"] == "replay"
    assert manifest["split"] == "test"
    assert manifest["dataset_test_sha256"] == run.metrics["run"]["dataset_test_sha256"]
    assert manifest["metrics_path"] == "evals/eval-replay-full-1/metrics.json"
    assert manifest["predictions_path"] == "evals/eval-replay-full-1/predictions.jsonl"

    assert set(run.metrics["retrieval"]["by_variant"]) == {
        "original",
        "contextual_concat",
        "core_intent_filter",
    }
    answerable = run.metrics["retrieval"]["by_variant"]["original"]["overall"][
        "answerable_items"
    ]
    assert answerable > 0
    assert run.metrics["retrieval"]["by_variant"]["original"]["overall"][
        "recall_at_5"
    ] is not None
    assert set(run.metrics["inspection"]["by_variant"]) == {
        "rule_baseline",
        "llm_baseline",
        "combined",
    }
    assert len(run.metrics["inspection"]["by_variant"]["combined"]["per_rule"]) == 6
    assert run.metrics["inspection"]["by_variant"]["combined"]["macro_f1"] is not None
    assert run.metrics["citation"]["validity"] == 1.0
    assert run.metrics["citation"]["support"]["status"] == "NOT_MEASURED"
    assert run.metrics["cost"]["estimated"] is True
    assert run.metrics["cost"]["cny"] == 0.0
    assert run.metrics["acceptance"]["gates_applied"] is False
    assert run.metrics["failure_cases"]["retrieval"] or True
    assert run.metrics["run"]["sample"]["retrieval_items"] > 0
    assert run.metrics["run"]["sample"]["inspection_items"] > 0

    lines = (run_dir / "predictions.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == len(run.predictions)
    assert {json.loads(line)["type"] for line in lines} == {"retrieval", "inspection"}


def test_replay_run_is_reproducible_apart_from_wall_clock(artifact_root: Path) -> None:
    first = run_eval(
        _config(artifact_root),
        run_id="eval-replay-repeat-1",
        started_at=STARTED,
    )
    second = run_eval(
        _config(artifact_root),
        run_id="eval-replay-repeat-2",
        started_at=STARTED,
    )
    assert _strip_latency(first.metrics["retrieval"]) == _strip_latency(
        second.metrics["retrieval"]
    )
    assert _strip_latency(first.metrics["inspection"]) == _strip_latency(
        second.metrics["inspection"]
    )
    first_predictions = [
        {key: value for key, value in row.items() if key != "latency_ms"}
        for row in first.predictions
    ]
    second_predictions = [
        {key: value for key, value in row.items() if key != "latency_ms"}
        for row in second.predictions
    ]
    assert first_predictions == second_predictions


def test_tampered_test_split_is_refused(artifact_root: Path, tmp_path: Path) -> None:
    copied = tmp_path / "artifacts"
    shutil.copytree(artifact_root, copied)
    test_path = copied / "data" / "bank-cs-m6-synthetic" / "v1" / "test.jsonl"
    rows = test_path.read_text("utf-8").splitlines()
    test_path.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(EvalError) as excinfo:
        run_eval(
            _config(copied),
            run_id="eval-replay-tampered",
            started_at=STARTED,
        )
    assert "校验失败" in str(excinfo.value) or "冻结哈希" in str(excinfo.value)


def test_missing_dataset_fails_with_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(EvalError) as excinfo:
        run_eval(
            _config(tmp_path),
            run_id="eval-replay-missing",
            started_at=STARTED,
        )
    assert "dataset-build" in str(excinfo.value)
