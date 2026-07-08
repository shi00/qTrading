"""V1 兼容：Control.page 只读 property 的可写覆盖。

Flet 1.0 (0.85.3) 中 ``ft.Control.page`` 改为只读 property（通过 ``parent``
链查找），``self.page = page`` 赋值会抛 ``AttributeError``。

本项目 5 个控件（``AppLayout``/``TaskCenterView``/``FailoverConfigPanel``/
``ProviderCredentialDialog``/``ResizableSplitter``）依赖 V0 行为：构造时直接
``self.page = page`` 赋值或通过 getter 获取 page，以便在未挂载到
``page.controls`` 前就能引用 page（如注册回调、读取 ``page.theme_mode`` 等）。

本 mixin 覆盖 ``page`` property：
- getter：优先返回 ``__dict__['_page_ref']``，未设过则走 V1 原生 ``parent``
  链查找；若控件未挂载（原生查找抛 ``RuntimeError``），返回 ``None``
  （兼容 ``if self.page:`` 用法）。
- setter：写入 ``__dict__['_page_ref']``。

测试环境另有 ``mock_flet._install_v1_compat_control_page_mock()`` 全局
monkey-patch ``ft.Control.page``，二者互不冲突（mixin 优先级更高）。
"""

# NOTE(lazy): 5 个控件构造期 self.page = page 赋值或通过 PageRefMixin getter 获取 page 的 V1 兼容垫片.
# ceiling: 5 个控件（AppLayout/TaskCenterView/FailoverConfigPanel/ProviderCredentialDialog/ResizableSplitter）.
# upgrade: 5 个控件全部改造为 did_mount() 阶段获取 page 后删除本 mixin 与 R16 配方.

import flet as ft


class PageRefMixin:
    """V1 兼容 mixin：使 ``ft.Control`` 子类的 ``page`` 属性可读写。

    用法：``class AppLayout(PageRefMixin, ft.Container):``
    """

    @property
    def page(self) -> ft.Page | None:  # type: ignore[override]  # [reason: 覆盖 V1 只读 page property 以支持 _page_ref 注入]
        """V1 兼容：优先返回 ``_page_ref``，回退 V1 原生 parent 链。"""
        ref = self.__dict__.get("_page_ref")
        if ref is not None:
            return ref
        try:
            return ft.Control.page.fget(self)  # type: ignore[misc]  # [reason: 调用父类 property fget 绕过子类覆盖]
        except RuntimeError:
            return None

    @page.setter
    def page(self, value: ft.Page | None) -> None:  # type: ignore[override]  # [reason: 同 getter, 写入 __dict__ 绕过只读 property]
        self.__dict__["_page_ref"] = value
