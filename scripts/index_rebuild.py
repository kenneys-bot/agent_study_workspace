#!/usr/bin/env python
"""重建版本化知识索引，并在校验通过后原子切换活动指针（M3，ADR-0005）。

``make index-rebuild`` 的入口：

1. 从 PostgreSQL 取「已发布且在有效期、范围内」的知识版本（草稿/过期/未来/
   跨范围不进入索引）；
2. 用 ``EMBEDDING_PROVIDER``（默认 fastembed + bge-small-zh-v1.5）生成分块向量，
   写入 Chroma 集合 ``knowledge_<index_version>`` 与 ``knowledge_chunks`` 行；
   索引文本形态由 ``RETRIEVAL_EMBEDDING_TEXT`` 决定（T37 默认 ``title_chunk``：
   标题拼入 embedding 输入，分块正文与区间不变）；
3. 写清单（含校准向量哈希与知识快照），校验集合条数；
4. 原子切换 ``data/index/active.json``；失败保留旧索引与旧指针并非零退出。

离线演练：``EMBEDDING_PROVIDER=fake make index-rebuild``（确定性假向量，不下载
模型；索引可重建但没有真实语义质量，报告里必须写清楚）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bank_assistant.config.settings import settings
from bank_assistant.infra.db import session_scope
from bank_assistant.knowledge.embedding import (
    EmbeddingError,
    create_embedding_provider,
)
from bank_assistant.knowledge.indexing import (
    ChromaVectorStore,
    KnowledgeIndexError,
    build_index,
    load_active_index,
    read_active_pointer,
)
from bank_assistant.knowledge.ranking import EMBEDDING_TEXT_MODES


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重建版本化知识索引")
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
    parser.add_argument(
        "--index-dir",
        default=settings.INDEX_DIR,
        help="索引目录（默认 data/index）",
    )
    parser.add_argument(
        "--scope-id",
        default=settings.DEMO_SCOPE_ID,
        help="只索引该访问范围的知识版本",
    )
    parser.add_argument(
        "--embedding-text",
        default=settings.RETRIEVAL_EMBEDDING_TEXT,
        choices=sorted(EMBEDDING_TEXT_MODES),
        help=(
            "索引文本形态（默认 RETRIEVAL_EMBEDDING_TEXT，T37 为 title_chunk）；"
            "改动会改变向量，必须新建 index_version"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    index_dir = Path(args.index_dir)
    previous = read_active_pointer(index_dir)

    try:
        provider = create_embedding_provider(args.provider, model_name=args.model)
    except (EmbeddingError, ValueError) as exc:
        print(f"FAIL: 无法加载 embedding 提供方：{exc}", file=sys.stderr)
        return 2

    store = ChromaVectorStore(index_dir / "chroma")
    try:
        with session_scope() as session:
            result = build_index(
                session,
                provider=provider,
                store=store,
                scope_id=args.scope_id,
                index_dir=index_dir,
                embedding_text=args.embedding_text,
            )
        # 切换后再读一次活动索引，验证加载路径（校准哈希 + 集合条数）。
        _, verified = load_active_index(index_dir, provider=provider, store=store)
    except (KnowledgeIndexError, EmbeddingError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if previous is not None:
            print(
                f"  旧索引保留：{previous['index_version']}（活动指针未改动）",
                file=sys.stderr,
            )
        return 2

    print(f"index-rebuild: scope={args.scope_id}")
    print(f"  embedding：{result.manifest['embedding_model_version']}")
    print(f"  索引文本形态：{result.manifest['embedding_text']}")
    print(f"  索引版本：{result.index_version}（集合 {result.collection}）")
    print(f"  知识版本 {result.version_count} 个，分块 {result.chunk_count} 条")
    print(f"  内容身份 sha256：{result.manifest['index_content_sha256']}")
    print(
        "  校准向量 sha256："
        f"{result.manifest['calibration_vector_sha256']}"
    )
    previous_version = previous["index_version"] if previous else "无（首次构建）"
    print(f"  活动指针：{previous_version} → {result.index_version}")
    print(f"  清单：{index_dir / 'manifests' / (result.index_version + '.json')}")
    print(f"  校验：OK（校准哈希一致，集合条数 {verified['chunk_count']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
