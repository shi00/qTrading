"""ScreenerViewModel 历史模式与快照切换 mixin（C3-5 拆分）。

从 ``screener_view_model.py`` 按职责拆出：REALTIME/HISTORY 模式切换与 ``_realtime_snapshot``
保存/恢复（``switch_to_history``/``switch_to_realtime``）、历史树加载（``load_history_tree``/
``_build_history_tree_rows``/``_format_history_date``）、历史记录加载（``load_history_data``）、
历史查看状态（``set_history_viewing_status``）。

本 mixin 无独立状态，依赖宿主 VM 的共享成员（``_state``/``_full_results``/``_realtime_snapshot``/
``_stream_buffers``/``_discarded_buffer``/``_set_state`` 等）；跨 mixin 方法（如
``_update_pagination``）在组合实例上解析。``_state`` 以类级注解声明以满足类型检查。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable
from dataclasses import replace

import pandas as pd

from data.cache.cache_manager import CacheManager
from ui.viewmodels import Message
from ui.viewmodels.screener_types import (
    HistoryTreeRow,
    HistoryTreeState,
    RealtimeSnapshot,
    ScreenerState,
    StrategyRunRow,
)

logger = logging.getLogger(__name__)


class HistoryModeMixin:
    """历史模式与快照切换职责（C3-5）。组合进 ``ScreenerViewModel``。"""

    _state: ScreenerState
    _full_results: pd.DataFrame | None
    _realtime_snapshot: RealtimeSnapshot | None
    _ai_buffer: list[dict]
    _stream_buffers: dict[str, dict]
    _discarded_buffer: list[dict]
    _set_state: Callable[..., None]
    _update_pagination: Callable[..., None]
    cancel_retry: Callable[[], None]

    def switch_to_history(self):
        """Switch to HISTORY mode, snapshot current realtime state."""
        if self._state.mode == "HISTORY":
            return
        # UX-2.3: 切换到历史模式前若有正在进行的单股重试，取消并重置占位卡
        # 避免后台重试结束后无处回调、切回实时态时卡片永久停留在 is_analyzing
        self.cancel_retry()
        # Snapshot realtime state
        self._realtime_snapshot = RealtimeSnapshot(
            full_results=self._full_results,
            page_no=self._state.page_no,
            sort_column=self._state.sort_column,
            sort_ascending=self._state.sort_ascending,
            ai_buffer=self._ai_buffer[:],
            stream_cards=self._state.stream_cards,
            stream_buffers=dict(self._stream_buffers),
        )
        # Clear for history data
        self._full_results = None
        self._ai_buffer = []
        self._stream_buffers.clear()
        # C2b H1: 经唯一 owner 单帧原子产出「HISTORY + 空表 + 分页归零」,
        # 避免「mode 未切换 + 空表」或「HISTORY + 旧 REALTIME 切片」陈旧中间帧 (M-1/H-1)
        self._update_pagination(
            page_no=1,
            mode="HISTORY",
            sort_column=None,
            sort_ascending=True,
            stream_cards=(),
            history_tree=HistoryTreeState(),
        )
        logger.info("[ScreenerVM] Switched to HISTORY mode")

    def switch_to_realtime(self):
        """Switch back to REALTIME mode, restore snapshot."""
        if self._state.mode == "REALTIME":
            return
        # Restore snapshot
        snap = self._realtime_snapshot
        if snap is not None:
            self._full_results = snap.full_results
            pn = snap.page_no
            sc = snap.sort_column
            sa = snap.sort_ascending
            self._ai_buffer = snap.ai_buffer
            stream_cards = snap.stream_cards
            self._stream_buffers = snap.stream_buffers
            self._realtime_snapshot = None
            # U-3 fix: Merge discarded_buffer back to ai_buffer
            if self._discarded_buffer:
                self._ai_buffer.extend(self._discarded_buffer)
                logger.debug("[ScreenerVM] Merged %s discarded items back to ai_buffer", len(self._discarded_buffer))
                self._discarded_buffer = []
            # C2b H1: 恢复快照后经唯一 owner 单帧原子产出「REALTIME + 恢复内容 + 合法页码」,
            # 避免「mode=REALTIME + HISTORY 旧切片」陈旧中间帧 (M-1/H-1);
            # UX-04: 快照页码越界时 _update_pagination 已钳制到过滤后合法范围
            self._update_pagination(
                page_no=pn,
                mode="REALTIME",
                sort_column=sc,
                sort_ascending=sa,
                stream_cards=stream_cards,
            )
        else:
            self._set_state(mode="REALTIME")
        logger.info("[ScreenerVM] Switched to REALTIME mode")

    async def load_history_tree(self, append: bool = False) -> None:
        """加载历史树数据并更新 state.history_tree (Task 3.2: 不再返回 dict).

        Args:
            append: True 追加到现有 rows (load_more 路径); False 重置 rows (切换模式/初始加载).
        """
        cache = CacheManager()
        offset = self._state.history_tree.offset if append else 0
        df = await cache.screener_dao.get_history_tree(offset=offset)
        if df is None or df.empty:
            if not append:
                # 重置 rows (切换到 HISTORY 模式后无数据)
                self._set_state(
                    history_tree=replace(
                        self._state.history_tree,
                        rows=(),
                        offset=0,
                        has_more=False,
                    )
                )
            else:
                # append 路径下无更多数据, 仅隐藏 load_more
                self._set_state(history_tree=replace(self._state.history_tree, has_more=False))
            return

        new_rows = self._build_history_tree_rows(df)
        if append:
            merged_rows = self._state.history_tree.rows + new_rows
        else:
            merged_rows = new_rows
        self._set_state(
            history_tree=replace(
                self._state.history_tree,
                rows=merged_rows,
                offset=offset + len(df),
                has_more=len(df) >= 30,
            )
        )

    @staticmethod
    def _build_history_tree_rows(df: pd.DataFrame) -> tuple[HistoryTreeRow, ...]:
        """从 DataFrame 构建历史树行 (不依赖 I18n, 日期格式化内聚到 VM).

        策略名 strategy_name 为 raw key, View 渲染时调 translate_strategy_name 翻译 (§3.2).
        """
        # Group by trade_date -> {date: [{run_id, strategy_name, cnt}, ...]}
        # PRF-09: 避免 iterrows (每行构造 Series, 慢 ~17x); get_history_tree 列固定为
        # run_id/trade_date/strategy_name/cnt, to_numpy 列索引等价取列
        if df is None or df.empty:
            return ()

        required = ("trade_date", "strategy_name", "run_id", "cnt")
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise KeyError(f"Missing required history tree columns: {missing}")

        col_map = {col: i for i, col in enumerate(df.columns)}
        i_date = col_map["trade_date"]
        i_name = col_map["strategy_name"]
        i_run = col_map["run_id"]
        i_cnt = col_map["cnt"]

        arr = df.to_numpy(dtype=object)
        tree: dict[str, list[StrategyRunRow]] = {}
        for i in range(len(df)):
            date = str(arr[i, i_date])
            tree.setdefault(date, []).append(
                StrategyRunRow(
                    strategy_name=str(arr[i, i_name]),
                    run_id=str(arr[i, i_run]),
                    cnt=int(arr[i, i_cnt]),
                )
            )
        rows: list[HistoryTreeRow] = []
        for date_str, strategies in tree.items():
            display_date, d_key = HistoryModeMixin._format_history_date(date_str)
            total_cnt = sum(s.cnt for s in strategies)
            rows.append(
                HistoryTreeRow(
                    display_date=display_date,
                    d_key=d_key,
                    total_cnt=total_cnt,
                    strategies=tuple(strategies),
                )
            )
        return tuple(rows)

    @staticmethod
    def _format_history_date(date_str) -> tuple[str, str]:
        """格式化历史树日期: 返回 (display_date, internal_key).

        纯函数不依赖 I18n, 与 View 中同名函数保持一致行为 (Task 3.2 内聚到 VM).
        """
        if isinstance(date_str, (datetime.date, datetime.datetime)):
            display = date_str.strftime("%Y-%m-%d")
            key = display
        else:
            s = str(date_str)
            display = f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else s
            key = s
        return display, key

    async def load_history_data(self, trade_date: str, strategy_name: str | None = None, run_id: str | None = None):  # type: ignore[untyped]
        """Load historical screening records for a specific run_id, or fall back to trade_date/strategy_name.

        Task 3.2: VM 内聚 loading 管理 (View 不再 set_progress_visible).
        """
        self._set_state(loading=True)
        try:
            cache = CacheManager()
            df = await cache.screener_dao.get_history_records(trade_date, strategy_name, run_id)
            if df is not None and not df.empty:
                self._full_results = df
            else:
                self._full_results = pd.DataFrame()
            if df is not None and not df.empty and "ai_score" in df.columns:
                sort_column = "ai_score"
            else:
                sort_column = None
            # C2b H1: 数据内容变更后经唯一 owner 单帧原子产出分页元数据 + 当前页切片,
            # 避免「loading=False + 旧表格 + 旧 total」陈旧帧 (M-1)
            self._update_pagination(
                page_no=1,
                loading=False,
                sort_column=sort_column,
                sort_ascending=False,
            )
        except asyncio.CancelledError:
            self._set_state(loading=False)
            raise
        except Exception:
            self._set_state(loading=False)
            raise

    def set_history_viewing_status(
        self,
        date_str: str,
        strategy_name: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """设置历史查看状态到 state (R.2.6.3: 业务状态迁入 VM).

        接收 raw ``strategy_name`` (i18n key) 和 ``run_id``, 构造 Message 存入 state.
        VM 不感知 locale (§3.2), View 渲染时通过 ``_render_status_message`` 翻译
        ``label_key`` 后缀 params 为当前 locale 字符串.

        Args:
            date_str: 已格式化的日期字符串 (如 "2024-12-27")
            strategy_name: raw 策略名 i18n key (如 "strategy_oversold_name"), None 表示无策略
            run_id: 运行 ID, 优先于 strategy_name 显示 (locale-independent, 直接存入 label)
        """
        if run_id:
            params: dict = {"date": date_str, "label": f"#{run_id[:8]}"}
        elif strategy_name:
            params = {"date": date_str, "label_key": strategy_name}
        else:
            params = {"date": date_str, "label_key": "screener_all_strategies"}
        self._set_state(
            status_message=Message("screener_history_viewing", params),
            status_color="info",
            status_action_key=None,
        )
