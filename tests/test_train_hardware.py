"""硬件探针（T28，#29）：解析 nvidia-smi、忙碌判定、无 GPU 降级。

不依赖真实 GPU；用注入的可执行脚本模拟 nvidia-smi。
"""

from __future__ import annotations

import os
from pathlib import Path

from bank_assistant.training.hardware import parse_nvidia_smi_csv, probe_hardware


def test_parse_nvidia_smi_csv() -> None:
    parsed = parse_nvidia_smi_csv(
        "NVIDIA GeForce RTX 3060 Ti, 580.178.04, 8192, 1457, 6735, 0\n"
    )
    assert parsed["name"] == "NVIDIA GeForce RTX 3060 Ti"
    assert parsed["memory.total"] == "8192"
    assert parsed["memory.free"] == "6735"
    assert parsed["utilization.gpu"] == "0"
    assert parse_nvidia_smi_csv("") == {}


def test_probe_without_nvidia_smi_reports_unavailable() -> None:
    probe = probe_hardware(nvidia_smi="/definitely/not/a/real/nvidia-smi")
    assert probe.nvidia_smi_available is False
    assert probe.gpu_available is False
    assert "not found" in (probe.error or "") or "No such file" in (probe.error or "")
    assert probe.block_reasons(
        min_free_vram_gib=2.0, min_total_vram_gib=8.0, allow_time_sharing=False
    )


def _fake_nvidia_smi(tmp_path: Path, *, busy: bool) -> str:
    script = tmp_path / "nvidia-smi"
    process_line = (
        "14219, /home/jack/workspace/dingtalk-embed/bin/python, 1280" if busy else ""
    )
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        '  echo "NVIDIA-SMI 580.178.04   Driver Version: 580.178.04"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "--query-compute-apps=pid,process_name,used_memory" ]; then\n'
        f'  echo "{process_line}"\n'
        "  exit 0\n"
        "fi\n"
        'echo "NVIDIA GeForce RTX 3060 Ti, 580.178.04, 8192, 1457, 6735, 0"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    return str(script)


def test_probe_busy_gpu_blocks_training(tmp_path: Path) -> None:
    probe = probe_hardware(nvidia_smi=_fake_nvidia_smi(tmp_path, busy=True))
    assert probe.gpu_available is True
    assert probe.gpu_busy is True
    assert probe.free_vram_gib == 6.58
    reasons = probe.block_reasons(
        min_free_vram_gib=2.0, min_total_vram_gib=8.0, allow_time_sharing=False
    )
    assert any("被其他任务占用" in reason for reason in reasons)
    assert any("dingtalk-embed" in reason for reason in reasons)


def test_probe_idle_gpu_passes_gate(tmp_path: Path) -> None:
    probe = probe_hardware(nvidia_smi=_fake_nvidia_smi(tmp_path, busy=False))
    assert probe.gpu_busy is False
    assert (
        probe.block_reasons(
            min_free_vram_gib=2.0, min_total_vram_gib=8.0, allow_time_sharing=False
        )
        == []
    )
    assert "RTX 3060 Ti" in probe.summary


def test_allow_time_sharing_permits_busy_gpu(tmp_path: Path) -> None:
    probe = probe_hardware(nvidia_smi=_fake_nvidia_smi(tmp_path, busy=True))
    reasons = probe.block_reasons(
        min_free_vram_gib=2.0, min_total_vram_gib=8.0, allow_time_sharing=True
    )
    assert reasons == []
