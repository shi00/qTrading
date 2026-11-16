"""``tests.integration._real_pg_gate`` 的条件分支单测（D21-OTHER-010）。

不连接真实 PG：用伪 env 与伪连接异常验证「本地可选 skip / required-real 失败」边界。
"""

import asyncpg
import pytest

from tests.integration._real_pg_gate import (
    describe_connect_failure,
    is_required_real_pg,
    should_skip_connect_failure,
)

pytestmark = pytest.mark.unit


class TestIsRequiredRealPg:
    def test_github_actions_is_required(self):
        assert is_required_real_pg({"GITHUB_ACTIONS": "true"}) is True

    def test_local_without_flags_is_optional(self):
        assert is_required_real_pg({"GITHUB_ACTIONS": "false", "CI": "true"}) is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_explicit_opt_in(self, value):
        assert is_required_real_pg({"TEST_DB_REQUIRED": value}) is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no"])
    def test_explicit_opt_out_is_optional(self, value):
        assert is_required_real_pg({"TEST_DB_REQUIRED": value}) is False


class TestShouldSkipConnectFailure:
    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionRefusedError(111, "Connection refused"),
            TimeoutError("connect timeout"),
            asyncpg.CannotConnectNowError("the database system is starting up"),
        ],
    )
    def test_optional_local_without_service_may_skip(self, exc):
        assert should_skip_connect_failure(exc, required=False) is True

    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionRefusedError(111, "Connection refused"),
            TimeoutError("connect timeout"),
            asyncpg.CannotConnectNowError("the database system is starting up"),
        ],
    )
    def test_required_real_never_skips_unreachable(self, exc):
        assert should_skip_connect_failure(exc, required=True) is False

    @pytest.mark.parametrize(
        "exc",
        [
            asyncpg.InvalidPasswordError('password authentication failed for user "postgres"'),
            asyncpg.InvalidAuthorizationSpecificationError("no pg_hba.conf entry"),
            asyncpg.InvalidCatalogNameError('database "missing" does not exist'),
            asyncpg.ClientConfigurationError("invalid port"),
            asyncpg.ProtocolViolationError("unexpected message"),
            ValueError("bad port"),
        ],
    )
    def test_auth_config_protocol_errors_always_raise(self, exc):
        assert should_skip_connect_failure(exc, required=False) is False
        assert should_skip_connect_failure(exc, required=True) is False


class TestDescribeConnectFailure:
    def test_contains_type_and_sqlstate_but_not_secret(self):
        exc = asyncpg.InvalidPasswordError('password authentication failed for user "postgres"')
        summary = describe_connect_failure(exc)
        assert "InvalidPasswordError" in summary
        assert "28P01" in summary
        assert "postgres" not in summary

    def test_local_oserror_has_no_sqlstate(self):
        summary = describe_connect_failure(ConnectionRefusedError(111, "Connection refused"))
        assert summary == "ConnectionRefusedError"
