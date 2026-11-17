"""Unit tests for the PostgreSQL advisory-lock migration mutex.

These tests model the lock protocol (try-lock polling, bounded wait, release)
against a fake DBAPI connection; behaviour against a real PostgreSQL session is
covered by the opt-in integration test in
``tests/integration/test_migration_advisory_lock.py``.
"""

import asyncio

import pytest

from data.persistence import migration_lock
from data.persistence.migration_lock import (
    DEFAULT_LOCK_TIMEOUT_S,
    LOCK_TIMEOUT_ENV_VAR,
    MIGRATION_ADVISORY_LOCK_KEY,
    MigrationLockTimeoutError,
    acquire_migration_lock,
    release_migration_lock,
    resolve_lock_timeout,
)

pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeConnection:
    """Records SQL and replays scripted ``pg_try_advisory_lock`` answers.

    An item may be an exception instance, which is raised instead of returned
    (used to model cancellation while waiting).
    """

    def __init__(self, attempts: list, *, release_error: BaseException | None = None):
        self._attempts = list(attempts)
        self._release_error = release_error
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "pg_try_advisory_lock" in sql:
            outcome = self._attempts.pop(0) if self._attempts else False
            if isinstance(outcome, BaseException):
                raise outcome
            return _FakeResult(outcome)
        if self._release_error is not None:
            raise self._release_error
        return _FakeResult(True)


class TestLockKey:
    def test_key_fits_postgres_bigint_and_is_positive(self):
        assert 0 < MIGRATION_ADVISORY_LOCK_KEY < 2**63

    def test_acquire_and_release_use_the_same_key(self):
        assert MIGRATION_ADVISORY_LOCK_KEY == 0x4153544F434B4D47


class TestResolveLockTimeout:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv(LOCK_TIMEOUT_ENV_VAR, raising=False)
        assert resolve_lock_timeout() == DEFAULT_LOCK_TIMEOUT_S

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(LOCK_TIMEOUT_ENV_VAR, "42.5")
        assert resolve_lock_timeout() == 42.5

    def test_invalid_env_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv(LOCK_TIMEOUT_ENV_VAR, "not-a-number")
        with caplog.at_level("WARNING", logger=migration_lock.__name__):
            assert resolve_lock_timeout() == DEFAULT_LOCK_TIMEOUT_S
        assert LOCK_TIMEOUT_ENV_VAR in caplog.text

    def test_explicit_argument_beats_env(self, monkeypatch):
        monkeypatch.setenv(LOCK_TIMEOUT_ENV_VAR, "10")
        assert resolve_lock_timeout(0.0) == 0.0

    def test_negative_budget_clamped_to_zero(self):
        assert resolve_lock_timeout(-5) == 0.0

    def test_non_finite_budget_falls_back_to_default(self, monkeypatch):
        # nan/inf 会破坏 deadline 比较造成无限等待
        assert resolve_lock_timeout(float("nan")) == DEFAULT_LOCK_TIMEOUT_S
        assert resolve_lock_timeout(float("inf")) == DEFAULT_LOCK_TIMEOUT_S

        monkeypatch.setenv(LOCK_TIMEOUT_ENV_VAR, "inf")
        assert resolve_lock_timeout() == DEFAULT_LOCK_TIMEOUT_S


class TestAcquireMigrationLock:
    @pytest.mark.asyncio
    async def test_acquires_on_first_attempt(self):
        conn = _FakeConnection([True])

        await acquire_migration_lock(conn, timeout_s=0.0)

        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "pg_try_advisory_lock" in conn.calls[0][0]
        assert params == {"lock_key": MIGRATION_ADVISORY_LOCK_KEY}
        assert "pg_try_advisory_lock" in sql

    @pytest.mark.asyncio
    async def test_retries_until_peer_releases(self):
        conn = _FakeConnection([False, False, True])

        await acquire_migration_lock(conn, timeout_s=5.0, poll_interval_s=0)

        assert len(conn.calls) == 3
        assert all("pg_try_advisory_lock" in conn.calls[i][0] for i in range(len(conn.calls)))

    @pytest.mark.asyncio
    async def test_timeout_raises_without_starting_migration(self):
        conn = _FakeConnection([False, False, False])

        with pytest.raises(MigrationLockTimeoutError) as exc_info:
            await acquire_migration_lock(conn, timeout_s=0.0, poll_interval_s=0)

        assert exc_info.value.timeout_s == 0.0
        assert LOCK_TIMEOUT_ENV_VAR in str(exc_info.value)
        # One probe only: the budget is exhausted before any retry sleep.
        assert len(conn.calls) == 1
        assert "pg_try_advisory_lock" in conn.calls[0][0]

    @pytest.mark.asyncio
    async def test_cancellation_while_waiting_propagates(self):
        conn = _FakeConnection([False, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await acquire_migration_lock(conn, timeout_s=5.0, poll_interval_s=0)

        # R2: cancellation must not be converted into a timeout/acquired result.
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert len(conn.calls) == 2
        assert "pg_try_advisory_lock" in conn.calls[1][0]


class TestReleaseMigrationLock:
    @pytest.mark.asyncio
    async def test_release_calls_advisory_unlock_with_same_key(self):
        conn = _FakeConnection([True])

        await release_migration_lock(conn)

        assert len(conn.calls) == 1
        sql, params = conn.calls[0]
        assert "pg_advisory_unlock" in conn.calls[0][0]
        assert params == {"lock_key": MIGRATION_ADVISORY_LOCK_KEY}
        assert "pg_advisory_unlock" in sql

    @pytest.mark.asyncio
    async def test_release_failure_does_not_mask_migration_error(self, caplog):
        conn = _FakeConnection([True], release_error=RuntimeError("connection lost"))

        with caplog.at_level("WARNING", logger=migration_lock.__name__):
            await release_migration_lock(conn)

        assert "Failed to release migration lock" in caplog.text

    @pytest.mark.asyncio
    async def test_release_cancellation_propagates(self):
        conn = _FakeConnection([True], release_error=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await release_migration_lock(conn)

        # R2: release-side cancellation propagates verbatim, never downgraded.
        assert isinstance(exc_info.value, asyncio.CancelledError)
