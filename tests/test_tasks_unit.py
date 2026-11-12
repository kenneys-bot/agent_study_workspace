"""任务与幂等的纯函数单元测试（不依赖数据库）。

seam：
- ``canonical_request_hash``：请求输入的规范化哈希（键序、Unicode 稳定）；
- ``resolve_binding``：同一幂等键再次出现时判定 replay / conflict；
- ``lease_deadline`` / ``is_lease_active`` / ``can_claim``：租约与重领判定；
- ``next_status_after_failure``：失败后重排队还是终结。

这些规则是 CT-05/CT-06/CT-11 的核心判定，放在纯函数里，数据库只负责持久化。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bank_assistant.infra.idempotency import (
    BindingOutcome,
    canonical_request_hash,
    resolve_binding,
)
from bank_assistant.infra.task_queue import (
    MAX_ATTEMPTS_ERROR_CODE,
    TaskStatus,
    can_claim,
    is_lease_active,
    lease_deadline,
    next_status_after_failure,
)

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)


class TestCanonicalRequestHash:
    def test_key_order_does_not_change_the_hash(self) -> None:
        first = {"type": "inspection", "payload_ref": "conversation:demo-1"}
        second = {"payload_ref": "conversation:demo-1", "type": "inspection"}

        assert canonical_request_hash(first) == canonical_request_hash(second)

    def test_known_digest_matches_independent_reference(self) -> None:
        """期望值由独立计算给出，不调用被测函数重算。"""
        payload = {"type": "inspection", "payload_ref": "conversation:demo-1"}

        assert (
            canonical_request_hash(payload)
            == "95752325b332d3daa1094251604246cd95df233e8e588873e3d4865771fbf8cc"
        )

    def test_unicode_is_hashed_as_utf8(self) -> None:
        payload = {"payload_ref": "会话:1", "type": "inspection"}

        assert (
            canonical_request_hash(payload)
            == "948179babf4b24b311d8ff81cead5115bc447024a0bb7ed7e2544b2ec287f531"
        )

    def test_different_inputs_have_different_hashes(self) -> None:
        base = {"type": "inspection", "payload_ref": "conversation:demo-1"}
        changed = {"type": "inspection", "payload_ref": "conversation:demo-2"}

        assert canonical_request_hash(base) != canonical_request_hash(changed)

    def test_accepts_pydantic_models(self) -> None:
        from bank_assistant.api.models.task import TaskCreate

        model = TaskCreate(type="inspection", payload_ref="conversation:demo-1")

        assert canonical_request_hash(model) == canonical_request_hash(
            {"type": "inspection", "payload_ref": "conversation:demo-1"}
        )


class TestResolveBinding:
    def test_first_use_creates(self) -> None:
        assert resolve_binding(None, "hash-a") is BindingOutcome.CREATE

    def test_same_input_replays(self) -> None:
        assert resolve_binding("hash-a", "hash-a") is BindingOutcome.REPLAY

    def test_different_input_conflicts(self) -> None:
        assert resolve_binding("hash-a", "hash-b") is BindingOutcome.CONFLICT


class TestLeaseMath:
    def test_lease_deadline_is_now_plus_seconds(self) -> None:
        assert lease_deadline(NOW, 30) == NOW + timedelta(seconds=30)

    def test_non_positive_lease_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            lease_deadline(NOW, 0)
        with pytest.raises(ValueError):
            lease_deadline(NOW, -1)

    def test_lease_is_active_only_before_expiry(self) -> None:
        assert is_lease_active(NOW + timedelta(seconds=1), NOW) is True
        assert is_lease_active(NOW, NOW) is False
        assert is_lease_active(NOW - timedelta(seconds=1), NOW) is False
        assert is_lease_active(None, NOW) is False

    def test_queued_task_with_attempts_left_is_claimable(self) -> None:
        assert (
            can_claim(
                TaskStatus.QUEUED,
                None,
                attempts=0,
                max_attempts=3,
                now=NOW,
            )
            is True
        )

    def test_running_task_is_reclaimable_only_after_expiry(self) -> None:
        assert (
            can_claim(
                TaskStatus.RUNNING,
                NOW + timedelta(seconds=5),
                attempts=1,
                max_attempts=3,
                now=NOW,
            )
            is False
        )
        assert (
            can_claim(
                TaskStatus.RUNNING,
                NOW - timedelta(seconds=1),
                attempts=1,
                max_attempts=3,
                now=NOW,
            )
            is True
        )

    def test_attempts_cap_blocks_reclaim(self) -> None:
        assert (
            can_claim(
                TaskStatus.RUNNING,
                NOW - timedelta(seconds=1),
                attempts=3,
                max_attempts=3,
                now=NOW,
            )
            is False
        )

    def test_terminal_statuses_are_not_claimable(self) -> None:
        for status in (
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            assert (
                can_claim(status, None, attempts=1, max_attempts=3, now=NOW)
                is False
            )


class TestFailureTransition:
    def test_retryable_failure_requeues_below_cap(self) -> None:
        assert (
            next_status_after_failure(attempts=1, max_attempts=3, retryable=True)
            is TaskStatus.QUEUED
        )

    def test_attempts_cap_ends_in_failed(self) -> None:
        assert (
            next_status_after_failure(attempts=3, max_attempts=3, retryable=True)
            is TaskStatus.FAILED
        )

    def test_non_retryable_failure_ends_in_failed(self) -> None:
        assert (
            next_status_after_failure(attempts=1, max_attempts=3, retryable=False)
            is TaskStatus.FAILED
        )

    def test_max_attempts_error_code_is_stable(self) -> None:
        assert MAX_ATTEMPTS_ERROR_CODE == "MAX_ATTEMPTS_EXCEEDED"
