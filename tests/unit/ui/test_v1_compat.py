"""PageRefMixin (R16) 单元测试。

覆盖 ui/v1_compat.py 的 4 条分支：
1. getter：_page_ref 已设置 → 返回 _page_ref
2. getter：_page_ref 未设置，ft.Control.page.fget 成功 → 返回原生 page
3. getter：_page_ref 未设置，ft.Control.page.fget 抛 RuntimeError → 返回 None
4. setter：写入 __dict__['_page_ref']

注意：mock_flet.py 的 _install_v1_compat_control_page_mock() 会 monkey-patch
ft.Control.page，但 PageRefMixin 通过 MRO 优先级覆盖，二者不冲突。
"""

from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from ui.v1_compat import PageRefMixin

pytestmark = pytest.mark.unit


class _DummyControl(PageRefMixin, ft.Container):
    """测试用控件：PageRefMixin 优先于 ft.Container。"""

    pass


class TestPageRefMixinGetterSetRef:
    """分支 1：_page_ref 已设置 → 返回 _page_ref。"""

    def test_getter_returns_page_ref_when_set(self):
        ctrl = _DummyControl()
        mock_page = MagicMock(spec=ft.Page)
        ctrl.page = mock_page  # 走 setter

        assert ctrl.page is mock_page
        assert ctrl.__dict__["_page_ref"] is mock_page

    def test_getter_returns_none_when_ref_set_to_none(self):
        ctrl = _DummyControl()
        ctrl.page = None

        # _page_ref 为 None 时，getter 会回退到 fget；未挂载时 fget 抛 RuntimeError → 返回 None
        assert ctrl.page is None


class TestPageRefMixinGetterFallbackSuccess:
    """分支 2：_page_ref 未设置，ft.Control.page.fget 成功 → 返回原生 page。"""

    def test_getter_falls_back_to_native_fget_when_ref_unset(self):
        ctrl = _DummyControl()
        native_page = MagicMock(spec=ft.Page)

        # 模拟原生 fget 成功返回 page（控件已挂载到 page.controls）
        with patch.object(ft.Control, "page", new_callable=lambda: property(lambda self: native_page)):
            assert ctrl.page is native_page
            assert "_page_ref" not in ctrl.__dict__


class TestPageRefMixinGetterFallbackRuntimeError:
    """分支 3：_page_ref 未设置，ft.Control.page.fget 抛 RuntimeError → 返回 None。"""

    def test_getter_returns_none_when_fget_raises_runtime_error(self):
        ctrl = _DummyControl()

        # 模拟原生 fget 抛 RuntimeError（控件未挂载）
        def _raise_runtime_error(self):
            raise RuntimeError("Control is not attached to any page")

        with patch.object(ft.Control, "page", new_callable=lambda: property(_raise_runtime_error)):
            assert ctrl.page is None
            assert "_page_ref" not in ctrl.__dict__


class TestPageRefMixinSetter:
    """分支 4：setter 写入 __dict__['_page_ref']。"""

    def test_setter_writes_page_ref_to_dict(self):
        ctrl = _DummyControl()
        mock_page = MagicMock(spec=ft.Page)

        ctrl.page = mock_page

        assert ctrl.__dict__["_page_ref"] is mock_page

    def test_setter_overrides_existing_ref(self):
        ctrl = _DummyControl()
        page1 = MagicMock(spec=ft.Page)
        page2 = MagicMock(spec=ft.Page)

        ctrl.page = page1
        assert ctrl.page is page1

        ctrl.page = page2
        assert ctrl.page is page2
        assert ctrl.__dict__["_page_ref"] is page2

    def test_setter_accepts_none(self):
        ctrl = _DummyControl()
        mock_page = MagicMock(spec=ft.Page)

        ctrl.page = mock_page
        ctrl.page = None

        assert ctrl.__dict__["_page_ref"] is None


class TestPageRefMixinIntegrationWithMockFlet:
    """集成验证：PageRefMixin 与 mock_flet 全局桩共存不冲突。"""

    def test_mixin_takes_priority_over_global_mock(self):
        """PageRefMixin 通过 MRO 优先级覆盖 ft.Control.page，应优先于 mock_flet 全局桩。"""
        ctrl = _DummyControl()
        mock_page = MagicMock(spec=ft.Page)

        # 即使全局桩已 patch ft.Control.page，PageRefMixin 仍应优先
        ctrl.page = mock_page
        assert ctrl.page is mock_page
