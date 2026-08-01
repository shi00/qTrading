"""error_history_store — 错误历史全局状态存储 (Issue #448).

参考 toast_manager.py 的 ToastManagerState 模式:
- @ft.observable dataclass + module-level singleton
- get_global_state() / record_error() / clear_history() 函数式 API
- View 层在 except 块或 use_effect 中调用 record_error() (与 page.show_toast 同层)
- VM 不导入本模块 (CLAUDE.md §3.2 MVVM: VM 禁止 import flet)
- record_error 内部对 title/message/details 三字段强制 R9 脱敏兜底 (v3 M1)

线程安全 (v3 B1 修复):
- get_global_state() 内部用 _state_lock 保护 _state 初始化
- record_error()/clear_history() 不调用 get_global_state(), 直接访问 _state
  避免 threading.Lock 不可重入导致的死锁 (toast_manager.py 用两把独立锁,
  本模块用一把锁 + 直接访问 _state 模式)
"""

import datetime
import logging
import threading
import webbrowser
from dataclasses import dataclass, field

import flet as ft

from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

MAX_ERROR_HISTORY = 10
GITHUB_ISSUES_URL = "https://github.com/shi00/qTrading/issues/new"


@dataclass(frozen=True)
class ErrorHistoryEntry:
    """单条错误历史记录 (frozen)."""

    timestamp: datetime.datetime
    source: str  # "home" / "watchlist" / "backtest" / "sql_console" / "task_center"
    title: str  # 已脱敏的标题 (record_error 内部强制脱敏)
    message: str  # 已脱敏的消息 (record_error 内部强制脱敏)
    details: str = ""  # 已脱敏的错误详情 (record_error 内部强制脱敏)


@ft.observable
@dataclass
class ErrorHistoryState(ft.Observable):
    """错误历史 Observable 状态源 (对齐 ToastManagerState)."""

    errors: list[ErrorHistoryEntry] = field(default_factory=list)


_state: ErrorHistoryState | None = None
_state_lock = threading.Lock()


def get_global_state() -> ErrorHistoryState:
    """获取全局 ErrorHistoryState 单例 (线程安全).

    仅用于 View 订阅 (ft.use_state(get_global_state)), 内部加锁初始化 _state。
    record_error/clear_history 不调用本函数, 直接访问 _state 避免锁嵌套 (B1 修复)。
    """
    global _state
    with _state_lock:
        if _state is None:
            _state = ErrorHistoryState()
        return _state


def record_error(
    source: str,
    title: str,
    message: str,
    details: str = "",
) -> None:
    """记录一条错误到历史 (线程安全, 自动截断到 MAX_ERROR_HISTORY).

    R9 兜底 (v3 M1 修复): 对 title/message/details 三字段全部强制调用
    DataSanitizer.sanitize_error 脱敏, 即使调用方传入含敏感数据的
    i18n params 翻译结果也能保证不泄露。

    线程安全 (v3 B1 修复): 不调用 get_global_state(), 直接访问 _state,
    避免 threading.Lock 不可重入导致的死锁。
    """
    # R9: 三字段统一脱敏兜底 (M1 修复 — i18n params 可能含敏感数据)
    sanitized_title = DataSanitizer.sanitize_error(title) if title else ""
    sanitized_message = DataSanitizer.sanitize_error(message) if message else ""
    sanitized_details = DataSanitizer.sanitize_error(details) if details else ""

    entry = ErrorHistoryEntry(
        timestamp=datetime.datetime.now(),
        source=source,
        title=sanitized_title,
        message=sanitized_message,
        details=sanitized_details,
    )
    with _state_lock:
        # B1 修复: 直接访问 _state, 不调用 get_global_state() (避免死锁)
        global _state
        if _state is None:
            _state = ErrorHistoryState()
        new_list = [entry, *_state.errors]  # 最新的在前
        while len(new_list) > MAX_ERROR_HISTORY:
            new_list.pop()
        _state.errors = new_list  # 触发 @ft.observable 通知


def clear_history() -> None:
    """清空错误历史.

    线程安全 (v3 B1 修复): 不调用 get_global_state(), 直接访问 _state。
    """
    with _state_lock:
        global _state
        if _state is None:
            _state = ErrorHistoryState()
        _state.errors = []


def open_github_issues() -> None:
    """打开 GitHub issue 页面 (R16: webbrowser.open 经 ThreadPoolManager offload).

    供各 View 的 ErrorState on_contact_support 回调调用。
    通过 page.run_task 调度异步 offload, 需在 Flet 渲染上下文调用。
    """
    try:
        page = ft.context.page
    except RuntimeError:
        logger.warning("[ErrorHistory] Cannot open GitHub issues: page not available")
        return
    if page is None:
        return

    async def _open() -> None:
        await ThreadPoolManager().run_async(TaskType.IO, webbrowser.open, GITHUB_ISSUES_URL)

    page.run_task(_open)


def _reset_state_for_test() -> None:
    """测试隔离: 重置全局 state.

    必须在 tests/unit/conftest.py 全局 autouse fixture 中调用 (M3 修复, R7 合规),
    不能仅在新测试文件模块级注册。
    """
    global _state
    with _state_lock:
        _state = None


__all__ = [
    "ErrorHistoryEntry",
    "ErrorHistoryState",
    "MAX_ERROR_HISTORY",
    "GITHUB_ISSUES_URL",
    "get_global_state",
    "record_error",
    "clear_history",
    "open_github_issues",
    "_reset_state_for_test",
]
