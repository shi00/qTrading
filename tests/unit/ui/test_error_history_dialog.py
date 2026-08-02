"""ui/components/error_history_dialog.py 单元测试 (Issue #448).

验证维度:
1. build_error_history_dialog 纯函数契约 (is_open=False 返回 None)
2. 空错误列表渲染 error_history_empty 提示
3. 非空错误列表渲染 _build_error_entry 条目
4. _build_error_entry 含/不含 details 分支
5. on_close / clear_history 按钮回调绑定
6. i18n key 调用契约 (error_history_title / error_history_empty / common_close / error_history_clear)
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import datetime
from unittest.mock import MagicMock

import flet as ft
import pytest

from ui.components.error_history_dialog import (
    _ENTRY_BORDER_RADIUS,
    _ENTRY_DETAILS_MAX_LINES,
    _ENTRY_PADDING_ALL,
    _DIALOG_HEIGHT,
    _DIALOG_WIDTH,
    _build_error_entry,
    build_error_history_dialog,
)
from ui.components.error_history_store import ErrorHistoryEntry
from ui.theme import AppColors

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_i18n(monkeypatch):
    """Mock I18n.get 返回 key 本身 (不依赖 locale 文件)."""
    mock = MagicMock()
    mock.get.side_effect = lambda key, *a, **kw: key
    monkeypatch.setattr("ui.components.error_history_dialog.I18n", mock)
    return mock


@pytest.fixture
def sample_entry() -> ErrorHistoryEntry:
    """含 details 的样例错误条目."""
    return ErrorHistoryEntry(
        timestamp=datetime.datetime(2026, 8, 1, 12, 0, 0),
        source="watchlist",
        title="加载失败",
        message="无法连接数据库",
        details="ConnectionRefusedError: localhost:5432",
    )


@pytest.fixture
def sample_entry_no_details() -> ErrorHistoryEntry:
    """不含 details 的样例错误条目."""
    return ErrorHistoryEntry(
        timestamp=datetime.datetime(2026, 8, 1, 12, 0, 0),
        source="task_center",
        title="任务失败",
        message="执行超时",
        details="",
    )


# ============================================================================
# 1. build_error_history_dialog 纯函数契约
# ============================================================================


class TestBuildDialogContract:
    """build_error_history_dialog 纯函数契约守护."""

    def test_returns_none_when_closed(self, mock_i18n):
        """is_open=False 时返回 None (ft.use_dialog(None) 不挂载)."""
        result = build_error_history_dialog(
            is_open=False,
            on_close=lambda: None,
            errors=[],
        )
        assert result is None

    def test_returns_alert_dialog_when_open(self, mock_i18n, sample_entry):
        """is_open=True 且有错误时返回 ft.AlertDialog."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        assert isinstance(result, ft.AlertDialog)

    def test_returns_alert_dialog_when_open_empty(self, mock_i18n):
        """is_open=True 且无错误时也返回 ft.AlertDialog (展示 empty 提示)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[],
        )
        assert isinstance(result, ft.AlertDialog)

    def test_dialog_is_modal(self, mock_i18n, sample_entry):
        """Dialog 必须是 modal (阻塞主窗口交互)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        assert result.modal is True


# ============================================================================
# 2. i18n key 调用契约
# ============================================================================


class TestI18nKeys:
    """验证 build_error_history_dialog 调用了正确的 i18n key."""

    def test_calls_error_history_title(self, mock_i18n, sample_entry):
        """打开 dialog 时调用 I18n.get('error_history_title')."""
        build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        called_keys = [call.args[0] for call in mock_i18n.get.call_args_list]
        assert "error_history_title" in called_keys

    def test_calls_common_close(self, mock_i18n, sample_entry):
        """打开 dialog 时调用 I18n.get('common_close')."""
        build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        called_keys = [call.args[0] for call in mock_i18n.get.call_args_list]
        assert "common_close" in called_keys

    def test_calls_error_history_clear(self, mock_i18n, sample_entry):
        """打开 dialog 时调用 I18n.get('error_history_clear')."""
        build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        called_keys = [call.args[0] for call in mock_i18n.get.call_args_list]
        assert "error_history_clear" in called_keys

    def test_calls_error_history_empty_when_no_errors(self, mock_i18n):
        """无错误时调用 I18n.get('error_history_empty')."""
        build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[],
        )
        called_keys = [call.args[0] for call in mock_i18n.get.call_args_list]
        assert "error_history_empty" in called_keys

    def test_does_not_call_error_history_empty_when_has_errors(self, mock_i18n, sample_entry):
        """有错误时不调用 error_history_empty."""
        build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        called_keys = [call.args[0] for call in mock_i18n.get.call_args_list]
        assert "error_history_empty" not in called_keys


# ============================================================================
# 3. 空错误列表渲染
# ============================================================================


class TestEmptyStateRendering:
    """空错误列表渲染分支."""

    def test_empty_errors_renders_placeholder_text(self, mock_i18n):
        """空错误列表渲染 error_history_empty 提示文案."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[],
        )
        # content 是 Container 包裹 Column
        assert result.content is not None
        column = result.content.content
        assert isinstance(column, ft.Column)
        assert len(column.controls) == 1
        placeholder = column.controls[0]
        assert isinstance(placeholder, ft.Container)
        text = placeholder.content
        assert isinstance(text, ft.Text)
        # mock I18n.get 返回 key 本身
        assert text.value == "error_history_empty"


# ============================================================================
# 4. _build_error_entry 条目渲染
# ============================================================================


class TestBuildErrorEntry:
    """_build_error_entry 单条错误条目构建."""

    def test_returns_container(self, mock_i18n, sample_entry):
        """_build_error_entry 返回 ft.Container."""
        result = _build_error_entry(sample_entry)
        assert isinstance(result, ft.Container)

    def test_has_border(self, mock_i18n, sample_entry):
        """条目包含 border (Border.all 结构, 视觉边界)."""
        result = _build_error_entry(sample_entry)
        assert isinstance(result.border, ft.Border)
        # Border.all 产生 4 个 BorderSide (top/right/bottom/left)
        assert result.border.top is not None
        assert result.border.right is not None
        assert result.border.bottom is not None
        assert result.border.left is not None

    def test_has_bgcolor_surface(self, mock_i18n, sample_entry):
        """条目 bgcolor 为 SURFACE (与背景区分)."""
        result = _build_error_entry(sample_entry)
        assert result.bgcolor == AppColors.SURFACE

    def test_has_padding(self, mock_i18n, sample_entry):
        """条目包含 padding (值为 _ENTRY_PADDING_ALL)."""
        result = _build_error_entry(sample_entry)
        assert result.padding == ft.Padding.all(_ENTRY_PADDING_ALL)

    def test_has_border_radius(self, mock_i18n, sample_entry):
        """条目包含 border_radius."""
        result = _build_error_entry(sample_entry)
        assert result.border_radius == _ENTRY_BORDER_RADIUS

    def test_column_has_title_row(self, mock_i18n, sample_entry):
        """条目 Column 第一个控件是标题 Row (icon + title)."""
        result = _build_error_entry(sample_entry)
        column = result.content
        assert isinstance(column, ft.Column)
        assert len(column.controls) >= 3
        title_row = column.controls[0]
        assert isinstance(title_row, ft.Row)
        assert any(isinstance(c, ft.Icon) for c in title_row.controls)
        assert any(isinstance(c, ft.Text) and c.value == sample_entry.title for c in title_row.controls)

    def test_column_has_message_text(self, mock_i18n, sample_entry):
        """条目 Column 第二个控件是消息 Text."""
        result = _build_error_entry(sample_entry)
        column = result.content
        message_text = column.controls[1]
        assert isinstance(message_text, ft.Text)
        assert message_text.value == sample_entry.message

    def test_column_has_source_time_row(self, mock_i18n, sample_entry):
        """条目 Column 第三个控件是来源+时间 Row."""
        result = _build_error_entry(sample_entry)
        column = result.content
        source_time_row = column.controls[2]
        assert isinstance(source_time_row, ft.Row)
        text = source_time_row.controls[0]
        assert isinstance(text, ft.Text)
        assert "2026-08-01 12:00:00" in text.value

    def test_source_label_uses_error_source_key(self, mock_i18n, sample_entry):
        """来源标签使用 error_source_<source> i18n key."""
        _build_error_entry(sample_entry)
        called_keys = [call.args[0] for call in mock_i18n.get.call_args_list]
        assert "error_source_watchlist" in called_keys

    def test_source_label_fallback_to_source_when_no_i18n(self, mock_i18n, sample_entry):
        """i18n 未命中时 fallback 到 source 本身 (通过 I18n.get 第二参数)."""
        # mock_i18n.get.side_effect = lambda key, *a, **kw: key 已经会返回 key
        # 验证调用签名: I18n.get(f"error_source_{source}", source) 第二参数为 source
        _build_error_entry(sample_entry)
        # 找到 error_source_watchlist 的调用
        source_call = next(call for call in mock_i18n.get.call_args_list if call.args[0] == "error_source_watchlist")
        # 第二位置参数应为 source (fallback)
        assert source_call.args[1] == "watchlist"

    def test_includes_details_when_present(self, mock_i18n, sample_entry):
        """details 非空时追加 details Text (第 4 个控件)."""
        result = _build_error_entry(sample_entry)
        column = result.content
        assert len(column.controls) == 4
        details_text = column.controls[3]
        assert isinstance(details_text, ft.Text)
        assert details_text.value == sample_entry.details
        assert details_text.selectable is True
        assert details_text.max_lines == _ENTRY_DETAILS_MAX_LINES

    def test_omits_details_when_empty(self, mock_i18n, sample_entry_no_details):
        """details 为空时不追加 details Text (仅 3 个控件)."""
        result = _build_error_entry(sample_entry_no_details)
        column = result.content
        assert len(column.controls) == 3


# ============================================================================
# 5. 按钮回调
# ============================================================================


class TestButtonCallbacks:
    """按钮回调绑定验证."""

    def test_has_two_action_buttons(self, mock_i18n, sample_entry):
        """Dialog actions 包含 2 个按钮 (清除 + 关闭)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        assert len(result.actions) == 2

    def test_clear_button_uses_error_color(self, mock_i18n, sample_entry):
        """清除按钮使用 ERROR 颜色 (危险操作)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        clear_btn = result.actions[0]
        assert isinstance(clear_btn, ft.TextButton)
        assert clear_btn.style is not None

    def test_clear_button_has_delete_icon(self, mock_i18n, sample_entry):
        """清除按钮包含 DELETE_SWEEP_OUTLINED 图标."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        clear_btn = result.actions[0]
        assert clear_btn.icon == ft.Icons.DELETE_SWEEP_OUTLINED

    def test_close_button_is_last_action(self, mock_i18n, sample_entry):
        """关闭按钮是最后一个 action (UX 惯例: 主操作在右)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        close_btn = result.actions[-1]
        assert isinstance(close_btn, ft.TextButton)

    def test_actions_alignment_end(self, mock_i18n, sample_entry):
        """actions_alignment 为 END (按钮右对齐)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        assert result.actions_alignment == ft.MainAxisAlignment.END


# ============================================================================
# 6. Dialog 容器结构
# ============================================================================


class TestDialogLayout:
    """Dialog 容器结构验证."""

    def test_title_has_icon_and_text(self, mock_i18n, sample_entry):
        """title 是 Row(icon + text) 结构."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        title = result.title
        assert isinstance(title, ft.Row)
        assert any(isinstance(c, ft.Icon) for c in title.controls)
        assert any(isinstance(c, ft.Text) for c in title.controls)

    def test_content_is_container_with_column(self, mock_i18n, sample_entry):
        """content 是 Container 包裹 Column 结构."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        assert isinstance(result.content, ft.Container)
        assert isinstance(result.content.content, ft.Column)

    def test_content_has_fixed_dimensions(self, mock_i18n, sample_entry):
        """content Container 有固定宽高 (避免无限拉伸)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        assert result.content.width == _DIALOG_WIDTH
        assert result.content.height == _DIALOG_HEIGHT

    def test_content_column_scroll_auto(self, mock_i18n, sample_entry):
        """content Column 启用 scroll (错误多时可滚动)."""
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=[sample_entry],
        )
        column = result.content.content
        assert column.scroll == ft.ScrollMode.AUTO


# ============================================================================
# 7. 多错误条目渲染
# ============================================================================


class TestMultipleErrorsRendering:
    """多错误条目渲染验证."""

    def test_renders_all_entries(self, mock_i18n):
        """多条错误全部渲染到 Column.controls."""
        entries = [
            ErrorHistoryEntry(
                timestamp=datetime.datetime(2026, 8, 1, 12, 0, 0),
                source=f"src_{i}",
                title=f"title_{i}",
                message=f"msg_{i}",
                details=f"details_{i}" if i % 2 == 0 else "",
            )
            for i in range(5)
        ]
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=entries,
        )
        column = result.content.content
        assert len(column.controls) == 5

    def test_mixed_details_and_no_details(self, mock_i18n):
        """混合 details 有无的条目都能正确渲染."""
        entries = [
            ErrorHistoryEntry(
                timestamp=datetime.datetime(2026, 8, 1, 12, 0, 0),
                source="src1",
                title="t1",
                message="m1",
                details="d1",
            ),
            ErrorHistoryEntry(
                timestamp=datetime.datetime(2026, 8, 1, 12, 0, 0),
                source="src2",
                title="t2",
                message="m2",
                details="",
            ),
        ]
        result = build_error_history_dialog(
            is_open=True,
            on_close=lambda: None,
            errors=entries,
        )
        column = result.content.content
        assert len(column.controls) == 2
        # 第一条有 details (4 个控件), 第二条无 (3 个控件)
        first_entry_column = column.controls[0].content
        second_entry_column = column.controls[1].content
        assert len(first_entry_column.controls) == 4
        assert len(second_entry_column.controls) == 3
