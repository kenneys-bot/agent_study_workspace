"""密码哈希：只保存成熟 KDF 的输出，禁止明文或自制加密（M2-02）。

使用 Argon2id（``pwdlib[argon2]`` 的推荐配置）。哈希串自带盐与参数，可直接
存进 ``users.password_hash``；验证失败或哈希格式非法都一律返回 ``False``，
不把底层异常细节暴露给调用方。
"""

from __future__ import annotations

from pwdlib import PasswordHash

# 推荐配置即 Argon2id；单例复用，避免每个请求重复构造。
_PASSWORD_HASH = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """返回可直接入库的 Argon2 哈希串。"""
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与存储哈希是否匹配；哈希损坏时按失败处理。"""
    try:
        return _PASSWORD_HASH.verify(password, password_hash)
    except Exception:
        return False
