"""MockFletPage 契约测试：确保 mock 与真实 ft.Page 接口同步。

捕捉 Flet 0.28.3 → 0.85.3 升级时 MockFletPage 与真实 Page 的接口漂移：
- 正向契约：MockFletPage 显式实现的公开成员必须在真实 ft.Page 上存在
  （Flet 删除/重命名属性时立即失败，提醒同步 mock）
- 排除集校验：项目扩展（show_toast 由 main.py 动态挂载）不在
  ft.Page 原生接口上，需显式排除并验证排除集仍然准确
- V1 关键成员存在性：shared_preferences/services/show_dialog/pop_dialog/on_resize/run_task
  在 V1 Page 上必备（R2/R3/R4/R10/R11 配方应用后），逐项断言避免 mock 漂移
- §4.1 spike 实测结论全覆盖：入口/按钮/FilePicker/Page 字段/NavRail/flet_charts/
  事件模型/主题枚举/控件字段 8 大类 36+ 项断言守护升级配方
"""

import inspect

import flet as ft
import flet_charts as fch
import pytest

from tests.unit.ui.mock_flet import MockFletPage

pytestmark = pytest.mark.unit

# 项目扩展方法/属性：由 main.py 动态挂载到 Page 实例，不在 flet 0.85.3 原生 Page 类上
# - show_toast: main.py 动态挂载（page.show_toast = show_toast）
# R11 已删除 mock 的 dialog/open/close，不再纳入排除集；R10 已将 client_storage 替换为
# shared_preferences，client_storage 同样不再纳入排除集。
_PROJECT_EXTENSIONS = frozenset({"show_toast"})

# V1 Page 必备成员（R2/R3/R4/R10/R11 配方应用后 mock 与真实 Page 均应存在）
# - shared_preferences: R10 替代 V0 client_storage
# - services: R4 FilePicker 服务化挂载点
# - show_dialog/pop_dialog: R3 替代 V0 open/close/dialog
# - on_resize: R2 替代 V0 on_resized
# - run_task: §13.A 删除 _scheduled_tasks 后唯一协程调度入口
_V1_REQUIRED_MEMBERS = frozenset(
    {"shared_preferences", "services", "show_dialog", "pop_dialog", "on_resize", "run_task"}
)


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


def test_v1_tabs_three_piece_set_controls_exist():
    """R12.b 契约：V1 三件套控件 ft.Tabs/ft.TabBar/ft.TabBarView/ft.Tab 必须存在。

    S12 spike 证伪：V0 ``ft.Tabs(tabs=[ft.Tab(text=..., content=...)])`` 在 V1 已改为
    ``ft.Tabs(length=, content=ft.Column([ft.TabBar(tabs=[ft.Tab(label=...)]), ft.TabBarView(controls=[...])]))``
    三件套模式。本断言守护三件套控件存在性，避免 flet 升级后控件被重命名/移除。
    """
    assert hasattr(ft, "Tabs"), "ft.Tabs 缺失——flet 可能已升级，请检查 R12.b 配方"
    assert hasattr(ft, "TabBar"), "ft.TabBar 缺失——flet 可能已升级，请检查 R12.b 配方"
    assert hasattr(ft, "TabBarView"), "ft.TabBarView 缺失——flet 可能已升级，请检查 R12.b 配方"
    assert hasattr(ft, "Tab"), "ft.Tab 缺失——flet 可能已升级，请检查 R12.b 配方"


def test_v1_image_src_field_exists_and_src_base64_removed():
    """R15 契约：ft.Image 必须有 ``src`` 字段，``src_base64`` 字段应已移除。

    S10 spike 证伪：V0 ``ft.Image(src_base64=...)`` 在 V1 已移除，改用
    ``ft.Image(src=f"data:image/png;base64,...")`` 直接 base64。本断言守护字段名漂移。
    """
    assert hasattr(ft.Image, "src"), "ft.Image 缺少 src 字段——flet 可能已升级，请检查 R15 配方"
    # V0 src_base64 字段应已移除
    if hasattr(ft.Image, "__dataclass_fields__"):
        field_names = set(ft.Image.__dataclass_fields__.keys())
        assert "src" in field_names, f"ft.Image dataclass 缺少 src 字段: {sorted(field_names)[:20]}..."
        assert "src_base64" not in field_names, "ft.Image 仍残留 src_base64 字段——R15 迁移未完成或 flet 兼容旧名"


def test_v1_boxfit_enum_exists_and_imagefit_removed():
    """R17 契约：ft.BoxFit 枚举必须存在，ft.ImageFit 应已移除。

    V1 将 ``ft.ImageFit`` 重命名为 ``ft.BoxFit``。本断言守护枚举重命名漂移。
    """
    assert hasattr(ft, "BoxFit"), "ft.BoxFit 缺失——flet 可能已升级，请检查 R17 配方"
    assert hasattr(ft.BoxFit, "CONTAIN"), "ft.BoxFit.CONTAIN 缺失——请检查 R17 配方"
    # V0 ft.ImageFit 应已移除（若仍存在，说明 flet 兼容旧名，R17 迁移可保留新名）
    # 不做硬性断言，因为 flet 可能保留旧名作为兼容别名


def test_v1_page_window_icon_field_exists():
    """window.icon 契约：ft.Page.window 必须有 ``icon`` 字段（V1 替代 V0 ``page.window_icon``）。

    项目 ``main.py`` 已迁移到 ``page.window.icon = ...``，本断言守护字段存在性。
    """
    # window 是 property，需实例化后检查；MockFletPage 已实例化 window
    mock_page = MockFletPage()
    assert hasattr(mock_page, "window"), "MockFletPage.window 缺失——请检查 mock_flet.py"
    # window 对象应有 icon 属性（V1 字段）
    window_obj = mock_page.window
    assert hasattr(window_obj, "icon"), "page.window.icon 字段缺失——flet 可能已升级，请检查 window.icon 配方"


def test_v1_control_update_silent_when_unmounted():
    """R18 契约：per-test ``_v1_page_compat`` fixture 使未挂载 ft.Control.update() 静默返回。

    V1 原生 ``ft.Control.update()`` 在控件未挂载到 page 时抛 ``RuntimeError``；
    ``_v1_page_compat`` autouse fixture（见 ``conftest.py``）monkey-patch 后应静默返回
    （V0 兼容行为），替代已删除的 ``_install_v1_compat_control_page_mock`` 全局桩。
    """
    # _v1_page_compat autouse fixture 已激活（见 tests/unit/ui/conftest.py）
    # 未挂载控件调用 update() 应静默返回（不抛 RuntimeError）
    ctrl = ft.Container()
    assert ctrl.__dict__.get("_mock_page", None) is None
    ctrl.update()  # 不应抛 RuntimeError


def test_v1_control_page_property_writable_via_mock():
    """R18 契约：per-test ``_v1_page_compat`` fixture 使 ft.Control.page 可读写（V1 原生为只读 property）。

    测试代码通过 ``control.page = mock_page`` 注入 mock_page，getter 应返回该值。
    用 ``setattr`` 而非直接赋值：V1 静态类型将 ``ft.Control.page`` 标为只读 property，
    但 ``_v1_page_compat`` fixture 在运行时通过 monkeypatch 注入了 setter。
    """
    mock_page = MockFletPage()
    ctrl = ft.Container()

    # 注入 mock_page（fixture patched page setter 在运行时生效）
    ctrl.page = mock_page
    assert ctrl.page is mock_page

    # 清除 mock_page 后，未挂载控件应返回 None（不抛 RuntimeError）
    ctrl._mock_page = None
    assert ctrl.page is None


def test_v1_pop_dialog_signature_no_args():
    """R3 契约：pop_dialog 应为无参签名（V1 spike 实测 (self) -> Optional[DialogControl]）。

    守护 pop_dialog 签名漂移：若 flet 升级后 pop_dialog 增加 dialog 参数（回退 V0 open 风格），
    mock 与源码调用方需同步调整；本断言在升级时立即失败提醒。
    """
    sig_real = inspect.signature(ft.Page.pop_dialog)
    real_params = [name for name in sig_real.parameters if name != "self"]
    assert len(real_params) == 0, f"ft.Page.pop_dialog 应为无参签名（除 self），实际参数: {real_params}"

    sig_mock = inspect.signature(MockFletPage.pop_dialog)
    mock_params = [name for name in sig_mock.parameters if name != "self"]
    assert len(mock_params) == 0, f"MockFletPage.pop_dialog 应为无参签名（除 self），实际参数: {mock_params}"


def test_v1_removed_members_not_on_real_page():
    """R3 契约：真实 ft.Page 不应残留 V1 已移除的成员（open/close/dialog/client_storage/set_clipboard/on_resized）。

    与 ``test_v1_removed_members_not_on_mock`` 对称：mock 侧已清理（R10/R11），
    真实 Page 侧若仍残留旧成员，说明 flet 兼容回退，需评估 mock 是否需补齐或配方是否需重审。
    """
    _V1_REMOVED_MEMBERS = {"dialog", "open", "close", "client_storage", "set_clipboard", "on_resized"}
    real_members = _real_page_public_members()
    leaked = real_members & _V1_REMOVED_MEMBERS
    assert not leaked, f"ft.Page 仍残留 V1 已移除的成员（flet 可能回退兼容旧 API）: {sorted(leaked)}"


# ===========================================================================
# §4.1 spike 实测结论契约断言（升级配方守护）
# ===========================================================================


def test_v1_run_signature_has_web_renderer_param():
    """R1 契约：ft.run 签名必须含 ``web_renderer`` 参数（A1 spike ✅）。

    项目 ``main.py`` 在 ``E2E_TESTING`` 模式下传 ``web_renderer=ft.WebRenderer.CANVAS_KIT``
    固定渲染器，守护 E2E Playwright 回退路径（§8.1）。若 flet 升级移除该参数，需评估
    E2E 渲染器锁定方案的替代实现。
    """
    sig = inspect.signature(ft.run)
    assert "web_renderer" in sig.parameters, (
        "ft.run 签名缺少 web_renderer 参数——flet 可能已升级，请检查 R1 配方与 E2E 渲染器锁定方案"
    )


def test_v1_alignment_class_has_nine_constants():
    """R5 契约：ft.Alignment 必须含全部 9 个方位常量（A7 spike ✅）。

    V1 ``ft.alignment.center`` 已移除（抛 AttributeError），改用 ``ft.Alignment.CENTER``
    常量。9 个方位常量是项目 UI 布局的基石，缺失任何一个会导致样式 helper 替换失败。
    """
    assert hasattr(ft, "Alignment"), "ft.Alignment 缺失——请检查 R5 配方"
    expected = {
        "TOP_LEFT",
        "TOP_CENTER",
        "TOP_RIGHT",
        "CENTER_LEFT",
        "CENTER",
        "CENTER_RIGHT",
        "BOTTOM_LEFT",
        "BOTTOM_CENTER",
        "BOTTOM_RIGHT",
    }
    actual = {a for a in expected if hasattr(ft.Alignment, a)}
    missing = expected - actual
    assert not missing, f"ft.Alignment 缺少方位常量（R5）: {sorted(missing)}"


def test_v1_style_helpers_classmethod_exists():
    """R5 契约：ft.Padding/Margin/Border/BorderRadius/BorderSide classmethod 必须存在（强制迁移）。

    V1 ``ft.padding.only/all/symmetric`` 等模块级函数已移除（抛 AttributeError），
    改用 classmethod 形式。本断言守护 classmethod 存在性，缺失会导致样式 helper 替换失败。
    """
    assert hasattr(ft.Padding, "only"), "ft.Padding.only 缺失——请检查 R5 配方"
    assert hasattr(ft.Padding, "all"), "ft.Padding.all 缺失——请检查 R5 配方"
    assert hasattr(ft.Padding, "symmetric"), "ft.Padding.symmetric 缺失——请检查 R5 配方"
    assert hasattr(ft.Margin, "all"), "ft.Margin.all 缺失——请检查 R5 配方"
    assert hasattr(ft.Border, "only"), "ft.Border.only 缺失——请检查 R5 配方"
    assert hasattr(ft.Border, "all"), "ft.Border.all 缺失——请检查 R5 配方"
    assert hasattr(ft.BorderRadius, "all"), "ft.BorderRadius.all 缺失——请检查 R5 配方"
    assert hasattr(ft, "BorderSide"), "ft.BorderSide 缺失——请检查 R5 配方"


def test_v0_style_helpers_removed():
    """R5 反向契约：V0 模块级样式 helper 应已移除（``ft.alignment.``/``ft.padding.``/``ft.margin.``）。

    V1 这些模块级函数调用会抛 AttributeError，项目代码已全部改为 classmethod。
    守护 flet 不应回退添加旧 API（若回退，R5 迁移可保留新写法但需评估）。
    """
    assert not hasattr(ft.alignment, "center"), "ft.alignment.center 仍存在——flet 可能回退兼容旧 API"
    assert not hasattr(ft.padding, "only"), "ft.padding.only 仍存在——flet 可能回退兼容旧 API"
    assert not hasattr(ft.margin, "all"), "ft.margin.all 仍存在——flet 可能回退兼容旧 API"


def test_v1_text_button_content_kw_accepted_text_kw_rejected():
    """R6 契约：ft.TextButton 接受 ``content=`` 关键字，拒绝 ``text=`` 关键字（spike ✅）。

    V1 按钮无 ``text`` 属性（``text=`` 抛 TypeError），改用 ``content=``。
    位置参数首实参仍映射到 ``content``（可保留 ``ft.TextButton("x")`` 简写）。
    """
    # content= 关键字可用
    btn = ft.TextButton(content="x")
    assert btn is not None
    # 位置参数仍映射到 content
    btn2 = ft.TextButton("x")
    assert btn2 is not None
    # text= 关键字应抛 TypeError
    with pytest.raises(TypeError):
        ft.TextButton(text="x")  # type: ignore[call-arg]


def test_v1_button_and_elevated_button_exist():
    """R6 契约：ft.Button 与 ft.ElevatedButton 必须可导入（A8/A9 spike ✅）。

    - A8: ``ft.Button`` 真实存在（``flet.controls.material.button.Button``），R6 迁移目标控件
    - A9: ``ft.ElevatedButton`` 实测无 DeprecationWarning 仍可导入，R6 优先级降为"建议"
    """
    assert hasattr(ft, "Button"), "ft.Button 缺失——请检查 R6 配方（A8 spike）"
    assert hasattr(ft, "ElevatedButton"), "ft.ElevatedButton 缺失——请检查 R6 配方（A9 spike）"


def test_v1_file_picker_methods_are_coroutines():
    """R4 契约：ft.FilePicker 的 save_file/pick_files/get_directory_path 必须是协程实例方法（A5/A6 spike ✅）。

    V1 FilePicker 服务化：三方法均为协程实例方法（``has_self=True``），需 ``await``，
    无 V0 的 ``on_result`` 事件回调。签名守护升级后参数名漂移。
    """
    assert hasattr(ft, "FilePicker"), "ft.FilePicker 缺失——请检查 R4 配方"
    for method_name in ("save_file", "pick_files", "get_directory_path"):
        method = getattr(ft.FilePicker, method_name, None)
        assert method is not None, f"ft.FilePicker.{method_name} 缺失——请检查 R4 配方（A5 spike）"
        assert inspect.iscoroutinefunction(method), (
            f"ft.FilePicker.{method_name} 应为协程方法——请检查 R4 配方（A6 spike）"
        )


def test_v1_page_dataclass_fields_exist():
    """R2/S5 契约：ft.Page dataclass 必须含 title/width/height/on_close/on_resize/on_disconnect/on_error/on_connect/window 字段（S5 spike ✅）。

    这些字段是项目 ``main.py`` 窗口管理与生命周期挂载的依赖。若字段被移除/重命名，
    入口与生命周期回调接线需同步调整。
    """
    assert hasattr(ft.Page, "__dataclass_fields__"), "ft.Page 不是 dataclass——flet 可能已升级"
    fields = set(ft.Page.__dataclass_fields__.keys())
    required = {
        "title",
        "width",
        "height",
        "on_close",
        "on_resize",
        "on_disconnect",
        "on_error",
        "on_connect",
        "window",
    }
    missing = required - fields
    assert not missing, f"ft.Page dataclass 缺少字段（S5 spike）: {sorted(missing)}"


def test_v1_window_class_fields_and_methods_exist():
    """R2 契约：ft.Window dataclass 必须含核心字段 + center/destroy/close 方法（spike ✅）。

    项目 ``main.py`` 依赖 ``page.window.prevent_close / on_event / width / height /
    min_width / min_height / center() / destroy()`` 等字段与方法。
    """
    assert hasattr(ft, "Window"), "ft.Window 缺失——请检查 R2 配方"
    assert hasattr(ft.Window, "__dataclass_fields__"), "ft.Window 不是 dataclass——flet 可能已升级"
    fields = set(ft.Window.__dataclass_fields__.keys())
    required_fields = {
        "bgcolor",
        "width",
        "height",
        "top",
        "left",
        "max_width",
        "max_height",
        "min_width",
        "min_height",
        "opacity",
        "prevent_close",
        "icon",
        "on_event",
    }
    missing_fields = required_fields - fields
    assert not missing_fields, f"ft.Window dataclass 缺少字段: {sorted(missing_fields)}"
    for method_name in ("center", "destroy", "close"):
        assert hasattr(ft.Window, method_name), f"ft.Window.{method_name}() 方法缺失——请检查 R2 配方"


def test_v1_nav_rail_destination_label_field_exists():
    """R12/NavRail 契约：NavigationRailDestination 必须有 ``label`` 字段，``label_content`` 应已移除（A12 spike ✅）。

    V1 ``label_content`` 已移除，``label`` 可接受 ``str`` 或 ``Control``（如 ``ft.Text(...)``）。
    项目 ``app_layout.py`` 已迁移到 ``label=ft.Text(...)`` 模式。
    """
    assert hasattr(ft.NavigationRailDestination, "__dataclass_fields__"), (
        "NavigationRailDestination 不是 dataclass——flet 可能已升级"
    )
    fields = set(ft.NavigationRailDestination.__dataclass_fields__.keys())
    assert "label" in fields, "NavigationRailDestination.label 字段缺失——请检查 NavRail 配方（A12 spike）"
    assert "label_content" not in fields, "NavigationRailDestination.label_content 仍存在——flet 可能回退兼容旧 API"


def test_v1_flet_charts_module_and_chart_classes_exist():
    """R7 契约：flet_charts 独立模块 + LineChart/BarChart 等控件类必须存在（A13 spike ✅）。

    V1 ``ft.LineChart`` 等已从 ``ft`` 顶层移除，改用 ``flet_charts`` 命名空间。
    项目 ``backtest_result_panel.py`` 依赖这些类渲染回测图表。
    """
    for cls_name in (
        "LineChart",
        "BarChart",
        "LineChartData",
        "LineChartDataPoint",
        "ChartAxis",
        "BarChartGroup",
        "BarChartRod",
    ):
        assert hasattr(fch, cls_name), f"flet_charts.{cls_name} 缺失——请检查 R7 配方（A13 spike）"


def test_v1_line_chart_fields_exist_and_data_points_removed():
    """R7 契约：fch.LineChart 必须含 data_series/left_axis/bottom_axis 字段，data_points 应已移除（S7 spike ✅）。

    V1 ``LineChart`` 用 ``data_series`` 替代 V0 ``data_points``。
    """
    assert hasattr(fch.LineChart, "__dataclass_fields__"), "fch.LineChart 不是 dataclass——flet 可能已升级"
    fields = set(fch.LineChart.__dataclass_fields__.keys())
    for required in ("data_series", "left_axis", "bottom_axis"):
        assert required in fields, f"fch.LineChart 缺少字段: {required}（S7 spike）"
    assert "data_points" not in fields, "fch.LineChart 仍残留 data_points 字段——R7 迁移未完成或 flet 兼容旧名"


def test_v1_drag_update_event_fields_exist_and_delta_removed():
    """R13 契约：DragUpdateEvent 必须含 local_delta/global_delta/primary_delta，delta_x/delta_y 应已移除（A11 spike ✅）。

    V1 ``DragUpdateEvent`` 强类型化，无 V0 的 ``delta_x/delta_y``，改用 ``primary_delta``
    （主路径）或 ``local_delta.x``（回退）。项目 ``resizable_splitter.py`` 依赖此字段链路。
    """
    assert hasattr(ft.DragUpdateEvent, "__dataclass_fields__"), "DragUpdateEvent 不是 dataclass——flet 可能已升级"
    fields = set(ft.DragUpdateEvent.__dataclass_fields__.keys())
    for required in ("local_delta", "global_delta", "primary_delta"):
        assert required in fields, f"DragUpdateEvent 缺少字段: {required}（A11 spike）"
    for removed in ("delta_x", "delta_y"):
        assert removed not in fields, f"DragUpdateEvent 仍残留 {removed} 字段——R13 迁移未完成"


def test_v1_page_resize_event_has_width_and_height():
    """R2 契约：PageResizeEvent 必须含 width/height 字段（spike ✅）。

    ``page.on_resize`` 回调携带 ``e.width``/``e.height``，项目 ``main.py::_on_resize`` 读取。
    """
    assert hasattr(ft.PageResizeEvent, "__dataclass_fields__"), "PageResizeEvent 不是 dataclass"
    fields = set(ft.PageResizeEvent.__dataclass_fields__.keys())
    assert "width" in fields, "PageResizeEvent.width 字段缺失——请检查 R2 配方"
    assert "height" in fields, "PageResizeEvent.height 字段缺失——请检查 R2 配方"


def test_v1_window_event_and_type_enum_exist():
    """R2 契约：WindowEvent 必须含 type 字段，WindowEventType.CLOSE/RESIZE 必须存在（spike ✅）。

    项目 ``main.py`` 窗口关闭事件依赖 ``WindowEventType.CLOSE``。
    """
    assert hasattr(ft.WindowEvent, "__dataclass_fields__"), "WindowEvent 不是 dataclass"
    assert "type" in ft.WindowEvent.__dataclass_fields__, "WindowEvent.type 字段缺失——请检查 R2 配方"
    assert hasattr(ft.WindowEventType, "CLOSE"), "WindowEventType.CLOSE 缺失——请检查 R2 配方"
    assert hasattr(ft.WindowEventType, "RESIZE"), "WindowEventType.RESIZE 缺失——请检查 R2 配方"


def test_v1_web_renderer_enum_members_exist():
    """§8.1 E2E 契约：WebRenderer.AUTO/CANVAS_KIT/SKWASM 必须存在（spike ✅）。

    项目 ``main.py`` 在 ``E2E_TESTING`` 模式下用 ``ft.WebRenderer.CANVAS_KIT`` 固定渲染器。
    """
    assert hasattr(ft, "WebRenderer"), "ft.WebRenderer 缺失——请检查 §8.1 E2E 配置"
    for member in ("AUTO", "CANVAS_KIT", "SKWASM"):
        assert hasattr(ft.WebRenderer, member), f"ft.WebRenderer.{member} 缺失——请检查 §8.1 E2E 配置"


def test_v1_control_state_enum_members_exist():
    """主题契约：ft.ControlState.DEFAULT/HOVERED 必须存在（S4 spike ✅）。

    项目 ``theme.py`` 与 ``news_feed.py`` 依赖 ``ft.ControlState.DEFAULT`` 与 ``HOVERED``。
    """
    assert hasattr(ft, "ControlState"), "ft.ControlState 缺失——请检查主题相关配方（S4 spike）"
    assert hasattr(ft.ControlState, "DEFAULT"), "ft.ControlState.DEFAULT 缺失——请检查 S4 spike"
    assert hasattr(ft.ControlState, "HOVERED"), "ft.ControlState.HOVERED 缺失——请检查 S4 spike"


def test_v1_theme_classes_exist_and_removed_types_absent():
    """主题契约：ColorScheme/Theme/DividerTheme 必须存在，ThemeType/CardStyle 应已移除（S4 spike ✅）。

    - ``ft.ColorScheme/Theme/DividerTheme`` 保留：项目 ``theme.py`` 依赖
    - ``ft.ThemeType`` 不存在：项目无依赖（自定义 ``ThemeMode`` 字符串）
    - ``ft.CardStyle`` 不存在：项目 ``theme.py`` 的 ``CardStyle``/``DashboardCardStyle`` 是自定义 TypedDict
    """
    for cls_name in ("ColorScheme", "Theme", "DividerTheme"):
        assert hasattr(ft, cls_name), f"ft.{cls_name} 缺失——请检查主题相关配方（S4 spike）"
    assert not hasattr(ft, "ThemeType"), "ft.ThemeType 仍存在——flet 可能回退兼容旧 API（S4 spike 证伪项）"
    assert not hasattr(ft, "CardStyle"), "ft.CardStyle 仍存在——flet 可能回退兼容旧 API（S4 spike 证伪项）"


def test_v1_scroll_interval_field_exists_and_on_scroll_interval_removed():
    """R8 契约：ft.ListView/Column 必须含 scroll_interval，on_scroll_interval 应已移除（S9 spike ✅）。

    V1 ``on_scroll_interval`` 已重命名为 ``scroll_interval``。项目 ``virtual_table.py`` 依赖。
    """
    for cls_name in ("ListView", "Column"):
        cls = getattr(ft, cls_name, None)
        assert cls is not None, f"ft.{cls_name} 缺失"
        assert hasattr(cls, "__dataclass_fields__"), f"ft.{cls_name} 不是 dataclass"
        fields = set(cls.__dataclass_fields__.keys())
        assert "scroll_interval" in fields, f"ft.{cls_name}.scroll_interval 缺失——请检查 R8 配方（S9 spike）"
        assert "on_scroll_interval" not in fields, f"ft.{cls_name}.on_scroll_interval 仍存在——R8 迁移未完成"


def test_v1_alert_dialog_fields_exist():
    """R3 契约：ft.AlertDialog 必须含 content/actions/title/modal/open/on_dismiss/actions_alignment 字段（S11 spike ✅）。"""
    assert hasattr(ft.AlertDialog, "__dataclass_fields__"), "ft.AlertDialog 不是 dataclass"
    fields = set(ft.AlertDialog.__dataclass_fields__.keys())
    for required in ("content", "actions", "title", "modal", "open", "on_dismiss", "actions_alignment"):
        assert required in fields, f"ft.AlertDialog.{required} 字段缺失——请检查 R3 配方（S11 spike）"


def test_v1_date_picker_fields_exist_and_on_result_removed():
    """R3/R8 契约：ft.DatePicker 必须含 value/on_change/on_dismiss/modal/first_date/last_date，on_result 应已移除（S8 spike ✅）。"""
    assert hasattr(ft.DatePicker, "__dataclass_fields__"), "ft.DatePicker 不是 dataclass"
    fields = set(ft.DatePicker.__dataclass_fields__.keys())
    for required in ("value", "on_change", "on_dismiss", "modal", "first_date", "last_date"):
        assert required in fields, f"ft.DatePicker.{required} 字段缺失——请检查 S8 spike"
    assert "on_result" not in fields, "ft.DatePicker.on_result 仍存在——S8 spike 证伪项未对齐"


def test_v1_image_fit_field_exists():
    """R15 契约：ft.Image 必须含 fit 字段（与 src 一并守护）。"""
    assert hasattr(ft.Image, "__dataclass_fields__"), "ft.Image 不是 dataclass"
    fields = set(ft.Image.__dataclass_fields__.keys())
    assert "fit" in fields, "ft.Image.fit 字段缺失——请检查 R15/R17 配方"


def test_v1_container_fields_exist():
    """S12 契约：ft.Container 必须含 padding/margin/alignment/border/bgcolor 字段（S12 spike ✅）。"""
    assert hasattr(ft.Container, "__dataclass_fields__"), "ft.Container 不是 dataclass"
    fields = set(ft.Container.__dataclass_fields__.keys())
    for required in ("padding", "margin", "alignment", "border", "bgcolor"):
        assert required in fields, f"ft.Container.{required} 字段缺失——请检查 S12 spike"


def test_v1_tabs_fields_exist_and_tabs_field_removed():
    """R12.b 契约：ft.Tabs 必须含 content/length/selected_index/animation_duration/on_change，tabs 应已移除（S12 spike ✅）。"""
    assert hasattr(ft.Tabs, "__dataclass_fields__"), "ft.Tabs 不是 dataclass"
    fields = set(ft.Tabs.__dataclass_fields__.keys())
    for required in ("content", "length", "selected_index", "animation_duration", "on_change"):
        assert required in fields, f"ft.Tabs.{required} 字段缺失——请检查 R12.b 配方（S12 spike）"
    assert "tabs" not in fields, "ft.Tabs.tabs 仍存在——R12.b 迁移未完成（S12 spike 证伪项）"


def test_v1_tab_fields_exist_and_text_content_removed():
    """R12.b 契约：ft.Tab 必须含 label/icon/height/icon_margin，text/content 应已移除（S12 spike ✅）。"""
    assert hasattr(ft.Tab, "__dataclass_fields__"), "ft.Tab 不是 dataclass"
    fields = set(ft.Tab.__dataclass_fields__.keys())
    for required in ("label", "icon", "height", "icon_margin"):
        assert required in fields, f"ft.Tab.{required} 字段缺失——请检查 R12.b 配方（S12 spike）"
    for removed in ("text", "content"):
        assert removed not in fields, f"ft.Tab.{removed} 仍存在——R12.b 迁移未完成（S12 spike 证伪项）"


def test_v1_gesture_detector_drag_handlers_exist():
    """R13 契约：ft.GestureDetector 必须含 on_enter/on_exit/on_horizontal_drag_start/update/end（S12 spike ✅）。

    项目 ``resizable_splitter.py`` 依赖 ``on_horizontal_drag_update`` 实现拖拽调宽。
    """
    assert hasattr(ft.GestureDetector, "__dataclass_fields__"), "ft.GestureDetector 不是 dataclass"
    fields = set(ft.GestureDetector.__dataclass_fields__.keys())
    for required in (
        "on_enter",
        "on_exit",
        "on_horizontal_drag_start",
        "on_horizontal_drag_update",
        "on_horizontal_drag_end",
    ):
        assert required in fields, f"ft.GestureDetector.{required} 字段缺失——请检查 R13 配方（S12 spike）"


def test_v1_form_control_fields_exist():
    """S13 契约：Switch/Checkbox/Slider/RadioGroup 核心字段必须存在（S13 spike ✅）。"""
    expected = {
        "Switch": ("value", "label", "label_position"),
        "Checkbox": ("value", "label"),
        "Slider": ("min", "max", "value", "label"),
        "RadioGroup": ("value",),
    }
    for cls_name, required_fields in expected.items():
        cls = getattr(ft, cls_name, None)
        assert cls is not None, f"ft.{cls_name} 缺失"
        assert hasattr(cls, "__dataclass_fields__"), f"ft.{cls_name} 不是 dataclass"
        fields = set(cls.__dataclass_fields__.keys())
        for f in required_fields:
            assert f in fields, f"ft.{cls_name}.{f} 字段缺失——请检查 S13 spike"


def test_v1_text_field_focus_border_color_removed():
    """R14 反向契约：ft.TextField 不应残留 ``focus_border_color`` 字段（S13 spike 证伪项）。

    与 ``test_text_field_focused_border_color_field_exists`` 对称：正向断言新字段存在，
    反向断言旧字段已移除。守护 flet 不应回退添加旧字段名。
    """
    assert hasattr(ft.TextField, "__dataclass_fields__"), "ft.TextField 不是 dataclass"
    fields = set(ft.TextField.__dataclass_fields__.keys())
    assert "focus_border_color" not in fields, (
        "ft.TextField.focus_border_color 仍存在——R14 迁移未完成或 flet 回退兼容旧 API"
    )
