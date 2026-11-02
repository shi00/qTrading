"""R9 回归：``_parse_db_url`` 解析失败时不得回显原始 E2E_DATABASE_URL。

skip reason 会进入 pytest 报告与 CI 日志，原始 URL 可能含明文 userinfo/password。
"""

from __future__ import annotations

import pytest

from tests.e2e.test_onboarding_wizard import _parse_db_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "forbidden"),
    [
        # 普通明文密码 + 缺少 database 段 → 解析失败
        (
            "postgresql+asyncpg://postgres:s3cr3t@db.internal:5432",
            ["s3cr3t"],
        ),
        # 百分号编码的特殊字符密码 + IPv6 host（host 正则不匹配 → 解析失败）
        (
            "postgresql+asyncpg://user:p%40ss%3Aw0rd@[::1]:5432/appdb",
            ["p%40ss%3Aw0rd", "p@ss:w0rd"],
        ),
        # 密码含 @ 且缺少 port 段 → 解析失败
        (
            "postgresql+asyncpg://bob:p@ss!#word@db/appdb",
            ["p@ss!#word"],
        ),
        # 无密码（仅 userinfo），解析失败
        (
            "postgresql+asyncpg://alice@db:5432/appdb",
            ["alice"],
        ),
        # 缺少 scheme、含明文密码 → 解析失败
        (
            "postgres:alice:s3cr3t@db/appdb",
            ["s3cr3t"],
        ),
    ],
)
def test_parse_db_url_failure_skip_reason_omits_raw_url(url: str, forbidden: list[str]) -> None:
    with pytest.raises(pytest.skip.Exception) as exc_info:
        _parse_db_url(url)

    message = str(exc_info.value)
    assert "Cannot parse E2E_DATABASE_URL" in message
    assert url not in message
    for value in forbidden:
        assert value not in message


def test_parse_db_url_success_semantics_unchanged() -> None:
    """成功路径保持原有解析与解码语义（本次仅改失败分支消息）。"""
    parsed = _parse_db_url("postgresql+asyncpg://alice:pa%40ss@db:5432/appdb")
    assert parsed == {
        "user": "alice",
        "password": "pa@ss",
        "host": "db",
        "port": "5432",
        "database": "appdb",
    }
