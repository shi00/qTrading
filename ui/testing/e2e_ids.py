"""E2E 测试 anchor ID 命名空间常量。

每个 EID 是 `(id_string, AnchorKind)` 二元组：
- `id_string` 是稳定 ASCII 标识符（与 i18n key、控件文案、控件类型解耦），也是
  CanvasKit 上的 DOM 属性 `flt-semantics-identifier`（由 `anchored()` 注入）；
  `AnchorPage` 以精确选择器 `flt-semantics[flt-semantics-identifier="<EID>"]` 定位。
- `AnchorKind` 现仅承载「kind 特定行为」（见 `AnchorKind` docstring），不影响
  `anchored()` 生成逻辑

命名规范（附录 A）：`e2e.<view>.<role>[.<qualifier>]`
- 全 ASCII 小写，仅字母数字下划线 + `.` 分隔
- 禁用：中文、空格、破折号 `-`（Flutter Web 解析冲突）
- EID 全局唯一（按 identifier 精确选择器，无前缀/后缀匹配，故不要求互不为前缀/后缀）
- 动态生成必须走静态方法，禁止调用方字符串拼接

稳定性策略（附录 12A）：append-only。新增随意；删除/重命名 = 破坏性变更，
需走弃用流程（新旧并存 → 迁移 → 1 release cycle 后删除）。
"""

from enum import Enum


class AnchorKind(Enum):
    """承载 anchor 的「kind 特定行为」，不再决定定位通道。

    identifier 是唯一定位通道：`anchored()` 注入 `Semantics(identifier=EID)`，CanvasKit
    落为 DOM 属性 `flt-semantics-identifier`，`AnchorPage` 以精确选择器
    `flt-semantics[flt-semantics-identifier="<EID>"]` 定位。该节点存在性与 AnchorKind
    无关（P0-2 矩阵：四类 kind 共用同一选择器）。

    AnchorKind 现仅承载 kind 特定行为：
    - `INPUT`：identifier 节点 bbox 与真实输入面不一致（边框/内边距差异），需下潜
      后代 `input`/`textarea` 取真实 bbox。
    - `LABEL`：display-only，`click` / `scroll_into_view` 显式拒绝（无点击语义）。
    - `INTERACTIVE` / `COMPLEX`：在 identifier 路径下定位行为一致（均取 identifier
      节点自身 bbox + 物理鼠标点击）。

    `anchored()` 统一以 `Semantics(container=True)` 包裹，并对 `INTERACTIVE` 设
    `button=True`：保留独立无障碍语义节点（非定位必需，定位已由 identifier 承担）。
    """

    INTERACTIVE = "interactive"  # Button 系列（无障碍语义标注；identifier 定位）
    INPUT = "input"  # TextField/TextArea（identifier 定位需下潜后代 input）
    LABEL = "label"  # Text (纯展示, 无点击；click/scroll 拒绝)
    COMPLEX = "complex"  # Dropdown / GestureDetector / Container(on_click)


# EID 类型别名：(id_string, AnchorKind) 二元组
Eid = tuple[str, AnchorKind]


class _ScreenerIds:
    """选股页 anchor 命名空间。

    PR-1 启用 STRATEGY_DROPDOWN + RUN_BUTTON；PR-2 补齐 EXPORT_CSV_BUTTON +
    EXPORT_EXCEL_BUTTON + result_row/column_header 动态 anchor；其余位点在
    PR-3 按改造进度补齐（append-only，禁止提前声明未使用常量）。
    """

    STRATEGY_DROPDOWN: Eid = ("e2e.screener.strategy_dropdown", AnchorKind.COMPLEX)
    # MAJOR-07：策略参数区「高级设置」ExpansionTile 入口 anchor。E2E 在最小视口
    # (1280×672) 下展开它以验证控制区不被裁切且结果表首行仍可见（DoD）。
    ADVANCED_SETTINGS: Eid = ("e2e.screener.advanced_settings", AnchorKind.COMPLEX)
    RUN_BUTTON: Eid = ("e2e.screener.run_button", AnchorKind.INTERACTIVE)
    EXPORT_CSV_BUTTON: Eid = ("e2e.screener.export_csv_button", AnchorKind.INTERACTIVE)
    EXPORT_EXCEL_BUTTON: Eid = ("e2e.screener.export_excel_button", AnchorKind.INTERACTIVE)

    # 动态 anchor 前缀（静态方法生成，禁止调用方字符串拼接）
    _RESULT_ROW_PREFIX = "e2e.screener.result_row"
    _COLUMN_HEADER_PREFIX = "e2e.screener.column_header"
    _DETAIL_BUTTON_PREFIX = "e2e.screener.detail_button"

    @staticmethod
    def result_row(ts_code: str) -> Eid:
        """生成单行 anchor（GestureDetector-based，AnchorKind=COMPLEX）。

        ts_code 格式: 6位数字 + .SZ/.SH（ASCII）。
        行用 GestureDetector(on_tap) 包裹；identifier 路径不区分节点形态，统一以
        `flt-semantics[flt-semantics-identifier="<EID>"]` 精确定位（AnchorKind=COMPLEX
        不改变定位方式，仅标注可交互语义）。

        Precondition: ts_code 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._RESULT_ROW_PREFIX}.{ts_code}", AnchorKind.COMPLEX)

    @staticmethod
    def column_header(col_id: str) -> Eid:
        """生成列头 anchor（GestureDetector-based，AnchorKind=COMPLEX）。

        col_id 是数据列名（ASCII，如 pct_chg/close/name）。
        列头用 GestureDetector(on_tap) 包裹；identifier 路径统一精确定位（与
        result_row 同策略，AnchorKind 仅做语义标注）。

        Precondition: col_id 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._COLUMN_HEADER_PREFIX}.{col_id}", AnchorKind.COMPLEX)

    @staticmethod
    def detail_button(ts_code: str) -> Eid:
        """生成行内「详情」按钮 anchor（ft.TextButton，AnchorKind=INTERACTIVE）。

        「详情」动作列入口作为**独立** anchor：与行 anchor（``result_row``）语义分离，
        位于行 anchor 子树之外（兄弟节点，见 virtual_table 模块 docstring），
        使行 anchor 与行内交互控件的语义/命中区域互不覆盖。
        TextButton 为 Flet 原生 Button 系列，AnchorKind 取 INTERACTIVE 以标注可交互语义
        （identifier 路径下与 COMPLEX 定位方式一致：identifier 节点自身 bbox）。

        Precondition: ts_code 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_ScreenerIds._DETAIL_BUTTON_PREFIX}.{ts_code}", AnchorKind.INTERACTIVE)


class _DetailDialogIds:
    """股票详情对话框 anchor 命名空间。"""

    CLOSE_BUTTON: Eid = ("e2e.detail_dialog.close_button", AnchorKind.INTERACTIVE)


class _NewsRiskIds:
    """新闻风险解读面板 anchor 命名空间（Phase E3, e2e.news_risk.*）。

    对应 ``ui/components/news_insight_panel.py`` 按 phase 渲染的各区块：
    - loading/analyzing 阶段用 ``LOADING_EVIDENCE`` / ``ANALYZING``（LABEL, 存在性探测）
    - evidence_ready 阶段 ``GENERATE_BUTTON``（INTERACTIVE 触发生成）
    - degraded/error 阶段 ``RETRY_BUTTON``（INTERACTIVE 触发重试）；error 语义
      ``ERROR``（LABEL）与说明文本同节点
    - ready 阶段 ``RISK_LEVEL`` / ``SUMMARY`` / ``EVENT_EMPTY`` / ``COVERAGE``（LABEL）
      及动态 ``event_card(idx)``（LABEL, 卡片可点展开但 AnchorPage 断言定位即可）
    """

    LOADING_EVIDENCE: Eid = ("e2e.news_risk.loading_evidence", AnchorKind.LABEL)
    ANALYZING: Eid = ("e2e.news_risk.analyzing", AnchorKind.LABEL)
    GENERATE_BUTTON: Eid = ("e2e.news_risk.generate_button", AnchorKind.INTERACTIVE)
    RETRY_BUTTON: Eid = ("e2e.news_risk.retry_button", AnchorKind.INTERACTIVE)
    RISK_LEVEL: Eid = ("e2e.news_risk.risk_level", AnchorKind.LABEL)
    SUMMARY: Eid = ("e2e.news_risk.summary", AnchorKind.LABEL)
    EVENT_EMPTY: Eid = ("e2e.news_risk.event_empty", AnchorKind.LABEL)
    COVERAGE: Eid = ("e2e.news_risk.coverage", AnchorKind.LABEL)
    ERROR: Eid = ("e2e.news_risk.error", AnchorKind.LABEL)

    _EVENT_CARD_PREFIX = "e2e.news_risk.event_card"

    @staticmethod
    def event_card(idx: int) -> Eid:
        """生成第 idx 张风险事件卡片 anchor（LABEL, 定位/断言用）。"""
        return (f"{_NewsRiskIds._EVENT_CARD_PREFIX}.{idx}", AnchorKind.LABEL)


class _SettingsIds:
    """设置页 anchor 命名空间。"""

    LANGUAGE_DROPDOWN: Eid = ("e2e.settings.language_dropdown", AnchorKind.COMPLEX)
    THEME_DROPDOWN: Eid = ("e2e.settings.theme_dropdown", AnchorKind.COMPLEX)
    LOG_LEVEL_DROPDOWN: Eid = ("e2e.settings.log_level_dropdown", AnchorKind.COMPLEX)

    _TAB_PREFIX = "e2e.settings.tab"

    @staticmethod
    def tab(role: str) -> Eid:
        """生成 Tab 按钮 anchor（ft.Button，AnchorKind=INTERACTIVE）。

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
    FILTER_CLEAR_BUTTON: Eid = ("e2e.data.clear_filter_button", AnchorKind.INTERACTIVE)
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


class _TushareIds:
    """Tushare 配置面板 anchor 命名空间（设置 data tab 与 onboarding wizard 复用）。

    VERIFY_BUTTON: ``ft.Button`` 标准交互控件（AnchorKind.INTERACTIVE）。
    identifier 路径下 AnchorPage 以 `flt-semantics-identifier` 精确定位该节点，取
    bbox 中心做稳定的物理鼠标点击（PR669 E2E 修复：非 anchor 的 click_button
    fallback 点击偶发不触发 Flutter 回调）。
    """

    VERIFY_BUTTON: Eid = ("e2e.tushare.verify_button", AnchorKind.INTERACTIVE)


class _NavIds:
    """导航栏 anchor 命名空间 (PR-4 Task 4.0/4.1)。

    NavigationRailDestination.label 用 ``anchored()`` 包裹 ``ft.Text``。
    AnchorKind=LABEL（纯展示，无点击）；identifier 路径下以
    `flt-semantics[flt-semantics-identifier="<EID>"]` 精确定位。

    Task 4.0 PoC 验证 T-3 不确定性：``NavigationRail(extended=False)`` 折叠态下
    ``Semantics(container=True)`` 包裹的 label 是否仍暴露到 DOM。PASS 则 4.1 P2-1
    nav 迁移维持 LABEL 策略；FAIL 则改用 icon ``tooltip`` 或 ``IconButton`` wrap。
    """

    MARKET: Eid = ("e2e.nav.market", AnchorKind.LABEL)
    SCREENER: Eid = ("e2e.nav.screener", AnchorKind.LABEL)
    BACKTEST: Eid = ("e2e.nav.backtest", AnchorKind.LABEL)
    DATA: Eid = ("e2e.nav.data", AnchorKind.LABEL)
    TASKS: Eid = ("e2e.nav.tasks", AnchorKind.LABEL)
    SETTINGS: Eid = ("e2e.nav.settings", AnchorKind.LABEL)
    WATCHLIST: Eid = ("e2e.nav.watchlist", AnchorKind.LABEL)


class _HomeIds:
    """首页 KPI 卡片 anchor 命名空间 (PR-4 Task 4.1, P2-2)。

    ``MarketDashboard`` 组件的 KPI 卡片标题用 ``anchored()`` 包裹 ``ft.Text``。
    AnchorKind=LABEL（纯展示，无点击）；identifier 路径下以
    `flt-semantics[flt-semantics-identifier="<EID>"]` 精确定位。
    """

    KPI_SH: Eid = ("e2e.home.kpi.sh", AnchorKind.LABEL)
    KPI_SZ: Eid = ("e2e.home.kpi.sz", AnchorKind.LABEL)
    KPI_CYB: Eid = ("e2e.home.kpi.cyb", AnchorKind.LABEL)
    KPI_NORTHBOUND: Eid = ("e2e.home.kpi.northbound", AnchorKind.LABEL)


class _TaskCenterIds:
    """任务中心 anchor 命名空间 (PR-4 Task 4.1, P2-3)。

    任务行卡片整体用 ``anchored()`` 包裹（``ft.Container`` 无 ``on_click``）。
    AnchorKind=LABEL（卡片整体纯展示）；identifier 路径下以
    `flt-semantics[flt-semantics-identifier="<EID>"]` 精确定位。

    task_id 来自 ``TaskRow.id``（UUID 前 12 字符，ASCII）。
    """

    TASK_LIST: Eid = ("e2e.task_center.task_list", AnchorKind.LABEL)

    _TASK_ROW_PREFIX = "e2e.task_center.task_row"

    @staticmethod
    def task_row(task_id: str) -> Eid:
        """生成单个任务行 anchor (LABEL, 卡片整体无 on_click)。

        Precondition: task_id 必须为 ASCII 且不含空格/破折号（附录 A 命名规范）。
        调用方负责确保输入合法，本方法不做运行时校验（YAGNI）。
        """
        return (f"{_TaskCenterIds._TASK_ROW_PREFIX}.{task_id}", AnchorKind.LABEL)


class EIDS:
    """E2E anchor ID 命名空间根。

    使用：`anchored(EIDS.SCREENER.RUN_BUTTON, ft.Button(...))`
    """

    SCREENER = _ScreenerIds
    DETAIL_DIALOG = _DetailDialogIds
    NEWS_RISK = _NewsRiskIds
    SETTINGS = _SettingsIds
    DATA = _DataIds
    BACKTEST = _BacktestIds
    WIZARD = _WizardIds
    TUSHARE = _TushareIds
    NAV = _NavIds
    HOME = _HomeIds
    TASK_CENTER = _TaskCenterIds
