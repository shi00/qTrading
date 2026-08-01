"""ui/components/error_history_store.py 单元测试 (Issue #448).

验证维度:
1. ErrorHistoryEntry / ErrorHistoryState 数据结构契约
2. get_global_state 单例 (线程安全初始化)
3. record_error 记录错误 (含 R9 脱敏兜底, M1 修复)
4. clear_history 清空历史
5. MAX_ERROR_HISTORY 上限 (FIFO 截断)
6. _reset_state_for_test 测试隔离
7. record_error 线程安全 (B1 修复: 不嵌套 acquire _state_lock)
"""

import datetime
import threading
from unittest.mock import AsyncMock, patch

import flet as ft
import pytest

from ui.components.error_history_store import (
    MAX_ERROR_HISTORY,
    ErrorHistoryEntry,
    ErrorHistoryState,
    _reset_state_for_test,
    clear_history,
    get_global_state,
    open_github_issues,
    record_error,
)
from ui.components import error_history_store as mod


@pytest.fixture(autouse=True)
def _reset_state_per_test():
    """每条测试前重置全局 state (双重保险, conftest.py 已注册 autouse fixture)."""
    _reset_state_for_test()
    yield
    _reset_state_for_test()


# ============================================================================
# 1. 数据结构契约
# ============================================================================


class TestErrorHistoryEntry:
    """ErrorHistoryEntry frozen dataclass 契约守护."""

    def test_is_frozen_dataclass(self):
        """ErrorHistoryEntry 必须是 frozen dataclass."""
        import dataclasses

        entry = ErrorHistoryEntry(
            timestamp=datetime.datetime.now(),
            source="test",
            title="title",
            message="message",
        )
        assert hasattr(entry, "__dataclass_fields__")
        # frozen dataclass 赋值应抛 FrozenInstanceError, 强断言异常类型 + 原值未被修改
        with pytest.raises(dataclasses.FrozenInstanceError) as exc_info:
            entry.title = "modified"  # type: ignore[misc]
        assert isinstance(exc_info.value, dataclasses.FrozenInstanceError)
        assert entry.title == "title"

    def test_details_default_empty(self):
        """details 字段默认空字符串."""
        entry = ErrorHistoryEntry(
            timestamp=datetime.datetime.now(),
            source="test",
            title="t",
            message="m",
        )
        assert entry.details == ""

    def test_full_construction(self):
        """完整构造字段断言."""
        ts = datetime.datetime.now()
        entry = ErrorHistoryEntry(
            timestamp=ts,
            source="backtest",
            title="回测失败",
            message="回测执行失败",
            details="ValueError: invalid config",
        )
        assert entry.timestamp == ts
        assert entry.source == "backtest"
        assert entry.title == "回测失败"
        assert entry.message == "回测执行失败"
        assert entry.details == "ValueError: invalid config"


class TestErrorHistoryState:
    """ErrorHistoryState observable dataclass 契约守护."""

    def test_is_ft_observable_subclass(self):
        """ErrorHistoryState 必须继承 ft.Observable (声明式状态源)."""
        assert issubclass(ErrorHistoryState, ft.Observable)

    def test_default_empty_errors(self):
        """默认 errors 列表为空."""
        state = ErrorHistoryState()
        assert state.errors == []

    def test_is_mutable_dataclass(self):
        """ErrorHistoryState 是 mutable dataclass (errors 列表可替换触发 observable)."""
        state = ErrorHistoryState()
        state.errors = [ErrorHistoryEntry(datetime.datetime.now(), "s", "t", "m")]
        assert len(state.errors) == 1


# ============================================================================
# 2. get_global_state 单例
# ============================================================================


class TestGetGlobalState:
    """get_global_state 单例行为."""

    def test_returns_same_instance(self):
        """多次调用返回同一实例 (单例)."""
        s1 = get_global_state()
        s2 = get_global_state()
        assert s1 is s2

    def test_returns_observable_state(self):
        """返回类型必须是 ErrorHistoryState."""
        state = get_global_state()
        assert isinstance(state, ErrorHistoryState)

    def test_thread_safe_initialization(self):
        """多线程并发调用 get_global_state 不抛异常, 返回同一实例."""
        # 重置状态确保并发初始化场景
        _reset_state_for_test()

        results: list[ErrorHistoryState] = []
        barrier = threading.Barrier(8)

        def _worker():
            barrier.wait()
            results.append(get_global_state())

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 8
        first = results[0]
        assert all(r is first for r in results), "并发调用应返回同一实例"


# ============================================================================
# 3. record_error 记录错误 (含 R9 脱敏)
# ============================================================================


class TestRecordError:
    """record_error 行为 + R9 脱敏."""

    def test_appends_entry_to_head(self):
        """record_error 在列表头部插入新错误 (最新在前)."""
        record_error(source="s1", title="t1", message="m1")
        record_error(source="s2", title="t2", message="m2")

        state = get_global_state()
        assert len(state.errors) == 2
        assert state.errors[0].source == "s2"  # 最新的在前
        assert state.errors[1].source == "s1"

    def test_records_timestamp(self):
        """record_error 自动记录当前时间戳."""
        before = datetime.datetime.now()
        record_error(source="s", title="t", message="m")
        after = datetime.datetime.now()

        state = get_global_state()
        ts = state.errors[0].timestamp
        assert before <= ts <= after

    def test_records_all_fields(self):
        """record_error 记录 source/title/message/details 全部字段."""
        record_error(
            source="sql_console",
            title="SQL 失败",
            message="执行失败",
            details="SyntaxError: near 'FROM'",
        )
        entry = get_global_state().errors[0]
        assert entry.source == "sql_console"
        assert entry.title == "SQL 失败"
        assert entry.message == "执行失败"
        assert entry.details == "SyntaxError: near 'FROM'"

    def test_empty_title_message_handled(self):
        """空 title/message 不会抛异常."""
        record_error(source="s", title="", message="")
        entry = get_global_state().errors[0]
        assert entry.title == ""
        assert entry.message == ""

    def test_sanitizes_title_with_token(self):
        """R9 兜底: title 中包含 token=xxx 被脱敏为 token=***."""
        record_error(
            source="s",
            title="失败 token=abcdef123456",
            message="msg",
        )
        entry = get_global_state().errors[0]
        assert "abcdef123456" not in entry.title
        assert "***" in entry.title

    def test_sanitizes_message_with_token(self):
        """R9 兜底: message 中包含 token=xxx 被脱敏."""
        record_error(
            source="s",
            title="t",
            message="error: api_key=secret_value_abc",
        )
        entry = get_global_state().errors[0]
        assert "secret_value_abc" not in entry.message
        assert "***" in entry.message

    def test_sanitizes_details_with_token(self):
        """R9 兜底: details 中包含 token=xxx 被脱敏."""
        record_error(
            source="s",
            title="t",
            message="m",
            details="Exception: Authorization: Bearer secret_token_xyz",
        )
        entry = get_global_state().errors[0]
        assert "secret_token_xyz" not in entry.details
        assert "***" in entry.details

    def test_sanitizes_windows_path_in_details(self):
        """R9 兜底: details 中包含 Windows 路径被脱敏为 <PATH>."""
        record_error(
            source="s",
            title="t",
            message="m",
            details="FileNotFoundError: C:\\Users\\admin\\secrets\\key.txt",
        )
        entry = get_global_state().errors[0]
        assert "C:\\Users\\admin" not in entry.details
        assert "<PATH>" in entry.details

    def test_thread_safe_concurrent_calls_no_deadlock(self):
        """B1 修复: 多线程并发 record_error 不死锁 (不嵌套 acquire _state_lock)."""
        barrier = threading.Barrier(8)

        def _worker(i):
            barrier.wait()
            for j in range(20):
                record_error(source=f"s{i}", title=f"t{i}_{j}", message=f"m{i}_{j}")

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # 160 条记录会被截断到 MAX_ERROR_HISTORY
        state = get_global_state()
        assert len(state.errors) == MAX_ERROR_HISTORY


# ============================================================================
# 4. clear_history
# ============================================================================


class TestClearHistory:
    """clear_history 行为."""

    def test_clears_all_errors(self):
        """clear_history 清空所有错误记录."""
        record_error(source="s1", title="t1", message="m1")
        record_error(source="s2", title="t2", message="m2")
        assert len(get_global_state().errors) == 2

        clear_history()
        assert get_global_state().errors == []

    def test_clear_when_already_empty(self):
        """空状态调用 clear_history 不抛异常."""
        clear_history()
        assert get_global_state().errors == []

    def test_can_record_after_clear(self):
        """清空后仍可继续记录新错误."""
        record_error(source="s1", title="t1", message="m1")
        clear_history()
        record_error(source="s2", title="t2", message="m2")

        state = get_global_state()
        assert len(state.errors) == 1
        assert state.errors[0].source == "s2"


# ============================================================================
# 5. MAX_ERROR_HISTORY 上限
# ============================================================================


class TestMaxHistoryLimit:
    """MAX_ERROR_HISTORY FIFO 截断行为."""

    def test_truncates_to_max_when_exceeded(self):
        """记录超过上限时自动截断到 MAX_ERROR_HISTORY."""
        for i in range(MAX_ERROR_HISTORY + 5):
            record_error(source="s", title=f"t{i}", message=f"m{i}")

        state = get_global_state()
        assert len(state.errors) == MAX_ERROR_HISTORY

    def test_keeps_latest_entries(self):
        """截断后保留最新的 MAX_ERROR_HISTORY 条."""
        for i in range(MAX_ERROR_HISTORY + 5):
            record_error(source="s", title=f"t{i}", message=f"m{i}")

        state = get_global_state()
        titles = [e.title for e in state.errors]
        # 最新的在前, 第一条是最后记录的 t{MAX_ERROR_HISTORY+4}
        assert titles[0] == f"t{MAX_ERROR_HISTORY + 4}"
        # 末尾保留 t5 (倒数第 MAX_ERROR_HISTORY 条)
        assert titles[-1] == "t5"


# ============================================================================
# 6. _reset_state_for_test
# ============================================================================


class TestResetStateForTest:
    """_reset_state_for_test 测试隔离."""

    def test_clears_existing_errors(self):
        """重置后 errors 为空."""
        record_error(source="s", title="t", message="m")
        assert len(get_global_state().errors) == 1

        _reset_state_for_test()
        assert get_global_state().errors == []

    def test_creates_fresh_instance_after_reset(self):
        """重置后 get_global_state 返回新实例 (老引用失效)."""
        old_state = get_global_state()
        _reset_state_for_test()
        new_state = get_global_state()
        assert old_state is not new_state


# ============================================================================
# 7. open_github_issues (mock 验证)
# ============================================================================


class TestOpenGithubIssues:
    """open_github_issues 行为 — 验证 webbrowser.open 经 ThreadPoolManager offload (R16)."""

    def test_returns_when_no_page_context(self):
        """无 ft.context.page 时不抛异常, 直接返回."""
        with patch("ui.components.error_history_store.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            # 不应抛异常
            open_github_issues()

    def test_offloads_webbrowser_open_via_thread_pool(self):
        """R16: webbrowser.open 经 ThreadPoolManager.run_async 提交到 IO 线程池."""
        # Mock page.run_task 捕获 coroutine
        captured_coro = []

        class _FakePage:
            def run_task(self, coro):
                captured_coro.append(coro)

        fake_page = _FakePage()

        with (
            patch("ui.components.error_history_store.ft.context") as mock_ctx,
            patch("ui.components.error_history_store.webbrowser"),
            patch.object(mod.ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async,
        ):
            type(mock_ctx).page = property(lambda self: fake_page)
            mock_run_async.return_value = None

            open_github_issues()

            # page.run_task 被调用, 捕获了 coroutine
            assert len(captured_coro) == 1
