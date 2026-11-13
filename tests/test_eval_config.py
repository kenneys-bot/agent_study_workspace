"""评测配置加载（T27，#28）：JSON/YAML、模式约束、split 守卫与价目表。

配置是公开文件，只放价格来源与占位值；凭据永远从环境/.env 读取，不写进
EVAL_CONFIG。测试不联网。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bank_assistant.datasets.split_guard import SplitLeakageError
from bank_assistant.evaluation.config import (
    EvalConfig,
    EvalConfigError,
    load_eval_config,
)


def _payload(**overrides) -> dict:
    payload = {
        "mode": "replay",
        "split": "test",
        "dataset_id": "bank-cs-m6-synthetic",
        "dataset_version": "v1",
        "dataset_dir": "artifacts/data/bank-cs-m6-synthetic/v1",
        "artifact_root": "artifacts",
        "seed": 42,
        "top_k": 5,
        "min_similarity": 0.1,
        "model": {"provider": "deepseek", "model": "deepseek-chat"},
        "embedding": {"provider": "replay_lexical"},
        "pricing": [
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "input_cny_per_mtok": 2.0,
                "output_cny_per_mtok": 8.0,
                "source": "https://api-docs.deepseek.com/quick_start/pricing",
                "as_of": "2026-11-12",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_load_json_config_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    config = load_eval_config(path)
    assert isinstance(config, EvalConfig)
    assert config.mode == "replay"
    assert config.split == "test"
    assert config.seed == 42
    assert config.top_k == 5
    assert config.dataset_dir == Path("artifacts/data/bank-cs-m6-synthetic/v1")
    assert config.artifact_root == Path("artifacts")
    assert config.model_mode == "replay"
    assert config.tasks == ("retrieval", "inspection")
    assert config.max_items is None


def test_load_yaml_config(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "eval.yaml"
    path.write_text(
        """
mode: replay
split: test
dataset_id: bank-cs-m6-synthetic
dataset_version: v1
dataset_dir: artifacts/data/bank-cs-m6-synthetic/v1
artifact_root: artifacts
seed: 42
model:
  provider: deepseek
  model: deepseek-chat
embedding:
  provider: replay_lexical
pricing:
  - provider: deepseek
    model: deepseek-chat
    input_cny_per_mtok: 2.0
    output_cny_per_mtok: 8.0
    source: https://api-docs.deepseek.com/quick_start/pricing
    as_of: "2026-11-12"
""",
        encoding="utf-8",
    )
    config = load_eval_config(path)
    assert config.mode == "replay"


def test_live_mode_requires_sample_cap_and_budget(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(_payload(mode="live")), encoding="utf-8")
    with pytest.raises(EvalConfigError) as excinfo:
        load_eval_config(path)
    assert "max_items" in str(excinfo.value) and "budget_cny" in str(excinfo.value)

    path.write_text(
        json.dumps(_payload(mode="live", max_items=20, budget_cny=2.0)),
        encoding="utf-8",
    )
    config = load_eval_config(path)
    assert config.model_mode == "cloud"
    assert config.max_items == 20
    assert config.budget_cny == 2.0


def test_replay_mode_must_use_deterministic_provider(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(
        json.dumps(_payload(embedding={"provider": "fastembed"})),
        encoding="utf-8",
    )
    with pytest.raises(EvalConfigError) as excinfo:
        load_eval_config(path)
    assert "replay" in str(excinfo.value)


def test_non_test_split_is_refused_at_config_load(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    for split in ("dev", "train"):
        path.write_text(json.dumps(_payload(split=split)), encoding="utf-8")
        with pytest.raises(SplitLeakageError):
            load_eval_config(path)


def test_unknown_mode_and_unknown_keys_fail_loudly(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(_payload(mode="mixed")), encoding="utf-8")
    with pytest.raises(EvalConfigError):
        load_eval_config(path)
    path.write_text(json.dumps(_payload(typo_key=1)), encoding="utf-8")
    with pytest.raises(EvalConfigError) as excinfo:
        load_eval_config(path)
    assert "typo_key" in str(excinfo.value)


def test_live_mode_requires_a_matching_price_entry(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    payload = _payload(mode="live", max_items=5, budget_cny=1.0, pricing=[])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvalConfigError) as excinfo:
        load_eval_config(path)
    assert "价目" in str(excinfo.value)


def test_price_lookup_returns_configured_prices(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    config = load_eval_config(path)
    price = config.price_for("deepseek", "deepseek-chat")
    assert price.input_cny_per_mtok == 2.0
    assert price.output_cny_per_mtok == 8.0
    assert price.source.startswith("https://")
    with pytest.raises(EvalConfigError):
        config.price_for("deepseek", "unknown-model")


def test_retrieval_options_default_to_t27_behaviour(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    options = load_eval_config(path).retrieval
    assert options.embedding_text == "chunk"
    assert options.business_filter == "hard"
    assert options.rerank_alpha == 0.0
    assert options.scenario_overrides == {}


def test_retrieval_options_are_parsed_and_overridable_per_scenario(
    tmp_path: Path,
) -> None:
    path = tmp_path / "eval.json"
    path.write_text(
        json.dumps(
            _payload(
                retrieval={
                    "embedding_text": "title_chunk",
                    "business_filter": "soft",
                    "business_bonus": 0.05,
                    "rerank_alpha": 0.2,
                    "rerank_pool": 10,
                    "scenario_overrides": {
                        "complaint": {"business_filter": "hard", "top_k": 7}
                    },
                }
            )
        ),
        encoding="utf-8",
    )
    options = load_eval_config(path).retrieval
    assert options.embedding_text == "title_chunk"
    assert options.business_filter == "soft"
    assert options.rerank_alpha == 0.2
    override = options.for_scenario("complaint")
    assert override.business_filter == "hard"
    assert override.top_k == 7
    assert options.for_scenario("lost_card").business_filter is None


def test_unknown_retrieval_option_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "eval.json"
    path.write_text(
        json.dumps(_payload(retrieval={"embedding_text": "chunk", "typo": 1})),
        encoding="utf-8",
    )
    with pytest.raises(EvalConfigError) as excinfo:
        load_eval_config(path)
    assert "typo" in str(excinfo.value)
    path.write_text(
        json.dumps(_payload(retrieval={"embedding_text": "magic"})),
        encoding="utf-8",
    )
    with pytest.raises(EvalConfigError):
        load_eval_config(path)
