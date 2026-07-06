"""MockFletPage 契约测试：确保 mock 与真实 ft.Page 接口同步。

捕捉 Flet 0.28.3 → 0.85.3 升级时 MockFletPage 与真实 Page 的接口漂移：
- 正向契约：MockFletPage 显式实现的公开成员必须在真实 ft.Page 上存在
  （Flet 删除/重命名属性时立即失败，提醒同步 mock）
- 排除集校验：项目扩展（show_toast 由 main.py 动态挂载）不在
  ft.Page 原生接口上，需显式排除并验证排除集仍然准确
- V1 关键成员存在性：shared_preferences/services/show_dialog/pop_dialog
  在 V1 Page 上必备（R10/R11 配方应用后），逐项断言避免 mock 漂移
"""

import flet as ft
import pytest

from tests.unit.ui.mock_flet import MockFletPage

pytestmark = pytest.mark.unit

# 项目扩展方法/属性：由 main.py 动态挂载到 Page 实例，不在 flet 0.85.3 原生 Page 类上
# - show_toast: main.py 动态挂载（page.show_toast = show_toast）
# R11 已删除 mock 的 dialog/open/close，不再纳入排除集；R10 已将 client_storage 替换为
# shared_preferences，client_storage 同样不再纳入排除集。
_PROJECT_EXTENSIONS = frozenset({"show_toast"})

# V1 Page 必备成员（R10/R11 配方应用后 mock 与真实 Page 均应存在）
_V1_REQUIRED_MEMBERS = frozenset({"shared_preferences", "services", "show_dialog", "pop_dialog"})


def _mock_flet_page_public_members() -> set[str]:
    """获取 MockFletPage 显式定义的公开成员名（属性 + 方法 + property）。

    不含继承自 object 的成员（__dict__/__module__/__weakref__/__doc__）。
    """
    return {
        name
        for name in vars(MockFletPage)
        if not name.startswith("_") and name not in {"__dict__", "__module__", "__weakref__", "__doc__"}
    }


def _real_page_public_members() -> set[str]:
    """获取真实 ft.Page 的公开成员名（含继承自基类的属性/方法）。"""
    return {name for name in dir(ft.Page) if not name.startswith("_")}


def test_mock_flet_page_attrs_exist_on_real_page():
    """正向契约：MockFletPage 显式实现的公开成员必须在真实 ft.Page 上存在。

    捕捉 Flet 升级时 MockFletPage 残留已删除/重命名属性导致的接口漂移。
    项目扩展（show_toast）由 main.py 动态挂载，不在 ft.Page 原生接口上，
    因此显式排除。
    """
    mock_members = _mock_flet_page_public_members()
    real_members = _real_page_public_members()
    flet_native_members = mock_members - _PROJECT_EXTENSIONS

    # 防御：确保排除集没有误包含已移除的成员（排除集失效时测试空转）
    assert mock_members >= _PROJECT_EXTENSIONS, (
        f"_PROJECT_EXTENSIONS 包含 MockFletPage 上不存在的成员: {_PROJECT_EXTENSIONS - mock_members}"
    )

    missing = flet_native_members - real_members
    assert not missing, (
        f"MockFletPage 以下成员不在 ft.Page 公开接口中——flet 可能已升级，请检查 mock_flet.py 是否需要同步: {sorted(missing)}"
    )


def test_project_extensions_are_not_on_real_page():
    """排除集校验：项目扩展成员不应在真实 ft.Page 上存在。

    若某项目扩展已被 Flet 原生支持（出现在 ft.Page 上），应将其从
    _PROJECT_EXTENSIONS 移除并纳入正向契约检查，避免遗漏漂移检测。
    """
    real_members = _real_page_public_members()
    leaked = _PROJECT_EXTENSIONS & real_members
    assert not leaked, f"以下项目扩展已出现在 ft.Page 原生接口上，应从 _PROJECT_EXTENSIONS 移除: {sorted(leaked)}"


def test_v1_required_members_on_mock_and_real_page():
    """V1 关键成员存在性：shared_preferences/services/show_dialog/pop_dialog
    在 mock 与真实 ft.Page 上均应存在（R10/R11 配方应用后的契约守护）。

    - shared_preferences: R10 替代 V0 client_storage
    - services: R4 FilePicker 服务化挂载点（MockFletPage 在 __init__ 中赋值为实例属性）
    - show_dialog/pop_dialog: R3 替代 V0 open/close/dialog
    """
    # MockFletPage 实例化后获取全部公开成员（含 __init__ 中赋值的实例属性如 services）
    mock_page = MockFletPage()
    mock_members = {name for name in dir(mock_page) if not name.startswith("_")}
    real_members = _real_page_public_members()

    mock_missing = _V1_REQUIRED_MEMBERS - mock_members
    assert not mock_missing, f"MockFletPage 缺少 V1 必备成员: {sorted(mock_missing)}"

    real_missing = _V1_REQUIRED_MEMBERS - real_members
    assert not real_missing, f"ft.Page 缺少 V1 必备成员（flet 可能已升级）: {sorted(real_missing)}"


def test_v1_removed_members_not_on_mock():
    """V1 已移除成员不应在 MockFletPage 上存在（R11 配方应用后的反向契约）。

    - dialog/open/close: V1 已移除，R11 已从 mock 删除
    - client_storage: V1 已移除，R10 已替换为 shared_preferences
    """
    mock_members = _mock_flet_page_public_members()
    leaked = mock_members & {"dialog", "open", "close", "client_storage"}
    assert not leaked, f"MockFletPage 仍残留 V1 已移除的成员（R10/R11 未完全应用）: {sorted(leaked)}"


def test_text_field_focused_border_color_field_exists():
    """R14 契约：ft.TextField 必须有 ``focused_border_color`` 字段（V1 字段名）。

    S13 spike 证伪：V0 ``focus_border_color`` 在 V1 已移除，改用
    ``focused_border_color``。项目 grep 实证未使用 ``focus_border_color``，
    无源码替换需求；本断言守护字段名漂移，避免 mock 或源码误用旧名。
    """
    assert hasattr(ft.TextField, "focused_border_color"), (
        "ft.TextField 缺少 focused_border_color 字段——flet 可能已升级，请检查 R14 配方"
    )
    # V0 字段名应已移除（若仍存在，说明 flet 兼容旧名，R14 迁移可保留新名）
    if hasattr(ft.TextField, "__dataclass_fields__"):
        field_names = set(ft.TextField.__dataclass_fields__.keys())
        assert "focused_border_color" in field_names, (
            f"ft.TextField dataclass 缺少 focused_border_color 字段: {sorted(field_names)[:20]}..."
        )


@pytest.mark.parametrize("attr", sorted(_mock_flet_page_public_members() - _PROJECT_EXTENSIONS))
def test_mock_flet_page_member_exists_on_real_page(attr: str):
    """参数化正向契约：逐个验证 MockFletPage 公开成员在 ft.Page 上存在。

    参数化形式便于定位首个漂移成员（优于批量断言的单点失败）。
    """
    assert hasattr(ft.Page, attr), (
        f"MockFletPage.{attr} 不在 ft.Page 上——flet 可能已升级，请检查 mock_flet.py 是否需要同步"
    )
