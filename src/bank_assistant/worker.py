"""质检 worker 入口：``python -m bank_assistant.worker``（T18，M4-03；契约 §3）。

worker 通过既有租约队列（:mod:`bank_assistant.infra.task_queue`）领取
``inspection`` 类型任务，交给 :class:`InspectionRunner` 执行。设计边界：

- 每个任务的处理都在短事务之外调用模型；租约到期后任务可被其他 worker 重新
  领取（CT-11），旧 worker 的最终化会被租约复核拒绝；
- 进程崩溃不会留下永久 ``running``：租约到期被重领后幂等收尾；若任务已在
  崩溃中达到尝试上限，:meth:`Worker.reconcile` 会把运行收口为 ``failed``；
- 失败一律写入稳定 ``error_code``（模型失败、输出结构非法、证据无效、会话
  变更等），重试上限沿用 ``TASK_MAX_ATTEMPTS``（默认 3）；
- worker 不实现人工复核与报告导出（#20/#21）。

运行：``make worker``（等价 ``uv run python -m bank_assistant.worker``）。
``--once`` 用于本地验证：抽干当前队列后退出，不作为常驻服务。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from bank_assistant.config.settings import settings
from bank_assistant.core.errors import CapabilityUnavailableError
from bank_assistant.core.model_gateway import ModelGateway
from bank_assistant.infra.db import get_session_factory
from bank_assistant.infra.task_queue import TaskQueue
from bank_assistant.inspections.runner import ExecutionOutcome, InspectionRunner
from bank_assistant.inspections.schema import InspectionErrorCode, InspectionStatus
from bank_assistant.inspections.service import TASK_TYPE

logger = logging.getLogger(__name__)

__all__ = ["Worker", "live_worker_pids", "main"]

DEFAULT_POLL_SECONDS = 1.0
WORKER_MARKER = b"bank_assistant.worker"


def live_worker_pids(proc_root: Path = Path("/proc")) -> list[int]:
    """进程存活检查（T41）：扫描 ``/proc/<pid>/cmdline`` 找常驻 worker。

    worker 没有 HTTP 端点，compose 的 healthcheck 需要一条不依赖 ``pgrep``/
    ``procps`` 的命令（运行时镜像是 slim，未装 procps）。判据是 argv 中恰好有
    ``bank_assistant.worker`` 这一项；**排除探针自身**（探针命令行同样包含该串），
    否则 worker 已死时也会误报存活。读不到 ``cmdline``（进程刚退出、权限不足）
    的条目直接跳过，不让探针因竞态崩溃。
    """

    own_pid = os.getpid()
    found: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own_pid:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if WORKER_MARKER in argv:
            found.append(pid)
    return sorted(found)


class Worker:
    """领取并执行质检任务；一个进程内可复用于测试与常驻运行。"""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        queue: TaskQueue | None = None,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
        session_factory: sessionmaker[Session] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.worker_id = worker_id or f"inspection-worker-{uuid4().hex[:8]}"
        self.lease_seconds = (
            settings.TASK_DEFAULT_LEASE_SECONDS
            if lease_seconds is None
            else lease_seconds
        )
        self.queue = queue or TaskQueue(session_factory=session_factory)
        self.runner = InspectionRunner(
            gateway=gateway,
            session_factory=session_factory,
            now=now,
            lease_seconds=self.lease_seconds,
        )

    def reconcile(self) -> int:
        """收口「任务已终态、运行非终态」的运行，避免永久 running。"""
        return self.runner.reconcile()

    def run_once(self) -> ExecutionOutcome | None:
        """领取并处理一个任务；没有可领取任务时返回 ``None``。"""
        task = self.queue.claim_next(
            TASK_TYPE, self.lease_seconds, owner=self.worker_id
        )
        if task is None:
            # 领取时的 sweep 可能刚把达到上限的任务终结为 failed，这里收口其运行。
            self.reconcile()
            return None

        try:
            outcome = self.runner.process(task, owner=self.worker_id, queue=self.queue)
        except Exception as exc:  # 未预期错误也必须留下显式失败，绝不永久 running
            logger.exception(
                "inspection_worker_error task_id=%s inspection=%s",
                task.task_id,
                task.payload_ref,
            )
            from bank_assistant.inspections.service import parse_inspection_payload_ref

            return self.runner.record_failure(
                task_id=task.task_id,
                inspection_id=parse_inspection_payload_ref(task.payload_ref),
                owner=self.worker_id,
                queue=self.queue,
                error_code=InspectionErrorCode.WORKER_ERROR,
                message=f"worker 未预期错误：{type(exc).__name__}",
                retryable=True,
            )
        logger.info(
            "inspection_worker_done worker=%s task=%s inspection=%s status=%s "
            "findings=%d error=%s",
            self.worker_id,
            task.task_id,
            outcome.inspection_id,
            outcome.status,
            outcome.findings_count,
            outcome.error_code,
        )
        return outcome

    def drain(self, *, max_tasks: int | None = None) -> list[ExecutionOutcome]:
        """处理到队列为空（或达到上限）；``--once`` 与测试复用同一路径。"""
        processed: list[ExecutionOutcome] = []
        while max_tasks is None or len(processed) < max_tasks:
            outcome = self.run_once()
            if outcome is None:
                break
            processed.append(outcome)
        return processed

    def run_forever(self, *, poll_seconds: float = DEFAULT_POLL_SECONDS) -> None:
        logger.info(
            "inspection_worker_start worker=%s lease_seconds=%d mode=%s",
            self.worker_id,
            self.lease_seconds,
            self.gateway.mode,
        )
        while True:
            outcome = self.run_once()
            if outcome is None:
                time.sleep(poll_seconds)
            elif outcome.status == InspectionStatus.FAILED.value:
                logger.warning(
                    "inspection_failed inspection=%s error=%s",
                    outcome.inspection_id,
                    outcome.error_code,
                )


def _build_gateway() -> ModelGateway:
    """生产入口按环境变量构建统一模型适配层；没有凭据时显式失败。"""
    return ModelGateway.from_settings()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bank_assistant.worker",
        description="质检任务 worker：领取租约任务、按规则快照执行质检",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="抽干当前队列后退出（本地验证用，不是常驻服务）",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        help="空闲轮询间隔（秒）",
    )
    parser.add_argument("--worker-id", default=None, help="租约持有者标识")
    parser.add_argument("--lease-seconds", type=int, default=None, help="任务租约秒数")
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="只做进程存活检查（compose healthcheck 用），0=存活 1=没有 worker",
    )
    args = parser.parse_args(argv)

    if args.healthcheck:
        # 不构建模型网关、不连数据库：探针只回答「进程还在不在」。
        pids = live_worker_pids()
        if not pids:
            print("worker process not found", file=sys.stderr)
            return 1
        print(f"worker alive pids={','.join(str(pid) for pid in pids)}")
        return 0

    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        gateway = _build_gateway()
    except CapabilityUnavailableError as exc:
        logger.error("inspection_worker_unavailable: %s", exc)
        return 2

    worker = Worker(
        gateway=gateway,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )
    if args.once:
        processed = worker.drain()
        logger.info("inspection_worker_once processed=%d", len(processed))
        return 0
    try:
        worker.run_forever(poll_seconds=args.poll_seconds)
    except KeyboardInterrupt:
        logger.info("inspection_worker_stopped worker=%s", worker.worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
