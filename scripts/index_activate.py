#!/usr/bin/env python
"""把活动指针切回某个已存在的旧索引版本（T37 回滚演练入口）。

索引版本是「只增不改」的：``build_index`` 每次生成新 ``index_version``，
旧集合与清单保留（ADR-0005）。回滚时不需要重建，只要把 ``active.json``
指回旧版本；本脚本在切换前用**当前** embedding provider 重算校准哈希并
核对集合条数，校验不过就显式失败，绝不把不可用的旧索引指成活动的。

用法（服务器上用已部署镜像的一次性容器执行，见 docs/ops/runbook.md §7）：

    python scripts/index_activate.py --index-version idx-20261112T110703Z-2334d03e
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bank_assistant.config.settings import settings
from bank_assistant.knowledge.embedding import EmbeddingError, create_embedding_provider
from bank_assistant.knowledge.indexing import (
    ChromaVectorStore,
    KnowledgeIndexError,
    activate_index_version,
    read_active_pointer,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="切换活动索引指针到指定版本")
    parser.add_argument("--index-version", required=True, help="目标索引版本")
    parser.add_argument(
        "--index-dir", default=settings.INDEX_DIR, help="索引目录（默认 data/index）"
    )
    parser.add_argument(
        "--provider",
        default=settings.EMBEDDING_PROVIDER,
        choices=["fastembed", "fake"],
        help="embedding 提供方（默认取 EMBEDDING_PROVIDER）",
    )
    parser.add_argument(
        "--model",
        default=settings.EMBEDDING_MODEL_NAME,
        help="fastembed 模型名（默认 BAAI/bge-small-zh-v1.5）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    index_dir = Path(args.index_dir)
    previous = read_active_pointer(index_dir)
    try:
        provider = create_embedding_provider(args.provider, model_name=args.model)
        store = ChromaVectorStore(index_dir / "chroma")
        pointer = activate_index_version(
            index_dir,
            index_version=args.index_version,
            provider=provider,
            store=store,
        )
    except (KnowledgeIndexError, EmbeddingError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if previous is not None:
            print(
                f"  活动指针未改动：{previous['index_version']}",
                file=sys.stderr,
            )
        return 2

    print("index-activate: OK")
    print(f"  活动指针：{previous['index_version'] if previous else '无'} → "
          f"{pointer['index_version']}")
    print(f"  校准向量 sha256：{pointer['calibration_vector_sha256']}")
    print(f"  embedding 模型：{pointer['embedding_model_version']}")
    print(f"  切换时间：{pointer['switched_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
