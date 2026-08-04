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

    PR-1 仅启用 STRATEGY_DROPDOWN + RUN_BUTTON；其余位点的 EIDS 常量在
    PR-2/PR-3 按改造进度补齐（append-only，禁止提前声明未使用常量）。
    """

    STRATEGY_DROPDOWN: Eid = ("e2e.screener.strategy_dropdown", AnchorKind.COMPLEX)
    RUN_BUTTON: Eid = ("e2e.screener.run_button", AnchorKind.INTERACTIVE)


class EIDS:
    """E2E anchor ID 命名空间根。

    使用：`anchored(EIDS.SCREENER.RUN_BUTTON, ft.Button(...))`
    """

    SCREENER = _ScreenerIds
