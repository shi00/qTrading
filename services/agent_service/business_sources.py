"""回测结果 → 业务来源严格读适配（E-01 D5，§9.13）。

四分支区分（不把失败伪装成已读/已核验）：
- 成功且有行 → ``read_state=ready``、``verification=program_verified``；
- 成功且无行 → ``unavailable`` + ``result_not_found``；
- 数据库失败 → ``PERSISTENCE_UNAVAILABLE``（不构造来源）；
- disposed → ``EngineDisposedError`` 原样传播；取消 → ``CancelledError`` 必须 raise（R2）。
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    ReadState,
    Selection,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceVersionRef,
    VerificationState,
    sha256_hex,
)
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError

#: 可用的回测时间列：``executed_at`` 是 ``backtest_results`` 的真实列（无 ``created_at``）。
_TIMESTAMP_KEYS = ("executed_at", "created_at", "timestamp", "run_time", "created_time")


class BacktestResultReader(Protocol):
    """回测结果严格读端口（生产为 ``BacktestDao.get_result``）。"""

    async def get_result(self, run_id: str, *, suppress_errors: bool = True) -> dict | None: ...


@dataclass(frozen=True, slots=True)
class BusinessSnapshotRead:
    """业务快照读取结果：失败时 ``source=None``，绝不携带可登记「已核验」来源。"""

    source: BusinessSourceItem | None
    read_state: ReadState
    missing_reason: str | None


async def read_backtest_snapshot(
    dao: BacktestResultReader,
    *,
    user_scope: str,
    result_id: str,
    cutoff: str | None = None,
) -> BusinessSnapshotRead:
    """严格读取回测结果并构造业务快照来源（不登记；登记由 ``BusinessSourceService``）。"""

    identity = f"{user_scope}/backtest/{result_id}"
    if not isinstance(result_id, str) or not result_id:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "result_id must be non-empty", identity)
    try:
        row = await dao.get_result(result_id, suppress_errors=False)
    except asyncio.CancelledError:
        raise
    except EngineDisposedError:
        raise
    except DatabaseQueryError as exc:
        raise AgentServiceError(
            AgentErrorCode.PERSISTENCE_UNAVAILABLE, "backtest result read failed", identity
        ) from exc

    if row is None:
        return BusinessSnapshotRead(source=None, read_state=ReadState.UNAVAILABLE, missing_reason="result_not_found")
    if not isinstance(row, Mapping):
        raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "backtest row shape invalid", identity)

    safe_row = _json_safe(dict(row))
    digest = sha256_hex(
        json.dumps(safe_row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    available_at = _wire_timestamp(_first_timestamp(row))
    cutoff_dt = _parse_timestamp(cutoff, identity)
    if cutoff_dt is not None:
        if available_at is None:
            # 时点未知不能伪装成「晚于截止」（R21）：单独给出可审计原因。
            return BusinessSnapshotRead(
                source=None, read_state=ReadState.UNAVAILABLE, missing_reason="available_at_unknown"
            )
        available_dt = _parse_timestamp(available_at, identity)
        if available_dt is None or available_dt > cutoff_dt:
            return BusinessSnapshotRead(
                source=None, read_state=ReadState.UNAVAILABLE, missing_reason="available_after_cutoff"
            )

    source_id = f"backtest:{result_id}"
    source = SourceRef(
        user_scope=user_scope,
        source_id=source_id,
        version="1",
        digest=digest,
        source_kind=SourceKind.BUSINESS_SNAPSHOT,
        display_name=f"回测结果 {result_id}",
        object_ref=source_id,
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.BUSINESS_SNAPSHOT,
                source_id=source_id,
                source_version="1",
                source_digest=digest,
            ),
            selection=Selection(kind="all"),
        ),
        available_at=available_at,
        period=_period_of(row),
        unit_note=None,
        read_state=ReadState.READY,
        missing_reason=None,
        provenance_refs=(),
    )
    return BusinessSnapshotRead(
        source=BusinessSourceItem(source=source, verification=VerificationState.PROGRAM_VERIFIED),
        read_state=ReadState.READY,
        missing_reason=None,
    )


def _first_timestamp(row: Mapping[str, Any]) -> datetime | date | None:
    for key in _TIMESTAMP_KEYS:
        value = row.get(key)
        if isinstance(value, datetime):
            return value
    return None


def _period_of(row: Mapping[str, Any]) -> str | None:
    start = row.get("start_date") or row.get("period_start")
    end = row.get("end_date") or row.get("period_end")
    if start and end:
        return f"{start}/{end}"
    return None


def _wire_timestamp(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return f"{value.isoformat()}T00:00:00.000000Z"


def _parse_timestamp(value: str | None, identity: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp is not ISO-8601", identity) from exc
    if parsed.tzinfo is None:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp must carry a timezone", identity)
    return parsed


def _json_safe(value: Any) -> Any:
    """行内容 → 规范 JSON 安全结构（用于摘要；不推断业务单位/语义）。"""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    item_method = getattr(value, "item", None)
    if callable(item_method):
        return _json_safe(item_method())
    return str(value)
