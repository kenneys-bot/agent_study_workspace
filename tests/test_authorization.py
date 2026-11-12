"""授权语义单元测试（M2-02）：越权 403、不可见资源 404 且不泄露存在性。

seam：``ensure_scope`` / ``ensure_role`` / ``ensure_resource_visible``——未来
业务路由通过它们做授权，所以这里测的是路由将要依赖的公开契约，不是私有
实现。本文件不连数据库。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bank_assistant.api.errors import ApiError
from bank_assistant.security.authorization import (
    ensure_resource_visible,
    ensure_role,
    ensure_scope,
)
from bank_assistant.security.identity import Identity, Role

IDENTITY = Identity(
    user_id=uuid4(),
    username="agent-a",
    role=Role.AGENT,
    scope_id="scope-a",
)


def test_same_scope_is_allowed():
    assert ensure_scope(IDENTITY, "scope-a") is None


def test_cross_scope_is_rejected_with_403():
    with pytest.raises(ApiError) as caught:
        ensure_scope(IDENTITY, "scope-b")

    assert caught.value.status_code == 403
    assert caught.value.code == "FORBIDDEN"


def test_missing_role_is_rejected_with_403():
    with pytest.raises(ApiError) as caught:
        ensure_role(IDENTITY, Role.REVIEWER, Role.ADMIN)

    assert caught.value.status_code == 403
    assert caught.value.code == "FORBIDDEN"


def test_allowed_role_passes():
    assert ensure_role(IDENTITY, Role.AGENT, Role.ADMIN) is None


def test_same_scope_resource_is_visible():
    resource = SimpleNamespace(scope_id="scope-a")

    assert ensure_resource_visible(resource, IDENTITY) is None


def test_missing_resource_is_404():
    with pytest.raises(ApiError) as caught:
        ensure_resource_visible(None, IDENTITY)

    assert caught.value.status_code == 404
    assert caught.value.code == "NOT_FOUND"


def test_foreign_scope_resource_is_indistinguishable_from_missing():
    def failure(resource) -> tuple[int, str, str]:
        with pytest.raises(ApiError) as caught:
            ensure_resource_visible(resource, IDENTITY)
        error = caught.value
        return error.status_code, error.code, error.message

    missing = failure(None)
    foreign = failure(SimpleNamespace(scope_id="scope-b"))

    assert missing == foreign
    assert missing[0] == 404
