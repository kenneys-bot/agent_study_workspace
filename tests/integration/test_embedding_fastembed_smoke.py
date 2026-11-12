"""真实 fastembed（BAAI/bge-small-zh-v1.5，512 维，ONNX CPU）冒烟测试。

这属于集成路径：慢、依赖模型缓存/网络，因此只在 ``make test-integration``
时运行，并且**显式跳过**而不是伪造成功：

- 模型未缓存时默认 ``HF_HUB_OFFLINE=1`` 探测，缺失即 SKIP；
- 需要临时允许下载时设置 ``FASTEMBED_SMOKE=1``；
- 依赖缺失、模型加载失败或探测超时同样 SKIP，reason 里写明原因。

报告里必须如实写“REAL（已跑）”或“SKIPPED（未跑）”，不能把跳过当通过。
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bank_assistant.knowledge.embedding import (
    EMBEDDING_DIMENSION,
    FastEmbedProvider,
    calibration_vector_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE = """
from bank_assistant.knowledge.embedding import FastEmbedProvider
provider = FastEmbedProvider()
vector = provider.embed(["probe"])[0]
assert len(vector) == provider.dimension
print(provider.model_version)
"""


def _probe_fastembed() -> str | None:
    """在子进程里以离线模式探测模型缓存；返回 None 表示可用，否则返回原因。"""
    environment = dict(os.environ)
    if os.environ.get("FASTEMBED_SMOKE") == "1":
        environment.pop("HF_HUB_OFFLINE", None)
    else:
        environment["HF_HUB_OFFLINE"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=REPO_ROOT,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"探测未完成：{exc}"
    if completed.returncode != 0:
        lines = (completed.stderr or completed.stdout).strip().splitlines()
        detail = lines[-1][:240] if lines else "未知错误"
        return f"模型/依赖不可用（离线缓存缺失？可用 FASTEMBED_SMOKE=1 允许下载）：{detail}"
    return None


_SKIP_REASON = _probe_fastembed()


@pytest.mark.integration
@pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=f"fastembed 冒烟跳过：{_SKIP_REASON}",
)
def test_fastembed_bge_small_zh_returns_512d_and_stable_calibration() -> None:
    provider = FastEmbedProvider()

    assert provider.dimension == EMBEDDING_DIMENSION == 512
    assert provider.model_version == "BAAI/bge-small-zh-v1.5@512"

    vector = provider.embed(["挂失信用卡后多久可以补卡？"])[0]
    assert len(vector) == 512
    assert all(math.isfinite(value) for value in vector)

    # 两个独立实例必须给出同一校准哈希：跨进程/重建可复现。
    assert calibration_vector_sha256(provider) == calibration_vector_sha256(
        FastEmbedProvider()
    )
