"""Unit tests for AkshareConceptClient singleton (Task 2.1).

Covers:
- Singleton identity & _reset_singleton semantics (R7, R15)
- get_concept_list / get_concept_constituents happy path via ThreadPoolManager mock (R16)
- TokenBucket.consume_async rate limiting is invoked before each call
- CancelledError propagates (R2)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.external.akshare_concept_client import AkshareConceptClient

pytestmark = pytest.mark.unit


@pytest.fixture
def client_with_mocks():
    """Build an AkshareConceptClient with patched ThreadPoolManager and TokenBucket.

    Returns (client, mock_tpm, mock_bucket, mock_ak). The akshare module is
    lazily imported inside the client via _get_akshare; patching that method
    avoids any real akshare import during tests.
    """
    mock_ak = MagicMock()
    mock_ak.stock_board_concept_name_em.return_value = pd.DataFrame({"板块名称": ["锂电池"], "板块代码": ["BK0573"]})
    mock_ak.stock_board_concept_cons_em.return_value = pd.DataFrame({"代码": ["300750"], "名称": ["宁德时代"]})

    mock_bucket = MagicMock()
    mock_bucket.consume_async = AsyncMock()

    mock_tpm = MagicMock()
    # run_async must be awaitable and return whatever the submitted func returns.
    mock_tpm.run_async = AsyncMock(side_effect=lambda task_type, func, *args, **kwargs: func())

    with (
        patch.object(AkshareConceptClient, "_get_akshare", return_value=mock_ak),
        patch.object(AkshareConceptClient, "_build_rate_limiter", return_value=mock_bucket),
        patch("data.external.akshare_concept_client.ThreadPoolManager", return_value=mock_tpm),
    ):
        client = AkshareConceptClient()
        yield client, mock_tpm, mock_bucket, mock_ak


class TestSingletonIdentity:
    def test_same_instance_returned(self):
        c1 = AkshareConceptClient()
        c2 = AkshareConceptClient()
        assert c1 is c2

    def test_reset_singleton_clears_instance(self):
        AkshareConceptClient()
        assert AkshareConceptClient._instance is not None
        AkshareConceptClient._reset_singleton()
        assert AkshareConceptClient._instance is None

    def test_reset_singleton_creates_new_instance(self):
        c1 = AkshareConceptClient()
        AkshareConceptClient._reset_singleton()
        c2 = AkshareConceptClient()
        assert c1 is not c2

    def test_reset_singleton_clears_initialized_flag(self):
        AkshareConceptClient()
        assert AkshareConceptClient._initialized is True
        AkshareConceptClient._reset_singleton()
        assert AkshareConceptClient._initialized is False


class TestAtexitCleanup:
    def test_atexit_cleanup_no_error_when_instance_none(self):
        """_atexit_cleanup must not raise when _instance is None."""
        AkshareConceptClient._reset_singleton()
        # Should not raise
        AkshareConceptClient._atexit_cleanup()

    def test_atexit_cleanup_no_error_when_instance_present(self):
        """_atexit_cleanup must not raise when instance exists (no-op for stateless client)."""
        AkshareConceptClient()
        # Should not raise
        AkshareConceptClient._atexit_cleanup()


class TestGetConceptList:
    @pytest.mark.asyncio
    async def test_returns_dataframe(self, client_with_mocks):
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        df = await client.get_concept_list()
        assert isinstance(df, pd.DataFrame)
        assert "板块名称" in df.columns
        mock_ak.stock_board_concept_name_em.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_rate_limiter_consumed_before_call(self, client_with_mocks):
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        await client.get_concept_list()
        mock_bucket.consume_async.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_submitted_to_io_pool(self, client_with_mocks):
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        await client.get_concept_list()
        mock_tpm.run_async.assert_awaited_once()
        # First positional arg is TaskType.IO (R16 compliance)
        from utils.thread_pool import TaskType

        args, _ = mock_tpm.run_async.call_args
        assert args[0] is TaskType.IO


class TestGetConceptConstituents:
    @pytest.mark.asyncio
    async def test_returns_dataframe(self, client_with_mocks):
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        df = await client.get_concept_constituents(symbol="锂电池")
        assert isinstance(df, pd.DataFrame)
        assert "代码" in df.columns
        mock_ak.stock_board_concept_cons_em.assert_called_once_with(symbol="锂电池")

    @pytest.mark.asyncio
    async def test_rate_limiter_consumed_before_call(self, client_with_mocks):
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        await client.get_concept_constituents(symbol="锂电池")
        mock_bucket.consume_async.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_symbol_passed_through(self, client_with_mocks):
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        await client.get_concept_constituents(symbol="光伏概念")
        mock_ak.stock_board_concept_cons_em.assert_called_once_with(symbol="光伏概念")


class TestRateLimiterConfig:
    def test_rate_limiter_uses_1_qps_capacity_2(self):
        """TokenBucket must be configured for 1 QPS with capacity 2 (burst absorption)."""
        from utils.rate_limiter import TokenBucket

        client = AkshareConceptClient()
        bucket = client._rate_limiter
        assert isinstance(bucket, TokenBucket)
        assert bucket.original_rate == 1.0
        assert bucket.capacity == 2.0


class TestCancelledErrorPropagation:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_consume_async(self, client_with_mocks):
        """R2: asyncio.CancelledError must propagate, not be swallowed."""
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        import asyncio

        mock_bucket.consume_async = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await client.get_concept_list()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_run_async(self, client_with_mocks):
        """R2: CancelledError from ThreadPoolManager.run_async must propagate."""
        client, mock_tpm, mock_bucket, mock_ak = client_with_mocks
        import asyncio

        mock_tpm.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await client.get_concept_list()
