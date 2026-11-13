"""部署文件与恢复闸门的回归测试（M7-01/M7-05，T29）。

这些断言只读文本/调用纯函数，不连服务器、不连数据库：
- `deploy/compose.app.yml` 必须保持“回环端口 + 不可变镜像 + restart + 存活探针”的形状，
  防止后续改动把 API 暴露到局域网或让服务器重建源码；
- `scripts/_ops.sh` 的恢复目标闸门只接受 `*_restore_check`，绝不等于源库。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_APP = REPO_ROOT / "deploy" / "compose.app.yml"
OPS_SH = REPO_ROOT / "scripts" / "_ops.sh"


def load_compose() -> dict:
    return yaml.safe_load(COMPOSE_APP.read_text(encoding="utf-8"))


def test_compose_app_uses_immutable_image_and_loopback_binding():
    """镜像由参数注入（不 build）；host 网络下 uvicorn 必须显式绑 127.0.0.1。"""
    api = load_compose()["services"]["api"]

    assert "build" not in api
    assert "${API_IMAGE:" in api["image"]
    # host 网络是为了让 DATABASE_URL 的 127.0.0.1:5432 指向宿主；
    # 端口隔离改由 command 的 --host 127.0.0.1 保证，不能出现 0.0.0.0。
    assert api["network_mode"] == "host"
    assert "ports" not in api
    command = [str(part) for part in api["command"]]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert "0.0.0.0" not in command
    assert api["restart"] == "unless-stopped"
    assert api["container_name"] == "bank-assistant-api"


def test_compose_app_reads_config_outside_repo_and_checks_liveness():
    """配置来自仓库外 env 文件；健康检查命中 /health/live。"""
    api = load_compose()["services"]["api"]

    env_file = api["env_file"][0]
    assert env_file["required"] is True
    assert "${APP_ENV_FILE:" in env_file["path"]
    assert "/health/live" in " ".join(str(part) for part in api["healthcheck"]["test"])

    volumes = " ".join(api["volumes"])
    assert "${APP_DATA_DIR:" in volumes
    assert "${APP_ARTIFACT_DIR:" in volumes


def test_compose_app_pins_git_sha():
    """部署必须注入代码 SHA，版本页才能自证镜像身份。"""
    environment = load_compose()["services"]["api"]["environment"]
    assert "${APP_GIT_SHA:" in environment["GIT_SHA"]


def _run_guard(target: str, source: str) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n"
        f"source '{OPS_SH}'\n"
        f"require_restore_target '{target}' '{source}'\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_restore_target_guard_accepts_only_restore_check_databases():
    """白名单：只允许 bank_assistant_*_restore_check，且不能等于源库。"""
    assert _run_guard("bank_assistant_demo_restore_check", "bank_assistant_demo").returncode == 0
    assert _run_guard("bank_assistant_dev_restore_check", "bank_assistant_dev").returncode == 0

    for target, source in [
        ("bank_assistant_demo", "bank_assistant_demo"),  # 直连源库
        ("bank_assistant_demo_restore_check", "bank_assistant_demo_restore_check"),
        ("bank_assistant_restore", "bank_assistant_demo"),
        ("bank_assistant_dev", "bank_assistant_demo"),
        ("postgres", "bank_assistant_demo"),
        ("bank_assistant_demo_restore_check; drop table users", "bank_assistant_demo"),
    ]:
        assert _run_guard(target, source).returncode != 0, target


def test_compose_app_runs_api_and_worker_from_same_immutable_image():
    """T41/#41：compose 必须同时编排 api + worker，worker 不发布端口、同一镜像。"""
    services = load_compose()["services"]
    assert set(services) == {"api", "worker"}
    api, worker = services["api"], services["worker"]

    # 同一不可变镜像：worker 不能 build、不能再打别的 tag。
    assert worker["image"] == api["image"]
    assert "build" not in worker
    assert worker["restart"] == "unless-stopped"
    # 常驻进程没有 HTTP 入口：host 网络下不写 ports，也就不可能对外发布端口。
    assert "ports" not in worker
    assert worker["network_mode"] == api["network_mode"] == "host"
    assert worker["container_name"] == "bank-assistant-worker"

    command = [str(part) for part in worker["command"]]
    assert command == ["python", "-m", "bank_assistant.worker"]

    # 配置与挂载和 api 完全一致（同一个仓库外 env、同一 data/artifacts）。
    assert worker["env_file"] == api["env_file"]
    assert worker["volumes"] == api["volumes"]
    assert worker["environment"]["GIT_SHA"] == api["environment"]["GIT_SHA"]

    healthcheck = " ".join(str(part) for part in worker["healthcheck"]["test"])
    assert "bank_assistant.worker" in healthcheck
    assert "--healthcheck" in healthcheck


def test_live_worker_pids_finds_worker_and_ignores_probe_itself(tmp_path):
    """worker 存活探针按 argv 精确匹配，且必须排除探针自身（否则永远 healthy）。"""
    import os

    from bank_assistant.worker import live_worker_pids

    def write_pid(pid: int, argv: list[bytes]) -> None:
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "cmdline").write_bytes(b"\0".join(argv) + b"\0")

    write_pid(101, [b"/usr/local/bin/python", b"-m", b"bank_assistant.worker"])
    write_pid(103, [b"uvicorn", b"bank_assistant.main:app"])
    write_pid(104, [b"python", b"-c", b"print('bank_assistant.worker')"])
    write_pid(os.getpid(), [b"python", b"-m", b"bank_assistant.worker"])
    (tmp_path / "not-a-pid").mkdir()
    (tmp_path / "105").mkdir()  # 进程已退出、cmdline 读不到：跳过而不是崩溃

    assert live_worker_pids(tmp_path) == [101]


def test_live_worker_pids_returns_empty_without_proc(tmp_path):
    from bank_assistant.worker import live_worker_pids

    assert live_worker_pids(tmp_path / "missing") == []
