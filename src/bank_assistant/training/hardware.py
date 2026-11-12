"""硬件探针（T28，#29）：GPU 是否可用、是否被别的任务占用、显存余量。

8GB RTX 3060 Ti 与 ``dingtalk-train``/``dingtalk-embed`` 共享，训练前必须确认
空闲；本模块只**探测**，不杀进程、不重置 GPU、不排队。探测结果原样写进
run manifest 的 ``hardware``，让 ``NOT_RUN`` 结论可以被复核，而不是一句
"资源不足"。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

__all__ = [
    "HardwareProbe",
    "parse_nvidia_smi_csv",
    "probe_hardware",
]

_NVIDIA_SMI_TIMEOUT = 10


@dataclass(frozen=True, slots=True)
class HardwareProbe:
    probed_at: str
    platform: str
    machine: str
    cpu_count: int | None
    memory_gib: float | None
    python: str
    nvidia_smi_available: bool
    nvidia_smi_version: str | None
    gpu_name: str | None
    driver_version: str | None
    total_vram_gib: float | None
    used_vram_gib: float | None
    free_vram_gib: float | None
    gpu_utilization_pct: float | None
    compute_processes: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None
    raw_query: str | None = None
    raw_processes: str | None = None

    @property
    def gpu_available(self) -> bool:
        return self.nvidia_smi_available and self.gpu_name is not None

    @property
    def gpu_busy(self) -> bool:
        return bool(self.compute_processes)

    @property
    def summary(self) -> str:
        if not self.gpu_available:
            cpu = f"{self.cpu_count} CPU" if self.cpu_count else "CPU 未知"
            memory = f" / {self.memory_gib} GiB" if self.memory_gib else ""
            return (
                f"{self.platform} / {self.machine} / {cpu} / Python {self.python}"
                f"{memory} / GPU 不可用"
            )
        return (
            f"{self.gpu_name} / {self.total_vram_gib} GiB"
            f"（已用 {self.used_vram_gib} GiB，空闲 {self.free_vram_gib} GiB）"
            f" / driver {self.driver_version}"
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["gpu_available"] = self.gpu_available
        payload["gpu_busy"] = self.gpu_busy
        payload["summary"] = self.summary
        return payload

    def block_reasons(
        self,
        *,
        min_free_vram_gib: float,
        min_total_vram_gib: float,
        allow_time_sharing: bool,
    ) -> list[str]:
        """真实训练前的资源闸门；返回空列表才允许启动。"""
        reasons: list[str] = []
        if not self.gpu_available:
            reasons.append(
                "未检测到可用 NVIDIA GPU（nvidia-smi 缺失或失败）："
                f"{self.error or 'unknown'}"
            )
            return reasons
        if self.gpu_busy and not allow_time_sharing:
            names = ", ".join(
                f"{item.get('process_name')}(pid={item.get('pid')}, "
                f"{item.get('used_memory_mib')} MiB)"
                for item in self.compute_processes
            )
            reasons.append(
                "GPU 被其他任务占用，未获准 time-sharing（hardware.allow_time_sharing=false）："
                f"{names}"
            )
        if (
            self.total_vram_gib is None
            or self.total_vram_gib < min_total_vram_gib
        ):
            reasons.append(
                f"总显存 {self.total_vram_gib} GiB 低于阈值 {min_total_vram_gib} GiB"
            )
        if self.free_vram_gib is None or self.free_vram_gib < min_free_vram_gib:
            reasons.append(
                f"空闲显存 {self.free_vram_gib} GiB 低于阈值 {min_free_vram_gib} GiB"
            )
        return reasons


def _run(command: list[str], *, timeout: int = _NVIDIA_SMI_TIMEOUT) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def parse_nvidia_smi_csv(text: str) -> dict[str, str]:
    """解析 ``--format=csv,noheader,nounits`` 的单行输出；字段顺序固定。"""
    line = next((item for item in text.splitlines() if item.strip()), "")
    parts = [part.strip() for part in line.split(",")]
    keys = (
        "name",
        "driver_version",
        "memory.total",
        "memory.used",
        "memory.free",
        "utilization.gpu",
    )
    if len(parts) < len(keys):
        return {}
    return dict(zip(keys, parts, strict=False))


def _processes(text: str) -> tuple[dict[str, Any], ...]:
    processes: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        pid, process_name, used = parts[0], parts[1], parts[2]
        try:
            used_mib = int(float(used))
        except ValueError:
            used_mib = None
        processes.append(
            {
                "pid": int(pid) if pid.isdigit() else pid,
                "process_name": process_name,
                "used_memory_mib": used_mib,
            }
        )
    return tuple(processes)


def _memory_gib() -> float | None:
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    return round(total / 1024**3, 1)


def _to_gib(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / 1024, 2)
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def probe_hardware(*, nvidia_smi: str | None = None) -> HardwareProbe:
    """探测本机硬件；在 Mac 上会诚实返回 "GPU 不可用"。"""
    probed_at = datetime.now(UTC).isoformat()
    executable = nvidia_smi or shutil.which("nvidia-smi")
    base: dict[str, Any] = {
        "probed_at": probed_at,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_gib": _memory_gib(),
        "python": sys.version.split()[0],
        "nvidia_smi_available": False,
        "nvidia_smi_version": None,
        "gpu_name": None,
        "driver_version": None,
        "total_vram_gib": None,
        "used_vram_gib": None,
        "free_vram_gib": None,
        "gpu_utilization_pct": None,
        "compute_processes": (),
        "error": None,
        "raw_query": None,
        "raw_processes": None,
    }
    if not executable:
        base["error"] = "nvidia-smi not found in PATH"
        return HardwareProbe(**base)

    version_code, version_out, version_err = _run([executable, "--version"])
    if version_code != 0:
        base["error"] = version_err or version_out or f"nvidia-smi --version exited {version_code}"
        return HardwareProbe(**base)
    version_line = next(
        (line for line in version_out.splitlines() if "NVIDIA-SMI" in line), ""
    )
    base["nvidia_smi_version"] = version_line.strip() or version_out.splitlines()[0].strip()

    query_code, query_out, query_err = _run(
        [
            executable,
            "--query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if query_code != 0:
        base["error"] = query_err or query_out or f"nvidia-smi query exited {query_code}"
        return HardwareProbe(**base)
    parsed = parse_nvidia_smi_csv(query_out)
    if not parsed:
        base["error"] = "nvidia-smi query 输出无法解析"
        return HardwareProbe(**base)
    base.update(
        {
            "nvidia_smi_available": True,
            "gpu_name": parsed["name"],
            "driver_version": parsed["driver_version"],
            "total_vram_gib": _to_gib(parsed["memory.total"]),
            "used_vram_gib": _to_gib(parsed["memory.used"]),
            "free_vram_gib": _to_gib(parsed["memory.free"]),
            "gpu_utilization_pct": _to_float(parsed["utilization.gpu"]),
            "raw_query": query_out,
        }
    )
    process_code, process_out, process_err = _run(
        [
            executable,
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    if process_code == 0:
        base["compute_processes"] = _processes(process_out)
        base["raw_processes"] = process_out
    else:
        base["raw_processes"] = process_err or process_out
    return HardwareProbe(**base)
