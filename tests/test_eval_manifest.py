"""运行 manifest 与产物落盘（T27，#28；契约 §6）。

测试只打两个公开 seam：``build_run_manifest``（字段完整性与秘密扫描）与
``write_run_artifacts``（ARTIFACT_ROOT 下的相对路径、可复算的产物文件）。
不启动数据库、不访问网络。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bank_assistant.evaluation.manifest import (
    REQUIRED_MANIFEST_FIELDS,
    ManifestError,
    RunContext,
    assert_no_secrets,
    build_run_manifest,
    validate_manifest,
    write_run_artifacts,
)

STARTED = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
FINISHED = datetime(2026, 11, 12, 10, 5, 0, tzinfo=UTC)


def _context(**overrides) -> RunContext:
    base = dict(
        run_id="eval-replay-20261112T100000",
        task="m6_eval",
        mode="replay",
        model_mode="replay",
        dataset_id="bank-cs-m6-synthetic",
        dataset_version="v1",
        dataset_manifest_sha256="a" * 64,
        split="test",
        split_manifest_sha256="b" * 64,
        dataset_test_sha256="c" * 64,
        knowledge_snapshot="d" * 64,
        rule_snapshot="e" * 64,
        prompt_version="eval-v1:0123456789abcdef",
        prompt_components={"rewrite": "query-rewrite-v1:1", "inspection": "inspection-v1:2"},
        model_provider="replay",
        model_version="deterministic-replay",
        embedding_version="replay-lexical-char2gram@512",
        index_version="eval-replay-0123456789ab",
        seed=42,
        code_sha="f" * 40,
        worktree_clean=True,
        hardware_summary="macOS-26 / 16 CPU / Python 3.13 / 64 GiB",
        hardware={"platform": "macOS-26", "cpu_count": 16, "python": "3.13"},
        started_at=STARTED,
        finished_at=FINISHED,
        status="succeeded",
    )
    base.update(overrides)
    return RunContext(**base)


def test_manifest_contains_every_contract_and_task_field() -> None:
    manifest = build_run_manifest(
        context=_context(),
        metrics_path="evals/run/metrics.json",
        predictions_path="evals/run/predictions.jsonl",
    )
    for field in REQUIRED_MANIFEST_FIELDS:
        assert field in manifest, field
    assert manifest["mode"] == "replay"
    assert manifest["model_mode"] == "replay"
    assert manifest["split"] == "test"
    assert manifest["seed"] == 42
    assert manifest["started_at"] == "2026-11-12T10:00:00+00:00"
    assert manifest["finished_at"] == "2026-11-12T10:05:00+00:00"
    assert manifest["status"] == "succeeded"
    validate_manifest(manifest)


def test_manifest_validation_names_missing_fields() -> None:
    manifest = build_run_manifest(context=_context())
    broken = {key: value for key, value in manifest.items() if key != "dataset_version"}
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(broken)
    assert "dataset_version" in str(excinfo.value)
    with pytest.raises(ManifestError):
        validate_manifest({**manifest, "mode": "mixed"})


def test_manifest_never_carries_credentials() -> None:
    with pytest.raises(ManifestError):
        assert_no_secrets({"note": "sk-1234567890abcdef"})
    with pytest.raises(ManifestError):
        assert_no_secrets({"extra": {"api_key": "whatever"}})
    with pytest.raises(ManifestError):
        assert_no_secrets({"extra": "DEEPSEEK_API_KEY=abc"})
    # token 指标字段不能因为名字里有 token 就被误判。
    assert_no_secrets({"input_tokens": 10, "output_tokens": 20})
    manifest = build_run_manifest(context=_context())
    assert_no_secrets(manifest)


def test_write_run_artifacts_uses_relative_paths_under_artifact_root(
    tmp_path: Path,
) -> None:
    manifest = build_run_manifest(context=_context())
    metrics = {"mode": "replay", "run_id": "eval-replay-20261112T100000"}
    predictions = [{"item_id": "rq-1", "variant": "original"}]
    written = write_run_artifacts(
        artifact_root=tmp_path,
        manifest=manifest,
        metrics=metrics,
        predictions=predictions,
    )
    run_dir = tmp_path / "evals" / "eval-replay-20261112T100000"
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "metrics.json").is_file()
    assert (run_dir / "predictions.jsonl").is_file()
    assert written["metrics_path"] == "evals/eval-replay-20261112T100000/metrics.json"
    assert written["predictions_path"] == (
        "evals/eval-replay-20261112T100000/predictions.jsonl"
    )
    stored = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    assert stored["metrics_path"] == written["metrics_path"]
    assert stored["predictions_path"] == written["predictions_path"]
    validate_manifest(stored)
    lines = (run_dir / "predictions.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(line) for line in lines] == predictions


def test_run_id_cannot_escape_artifact_root(tmp_path: Path) -> None:
    manifest = build_run_manifest(context=_context(run_id="../escape"))
    with pytest.raises(ManifestError):
        write_run_artifacts(
            artifact_root=tmp_path,
            manifest=manifest,
            metrics={},
            predictions=[],
        )
    assert not (tmp_path.parent / "escape").exists()
