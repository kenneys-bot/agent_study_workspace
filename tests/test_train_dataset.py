"""意图数据集加载与 split 守卫（T28，#29）。

使用 T26 构建器在临时目录生成的可校验数据集，不依赖 gitignore 的 artifacts/。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bank_assistant.datasets.split_guard import SplitLeakageError
from bank_assistant.training.dataset import (
    IntentDataError,
    load_intent_split,
)

DATASET_ID = "bank-cs-m6-synthetic"
DATASET_VERSION = "v1"


def _load(directory: Path, split: str, purpose: str):
    return load_intent_split(
        dataset_dir=directory,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        split=split,
        purpose=purpose,
        task="retrieval",
        text_field="query",
        label_field="business_type",
    )


def test_train_split_loads_expected_slice(frozen_dataset_dir: Path) -> None:
    dataset = _load(frozen_dataset_dir, "train", "train")
    assert dataset.n == 202
    assert dataset.label_names == ("complaint", "credit_card_fee", "lost_card")
    assert sum(dataset.label_counts.values()) == 202
    assert dataset.file_sha256 == (
        "538b3847bada550a5fff7ea8d858a89afc4384f7f3eba43bf9653ae7cd58588a"
    )
    assert all(example.text for example in dataset.examples)


@pytest.mark.parametrize("split", ["dev", "test"])
def test_training_refuses_dev_and_test(
    frozen_dataset_dir: Path, split: str
) -> None:
    with pytest.raises(SplitLeakageError):
        _load(frozen_dataset_dir, split, "train")


def test_frozen_test_split_loads_for_eval(frozen_dataset_dir: Path) -> None:
    dataset = _load(frozen_dataset_dir, "test", "eval")
    assert dataset.n == 50
    assert dataset.file_sha256 == (
        "869a8bd02b231d071fe1cff3299bd587e6ccd4771675b2485327685463c70096"
    )
    assert set(dataset.label_names) <= {"complaint", "credit_card_fee", "lost_card"}


@pytest.mark.parametrize("split", ["dev", "train"])
def test_eval_refuses_non_frozen_test(frozen_dataset_dir: Path, split: str) -> None:
    with pytest.raises(SplitLeakageError):
        _load(frozen_dataset_dir, split, "eval")


def test_tampered_split_hash_is_rejected(
    frozen_dataset_dir: Path, tmp_path: Path
) -> None:
    import shutil

    copied = tmp_path / "dataset"
    shutil.copytree(frozen_dataset_dir, copied)
    train_path = copied / "train.jsonl"
    rows = train_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["query"] = row["query"] + " 篡改"
    rows[0] = json.dumps(row, ensure_ascii=False)
    train_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(IntentDataError, match="哈希不一致|校验失败"):
        _load(copied, "train", "train")


def test_dataset_id_mismatch_is_rejected(frozen_dataset_dir: Path) -> None:
    with pytest.raises(IntentDataError, match="数据集 id 不一致"):
        load_intent_split(
            dataset_dir=frozen_dataset_dir,
            dataset_id="other-dataset",
            dataset_version=DATASET_VERSION,
            split="train",
            purpose="train",
            task="retrieval",
            text_field="query",
            label_field="business_type",
        )


def test_label_schema_hash_is_stable(frozen_dataset_dir: Path) -> None:
    first = _load(frozen_dataset_dir, "train", "train")
    second = _load(frozen_dataset_dir, "train", "train")
    assert first.label_schema_sha256 == second.label_schema_sha256
    assert len(first.label_schema_sha256) == 64
    assert first.label_field == "business_type"
