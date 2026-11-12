#!/usr/bin/env python
"""打印当前活动索引版本与清单摘要（M3，``make index-info``）。

只读：不构建、不改指针。清单缺失或结构非法时非零退出，避免把“没有索引”
说成“索引正常”。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, select

from bank_assistant.config.settings import settings
from bank_assistant.infra.db import session_scope
from bank_assistant.infra.models import KnowledgeChunk
from bank_assistant.knowledge.indexing import (
    IndexNotBuilt,
    read_active_pointer,
    read_manifest,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="打印活动索引版本与清单摘要")
    parser.add_argument(
        "--index-dir",
        default=settings.INDEX_DIR,
        help="索引目录（默认 data/index）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    index_dir = Path(args.index_dir)
    pointer = read_active_pointer(index_dir)
    if pointer is None:
        print(
            f"NOT_BUILT: {index_dir / 'active.json'} 不存在；请先 make index-rebuild",
            file=sys.stderr,
        )
        return 2

    try:
        manifest = read_manifest(index_dir / pointer["manifest"])
    except IndexNotBuilt as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    snapshot = manifest.get("knowledge_snapshot", [])
    print(f"index-info: {index_dir}")
    print(f"  活动索引版本：{manifest['index_version']}")
    print(f"  集合：{manifest['collection']}")
    print(f"  embedding 模型：{manifest['embedding_model_version']}")
    print(f"  校准向量 sha256：{manifest['calibration_vector_sha256']}")
    print(f"  创建时间：{manifest['created_at']}")
    print(f"  切换时间：{pointer.get('switched_at', '未知')}")
    print(f"  知识快照：{len(snapshot)} 个版本，{manifest['chunk_count']} 个分块")
    print(f"  清单文件：{pointer['manifest']}")

    try:
        with session_scope() as session:
            stored = session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.index_version == manifest["index_version"])
            )
    except Exception as exc:  # noqa: BLE001 - DB 不可用不影响清单摘要可读
        print(f"  数据库分块校验：不可用（{exc.__class__.__name__}）")
        return 0

    if stored != manifest["chunk_count"]:
        print(
            f"  FAIL: 数据库分块 {stored} 与清单 {manifest['chunk_count']} 不一致",
            file=sys.stderr,
        )
        return 2
    print(f"  数据库分块校验：OK（{stored} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
