"""签名令牌单元测试（M2-02）。

seam：``create_access_token`` / ``decode_access_token``。测试不读数据库；
独立事实来源是 JWT 规范与 PyJWT——用库直接解出 ``sub``，证明令牌真的由
HS256 与给定密钥签名，而不是被测代码自己编码后又自己解回来。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from bank_assistant.config.settings import settings
from bank_assistant.security.identity import Identity
from bank_assistant.security.tokens import (
    SecretKeyMissingError,
    TokenError,
    create_access_token,
    decode_access_token,
)

UNIT_SECRET = "unit-test-signing-secret-0123456789abcdef"
IDENTITY = Identity(
    user_id=uuid4(),
    username="agent-a",
    role="agent",
    scope_id="scope-a",
)


def test_roundtrip_returns_only_server_side_user_id():
    token = create_access_token(IDENTITY, secret=UNIT_SECRET)

    assert decode_access_token(token, secret=UNIT_SECRET) == IDENTITY.user_id


def test_token_is_a_real_hs256_jwt_signed_with_the_secret():
    token = create_access_token(IDENTITY, secret=UNIT_SECRET)

    claims = jwt.decode(token, UNIT_SECRET, algorithms=["HS256"])

    assert claims["sub"] == str(IDENTITY.user_id)
    assert claims["exp"] > claims["iat"]
    # 身份字段不来自客户端令牌，令牌里也没有可篡改的角色/范围声明。
    assert "role" not in claims
    assert "scope_id" not in claims


def test_token_signed_with_another_key_is_rejected():
    token = create_access_token(IDENTITY, secret=UNIT_SECRET)

    with pytest.raises(TokenError):
        decode_access_token(token, secret="a-different-signing-secret-0123456789ab")


def test_tampered_signature_is_rejected():
    token = create_access_token(IDENTITY, secret=UNIT_SECRET)
    header, payload, _signature = token.split(".")

    with pytest.raises(TokenError):
        decode_access_token(f"{header}.{payload}.not-a-valid-signature", secret=UNIT_SECRET)


def test_expired_token_is_rejected():
    token = create_access_token(
        IDENTITY,
        secret=UNIT_SECRET,
        now=datetime.now(UTC) - timedelta(minutes=10),
        expires_minutes=5,
    )

    with pytest.raises(TokenError):
        decode_access_token(token, secret=UNIT_SECRET)


def test_missing_secret_fails_clearly_instead_of_inventing_one(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "   ")

    with pytest.raises(SecretKeyMissingError, match="SECRET_KEY"):
        create_access_token(IDENTITY)
