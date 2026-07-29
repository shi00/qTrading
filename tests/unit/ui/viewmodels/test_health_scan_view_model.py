"""HealthScanViewModel 单元测试 (P1-2 MVVM 下沉).

测试 VM state/commands, 不依赖 Flet 渲染。覆盖：
- frozen state 不可变 (HealthScanState)
- DataProcessor 经构造函数注入 (DI)
- start_scan() 成功/失败/取消/None 路径
- R2 CancelledError 显式 raise
- on_progress 跨线程回调用 run_coroutine_threadsafe 调度回主 loop (R11)
- cancel_pending_futures() 取消 pending futures (R2 兼容不重新抛出)
- subscribe / _notify / dispose

由 ``test_health_report_dialog.py::TestHealthScanDialogComponent`` 中的
data_processor/scan/on_progress/cleanup 业务逻辑迁移至本 VM 单测（声明式 View
不测业务逻辑，仅测声明式契约）。
"""

import asyncio
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ui.viewmodels.health_scan_view_model import (
    HealthScanState,
    HealthScanViewModel,
)
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

pytestmark = pytest.mark.unit


# --- Fixtures ---


def _make_data_processor(scan_side_effect: Any = None) -> MagicMock:
    """构造 mock DataProcessor，run_quality_scan 可配置返回值/副作用。"""
    dp = MagicMock()
    if scan_side_effect is None:
        dp.run_quality_scan = AsyncMock(
            return_value={
                "score": 90,
                "tier": 3,
                "avg_lag": 1,
                "avg_continuity": 0.95,
                "avg_fundamental": 0.8,
                "fin_recency_ok": True,
                "sample_size": 50,
            }
        )
    else:
        dp.run_quality_scan = scan_side_effect
    return dp


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self):
        vm = HealthScanViewModel()
        with pytest.raises(FrozenInstanceError):
            vm.state.scan_state = "error"  # type: ignore[misc]

    def test_state_default_values(self):
        vm = HealthScanViewModel()
        assert vm.state.scan_state == "idle"
        assert vm.state.progress == 0.0
        assert vm.state.status_text == ""
        assert vm.state.result is None
        assert vm.state.error_key is None


# --- Subscribe / notify ---


class TestSubscribeNotify:
    def test_subscribe_receives_state_changes(self):
        vm = HealthScanViewModel()
        received: list[HealthScanState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._set_state(scan_state="scanning")
        assert len(received) == 1
        assert received[0].scan_state == "scanning"

    def test_unsubscribe_stops_receiving(self):
        vm = HealthScanViewModel()
        received: list[HealthScanState] = []
        unsub = vm.subscribe(lambda s: received.append(s))
        unsub()
        vm._set_state(scan_state="scanning")
        assert len(received) == 0

    def test_unsubscribe_when_callback_already_removed_is_noop(self):
        """二次退订：callback 已不在列表，no-op（防御分支）。"""
        vm = HealthScanViewModel()
        callback = MagicMock()
        unsub = vm.subscribe(callback)
        unsub()
        assert callback not in vm._subscribers
        unsub()  # 二次：no-op，不抛异常
        assert callback not in vm._subscribers

    def test_subscribe_captures_main_loop_when_running(self):
        """subscribe 在事件循环中调用时捕获 _main_loop（R11 loop-local 守卫）。"""
        vm = HealthScanViewModel()

        async def _subscribe_in_loop() -> None:
            vm.subscribe(lambda s: None)

        asyncio.run(_subscribe_in_loop())
        assert vm._main_loop is not None
        assert isinstance(vm._main_loop, asyncio.AbstractEventLoop)

    def test_subscribe_without_running_loop_logs_debug(self):
        """subscribe 在无事件循环时（如测试上下文）不抛异常，_main_loop 保持 None。"""
        vm = HealthScanViewModel()
        vm.subscribe(lambda s: None)
        assert vm._main_loop is None


# --- start_scan ---


class TestStartScan:
    @pytest.mark.asyncio
    async def test_data_processor_none_sets_error_state(self):
        """data_processor=None 时设置 error state（error_key 为 i18n key）。"""
        vm = HealthScanViewModel(data_processor=None)
        await vm.start_scan()
        assert vm.state.scan_state == "error"
        assert vm.state.error_key == "db_err_format"
        assert vm.state.result is None

    @pytest.mark.asyncio
    async def test_scan_success_sets_done_state(self):
        """run_quality_scan 成功 → state.scanning→done + result 正确设置。"""
        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        received: list[HealthScanState] = []
        vm.subscribe(lambda s: received.append(s))

        await vm.start_scan()

        assert vm.state.scan_state == "done"
        assert vm.state.result is not None
        assert vm.state.result["score"] == 90
        # state 转换序列：scanning → done
        assert [s.scan_state for s in received] == ["scanning", "done"]
        # DataProcessor 调用参数（sample_size 默认 50，progress_callback 注入）
        dp.run_quality_scan.assert_awaited_once()
        call_kwargs = dp.run_quality_scan.call_args.kwargs
        assert call_kwargs["sample_size"] == 50
        assert callable(call_kwargs["progress_callback"])

    @pytest.mark.asyncio
    async def test_scan_exception_sets_error_state(self):
        """run_quality_scan 抛 Exception → state 设置 error（不传播异常）。"""
        dp = _make_data_processor(scan_side_effect=AsyncMock(side_effect=RuntimeError("scan failed")))
        vm = HealthScanViewModel(data_processor=dp)

        await vm.start_scan()

        assert vm.state.scan_state == "error"
        assert vm.state.error_key == "db_err_format"
        assert vm.state.result is None

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """R2: CancelledError 必须 raise，不被 except Exception 吞没。"""
        dp = _make_data_processor(scan_side_effect=AsyncMock(side_effect=asyncio.CancelledError()))
        vm = HealthScanViewModel(data_processor=dp)

        with pytest.raises(asyncio.CancelledError):
            await vm.start_scan()

    @pytest.mark.asyncio
    async def test_start_scan_captures_main_loop(self):
        """start_scan 在事件循环中调用时捕获 _main_loop（R11 loop-local 守卫）。

        Phase2 P2-4 修复后：Mixin.subscribe() 统一捕获 loop（替代旧的 start_scan 手动捕获）。
        真实 View 总是先 subscribe 再 action，因此测试中 subscribe 先被调用。
        """
        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        # 模拟真实 View 初始化流程：先 subscribe（Mixin 捕获 loop/tid），再 action
        unsub = vm.subscribe(lambda _s: None)

        await vm.start_scan()

        assert vm._main_loop is not None
        assert isinstance(vm._main_loop, asyncio.AbstractEventLoop)
        unsub()


# --- on_progress (Mixin 统一跨线程通知) ---


class TestOnProgress:
    @pytest.mark.asyncio
    async def test_on_progress_updates_state_via_mixin(self):
        """Phase2 P2-4: on_progress 回调通过 Mixin._set_state 通知, Mixin 负责跨线程安全。

        旧实现：VM 手动调 run_coroutine_threadsafe 调度协程回主线程
                → 风险：重复、遗漏、R11 同步原语跨循环复用
        新实现：VM 只调 _set_state，Mixin 统一处理 loop 判定/节流/跨线程
        """
        captured_cb: list[Any] = []

        async def fake_scan(*args: Any, **kwargs: Any) -> dict:
            cb = kwargs.get("progress_callback")
            if cb:
                captured_cb.append(cb)
                cb(5, 10, "scanning...")
            return {"score": 90, "tier": 3, "avg_lag": 1, "avg_continuity": 0.95}

        dp = MagicMock()
        dp.run_quality_scan = fake_scan
        vm = HealthScanViewModel(data_processor=dp)
        received: list[HealthScanState] = []
        # 真实 View 先 subscribe 捕获 loop/tid
        vm.subscribe(lambda s: received.append(s))

        await vm.start_scan()

        # cb 被调用过 → state 已更新（无需手动 RCT，Mixin 统一分发）
        assert len(captured_cb) == 1
        assert vm.state.progress == pytest.approx(0.5)
        assert vm.state.status_text == "scanning..."
        # 至少收到一次 progress 更新通知
        assert any(s.progress == pytest.approx(0.5) for s in received)

    @pytest.mark.asyncio
    async def test_on_progress_future_tracking_legacy(self):
        """cancel_pending_futures 仍可用（为旧代码/自定义 future 兼容）。

        Phase2 P2-4 后：on_progress 不再调度 future（Mixin 直接 dispatch），
        因此 VM 自己的 _futures 集合不填充（与 Mixin 无关）。
        cancel_pending_futures 仍可由外部代码手动 add 的 future 触发。
        """
        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)

        await vm.start_scan()

        # on_progress 不再使用 future（由 Mixin 统一分发）
        # 手动填充 future 以验证 cancel_pending_futures 机制仍可用
        mock_future = MagicMock()
        mock_future.done.return_value = False
        vm._futures.add(mock_future)
        assert len(vm._futures) == 1

        vm.cancel_pending_futures()
        mock_future.cancel.assert_called_once()
        assert len(vm._futures) == 0

    @pytest.mark.asyncio
    async def test_update_progress_updates_state(self):
        """on_progress (简化后直接 _set_state) 更新 progress/status_text state。

        Phase2 P2-4: 移除了冗余的 _update_progress 包装协程；进度回调直接
        走 _set_state → Mixin 通知分发。此处用等价的 _set_state 验证契约。
        """
        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        received: list[HealthScanState] = []
        vm.subscribe(lambda s: received.append(s))

        # 等价于旧 _update_progress(3, 10, "translating...")
        vm._set_state(progress=3 / 10, status_text="translating...")

        assert vm.state.progress == pytest.approx(0.3)
        assert vm.state.status_text == "translating..."
        assert len(received) >= 1


# --- cancel_pending_futures ---


class TestCancelPendingFutures:
    @pytest.mark.asyncio
    async def test_cancel_pending_futures_cancels_unfinished(self):
        """cancel_pending_futures 对未完成 future 调 cancel()。"""
        mock_future1 = MagicMock()
        mock_future1.done.return_value = False
        mock_future1.cancel = MagicMock()

        mock_future2 = MagicMock()
        mock_future2.done.return_value = False
        mock_future2.cancel = MagicMock()

        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        vm._futures.add(mock_future1)
        vm._futures.add(mock_future2)

        vm.cancel_pending_futures()

        mock_future1.cancel.assert_called_once()
        mock_future2.cancel.assert_called_once()
        assert len(vm._futures) == 0

    @pytest.mark.asyncio
    async def test_cancel_pending_futures_skips_done(self):
        """已 done 的 future 不调 cancel()（cancel 返回 False 无意义）。"""
        mock_future_done = MagicMock()
        mock_future_done.done.return_value = True
        mock_future_done.cancel = MagicMock()

        mock_future_pending = MagicMock()
        mock_future_pending.done.return_value = False
        mock_future_pending.cancel = MagicMock()

        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        vm._futures.add(mock_future_done)
        vm._futures.add(mock_future_pending)

        vm.cancel_pending_futures()

        mock_future_done.cancel.assert_not_called()
        mock_future_pending.cancel.assert_called_once()
        assert len(vm._futures) == 0

    def test_cancel_pending_futures_empty_set_no_error(self):
        """_futures 为空集合时不抛异常（初始化后未启动扫描场景）。"""
        vm = HealthScanViewModel()
        vm.cancel_pending_futures()  # 不抛异常
        assert len(vm._futures) == 0

    @pytest.mark.asyncio
    async def test_cancel_pending_futures_does_not_raise_cancelled_error(self):
        """R2 兼容：cancel_pending_futures 不向调用方传播 CancelledError。"""
        mock_future = MagicMock()
        mock_future.done.return_value = False
        # future.cancel() 触发 future 内部 CancelledError，但不向调用方传播
        mock_future.cancel = MagicMock()

        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        vm._futures.add(mock_future)

        # 不应抛出异常
        vm.cancel_pending_futures()
        mock_future.cancel.assert_called_once()


# --- dispose ---


class TestDispose:
    def test_dispose_clears_subscribers(self):
        vm = HealthScanViewModel()
        callback = MagicMock()
        vm.subscribe(callback)
        assert len(vm._subscribers) == 1

        vm.dispose()

        assert len(vm._subscribers) == 0
        # dispose 后 _set_state 不应通知任何订阅者
        vm._set_state(scan_state="error")
        callback.assert_not_called()

    def test_dispose_cancels_pending_futures(self):
        """dispose 内部调 cancel_pending_futures 清理 pending futures。"""
        mock_future = MagicMock()
        mock_future.done.return_value = False
        mock_future.cancel = MagicMock()

        vm = HealthScanViewModel()
        vm._futures.add(mock_future)

        vm.dispose()

        mock_future.cancel.assert_called_once()
        assert len(vm._futures) == 0

    def test_dispose_idempotent(self):
        """dispose 可多次调用不抛异常（防 use_viewmodel cleanup 重复触发）。"""
        vm = HealthScanViewModel()
        vm.subscribe(lambda s: None)
        vm.dispose()
        vm.dispose()  # 二次：no-op
        assert len(vm._subscribers) == 0

    def test_dispose_short_circuits_set_state(self):
        """P2-3: dispose 后 _set_state 短路, state 字段不更新 (对齐 ScreenerViewModel _disposed 模式).

        场景: 跨线程延迟回调 (run_coroutine_threadsafe 调度的 _update_progress) 在
        dispose 之后触发 _set_state; guard 使其短路, 避免更新已清理的 state.
        """
        vm = HealthScanViewModel()
        # 设置一个非默认值, 验证 dispose 后不被覆盖
        vm._set_state(scan_state="scanning", progress=0.5, status_text="before dispose")
        snapshot_before = vm.state

        vm.dispose()
        vm._set_state(scan_state="done", progress=1.0, status_text="after dispose")

        # state 不应被更新 (短路)
        assert vm.state is snapshot_before
        assert vm.state.scan_state == "scanning"
        assert vm.state.progress == 0.5
        assert vm.state.status_text == "before dispose"

    @pytest.mark.asyncio
    async def test_dispose_short_circuits_late_progress_callback(self):
        """P2-3: dispose 后 late progress callback (via _set_state) 被短路.

        场景: use_effect cleanup 调 dispose() 后, 工作线程回调的 late
        progress 继续尝试更新 state; Mixin._set_state 的 disposed guard
        使更新短路.
        """
        dp = _make_data_processor()
        vm = HealthScanViewModel(data_processor=dp)
        vm._set_state(scan_state="scanning", progress=0.3)
        snapshot_before = vm.state

        vm.dispose()
        # 等价于 late 到达的 progress callback：_set_state(progress=0.8)
        vm._set_state(progress=8 / 10, status_text="late update after dispose")

        # state 不应被 late callback 更新
        assert vm.state is snapshot_before
        assert vm.state.scan_state == "scanning"
        assert vm.state.progress == 0.3
        assert vm.state.status_text == ""


# --- DataProcessor DI ---


class TestDataProcessorInjection:
    def test_data_processor_default_none(self):
        """无参数构造时 data_processor=None（容错）。"""
        vm = HealthScanViewModel()
        assert vm._data_processor is None

    def test_data_processor_injected(self):
        """构造函数注入 DataProcessor 实例。"""
        dp = MagicMock()
        vm = HealthScanViewModel(data_processor=dp)
        assert vm._data_processor is dp

    @pytest.mark.asyncio
    async def test_data_processor_none_skips_run_quality_scan(self):
        """data_processor=None 时不调 run_quality_scan（早返回）。"""
        vm = HealthScanViewModel(data_processor=None)
        await vm.start_scan()
        # 直接断言 state 已转 error，无 run_quality_scan 调用（dp 为 None 无法断言 mock）
        assert vm.state.scan_state == "error"


# --- VM 契约守护 (R2/R11 守卫已下沉到 VM) ---


def _vm_source_without_docstrings() -> str:
    """VM 源码（去除 docstring），用于契约守护检查。

    避免源码 docstring 中提及被禁止的 API 名（作为变更说明）导致字符串匹配误判。
    参考 ``test_health_report_dialog_contract.py::_source_without_docstrings`` 范式。
    """
    import ast
    from pathlib import Path

    vm_path = Path(__file__).parent.parent.parent.parent.parent / "ui" / "viewmodels" / "health_scan_view_model.py"
    source = vm_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


class TestHealthScanViewModelContract:
    """HealthScanViewModel 契约守护测试。

    P1-2 MVVM 下沉后，R2 (CancelledError raise) / R11 (run_coroutine_threadsafe)
    守卫由 VM 承担，本类守护 VM 源码契约（不依赖运行时调用）。
    """

    def test_uses_cancelled_error_raise(self):
        """R2: VM 源码必须 ``except asyncio.CancelledError: raise``。"""
        source = _vm_source_without_docstrings()
        assert "except asyncio.CancelledError:" in source, "VM 必须捕获 CancelledError"
        assert "raise  # R2" in source, "CancelledError 必须 raise (R2)"

    def test_uses_run_coroutine_threadsafe(self):
        """Phase2 P2-4 更新：VM 不再手动 RCT，改为 Mixin 统一跨线程调度。

        旧实现：VM 源码必须显式使用 ``asyncio.run_coroutine_threadsafe``
        新实现：VM 继承 ObservableViewModelMixin，由 Mixin 统一执行
                ``call_soon_threadsafe`` / 同步直调判定 / 节流合并

        因此本契约：检查 Mixin 源码中确实存在跨线程调度机制，
        且 VM 继承 Mixin（不重复造轮子）。
        """
        import inspect
        from pathlib import Path

        # (1) VM 继承 Mixin
        assert issubclass(HealthScanViewModel, ObservableViewModelMixin), (
            "VM 必须继承 ObservableViewModelMixin 以统一跨线程安全"
        )

        # (2) Mixin 源码包含跨线程调度原语（call_soon_threadsafe 或等价安全路径）
        mixin_path = Path(inspect.getfile(ObservableViewModelMixin))
        mixin_source = mixin_path.read_text(encoding="utf-8")
        has_call_soon = "call_soon_threadsafe" in mixin_source
        has_sync_fallback = "_do_notify" in mixin_source  # 同步环境直调
        assert has_call_soon or has_sync_fallback, "Mixin 必须包含跨线程安全调度机制 (call_soon_threadsafe 或等价)"
        assert has_call_soon, "Mixin 必须用 call_soon_threadsafe 做跨线程调度"

    def test_no_i18n_get_in_vm(self):
        """CLAUDE.md §3.2: VM 不调 I18n.get，不感知 locale。

        VM 产出 error_key (i18n key)，View 渲染时 I18n.get(error_key)。
        docstring 中的提及作为变更说明，去除后检查。
        """
        source = _vm_source_without_docstrings()
        assert "I18n.get(" not in source, "VM 不应调 I18n.get (locale 由 View 渲染)"

    def test_data_processor_injected_via_constructor(self):
        """DI: DataProcessor 经构造函数注入（``__init__(data_processor=...)``）。"""
        import inspect

        sig = inspect.signature(HealthScanViewModel.__init__)
        assert "data_processor" in sig.parameters, "构造函数必须有 data_processor 参数"
        assert sig.parameters["data_processor"].default is None, "data_processor 默认 None (容错)"

    def test_state_is_frozen_dataclass(self):
        """CLAUDE.md §3.2: state 必须是 frozen dataclass (不可变 snapshot)。"""
        from dataclasses import is_dataclass

        assert is_dataclass(HealthScanState), "HealthScanState 必须是 dataclass"
        # frozen=True 时 setattr 抛 FrozenInstanceError，由 test_state_is_frozen 验证
        # 此处仅断言 dataclass 装饰
