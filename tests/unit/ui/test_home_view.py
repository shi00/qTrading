"""Unit tests for HomeView declarative rewrite (Phase C.1).

契约守护测试: 验证 home_view.py 声明式范式合规性
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook, §3 红线).

覆盖:
- @ft.component 函数组件 (非 class ft.Container 子类)
- 无命令式 API (did_mount/will_unmount/refresh_locale/handle_resize/set_visible/.update()/_vm_unsubscribe)
- use_viewmodel(HomeViewModel) 内部 VM 模式
- ft.use_state(*.get_observable_state) i18n/theme 订阅
- PubSub use_effect(setup, [], cleanup=cleanup) 模式 (Phase 3.0.3)
- 消费声明式 MarketDashboard(data=...) / NewsFeed(news_items=..., has_more=..., on_load_more_click=...)
- page 访问用 ft.context.page (非 PageRefMixin/_page_ref)
- R2: asyncio.CancelledError 传播
"""

from pathlib import Path

import pytest

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
        """验证消费声明式 MarketDashboard(data=...) props 推送."""
        content = _read_source()
        assert "MarketDashboard(data=" in content

    def test_consumes_declarative_news_feed(self) -> None:
        """验证消费声明式 NewsFeed(news_items=..., has_more=..., on_load_more_click=...)."""
        content = _read_source()
        assert "NewsFeed(" in content
        assert "news_items=" in content
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
        # 含 await 的 async 函数: _on_load_more_click, _load_data, _init_and_load,
        # _on_market_update, _on_news_update (5 个)
        # 每个都有 `except asyncio.CancelledError: raise` 守卫
        cancelled_raise_count = content.count("raise  # R2")
        assert cancelled_raise_count >= 5, (
            f"R2 违规: 含 await 的 async 函数应至少 5 处 CancelledError raise, 实际 {cancelled_raise_count}"
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
