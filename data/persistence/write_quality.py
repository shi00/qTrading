"""进程内表级写入质量登记（DAT-03：脏日期 coerce 计数接入数据质量门控）。

背景：``BaseDao._save_upsert`` 在用 ``pd.to_datetime(..., errors="coerce")`` 转换日期列时，
脏日期（上游返回 ``''``/``'0'``/``'--'`` 或非法格式）会被**静默置为 NULL**，无任何日志。
本模块为每次写入登记"最近一次写入的日期 coerce 率（coerced_rows / rows）"，供
``HealthCheckMixin.check_data_health`` 在计算质量 tier 时读取——若某**关键表**最近一次
写入的 coerce 率超过阈值，说明该表刚写入的数据可能是错/缺的，应视为质量降级，
从而阻断依赖它的策略执行。

时效语义：只认"最近一次写入且发生在 ``WRITE_COERCE_MAX_AGE_SEC`` 内"的 coerce 率，
避免某次瞬时脏数据导致**永久性** tier 降级；后续一次正常写入（coerced=0）会覆盖 ratio，
使该表恢复可信。
"""

from __future__ import annotations

import threading
import time
import typing

from utils.singleton_registry import register_singleton

# coerce 率超过该比例（0.1%）即视为关键表质量降级（对齐检视报告 DAT-03 建议阈值）
WRITE_COERCE_DEGRADE_RATIO = 0.001
# 上次写入超过此时长（秒）后，coerce 率失效，不再影响 tier（避免陈旧降级）
WRITE_COERCE_MAX_AGE_SEC = 24 * 3600


@register_singleton
class WriteQuality:
    """单例登记表：per-table 最近一次写入的日期 coerce 统计。

    R7 测试隔离：经 ``@register_singleton`` + ``_reset_singleton``，由
    ``tests/unit/conftest.py`` 的 autouse fixture 自动重置，不污染用例间状态。
    """

    _instance = None
    _lock = threading.Lock()  # Thread-safe singleton

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            inst = cls._instance
            cls._instance = None
            cls._initialized = False
        if inst is not None:
            inst._stats.clear()

    def __init__(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._initialized = True
            self._stats: dict[str, dict[str, typing.Any]] = {}

    def record_write(self, table_name: str, rows: int, coerced_rows: int) -> None:
        """登记一次写入的表级 coerce 统计（最近一次覆盖式，非累计）。"""
        with self._lock:
            self._stats[table_name] = {
                "rows": rows,
                "coerced_rows": coerced_rows,
                "coerce_ratio": coerced_rows / rows if rows > 0 else 0.0,
                "ts": time.time(),
            }

    def get_coerce_ratio(self, table_name: str, max_age_sec: float = WRITE_COERCE_MAX_AGE_SEC) -> float | None:
        """返回该表最近一次写入（max_age_sec 内）的 coerce 率；无有效写入返回 None。

        None 表示该表无有效 coerce 依据（最近未写入或写入过期），不触发降级。
        """
        with self._lock:
            stat = self._stats.get(table_name)
            if not stat or stat["rows"] <= 0:
                return None
            if time.time() - stat["ts"] > max_age_sec:
                return None
            return float(stat["coerce_ratio"])

    def is_degraded(
        self,
        table_name: str,
        ratio_threshold: float = WRITE_COERCE_DEGRADE_RATIO,
    ) -> bool:
        """该表最近写入的 coerce 率是否超过阈值（关键表健康降级信号）。"""
        ratio = self.get_coerce_ratio(table_name)
        return ratio is not None and ratio > ratio_threshold
