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
from unittest.mock import MagicMock

import flet as ft
import pytest

from ui.components import toast_manager as tm_module
from ui.components.toast_manager import (
    ToastCard,
    ToastData,
    ToastManager,
    ToastManagerView,
    get_global_state,
)

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
