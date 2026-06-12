import pytest
from unittest.mock import MagicMock, patch
from data.external.tushare_client import TushareClient
from utils.correlation import set_correlation_id, get_correlation_id, clear_correlation_id


class TestTushareFixes:
    """验证 OBS-010 跨线程 correlation_id 穿透"""

    @pytest.mark.asyncio
    async def test_correlation_id_propagation_to_thread(self):
        # Mock ConfigHandler and Tushare connection to prevent real network calls
        with (
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
            patch("data.external.tushare_client.ts") as mock_ts,
        ):
            mock_ts.pro_api.return_value = MagicMock()
            mock_ch.get_token.return_value = "mock_token"
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 120

            client = TushareClient(token="mock_token")
            client.pro = MagicMock()

            test_cid = "tushare_prop_test"
            set_correlation_id(test_cid)

            def mock_api_func(**kwargs):
                # 此时运行在 ThreadPoolManager().io_pool 线程中，应能获取到父线程的 correlation_id
                cid_in_thread = get_correlation_id()
                assert cid_in_thread == test_cid
                import pandas as pd

                return pd.DataFrame({"col": [1]})

            try:
                # 执行 _handle_api_call，它会将 mock_api_func 放入 executor 中并用 contextvars.copy_context().run 包裹
                res = await client._handle_api_call(mock_api_func)
                assert res is not None
                assert not res.empty
            finally:
                clear_correlation_id()
