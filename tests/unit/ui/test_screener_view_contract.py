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
