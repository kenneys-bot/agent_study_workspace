"""密码哈希单元测试（M2-02）。

seam：``hash_password`` / ``verify_password`` 公开函数。测试只断言外部可见
行为——库里拿到的是不可逆哈希、正确密码能通过、错误密码不能通过——不关心
使用哪种 KDF 内部结构。独立事实来源：Argon2 编码前缀 ``$argon2`` 是库文档
规定的存储格式，而不是由被测代码计算出的期望值。
"""

from bank_assistant.security.passwords import hash_password, verify_password

DEMO_PASSWORD = "demo-pass-not-a-real-secret"


def test_password_is_not_stored_as_plaintext():
    stored = hash_password(DEMO_PASSWORD)

    assert stored != DEMO_PASSWORD
    assert DEMO_PASSWORD not in stored
    # Argon2 编码串以 $argon2 开头；这是哈希库的公开存储契约。
    assert stored.startswith("$argon2")


def test_verify_accepts_correct_password():
    stored = hash_password(DEMO_PASSWORD)

    assert verify_password(DEMO_PASSWORD, stored) is True


def test_verify_rejects_wrong_password():
    stored = hash_password(DEMO_PASSWORD)

    assert verify_password("wrong-password", stored) is False


def test_same_password_gets_distinct_hashes():
    """每个哈希带独立盐，不能让相同密码产生相同行值。"""
    first = hash_password(DEMO_PASSWORD)
    second = hash_password(DEMO_PASSWORD)

    assert first != second
    assert verify_password(DEMO_PASSWORD, first) is True
    assert verify_password(DEMO_PASSWORD, second) is True
