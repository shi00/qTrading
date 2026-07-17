"""ui/views/screener_view.py 声明式契约守护测试 (Phase F.3).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/
   _on_locale_change/refresh_locale/handle_resize/PageRefMixin/_page_ref/weakref/_ai_cards)
2. 正向契约: @ft.component / use_viewmodel / Observable state 订阅 /
   ft.context.page / FilePicker use_effect / PubSub use_effect+cleanup /
   流式 ref buffer + 节流

业务逻辑覆盖 (VM 交互 + 流式渲染 + 深度链接 + 模式切换) 由集成测试
(flet_test_page fixture) 承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名 (作为变更说明) 导致字符串匹配误判。
    """
    import ast

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


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.views.screener_view as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.screener_view as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (ScreenerView)
# ============================================================================


class TestScreenerViewContract:
    """ScreenerView 声明式契约守护测试 (Phase F.3)。"""

    def test_screener_view_is_ft_component(self):
        """DoD: ScreenerView 必须被 @ft.component 装饰。"""
        from ui.views.screener_view import ScreenerView

        assert hasattr(ScreenerView, "__wrapped__"), "ScreenerView 必须用 @ft.component 装饰"

    def test_screener_view_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "ScreenerView 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class ScreenerView(" not in _code_source(), "ScreenerView 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def ScreenerView(...) -> ft.Container。"""
        assert "def ScreenerView(" in _code_source(), "必须是函数定义"
        assert "-> ft.Container" in _code_source(), "返回类型必须为 ft.Container"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        assert "_on_locale_change" not in _code_source(), "不应使用 _on_locale_change (声明式自动重渲染)"

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme (声明式通过 Observable state 自动重渲染)。"""
        assert "update_theme" not in _code_source(), "不应使用 update_theme (声明式自动重渲染)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联 (子组件自管)。"""
        assert "handle_resize" not in _code_source(), "不应使用 handle_resize (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_ai_cards(self):
        """DoD: 禁止命令式 _ai_cards 占位字典 (改用 state 驱动)。"""
        assert "_ai_cards" not in _code_source(), "不应使用 _ai_cards (命令式占位字典)"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "get_observable_state" in _raw_source(), "必须订阅 get_observable_state"

    def test_subscribes_theme(self):
        """DoD: 必须订阅 AppColors.get_observable_state (theme 自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source(), "必须订阅 AppColors.get_observable_state"

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page (try/except 守卫)。"""
        assert "ft.context.page" in _code_source(), "page 访问必须通过 ft.context.page"

    def test_uses_use_viewmodel(self):
        """DoD: 必须通过 use_viewmodel hook 消费 ScreenerViewModel。"""
        assert "use_viewmodel" in _raw_source(), "必须使用 use_viewmodel hook"
        assert "ScreenerViewModel" in _raw_source(), "必须消费 ScreenerViewModel"

    def test_file_picker_uses_use_effect(self):
        """DoD: FilePicker 必须通过 use_effect 注册到 page.services (含 cleanup)。"""
        code = _code_source()
        assert "ft.FilePicker()" in code, "必须实例化 ft.FilePicker"
        assert "page.services" in code, "FilePicker 必须注册到 page.services"
        # use_effect 携带 cleanup 参数 (注册/移除成对)
        assert "cleanup=" in code, "FilePicker use_effect 必须含 cleanup"

    def test_pubsub_uses_use_effect_with_cleanup(self):
        """DoD: PubSub (TaskManager) 订阅必须通过 use_effect + cleanup 退订。"""
        code = _code_source()
        assert "use_effect" in code, "PubSub 必须用 use_effect 订阅"
        # cleanup 函数成对出现 (FilePicker + PubSub 各一处 cleanup)
        assert code.count("cleanup=") >= 2, "FilePicker + PubSub 各需一处 cleanup"

    def test_stream_cards_state_driven(self):
        """DoD: 流式卡片必须从 state.stream_cards 渲染 (state-driven, 不用 ref buffer)。"""
        code = _code_source()
        assert "state.stream_cards" in code, "必须从 state.stream_cards 渲染流式卡片"
        assert "StreamCard" in code, "必须使用 StreamCard dataclass"

    def test_no_callback_injection(self):
        """DoD: 禁止回调注入模式 (on_log_stream_start / on_ai_card_start)。"""
        code = _code_source()
        assert "on_log_stream_start" not in code, "禁止 on_log_stream_start 回调注入"
        assert "on_ai_card_start" not in code, "禁止 on_ai_card_start 回调注入"
        assert "_setup_vm_callbacks" not in code, "禁止 _setup_vm_callbacks 回调绑定"

    def test_consumes_resizable_splitter(self):
        """DoD: 必须函数调用消费 ResizableSplitter (props 推送)。"""
        assert "ResizableSplitter(" in _code_source(), "必须函数调用 ResizableSplitter"

    def test_consumes_paginated_table(self):
        """DoD: 必须函数调用消费 PaginatedTable (props 推送)。"""
        assert "PaginatedTable(" in _code_source(), "必须函数调用 PaginatedTable"

    def test_consumes_stock_detail_dialog(self):
        """DoD: 必须函数调用消费 StockDetailDialog (props 推送)。"""
        assert "StockDetailDialog(" in _code_source(), "必须函数调用 StockDetailDialog"

    def test_cancelled_error_propagated(self):
        """DoD: asyncio.CancelledError 必须 raise (R2 红线)。"""
        code = _code_source()
        assert "asyncio.CancelledError" in code, "必须捕获 CancelledError"
        assert "raise" in code, "CancelledError 必须 raise (R2)"

    def test_no_page_param_in_signature(self):
        """DoD: ScreenerView 签名不应包含 page 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.views.screener_view import ScreenerView

        sig = inspect.signature(ScreenerView.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page" not in params, "ScreenerView 不应接收 page 参数"
        assert "initial_strategy" in params, "ScreenerView 必须接收 initial_strategy 参数"

    # ========================================================================
    # R.2.2: ScreenerView 改用 VM state (selected_strategy/tier_hint)
    # 消除双源真相: View 禁止 use_state 持有业务状态, 改从 state.* 读取
    # ========================================================================

    def test_screener_view_reads_selected_strategy_from_vm(self):
        """R.2.2: View 从 VM state 读取 selected_strategy/tier_hint, 禁止本地 use_state 双源真相。

        DoD: grep `selected_strategy.*use_state\\|set_tier_hint` ui/views/screener_view.py = 0。
        全量 regex 校验所有 use_state 解构 + set_tier_hint 调用为 0,
        避免"存在一处合规即放行"的虚假保障 (R.2.1 QA Critical 教训)。
        """
        import re

        code = _code_source()

        # 禁止: selected_strategy 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(r"selected_strategy[^\n]*use_state", code)
        assert use_state_matches == [], f"selected_strategy 不应使用 use_state (双源真相, R.2.2): {use_state_matches}"

        # 禁止: tier_hint 通过 use_state 解构持有 (双源真相)
        tier_hint_use_state = re.findall(r"tier_hint[^\n]*use_state", code)
        assert tier_hint_use_state == [], f"tier_hint 不应使用 use_state (双源真相, R.2.2): {tier_hint_use_state}"

        # 禁止: set_tier_hint 任何调用/解构 (VM select_strategy 内聚)
        set_tier_hint_matches = re.findall(r"\bset_tier_hint\b", code)
        assert set_tier_hint_matches == [], (
            f"禁止 set_tier_hint 调用 (VM select_strategy 内聚, R.2.2): {set_tier_hint_matches}"
        )

        # 禁止: set_selected_strategy 任何调用/解构 (VM select_strategy 内聚)
        set_selected_matches = re.findall(r"\bset_selected_strategy\b", code)
        assert set_selected_matches == [], (
            f"禁止 set_selected_strategy 调用 (VM select_strategy 内聚, R.2.2): {set_selected_matches}"
        )

        # 禁止: 模块级 _compute_tier_hint 函数定义 (已迁入 VM 为静态方法)
        def_matches = re.findall(r"def _compute_tier_hint", code)
        assert def_matches == [], f"禁止模块级 _compute_tier_hint 定义 (已迁入 VM, R.2.2): {def_matches}"

        # 必须: 调用 vm.select_strategy (新 API, 替代 set_selected_strategy + set_tier_hint)
        assert "vm.select_strategy" in code, "必须调用 vm.select_strategy (R.2.2 新 API)"

        # 必须: 从 state.selected_strategy 读取 (VM state 单源真相)
        assert "state.selected_strategy" in code, "必须从 state.selected_strategy 读取 (R.2.2)"

        # 必须: 从 state.tier_hint 读取 (VM state 单源真相)
        assert "state.tier_hint" in code, "必须从 state.tier_hint 读取 (R.2.2)"

    # ========================================================================
    # R.2.4: ScreenerView mode/page_size 双源移除 (VM state 单源真相)
    # ========================================================================

    def test_screener_view_reads_mode_from_vm(self):
        """R.2.4: View 从 VM state 读取 mode, 禁止本地 use_state 双源真相.

        DoD: grep `mode.*use_state\\|set_mode` ui/views/screener_view.py = 0.
        VM 已有 switch_to_history()/switch_to_realtime() commands + state.mode.
        """
        import re

        code = _code_source()

        # 禁止: mode 通过 use_state 解构持有 (双源真相)
        mode_use_state = re.findall(r"^\s*mode\s*,\s*set_mode\s*=\s*ft\.use_state", code, re.MULTILINE)
        assert mode_use_state == [], f"mode 不应使用 use_state (双源真相, R.2.4): {mode_use_state}"

        # 禁止: set_mode 任何调用/解构 (VM switch_to_history/switch_to_realtime 内聚)
        set_mode_matches = re.findall(r"\bset_mode\b", code)
        assert set_mode_matches == [], (
            f"禁止 set_mode 调用 (VM switch_to_history/switch_to_realtime 内聚, R.2.4): {set_mode_matches}"
        )

        # 必须: 从 state.mode 读取 (VM state 单源真相)
        assert "state.mode" in code, "必须从 state.mode 读取 (R.2.4)"

    def test_screener_view_reads_page_size_from_vm(self):
        """R.2.4: View 从 VM state 读取 page_size, 禁止本地 use_state 双源真相.

        DoD: grep `page_size.*use_state\\|set_page_size` ui/views/screener_view.py = 0.
        VM 已有 change_page_size() command + state.page_size.
        """
        import re

        code = _code_source()

        # 禁止: page_size 通过 use_state 解构持有 (双源真相)
        page_size_use_state = re.findall(r"^\s*page_size\s*,\s*set_page_size\s*=\s*ft\.use_state", code, re.MULTILINE)
        assert page_size_use_state == [], f"page_size 不应使用 use_state (双源真相, R.2.4): {page_size_use_state}"

        # 禁止: set_page_size 任何调用/解构 (VM change_page_size 内聚)
        set_page_size_matches = re.findall(r"\bset_page_size\b", code)
        assert set_page_size_matches == [], (
            f"禁止 set_page_size 调用 (VM change_page_size 内聚, R.2.4): {set_page_size_matches}"
        )

        # 必须: 从 state.page_size 读取 (VM state 单源真相)
        assert "state.page_size" in code, "必须从 state.page_size 读取 (R.2.4)"

    # ========================================================================
    # R.2.6.1: strategies_loaded/strategy_options 双源移除 (VM state 单源真相)
    # ========================================================================

    def test_screener_view_reads_strategies_loaded_from_vm(self):
        """R.2.6.1: View 从 VM state 读取 strategies_loaded, 禁止本地 use_state 双源真相.

        DoD: grep `strategies_loaded.*use_state\\|set_strategies_loaded` ui/views/screener_view.py = 0.
        VM 已有 load_strategies() command + state.strategies_loaded.
        """
        import re

        code = _code_source()

        # 禁止: strategies_loaded 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(
            r"^\s*strategies_loaded\s*,\s*set_strategies_loaded\s*=\s*ft\.use_state", code, re.MULTILINE
        )
        assert use_state_matches == [], f"strategies_loaded 不应使用 use_state (双源真相, R.2.6.1): {use_state_matches}"

        # 禁止: set_strategies_loaded 任何调用/解构 (VM load_strategies 内聚)
        set_matches = re.findall(r"\bset_strategies_loaded\b", code)
        assert set_matches == [], f"禁止 set_strategies_loaded 调用 (VM load_strategies 内聚, R.2.6.1): {set_matches}"

        # 必须: 从 state.strategies_loaded 读取 (VM state 单源真相)
        assert "state.strategies_loaded" in code, "必须从 state.strategies_loaded 读取 (R.2.6.1)"

    def test_screener_view_reads_strategy_options_from_vm(self):
        """R.2.6.1: View 从 VM state.strategies_with_dep 构建 Flet Options, 禁止本地 use_state 缓存.

        DoD: grep `strategy_options.*use_state\\|set_strategy_options` ui/views/screener_view.py = 0.
        VM state.strategies_with_dep 持有原始策略数据, View 每次渲染调 _build_strategy_options 构建,
        确保 locale 切换后 Options 自动重新翻译 (避免 use_state 缓存旧 locale 翻译).
        """
        import re

        code = _code_source()

        # 禁止: strategy_options 通过 use_state 解构持有 (双源真相 + locale 缓存问题)
        use_state_matches = re.findall(
            r"^\s*strategy_options\s*,\s*set_strategy_options\s*=\s*ft\.use_state", code, re.MULTILINE
        )
        assert use_state_matches == [], (
            f"strategy_options 不应使用 use_state (双源真相+locale缓存, R.2.6.1): {use_state_matches}"
        )

        # 禁止: set_strategy_options 任何调用/解构 (VM load_strategies 内聚)
        set_matches = re.findall(r"\bset_strategy_options\b", code)
        assert set_matches == [], f"禁止 set_strategy_options 调用 (VM load_strategies 内聚, R.2.6.1): {set_matches}"

        # 必须: 从 state.strategies_with_dep 构建 Options (VM state 单源真相)
        assert "state.strategies_with_dep" in code, "必须从 state.strategies_with_dep 构建 Options (R.2.6.1)"

    # ========================================================================
    # R.2.6.2: strategy_desc/strategy_desc_color 双源移除 (VM state 单源真相)
    # ========================================================================

    def test_screener_view_reads_strategy_desc_from_vm(self):
        """R.2.6.2: View 从 VM state 读取 strategy_desc, 禁止本地 use_state 双源真相.

        DoD: grep `strategy_desc.*use_state\\|set_strategy_desc` ui/views/screener_view.py = 0.
        VM 已有 update_strategy_desc() command + state.strategy_desc.
        """
        import re

        code = _code_source()

        # 禁止: strategy_desc 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(
            r"^\s*strategy_desc\s*,\s*set_strategy_desc\s*=\s*ft\.use_state", code, re.MULTILINE
        )
        assert use_state_matches == [], f"strategy_desc 不应使用 use_state (双源真相, R.2.6.2): {use_state_matches}"

        # 禁止: set_strategy_desc 任何调用/解构 (VM update_strategy_desc 内聚)
        set_matches = re.findall(r"\bset_strategy_desc\b", code)
        assert set_matches == [], f"禁止 set_strategy_desc 调用 (VM update_strategy_desc 内聚, R.2.6.2): {set_matches}"

        # 必须: 从 state.strategy_desc 读取 (VM state 单源真相)
        assert "state.strategy_desc" in code, "必须从 state.strategy_desc 读取 (R.2.6.2)"

    def test_screener_view_reads_strategy_desc_color_from_vm(self):
        """R.2.6.2: View 从 VM state 读取 strategy_desc_color, 禁止本地 use_state 双源真相.

        DoD: grep `strategy_desc_color.*use_state\\|set_strategy_desc_color` ui/views/screener_view.py = 0.
        VM state.strategy_desc_color 产出语义标识符 ("default"/"warning"), View 映射到 AppColors.
        """
        import re

        code = _code_source()

        # 禁止: strategy_desc_color 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(
            r"^\s*strategy_desc_color\s*,\s*set_strategy_desc_color\s*=\s*ft\.use_state",
            code,
            re.MULTILINE,
        )
        assert use_state_matches == [], (
            f"strategy_desc_color 不应使用 use_state (双源真相, R.2.6.2): {use_state_matches}"
        )

        # 禁止: set_strategy_desc_color 任何调用/解构 (VM update_strategy_desc 内聚)
        set_matches = re.findall(r"\bset_strategy_desc_color\b", code)
        assert set_matches == [], (
            f"禁止 set_strategy_desc_color 调用 (VM update_strategy_desc 内聚, R.2.6.2): {set_matches}"
        )

        # 必须: 从 state.strategy_desc_color 读取 (VM state 单源真相)
        assert "state.strategy_desc_color" in code, "必须从 state.strategy_desc_color 读取 (R.2.6.2)"

        # 必须: 调用 vm.update_strategy_desc (新 API, 替代 set_strategy_desc + set_strategy_desc_color)
        assert "vm.update_strategy_desc" in code, "必须调用 vm.update_strategy_desc (R.2.6.2 新 API)"

        # 必须: 存在 _resolve_strategy_desc_color 映射函数 (VM 不感知 AppColors, §3.2)
        assert "_resolve_strategy_desc_color" in code, (
            "必须有 _resolve_strategy_desc_color 映射函数 (VM 不感知 AppColors, R.2.6.2)"
        )

    # ========================================================================
    # R.2.6.3: status_msg/status_color 双源移除 (VM state 单源真相)
    # ========================================================================

    def test_screener_view_no_status_msg_use_state(self):
        """R.2.6.3: View 禁止 use_state 持有 status_msg, 改从 VM state.status_message 读取.

        DoD: grep `status_msg.*use_state\\|set_status_msg` ui/views/screener_view.py = 0.
        VM 已有 set_history_viewing_status() command + state.status_message.
        """
        import re

        code = _code_source()

        # 禁止: status_msg 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(r"^\s*status_msg\s*,\s*set_status_msg\s*=\s*ft\.use_state", code, re.MULTILINE)
        assert use_state_matches == [], f"status_msg 不应使用 use_state (双源真相, R.2.6.3): {use_state_matches}"

        # 禁止: status_message 全称通过 use_state 解构持有 (QA m-4: 防止用全称创建新双源)
        use_state_full_matches = re.findall(
            r"^\s*status_message\s*,\s*set_status_message\s*=\s*ft\.use_state", code, re.MULTILINE
        )
        assert use_state_full_matches == [], (
            f"status_message 不应使用 use_state (双源真相, R.2.6.3): {use_state_full_matches}"
        )

        # 禁止: set_status_msg 任何调用/解构 (VM set_history_viewing_status 内聚)
        set_matches = re.findall(r"\bset_status_msg\b", code)
        assert set_matches == [], (
            f"禁止 set_status_msg 调用 (VM set_history_viewing_status 内聚, R.2.6.3): {set_matches}"
        )

    def test_screener_view_no_status_color_use_state(self):
        """R.2.6.3: View 禁止 use_state 持有 status_color, 改从 VM state.status_color 读取.

        DoD: grep `status_color.*use_state\\|set_status_color` ui/views/screener_view.py = 0.
        VM state.status_color 已承载所有状态颜色 (run/history/error/success).
        """
        import re

        code = _code_source()

        # 禁止: status_color 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(
            r"^\s*status_color\s*,\s*set_status_color\s*=\s*ft\.use_state", code, re.MULTILINE
        )
        assert use_state_matches == [], f"status_color 不应使用 use_state (双源真相, R.2.6.3): {use_state_matches}"

        # 禁止: set_status_color 任何调用/解构 (VM 内聚)
        set_matches = re.findall(r"\bset_status_color\b", code)
        assert set_matches == [], f"禁止 set_status_color 调用 (VM 内聚, R.2.6.3): {set_matches}"

        # 必须: 调用 vm.set_history_viewing_status (新 API, 替代 set_status_msg + set_status_color)
        assert "vm.set_history_viewing_status" in code, "必须调用 vm.set_history_viewing_status (R.2.6.3 新 API)"

        # 必须: 从 state.status_message 渲染 (VM state 单源真相, 无 else 回退)
        assert "state.status_message" in code, "必须从 state.status_message 读取 (R.2.6.3)"

        # 必须: 从 state.status_color 读取 (VM state 单源真相, 与 state.status_message 对称)
        assert "state.status_color" in code, "必须从 state.status_color 读取 (R.2.6.3)"

    # ========================================================================
    # Task 3.2: progress_visible/run_disabled/export_disabled 派生状态契约
    # 消除双轨状态: View 禁止 use_state 持有, 改为从 VM state 派生
    # ========================================================================

    def test_no_progress_visible_use_state(self):
        """Task 3.2: View 禁止 use_state 持有 progress_visible, 改为派生 state.loading.

        DoD: grep `progress_visible.*use_state\\|set_progress_visible` ui/views/screener_view.py = 0.
        """
        import re

        code = _code_source()

        # 禁止: progress_visible 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(r"progress_visible[^\n]*use_state", code)
        assert use_state_matches == [], (
            f"progress_visible 不应使用 use_state (派生 state.loading, Task 3.2): {use_state_matches}"
        )

        # 禁止: set_progress_visible 任何调用/解构
        set_matches = re.findall(r"\bset_progress_visible\b", code)
        assert set_matches == [], f"禁止 set_progress_visible 调用 (派生 state.loading, Task 3.2): {set_matches}"

        # 必须: 从 state.loading 派生 progress_visible
        assert "progress_visible = state.loading" in code, "必须从 state.loading 派生 progress_visible (Task 3.2)"

    def test_no_run_disabled_use_state(self):
        """Task 3.2: View 禁止 use_state 持有 run_disabled, 改为派生 state.loading + state.selected_strategy.

        DoD: grep `run_disabled.*use_state\\|set_run_disabled` ui/views/screener_view.py = 0.
        """
        import re

        code = _code_source()

        # 禁止: run_disabled 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(r"run_disabled[^\n]*use_state", code)
        assert use_state_matches == [], (
            f"run_disabled 不应使用 use_state (派生 state.loading+selected_strategy, Task 3.2): {use_state_matches}"
        )

        # 禁止: set_run_disabled 任何调用/解构
        set_matches = re.findall(r"\bset_run_disabled\b", code)
        assert set_matches == [], (
            f"禁止 set_run_disabled 调用 (派生 state.loading+selected_strategy, Task 3.2): {set_matches}"
        )

        # 必须: 从 state.loading + state.selected_strategy 派生 run_disabled
        assert "run_disabled = state.loading or not state.selected_strategy" in code, (
            "必须从 state.loading + state.selected_strategy 派生 run_disabled (Task 3.2)"
        )

    def test_no_export_disabled_use_state(self):
        """Task 3.2: View 禁止 use_state 持有 export_disabled, 改为派生 state.total_items == 0.

        DoD: grep `export_disabled.*use_state\\|set_export_disabled` ui/views/screener_view.py = 0.
        """
        import re

        code = _code_source()

        # 禁止: export_disabled 通过 use_state 解构持有 (双源真相)
        use_state_matches = re.findall(r"export_disabled[^\n]*use_state", code)
        assert use_state_matches == [], (
            f"export_disabled 不应使用 use_state (派生 state.total_items, Task 3.2): {use_state_matches}"
        )

        # 禁止: set_export_disabled 任何调用/解构
        set_matches = re.findall(r"\bset_export_disabled\b", code)
        assert set_matches == [], f"禁止 set_export_disabled 调用 (派生 state.total_items, Task 3.2): {set_matches}"

        # 必须: 从 state.total_items 派生 export_btn_disabled
        assert "export_btn_disabled = total_items == 0" in code, (
            "必须从 state.total_items 派生 export_btn_disabled (Task 3.2)"
        )

    def test_no_history_tree_use_state(self):
        """Task 3.2: View 禁止 use_state 持有历史树状态, 改为从 state.history_tree 派生.

        DoD: grep `history_tree_items.*use_state\\|set_history_tree_items\\|
        history_tree_offset.*use_state\\|set_history_tree_offset\\|
        history_load_more_visible.*use_state\\|set_history_load_more_visible`
        ui/views/screener_view.py = 0.
        """
        import re

        code = _code_source()

        # 禁止: history_tree_items 通过 use_state 解构持有
        items_use_state = re.findall(r"history_tree_items[^\n]*use_state", code)
        assert items_use_state == [], (
            f"history_tree_items 不应使用 use_state (派生 state.history_tree.rows, Task 3.2): {items_use_state}"
        )

        # 禁止: history_tree_offset 通过 use_state 解构持有
        offset_use_state = re.findall(r"history_tree_offset[^\n]*use_state", code)
        assert offset_use_state == [], (
            f"history_tree_offset 不应使用 use_state (派生 state.history_tree.offset, Task 3.2): {offset_use_state}"
        )

        # 禁止: history_load_more_visible 通过 use_state 解构持有
        load_more_use_state = re.findall(r"history_load_more_visible[^\n]*use_state", code)
        assert load_more_use_state == [], (
            f"history_load_more_visible 不应使用 use_state (派生 state.history_tree.has_more, Task 3.2): "
            f"{load_more_use_state}"
        )

        # 禁止: set_history_tree_items / set_history_tree_offset / set_history_load_more_visible 调用
        for setter in ("set_history_tree_items", "set_history_tree_offset", "set_history_load_more_visible"):
            set_matches = re.findall(rf"\b{setter}\b", code)
            assert set_matches == [], f"禁止 {setter} 调用 (派生 state.history_tree, Task 3.2): {set_matches}"

        # 必须: 从 state.history_tree 派生历史树控件
        assert "state.history_tree" in code, "必须从 state.history_tree 派生历史树控件 (Task 3.2)"

    # ========================================================================
    # Phase 3.3: ConfigHandler/ThreadPoolManager 下沉到 ScreenerViewModel
    # View 不直接 import 业务编排对象 (CLAUDE.md §3.2 MVVM)
    # ========================================================================

    def test_screener_view_does_not_import_config_handler_or_thread_pool(self):
        """Phase 3.3: View 不应直接 import ConfigHandler/ThreadPoolManager/TaskType (已下沉到 VM).

        DoD: AST 扫描 screener_view.py 源码, 无 ConfigHandler/ThreadPoolManager/TaskType
        的 import 语句 (含 alias / lazy import / from parent import child).
        跳过 TYPE_CHECKING 块内的 import (仅类型注解用途, 不引入运行时依赖).
        """
        import ast
        from pathlib import Path

        import ui.views.screener_view as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # TYPE_CHECKING 块内 import 不算违规 (类型注解用途)
        type_checking_imports: set[tuple[str, str]] = set()

        def _is_type_checking_test(test: ast.expr) -> bool:
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
            return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"

        for node in ast.walk(tree):
            if isinstance(node, ast.If) and _is_type_checking_test(node.test):
                for stmt in node.body:
                    if isinstance(stmt, ast.ImportFrom):
                        for alias in stmt.names:
                            bound = alias.asname or alias.name
                            type_checking_imports.add((stmt.module or "", bound))
                    elif isinstance(stmt, ast.Import):
                        for alias in stmt.names:
                            bound = alias.asname or alias.name.split(".")[-1]
                            type_checking_imports.add((alias.name, bound))

        forbidden_symbols = {"ConfigHandler", "ThreadPoolManager", "TaskType"}
        violations: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # 跳过 TYPE_CHECKING 块内 import
                if any(
                    (node.module or "", alias.asname or alias.name) in type_checking_imports for alias in node.names
                ):
                    continue
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if bound in forbidden_symbols:
                        violations.append(
                            f"line {node.lineno}: from {node.module} import {alias.name}"
                            + (f" as {alias.asname}" if alias.asname else "")
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[-1]
                    if bound in forbidden_symbols:
                        violations.append(
                            f"line {node.lineno}: import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
                        )

        assert violations == [], (
            "screener_view.py 不应直接 import ConfigHandler/ThreadPoolManager/TaskType "
            "(Phase 3.3 已下沉到 ScreenerViewModel, CLAUDE.md §3.2 MVVM): " + str(violations)
        )

    def test_screener_view_calls_vm_reset_strategy_prompt(self):
        """Phase 3.3: View 必须通过 vm.reset_strategy_prompt 消费业务编排 (替代 ConfigHandler + ThreadPoolManager)."""
        code = _code_source()
        assert "vm.reset_strategy_prompt" in code, "必须调用 vm.reset_strategy_prompt (Phase 3.3 新 API)"

    def test_screener_view_calls_vm_save_strategy_prompt(self):
        """Phase 3.3: View 必须通过 vm.save_strategy_prompt 消费业务编排 (替代 validate_prompt + ConfigHandler)."""
        code = _code_source()
        assert "vm.save_strategy_prompt" in code, "必须调用 vm.save_strategy_prompt (Phase 3.3 新 API)"
