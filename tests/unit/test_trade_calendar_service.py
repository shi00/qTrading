import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.domain_services.trade_calendar_service import TradeCalendarService


def _make_service(cache_return=None, api_return=None, offline_return=None, cache_return_no_filter=None):
    mock_cache = MagicMock()

    async def _get_trade_cal(**kwargs):
        if kwargs.get("is_open") == 1:
            return cache_return
        if cache_return_no_filter is not None:
            return cache_return_no_filter
        return cache_return

    mock_cache.get_trade_cal = AsyncMock(side_effect=_get_trade_cal)
    mock_cache.save_trade_cal = AsyncMock()
    mock_cache.get_trade_cal_range = AsyncMock(return_value=(None, None))
    mock_cache.get_start_date_by_trade_days = AsyncMock(return_value=None)
    mock_cache.stock_dao = MagicMock()
    mock_cache.stock_dao.count_trade_days = AsyncMock(return_value=5)

    mock_api = MagicMock()
    mock_api.get_trade_cal = AsyncMock(return_value=api_return)

    svc = TradeCalendarService(mock_cache, mock_api)

    if offline_return is not None:
        mock_offline = MagicMock()
        mock_offline.is_trading_day = MagicMock(return_value=offline_return)
        mock_offline.get_trade_dates = MagicMock(return_value=offline_return)
        svc._offline = mock_offline

    return svc


class TestToDate:
    def test_none(self):
        svc = _make_service()
        assert svc._to_date(None) is None

    def test_date(self):
        svc = _make_service()
        d = datetime.date(2024, 6, 14)
        assert svc._to_date(d) == d

    def test_datetime(self):
        svc = _make_service()
        dt = datetime.datetime(2024, 6, 14, 15, 30)
        assert svc._to_date(dt) == datetime.date(2024, 6, 14)

    def test_string(self):
        svc = _make_service()
        assert svc._to_date("20240614") == datetime.date(2024, 6, 14)

    def test_string_with_dashes(self):
        svc = _make_service()
        assert svc._to_date("2024-06-14") == datetime.date(2024, 6, 14)

    def test_invalid_type(self):
        svc = _make_service()
        with pytest.raises(ValueError):
            svc._to_date(12345)


class TestToStr:
    def test_none(self):
        svc = _make_service()
        assert svc._to_str(None) is None

    def test_date(self):
        svc = _make_service()
        assert svc._to_str(datetime.date(2024, 6, 14)) == "20240614"


class TestEnsureDataPersisted:
    @pytest.mark.asyncio
    async def test_none_df(self):
        svc = _make_service()
        assert await svc._ensure_data_persisted(None) is False

    @pytest.mark.asyncio
    async def test_empty_df(self):
        svc = _make_service()
        assert await svc._ensure_data_persisted(pd.DataFrame()) is False

    @pytest.mark.asyncio
    async def test_success(self):
        svc = _make_service()
        df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        result = await svc._ensure_data_persisted(df)
        assert result is True
        svc._cache.save_trade_cal.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_failure(self):
        svc = _make_service()
        svc._cache.save_trade_cal = AsyncMock(side_effect=Exception("DB error"))
        df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        result = await svc._ensure_data_persisted(df)
        assert result is False


class TestFetchFromApiAndPersist:
    @pytest.mark.asyncio
    async def test_success(self):
        df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(api_return=df)
        result = await svc._fetch_from_api_and_persist(datetime.date(2024, 6, 14), datetime.date(2024, 6, 14))
        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_api_returns_none(self):
        svc = _make_service(api_return=None)
        result = await svc._fetch_from_api_and_persist(datetime.date(2024, 6, 14), datetime.date(2024, 6, 14))
        assert result is None

    @pytest.mark.asyncio
    async def test_api_exception(self):
        svc = _make_service()
        svc._api.get_trade_cal = AsyncMock(side_effect=Exception("API error"))
        result = await svc._fetch_from_api_and_persist(datetime.date(2024, 6, 14), datetime.date(2024, 6, 14))
        assert result is None


class TestEnsureCalendarRange:
    @pytest.mark.asyncio
    async def test_invalid_start(self):
        svc = _make_service()
        with pytest.raises(ValueError):
            await svc.ensure_calendar_range("invalid", "20240614")

    @pytest.mark.asyncio
    async def test_invalid_end(self):
        svc = _make_service()
        result = await svc.ensure_calendar_range("20240614", None)
        assert result is False

    @pytest.mark.asyncio
    async def test_already_covered(self):
        svc = _make_service()
        svc._cache.get_trade_cal_range = AsyncMock(
            return_value=(datetime.date(2024, 1, 1), datetime.date(2024, 12, 31))
        )
        result = await svc.ensure_calendar_range("20240601", "20240630")
        assert result is True

    @pytest.mark.asyncio
    async def test_api_fills_range(self):
        df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(api_return=df)
        result = await svc.ensure_calendar_range("20240601", "20240630")
        assert result is True

    @pytest.mark.asyncio
    async def test_api_returns_empty(self):
        svc = _make_service(api_return=pd.DataFrame())
        result = await svc.ensure_calendar_range("20240601", "20240630")
        assert result is False

    @pytest.mark.asyncio
    async def test_api_exception(self):
        svc = _make_service()
        svc._api.get_trade_cal = AsyncMock(side_effect=Exception("API error"))
        result = await svc.ensure_calendar_range("20240601", "20240630")
        assert result is False


class TestIsTradingDay:
    @pytest.mark.asyncio
    async def test_none_date(self):
        svc = _make_service()
        result = await svc.is_trading_day(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_db_has_trading_day(self):
        df = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 14)], "is_open": [1]})
        svc = _make_service(cache_return=df)
        result = await svc.is_trading_day("20240614")
        assert result is True

    @pytest.mark.asyncio
    async def test_db_has_non_trading_day(self):
        df_no_filter = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 15)], "is_open": [0]})
        svc = _make_service(cache_return=pd.DataFrame(), cache_return_no_filter=df_no_filter)
        result = await svc.is_trading_day("20240615")
        assert not result

    @pytest.mark.asyncio
    async def test_db_empty_then_api(self):
        api_df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(cache_return=pd.DataFrame(), api_return=api_df)
        result = await svc.is_trading_day("20240614")
        assert result

    @pytest.mark.asyncio
    async def test_fallback_to_offline(self):
        svc = _make_service(cache_return=None, api_return=None, offline_return=True)
        result = await svc.is_trading_day("20240614")
        assert result is True

    @pytest.mark.asyncio
    async def test_db_exception_fallback(self):
        svc = _make_service()
        svc._cache.get_trade_cal = AsyncMock(side_effect=Exception("DB error"))
        svc._offline = MagicMock()
        svc._offline.is_trading_day = MagicMock(return_value=True)
        result = await svc.is_trading_day("20240614")
        assert result is True


class TestGetTradeDates:
    @pytest.mark.asyncio
    async def test_none_start(self):
        svc = _make_service()
        result = await svc.get_trade_dates(None, "20240614")
        assert result == []

    @pytest.mark.asyncio
    async def test_start_after_end(self):
        svc = _make_service()
        result = await svc.get_trade_dates("20240620", "20240614")
        assert result == []

    @pytest.mark.asyncio
    async def test_from_db(self):
        df = pd.DataFrame({"cal_date": ["20240614", "20240615"], "is_open": [1, 1]})
        svc = _make_service(cache_return=df)
        result = await svc.get_trade_dates("20240614", "20240615")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_from_api_when_db_empty(self):
        api_df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(cache_return=pd.DataFrame(), api_return=api_df)
        result = await svc.get_trade_dates("20240614", "20240614")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_from_offline_when_all_fail(self):
        svc = _make_service(cache_return=None, api_return=None)
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=["20240614"])
        result = await svc.get_trade_dates("20240614", "20240614")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_db_data_incomplete_fallback(self):
        df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(cache_return=df)
        api_df = pd.DataFrame({"cal_date": ["20240614", "20240615", "20240616"], "is_open": [1, 1, 1]})
        svc._api.get_trade_cal = AsyncMock(return_value=api_df)
        result = await svc.get_trade_dates("20240101", "20240630")
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_exception_falls_to_offline(self):
        svc = _make_service()
        svc._cache.get_trade_cal = AsyncMock(side_effect=Exception("DB error"))
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=["20240614"])
        result = await svc.get_trade_dates("20240614", "20240614")
        assert len(result) == 1


class TestCountTradeDays:
    @pytest.mark.asyncio
    async def test_none_dates(self):
        svc = _make_service()
        result = await svc.count_trade_days(None, "20240614")
        assert result == 0

    @pytest.mark.asyncio
    async def test_from_dao(self):
        svc = _make_service()
        result = await svc.count_trade_days("20240614", "20240620")
        assert result == 5

    @pytest.mark.asyncio
    async def test_dao_exception_fallback(self):
        svc = _make_service()
        svc._cache.stock_dao.count_trade_days = AsyncMock(side_effect=Exception("DAO error"))
        df = pd.DataFrame({"cal_date": ["20240614", "20240615"], "is_open": [1, 1]})
        svc._cache.get_trade_cal = AsyncMock(return_value=df)
        result = await svc.count_trade_days("20240614", "20240615")
        assert result == 2


class TestGetStartDateByTradeDays:
    @pytest.mark.asyncio
    async def test_none_end_date(self):
        svc = _make_service()
        result = await svc.get_start_date_by_trade_days(None, 120)
        assert result is None

    @pytest.mark.asyncio
    async def test_zero_trade_days(self):
        svc = _make_service()
        result = await svc.get_start_date_by_trade_days("20240614", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_from_cache(self):
        svc = _make_service()
        svc._cache.get_start_date_by_trade_days = AsyncMock(return_value=datetime.date(2024, 1, 15))
        result = await svc.get_start_date_by_trade_days("20240614", 120)
        assert result == datetime.date(2024, 1, 15)

    @pytest.mark.asyncio
    async def test_from_trade_dates_list(self):
        df = pd.DataFrame({"cal_date": [f"202406{i:02d}" for i in range(1, 15)], "is_open": [1] * 14})
        svc = _make_service(cache_return=df)
        result = await svc.get_start_date_by_trade_days("20240614", 5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_exception_fallback(self):
        svc = _make_service()
        svc._cache.get_start_date_by_trade_days = AsyncMock(side_effect=Exception("error"))
        svc._cache.get_trade_cal = AsyncMock(side_effect=Exception("error"))
        result = await svc.get_start_date_by_trade_days("20240614", 120)
        assert result is not None


class TestGetPrevTradeDate:
    @pytest.mark.asyncio
    async def test_none_date(self):
        svc = _make_service()
        result = await svc.get_prev_trade_date(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_with_dates_before(self):
        df = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 13), datetime.date(2024, 6, 14)], "is_open": [1, 1]})
        svc = _make_service(cache_return=df)
        result = await svc.get_prev_trade_date(datetime.date(2024, 6, 14))
        assert result == datetime.date(2024, 6, 13)

    @pytest.mark.asyncio
    async def test_no_dates_before(self):
        df = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 14)], "is_open": [1]})
        svc = _make_service(cache_return=df)
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=["20240613"])
        result = await svc.get_prev_trade_date(datetime.date(2024, 6, 14))
        assert result == datetime.date(2024, 6, 13)

    @pytest.mark.asyncio
    async def test_no_dates_at_all(self):
        svc = _make_service(cache_return=None, api_return=None)
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=[])
        result = await svc.get_prev_trade_date(datetime.date(2024, 6, 14))
        assert result is None


class TestGetNextTradeDate:
    @pytest.mark.asyncio
    async def test_none_date(self):
        svc = _make_service()
        result = await svc.get_next_trade_date(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_with_dates_after(self):
        df = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 14), datetime.date(2024, 6, 15)], "is_open": [1, 1]})
        svc = _make_service(cache_return=df)
        result = await svc.get_next_trade_date(datetime.date(2024, 6, 14))
        assert result == datetime.date(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_no_dates_after_offline(self):
        df = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 14)], "is_open": [1]})
        svc = _make_service(cache_return=df)
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=["20240615"])
        result = await svc.get_next_trade_date(datetime.date(2024, 6, 14))
        assert result == datetime.date(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_no_dates_at_all(self):
        svc = _make_service(cache_return=None, api_return=None)
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=[])
        result = await svc.get_next_trade_date(datetime.date(2024, 6, 14))
        assert result is None


class TestGetLatestTradeDate:
    @pytest.mark.asyncio
    async def test_from_cache_ttl(self):
        svc = _make_service()
        svc._latest_trade_date_cache = {"ts": 9999999999, "val": datetime.date(2024, 6, 14)}
        result = await svc.get_latest_trade_date()
        assert result == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_from_db(self):
        df = pd.DataFrame({"cal_date": [datetime.date(2024, 6, 13), datetime.date(2024, 6, 14)], "is_open": [1, 1]})
        svc = _make_service(cache_return=df)
        with patch("data.domain_services.trade_calendar_service.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14, 16, 0)
            result = await svc.get_latest_trade_date()
            assert result is not None

    @pytest.mark.asyncio
    async def test_weekday_fallback(self):
        svc = _make_service(cache_return=None, api_return=None)
        svc._offline = MagicMock()
        svc._offline.get_trade_dates = MagicMock(return_value=[])
        with patch("data.domain_services.trade_calendar_service.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 14, 16, 0)
            result = await svc.get_latest_trade_date()
            assert result is not None


class TestGetTradeDatesBatch:
    @pytest.mark.asyncio
    async def test_empty_ranges(self):
        svc = _make_service()
        result = await svc.get_trade_dates_batch([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_ranges(self):
        df = pd.DataFrame(
            {
                "cal_date": [
                    datetime.date(2024, 6, 10),
                    datetime.date(2024, 6, 11),
                    datetime.date(2024, 6, 14),
                    datetime.date(2024, 6, 15),
                ],
                "is_open": [1, 1, 1, 1],
            }
        )
        svc = _make_service(cache_return=df)
        ranges = [
            (datetime.date(2024, 6, 10), datetime.date(2024, 6, 11)),
            (datetime.date(2024, 6, 14), datetime.date(2024, 6, 15)),
        ]
        result = await svc.get_trade_dates_batch(ranges)
        assert len(result) == 2


class TestClearCache:
    def test_clear(self):
        svc = _make_service()
        svc._mem_cache = {"key": "value"}
        svc.clear_cache()
        assert svc._mem_cache == {}
        assert svc._latest_trade_date_cache["val"] is None


class TestGetTradeCalDf:
    @pytest.mark.asyncio
    async def test_from_cache(self):
        df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(cache_return=df)
        result = await svc.get_trade_cal_df(start_date="20240614", end_date="20240614")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_from_api(self):
        api_df = pd.DataFrame({"cal_date": ["20240614"], "is_open": [1]})
        svc = _make_service(cache_return=pd.DataFrame(), api_return=api_df)
        result = await svc.get_trade_cal_df(start_date="20240614", end_date="20240614")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_is_open_filter(self):
        api_df = pd.DataFrame({"cal_date": ["20240614", "20240615"], "is_open": [1, 0]})
        svc = _make_service(cache_return=pd.DataFrame(), api_return=api_df)
        result = await svc.get_trade_cal_df(start_date="20240614", end_date="20240615", is_open=1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        svc = _make_service()
        svc._cache.get_trade_cal = AsyncMock(side_effect=Exception("error"))
        svc._api.get_trade_cal = AsyncMock(side_effect=Exception("error"))
        result = await svc.get_trade_cal_df(start_date="20240614", end_date="20240614")
        assert isinstance(result, pd.DataFrame)
        assert result.empty
