"""MockFletPage 契约测试：确保 mock 与真实 ft.Page 接口同步。

捕捉 Flet 0.28.3 升级时 MockFletPage 与真实 Page 的接口漂移：
- 正向契约：MockFletPage 显式实现的公开成员必须在真实 ft.Page 上存在
  （Flet 删除/重命名属性时立即失败，提醒同步 mock）
- 排除集校验：项目扩展（show_toast/dialog 由 main.py 动态挂载）不在
  ft.Page 原生接口上，需显式排除并验证排除集仍然准确
"""

import flet as ft
import pytest

from tests.unit.ui.mock_flet import MockFletPage

pytestmark = pytest.mark.unit

# 项目扩展方法/属性：由 main.py 动态挂载到 Page 实例，不在 flet 0.28.3 原生 Page 类上
# - show_toast: main.py 动态挂载（page.show_toast = show_toast）
# - dialog: main.py:106 兜底赋值（page.dialog = dialog，仅在 page.open 不存在时触发）
_PROJECT_EXTENSIONS = frozenset({"show_toast", "dialog"})


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
    项目扩展（show_toast/dialog）由 main.py 动态挂载，不在 ft.Page 原生接口上，
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


@pytest.mark.parametrize("attr", sorted(_mock_flet_page_public_members() - _PROJECT_EXTENSIONS))
def test_mock_flet_page_member_exists_on_real_page(attr: str):
    """参数化正向契约：逐个验证 MockFletPage 公开成员在 ft.Page 上存在。

    参数化形式便于定位首个漂移成员（优于批量断言的单点失败）。
    """
    assert hasattr(ft.Page, attr), (
        f"MockFletPage.{attr} 不在 ft.Page 上——flet 可能已升级，请检查 mock_flet.py 是否需要同步"
    )
