#!/usr/bin/env python
"""真实模型链路冒烟：一次云 chat + 一次真实 embedding（T25，M5 验收）。

- 显式读取 ``ENV_FILE``（默认仓库根 ``.env``）与进程环境；不打印密钥、不把
  凭据写进日志；
- 必须处于 live 模式：``MODEL_MODE=replay`` 时直接失败，不能用回放冒充真实链路；
- 凭据缺失、chat 失败、embedding 失败都非零退出，并给出稳定的失败类别；
- 输出只包含 mode/provider/model/status/attempts、embedding 模型标识、维度与
  向量范数，便于写入报告。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实云模型与 embedding 冒烟")
    parser.add_argument(
        "--env-file",
        default=os.environ.get("ENV_FILE", ".env"),
        help="dotenv 文件路径（默认 .env；已存在的进程环境优先）",
    )
    parser.add_argument(
        "--prompt",
        default="只回复四个字：链路正常",
        help="chat 冒烟提示词",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from dotenv import load_dotenv

    env_path = Path(args.env_file)
    loaded = load_dotenv(env_path, override=False) if env_path.is_file() else False

    # 必须在读取 dotenv 之后再 import settings（settings 是模块级单例）。
    from bank_assistant.config.settings import settings
    from bank_assistant.core.errors import CapabilityUnavailableError
    from bank_assistant.core.model_gateway import ModelGateway
    from bank_assistant.knowledge.embedding import (
        EmbeddingError,
        create_embedding_provider,
    )

    print("demo-real-model-smoke: 真实链路（不会回放）")
    print(
        f"  env：{env_path}（{'已读取' if loaded else '文件不存在或未读取，使用进程环境'}）"
    )
    print(f"  mode：{settings.MODEL_MODE}")
    if settings.MODEL_MODE != "live":
        print(
            "FAIL: MODEL_MODE 必须是 live；拒绝把回放当成真实云模型链路证明",
            file=sys.stderr,
        )
        return 2

    try:
        gateway = ModelGateway.from_settings()
    except CapabilityUnavailableError as exc:
        print(f"FAIL: 模型凭据/依赖缺失：{exc}", file=sys.stderr)
        return 2

    result = gateway.complete(
        [
            {"role": "system", "content": "你是链路冒烟助手，只按用户要求简短回答。"},
            {"role": "user", "content": args.prompt},
        ]
    )
    provider = result.provider.value if result.provider else "none"
    print(
        "  chat："
        f"mode={result.mode} provider={provider} model={result.model or 'none'} "
        f"status={result.status.value} attempts={result.attempts} "
        f"finish_reason={result.finish_reason or 'none'} text_len={len(result.text)}"
    )
    if not result.ok:
        # 不打印 error 原文，避免上游异常文本里意外带出请求头/凭据。
        print(f"FAIL: chat 未成功（status={result.status.value}）", file=sys.stderr)
        return 2

    try:
        embedding = create_embedding_provider(
            settings.EMBEDDING_PROVIDER,
            model_name=settings.EMBEDDING_MODEL_NAME,
        )
        vectors = embedding.embed(["银行坐席演示链路：挂失、费用、投诉（合成文本）"])
    except (EmbeddingError, ValueError) as exc:
        print(f"FAIL: embedding 不可用（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2
    if len(vectors) != 1:
        print(f"FAIL: embedding 返回 {len(vectors)} 个向量，期望 1 个", file=sys.stderr)
        return 2
    norm = math.sqrt(sum(value * value for value in vectors[0]))
    print(
        "  embedding："
        f"provider={settings.EMBEDDING_PROVIDER} "
        f"model={embedding.model_version} "
        f"dim={len(vectors[0])} norm={norm:.6f}"
    )
    print("  结果：通过（chat + embedding 均成功；未打印任何密钥）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
