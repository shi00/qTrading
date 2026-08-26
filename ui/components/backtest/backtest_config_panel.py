"""回测配置面板（声明式 V1）。

提供回测参数配置界面：
- 日期范围
- 初始资金
- 调仓频率
- 费率设置（含印花税自动分段费率）

变更要点（Phase 3.2.5）：
- 旧命令式 Container 子类 → ``@ft.component def BacktestConfigPanel(on_run_backtest)``
- 纯 UI 状态组件（收集用户输入的回测参数，无业务逻辑/IO/验证/保存），按 project_memory
  责任分层原则用 ``use_state`` 管理，不建 VM（YAGNI）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- DatePicker 通过 ``ft.use_dialog()`` 声明式管理（DialogControl 子类，§10.1）
- 删除死代码 ``_strategy_key``/``set_strategy_key``（BacktestView 用自身 _selected_strategy，
  panel 的 _strategy_key 从未被 get_config/_on_run_click 使用）
- 提取 ``_get_config_from_state`` 纯函数（类型转换/默认值/stamp_duty 分段），可独立单测
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date, timedelta

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_on_change,
    safe_on_click,
    safe_on_dismiss,
    safe_on_select,
)
from ui.components.slider_input import SliderInput
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


def _validate_backtest_inputs(
    start_date: date,
    end_date: date,
    initial_capital_str: str,
    max_positions_str: str,
) -> dict[str, str]:
    """UX-05 (P1-01): 校验回测输入，返回 {字段标识: i18n error key}；全部合法返回空 dict。

    规则与后端 strategies/backtest/config.py::validate 严格对齐：
    - initial_capital: 非空 / float 可解析且 isfinite（拦截 inf/nan/溢出）/ > 0
    - max_positions: 非空 / int 可解析（整数语义）/ >= 1
    - 日期: start < end（严格小于, 与后端 L70 一致）
    """
    errors: dict[str, str] = {}

    if not initial_capital_str:
        errors["initial_capital"] = "backtest_error_required"
    else:
        try:
            capital = float(initial_capital_str)
        except ValueError:
            errors["initial_capital"] = "backtest_error_invalid_number"
        else:
            # isfinite: float("inf")/float("nan")/float("1e309") 均通过 >0 但会破坏 NAV（对抗检视 MAJOR）
            if not math.isfinite(capital):
                errors["initial_capital"] = "backtest_error_invalid_number"
            elif capital <= 0:
                errors["initial_capital"] = "backtest_error_capital_positive"

    if not max_positions_str:
        errors["max_positions"] = "backtest_error_required"
    else:
        try:
            positions = int(max_positions_str)
        except ValueError:
            # 整数语义: "3.5"/"50.0" 被 int() 拒绝 → 非法
            errors["max_positions"] = "backtest_error_invalid_number"
        else:
            if positions < 1:
                errors["max_positions"] = "backtest_error_positions_positive"

    if start_date >= end_date:
        errors["date_range"] = "backtest_error_date_range"

    return errors


def _get_config_from_state(
    start_date: date,
    end_date: date,
    initial_capital_str: str,
    rebalance_freq: str,
    max_positions_str: str,
    commission: float,
    stamp_duty_auto: bool,
    stamp_duty_rate: float,
    slippage: float,
) -> dict:
    """从 UI 状态提取回测配置 dict（纯函数，可独立单测）。

    含类型转换、stamp_duty_auto 分段逻辑（**UX-05: 非法输入不再静默兜底**——
    调用方须先经 _validate_backtest_inputs 校验，此处 float/int 转换失败
    自然抛 ValueError，trust boundary 不吞噬）：
    - stamp_duty_auto=True → stamp_duty_rate=None（由 BacktestConfig 默认值决定）
    - stamp_duty_auto=False → stamp_duty_rate=slider_value/1000（‰ → 小数，0 合法）
    - commission slider 值为万分之一（‱）→ commission_rate=slider_value/10000（0 合法）
    - slippage slider 值为 bps → slippage_bps=slider_value（0 合法）

    注：commission/slippage/stamp_duty_rate 入参类型为 float（use_state 初始化 +
    on_change 的 None 兜底保证非 None），0 是合法值，不用 falsy 兜底。
    """
    initial_capital = float(initial_capital_str)
    max_positions = int(max_positions_str)

    if stamp_duty_auto:
        stamp_duty_rate_val = None
    else:
        stamp_duty_rate_val = stamp_duty_rate / 1000

    return {
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "rebalance_freq": rebalance_freq or "signal",
        "max_position_count": max_positions,
        "commission_rate": commission / 10000,
        "stamp_duty_rate": stamp_duty_rate_val,
        "slippage_bps": slippage,
    }


def _make_date_picker(
    first_date: date,
    last_date: date,
    value: date,
    on_change: ft.ControlEventHandler | None = None,
    on_dismiss: ft.ControlEventHandler | None = None,
) -> ft.DatePicker:
    """创建 DatePicker（i18n 文案在渲染时由声明式重建自动刷新）。

    Args:
        value: 选定日期（picker 高亮显示的当前选中值）。
            current_date 保持默认（datetime.now()），日历网格正确高亮"今天"。
        on_change: 日期变更回调（已通过 ``safe_on_change`` 包装为 ``ControlEventHandler``）。
        on_dismiss: 取消回调（已通过 ``safe_on_dismiss`` 包装为 ``ControlEventHandler``）。
    """
    return ft.DatePicker(
        first_date=first_date,
        last_date=last_date,
        value=value,
        help_text=I18n.get("date_picker_help"),
        cancel_text=I18n.get("common_cancel"),
        confirm_text=I18n.get("common_ok"),
        error_format_text=I18n.get("date_picker_error_format"),
        error_invalid_text=I18n.get("date_picker_error_invalid"),
        on_change=on_change,
        on_dismiss=on_dismiss,
    )


@ft.component
def BacktestConfigPanel(
    on_run_backtest: Callable[[dict], None] | None = None,
) -> ft.Container:
    """回测配置面板（声明式）。

    CLAUDE.md §3.2 MVVM + §3.3 声明式范式：
    - 纯 UI 状态组件（收集回测参数），用 ``use_state`` 管理，不建 VM（YAGNI）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        on_run_backtest: 运行回测回调，接收 config dict
    """
    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- UI state (pure UI state, no VM) ---
    today = date.today()
    one_year_ago = today - timedelta(days=365)

    start_date, set_start_date = ft.use_state(one_year_ago)
    end_date, set_end_date = ft.use_state(today)
    initial_capital, set_initial_capital = ft.use_state("1000000")
    rebalance_freq, set_rebalance_freq = ft.use_state("signal")
    max_positions, set_max_positions = ft.use_state("50")
    commission, set_commission = ft.use_state(3.0)
    stamp_duty_auto, set_stamp_duty_auto = ft.use_state(True)
    stamp_duty_rate, set_stamp_duty_rate = ft.use_state(0.5)
    slippage, set_slippage = ft.use_state(5.0)

    # --- DatePicker 声明式管理 (ft.use_dialog, §10.1) ---
    # DialogControl 子类，show_xxx_picker state 驱动 use_dialog 显示/关闭
    show_start_picker, set_show_start_picker = ft.use_state(False)
    show_end_picker, set_show_end_picker = ft.use_state(False)

    # --- UX-05 (P1-01): 输入校验 (渲染期派生, 非 use_state — 无双源真相, 修正后即时消失) ---
    validation_errors = _validate_backtest_inputs(start_date, end_date, initial_capital, max_positions)

    def _on_start_change(e: ft.ControlEvent) -> None:
        val = get_control_value(e.control, ft.DatePicker)
        if val is not None:
            set_start_date(val)
        set_show_start_picker(False)

    def _on_end_change(e: ft.ControlEvent) -> None:
        val = get_control_value(e.control, ft.DatePicker)
        if val is not None:
            set_end_date(val)
        set_show_end_picker(False)

    def _on_start_dismiss(e: ft.ControlEvent) -> None:
        set_show_start_picker(False)

    def _on_end_dismiss(e: ft.ControlEvent) -> None:
        set_show_end_picker(False)

    ft.use_dialog(
        _make_date_picker(
            date(2020, 1, 1),
            today,
            start_date,
            on_change=safe_on_change(_on_start_change),
            on_dismiss=safe_on_dismiss(_on_start_dismiss),
        )
        if show_start_picker
        else None
    )

    ft.use_dialog(
        _make_date_picker(
            date(2020, 1, 1),
            today,
            end_date,
            on_change=safe_on_change(_on_end_change),
            on_dismiss=safe_on_dismiss(_on_end_dismiss),
        )
        if show_end_picker
        else None
    )

    # --- Handlers ---
    def _show_start_picker(e: ft.ControlEvent) -> None:
        set_show_start_picker(True)

    def _show_end_picker(e: ft.ControlEvent) -> None:
        set_show_end_picker(True)

    def _on_run_click(e: ft.ControlEvent) -> None:
        # UX-05: disabled 是第一道防线 (Flet 原生), handler 内校验短路是第二道防线 —
        # 防御绕过按钮 enabled 语义的调用路径 (组件测试直调 handler/未来新调用方)
        if validation_errors:
            logger.warning(
                "[BacktestConfigPanel] run blocked by input validation errors: %s", sorted(validation_errors)
            )
            return
        if on_run_backtest is not None:
            config = _get_config_from_state(
                start_date=start_date,
                end_date=end_date,
                initial_capital_str=initial_capital,
                rebalance_freq=rebalance_freq,
                max_positions_str=max_positions,
                commission=commission,
                stamp_duty_auto=stamp_duty_auto,
                stamp_duty_rate=stamp_duty_rate,
                slippage=slippage,
            )
            on_run_backtest(config)

    def _on_stamp_duty_auto_change(e: ft.ControlEvent) -> None:
        is_auto = get_control_value(e.control, ft.Switch)
        set_stamp_duty_auto(is_auto)

    # --- Build form controls (driven by state) ---
    start_date_btn = ft.OutlinedButton(
        content=start_date.strftime("%Y-%m-%d"),
        icon=ft.Icons.CALENDAR_TODAY,
        on_click=safe_on_click(_show_start_picker),
    )
    end_date_btn = ft.OutlinedButton(
        content=end_date.strftime("%Y-%m-%d"),
        icon=ft.Icons.CALENDAR_TODAY,
        on_click=safe_on_click(_show_end_picker),
    )

    def _on_initial_capital_change(e: ft.ControlEvent) -> None:
        set_initial_capital(get_control_value(e.control, ft.TextField) or "")

    initial_capital_input = anchored(
        EIDS.BACKTEST.INITIAL_CAPITAL_INPUT,
        ft.TextField(
            label=I18n.get("backtest_initial_capital"),
            value=initial_capital,
            keyboard_type=ft.KeyboardType.NUMBER,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
            on_change=safe_on_change(_on_initial_capital_change),
            # D19: Enter 提交 → 触发运行回测 (表单主操作, 与 Run 按钮同 handler)
            on_submit=safe_on_change(_on_run_click),
        ),
    )

    def _on_rebalance_select(e: ft.ControlEvent) -> None:
        set_rebalance_freq(get_control_value(e.control, ft.Dropdown) or "signal")

    rebalance_dropdown = ft.Dropdown(
        label=I18n.get("backtest_rebalance_freq"),
        options=[
            ft.dropdown.Option("signal", I18n.get("backtest_rebalance_signal")),
            ft.dropdown.Option("daily", I18n.get("backtest_rebalance_daily")),
            ft.dropdown.Option("weekly", I18n.get("backtest_rebalance_weekly")),
            ft.dropdown.Option("monthly", I18n.get("backtest_rebalance_monthly")),
        ],
        value=rebalance_freq,
        bgcolor=AppColors.INPUT_BG,
        border_color=AppColors.INPUT_BORDER,
        color=AppColors.INPUT_TEXT,
        on_select=safe_on_select(_on_rebalance_select),
    )

    def _on_max_positions_change(e: ft.ControlEvent) -> None:
        set_max_positions(get_control_value(e.control, ft.TextField) or "")

    max_position_input = ft.TextField(
        label=I18n.get("backtest_max_positions"),
        value=max_positions,
        keyboard_type=ft.KeyboardType.NUMBER,
        bgcolor=AppColors.INPUT_BG,
        border_color=AppColors.INPUT_BORDER,
        color=AppColors.INPUT_TEXT,
        on_change=safe_on_change(_on_max_positions_change),
        # D19: Enter 提交 → 触发运行回测 (表单主操作, 与 Run 按钮同 handler)
        on_submit=safe_on_change(_on_run_click),
    )

    # --- UX-05: 字段错误提示 (声明式常驻构造, visible 驱动; flet 0.86.5 无 TextField.error_text) ---
    # 常驻构造: 无错误时 content 为空串 (visible=False), 避免条件插入破坏响应式布局后重排
    def _field_error(field: str) -> ft.Text:
        error_key = validation_errors.get(field)
        return ft.Text(
            I18n.get(error_key) if error_key else "",
            color=AppColors.ERROR,
            size=AppStyles.FONT_SIZE_BODY_SM,
            visible=field in validation_errors,
        )

    capital_error = _field_error("initial_capital")
    positions_error = _field_error("max_positions")
    date_range_error = ft.Text(
        I18n.get(validation_errors["date_range"]) if "date_range" in validation_errors else "",
        color=AppColors.ERROR,
        size=AppStyles.FONT_SIZE_BODY_SM,
        visible="date_range" in validation_errors,
    )

    def _on_commission_change(val: float) -> None:
        set_commission(val)

    commission_input = SliderInput(
        label=None,
        value=commission,
        min_val=0,
        max_val=10,
        step=1.0,
        divisions=10,
        fmt=lambda v: f"{v:g}‱",
        on_change=_on_commission_change,
        expand=True,
    )

    stamp_duty_auto_checkbox = ft.Checkbox(
        label=I18n.get("backtest_stamp_duty_auto"),
        value=stamp_duty_auto,
        on_change=safe_on_change(_on_stamp_duty_auto_change),
    )

    def _on_stamp_duty_rate_change(val: float) -> None:
        set_stamp_duty_rate(val)

    stamp_duty_input = SliderInput(
        label=None,
        value=stamp_duty_rate,
        min_val=0,
        max_val=2,
        step=0.5,
        divisions=4,
        fmt=lambda v: f"{v:.1f}‰",
        on_change=_on_stamp_duty_rate_change,
        disabled=stamp_duty_auto,
        expand=True,
    )

    # 状态文案：auto=True 显示 "auto" 文案；auto=False 显示具体费率（与 TextField 一致，作为状态提示）
    if stamp_duty_auto:
        stamp_duty_text_value = I18n.get("backtest_stamp_duty_auto")
    else:
        stamp_duty_text_value = f"{stamp_duty_rate:.1f}‰"
    stamp_duty_text = ft.Text(
        stamp_duty_text_value,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.TEXT_SECONDARY,
    )

    def _on_slippage_change(val: float) -> None:
        set_slippage(val)

    slippage_input = SliderInput(
        label=None,
        value=slippage,
        min_val=0,
        max_val=20,
        step=1.0,
        divisions=20,
        fmt=lambda v: f"{v:g} bps",
        on_change=_on_slippage_change,
        expand=True,
    )

    run_btn = anchored(
        EIDS.BACKTEST.RUN_BUTTON,
        ft.Button(
            content=I18n.get("backtest_run"),
            icon=ft.Icons.PLAY_ARROW,
            # UX-05: 任何非法参数无法提交
            disabled=bool(validation_errors),
            on_click=safe_on_click(_on_run_click),
            style=ft.ButtonStyle(
                bgcolor=AppColors.PRIMARY,
                color=AppColors.TEXT_ON_PRIMARY,
            ),
        ),
    )

    # --- Build layout ---
    return ft.Container(
        expand=True,
        content=ft.Column(
            [
                ft.Text(
                    I18n.get("backtest_config_title"),
                    size=AppStyles.FONT_SIZE_HEADLINE,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Divider(color=AppColors.DIVIDER),
                ft.Text(
                    I18n.get("backtest_date_range"),
                    size=AppStyles.FONT_SIZE_LG,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    I18n.get("backtest_start_date"),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                                start_date_btn,
                                # UX-05: 日期区间错误 (常驻构造, visible 驱动)
                                date_range_error,
                            ],
                            spacing=4,
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    I18n.get("backtest_end_date"),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                                end_date_btn,
                            ],
                            spacing=4,
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    ],
                    run_spacing=16,
                ),
                ft.Container(height=16),
                ft.Text(
                    I18n.get("backtest_portfolio_settings"),
                    size=AppStyles.FONT_SIZE_LG,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [initial_capital_input, capital_error],
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        ft.Column(
                            [rebalance_dropdown],
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        ft.Column(
                            [max_position_input, positions_error],
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    ],
                    run_spacing=16,
                ),
                ft.Container(height=16),
                ft.ExpansionTile(
                    title=ft.Text(I18n.get("backtest_fee_settings"), color=AppColors.TEXT_PRIMARY),
                    trailing=ft.Icon(ft.Icons.SETTINGS, color=AppColors.TEXT_SECONDARY),
                    controls=[
                        ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            I18n.get("backtest_commission_rate"),
                                            size=AppStyles.FONT_SIZE_BODY_SM,
                                            color=AppColors.TEXT_SECONDARY,
                                        ),
                                        commission_input,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Column(
                                    [
                                        stamp_duty_auto_checkbox,
                                        ft.Row(
                                            [
                                                ft.Text(
                                                    I18n.get("backtest_stamp_duty_rate"),
                                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                                    color=AppColors.TEXT_SECONDARY,
                                                ),
                                                stamp_duty_input,
                                                stamp_duty_text,
                                            ],
                                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        ),
                                    ],
                                    spacing=4,
                                ),
                                ft.Row(
                                    [
                                        ft.Text(
                                            I18n.get("backtest_slippage"),
                                            size=AppStyles.FONT_SIZE_BODY_SM,
                                            color=AppColors.TEXT_SECONDARY,
                                        ),
                                        slippage_input,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ],
                            spacing=12,
                        ),
                    ],
                ),
                ft.Container(height=24),
                ft.Row([run_btn], alignment=ft.MainAxisAlignment.END),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
