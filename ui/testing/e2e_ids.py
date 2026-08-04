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

    依据 PoC 实证（reviews/poc/EVIDENCE.md）CanvasKit 双轨映射：
    - INTERACTIVE/INPUT → `[aria-label="EID"]` 独立节点 + 内层 `[flt-tappable]/input`
    - LABEL/COMPLEX → EID 落入 `textContent`（无 aria-label 独立节点）

    `anchored()` 不依 kind 分叉生成逻辑（统一 `Semantics(container=True)` 包裹）。
    """

    INTERACTIVE = "interactive"  # Button/IconButton/Container(on_click)/GestureDetector
    INPUT = "input"  # TextField/TextArea
    LABEL = "label"  # Text (纯展示, 无点击)
    COMPLEX = "complex"  # Dropdown/PopupMenuButton (顶层已 role="button")


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
        """生成单行 anchor。

        ts_code 格式: 6位数字 + .SZ/.SH（ASCII，不会互相前缀重叠）。
        行用 GestureDetector(on_tap) 包裹，INTERACTIVE 类，内层 [flt-tappable] 可定位。

        Precondition: ts_code 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._RESULT_ROW_PREFIX}.{ts_code}", AnchorKind.INTERACTIVE)

    @staticmethod
    def column_header(col_id: str) -> Eid:
        """生成列头 anchor。

        col_id 是数据列名（ASCII，如 pct_chg/close/name）。
        列头用 GestureDetector(on_tap) 包裹（与行一致），INTERACTIVE 类。

        Precondition: col_id 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._COLUMN_HEADER_PREFIX}.{col_id}", AnchorKind.INTERACTIVE)


class _DetailDialogIds:
    """股票详情对话框 anchor 命名空间。"""

    CLOSE_BUTTON: Eid = ("e2e.detail_dialog.close_button", AnchorKind.INTERACTIVE)


class EIDS:
    """E2E anchor ID 命名空间根。

    使用：`anchored(EIDS.SCREENER.RUN_BUTTON, ft.Button(...))`
    """

    SCREENER = _ScreenerIds
    DETAIL_DIALOG = _DetailDialogIds
