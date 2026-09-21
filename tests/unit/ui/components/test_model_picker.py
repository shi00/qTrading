"""ModelPicker 组件运行时测试（AI-01 §3.1c：搜索+分组+回记模型选择组件）。

覆盖:
1. 模块级纯函数: _build_context_suffix / _icon_image / _build_row_content /
   _build_select_row / _build_group_header / _build_browse_panel / _build_search_panel
2. 组件渲染分支: 默认空态(结果面板隐藏) / 已选回显「供应商 / 模型」/ 搜索模式(有/无命中) /
   searching 加载态 / 浏览模式(最近使用 + 分组折叠/展开) / text_field_width
3. 事件处理器: 搜索框 on_change → vm.update_model_query; on_focus 已选态聚焦清关键字;
   行 on_click → vm.select_model + on_select 回调; 分组头 on_click → vm.toggle_group
4. 挂载 effect: page.run_task(vm.ensure_catalog_loaded) 预载目录;
   ft.context.page 抛 RuntimeError（无渲染器上下文）静默降级

MVVM 声明式组件测试范式：外部 VM mock(fake) + component_renderer 驱动渲染，
与 test_llm_config_panel.py 同级（后者已覆盖基础契约，此处聚焦渲染行为）。
"""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components import model_picker as model_picker_module
from ui.components.model_picker import ModelPicker
from ui.theme import AppColors
from ui.viewmodels.model_picker_view_model import (
    ModelPickerState,
    ModelRow,
    ProviderGroup,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_picker_i18n(monkeypatch) -> MagicMock:
    """ModelPicker 模块级 I18n mock：get 返回 key 或 default，不依赖真实 locale 文案。"""
    m = MagicMock()
    m.get.side_effect = lambda key, **kw: kw.get("default", key) if "default" in kw else key
    monkeypatch.setattr(model_picker_module, "I18n", m)
    return m


class _FakeModelPickerVM:
    """模拟 ModelPickerViewModel，满足 use_viewmodel(vm=) 外部 VM 模式契约。

    state 字段可外部注入，command 方法为 MagicMock 便于断言。
    """

    def __init__(self, state: ModelPickerState | None = None) -> None:
        self._state = state if state is not None else ModelPickerState()
        self._subscribers: list[Any] = []
        self.update_model_query = MagicMock()
        self.select_model = MagicMock()
        self.toggle_group = MagicMock()
        self.ensure_catalog_loaded = MagicMock()

    @property
    def state(self) -> ModelPickerState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _row(
    provider_id: str = "deepseek",
    provider_name: str = "DeepSeek",
    model_id: str = "deepseek-chat",
    context: int = 65536,
) -> ModelRow:
    return ModelRow(
        provider_id=provider_id,
        provider_name=provider_name,
        icon=f"{provider_id}.png",
        model_id=model_id,
        context=context,
    )


def _group(
    provider_id: str = "deepseek",
    provider_name: str = "DeepSeek",
    model_ids: tuple[str, ...] = ("deepseek-chat", "deepseek-v3"),
) -> ProviderGroup:
    models = tuple(_row(provider_id=provider_id, provider_name=provider_name, model_id=m) for m in model_ids)
    return ProviderGroup(
        provider_id=provider_id,
        provider_name=provider_name,
        icon=f"{provider_id}.png",
        model_count=len(models),
        models=models,
    )


def _render(
    state: ModelPickerState | None = None,
    *,
    on_select: Any = None,
    text_field_width: float | None = None,
) -> tuple[_FakeModelPickerVM, FakePage, Any, Any]:
    """渲染 ModelPicker，返回 (vm, page, result, component)。

    挂载 effect 经 FakePage.run_task（动态注入 MagicMock）触发 vm.ensure_catalog_loaded。
    """
    vm = _FakeModelPickerVM(state=state)
    page = FakePage()
    page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task 属性, 测试动态注入 MagicMock
    component = make_component(ModelPicker, vm=vm, on_select=on_select, text_field_width=text_field_width)
    run_mount_effects(component, page=page)
    result = render_once(component)
    return vm, page, result, component


def _page_run_task(page: FakePage) -> MagicMock:
    """获取 page.run_task mock（动态注入, pyright safe）。

    FakePage 类不定义 run_task 属性, _render 通过实例属性动态注入 MagicMock。
    用 cast(Any) 绕过 reportAttributeAccessIssue（ruff B009 禁止 getattr 常量属性）。
    """
    return cast(MagicMock, cast(Any, page).run_task)


def _walk_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树（含 controls/content；兼容传入控件列表）。"""
    if isinstance(root, list):
        result: list[Any] = []
        for item in root:
            result.extend(_walk_controls(item))
        return result
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_walk_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_walk_controls(content))
    return result


def _text_values(root: Any) -> list[str]:
    return [str(t.value) for t in _walk_controls(root) if isinstance(t, ft.Text)]


def _find_select_rows(root: Any) -> list[ft.Container]:
    """查找可点击的模型/供应商选择行（bgcolor=PRIMARY_DARK 的 Container）。"""
    return [
        c
        for c in _walk_controls(root)
        if isinstance(c, ft.Container) and getattr(c, "bgcolor", None) == AppColors.PRIMARY_DARK
    ]


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler（pyright safe，处理报告签名差异）。"""
    handler(*args)


# ============================================================================
# 模块级纯函数
# ============================================================================


class TestBuildContextSuffix:
    """_build_context_suffix: context 后缀（R21 context<=0 隐藏）。"""

    def test_positive_context_formatted_with_thousands_separator(self) -> None:
        assert model_picker_module._build_context_suffix(65536) == " (65,536)"

    def test_zero_context_hidden(self) -> None:
        assert model_picker_module._build_context_suffix(0) == ""

    def test_negative_context_hidden(self) -> None:
        assert model_picker_module._build_context_suffix(-1) == ""


class TestIconImage:
    """_icon_image: 供应商 icon 图片控件。"""

    def test_returns_image_with_path_and_fixed_size(self) -> None:
        img = model_picker_module._icon_image("deepseek.png")
        assert isinstance(img, ft.Image)
        assert img.src == "deepseek.png"
        assert img.width == model_picker_module._ICON_IMG_WIDTH
        assert img.height == model_picker_module._ICON_IMG_HEIGHT


class TestBuildRowContent:
    """_build_row_content: 搜索行/浏览行内容（highlight_model 切换主次文本）。"""

    def test_highlight_model_makes_model_id_primary(self) -> None:
        ctrls = model_picker_module._build_row_content(_row(), highlight_model=True)
        texts = [t.value for t in _walk_controls(ctrls) if isinstance(t, ft.Text)]
        assert texts[0] == "deepseek-chat"
        assert texts[1] == "DeepSeek (65,536)"

    def test_highlight_provider_makes_provider_primary(self) -> None:
        ctrls = model_picker_module._build_row_content(_row(), highlight_model=False)
        texts = [t.value for t in _walk_controls(ctrls) if isinstance(t, ft.Text)]
        assert texts[0] == "DeepSeek"
        assert texts[1] == "deepseek-chat (65,536)"


class TestBuildSelectRow:
    """_build_select_row: 可点击的模型/供应商选择行。"""

    def test_returns_clickable_container(self) -> None:
        c = model_picker_module._build_select_row(_row(), MagicMock(), highlight_model=True)
        assert isinstance(c, ft.Container)
        assert callable(c.on_click)

    def test_on_click_selects_row(self) -> None:
        on_select = MagicMock()
        c = model_picker_module._build_select_row(_row(), on_select, highlight_model=True)
        _invoke(c.on_click, MagicMock())
        on_select.assert_called_once_with(_row())


class TestBuildGroupHeader:
    """_build_group_header: 供应商分组折叠头。"""

    def test_expanded_uses_expand_more_icon(self) -> None:
        header = model_picker_module._build_group_header(_group(), expanded=True, on_toggle=MagicMock())
        icons = [c.icon for c in _walk_controls(header) if isinstance(c, ft.Icon)]
        assert ft.Icons.EXPAND_MORE in icons

    def test_collapsed_uses_expand_less_icon(self) -> None:
        header = model_picker_module._build_group_header(_group(), expanded=False, on_toggle=MagicMock())
        icons = [c.icon for c in _walk_controls(header) if isinstance(c, ft.Icon)]
        assert ft.Icons.EXPAND_LESS in icons

    def test_on_click_toggles_group(self) -> None:
        on_toggle = MagicMock()
        header = model_picker_module._build_group_header(_group(), expanded=False, on_toggle=on_toggle)
        _invoke(header.on_click, MagicMock())
        on_toggle.assert_called_once_with("deepseek")


class TestBuildBrowsePanel:
    """_build_browse_panel: 浏览模式（最近使用置顶 + 供应商分组折叠）。"""

    def test_recent_section_rendered(self) -> None:
        state = ModelPickerState(recent=(_row(),), groups=())
        ctrls = model_picker_module._build_browse_panel(state, MagicMock(), MagicMock())
        texts = [t.value for t in ctrls if isinstance(t, ft.Text)]
        assert "model_picker_section_recent" in texts
        assert len(_find_select_rows(ctrls)) == 1

    def test_collapsed_groups_render_only_headers(self) -> None:
        state = ModelPickerState(
            groups=(_group(), _group(provider_id="openai", provider_name="OpenAI")), expanded=frozenset()
        )
        ctrls = model_picker_module._build_browse_panel(state, MagicMock(), MagicMock())
        headers = [c for c in ctrls if isinstance(c, ft.Container) and c.bgcolor is None and c.on_click is not None]
        assert len(headers) == 2
        assert _find_select_rows(ctrls) == []

    def test_expanded_group_renders_model_rows(self) -> None:
        state = ModelPickerState(groups=(_group(),), expanded=frozenset({"deepseek"}))
        ctrls = model_picker_module._build_browse_panel(state, MagicMock(), MagicMock())
        assert len(_find_select_rows(ctrls)) == 2  # 展开的 deepseek 两模型行

    def test_empty_state_shows_browse_hint(self) -> None:
        ctrls = model_picker_module._build_browse_panel(ModelPickerState(), MagicMock(), MagicMock())
        texts = [t.value for t in ctrls if isinstance(t, ft.Text)]
        assert "model_picker_browse_empty" in texts


class TestBuildSearchPanel:
    """_build_search_panel: 搜索模式（命中列表 / 无结果提示）。"""

    def test_with_results_renders_select_rows(self) -> None:
        state = ModelPickerState(query="chat", search_results=(_row(), _row(model_id="deepseek-v3")))
        ctrls = model_picker_module._build_search_panel(state, MagicMock())
        assert len(_find_select_rows(ctrls)) == 2

    def test_no_results_shows_hint(self) -> None:
        state = ModelPickerState(query="zzz", search_results=())
        ctrls = model_picker_module._build_search_panel(state, MagicMock())
        texts = [t.value for t in ctrls if isinstance(t, ft.Text)]
        assert "model_picker_no_result" in texts


# ============================================================================
# 组件渲染分支
# ============================================================================


class TestModelPickerRender:
    """ModelPicker 渲染分支：显示文本 / 结果面板可见性 / 控件树结构。"""

    def test_default_empty_state_hides_result_panel(self, mock_i18n_state) -> None:
        _, _, result, _ = _render()
        assert isinstance(result, ft.Column)
        assert result.controls[1].visible is False

    def test_selected_model_displays_provider_and_model(self, mock_i18n_state) -> None:
        state = ModelPickerState(selected_provider="deepseek", selected_model="deepseek-chat")
        _, _, result, _ = _render(state=state)
        assert result.controls[0].value == "DeepSeek / deepseek-chat"

    def test_query_displayed_when_selected_and_query_both_present(self, mock_i18n_state) -> None:
        """已选 + 有关键字：显示关键字而非已选回显（聚焦即进入搜索态）。"""
        state = ModelPickerState(
            selected_provider="deepseek",
            selected_model="deepseek-chat",
            query="chat",
            search_results=(),
        )
        _, _, result, _ = _render(state=state)
        assert result.controls[0].value == "chat"

    def test_searching_shows_progress_panel(self, mock_i18n_state) -> None:
        state = ModelPickerState(searching=True)
        _, _, result, _ = _render(state=state)
        ctrls = _walk_controls(result)
        assert any(isinstance(c, ft.ProgressRing) for c in ctrls)
        assert result.controls[1].visible is True

    def test_search_results_render_rows(self, mock_i18n_state) -> None:
        state = ModelPickerState(query="chat", search_results=(_row(),))
        _, _, result, _ = _render(state=state)
        assert len(_find_select_rows(result)) == 1
        assert result.controls[1].visible is True

    def test_search_no_result_shows_hint(self, mock_i18n_state) -> None:
        state = ModelPickerState(query="zzz", search_results=())
        _, _, result, _ = _render(state=state)
        assert "model_picker_no_result" in _text_values(result)

    def test_browse_with_recent_and_expanded_groups(self, mock_i18n_state) -> None:
        state = ModelPickerState(
            recent=(_row(),),
            groups=(_group(), _group(provider_id="openai", provider_name="OpenAI")),
            expanded=frozenset({"deepseek"}),
        )
        _, _, result, _ = _render(state=state)
        assert result.controls[1].visible is True
        # 最近 1 行 + 展开的 deepseek 2 行（openai 折叠不渲染模型行）
        assert len(_find_select_rows(result)) == 3

    def test_text_field_width_applied(self, mock_i18n_state) -> None:
        _, _, result, _ = _render(text_field_width=320)
        assert result.controls[0].width == 320


# ============================================================================
# 事件处理器
# ============================================================================


class TestModelPickerEventHandlers:
    """事件处理器：搜索框输入 / 聚焦 / 行点击 / 分组头点击。"""

    def test_search_change_forwards_to_vm(self, mock_i18n_state) -> None:
        vm, _, result, _ = _render()
        field = result.controls[0]
        field.value = "gpt"  # noqa: PLW2901 覆写渲染值以构造输入事件
        e = MagicMock()
        e.control = field
        _invoke(field.on_change, e)
        vm.update_model_query.assert_called_once_with("gpt")

    def test_focus_with_selection_clears_query(self, mock_i18n_state) -> None:
        state = ModelPickerState(selected_provider="deepseek", selected_model="deepseek-chat")
        vm, _, result, _ = _render(state=state)
        _invoke(result.controls[0].on_focus, MagicMock())
        vm.update_model_query.assert_called_once_with("")

    def test_focus_without_selection_does_nothing(self, mock_i18n_state) -> None:
        vm, _, result, _ = _render()
        _invoke(result.controls[0].on_focus, MagicMock())
        vm.update_model_query.assert_not_called()

    def test_row_click_selects_and_notifies_consumer(self, mock_i18n_state) -> None:
        on_select = MagicMock()
        state = ModelPickerState(query="chat", search_results=(_row(),))
        vm, _, result, _ = _render(state=state, on_select=on_select)

        rows = _find_select_rows(result)
        assert len(rows) == 1
        _invoke(rows[0].on_click, MagicMock())
        vm.select_model.assert_called_once_with(_row())
        on_select.assert_called_once_with("deepseek", "deepseek-chat")

    def test_row_click_without_consumer_callback(self, mock_i18n_state) -> None:
        state = ModelPickerState(query="chat", search_results=(_row(),))
        vm, _, result, _ = _render(state=state, on_select=None)

        rows = _find_select_rows(result)
        _invoke(rows[0].on_click, MagicMock())
        vm.select_model.assert_called_once_with(_row())

    def test_group_header_click_toggles_vm(self, mock_i18n_state) -> None:
        state = ModelPickerState(groups=(_group(),), expanded=frozenset())
        vm, _, result, _ = _render(state=state)

        headers = [
            c
            for c in _walk_controls(result)
            if isinstance(c, ft.Container) and c.bgcolor is None and c.on_click is not None
        ]
        assert len(headers) == 1
        _invoke(headers[0].on_click, MagicMock())
        vm.toggle_group.assert_called_once_with("deepseek")


# ============================================================================
# 挂载/卸载生命周期 + 挂载 effect
# ============================================================================


class TestModelPickerLifecycle:
    """生命周期：挂载订阅 + 预载目录；卸载退订；无 page 上下文降级。"""

    def test_mount_subscribes_and_preloads_catalog(self, mock_i18n_state) -> None:
        vm, page, _, _ = _render()
        assert len(vm._subscribers) == 1
        _page_run_task(page).assert_called_once_with(vm.ensure_catalog_loaded)

    def test_unmount_unsubscribes_from_vm(self, mock_i18n_state) -> None:
        vm, _, _, component = _render()
        assert len(vm._subscribers) == 1
        run_unmount_effects(component)
        assert len(vm._subscribers) == 0

    def test_catalog_preload_degrades_when_page_context_unavailable(self, mock_i18n_state) -> None:
        """ft.context.page 抛 RuntimeError（无渲染器上下文）→ _load_catalog 静默降级。

        覆盖挂载 effect 的 except RuntimeError 分支（logger.debug，不 crash）。
        """
        with patch("ui.components.model_picker.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("page context missing")))
            _, page, _, _ = _render()
        _page_run_task(page).assert_not_called()
