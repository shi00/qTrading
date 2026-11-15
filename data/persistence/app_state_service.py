import asyncio
import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from data.persistence import engine_provider
from data.persistence.daos.base_dao import BaseDao, EngineDisposedError
from utils.log_decorators import PerfThreshold, log_async_operation
from data.persistence.models import AppState

logger = logging.getLogger(__name__)


def _ensure_engine_alive(engine, context: str) -> None:
    """R5：拒绝在已释放引擎上执行操作。

    ``CacheManager.close()`` 先标记 disposed、再 await dispose，最后才置空
    ``engine`` 引用；该窗口内 engine 非 None 但已不可用，仅判 None 会落到
    僵尸引擎上。
    """
    if engine_provider.is_disposed(engine):
        raise EngineDisposedError(
            f"[AppState] Engine disposed, {context} rejected. Call CacheManager.init_db() to reinitialize."
        )


async def _guard_engine(engine, context: str) -> None:
    """使用引擎前做 R5 检查，并在等待维护闸门后复查（DAT-01：消除 TOCTOU）。

    维护/停机期间 CacheManager 可能释放或替换引擎；首个 await 之后必须重新
    校验，避免携带过期结论在已释放引擎上 connect/begin。
    """
    _ensure_engine_alive(engine, context)
    await BaseDao._get_maintenance_event().wait()
    _ensure_engine_alive(engine, f"{context}:post-maintenance")


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def get_app_state(engine, key: str) -> str | None:
    if engine is None:
        return None
    try:
        await _guard_engine(engine, "read")
        async with engine.connect() as conn:
            result = await conn.execute(sa.select(AppState.config_value).where(AppState.config_key == key))
            row = result.fetchone()
            return row[0] if row else None
    except asyncio.CancelledError:
        raise
    except EngineDisposedError:
        # R5：生命周期错误不得被 best-effort 捕获降级为「无值」
        raise
    except Exception as e:
        logger.debug("[AppState] Failed to read key='%s': %s", key, e)
        return None


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def set_app_state(engine, key: str, value: str, *, strict: bool = False) -> None:
    """覆盖写 app_state。

    ``strict=False``（默认）时普通写失败仅告警降级，适用于偏好类状态；
    ``strict=True`` 时写失败向上抛出，供财报 pending / 水位等关键断点确认
    写入成功后再推进完成状态。引擎已释放（R5）与取消（R2）始终传播。
    """
    if engine is None:
        return
    try:
        await _guard_engine(engine, "write")
        async with engine.begin() as conn:
            stmt = pg_insert(AppState).values(config_key=key, config_value=value)
            stmt = stmt.on_conflict_do_update(
                index_elements=["config_key"],
                set_={"config_value": value, "updated_at": sa.func.now()},
            )
            await conn.execute(stmt)
    except asyncio.CancelledError:
        raise
    except EngineDisposedError:
        # R5：生命周期错误不得被 best-effort 捕获降级为「静默未写」
        raise
    except Exception as e:
        if strict:
            raise
        logger.warning("[AppState] Failed to write key='%s': %s", key, e)


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def set_app_state_max(engine, key: str, value: str, *, strict: bool = False) -> None:
    """单调写入：仅当新值字典序大于现有值时更新。

    用于 YYYYMMDD 格式的"已尝试水位"（该格式下字典序与时间序一致，见治理 D1-1）。
    并发写入由 PostgreSQL 的行级锁保证正确性（INSERT..ON CONFLICT 为单语句原子操作），
    无需 Python 侧读-改-写。`where` 让"无需更新"变成零行更新，兼顾正确性与减少 WAL。

    默认写失败由内部吞掉不阻断调用方（单调自愈：下次运行会重设为真实 max）；
    ``strict=True`` 时写失败向上抛出，供关键断点确认水位已落库。引擎未就绪时为 no-op，
    引擎已释放（R5）与取消（R2）始终传播。
    """
    if engine is None:
        return
    try:
        await _guard_engine(engine, "write-max")
        async with engine.begin() as conn:
            stmt = pg_insert(AppState).values(config_key=key, config_value=value)
            stmt = stmt.on_conflict_do_update(
                index_elements=["config_key"],
                set_={"config_value": value, "updated_at": sa.func.now()},
                where=(AppState.config_value < value),
            )
            await conn.execute(stmt)
    except asyncio.CancelledError:
        raise
    except EngineDisposedError:
        raise
    except Exception as e:
        if strict:
            raise
        logger.warning("[AppState] Failed to write max key='%s': %s", key, e)
