# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import datetime
from datetime import datetime as dt_datetime
import pytest
from unittest.mock import patch

from utils.time_utils import (
    get_now,
    parse_date,
    get_today_str,
    to_yyyymmdd_str,
    to_date,
    to_utc_for_db,
    from_utc_to_cst,
    CST_TZ,
)

pytestmark = pytest.mark.unit


class TestGetNow:
    def test_returns_datetime(self):
        result = get_now()
        assert isinstance(result, datetime.datetime)
        assert result.tzinfo is not None

    def test_has_timezone(self):
        result = get_now()
        assert result.tzinfo is not None  # noqa: weak-assertion tzinfo 存在性是时区感知契约验证

    def test_is_cst_timezone(self):
        result = get_now()
        assert result.tzinfo.zone == "Asia/Shanghai"

    def test_cst_tz_defined(self):
        assert CST_TZ is not None
        assert CST_TZ.zone == "Asia/Shanghai"


class TestParseDate:
    def test_parse_string_yyyymmdd(self):
        result = parse_date("20240101")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_parse_string_yyyy_mm_dd(self):
        result = parse_date("2024-01-01")
        assert result.year == 2024

    def test_parse_string_datetime(self):
        result = parse_date("2024-01-01 12:30:00")
        assert result.hour == 12
        assert result.minute == 30

    def test_parse_date_object(self):
        d = datetime.date(2024, 6, 15)
        result = parse_date(d)
        assert result.year == 2024
        assert result.month == 6

    def test_parse_datetime_object_naive(self):
        dt = datetime.datetime(2024, 6, 15, 10, 0)
        result = parse_date(dt)
        assert result.tzinfo is not None  # noqa: weak-assertion tzinfo 存在性是时区感知契约验证

    def test_parse_datetime_object_aware(self):
        dt = CST_TZ.localize(datetime.datetime(2024, 6, 15, 10, 0))
        result = parse_date(dt)
        assert result.tzinfo is not None  # noqa: weak-assertion tzinfo 存在性是时区感知契约验证


class TestGetTodayStr:
    def test_returns_string(self):
        result = get_today_str()
        assert isinstance(result, str)

    @patch("utils.time_utils.get_now")
    def test_format_yyyymmdd(self, mock_get_now):
        fixed_dt = CST_TZ.localize(datetime.datetime(2024, 6, 15, 10, 30, 0))
        mock_get_now.return_value = fixed_dt
        result = get_today_str()
        assert result == "20240615"
        assert len(result) == 8
        assert result.isdigit()


class TestToYyyymmddStr:
    def test_none_returns_none(self):
        assert to_yyyymmdd_str(None) is None

    def test_datetime_input(self):
        dt = datetime.datetime(2024, 6, 15)
        assert to_yyyymmdd_str(dt) == "20240615"

    def test_date_input(self):
        d = datetime.date(2024, 6, 15)
        assert to_yyyymmdd_str(d) == "20240615"

    def test_string_input(self):
        assert to_yyyymmdd_str("20240615") == "20240615"

    def test_nan_returns_none(self):
        assert to_yyyymmdd_str("nan") is None

    def test_nat_returns_none(self):
        assert to_yyyymmdd_str("NaT") is None

    def test_none_string_returns_none(self):
        assert to_yyyymmdd_str("None") is None

    def test_empty_string_returns_none(self):
        assert to_yyyymmdd_str("") is None

    def test_partial_digit_string(self):
        assert to_yyyymmdd_str("2024061500") == "20240615"

    def test_normalizes_common_inputs(self):
        assert to_yyyymmdd_str("2024-03-15") == "20240315"
        assert to_yyyymmdd_str("20240315") == "20240315"
        assert to_yyyymmdd_str(dt_datetime(2024, 3, 15, 10, 0, 0)) == "20240315"
        assert to_yyyymmdd_str(None) is None
        assert to_yyyymmdd_str("") is None


class TestToDate:
    def test_datetime_input(self):
        dt = datetime.datetime(2024, 6, 15)
        result = to_date(dt)
        assert result == datetime.date(2024, 6, 15)

    def test_date_input(self):
        d = datetime.date(2024, 6, 15)
        result = to_date(d)
        assert result == d

    def test_string_input(self):
        result = to_date("20240615")
        assert result == datetime.date(2024, 6, 15)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            to_date("not_a_date")

    def test_normalizes_common_inputs(self):
        assert to_date("20240315") == datetime.date(2024, 3, 15)
        assert to_date("2024-03-15") == datetime.date(2024, 3, 15)
        assert to_date(datetime.datetime(2024, 3, 15, 10, 0, 0)) == datetime.date(2024, 3, 15)


class TestToUtcForDb:
    def test_none_returns_none(self):
        assert to_utc_for_db(None) is None

    def test_naive_datetime(self):
        dt = datetime.datetime(2024, 6, 15, 8, 0)
        result = to_utc_for_db(dt)
        assert result.tzinfo is None
        assert result.hour == 0

    def test_aware_datetime(self):
        dt = CST_TZ.localize(datetime.datetime(2024, 6, 15, 8, 0))
        result = to_utc_for_db(dt)
        assert result.tzinfo is None
        assert result.hour == 0

    def test_aware_datetime_cst_to_utc(self):
        cst_time = dt_datetime(2024, 1, 15, 10, 30, 0)
        cst_aware = CST_TZ.localize(cst_time)
        utc_time = to_utc_for_db(cst_aware)
        assert utc_time.tzinfo is None
        assert utc_time.hour == 2

    def test_naive_datetime_treated_as_cst(self):
        naive_time = dt_datetime(2024, 1, 15, 10, 30, 0)
        utc_time = to_utc_for_db(naive_time)
        assert utc_time.hour == 2


class TestFromUtcToCst:
    def test_none_returns_none(self):
        assert from_utc_to_cst(None) is None

    def test_naive_datetime(self):
        dt = datetime.datetime(2024, 6, 15, 0, 0)
        result = from_utc_to_cst(dt)
        assert result.tzinfo is not None
        assert result.hour == 8

    def test_aware_datetime(self):
        dt = datetime.datetime(2024, 6, 15, 0, 0, tzinfo=datetime.UTC)
        result = from_utc_to_cst(dt)
        assert result.hour == 8

    def test_utc_naive_to_cst(self):
        utc_naive = dt_datetime(2024, 1, 15, 2, 30, 0)
        cst_time = from_utc_to_cst(utc_naive)
        assert cst_time.hour == 10
        assert cst_time.tzinfo.zone == "Asia/Shanghai"

    def test_roundtrip_preserves_time(self):
        original = CST_TZ.localize(dt_datetime(2024, 6, 15, 14, 45, 30))
        utc_stored = to_utc_for_db(original)
        restored = from_utc_to_cst(utc_stored)
        assert original.hour == restored.hour
        assert original.minute == restored.minute
