import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from utils.log_decorators import PerfThreshold, log_async_operation
from data.persistence.models import AppState

logger = logging.getLogger(__name__)


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def get_app_state(engine, key: str) -> str | None:
    if engine is None:
        return None
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sa.select(AppState.value).where(AppState.key == key))
            row = result.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.debug("[AppState] Failed to read key='%s': %s", key, e)
        return None


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def set_app_state(engine, key: str, value: str) -> None:
    if engine is None:
        return
    try:
        async with engine.begin() as conn:
            stmt = pg_insert(AppState).values(key=key, value=value)
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": sa.func.now()},
            )
            await conn.execute(stmt)
    except Exception as e:
        logger.warning("[AppState] Failed to write key='%s': %s", key, e)
