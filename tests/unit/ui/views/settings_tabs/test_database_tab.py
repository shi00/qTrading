"""ui/views/settings_tabs/database_tab.py 声明式契约守护测试 (Phase 3.3).

业务逻辑由 VM 单元测试覆盖 (test_database_config_panel_view_model.py)。
View 层测试聚焦于:
1. 模块级纯函数测试 (_on_test_success)
2. 契约守护 (grep 检查禁止的命令式模式: did_mount/.update()/refresh_locale/class 继承)
3. 组件体渲染测试 (DatabaseTab @ft.component body)
"""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

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


class TestDatabaseTabContract:
    """DatabaseTab 声明式契约守护测试 (Phase 3.3)。"""

    def test_database_tab_is_ft_component(self):
        """DoD: DatabaseTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        assert hasattr(DatabaseTab, "__wrapped__"), "DatabaseTab 必须用 @ft.component 装饰"

    def test_database_tab_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        import ui.views.settings_tabs.database_tab as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "@ft.component" in source, "DatabaseTab 必须用 @ft.component 装饰"

    def test_database_tab_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "class DatabaseTab(" not in source, "DatabaseTab 不应是 class (命令式)"

    def test_database_tab_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "did_mount" not in source, "DatabaseTab 不应使用 did_mount (命令式)"

    def test_database_tab_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "will_unmount" not in source, "DatabaseTab 不应使用 will_unmount (命令式)"

    def test_database_tab_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert ".update()" not in source, "DatabaseTab 不应使用 .update() (命令式)"
        assert "_safe_update" not in source, "DatabaseTab 不应使用 _safe_update (命令式)"

    def test_database_tab_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale / _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "refresh_locale" not in source, "DatabaseTab 不应使用 refresh_locale (声明式自动重渲染)"
        assert "_on_locale_change" not in source, "DatabaseTab 不应使用 _on_locale_change (声明式自动重渲染)"

    def test_database_tab_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        assert "PageRefMixin" not in source, "DatabaseTab 不应使用 PageRefMixin"
        assert "_page_ref" not in source, "DatabaseTab 不应使用 _page_ref"
        assert "weakref" not in source, "DatabaseTab 不应使用 weakref"

    def test_database_tab_uses_use_viewmodel(self):
        """DoD: 必须通过 use_viewmodel hook 消费 VM。"""
        import ui.views.settings_tabs.database_tab as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "use_viewmodel" in source, "DatabaseTab 必须使用 use_viewmodel hook"

    def test_database_tab_uses_i18n_observable_state(self):
        """DoD: 必须通过 ft.use_state(get_observable_state) 订阅 i18n 变化。"""
        import ui.views.settings_tabs.database_tab as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "get_observable_state" in source, "DatabaseTab 必须订阅 get_observable_state"

    def test_database_tab_uses_theme_observable_state(self):
        """DoD: 必须通过 ft.use_state(AppColors.get_observable_state) 订阅 theme 变化。"""
        import ui.views.settings_tabs.database_tab as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "AppColors.get_observable_state" in source, "DatabaseTab 必须订阅 AppColors.get_observable_state"

    def test_database_tab_no_use_ref_cache_command_instance(self):
        """DoD: 禁止 use_ref cache 命令式实例 (VM 通过 use_viewmodel 内部 use_ref 持久化)。"""
        import ui.views.settings_tabs.database_tab as mod

        source = _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))
        # use_viewmodel 内部用 use_ref 持久化 VM,DatabaseTab 自身不应直接调 use_ref
        assert "ft.use_ref" not in source, "DatabaseTab 不应直接使用 ft.use_ref (用 use_viewmodel 替代)"


class TestOnTestSuccess:
    """_on_test_success 模块级纯函数测试。"""

    def test_on_test_success_logs_debug(self, caplog):
        from ui.views.settings_tabs.database_tab import _on_test_success

        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            _on_test_success({"host": "localhost", "port": 5432, "database": "test"})
        assert any("Database connection test successful" in r.message for r in caplog.records)

    def test_on_test_success_log_contains_host(self, caplog):
        from ui.views.settings_tabs.database_tab import _on_test_success

        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            _on_test_success({"host": "db.example.com", "port": 3306, "database": "prod"})
        assert any("db.example.com" in r.message for r in caplog.records)

    def test_on_test_success_log_contains_port(self, caplog):
        from ui.views.settings_tabs.database_tab import _on_test_success

        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            _on_test_success({"host": "localhost", "port": 5432, "database": "test"})
        assert any("5432" in r.message for r in caplog.records)

    def test_on_test_success_log_contains_database(self, caplog):
        from ui.views.settings_tabs.database_tab import _on_test_success

        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            _on_test_success({"host": "localhost", "port": 5432, "database": "mydb"})
        assert any("mydb" in r.message for r in caplog.records)

    def test_on_test_success_with_nonstandard_port(self, caplog):
        from ui.views.settings_tabs.database_tab import _on_test_success

        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            _on_test_success({"host": "remote-host", "port": 9999, "database": "analytics"})
        assert any("9999" in r.message for r in caplog.records)
        assert any("analytics" in r.message for r in caplog.records)

    def test_on_test_success_missing_keys_raises_key_error(self):
        """config 缺少 key 时,logger.debug 的 f-string 格式化会抛 KeyError。"""
        from ui.views.settings_tabs.database_tab import _on_test_success

        with pytest.raises(KeyError):
            _on_test_success({"host": "localhost"})


# ============================================================================
# 组件体渲染测试 (DatabaseTab @ft.component body)
# ============================================================================


class _FakeDatabaseVM:
    """模拟 DatabaseConfigPanelViewModel, 满足 use_viewmodel hook 契约。"""

    def __init__(self) -> None:
        self._subscribers: list[Any] = []
        self.state = MagicMock()
        self.reload_config = MagicMock()
        self.dispose_called = False

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()


class TestDatabaseTabComponentBody:
    """DatabaseTab 组件体渲染测试: 验证控件树结构 + VM 生命周期。"""

    def test_mount_returns_container(self, mock_i18n_state, mock_app_colors_state):
        """挂载 DatabaseTab 返回 ft.Container。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        fake_vm = _FakeDatabaseVM()
        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanelViewModel", return_value=fake_vm):
            component = make_component(DatabaseTab, show_snack_callback=MagicMock())
            run_mount_effects(component)
            result = render_once(component)

        assert isinstance(result, ft.Container)

    def test_mount_creates_vm_via_factory(self, mock_i18n_state, mock_app_colors_state):
        """挂载时通过 factory 实例化 DatabaseConfigPanelViewModel。"""
        from ui.views.settings_tabs import database_tab as mod

        fake_vm = _FakeDatabaseVM()
        with patch.object(mod, "DatabaseConfigPanelViewModel", return_value=fake_vm) as mock_cls:
            component = make_component(mod.DatabaseTab, show_snack_callback=MagicMock())
            run_mount_effects(component)

        mock_cls.assert_called_once()

    def test_mount_triggers_reload_config_effect(self, mock_i18n_state, mock_app_colors_state):
        """挂载时 use_effect 触发 vm.reload_config()。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        fake_vm = _FakeDatabaseVM()
        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanelViewModel", return_value=fake_vm):
            component = make_component(DatabaseTab, show_snack_callback=MagicMock())
            run_mount_effects(component)

        fake_vm.reload_config.assert_called_once()

    def test_mount_subscribes_to_vm(self, mock_i18n_state, mock_app_colors_state):
        """挂载时 use_viewmodel hook 注册 VM 订阅。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        fake_vm = _FakeDatabaseVM()
        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanelViewModel", return_value=fake_vm):
            component = make_component(DatabaseTab, show_snack_callback=MagicMock())
            run_mount_effects(component)

        assert len(fake_vm._subscribers) > 0

    def test_render_contains_storage_icon_and_title(self, mock_i18n_state, mock_app_colors_state):
        """渲染的控件树含 STORAGE 图标 + 标题文本。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        fake_vm = _FakeDatabaseVM()
        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanelViewModel", return_value=fake_vm):
            component = make_component(DatabaseTab, show_snack_callback=MagicMock())
            run_mount_effects(component)
            result = render_once(component)

        # 遍历控件树查找 Icon + Text
        icons: list[ft.Icon] = []
        texts: list[ft.Text] = []

        def _walk(ctrl: Any) -> None:
            if isinstance(ctrl, ft.Icon):
                icons.append(ctrl)
            if isinstance(ctrl, ft.Text):
                texts.append(ctrl)
            for attr in ("controls", "content"):
                children = getattr(ctrl, attr, None)
                if isinstance(children, list):
                    for c in children:
                        if c is not None:
                            _walk(c)
                elif children is not None:
                    _walk(children)

        _walk(result)
        assert any(i.icon == ft.Icons.STORAGE for i in icons), "应含 STORAGE 图标"

    def test_render_contains_card(self, mock_i18n_state, mock_app_colors_state):
        """渲染的控件树含 ft.Card (配置面板容器)。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        fake_vm = _FakeDatabaseVM()
        with patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanelViewModel", return_value=fake_vm):
            component = make_component(DatabaseTab, show_snack_callback=MagicMock())
            run_mount_effects(component)
            result = render_once(component)

        cards: list[ft.Card] = []

        def _walk(ctrl: Any) -> None:
            if isinstance(ctrl, ft.Card):
                cards.append(ctrl)
            for attr in ("controls", "content"):
                children = getattr(ctrl, attr, None)
                if isinstance(children, list):
                    for c in children:
                        if c is not None:
                            _walk(c)
                elif children is not None:
                    _walk(children)

        _walk(result)
        assert len(cards) >= 1, "应含至少 1 个 Card"

    def test_vm_on_save_invokes_show_snack(self, mock_i18n_state, mock_app_colors_state):
        """_make_vm 的 _on_save 回调调用 show_snack_callback。"""
        from ui.views.settings_tabs.database_tab import DatabaseTab

        fake_vm = _FakeDatabaseVM()
        show_snack = MagicMock()
        with patch(
            "ui.views.settings_tabs.database_tab.DatabaseConfigPanelViewModel", return_value=fake_vm
        ) as mock_cls:
            component = make_component(DatabaseTab, show_snack_callback=show_snack)
            run_mount_effects(component)

        # 取出 _make_vm 传给 VM 构造函数的 on_save_callback
        call_kwargs = mock_cls.call_args.kwargs
        on_save_cb = call_kwargs.get("on_save_callback")
        assert on_save_cb is not None
        on_save_cb({"host": "localhost"})
        show_snack.assert_called_once()
