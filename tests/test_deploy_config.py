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
