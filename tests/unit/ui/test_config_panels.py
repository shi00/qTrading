from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名（作为变更说明）导致字符串匹配误判。
    """
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect_docstring_lines(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect_docstring_lines(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect_docstring_lines(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


class TestDatabaseConfigPanelContract:
    """DatabaseConfigPanel 声明式契约守护测试（Phase 3.2.1）。

    业务逻辑由 VM 单元测试覆盖（test_database_config_panel_view_model.py）。
    View 层测试聚焦于：
    1. 纯函数测试（_render_message）
    2. 契约守护（grep 检查禁止的命令式模式：did_mount/.update()/refresh_locale）
    """

    def test_database_config_panel_is_ft_component(self):
        """DoD: DatabaseConfigPanel 必须被 @ft.component 装饰。"""
        from ui.components.config_panels.database_config_panel import DatabaseConfigPanel

        assert hasattr(DatabaseConfigPanel, "__wrapped__"), "DatabaseConfigPanel 必须用 @ft.component 装饰"

    def test_database_config_panel_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        import ui.components.config_panels.database_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "did_mount" not in source, "DatabaseConfigPanel 不应使用 did_mount（命令式）"

    def test_database_config_panel_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        import ui.components.config_panels.database_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "will_unmount" not in source, "DatabaseConfigPanel 不应使用 will_unmount（命令式）"

    def test_database_config_panel_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        import ui.components.config_panels.database_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert ".update()" not in source, "DatabaseConfigPanel 不应使用 .update()（命令式）"
        assert "_safe_update" not in source, "DatabaseConfigPanel 不应使用 _safe_update（命令式）"

    def test_database_config_panel_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale / _on_locale_change（声明式用 ft.use_state 自动重渲染）。"""
        import ui.components.config_panels.database_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "refresh_locale" not in source, "DatabaseConfigPanel 不应使用 refresh_locale（声明式自动重渲染）"
        assert "_on_locale_change" not in source, "DatabaseConfigPanel 不应使用 _on_locale_change（声明式自动重渲染）"

    def test_database_config_panel_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        import ui.components.config_panels.database_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "@ft.component" in source, "DatabaseConfigPanel 必须用 @ft.component 装饰"

    def test_database_config_panel_uses_i18n_observable_state(self):
        """DoD: 必须通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 变化。"""
        import ui.components.config_panels.database_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "I18n.get_observable_state" in source, "DatabaseConfigPanel 必须订阅 I18n.get_observable_state"

    def test_database_config_panel_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        import ui.components.config_panels.database_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "class DatabaseConfigPanel(" not in source, "DatabaseConfigPanel 不应是 class（命令式）"


class TestRenderMessage:
    """_render_message 纯函数测试。"""

    def test_render_message_none_returns_empty(self):
        from ui.components.config_panels.database_config_panel import _render_message

        assert _render_message(None) == ""

    def test_render_message_with_default_param(self):
        from ui.components.config_panels.database_config_panel import _render_message
        from ui.viewmodels import Message

        msg = Message("_raw_msg_", {"default": "raw error text"})
        result = _render_message(msg)
        assert result == "raw error text"


class TestTushareConfigPanelContract:
    """TushareConfigPanel 声明式契约守护测试（Phase 3.2.2）。

    业务逻辑由 VM 单元测试覆盖（test_tushare_config_panel_view_model.py）。
    View 层测试聚焦于：
    1. 纯函数测试（_render_message / _build_tier_options）
    2. 契约守护（grep 检查禁止的命令式模式：did_mount/.update()/refresh_locale/class 继承）
    """

    def test_tushare_config_panel_is_ft_component(self):
        """DoD: TushareConfigPanel 必须被 @ft.component 装饰。"""
        from ui.components.config_panels.tushare_config_panel import TushareConfigPanel

        assert hasattr(TushareConfigPanel, "__wrapped__"), "TushareConfigPanel 必须用 @ft.component 装饰"

    def test_tushare_config_panel_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "did_mount" not in source, "TushareConfigPanel 不应使用 did_mount（命令式）"

    def test_tushare_config_panel_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "will_unmount" not in source, "TushareConfigPanel 不应使用 will_unmount（命令式）"

    def test_tushare_config_panel_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert ".update()" not in source, "TushareConfigPanel 不应使用 .update()（命令式）"
        assert "_safe_update" not in source, "TushareConfigPanel 不应使用 _safe_update（命令式）"

    def test_tushare_config_panel_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale / _on_locale_change（声明式用 ft.use_state 自动重渲染）。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "refresh_locale" not in source, "TushareConfigPanel 不应使用 refresh_locale（声明式自动重渲染）"
        assert "_on_locale_change" not in source, "TushareConfigPanel 不应使用 _on_locale_change（声明式自动重渲染）"

    def test_tushare_config_panel_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "@ft.component" in source, "TushareConfigPanel 必须用 @ft.component 装饰"

    def test_tushare_config_panel_uses_i18n_observable_state(self):
        """DoD: 必须通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 变化。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "I18n.get_observable_state" in source, "TushareConfigPanel 必须订阅 I18n.get_observable_state"

    def test_tushare_config_panel_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        import ui.components.config_panels.tushare_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "class TushareConfigPanel(" not in source, "TushareConfigPanel 不应是 class（命令式）"


class TestTushareRenderMessage:
    """TushareConfigPanel._render_message 纯函数测试。"""

    def test_render_message_none_returns_empty(self):
        from ui.components.config_panels.tushare_config_panel import _render_message

        assert _render_message(None) == ""

    def test_render_message_with_default_param(self):
        from ui.components.config_panels.tushare_config_panel import _render_message
        from ui.viewmodels import Message

        msg = Message("_raw_msg_", {"default": "raw error text"})
        result = _render_message(msg)
        assert result == "raw error text"


class TestLLMConfigPanelContract:
    """LLMConfigPanel 声明式契约守护测试（Phase 3.2.3）。

    业务逻辑由 VM 单元测试覆盖（test_llm_config_panel_view_model.py）。
    View 层测试聚焦于：
    1. 纯函数测试（_render_message）
    2. 契约守护（grep 检查禁止的命令式模式：did_mount/.update()/refresh_locale/class 继承）
    """

    def test_llm_config_panel_is_ft_component(self):
        """DoD: LLMConfigPanel 必须被 @ft.component 装饰。"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        assert hasattr(LLMConfigPanel, "__wrapped__"), "LLMConfigPanel 必须用 @ft.component 装饰"

    def test_llm_config_panel_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "did_mount" not in source, "LLMConfigPanel 不应使用 did_mount（命令式）"

    def test_llm_config_panel_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "will_unmount" not in source, "LLMConfigPanel 不应使用 will_unmount（命令式）"

    def test_llm_config_panel_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert ".update()" not in source, "LLMConfigPanel 不应使用 .update()（命令式）"
        assert "_safe_update" not in source, "LLMConfigPanel 不应使用 _safe_update（命令式）"

    def test_llm_config_panel_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale / _on_locale_change（声明式用 ft.use_state 自动重渲染）。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "refresh_locale" not in source, "LLMConfigPanel 不应使用 refresh_locale（声明式自动重渲染）"
        assert "_on_locale_change" not in source, "LLMConfigPanel 不应使用 _on_locale_change（声明式自动重渲染）"

    def test_llm_config_panel_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "@ft.component" in source, "LLMConfigPanel 必须用 @ft.component 装饰"

    def test_llm_config_panel_uses_i18n_observable_state(self):
        """DoD: 必须通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 变化。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "I18n.get_observable_state" in source, "LLMConfigPanel 必须订阅 I18n.get_observable_state"

    def test_llm_config_panel_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        import ui.components.config_panels.llm_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "class LLMConfigPanel(" not in source, "LLMConfigPanel 不应是 class（命令式）"


class TestLLMRenderMessage:
    """LLMConfigPanel._render_message 纯函数测试。"""

    def test_render_message_none_returns_empty(self):
        from ui.components.config_panels.llm_config_panel import _render_message

        assert _render_message(None) == ""

    def test_render_message_with_default_param(self):
        from ui.components.config_panels.llm_config_panel import _render_message
        from ui.viewmodels import Message

        msg = Message("_raw_msg_", {"default": "raw error text"})
        result = _render_message(msg)
        assert result == "raw error text"


class TestLocalModelConfigPanelContract:
    """LocalModelConfigPanel 声明式契约守护测试（Phase 3.2.4）。

    业务逻辑由 VM 单元测试覆盖（test_local_model_config_panel_view_model.py）。
    View 层测试聚焦于：
    1. 纯函数测试（_render_message）
    2. 契约守护（grep 检查禁止的命令式模式：did_mount/.update()/refresh_locale/class 继承）
    """

    def test_local_model_config_panel_is_ft_component(self):
        """DoD: LocalModelConfigPanel 必须被 @ft.component 装饰。"""
        from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel

        assert hasattr(LocalModelConfigPanel, "__wrapped__"), "LocalModelConfigPanel 必须用 @ft.component 装饰"

    def test_local_model_config_panel_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "did_mount" not in source, "LocalModelConfigPanel 不应使用 did_mount（命令式）"

    def test_local_model_config_panel_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "will_unmount" not in source, "LocalModelConfigPanel 不应使用 will_unmount（命令式）"

    def test_local_model_config_panel_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert ".update()" not in source, "LocalModelConfigPanel 不应使用 .update()（命令式）"
        assert "_safe_update" not in source, "LocalModelConfigPanel 不应使用 _safe_update（命令式）"

    def test_local_model_config_panel_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale / _on_locale_change（声明式用 ft.use_state 自动重渲染）。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "refresh_locale" not in source, "LocalModelConfigPanel 不应使用 refresh_locale（声明式自动重渲染）"
        assert "_on_locale_change" not in source, "LocalModelConfigPanel 不应使用 _on_locale_change（声明式自动重渲染）"

    def test_local_model_config_panel_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "@ft.component" in source, "LocalModelConfigPanel 必须用 @ft.component 装饰"

    def test_local_model_config_panel_uses_i18n_observable_state(self):
        """DoD: 必须通过 ft.use_state(I18n.get_observable_state) 订阅 i18n 变化。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "I18n.get_observable_state" in source, "LocalModelConfigPanel 必须订阅 I18n.get_observable_state"

    def test_local_model_config_panel_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        import ui.components.config_panels.local_model_config_panel as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "class LocalModelConfigPanel(" not in source, "LocalModelConfigPanel 不应是 class（命令式）"


class TestLocalModelRenderMessage:
    """LocalModelConfigPanel._render_message 纯函数测试。"""

    def test_render_message_none_returns_empty(self):
        from ui.components.config_panels.local_model_config_panel import _render_message

        assert _render_message(None) == ""

    def test_render_message_with_default_param(self):
        from ui.components.config_panels.local_model_config_panel import _render_message
        from ui.viewmodels import Message

        msg = Message("_raw_msg_", {"default": "raw error text"})
        result = _render_message(msg)
        assert result == "raw error text"
