"""ui/components/toast_manager.py 声明式重写契约守护测试 (Phase B.4).

验证维度（DoD）:
1. @ft.component 装饰（ToastCard / ToastManagerView）
2. 无命令式 API 残留（did_mount / .update() / page.overlay.append）
3. R2 CancelledError 传播（except 块必须 raise，cleanup 不吞没）
4. ToastManager 命令式 API 保留（show / stop_all / MAX_TOAST_COUNT）
5. stop_all 优雅停机（gather_for_shutdown_cleanup）

改造期策略（CLAUDE.md §3.3）: 混合态失败是已知技术债，本测试聚焦契约守护，
不验证运行时渲染行为（需 Flet 渲染管线，由集成测试覆盖）。
"""

import asyncio
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest
from flet.components.component import Component

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components import toast_manager as tm_module
from ui.components.toast_manager import (
    COLLAPSED_MAX_LINES,
    LONG_TEXT_THRESHOLD,
    ToastCard,
    ToastData,
    ToastManager,
    ToastManagerView,
    _resolve_color_icon,
    get_global_state,
)
from ui.theme import AppColors

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_toast_state():
    """每条测试前重置全局 state 和任务集合，避免跨测试泄漏。"""
    tm_module._reset_state_for_test()
    yield
    tm_module._reset_state_for_test()


# ============================================================================
# 1. @ft.component 装饰契约
# ============================================================================


class TestDeclarativeContract:
    """验证 ToastCard / ToastManagerView 是 @ft.component 函数组件。"""

    def test_toast_card_is_component(self):
        """ToastCard 必须是 @ft.component 装饰的函数（非类）。"""
        assert hasattr(ToastCard, "__component_impl__"), (
            "ToastCard 必须是 @ft.component 函数组件（应有 __component_impl__ 属性）"
        )
        assert not isinstance(ToastCard, type), "ToastCard 不能是类"

    def test_toast_manager_view_is_component(self):
        """ToastManagerView 必须是 @ft.component 装饰的函数。"""
        assert hasattr(ToastManagerView, "__component_impl__"), "ToastManagerView 必须是 @ft.component 函数组件"
        assert not isinstance(ToastManagerView, type), "ToastManagerView 不能是类"

    def test_toast_manager_not_ft_control_subclass(self):
        """ToastManager 不能继承 ft.Control（普通管理类）。"""
        assert not issubclass(ToastManager, ft.Control), "ToastManager 不能继承 ft.Control"

    def test_toast_data_is_dataclass(self):
        """ToastData 必须是 dataclass（不可变数据载体）。"""
        assert hasattr(ToastData, "__dataclass_fields__"), "ToastData 必须是 dataclass"

    def test_toast_manager_state_is_observable(self):
        """ToastManagerState 必须继承 ft.Observable（声明式状态源）。"""
        assert issubclass(tm_module.ToastManagerState, ft.Observable), "ToastManagerState 必须继承 ft.Observable"


# ============================================================================
# 2. 无命令式 API 残留
# ============================================================================


class TestNoImperativeAPI:
    """验证 toast_manager.py 中无命令式 API 残留（DoD grep 验收）。"""

    @pytest.fixture
    def source(self):
        return inspect.getsource(tm_module)

    def test_no_did_mount(self, source):
        """不能有 did_mount（改用 use_effect）。"""
        assert "did_mount" not in source, "did_mount 必须移除（改用 use_effect）"

    def test_no_update_call(self, source):
        """不能有 .update() 调用（声明式自动渲染）。"""
        assert ".update()" not in source, ".update() 必须移除（声明式组件由框架自动渲染）"

    def test_no_page_overlay_append(self, source):
        """不能有 page.overlay.append（消费方负责挂载）。"""
        assert "page.overlay.append" not in source, "page.overlay.append 必须移除（消费方负责挂载 ToastManagerView）"

    def test_no_class_ft_container(self, source):
        """不能有 class XXX(ft.Container)（ToastCard 必须是函数组件）。"""
        # 排除文档字符串中的描述
        code_lines = [line for line in source.splitlines() if not line.strip().startswith(("#", '"', "'"))]
        code = "\n".join(code_lines)
        assert "class ToastCard(ft.Container)" not in code, (
            "ToastCard 不能是 ft.Container 子类（必须是 @ft.component 函数）"
        )
        assert "class ToastManager(ft.Container)" not in code, "ToastManager 不能是 ft.Container 子类"

    def test_no_self_update(self, source):
        """不能有 self.update() 调用。"""
        assert "self.update()" not in source, "self.update() 必须移除"


# ============================================================================
# 3. R2 CancelledError 传播
# ============================================================================


class TestR2CancelledErrorPropagation:
    """验证 R2 CancelledError 传播（CLAUDE.md §3 红线 R2）。"""

    @pytest.fixture
    def source(self):
        return inspect.getsource(tm_module)

    def test_no_swallowed_cancelled_error(self, source):
        """except asyncio.CancelledError 块必须重新 raise。

        R2 红线：吞没 CancelledError 必须重新 raise 以配合优雅停机。
        本测试扫描所有 ``except asyncio.CancelledError`` 块，检查后续 5 行内
        是否有 ``raise``。
        """
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "except asyncio.CancelledError" in line:
                following = "\n".join(lines[i : i + 6])
                assert "raise" in following, f"except asyncio.CancelledError 必须重新 raise（行 {i + 1}）：{line}"

    def test_run_timer_raises_cancelled_error(self, source):
        """_run_timer 中 except asyncio.CancelledError 必须 raise（R2）。"""
        # 找到 _run_timer 函数体
        assert "except asyncio.CancelledError" in source, "_run_timer 必须捕获 CancelledError 以便日志记录后重新抛出"
        # 验证 except 块后有 raise
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "except asyncio.CancelledError" in line:
                following = "\n".join(lines[i : i + 6])
                assert "raise" in following, "except asyncio.CancelledError 块必须包含 raise（R2 红线）"

    def test_cleanup_awaits_cancelled_task(self, source):
        """use_effect cleanup 必须等待 task 取消完成（R2 CancelledError 传播）。"""
        # cleanup 中必须 await task（通过 gather_for_shutdown_cleanup）
        assert "gather_for_shutdown_cleanup" in source, (
            "cleanup 必须使用 gather_for_shutdown_cleanup 等待 task 取消完成"
        )

    def test_stop_all_uses_gather_for_shutdown(self, source):
        """stop_all 必须使用 gather_for_shutdown_cleanup（优雅停机）。"""
        assert "gather_for_shutdown_cleanup" in source, "stop_all 必须使用 gather_for_shutdown_cleanup（R2 + 优雅停机）"

    def test_no_bare_except_cancelled_error_pass(self, source):
        """不能有 ``except asyncio.CancelledError: pass`` 吞没模式。"""
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "except asyncio.CancelledError" in line:
                # 检查后续 3 行内不能有单独的 pass（无 raise）
                following = "\n".join(lines[i : i + 4])
                if "pass" in following and "raise" not in following:
                    pytest.fail(f"except asyncio.CancelledError 不能用 pass 吞没（行 {i + 1}）")


# ============================================================================
# 4. ToastManager 命令式 API 保留
# ============================================================================


class TestToastManagerAPI:
    """验证 ToastManager 命令式 API 保留（消费方兼容性）。"""

    def test_show_method_exists(self):
        assert hasattr(ToastManager, "show"), "ToastManager.show 必须保留"

    def test_stop_all_method_exists(self):
        assert hasattr(ToastManager, "stop_all"), "ToastManager.stop_all 必须保留"

    def test_max_toast_count_exists(self):
        assert hasattr(ToastManager, "MAX_TOAST_COUNT"), "ToastManager.MAX_TOAST_COUNT 必须保留"
        assert ToastManager.MAX_TOAST_COUNT == 5

    def test_init_accepts_page(self):
        """__init__ 接受 page 参数（main.py 兼容）。"""
        page = MagicMock()
        manager = ToastManager(page)
        assert manager.page is page

    def test_init_accepts_none(self):
        manager = ToastManager(None)
        assert manager.page is None


# ============================================================================
# 5. show() 行为契约
# ============================================================================


class TestShowBehavior:
    """验证 show() 行为契约。"""

    def _make_page(self):
        page = MagicMock()
        page.controls = [MagicMock()]  # 非空 controls
        return page

    def test_show_adds_toast_to_state(self):
        """show() 应将 toast 添加到全局 state。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("test message", toast_type="info")

        state = get_global_state()
        assert len(state.toasts) == 1
        assert state.toasts[0].message == "test message"
        assert state.toasts[0].duration == 10

    def test_show_respects_max_count(self):
        """show() 应限制最大 toast 数量。"""
        page = self._make_page()
        manager = ToastManager(page)
        for i in range(ToastManager.MAX_TOAST_COUNT + 3):
            manager.show(f"message {i}")

        state = get_global_state()
        assert len(state.toasts) == ToastManager.MAX_TOAST_COUNT

    def test_show_removes_oldest_when_exceeds_max(self):
        """超过最大数量时移除最旧的 toast。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("oldest")
        for i in range(ToastManager.MAX_TOAST_COUNT):
            manager.show(f"toast {i}")

        state = get_global_state()
        assert all(t.message != "oldest" for t in state.toasts)

    def test_show_does_nothing_when_stopping(self):
        """stop_all 后 show() 不应添加 toast。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager._is_stopping = True
        manager.show("should not appear")

        state = get_global_state()
        assert len(state.toasts) == 0

    def test_show_does_nothing_without_page(self):
        """无 page 时 show() 不应添加 toast。"""
        manager = ToastManager(None)
        manager.show("should not appear")

        state = get_global_state()
        assert len(state.toasts) == 0

    def test_show_does_nothing_with_empty_controls(self):
        """page.controls 为空时 show() 不应添加 toast（防御 Flet 崩溃）。"""
        page = MagicMock()
        page.controls = []
        manager = ToastManager(page)
        manager.show("should not appear")

        state = get_global_state()
        assert len(state.toasts) == 0

    def test_show_different_types(self):
        """show() 支持不同 toast_type。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("success", toast_type="success")
        manager.show("error", toast_type="error")
        manager.show("warning", toast_type="warning")
        manager.show("info", toast_type="info")

        state = get_global_state()
        assert len(state.toasts) == 4

    def test_show_assigns_unique_ids(self):
        """show() 应分配唯一 id。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("a")
        manager.show("b")
        manager.show("c")

        state = get_global_state()
        ids = [t.id for t in state.toasts]
        assert len(ids) == 3
        assert len(set(ids)) == 3  # 唯一


# ============================================================================
# 6. stop_all 优雅停机
# ============================================================================


class TestStopAll:
    """验证 stop_all 优雅停机契约。"""

    def _make_page(self):
        page = MagicMock()
        page.controls = [MagicMock()]
        return page

    @pytest.mark.asyncio
    async def test_stop_all_clears_toasts(self):
        """stop_all 应清空 toast 队列。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("a")
        manager.show("b")

        await manager.stop_all()

        state = get_global_state()
        assert len(state.toasts) == 0

    @pytest.mark.asyncio
    async def test_stop_all_sets_stopping_flag(self):
        """stop_all 应设置 _is_stopping 标志。"""
        page = self._make_page()
        manager = ToastManager(page)

        await manager.stop_all()

        assert manager._is_stopping is True

    @pytest.mark.asyncio
    async def test_stop_all_is_idempotent(self):
        """stop_all 应幂等（可多次调用）。"""
        page = self._make_page()
        manager = ToastManager(page)

        await manager.stop_all()
        await manager.stop_all()

        assert manager._is_stopping is True

    @pytest.mark.asyncio
    async def test_stop_all_cancels_active_tasks(self):
        """stop_all 应取消所有活动任务。"""
        page = self._make_page()
        manager = ToastManager(page)

        async def long_running():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise  # R2

        task = asyncio.create_task(long_running())
        tm_module._register_task(task)

        await manager.stop_all()

        assert task.done()
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_all_no_tasks_no_error(self):
        """无活动任务时 stop_all 不应报错。"""
        page = self._make_page()
        manager = ToastManager(page)

        await manager.stop_all()  # 不应抛出

        assert manager._is_stopping is True


# ============================================================================
# 7. _register_task 任务追踪
# ============================================================================


class TestRegisterTask:
    """验证 _register_task 任务追踪契约。"""

    def test_register_task_ignores_none(self):
        """_register_task(None) 应静默忽略。"""
        tm_module._register_task(None)  # 不应抛出
        assert len(tm_module._active_tasks) == 0

    def test_register_task_ignores_invalid_type(self):
        """_register_task 对非 Task/Future 类型应静默忽略。"""
        tm_module._register_task("not a task")  # type: ignore[arg-type]
        assert len(tm_module._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_register_task_auto_cleans_on_done(self):
        """任务完成后应自动从 _active_tasks 移除。"""
        task = asyncio.create_task(asyncio.sleep(0))
        tm_module._register_task(task)

        assert task in tm_module._active_tasks

        await task
        # done_callback 异步触发，等待事件循环
        await asyncio.sleep(0.01)

        assert task not in tm_module._active_tasks


# ============================================================================
# 8. _resolve_color_icon 模块级函数
# ============================================================================


class TestResolveColorIcon:
    """验证 _resolve_color_icon 4 种 type + 未知 fallback。"""

    def test_info_type(self):
        """info type → AppColors.INFO + Icons.INFO。"""
        color, icon = _resolve_color_icon("info")
        assert color == AppColors.INFO
        assert icon == ft.Icons.INFO

    def test_success_type(self):
        """success type → AppColors.SUCCESS + Icons.CHECK_CIRCLE。"""
        color, icon = _resolve_color_icon("success")
        assert color == AppColors.SUCCESS
        assert icon == ft.Icons.CHECK_CIRCLE

    def test_warning_type(self):
        """warning type → AppColors.WARNING + Icons.WARNING。"""
        color, icon = _resolve_color_icon("warning")
        assert color == AppColors.WARNING
        assert icon == ft.Icons.WARNING

    def test_error_type(self):
        """error type → AppColors.ERROR + Icons.ERROR。"""
        color, icon = _resolve_color_icon("error")
        assert color == AppColors.ERROR
        assert icon == ft.Icons.ERROR

    def test_unknown_type_falls_back_to_info(self):
        """未知 type → fallback 到 info。"""
        color, icon = _resolve_color_icon("unknown")
        assert color == AppColors.INFO
        assert icon == ft.Icons.INFO


# ============================================================================
# 9. ToastManager._remove_toast 覆盖 (205-207)
# ============================================================================


class TestToastManagerRemoveToast:
    """ToastManager._remove_toast 行为测试。"""

    def _make_page(self):
        page = MagicMock()
        page.controls = [MagicMock()]
        return page

    def test_remove_toast_removes_specified(self):
        """_remove_toast 从 state 中移除指定 id 的 toast。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("toast 1")
        manager.show("toast 2")

        state = get_global_state()
        toast_id_to_remove = state.toasts[0].id
        manager._remove_toast(toast_id_to_remove)

        state = get_global_state()
        assert len(state.toasts) == 1
        assert all(t.id != toast_id_to_remove for t in state.toasts)

    def test_remove_toast_nonexistent_id_no_error(self):
        """_remove_toast 不存在的 id 不报错 (无副作用)。"""
        page = self._make_page()
        manager = ToastManager(page)
        manager.show("toast 1")

        manager._remove_toast(999)  # 不存在的 id

        state = get_global_state()
        assert len(state.toasts) == 1


# ============================================================================
# 10. ToastCard 组件运行时测试基础设施
# ============================================================================


def _make_fake_page() -> FakePage:
    """创建带 run_task 的 fake page。"""
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    return page


def _make_toast(message: str = "test", duration: int = 10) -> ToastData:
    """构造测试用 ToastData。"""
    return ToastData(id=1, message=message, icon=ft.Icons.INFO, color="#000", duration=duration)


def _walk_all_controls(root: Any) -> list[Any]:
    """递归返回所有 ft.Control (含 Component 子组件内的控件)。"""
    found: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, ft.Control):
            found.append(c)
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)

    _walk(root)
    return found


def _get_close_button(container: ft.Container) -> ft.IconButton:
    """获取 CLOSE 按钮 (_on_dismiss_click 绑定)。"""
    for ctrl in _walk_all_controls(container):
        if isinstance(ctrl, ft.IconButton) and ctrl.icon == ft.Icons.CLOSE:
            return ctrl
    raise AssertionError("CLOSE IconButton not found")


def _get_expand_button(container: ft.Container) -> ft.IconButton | None:
    """获取展开按钮 (长文本时存在)。"""
    for ctrl in _walk_all_controls(container):
        if isinstance(ctrl, ft.IconButton) and ctrl.icon in (
            ft.Icons.KEYBOARD_ARROW_DOWN,
            ft.Icons.KEYBOARD_ARROW_UP,
        ):
            return ctrl
    return None


def _get_text_control(container: ft.Container) -> ft.Text:
    """获取 ToastCard 中的 Text 控件。"""
    for ctrl in _walk_all_controls(container):
        if isinstance(ctrl, ft.Text):
            return ctrl
    raise AssertionError("Text control not found")


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe, 绕过 Optional/CallIssue)。"""
    handler(*args)


# ============================================================================
# 11. ToastCard.setup 生命周期测试
# ============================================================================


class TestToastCardSetup:
    """ToastCard.setup: page None 早返回 / run_task 启动 / _register_task 调用。"""

    def test_setup_page_none_returns_early_source_guard(self):
        """page=None (ft.context.page 抛 RuntimeError) 时 setup 早返回 (源码守护)。

        运行时限制: flet 内部 ``_schedule_effect`` 也依赖 ``context.page.session``,
        无法用 FakeSession 在 page=None 上下文下触发 setup。
        用源码守护验证 try/except RuntimeError + if page is None: return。
        """
        source = inspect.getsource(tm_module)
        # 验证 setup 有 try/except RuntimeError + if page is None: return
        assert "try:" in source
        assert "except RuntimeError:" in source
        assert "if page is None:" in source

    def test_setup_starts_timer_via_run_task(self):
        """page 可用时 setup 调用 page.run_task(_run_timer) 启动 timer。"""
        page = _make_fake_page()
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)

        assert page.run_task.called
        handler = page.run_task.call_args.args[0]
        assert inspect.iscoroutinefunction(handler)

    def test_setup_registers_task(self):
        """setup 调用 _register_task 注册 run_task 返回的 task。"""
        page = _make_fake_page()
        mock_task = MagicMock()  # run_task 返回的 task 对象
        page.run_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())

        with patch("ui.components.toast_manager._register_task") as mock_register:
            run_mount_effects(component, page=page)
            mock_register.assert_called_once_with(mock_task)


# ============================================================================
# 12. ToastCard._run_timer 倒计时逻辑测试 (含 R2 守卫)
# ============================================================================


class TestToastCardRunTimer:
    """ToastCard._run_timer: 倒计时完成 / hover 暂停 / expand 暂停 / R2 CancelledError raise / 异常 logger.debug。"""

    def _get_handler(self, page: FakePage) -> Any:
        """从 page.run_task 调用中提取 _run_timer 协程函数。"""
        assert page.run_task.called, "page.run_task 未被调用"
        return page.run_task.call_args.args[0]

    @pytest.mark.asyncio
    async def test_run_timer_completes_and_calls_on_dismiss(self):
        """倒计时完成 → set_is_dismissing(True) → on_dismiss(data.id)。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(duration=1)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = self._get_handler(page)

        async def fake_sleep(_t: float) -> None:
            pass

        with patch("asyncio.sleep", fake_sleep):
            await handler()

        on_dismiss.assert_called_once_with(toast.id)

    @pytest.mark.asyncio
    async def test_run_timer_pauses_on_hover(self):
        """hover 时倒计时暂停 (remaining 不减少, on_dismiss 未调用)。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(duration=1)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = self._get_handler(page)

        # 在 _run_timer 执行前触发 hover → hovered_ref.current=True
        container = render_once(component)
        e = MagicMock()
        e.data = "true"
        _invoke(container.on_hover, e)
        render_once(component)  # 同步 hovered_ref

        # hover 后 remaining 不减少, while 循环无限执行, 用 CancelledError 终止
        call_count = [0]

        async def fake_sleep(_t: float) -> None:
            call_count[0] += 1
            if call_count[0] > 30:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await handler()

        # 倒计时未完成 → on_dismiss 未被调用
        on_dismiss.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_timer_pauses_on_expand(self):
        """expand 时倒计时暂停。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(message="x" * (LONG_TEXT_THRESHOLD + 1), duration=1)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = self._get_handler(page)

        # 触发 expand → expanded_ref.current=True
        container = render_once(component)
        expand_btn = _get_expand_button(container)
        assert expand_btn is not None
        _invoke(expand_btn.on_click, MagicMock())
        render_once(component)

        call_count = [0]

        async def fake_sleep(_t: float) -> None:
            call_count[0] += 1
            if call_count[0] > 30:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await handler()

        on_dismiss.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_timer_raises_cancelled_error(self):
        """R2: _run_timer 中 CancelledError 必须重新 raise (不被 except Exception 吞没)。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(duration=10)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = self._get_handler(page)

        async def fake_sleep(_t: float) -> None:
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await handler()

        on_dismiss.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_timer_logs_other_exceptions(self):
        """其他异常被 except 捕获, logger.debug 记录, 不抛出。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(duration=10)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = self._get_handler(page)

        async def fake_sleep(_t: float) -> None:
            raise RuntimeError("unexpected")

        with (
            patch("asyncio.sleep", fake_sleep),
            patch.object(tm_module, "logger") as mock_logger,
        ):
            # 不应抛出 (except Exception 捕获)
            await handler()
            mock_logger.debug.assert_called_once()

        on_dismiss.assert_not_called()


# ============================================================================
# 13. ToastCard.cleanup 卸载时任务清理测试
# ============================================================================


class TestToastCardCleanup:
    """ToastCard.cleanup: task None 早返回 / task.done 跳过 cancel / gather 调用。

    注意: cleanup 是 async 函数, FakeSession.schedule_effect 会用新事件循环运行它。
    因此测试本身不需要 @pytest.mark.asyncio (避免与 FakeSession 事件循环冲突)。
    """

    def test_cleanup_task_none_returns_early(self):
        """task=None 时 cleanup 早返回, 不调用 gather_for_shutdown_cleanup。

        通过 page.run_task 返回 None 让 setup 中 task_ref.current=None。
        """
        page = _make_fake_page()
        page.run_task = MagicMock(return_value=None)  # type: ignore[method-assign]
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)

        with patch("ui.components.toast_manager.gather_for_shutdown_cleanup", new_callable=AsyncMock) as mock_gather:
            run_unmount_effects(component)
            mock_gather.assert_not_called()

    def test_cleanup_task_done_skips_cancel(self):
        """task.done()=True 时跳过 cancel 调用。"""
        page = _make_fake_page()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        mock_task.cancel = MagicMock()
        page.run_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)

        async def _fake_gather(*_args: Any) -> list[Any]:
            return []

        with patch("ui.components.toast_manager.gather_for_shutdown_cleanup", _fake_gather):
            run_unmount_effects(component)
            mock_task.cancel.assert_not_called()

    def test_cleanup_cancels_active_task(self):
        """task 未 done 时调用 cancel。"""
        page = _make_fake_page()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        page.run_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)

        async def _fake_gather(*_args: Any) -> list[Any]:
            return []

        with patch("ui.components.toast_manager.gather_for_shutdown_cleanup", _fake_gather):
            run_unmount_effects(component)
            mock_task.cancel.assert_called_once()

    def test_cleanup_calls_gather_for_shutdown_cleanup(self):
        """cleanup 调用 gather_for_shutdown_cleanup 等待 task 清理完成。"""
        page = _make_fake_page()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        page.run_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)

        with patch("ui.components.toast_manager.gather_for_shutdown_cleanup", new_callable=AsyncMock) as mock_gather:
            run_unmount_effects(component)
            mock_gather.assert_called_once_with(mock_task)


# ============================================================================
# 14. ToastCard._on_hover hover 状态切换测试
# ============================================================================


class TestToastCardOnHover:
    """ToastCard._on_hover: e.data=="true" 切换 is_hovered。"""

    @pytest.mark.asyncio
    async def test_on_hover_true_pauses_countdown(self):
        """e.data=="true" → is_hovered=True → 倒计时暂停 (on_dismiss 未调用)。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(duration=1)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = page.run_task.call_args.args[0]

        # 触发 hover
        container = render_once(component)
        e = MagicMock()
        e.data = "true"
        _invoke(container.on_hover, e)
        render_once(component)

        call_count = [0]

        async def fake_sleep(_t: float) -> None:
            call_count[0] += 1
            if call_count[0] > 30:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await handler()

        on_dismiss.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_hover_false_resumes_countdown(self):
        """e.data!="true" → is_hovered=False → 倒计时继续完成。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast(duration=1)
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        handler = page.run_task.call_args.args[0]

        # 先 hover 再 unhover
        container = render_once(component)
        e_true = MagicMock()
        e_true.data = "true"
        _invoke(container.on_hover, e_true)
        render_once(component)

        e_false = MagicMock()
        e_false.data = "false"
        container = render_once(component)
        _invoke(container.on_hover, e_false)
        render_once(component)

        async def fake_sleep(_t: float) -> None:
            pass

        with patch("asyncio.sleep", fake_sleep):
            await handler()

        on_dismiss.assert_called_once_with(toast.id)


# ============================================================================
# 15. ToastCard._on_dismiss_click 手动 dismiss 测试
# ============================================================================


class TestToastCardOnDismissClick:
    """ToastCard._on_dismiss_click: is_dismissing 早返回 / 否则 set_is_dismissing + on_dismiss。"""

    def test_on_dismiss_click_calls_on_dismiss(self):
        """点击 CLOSE → set_is_dismissing(True) + on_dismiss(data.id)。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)
        container = render_once(component)

        close_btn = _get_close_button(container)
        _invoke(close_btn.on_click, MagicMock())

        on_dismiss.assert_called_once_with(toast.id)

    def test_on_dismiss_click_is_dismissing_true_returns_early(self):
        """is_dismissing=True 时再次点击 CLOSE 早返回 (on_dismiss 只调一次)。"""
        page = _make_fake_page()
        on_dismiss = MagicMock()
        toast = _make_toast()
        component = make_component(ToastCard, data=toast, on_dismiss=on_dismiss)
        run_mount_effects(component, page=page)

        # 第一次点击
        container = render_once(component)
        close_btn = _get_close_button(container)
        _invoke(close_btn.on_click, MagicMock())
        assert on_dismiss.call_count == 1

        # 重新渲染 (is_dismissing=True) → 再次点击应早返回
        container = render_once(component)
        close_btn = _get_close_button(container)
        _invoke(close_btn.on_click, MagicMock())
        assert on_dismiss.call_count == 1  # 未再次调用


# ============================================================================
# 16. ToastCard.is_long_text 长文本展开按钮显示测试
# ============================================================================


class TestToastCardLongText:
    """ToastCard.is_long_text: >80 字符显示展开按钮 / ≤80 不显示。"""

    def test_long_text_shows_expand_button(self):
        """message > 80 字符 → 显示展开按钮。"""
        page = _make_fake_page()
        long_message = "x" * (LONG_TEXT_THRESHOLD + 1)
        toast = _make_toast(message=long_message)
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)
        container = render_once(component)

        expand_btn = _get_expand_button(container)
        assert expand_btn is not None

    def test_short_text_hides_expand_button(self):
        """message ≤ 80 字符 → 不显示展开按钮。"""
        page = _make_fake_page()
        short_message = "x" * LONG_TEXT_THRESHOLD
        toast = _make_toast(message=short_message)
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)
        container = render_once(component)

        expand_btn = _get_expand_button(container)
        assert expand_btn is None


# ============================================================================
# 17. ToastCard.is_expanded 展开/折叠状态测试
# ============================================================================


class TestToastCardExpanded:
    """ToastCard.is_expanded: 切换 max_lines / expand_icon / expand_tooltip。"""

    def test_expand_toggles_max_lines_and_icon(self):
        """点击展开按钮 → max_lines=None / icon=UP / tooltip=collapse; 再点击恢复。"""
        page = _make_fake_page()
        long_message = "x" * (LONG_TEXT_THRESHOLD + 1)
        toast = _make_toast(message=long_message)
        component = make_component(ToastCard, data=toast, on_dismiss=MagicMock())
        run_mount_effects(component, page=page)

        # 初始状态: 折叠
        container = render_once(component)
        text_ctrl = _get_text_control(container)
        assert text_ctrl.max_lines == COLLAPSED_MAX_LINES
        expand_btn = _get_expand_button(container)
        assert expand_btn is not None
        assert expand_btn.icon == ft.Icons.KEYBOARD_ARROW_DOWN

        # 点击展开
        _invoke(expand_btn.on_click, MagicMock())
        container = render_once(component)
        text_ctrl = _get_text_control(container)
        assert text_ctrl.max_lines is None  # 展开后无限制
        expand_btn = _get_expand_button(container)
        assert expand_btn is not None
        assert expand_btn.icon == ft.Icons.KEYBOARD_ARROW_UP

        # 再点击折叠
        _invoke(expand_btn.on_click, MagicMock())
        container = render_once(component)
        text_ctrl = _get_text_control(container)
        assert text_ctrl.max_lines == COLLAPSED_MAX_LINES
        expand_btn = _get_expand_button(container)
        assert expand_btn is not None
        assert expand_btn.icon == ft.Icons.KEYBOARD_ARROW_DOWN


# ============================================================================
# 18. ToastManagerView 声明式渲染测试
# ============================================================================


class TestToastManagerView:
    """ToastManagerView: 空 state / 多 toast / _on_dismiss 移除。"""

    def test_render_empty_state(self):
        """空 state 时渲染空 Column。"""
        component = make_component(ToastManagerView)
        run_mount_effects(component, page=FakePage())
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)
        assert len(result.content.controls) == 0

    def test_render_multiple_toasts(self):
        """多个 toast 时渲染对应数量的 ToastCard Component。"""
        page = MagicMock()
        page.controls = [MagicMock()]
        manager = ToastManager(page)
        manager.show("toast 1")
        manager.show("toast 2")
        manager.show("toast 3")

        component = make_component(ToastManagerView)
        run_mount_effects(component, page=FakePage())
        result = render_once(component)

        assert len(result.content.controls) == 3
        # 验证每个控件是 ToastCard Component
        for card in result.content.controls:
            assert isinstance(card, Component)

    def test_on_dismiss_removes_toast(self):
        """_on_dismiss 回调移除指定 toast (state.toasts 减少)。"""
        page = MagicMock()
        page.controls = [MagicMock()]
        manager = ToastManager(page)
        manager.show("toast 1")
        manager.show("toast 2")

        component = make_component(ToastManagerView)
        run_mount_effects(component, page=FakePage())
        result = render_once(component)

        # 获取第一个 ToastCard 的 on_dismiss 回调与 toast id
        first_card = result.content.controls[0]
        on_dismiss = first_card.kwargs["on_dismiss"]
        toast_id = first_card.kwargs["data"].id

        # 触发 dismiss
        on_dismiss(toast_id)

        # 重新渲染
        render_once(component)

        # 验证 state 中只剩 1 个 toast
        state = get_global_state()
        assert len(state.toasts) == 1
        assert all(t.id != toast_id for t in state.toasts)


# ============================================================================
# 19. R7 守卫: _reset_state_for_test 测试隔离验证
# ============================================================================


class TestR7ResetStateForTest:
    """R7: _reset_state_for_test 行为 + autouse fixture 调用验证。"""

    def test_reset_state_for_test_clears_state(self):
        """_reset_state_for_test 清空全局 _state。"""
        # 先创建 state
        page = MagicMock()
        page.controls = [MagicMock()]
        manager = ToastManager(page)
        manager.show("test")
        assert tm_module._state is not None

        # 重置
        tm_module._reset_state_for_test()
        assert tm_module._state is None

    def test_reset_state_for_test_clears_active_tasks(self):
        """_reset_state_for_test 清空 _active_tasks 集合。"""
        # 直接填充 _active_tasks (绕过 _register_task 的 isinstance 检查)
        fake_item = object()
        with tm_module._active_tasks_lock:
            tm_module._active_tasks.add(fake_item)
        assert len(tm_module._active_tasks) > 0

        # 重置
        tm_module._reset_state_for_test()
        assert len(tm_module._active_tasks) == 0

    def test_autouse_fixture_calls_reset_state_for_test(self):
        """R7: autouse fixture _reset_toast_state 必须调用 _reset_state_for_test (源码守护)。"""
        fixture = globals().get("_reset_toast_state")
        assert fixture is not None, "_reset_toast_state autouse fixture 必须存在"
        source = inspect.getsource(fixture)
        assert "_reset_state_for_test" in source, "fixture 必须调用 _reset_state_for_test (R7 测试隔离)"

    def test_state_is_clean_at_test_start(self):
        """R7: 每个测试开始时 _state 应为 None 或空 (autouse fixture 已清理)。"""
        # 此测试开始时, autouse fixture 已调用 _reset_state_for_test
        # _state 应为 None (除非本测试前的代码创建了 state, 但 fixture 已清理)
        assert tm_module._state is None or len(tm_module._state.toasts) == 0
