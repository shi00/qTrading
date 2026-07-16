"""Unit tests for HomeView declarative rewrite (Phase C.1).

契约守护测试: 验证 home_view.py 声明式范式合规性
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook, §3 红线).

覆盖:
- @ft.component 函数组件 (非 class ft.Container 子类)
- 无命令式 API (did_mount/will_unmount/refresh_locale/handle_resize/set_visible/.update()/_vm_unsubscribe)
- use_viewmodel(HomeViewModel) 内部 VM 模式
- ft.use_state(*.get_observable_state) i18n/theme 订阅
- PubSub use_effect(setup, [], cleanup=cleanup) 模式 (Phase 3.0.3)
- 消费声明式 MarketDashboard(indices=..., hsgt=..., hot_concepts=...) / NewsFeed(news_rows=..., has_more=..., on_load_more_click=...)
- page 访问用 ft.context.page (非 PageRefMixin/_page_ref)
- R2: asyncio.CancelledError 传播

运行时测试: 用 FakeHomeViewModel + render_component 驱动组件渲染, 验证
- 挂载/卸载生命周期 (VM subscribe/dispose, PubSub 订阅/退订)
- 渲染输出结构 (header/dashboard/news_feed)
- 事件 handler 行为 (refresh_clicked/on_load_more_click/on_broadcast_message)
- 数据加载路径 (_load_data/_init_and_load/_on_market_update/_on_news_update)
- 错误路径 (CancelledError 传播, Exception 不抛出)
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)

_HOME_VIEW_PATH = Path(__file__).parent.parent.parent.parent / "ui" / "views" / "home_view.py"

pytestmark = pytest.mark.unit


def _read_source() -> str:
    return _HOME_VIEW_PATH.read_text(encoding="utf-8")


class TestHomeViewDeclarativeContract:
    """验证 HomeView 声明式重写的契约合规性 (源码静态断言)."""

    def test_is_declarative_component(self) -> None:
        """验证使用 @ft.component 装饰器 + 函数组件 (非 class 子类)."""
        content = _read_source()
        assert "@ft.component" in content
        assert "def HomeView(" in content

    def test_no_imperative_class(self) -> None:
        """验证无命令式 class HomeView(ft.Container) 子类 (R1 架构边界)."""
        content = _read_source()
        assert "class HomeView(ft.Container)" not in content
        assert "class HomeView(" not in content

    def test_no_did_mount(self) -> None:
        """验证移除 did_mount 生命周期回调."""
        content = _read_source()
        assert "did_mount" not in content

    def test_no_will_unmount(self) -> None:
        """验证移除 will_unmount 生命周期回调."""
        content = _read_source()
        assert "will_unmount" not in content

    def test_no_refresh_locale(self) -> None:
        """验证移除 refresh_locale 命令式 i18n 刷新 (由 observable state 替代)."""
        content = _read_source()
        assert "refresh_locale" not in content

    def test_no_handle_resize(self) -> None:
        """验证移除 handle_resize 命令式响应式回调."""
        content = _read_source()
        assert "handle_resize" not in content

    def test_no_set_visible(self) -> None:
        """验证移除 set_visible 可见性优化命令式 API."""
        content = _read_source()
        assert "set_visible" not in content

    def test_no_self_update(self) -> None:
        """验证移除 .update() 手动刷新 (声明式由 state 驱动重渲染)."""
        content = _read_source()
        assert ".update()" not in content

    def test_no_vm_unsubscribe(self) -> None:
        """验证移除 _vm_unsubscribe 命令式 state diff 订阅 (由 use_viewmodel 替代)."""
        content = _read_source()
        assert "_vm_unsubscribe" not in content

    def test_no_page_ref_mixin(self) -> None:
        """验证无 PageRefMixin / _page_ref (声明式用 ft.context.page)."""
        content = _read_source()
        assert "PageRefMixin" not in content
        assert "_page_ref" not in content

    def test_no_use_ref_cache_imperative(self) -> None:
        """验证无 use_ref 缓存命令式控件实例 (声明式红线 4)."""
        content = _read_source()
        assert "use_ref" not in content

    def test_uses_use_viewmodel(self) -> None:
        """验证通过 use_viewmodel(HomeViewModel) 消费 VM (内部 VM 模式)."""
        content = _read_source()
        assert "use_viewmodel(HomeViewModel)" in content

    def test_uses_observable_state_i18n(self) -> None:
        """验证通过 ft.use_state(get_observable_state) 订阅 i18n 自动重渲染."""
        content = _read_source()
        assert "ft.use_state(get_observable_state)" in content

    def test_uses_observable_state_theme(self) -> None:
        """验证通过 ft.use_state(AppColors.get_observable_state) 订阅 theme 自动重渲染."""
        content = _read_source()
        assert "ft.use_state(AppColors.get_observable_state)" in content

    def test_uses_context_page(self) -> None:
        """验证 page 访问用 ft.context.page (非 PageRefMixin/_page_ref)."""
        content = _read_source()
        assert "ft.context.page" in content

    def test_uses_pubsub_use_effect(self) -> None:
        """验证 PubSub 用 use_effect(setup, [], cleanup=cleanup) topic 模式."""
        content = _read_source()
        assert "page.pubsub.subscribe_topic" in content
        assert "page.pubsub.unsubscribe_topic" in content
        assert "ft.use_effect(" in content
        assert "cleanup=" in content

    def test_pubsub_uses_topic_unsubscribe(self) -> None:
        """验证 PubSub 用 unsubscribe_topic 精准退订 (避免误伤其他视图订阅)."""
        content = _read_source()
        assert "page.pubsub.unsubscribe_topic(" in content
        assert "page.pubsub.unsubscribe()" not in content

    def test_consumes_declarative_market_dashboard(self) -> None:
        """验证消费声明式 MarketDashboard(indices=...) props 推送."""
        content = _read_source()
        assert "MarketDashboard(indices=" in content

    def test_consumes_declarative_news_feed(self) -> None:
        """验证消费声明式 NewsFeed(news_rows=..., has_more=..., on_load_more_click=...)."""
        content = _read_source()
        assert "NewsFeed(" in content
        assert "news_rows=" in content
        assert "has_more=" in content
        assert "on_load_more_click=" in content

    def test_cancelled_error_propagated(self) -> None:
        """验证 R2 红线: asyncio.CancelledError 必须 raise 传播."""
        content = _read_source()
        assert "asyncio.CancelledError" in content
        assert "raise" in content


class TestHomeViewR2Compliance:
    """R2 红线专项: 验证含 await 的 async 路径的 CancelledError 传播."""

    def test_await_paths_have_cancelled_error_guard(self) -> None:
        """验证每个含 await 的 async 函数都有 CancelledError raise 守卫.

        _setup_pubsub/_cleanup_pubsub 无 await (同步 pubsub 操作包在 async 签名中),
        不会触发 CancelledError, 无需守卫. 含 await 的数据加载路径必须守卫.
        """
        content = _read_source()
        # 含 await 的 async 函数: _on_load_more_click, _load_data, _init_and_load (3 个)
        # 每个都有 `except asyncio.CancelledError: raise` 守卫
        # (dual-track 移除后, _on_market_update/_on_news_update 已删除)
        cancelled_raise_count = content.count("raise  # R2")
        assert cancelled_raise_count >= 3, (
            f"R2 违规: 含 await 的 async 函数应至少 3 处 CancelledError raise, 实际 {cancelled_raise_count}"
        )

    def test_no_bare_exception_swallows_cancelled_error(self) -> None:
        """验证 except Exception 之前都有 CancelledError raise 守卫 (R2 不吞没)."""
        content = _read_source()
        # 每处 `except Exception` 前应有 `except asyncio.CancelledError: raise`
        except_exception_count = content.count("except Exception")
        cancelled_guard_count = content.count("except asyncio.CancelledError")
        assert except_exception_count > 0
        assert cancelled_guard_count >= except_exception_count, (
            f"R2 违规: {except_exception_count} 处 except Exception 但仅 {cancelled_guard_count} 处 CancelledError 守卫"
        )


# ============================================================================
# 运行时测试: 用 FakeHomeViewModel + render_component 驱动组件渲染
# ============================================================================


@dataclass(frozen=True)
class _FakeHomeState:
    """模拟 HomeState 的最小字段集 (对齐 HomeState 全字段)."""

    news_page: int = 0
    has_more_news: bool = False
    is_loading_more: bool = False
    news_update_version: int = 0
    market_update_version: int = 0
    market_stale: bool = False
    market_date: str = "--"
    news_rows: tuple = ()
    market_indices: tuple = ()
    market_hsgt: Any = None
    market_hot_concepts: tuple = ()


class _FakeHomeViewModel:
    """模拟 HomeViewModel, 记录所有方法调用.

    满足 use_viewmodel 契约 (state/subscribe/dispose) + HomeView 调用的所有方法.
    """

    def __init__(self) -> None:
        self._state: _FakeHomeState = _FakeHomeState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.method_calls: list[str] = []
        self.last_market_data: dict = {}
        self.news_data: pd.DataFrame | None = None
        self._last_news_update: tuple[Any, Any] | None = None
        # 可被测试覆盖的返回值
        self.market_data_return: dict | None = {"date": "2025-01-01", "stale": False}
        self.news_return: tuple[pd.DataFrame | None, bool] = (pd.DataFrame(), False)
        self.next_page_return: tuple[pd.DataFrame | None, bool] = (None, False)
        self.cached_market_data_return: dict | None = {"date": "2025-01-01"}

    @property
    def state(self) -> _FakeHomeState:
        return self._state

    @property
    def last_news_update(self) -> tuple[Any, Any] | None:
        return self._last_news_update

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in self._subscribers:
            cb(snapshot)

    def _set_state(self, **changes: Any) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    # --- HomeView 调用的 VM 方法 ---

    def init(self) -> None:
        self.method_calls.append("init")

    async def init_data(self) -> None:
        self.method_calls.append("init_data")

    async def load_market_data(self) -> dict | None:
        self.method_calls.append("load_market_data")
        data = self.market_data_return
        if data:
            self._set_state(
                market_date=str(data.get("date", "--")),
                market_stale=bool(data.get("stale", False)),
            )
        return data

    async def get_cached_market_data(self) -> dict | None:
        self.method_calls.append("get_cached_market_data")
        data = self.cached_market_data_return
        if data:
            self._set_state(
                market_date=str(data.get("date", "--")),
                market_stale=bool(data.get("stale", False)),
            )
        return data

    async def refresh_news(self) -> tuple[pd.DataFrame | None, bool]:
        self.method_calls.append("refresh_news")
        return self.news_return

    async def load_next_page(self) -> tuple[pd.DataFrame | None, bool]:
        self.method_calls.append("load_next_page")
        return self.next_page_return

    def clear_state(self) -> None:
        self.method_calls.append("clear_state")
        self.last_market_data = {}
        self.news_data = None


@pytest.fixture
def mock_home_vm(monkeypatch):
    """注入 _FakeHomeViewModel 替换 HomeViewModel 类."""
    import ui.views.home_view as home_view_module

    fake_vm = _FakeHomeViewModel()
    monkeypatch.setattr(home_view_module, "HomeViewModel", lambda: fake_vm)
    return fake_vm


def _make_fake_page() -> FakePage:
    """创建带 pubsub/run_task 的 fake page (用于 attach_fake_page).

    使用 component_renderer.FakePage 以获得真实执行 effect 的 FakeSession,
    再补 pubsub/run_task 以支持 HomeView 的 PubSub 订阅/退订 + run_task 调度.
    """
    page = FakePage()
    page.pubsub = MagicMock()  # type: ignore[attr-defined]
    page.pubsub.subscribe_topic = MagicMock()  # type: ignore[attr-defined]
    page.pubsub.unsubscribe_topic = MagicMock()  # type: ignore[attr-defined]
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    return page


class TestHomeViewRuntime:
    """HomeView 运行时测试: 验证挂载/卸载/渲染/handler 行为."""

    def test_mount_returns_container(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """挂载 HomeView 不抛异常, 返回 ft.Container."""
        import flet as ft

        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        # content 是 Column
        assert isinstance(result.content, ft.Column)

    def test_mount_triggers_vm_subscribe_and_init(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """挂载后 VM.subscribe 被调用 + init/init_data 被调用 (mount effect)."""
        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        # VM subscribe 被调用 (use_viewmodel hook 注册)
        assert len(mock_home_vm._subscribers) > 0
        # init/init_data 被 mount effect 调用
        assert "init" in mock_home_vm.method_calls
        assert "init_data" in mock_home_vm.method_calls

    def test_mount_triggers_load_data(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """挂载后 _load_data 被调用 (load_market_data + refresh_news)."""
        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        assert "load_market_data" in mock_home_vm.method_calls
        assert "refresh_news" in mock_home_vm.method_calls

    def test_unmount_triggers_dispose(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """卸载后 VM.dispose 被调用 (use_viewmodel cleanup)."""
        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        assert mock_home_vm.dispose_called is False

        run_unmount_effects(component)
        assert mock_home_vm.dispose_called is True

    def test_mount_subscribes_pubsub(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """挂载后 pubsub.subscribe_topic(CACHE_CLEARED_TOPIC) 被调用."""
        from ui.views.home_view import HomeView
        from ui.pubsub_topics import CACHE_CLEARED_TOPIC

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        page.pubsub.subscribe_topic.assert_called_once()
        args = page.pubsub.subscribe_topic.call_args
        assert args.args[0] == CACHE_CLEARED_TOPIC

    def test_unmount_unsubscribes_pubsub(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """卸载后 pubsub.unsubscribe_topic(CACHE_CLEARED_TOPIC) 被调用."""
        from ui.views.home_view import HomeView
        from ui.pubsub_topics import CACHE_CLEARED_TOPIC

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        run_unmount_effects(component)

        page.pubsub.unsubscribe_topic.assert_called_once_with(CACHE_CLEARED_TOPIC)

    def test_render_header_contains_title_and_refresh_button(
        self, mock_i18n_state, mock_app_colors_state, mock_home_vm
    ) -> None:
        """渲染输出 header 含 title Text + IconButton (refresh)."""
        import flet as ft

        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        col = result.content
        # 第 1 项是 header (Row)
        header = col.controls[0]
        assert isinstance(header, ft.Row)
        # header 至少含: title Text, spacer Container, date Text, refresh IconButton
        assert len(header.controls) >= 4
        assert isinstance(header.controls[0], ft.Text)  # title
        assert isinstance(header.controls[-1], ft.IconButton)  # refresh

    def test_render_includes_dashboard_and_news_section(
        self, mock_i18n_state, mock_app_colors_state, mock_home_vm
    ) -> None:
        """渲染输出含 MarketDashboard 区域 + NewsFeed 区域 (5+ 控件)."""
        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        col = result.content
        # col 至少含: header, Divider, MarketDashboard, Text(home_live_news), NewsFeed
        assert len(col.controls) >= 5

    def test_render_stale_data_shows_updating_suffix(
        self, mock_i18n_state, mock_app_colors_state, mock_home_vm
    ) -> None:
        """market_data.stale=True 时 date_text 含 updating 后缀."""
        import flet as ft

        from ui.views.home_view import HomeView

        # 配置 market_data 返回 stale=True
        mock_home_vm.market_data_return = {"date": "2025-01-01", "stale": True}
        # market_update_effect 也会读 cached_market_data, 保持一致
        mock_home_vm.cached_market_data_return = {"date": "2025-01-01", "stale": True}

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        col = result.content
        header = col.controls[0]
        # 找到 date Text (header 倒数第二个控件, 最后是 IconButton)
        date_text = header.controls[-2]
        assert isinstance(date_text, ft.Text)
        # stale=True 时 value 应含 updating 后缀 (mock_i18n_state 用真实 I18nState 翻译)
        from ui.i18n import I18n

        assert I18n.get("home_data_updating") in date_text.value

    def test_refresh_clicked_invokes_run_task(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """refresh button on_click 触发 page.run_task(_load_data)."""
        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        # 渲染一次使 handlers 绑定
        result = render_once(component)

        # reset mock 以过滤 mount 时的调用
        page.run_task.reset_mock()

        # 找到 refresh IconButton 的 on_click 并触发
        header = result.content.controls[0]
        refresh_btn = header.controls[-1]
        # 触发 on_click (传入 mock event)
        refresh_btn.on_click(MagicMock())

        # run_task 被调用 (传入 _load_data 协程函数)
        assert page.run_task.called

    def test_on_broadcast_message_clears_state(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """CACHE_CLEARED_TOPIC 事件触发 vm.clear_state + set_market_data({})."""
        from ui.views.home_view import HomeView
        from ui.pubsub_topics import CACHE_CLEARED_TOPIC

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        # 找到 _setup_pubsub 注册的 callback
        subscribe_call = page.pubsub.subscribe_topic.call_args
        callback = subscribe_call.args[1]

        # 触发 cache_cleared 事件
        callback(CACHE_CLEARED_TOPIC, "cache_cleared")

        # vm.clear_state 被调用
        assert "clear_state" in mock_home_vm.method_calls

    def test_on_broadcast_message_ignores_other_messages(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """非 cache_cleared 消息不触发 clear_state."""
        from ui.views.home_view import HomeView
        from ui.pubsub_topics import CACHE_CLEARED_TOPIC

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        subscribe_call = page.pubsub.subscribe_topic.call_args
        callback = subscribe_call.args[1]

        # 触发非 cache_cleared 事件
        callback(CACHE_CLEARED_TOPIC, "other_message")
        callback("other_topic", "cache_cleared")

        assert "clear_state" not in mock_home_vm.method_calls

    def test_on_news_update_tag_update_no_throw(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """news_update_version 变化 + TAG_UPDATE 类型 → 更新已有 news_items 的 tags."""
        from services.news_subscription_service import NewsUpdateType

        from ui.views.home_view import HomeView

        # 设置 _last_news_update 为 TAG_UPDATE 类型
        mock_home_vm._last_news_update = (
            NewsUpdateType.TAG_UPDATE,
            {"content": "test content", "tags": "tag1,tag2"},
        )

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        # 触发 news_update_version 变化
        mock_home_vm._set_state(news_update_version=1)
        from tests.unit.ui.component_renderer import run_render_effects

        run_render_effects(component)

        # TAG_UPDATE 路径不调用 refresh_news (仅 NEW_ITEM/其他类型才调用)
        # 验证无异常抛出即可 (TAG_UPDATE 在 news_items 为空时静默处理)

    def test_on_news_update_none_returns_early(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_home_vm,
    ) -> None:
        """news_update_version 变化但 last_news_update=None 时早返回."""
        from ui.views.home_view import HomeView

        # 确保 _last_news_update 为 None
        mock_home_vm._last_news_update = None

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)

        # 重置 method_calls 以过滤 mount 时的调用
        mock_home_vm.method_calls.clear()

        # 触发 news_update_version 变化
        mock_home_vm._set_state(news_update_version=1)
        from tests.unit.ui.component_renderer import run_render_effects

        run_render_effects(component)

        # last_news_update=None 时早返回, 不调用 refresh_news
        assert "refresh_news" not in mock_home_vm.method_calls

    def test_load_data_handles_exception(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """_load_data 中 VM 方法抛 Exception 时不传播 (logger.error 降级)."""
        from ui.views.home_view import HomeView

        # 配置 load_market_data 抛 Exception
        mock_home_vm.market_data_return = None  # 先正常返回 None
        original_load = mock_home_vm.load_market_data

        async def raise_exc() -> None:
            raise RuntimeError("test error")

        mock_home_vm.load_market_data = raise_exc

        component = make_component(HomeView)
        page = _make_fake_page()
        # mount effect 会调 _init_and_load → _load_data, 异常被 except 捕获
        # 不抛出即测试通过
        run_mount_effects(component, page=page)
        # 恢复以避免影响其他测试
        mock_home_vm.load_market_data = original_load


# ============================================================================
# 异常路径测试 (Task 4.4): 覆盖 17 miss 到 80%+
# ============================================================================


class TestHomeViewExceptionPaths:
    """HomeView 异常路径测试 (Task 4.4): 覆盖 17 miss.

    覆盖路径:
    - _on_load_more_click 异常路径 (Exception + CancelledError + R9 守卫)
    - _init_and_load 异常路径 (vm.init/init_data 抛 Exception + CancelledError)
    - _load_data CancelledError 传播
    - _refresh_clicked page=None 路径
    - _setup_pubsub/_cleanup_pubsub page=None 路径
    """

    @staticmethod
    def _get_on_load_more_click(result: Any) -> Any:
        """从 HomeView 渲染结果中找到 NewsFeed 的 on_load_more_click 回调.

        NewsFeed 是 @ft.component, render_once 后作为 Component 对象存在 col.controls 中.
        """
        from flet.components.component import Component

        col = result.content
        for ctrl in col.controls:
            if isinstance(ctrl, Component) and "on_load_more_click" in ctrl.kwargs:
                return ctrl.kwargs["on_load_more_click"]
        raise AssertionError("NewsFeed on_load_more_click not found")

    def test_on_load_more_click_handles_exception(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """_on_load_more_click 中 vm.load_next_page 抛 Exception → logger.error + R9 守卫.

        覆盖 lines 81-86 (try/except Exception/sanitize_error/logger.error).
        R9 守卫: 验证 DataSanitizer.sanitize_error 被调用且参数为原始异常.
        """
        import asyncio
        from unittest.mock import patch

        from ui.views.home_view import HomeView

        async def raise_exc() -> None:
            raise RuntimeError("load more failed with token=secret")

        mock_home_vm.load_next_page = raise_exc

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        on_load_more = self._get_on_load_more_click(result)

        # R9 守卫: mock DataSanitizer.sanitize_error 返回脱敏字符串
        with patch("ui.views.home_view.DataSanitizer") as mock_sanitizer:
            mock_sanitizer.sanitize_error.return_value = "sanitized error"
            asyncio.run(on_load_more(MagicMock()))

            # 验证 sanitize_error 被调用, 参数为原始异常 (R9 守卫)
            mock_sanitizer.sanitize_error.assert_called_once()
            exc_arg = mock_sanitizer.sanitize_error.call_args.args[0]
            assert isinstance(exc_arg, RuntimeError)

    def test_on_load_more_click_cancelled_error_propagates(
        self, mock_i18n_state, mock_app_colors_state, mock_home_vm
    ) -> None:
        """R2: _on_load_more_click 中 CancelledError 必须传播.

        覆盖 lines 83-84 (except asyncio.CancelledError: raise).
        """
        import asyncio

        from ui.views.home_view import HomeView

        async def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        mock_home_vm.load_next_page = raise_cancelled

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        on_load_more = self._get_on_load_more_click(result)

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(on_load_more(MagicMock()))

    def test_init_and_load_handles_init_exception(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """_init_and_load 中 vm.init 抛 Exception → 不传播 (logger.error).

        覆盖 lines 106-107 (except Exception: logger.error).
        """
        from ui.views.home_view import HomeView

        def raise_init() -> None:
            raise RuntimeError("init failed")

        mock_home_vm.init = raise_init

        component = make_component(HomeView)
        page = _make_fake_page()
        # mount effect 调 _init_and_load, vm.init 抛 Exception 被 except 捕获
        run_mount_effects(component, page=page)
        # 不抛异常即通过

    def test_init_and_load_handles_init_data_exception(
        self, mock_i18n_state, mock_app_colors_state, mock_home_vm
    ) -> None:
        """_init_and_load 中 vm.init_data 抛 Exception → 不传播 (logger.error).

        覆盖 lines 106-107 (except Exception: logger.error).
        """
        from ui.views.home_view import HomeView

        async def raise_init_data() -> None:
            raise RuntimeError("init_data failed")

        mock_home_vm.init_data = raise_init_data

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        # 不抛异常即通过

    def test_load_data_cancelled_error_propagates(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """R2: _load_data 中 CancelledError 必须传播 (经由 _init_and_load).

        覆盖 line 95 (raise in _load_data) + line 105 (raise in _init_and_load).
        """
        import asyncio

        from ui.views.home_view import HomeView

        async def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        mock_home_vm.load_market_data = raise_cancelled

        component = make_component(HomeView)
        page = _make_fake_page()
        with pytest.raises(asyncio.CancelledError):
            run_mount_effects(component, page=page)

    def test_init_and_load_cancelled_error_propagates(
        self, mock_i18n_state, mock_app_colors_state, mock_home_vm
    ) -> None:
        """R2: _init_and_load 中 vm.init_data 抛 CancelledError 必须传播.

        覆盖 lines 104-105 (except asyncio.CancelledError: raise).
        """
        import asyncio

        from ui.views.home_view import HomeView

        async def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        mock_home_vm.init_data = raise_cancelled

        component = make_component(HomeView)
        page = _make_fake_page()
        with pytest.raises(asyncio.CancelledError):
            run_mount_effects(component, page=page)

    def test_refresh_clicked_page_none_logs_debug(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """page 不可用时 _refresh_clicked 进入 except RuntimeError 分支.

        覆盖 lines 75->exit (page is None branch) + 77-78 (except RuntimeError: logger.debug).
        """
        from flet.controls.context import _context_page

        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()
        run_mount_effects(component, page=page)
        result = render_once(component)

        page.run_task.reset_mock()

        # 模拟 page 不可用 (ft.context.page 抛 RuntimeError)
        token = _context_page.set(None)
        try:
            header = result.content.controls[0]
            refresh_btn = header.controls[-1]
            refresh_btn.on_click(MagicMock())
        finally:
            _context_page.reset(token)

        # run_task 未被调用 (page 不可用, 早返回)
        assert not page.run_task.called

    def test_setup_pubsub_page_none_no_throw(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """_setup_pubsub 在 page 不可用时进入 except RuntimeError, 不抛异常.

        覆盖 lines 114->exit (page is None branch) + 116-117 (except RuntimeError: pass).

        实现方式: 包装 FakeSession.schedule_effect, 在执行 effect setup 之前设置
        _context_page = None. 这样 _schedule_effect (访问 context.page 获取 session)
        仍能成功, 而 _setup_pubsub 内部 ft.context.page 抛 RuntimeError 被 except 捕获.
        """
        from flet.controls.context import _context_page

        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()

        # 包装 session.schedule_effect: effect 执行期间 _context_page = None
        original_schedule = page.session.schedule_effect

        def _schedule_with_no_context(hook: Any, is_cleanup: bool) -> None:
            token = _context_page.set(None)
            try:
                original_schedule(hook, is_cleanup)
            finally:
                _context_page.reset(token)

        page.session.schedule_effect = _schedule_with_no_context  # type: ignore[method-assign]

        run_mount_effects(component, page=page)

        # 不抛异常即通过; pubsub.subscribe_topic 未被调用 (page 不可用)
        assert not page.pubsub.subscribe_topic.called

    def test_cleanup_pubsub_page_none_no_throw(self, mock_i18n_state, mock_app_colors_state, mock_home_vm) -> None:
        """_cleanup_pubsub 在 page 不可用时进入 except RuntimeError, 不抛异常.

        覆盖 lines 122->exit (page is None branch) + 124-125 (except RuntimeError: pass).
        """
        from flet.controls.context import _context_page

        from ui.views.home_view import HomeView

        component = make_component(HomeView)
        page = _make_fake_page()

        # 包装 session.schedule_effect: effect 执行期间 _context_page = None
        original_schedule = page.session.schedule_effect

        def _schedule_with_no_context(hook: Any, is_cleanup: bool) -> None:
            token = _context_page.set(None)
            try:
                original_schedule(hook, is_cleanup)
            finally:
                _context_page.reset(token)

        page.session.schedule_effect = _schedule_with_no_context  # type: ignore[method-assign]

        run_mount_effects(component, page=page)
        # reset 以过滤 mount 时的调用
        page.pubsub.unsubscribe_topic.reset_mock()

        run_unmount_effects(component)

        # 不抛异常即通过; unsubscribe_topic 未被调用 (page 不可用)
        assert not page.pubsub.unsubscribe_topic.called
