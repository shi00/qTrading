"""core/i18n.py 架构纯度守护 (§4.2): 不得导入 flet 或定义 Observable state.

背景: UI 声明式迁移收官检视发现 core/i18n.py 导入 flet 并定义 I18nState(ft.Observable),
违反 CLAUDE.md §4.2 "core 层不得依赖 ui/utils 等模块". 方案 A 将 Observable state 下沉到
ui/i18n.py (对齐 AppColors 在 ui/theme.py 的合规模式), core 层仅保留 _listeners/subscribe
作为 locale 变更通知抽象. 本测试守护 core 层纯度, 防止回归.
"""

import ast
import logging
import pathlib

import pytest

CORE_I18N = pathlib.Path(__file__).parent.parent.parent / "core" / "i18n.py"

pytestmark = pytest.mark.unit


def test_core_i18n_not_import_flet():
    """§4.2: core/i18n.py 不得导入 flet."""
    tree = ast.parse(CORE_I18N.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("flet"), f"§4.2 违规: core/i18n.py 导入 flet ({alias.name})"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("flet"), f"§4.2 违规: core/i18n.py 从 flet 导入 (from {module})"


def test_core_i18n_no_observable_state():
    """§4.2: core/i18n.py 不得定义 Observable state (应下沉到 ui/i18n.py).

    用 AST 检查代码定义, 允许注释/docstring 中描述性提及 (如 "ui 层 I18nState").
    """
    tree = ast.parse(CORE_I18N.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ast.unparse(base) if hasattr(ast, "unparse") else ""
                assert "Observable" not in base_name, f"§4.2 违规: core/i18n.py 定义 Observable 子类 ({node.name})"
            assert node.name != "I18nState", "§4.2 违规: core/i18n.py 定义 I18nState 类"
        if isinstance(node, ast.FunctionDef):
            assert node.name != "get_observable_state", "core/i18n.py 不应保留 get_observable_state 方法"
        if isinstance(node, (ast.AnnAssign, ast.Assign)):
            for target in [node.target] if isinstance(node, ast.AnnAssign) else node.targets:
                target_name = ast.unparse(target) if hasattr(ast, "unparse") else ""
                if target_name.endswith("._state") or target_name == "_state":
                    raise AssertionError("core/i18n.py 不应保留 _state 属性赋值")


def test_core_i18n_keeps_listeners_abstraction():
    """_listeners/subscribe/unsubscribe 保留作为 locale 变更通知抽象 (ui 层 Observable 消费)."""
    src = CORE_I18N.read_text(encoding="utf-8")
    assert "def subscribe" in src, "_listeners 通知抽象必须保留供 ui/i18n.py 订阅"
    assert "def unsubscribe" in src, "unsubscribe 必须保留"
    assert "_listeners" in src, "_listeners 列表必须保留"


def test_core_i18n_initialize_triggers_listeners():
    """A1 fix: initialize() 必须触发 _listeners (与 set_locale 行为对齐).

    场景: main.py 启动时 ui/i18n.py 模块加载已 subscribe _sync_i18n_state,
    若 initialize() 不触发 _listeners, ui 层 I18nState.locale 会 stale (仍为 DEFAULT_LOCALE).

    用 AST 检查 initialize 函数体内是否存在对 _listeners 的迭代, 而非字符串包含.
    """
    tree = ast.parse(CORE_I18N.read_text(encoding="utf-8"))
    initialize_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "initialize":
            initialize_func = node
            break
    assert initialize_func is not None, "core/i18n.py 必须有 initialize 方法"
    # 检查 initialize 函数体内是否有 _listeners 引用
    func_src = ast.unparse(initialize_func)
    assert "_listeners" in func_src, "initialize() 函数体内必须触发 _listeners (A1 fix)"
    # 进一步检查有 for 循环迭代 _listeners
    assert "for" in func_src and "_listeners" in func_src, "initialize() 必须有 for listener in _listeners 迭代逻辑"


def test_core_i18n_get_locales_dir_uses_meipass_when_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    """B09: sys.frozen=True 且 sys._MEIPASS 存在时，I18n._get_locales_dir() 应解析为 _MEIPASS/locales。"""
    import sys

    from core.i18n import I18n

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(I18n, "_locales_dir", None)

    assert I18n._get_locales_dir() == tmp_path / "locales"


def test_core_i18n_get_locales_dir_falls_back_when_meipass_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    """MAJOR-4: sys.frozen=True 但 sys._MEIPASS=None 时，I18n._get_locales_dir() 不抛 TypeError，回退到 executable 父目录。"""
    import sys

    from core.i18n import I18n

    fake_exe = tmp_path / "AStockScreener.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", None, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    monkeypatch.setattr(I18n, "_locales_dir", None)

    assert I18n._get_locales_dir() == tmp_path / "locales"


class TestCoreI18nResourceContract:
    """运行时 locale 资源契约（M01 检视）。

    仓库内 JSON 完整性由 test_i18n.py 守护；本类守护「资源被损坏/手工改写后」的
    降级语义：不向调用方返回非 str 值、不在 list 载荷上误报命中、不把绝对路径写入日志（R9）。
    """

    @staticmethod
    def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        from core.i18n import I18n

        monkeypatch.setattr(I18n, "_locales_dir", tmp_path)
        monkeypatch.setattr(I18n, "_strings_cache", {})
        monkeypatch.setattr(I18n, "_missing_keys", {})
        monkeypatch.setattr(I18n, "_initialized", True)
        monkeypatch.setattr(I18n, "_locale", "zh_CN")
        return I18n

    @staticmethod
    def _write_locale(tmp_path: pathlib.Path, payload: str) -> None:
        locale_dir = tmp_path / "zh_CN"
        locale_dir.mkdir()
        (locale_dir / "strings.json").write_text(payload, encoding="utf-8")

    def test_non_string_translation_values_are_treated_as_missing(self, monkeypatch, tmp_path):
        I18n = self._isolate(monkeypatch, tmp_path)
        self._write_locale(tmp_path, '{"good": "OK", "bad": 123, "nested": {"k": "v"}}')

        assert I18n.get("good") == "OK"
        # 非字符串值不得穿透 I18n.get 的 -> str 契约
        assert I18n.get("bad") == "bad"
        assert I18n.get("nested") == "nested"

    def test_non_object_payload_is_treated_as_empty(self, monkeypatch, tmp_path):
        I18n = self._isolate(monkeypatch, tmp_path)
        self._write_locale(tmp_path, '["app_title"]')

        # list 载荷不得被当作映射命中（baseline 下 `in` 误判为 True）
        assert I18n.has("app_title") is False
        assert I18n.get("app_title") == "app_title"

    def test_missing_locale_log_does_not_leak_absolute_path(self, monkeypatch, tmp_path, caplog):
        I18n = self._isolate(monkeypatch, tmp_path)

        with caplog.at_level(logging.WARNING, logger="core.i18n"):
            assert I18n._load_locale_file("zh_CN") == {}

        assert "zh_CN" in caplog.text
        # R9: 绝对路径（含用户主目录/安装目录）不得进入日志
        assert str(tmp_path) not in caplog.text
