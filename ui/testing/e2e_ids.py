"""E2E 测试 anchor ID 命名空间常量。

每个 EID 是 `(id_string, AnchorKind)` 二元组：
- `id_string` 是稳定 ASCII 标识符（与 i18n key、控件文案、控件类型解耦）
- `AnchorKind` 元数据供 `AnchorPage` 决定定位/点击策略，不影响 `anchored()` 生成逻辑

命名规范（附录 A）：`e2e.<view>.<role>[.<qualifier>]`
- 全 ASCII 小写，仅字母数字下划线 + `.` 分隔
- 禁用：中文、空格、破折号 `-`（Flutter Web 解析冲突）
- 动态生成必须走静态方法，禁止调用方字符串拼接

稳定性策略（附录 12A）：append-only。新增随意；删除/重命名 = 破坏性变更，
需走弃用流程（新旧并存 → 迁移 → 1 release cycle 后删除）。
"""

from enum import Enum


class AnchorKind(Enum):
    """决定 `AnchorPage` 定位/点击策略。

    依据 PoC 实证 CanvasKit 双轨映射（按 DOM 形态划分，非控件名）：
    - INTERACTIVE/INPUT → `[aria-label="EID"]` 独立节点 + 内层 `[flt-tappable]/input`
    - LABEL/COMPLEX → EID 落入 `textContent`（无 aria-label 独立节点）

    `anchored()` 统一 `Semantics(container=True)` 包裹；INTERACTIVE 额外设
    `button=True` 辅助 Button 系列生成 aria-label 独立节点（对 GestureDetector
    场景被引擎忽略，故 GD 类应归 COMPLEX，PoC A7 实证）。

    分类依据（PoC A1 / A5 / A7 实证 DOM）：
    - INTERACTIVE：外层控件自带 role=button + label→aria-label 通道的 Flet 原生
      Button 系列（ft.Button / ft.IconButton / ft.FilledButton / ft.OutlinedButton）
      或 TextField（走 [aria-label] + input）。
    - COMPLEX：内部合并 Semantics 子树 + label 落 textContent 的复合控件族：
      ① ft.Dropdown / ft.PopupMenuButton（内层自带 role=button，PoC A5 已证）
      ② ft.Container(on_click=…) / ft.GestureDetector(on_tap=…)（PoC A7 已证：
         Flutter 引擎将外层 Semantics 与 GD 合并为单个 `flt-semantics[role="button"]`
         节点，label 落 textContent，`ft.Semantics.button=True` 参数被引擎忽略）
      定位统一走 `textContent` 前缀匹配（`.` 或 `\n` 分隔）+ `role="button"` 过滤。
    """

    INTERACTIVE = "interactive"  # Button 系列 / TextField (走 aria-label 独立节点)
    INPUT = "input"  # TextField/TextArea
    LABEL = "label"  # Text (纯展示, 无点击)
    COMPLEX = "complex"  # Dropdown / GestureDetector / Container(on_click) (label 落 textContent)


# EID 类型别名：(id_string, AnchorKind) 二元组
Eid = tuple[str, AnchorKind]


class _ScreenerIds:
    """选股页 anchor 命名空间。

    PR-1 启用 STRATEGY_DROPDOWN + RUN_BUTTON；PR-2 补齐 EXPORT_CSV_BUTTON +
    EXPORT_EXCEL_BUTTON + result_row/column_header 动态 anchor；其余位点在
    PR-3 按改造进度补齐（append-only，禁止提前声明未使用常量）。
    """

    STRATEGY_DROPDOWN: Eid = ("e2e.screener.strategy_dropdown", AnchorKind.COMPLEX)
    RUN_BUTTON: Eid = ("e2e.screener.run_button", AnchorKind.INTERACTIVE)
    EXPORT_CSV_BUTTON: Eid = ("e2e.screener.export_csv_button", AnchorKind.INTERACTIVE)
    EXPORT_EXCEL_BUTTON: Eid = ("e2e.screener.export_excel_button", AnchorKind.INTERACTIVE)

    # 动态 anchor 前缀（静态方法生成，禁止调用方字符串拼接）
    _RESULT_ROW_PREFIX = "e2e.screener.result_row"
    _COLUMN_HEADER_PREFIX = "e2e.screener.column_header"

    @staticmethod
    def result_row(ts_code: str) -> Eid:
        """生成单行 anchor（GestureDetector-based，走 COMPLEX textContent 通道）。

        ts_code 格式: 6位数字 + .SZ/.SH（ASCII，不会互相前缀重叠）。
        行用 GestureDetector(on_tap) 包裹；PoC A7 实证 Flutter 合并 Semantics + GD
        为单个 `flt-semantics[role="button"]` 节点，label 落 textContent，
        需按 COMPLEX 定位（textContent 前缀匹配 + role=button 过滤）。

        Precondition: ts_code 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._RESULT_ROW_PREFIX}.{ts_code}", AnchorKind.COMPLEX)

    @staticmethod
    def column_header(col_id: str) -> Eid:
        """生成列头 anchor（GestureDetector-based，走 COMPLEX textContent 通道）。

        col_id 是数据列名（ASCII，如 pct_chg/close/name）。
        列头用 GestureDetector(on_tap) 包裹（与行一致）；PoC A7 实证同 result_row，
        需按 COMPLEX 定位。

        Precondition: col_id 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._COLUMN_HEADER_PREFIX}.{col_id}", AnchorKind.COMPLEX)


class _DetailDialogIds:
    """股票详情对话框 anchor 命名空间。"""

    CLOSE_BUTTON: Eid = ("e2e.detail_dialog.close_button", AnchorKind.INTERACTIVE)


class _SettingsIds:
    """设置页 anchor 命名空间。"""

    LANGUAGE_DROPDOWN: Eid = ("e2e.settings.language_dropdown", AnchorKind.COMPLEX)
    THEME_DROPDOWN: Eid = ("e2e.settings.theme_dropdown", AnchorKind.COMPLEX)
    LOG_LEVEL_DROPDOWN: Eid = ("e2e.settings.log_level_dropdown", AnchorKind.COMPLEX)

    _TAB_PREFIX = "e2e.settings.tab"

    @staticmethod
    def tab(role: str) -> Eid:
        """生成 Tab 按钮 anchor（ft.Button，走 INTERACTIVE aria-label 通道）。

        role 是 tab 角色名（ASCII，如 data/database/ai/tasks/notify/system），
        从 _TAB_CONFIG 的 i18n_key 去掉 ``settings_tab_`` 前缀派生。

        Precondition: role 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        """
        return (f"{_SettingsIds._TAB_PREFIX}.{role}", AnchorKind.INTERACTIVE)


class _DataIds:
    """数据浏览器页 anchor 命名空间。"""

    TABLE_DROPDOWN: Eid = ("e2e.data.dropdown.table", AnchorKind.COMPLEX)
    FILTER_COL_DROPDOWN: Eid = ("e2e.data.dropdown.filter_col", AnchorKind.COMPLEX)
    FILTER_OP_DROPDOWN: Eid = ("e2e.data.dropdown.filter_op", AnchorKind.COMPLEX)
    FILTER_VALUE_INPUT: Eid = ("e2e.data.filter_value_input", AnchorKind.INPUT)
    QUERY_BUTTON: Eid = ("e2e.data.query_button", AnchorKind.INTERACTIVE)
    # PR-478 修复: 表格就绪信号 (LABEL, 仅做存在性探测). 仅在
    # tables_loaded=True + table_columns 非空 + is_loading=False 时渲染.
    # 切表时 reset_table_state 清空 table_columns → 信号先消失, 加载完成后再现,
    # 让 E2E 可以等待真实加载完成而非固定 sleep.
    TABLE_READY: Eid = ("e2e.data.table_ready", AnchorKind.LABEL)


class _BacktestIds:
    """回测页 anchor 命名空间。"""

    STRATEGY_DROPDOWN: Eid = ("e2e.backtest.strategy_dropdown", AnchorKind.COMPLEX)
    CANCEL_BUTTON: Eid = ("e2e.backtest.cancel_button", AnchorKind.INTERACTIVE)
    RUN_BUTTON: Eid = ("e2e.backtest.run_button", AnchorKind.INTERACTIVE)
    INITIAL_CAPITAL_INPUT: Eid = ("e2e.backtest.initial_capital_input", AnchorKind.INPUT)


class _WizardIds:
    """向导页 anchor 命名空间。"""

    NEXT_BUTTON: Eid = ("e2e.wizard.next_button", AnchorKind.INTERACTIVE)
    PREV_BUTTON: Eid = ("e2e.wizard.prev_button", AnchorKind.INTERACTIVE)
    SKIP_BUTTON: Eid = ("e2e.wizard.skip_button", AnchorKind.INTERACTIVE)
    TOKEN_INPUT: Eid = ("e2e.wizard.token_input", AnchorKind.INPUT)


class EIDS:
    """E2E anchor ID 命名空间根。

    使用：`anchored(EIDS.SCREENER.RUN_BUTTON, ft.Button(...))`
    """

    SCREENER = _ScreenerIds
    DETAIL_DIALOG = _DetailDialogIds
    SETTINGS = _SettingsIds
    DATA = _DataIds
    BACKTEST = _BacktestIds
    WIZARD = _WizardIds
