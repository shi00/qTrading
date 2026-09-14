"""EgressAuditViewModel — 数据出口审计聚合 ViewModel (SEC-03)。

承担「数据出口」面板的聚合展示数据编排：读取 EgressAudit 单例的今日/本月
按目的地聚合 + 本会话外发计数，暴露给 View（面板表格 + 状态栏指示器）。

设计要点（CLAUDE.md §3.2 MVVM）：
- frozen state snapshot (EgressAuditState dataclass)
- 纯状态+命令层，不 import flet / 不感知 locale
- 同步阻塞聚合查盘经 EgressAudit.get_aggregates()（内部经 ThreadPoolManager IO 池，
  R16），VM 仅 await 该 command
- R2: asyncio.CancelledError 显式 raise，不被 except Exception 吞没
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EgressAggRow:
    """按目的地聚合的一行（面板表格）。"""

    destination: str  # "llm:<effective_model>"
    count: int  # 次数
    payload_size_bytes: int  # 数据量


@dataclass(frozen=True)
class EgressAuditState:
    """EgressAuditViewModel 的不可变 state snapshot。"""

    # 今日/本月按目的地聚合（面板表格行）；空 list 表示未加载或暂无记录
    today_rows: list[EgressAggRow] = field(default_factory=list)
    month_rows: list[EgressAggRow] = field(default_factory=list)
    # 本会话外发计数（状态栏指示器）
    session_count: int = 0
    # 是否已成功加载聚合（区分「未加载」与「已加载但无记录」）
    loaded: bool = False


class EgressAuditViewModel(ObservableViewModelMixin[EgressAuditState]):
    """数据出口审计聚合 ViewModel（SEC-03 面板 + 状态栏指示器）。"""

    def __init__(self) -> None:
        self._state = EgressAuditState()
        self._init_mixin_fields()

    async def load_aggregates(self) -> None:
        """读取今日/本月聚合 + 会话计数到 state。

        EgressAudit.get_aggregates() 内部经 ThreadPoolManager IO 池扫 JSONL 文件
        （唯一事实源），不阻塞事件循环线程（R16）。async-native，无需再包线程池。
        R2: asyncio.CancelledError 必须 raise，不被 except Exception 吞没。
        """
        try:
            from utils.egress_audit import EgressAudit as _EgressAudit

            today_map, month_map = await _EgressAudit().get_aggregates()
            session_count = _EgressAudit().get_session_egress_count()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:  # noqa: BLE001 -- 聚合读取失败降级为空态, 不阻断 UI
            logger.debug("[EgressAuditVM] Failed to load egress aggregates: %s", ex)
            self._set_state(
                today_rows=[],
                month_rows=[],
                session_count=0,
                loaded=False,
            )
            return

        self._set_state(
            today_rows=_to_rows(today_map),
            month_rows=_to_rows(month_map),
            session_count=session_count,
            loaded=True,
        )


def _to_rows(agg: dict[str, tuple[int, int]]) -> list[EgressAggRow]:
    """把 {destination: (count, bytes)} 映射为行列表（按次数降序，供 UI 排序展示）。"""
    rows = [
        EgressAggRow(
            destination=dest,
            count=count,
            payload_size_bytes=total_bytes,
        )
        for dest, (count, total_bytes) in agg.items()
    ]
    rows.sort(key=lambda r: r.count, reverse=True)
    return rows
