#!/usr/bin/env python3
"""M6 合成数据集构建 CLI（T26，#27）。

用法::

    python -m scripts.build_dataset --help
    python -m scripts.build_dataset --seed 42
    python -m scripts.build_dataset --verify artifacts/data/bank-cs-m6-synthetic/v1

- 构建默认写 ``$ARTIFACT_ROOT/data/<dataset_id>/<version>/``（大文件，已 gitignore），
  并把 manifest 与每个 split 的少量样本发布到 ``data/datasets/``（小文件，可提交）；
- ``--verify DIR`` 只复算已有 manifest 与文件哈希，不重新构建；
- 不连接数据库、不调用模型、不访问网络；同一 seed 与 ``SOURCE_DATE_EPOCH``
  下两次构建逐字节一致。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from bank_assistant.datasets.build import (
    DATASET_ID,
    DEFAULT_SEED,
    build_dataset,
    verify_dataset,
    write_committed_sample,
    write_dataset,
)


def _parse_created_at(value: str | None) -> datetime | None:
    if value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        return datetime.fromtimestamp(int(epoch), UTC)
    return None


def _default_artifact_root() -> Path:
    return Path(os.environ.get("ARTIFACT_ROOT", "artifacts"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.build_dataset",
        description=(
            "构建并冻结 M6 合成数据集（检索查询 + 质检会话）；"
            "按组 60/20/20 拆分，测试集绑定 sha256。"
        ),
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="确定性随机种子")
    parser.add_argument(
        "--dataset-id", default=DATASET_ID, help=f"数据集 id（默认 {DATASET_ID}）"
    )
    parser.add_argument(
        "--dataset-version",
        default="v1",
        help="数据集版本；冻结后改数据必须提升版本",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="产物根目录（默认 ARTIFACT_ROOT 或 artifacts）",
    )
    parser.add_argument(
        "--committed-dir",
        type=Path,
        default=Path("data") / "datasets",
        help="manifest 与小样本的可提交目录（默认 data/datasets）",
    )
    parser.add_argument(
        "--no-committed",
        action="store_true",
        help="只写 ARTIFACT_ROOT，不发布可提交的 manifest/样本",
    )
    parser.add_argument(
        "--sample-per-split",
        type=int,
        default=5,
        help="每个 split 发布多少条样本（默认 5）",
    )
    parser.add_argument(
        "--created-at",
        default=None,
        help="固定构建时间（ISO-8601）；默认取 SOURCE_DATE_EPOCH 或当前时间",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        metavar="DIR",
        default=None,
        help="只校验已构建数据集目录的 manifest 与文件哈希",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verify is not None:
        report = verify_dataset(args.verify)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1

    artifact_root = (args.artifact_root or _default_artifact_root()).resolve()
    build = build_dataset(
        seed=args.seed,
        dataset_version=args.dataset_version,
        dataset_id=args.dataset_id,
        created_at=_parse_created_at(args.created_at),
    )
    out_dir = artifact_root / "data" / args.dataset_id / args.dataset_version
    manifest = write_dataset(build, out_dir)

    committed: dict[str, str] = {}
    if not args.no_committed:
        committed = write_committed_sample(
            build, args.committed_dir, per_split=args.sample_per_split
        )

    summary = {
        "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"],
        "artifact_dir": str(out_dir),
        "artifact_ref": f"data/{manifest['dataset_id']}/{manifest['dataset_version']}",
        "counts": manifest["counts"],
        "hard_samples": manifest["counts"]["hard_samples"],
        "dataset_manifest_sha256": manifest["dataset_manifest_sha256"],
        "split_manifest_sha256": manifest["split_manifest_sha256"],
        "test_split_sha256": manifest["frozen"]["test_split_sha256"],
        "annotation": manifest["annotation"],
        "self_consistency": manifest["self_consistency"],
        "committed": committed,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
