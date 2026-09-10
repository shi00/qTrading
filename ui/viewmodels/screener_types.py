"""ScreenerViewModel 共享类型模块（C3-2 拆分：dataclass 与常量单一归属）。

将 ``screener_view_model.py`` 中的 immutable state dataclass 与流式卡片常量拆出，
消除 VM/View 对状态类型的重复 import 面。本模块为**类型只读正本**：所有 frozen
dataclass 供 VM 各职责 mixin 与 View 消费，``screener_view_model.py`` 负责再导出
以保持既有 ``from ui.viewmodels.screener_view_model import X`` 接口不变。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import pandas as pd

from ui.viewmodels import Message

# Stream card limit (moved from View, VM owns card lifecycle)
MAX_LOG_CARDS = 10
_MAX_LOG_CARDS = MAX_LOG_CARDS  # 向后兼容别名

__all__ = [
    "MAX_LOG_CARDS",
    "_MAX_LOG_CARDS",
    "LogEntry",
    "StreamCard",
    "StrategyRunRow",
    "HistoryTreeRow",
    "HistoryTreeState",
    "RealtimeSnapshot",
    "StrategyDepRow",
    "ScreenerRow",
    "ScreenerState",
]


@dataclass(frozen=True)
class LogEntry:
    """Single AI streaming log entry (immutable, §3.0.1)."""

    name: str
    score: float
    thinking: str


@dataclass(frozen=True)
class StreamCard:
    """Single streaming/AI placeholder card (immutable, state-driven).

    - is_analyzing=True: 占位卡 (并发非流式模式, ProgressRing + "分析中")
    - is_analyzing=False: 流式卡 (reasoning + content Markdown)
    """

    name: str
    reasoning: str = ""
    content: str = ""
    is_analyzing: bool = False
    error: str | None = None  # UX-2.3: 失败时终结为错误状态（含重试按钮）


@dataclass(frozen=True)
class StrategyRunRow:
    """历史树中单次回测运行行 (D10: 替换 'strategy_name'/'run_id'/'cnt' 裸 dict).

    strategy_name 为 raw key, View 渲染时调 translate_strategy_name 翻译 (§3.2 VM 不感知 locale).
    """

    strategy_name: str
    run_id: str
    cnt: int


@dataclass(frozen=True)
class HistoryTreeRow:
    """历史树单行 (immutable, state-driven, Task 3.2).

    VM 内聚日期格式化 (不依赖 I18n); strategies 中的 strategy_name 为 raw key,
    View 渲染时调 translate_strategy_name 翻译为当前 locale (§3.2 VM 不感知 locale).
    """

    display_date: str
    d_key: str
    total_cnt: int
    strategies: tuple[StrategyRunRow, ...]


@dataclass(frozen=True)
class HistoryTreeState:
    """历史树子结构 (immutable, state-driven, Task 3.2).

    View 不再持有 rows/offset/has_more/loading 的 use_state, 改为派生自
    state.history_tree (消除双轨状态, 每项业务状态只有一个 owner).
    """

    rows: tuple[HistoryTreeRow, ...] = ()
    offset: int = 0
    has_more: bool = False
    loading: bool = False


@dataclass(frozen=True)
class RealtimeSnapshot:
    """HISTORY 切换前的实时态快照 (M12-017 不可变加固: dict → frozen dataclass).

    frozen 契约使字段缺失在构造期即报错 (不再靠 switch_to_realtime 里 dict .get 静默缺省);
    ai_buffer/stream_buffers 为 VM 内部可变缓冲, 仅随快照整体保存/恢复引用, 不改写其内容.
    """

    full_results: pd.DataFrame | None
    page_no: int
    sort_column: str | None
    sort_ascending: bool
    ai_buffer: list[dict]
    stream_cards: tuple[StreamCard, ...]
    stream_buffers: dict[str, dict]


@dataclass(frozen=True)
class StrategyDepRow:
    """单个策略的依赖信息行 (D10: 替换 ``{"name": str, "missing_apis": list}`` 裸 dict).

    ``name_key`` 为策略 i18n raw key (VM 装配, 不经 I18n.get, §3.2 不感知 locale),
    View 渲染时按当前 locale 翻译; ``missing_apis`` 用 tuple 保证 frozen 契约.
    """

    key: str
    name_key: str
    missing_apis: tuple[str, ...] = ()


_EMPTY_ROW_MAPPING: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class ScreenerRow:
    """当前页单行原始数据 (C2b 消除双轨制, locale-neutral).

    ``values`` 为唯一 owner ``_update_pagination`` 生成的 column → raw value 只读映射
    (MappingProxyType), VM 不调用 I18n.get (§3.2); View 渲染期经 ``_build_table_data``
    按当前 locale 格式化 unit_yi/wan 等展示。frozen 契约 + 只读映射保证不可变。
    """

    values: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_ROW_MAPPING)

    def __eq__(self, other: object) -> bool:
        """NaN 感知的等价性比较 (第 3 轮对抗检视).

        Python 原生 ``nan == nan`` 恒为 False; A 股策略结果中亏损/无分红/未分析等字段
        普遍包含 NaN。若按原生比较, 切片中只要有任一 NaN 字段就会导致
        ``rows == self._state.current_page_rows`` 恒为 False, 击穿流式输出时的切片引用复用与 View memo。
        本方法在 keys 完全一致前提下, 将 float('nan') 视作等价。
        """
        if self is other:
            return True
        if not isinstance(other, ScreenerRow):
            return False
        if self.values.keys() != other.values.keys():
            return False
        for k, v1 in self.values.items():
            v2 = other.values[k]
            if v1 is v2 or v1 == v2:
                continue
            if isinstance(v1, float) and isinstance(v2, float) and math.isnan(v1) and math.isnan(v2):
                continue
            return False
        return True

    def __hash__(self) -> int:
        """对齐 frozen dataclass 哈希契约 (保证 a == b => hash(a) == hash(b))."""
        return hash(tuple(self.values.keys()))


@dataclass(frozen=True)
class ScreenerState:
    """Immutable state snapshot for ScreenerView (§3.0.1).

    DataFrame (_full_results) is held internally by VM (双轨制, C2b 收敛为唯一切片 owner);
    View 不再直读 _full_results / get_current_page_data, 改读 state.current_page_rows
    (locale-neutral 原始行), 分页/切片元数据原子由 _update_pagination 单点产出。
    """

    # Pagination
    page_no: int = 1
    page_size: int = 50
    total_pages: int = 0
    total_items: int = 0
    # C2b: 当前页 locale-neutral 原始行 (唯一切片 owner 在 _update_pagination 内生成)
    current_page_rows: tuple[ScreenerRow, ...] = ()
    # Sorting
    sort_column: str | None = None
    sort_ascending: bool = True
    # Status bar (message + color)
    loading: bool = False
    status_message: Message | None = None
    status_color: str = ""
    # Task 5.2: 质量门阻断时的跳转 action key (如 screener_action_go_sync), None 无 action
    status_action_key: str | None = None
    # D3-4: 策略执行期业务警告 (不可变空载 tuple)。与 status_message 并存——放成功/失败
    # 后 status_message 会被 "完成" 状态覆盖，故警告需独立字段，View 在结果区上方单独渲染横幅。
    warnings: tuple[Message, ...] = ()
    # AI streaming logs (append-only tuple)
    logs: tuple[LogEntry, ...] = ()
    # AI streaming/placeholder cards (state-driven, §3.2 MVVM)
    stream_cards: tuple[StreamCard, ...] = ()
    # Task 8.4: 卡片截断提示 — True 表示已有卡片被 _MAX_LOG_CARDS 截断
    stream_cards_truncated: bool = False
    # Strategy selection (R.2.1: 内聚到 VM, 消除 View 双源真相)
    selected_strategy: str | None = None
    tier_hint: str | None = None
    # Mode: "REALTIME" or "HISTORY"
    mode: str = "REALTIME"
    # Task unlock signal (View resets after consuming)
    task_unlocked: bool = False
    # Strategy loading (R.2.6.1: 业务状态迁入 VM, View 构建 Flet Options 时翻译)
    strategies_loaded: bool = False
    # D10: tuple[StrategyDepRow, ...] 不可变行序列 (frozen 契约, 替换裸 dict)
    # name_key 为 raw i18n key, View 渲染时翻译 — 消除 VM 感知 locale 与 stale 翻译
    strategies_with_dep: tuple[StrategyDepRow, ...] = ()
    # Strategy description (R.2.6.2: 业务状态迁入 VM, View 映射 color 标识符到 AppColors)
    # P3-ScreenerVM-I18n-Get-Residual: 改为 Message 结构 (desc_key + params),
    # View 渲染时翻译 (§3.2 VM 不感知 locale).
    strategy_desc: Message | None = None
    strategy_desc_color: str = "default"  # 语义标识符: "default"/"warning"
    # D3: 策略参数 (View 编辑中间态下沉 VM — 消除 View params_ref 双轨).
    # 每次 set_strategy_param 生成新 dict (不可变快照), 切换策略时 init 重置.
    # M12-017: dict → Mapping; set/init/reset 均经 MappingProxyType 只读包装,
    # External 无法就地写. (空默认仍为只读空 dict, 无熵)
    strategy_params: Mapping[str, Any] = field(default_factory=dict)
    # History tree (Task 3.2: 子结构内聚 rows/offset/has_more/loading, 消除 View 双轨状态)
    history_tree: HistoryTreeState = field(default_factory=HistoryTreeState)
    # UX-2.3: 重试中标志，View 派生 run_disabled 禁用主运行按钮
    is_retrying: bool = False
    # UX-04 (P2-01): 股票代码过滤 (ts_code 子串匹配, 空串=不过滤; 深链/手动输入两来源)
    stock_filter: str = ""
