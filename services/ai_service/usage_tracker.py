"""LLM 云端调用累计成本追踪（review04 AI-03 完整版）。

把每次 LLM 调用的估算货币成本按月累计写入 ``app_state`` 表，支撑「本月累计 +
月度预算」的 UI 展示与超限软停。设计约定：

- 存储：``AppState`` 表，key 形如 ``ai_cost:{YYYYMM}``，value 为本月累计成本的
  **整数分**（pricing 计算的浮点元经 ``add_cost_cny`` 转分为整数后累计，避免浮点误差）。
- 跨月轮换：key 含年月，进入新月自然落到新 key；旧月数据保留供历史参考。
- 原子累加：并发写（多策略/多批次）用 ``on_conflict_do_update`` 在 SQL 侧
  ``cast(value as bigint) + delta`` 递增，杜绝「读-改-写」竞态丢数（对抗审查修正）。
- engine 注入：经 ``configure(engine)`` 注入，不反向依赖 ``CacheManager`` 单例
  （对抗审查修正）；未注入 engine 时读写均为 no-op（降级为不持久化，不阻断流程）。
- 单例：``@register_singleton`` + ``_reset_singleton`` 供测试隔离（R15）。
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any
from collections.abc import Callable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from data.persistence.models import AppState
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.singleton_registry import register_singleton

logger = logging.getLogger(__name__)

# app_state key 前缀；month key = f"{_AI_COST_KEY_PREFIX}:{YYYYMM}"。
_AI_COST_KEY_PREFIX = "ai_cost"
# 不可计价调用计数 key 前缀（AI-01）：模型不在 litellm 价格表时无法计价，
# 计数其 calls/tokens 以避免 "不可计量" 被呈现为 "零成本"（R21）。
_AI_UNPRICED_CALLS = "ai_unpriced_calls"
_AI_UNPRICED_TOKENS = "ai_unpriced_tokens"


def month_key(when: date) -> str:
    """生成某年月的成本累计 key（含年月，天然实现跨月轮换）。"""
    return f"{_AI_COST_KEY_PREFIX}:{when:%Y%m}"


def month_unpriced_calls_key(when: date) -> str:
    """生成某年月的不可计价调用次数 key。"""
    return f"{_AI_UNPRICED_CALLS}:{when:%Y%m}"


def month_unpriced_tokens_key(when: date) -> str:
    """生成某年月的不可计价 token 数 key。"""
    return f"{_AI_UNPRICED_TOKENS}:{when:%Y%m}"


@register_singleton
class AIUsageTracker:
    """按月累计 LLM 调用成本（单位：分）的单例追踪器。

    线程模型：``engin`` 在 ``configure`` / ``__init__`` 中设置，之后只读；
    读写走 asyncpg（异步、事件循环线程安全），无需额外锁。成本为单调累加，
    天然满足 R22 单调性；跨月轮换由 key 含年月保证。
    """

    _instance = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        *,
        engine: Any | None = None,
        clock: Callable[[], date] | None = None,
    ):
        """可选注入 engine 与时钟（依赖注入，便于测试隔离）。

        Args:
            engine: 数据库引擎。为 None 时读写 no-op（未就绪或未注入）。
            clock: 可注入时钟，返回 ``date``，用于决定当前月份（默认 ``date.today``）。
        """
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._engine: Any = engine
            self._clock: Callable[[], date] = clock or date.today
            self._initialized = True

    @classmethod
    def _reset_singleton(cls) -> None:
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def configure(self, *, engine: Any | None = None) -> None:
        """注入数据库引擎（runtime 就绪后由调用方配置）。"""
        if engine is not None:
            self._engine = engine

    def _resolve_engine(self) -> Any:
        return self._engine

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_month_cost_cny(self, when: date | None = None) -> int:
        """读取某月累计成本（分）。engine 未注入或无记录时返回 0。"""
        engine = self._resolve_engine()
        if engine is None:
            return 0
        key = month_key(when or self._clock())
        try:
            async with engine.connect() as conn:
                result = await conn.execute(sa.select(AppState.config_value).where(AppState.config_key == key))
                row = result.fetchone()
                if row is None or row[0] is None:
                    return 0
                return int(row[0])
        except Exception as e:  # noqa: BLE001 -- 读取降级为 0，不阻断流程
            logger.debug("[AIUsageTracker] Failed to read key='%s': %s", key, e)
            return 0

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def add_cost_cny(self, cents: int, when: date | None = None) -> None:
        """原子累加某月累计成本（分）。

        注入 engine 或较小时才写库；否则 no-op（不持久化，成本仅内存累计）。
        ``cents`` 为负时记录告警并忽略（成本不允许为负）。
        """
        engine = self._resolve_engine()
        if engine is None:
            return
        if cents < 0:
            logger.warning("[AIUsageTracker] Ignored negative cost delta: %s", cents)
            return
        if cents == 0:
            return
        key = month_key(when or self._clock())
        try:
            async with engine.begin() as conn:
                stmt = pg_insert(AppState).values(config_key=key, config_value=str(cents))
                stmt = stmt.on_conflict_do_update(
                    index_elements=["config_key"],
                    set_={
                        "config_value": sa.cast(
                            sa.cast(AppState.config_value, sa.BigInteger) + cents,
                            sa.String,
                        ),
                        "updated_at": sa.func.now(),
                    },
                )
                await conn.execute(stmt)
        except Exception as e:  # noqa: BLE001 -- 写入失败不阻断核心流程，仅告警
            logger.warning("[AIUsageTracker] Failed to accumulate cost on key='%s': %s", key, e)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_month_unpriced(self, when: date | None = None) -> tuple[int, int]:
        """读取某月不可计价调用次数与 token 数 (calls, tokens)。

        AI-01：不可计价调用（模型不在 litellm 价格表）须计数，否则「不可计量」被
        呈现为「零成本」（R21）。engine 未注入或无记录时返回 (0, 0)。
        """
        engine = self._resolve_engine()
        if engine is None:
            return (0, 0)
        base = when or self._clock()
        calls_key = month_unpriced_calls_key(base)
        tokens_key = month_unpriced_tokens_key(base)
        values: dict[str, int] = {}
        try:
            async with engine.connect() as conn:
                for k in (calls_key, tokens_key):
                    result = await conn.execute(sa.select(AppState.config_value).where(AppState.config_key == k))
                    row = result.fetchone()
                    values[k] = int(row[0]) if row is not None and row[0] is not None else 0
        except Exception as e:  # noqa: BLE001 -- 读取降级为 0，不阻断流程
            logger.debug("[AIUsageTracker] Failed to read unpriced keys '%s'/'%s': %s", calls_key, tokens_key, e)
            return (0, 0)
        return (values.get(calls_key, 0), values.get(tokens_key, 0))

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def add_unpriced(self, calls: int, tokens: int, when: date | None = None) -> None:
        """原子累加某月不可计价调用次数与 token 数。

        AI-01：与 ``add_cost_cny`` 同款 ``cast(BigInteger) + delta`` 原子累加，
        calls/tokens 各一个 key，同事务写入避免部分成功不一致。不可计价量为月度
        累计量，多批次/多策略并发必须累加而非覆盖。
        engine 未注入或调用/token 均非正时 no-op（不持久化）。
        """
        engine = self._resolve_engine()
        if engine is None:
            return
        if calls <= 0 and tokens <= 0:
            return
        base = when or self._clock()
        updates = [
            (month_unpriced_calls_key(base), calls),
            (month_unpriced_tokens_key(base), tokens),
        ]
        try:
            async with engine.begin() as conn:  # 单事务，两 key 一致性
                for key, delta in updates:
                    if delta <= 0:
                        continue
                    stmt = pg_insert(AppState).values(config_key=key, config_value=str(delta))
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["config_key"],
                        set_={
                            "config_value": sa.cast(
                                sa.cast(AppState.config_value, sa.BigInteger) + delta,
                                sa.String,
                            ),
                            "updated_at": sa.func.now(),
                        },
                    )
                    await conn.execute(stmt)
        except Exception as e:  # noqa: BLE001 -- 写入失败不阻断核心流程，仅告警
            logger.warning("[AIUsageTracker] Failed to accumulate unpriced on key: %s", e)
