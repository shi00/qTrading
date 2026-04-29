"""
Tests for time_utils module.

S1-6: TaskManager 时区统一 UTC 相关测试。
"""

import os
import sys
from datetime import datetime


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestTimezoneConversion:
    """S1-6: 时区转换测试"""

    def test_to_utc_for_db(self):
        """CST 时间转 UTC 存储"""
        from utils.time_utils import to_utc_for_db, CST_TZ

        cst_time = datetime(2024, 1, 15, 10, 30, 0)
        cst_aware = CST_TZ.localize(cst_time)

        utc_time = to_utc_for_db(cst_aware)
        assert utc_time.tzinfo is None
        assert utc_time.hour == 2

    def test_from_utc_to_cst(self):
        """UTC 时间转 CST 显示"""
        from utils.time_utils import from_utc_to_cst

        utc_naive = datetime(2024, 1, 15, 2, 30, 0)

        cst_time = from_utc_to_cst(utc_naive)
        assert cst_time.hour == 10
        assert cst_time.tzinfo.zone == "Asia/Shanghai"

    def test_roundtrip_preserves_time(self):
        """往返转换保持时间一致"""
        from utils.time_utils import to_utc_for_db, from_utc_to_cst, CST_TZ

        original = CST_TZ.localize(datetime(2024, 6, 15, 14, 45, 30))
        utc_stored = to_utc_for_db(original)
        restored = from_utc_to_cst(utc_stored)

        assert original.hour == restored.hour
        assert original.minute == restored.minute

    def test_to_utc_with_naive_datetime(self):
        """无时区信息的时间视为 CST 并转为 UTC"""
        from utils.time_utils import to_utc_for_db

        naive_time = datetime(2024, 1, 15, 10, 30, 0)
        utc_time = to_utc_for_db(naive_time)

        assert utc_time.hour == 2

    def test_cst_tz_defined(self):
        """CST_TZ 常量已定义"""
        from utils.time_utils import CST_TZ

        assert CST_TZ is not None
        assert CST_TZ.zone == "Asia/Shanghai"
