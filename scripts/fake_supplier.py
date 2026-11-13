#!/usr/bin/env python
"""受控假模型供应商（T39 / #39，供应商故障演练）。

只用于**本地临时 API 实例**的故障注入，不触碰线上部署。提供 OpenAI 兼容的
``POST /v1/chat/completions``，通过 ``GET /__mode?mode=...`` 切换下一次请求的
行为：

- ``ok``：返回合法结构化 JSON（仅用于自检）；
- ``rate_limit``：HTTP 429（``RateLimitError`` → 网关 ``RATE_LIMITED``）；
- ``timeout``：睡眠超过客户端超时（``APITimeoutError`` → ``TIMEOUT``）；
- ``invalid_json``：HTTP 200 但 content 不是 JSON（``INVALID_STRUCTURE``）。

连接被拒（``APIConnectionError`` → ``PROVIDER_UNAVAILABLE``）由演练脚本直接
停止本服务后发起请求来制造，不伪造异常。

运行：``python scripts/fake_supplier.py --port 19999``；``GET /__stats`` 返回
各模式请求计数与最近一次响应状态，便于把“请求 → 响应”写进报告。
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

MODES = ("ok", "rate_limit", "timeout", "invalid_json")
_OK_CONTENT = json.dumps(
    {
        "status": "needs_human",
        "answer": "",
        "citations": [],
        "missing_information": ["fake-supplier：仅用于故障演练，不产生业务答复"],
    },
    ensure_ascii=False,
)


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "ok"
        self.requests: dict[str, int] = {}
        self.last_status: dict[str, int] = {}

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"未知模式 {mode!r}；可选 {MODES}")
        with self.lock:
            self.mode = mode

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "mode": self.mode,
                "requests": dict(self.requests),
                "last_status": dict(self.last_status),
            }

    def record(self, mode: str, status: int) -> None:
        with self.lock:
            self.requests[mode] = self.requests.get(mode, 0) + 1
            self.last_status[mode] = status


def _chat_completion_body(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-t39-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "t39-fake-supplier",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


class _Handler(BaseHTTPRequestHandler):
    state: _State
    timeout_seconds: float

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # 演练日志由脚本收集，不在 stdout 刷屏；保留可读的服务器错误。
        pass

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        parsed = urlparse(self.path)
        if parsed.path == "/__stats":
            self._send_json(200, self.state.snapshot())
            return
        if parsed.path == "/__mode":
            query = parse_qs(parsed.query)
            mode = (query.get("mode") or ["ok"])[0]
            try:
                self.state.set_mode(mode)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, self.state.snapshot())
            return
        if parsed.path in ("/health", "/v1/models"):
            self._send_json(200, {"status": "ok", "mode": self.state.snapshot()["mode"]})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/__mode":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
                mode = str(payload.get("mode", "ok"))
                self.state.set_mode(mode)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, self.state.snapshot())
            return
        if not parsed.path.endswith("/chat/completions"):
            self._send_json(404, {"error": "not found"})
            return

        mode = self.state.snapshot()["mode"]
        if mode == "rate_limit":
            self.state.record(mode, 429)
            self._send_json(
                429,
                {
                    "error": {
                        "message": "t39 fake supplier: rate limit exceeded",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                },
            )
            return
        if mode == "timeout":
            time.sleep(self.timeout_seconds)
            self.state.record(mode, 200)
            self._send_json(200, _chat_completion_body(_OK_CONTENT))
            return
        if mode == "invalid_json":
            self.state.record(mode, 200)
            self._send_json(
                200,
                _chat_completion_body("这不是 JSON：t39 fake supplier invalid body"),
            )
            return
        self.state.record(mode, 200)
        self._send_json(200, _chat_completion_body(_OK_CONTENT))


class FakeSupplierServer:
    """可编程的 OpenAI 兼容假供应商；线程内启动/停止，便于演练脚本控制。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 19999) -> None:
        self.state = _State()
        handler = type(
            "_BoundHandler",
            (_Handler,),
            {"state": self.state, "timeout_seconds": 10.0},
        )
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="t39-fake-supplier", daemon=True
        )
        self.host = host
        self.port = port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def set_mode(self, mode: str) -> None:
        self.state.set_mode(mode)

    def stats(self) -> dict[str, Any]:
        return self.state.snapshot()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T39 受控假模型供应商")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19999)
    args = parser.parse_args(argv)
    server = FakeSupplierServer(host=args.host, port=args.port)
    server.start()
    print(f"fake-supplier: {server.base_url}（Ctrl-C 停止）", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
