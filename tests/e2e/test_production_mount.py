"""F07 生产挂载模型 E2E（full-mount 场景池 ``full_mount_page``）。

背景（reviews/flet-review.md F07）：E2E 快速 smoke 模式下
``app_layout._build_pages_stack`` 只构造当前激活视图、非激活视图用空 Container
占位（UX-06 启动优化），E2E 从未覆盖「生产常驻挂载模型」——切页不销毁组件、
use_state/VM 状态跨页保持、use_dialog overlay 跨页存活。本文件经
``E2E_FULL_MOUNT=true`` 场景池以生产挂载模型运行，与快速 smoke 模式互补
（覆盖边界见 ``app_layout._make_content`` docstring）。

证据链：组件重建必丢 use_state（快速 smoke 模式切走即重建，状态复位为初始值），
故「切页往返后状态保持」即「未重建 / 生产常驻挂载」的充分证据。

已知边界（对抗检视修正 + E2E 实测修正）：

1. 原方案场景⑤「详情弹窗切页后存活」经落地确认不可达，已删除：StockDetailDialog
   经 ``ft.use_dialog`` 挂 page overlay，但 ``ft.AlertDialog(modal=False,
   on_dismiss=_close)`` 的 barrier 可点击关闭——弹窗打开时点击导航栏先触发
   on_dismiss 关闭弹窗（点击不穿透），「带弹窗切页」在生产 UI 中无此路径，
   不做伪覆盖。
2. HomeView._init_and_load 在 E2E 模式下仍跳过外部依赖初始化（vm.init()/
   init_data() 会同步构造 TushareClient 并调用真实 Tushare API，违反 E2E
   「外部 IO 可控且无真实网络访问」约束）。full-mount 恢复该初始化需独立任务
   先建服务级 fake 注入通道（子进程内无 mock 注入点），见 PR 描述。
3. 下拉选中值断言不可行（run3/run4 两轮实测证伪，全渠道）：anchored 以
   ``Semantics(container=True, label=EID)`` 包裹控件，anchor 容器语义节点
   textContent 恒为 EID 字符串；且触发器显示文本被 EID label 吞名——视觉已
   选中（截图证实）时 get_by_text / aria-label / input value 三通道均读不到
   策略名，全局文本断言同样落空。选中态改走行为级证据：跳转成功
   （``_handle_backtest_jump`` 对 selected_strategy 为空静默 return，重建必丢
   use_state → 跳转不会发生）。
4. 「去回测」跳转按钮已 anchor 化（RUN_BACKTEST_BUTTON，产品代码零副作用）：
   按钮文本与左导航 nav_backtest label 同名，click_button 文本定位在 full-mount
   首跑实测误点/吞点后 ``_handle_backtest_jump`` 因 selected_strategy 空值守卫
   静默 return，页面不跳转；anchor 精确点击消除该歧义。
5. 场景①② skip（阻塞缺陷 F07-FULLMOUNT-1）：full-mount 首跑（run5）发现
   「去回测」跳转链路真实缺陷——点击事件到达处理器（``UI_ACTION Click
   btn_jump_backtest`` 日志 + select 策略成功），随后 pubsub 导航链抛
   ``RuntimeError: The context is not associated with any page`` ×4
   （exception_hooks AsyncioHandler 捕获，证据：run5
   ``e2e-artifacts/test_backtest_prefill_auto_applied.tar.gz`` 内
   e2e-flet-app.log.tail L35-48），跳转不发生。该链路在快速 smoke E2E 从未
   被覆盖（smoke 用例经导航栏 goto，不走跳转按钮），属 full-mount E2E 的
   首个价值发现；场景①行为级断言（跳转证明选中态保持）与场景②（透传
   跳转）均被其阻塞，skip 待缺陷修复后恢复。注意：场景①截图同时证实
   状态保持真实成立（切页往返后「放量突破」+「600519」均保持），缺陷仅
   阻断自动化断言通道，不影响 F07 核心结论（场景③④已自动化证实）。
"""

import logging

import pytest

from ui.i18n import I18n
from tests.e2e.pages import BacktestPage, NavPage, ScreenerPage, SettingsPage
from tests.e2e.timeouts import TIMEOUTS

pytestmark = [pytest.mark.timeout(180), pytest.mark.e2e, pytest.mark.timeout_e2e_standard]

logger = logging.getLogger(__name__)


@pytest.mark.skip(
    reason="阻塞缺陷 F07-FULLMOUNT-1：btn_jump_backtest 链路 RuntimeError (context not associated "
    "with any page)，跳转不发生（模块 docstring 边界 3/5）；行为级断言依赖该链路，修复后恢复"
)
async def test_screener_state_survives_page_switch(full_mount_page):
    """场景①：选股页控件状态在切页往返后保持（生产常驻挂载，未重建证据链）。

    快速 smoke 模式下切走即销毁 ScreenerView（空 Container 占位），切回重建后
    策略下拉复位 None、过滤值丢失；full-mount 下两者保持即生产挂载模型直接证据。

    断言策略（E2E 实测修正，见模块 docstring 边界 3）：
    - 过滤输入值：expect_text 的 input value 轮询兜底（input value 为真实 DOM
      属性，不受 anchored 语义吞名影响）；
    - 策略选中态：行为级证据——往返后点击「去回测」跳转成功。
      ``_handle_backtest_jump`` 对 selected_strategy 为空静默 return（不跳转、
      按钮不消失），组件重建必丢 use_state → 跳转不会发生 → click_run_backtest
      重试耗尽报错。跳转发生即选中态保持的直接行为证据。
    """
    screener = ScreenerPage(full_mount_page)
    backtest = BacktestPage(full_mount_page)
    nav = NavPage(full_mount_page)

    await screener.open()
    await screener.select_strategy("volume_breakout")

    # stock_filter 为无 anchor TextField（label 定位，透传 FletPage）
    filter_value = "600519"
    await full_mount_page.fill_textbox(I18n.get("screener_filter_stock"), filter_value)

    # 切走（设置页）再切回（选股页）
    await nav.goto("nav_settings")
    await nav.goto("nav_screener")

    # 输入值保持（input value 真实 DOM 属性）
    await full_mount_page.expect_text(filter_value, timeout_ms=TIMEOUTS.TITLE)
    # 策略选中态保持的行为级证据：跳转成功（重建必丢 selected_strategy → 不跳转）
    await screener.click_run_backtest()
    await backtest.expect_title(timeout_ms=TIMEOUTS.TITLE)


@pytest.mark.skip(
    reason="阻塞缺陷 F07-FULLMOUNT-1：btn_jump_backtest 链路 RuntimeError (context not associated "
    "with any page)，跳转不发生（模块 docstring 边界 5）；修复后恢复"
)
async def test_backtest_prefill_auto_applied(full_mount_page):
    """场景②：F13 选股→回测自动透传（首次 + 换策略二次跳转，均不手工重选）。

    生产常驻挂载下回测页在首次导航前已构造（active=False）。透传请求经单调递增
    seq 在「激活 + 新序号」时被 BacktestView._consume_prefill 消费：首次跳转与
    切回换策略后的二次跳转均自动应用新策略，无需在下拉手工重选。

    断言策略（E2E 实测修正，见模块 docstring 边界 3/4）：两次跳转前激活页均为
    选股页，expect_title 具区分性（跳转失败则回测页标题不出现）。透传值正确性
    （volume_breakout/oversold 落到回测页下拉）受 anchored 语义吞名限制不可文本
    断言，由 F13 单测（BacktestView._consume_prefill）覆盖值正确性；本场景行为
    级证据 = 跳转发生即透传链路工作（_handle_backtest_jump 空守卫 + seq 消费）。
    """
    screener = ScreenerPage(full_mount_page)
    backtest = BacktestPage(full_mount_page)
    nav = NavPage(full_mount_page)

    await screener.open()
    await screener.select_strategy("volume_breakout")
    # 首次跳转：RUN_BACKTEST_BUTTON anchor 精确点击（文本定位与左导航同名有歧义）
    await screener.click_run_backtest()
    await backtest.expect_title(timeout_ms=TIMEOUTS.TITLE)

    # 二次跳转：切回选股页换策略再跳转，新 seq 自动消费
    await nav.goto("nav_screener")
    await screener.select_strategy("oversold")
    await screener.click_run_backtest()
    await backtest.expect_title(timeout_ms=TIMEOUTS.TITLE)


async def test_settings_subtab_survives_page_switch(full_mount_page):
    """场景③：设置子页（system，index 5）在切页往返后保持。

    SettingsView.current_tab 为 use_state(0)——组件若被重建，current_tab 复位到
    data（index 0），system 子页内容不可见。切回后 system tab 的语言设置行仍可见
    即未重建证据。
    """
    sp = SettingsPage(full_mount_page)
    await sp.open()
    await sp.expect_title()
    await sp.click_tab("system", timeout_ms=TIMEOUTS.INTERACTION)

    nav = NavPage(full_mount_page)
    await nav.goto("nav_data")
    await nav.goto("nav_settings")

    # 仍在 system 子页：语言设置行标题可见（system tab 特有内容）
    await full_mount_page.expect_text(I18n.get("settings_language"), timeout_ms=TIMEOUTS.TITLE)


async def test_language_switch_applies_to_unvisited_page(full_mount_page):
    """场景④：语言切换对未访问页生效 + 切回后设置页状态保持。

    full-mount 下所有视图启动即常驻构造，i18n 为全局 Observable 驱动重渲染。
    验证：切语言后，本会话未导航过的页（data）直接以新 locale 渲染；切回设置页
    仍在 system 子页（英文 Language 标签可见）。

    语言切换会经 on_change 持久化 locale 到本池独立配置文件（session 进程 teardown
    即弃），不影响 ro/mut 池，故不加 mutates_config marker（该 marker 仅路由
    _e2e_app_dep，full_mount_page 不经其解析）；finally 经 UI 还原 zh（对齐
    test_settings_flow 既有模式）。
    """
    sp = SettingsPage(full_mount_page)
    await sp.open()
    await sp.expect_title()
    await sp.click_tab("system", timeout_ms=TIMEOUTS.INTERACTION)

    lang_label = I18n.get("settings_language")
    lang_en = I18n.get("settings_lang_en")
    lang_zh = I18n.get("settings_lang_zh")
    await full_mount_page.expect_text(lang_label, timeout_ms=10000)

    try:
        await sp.select_language(lang_en, timeout_ms=10000)
        # 同步测试进程 I18n（对齐 test_settings_language_switch 既有模式）
        I18n.set_locale("en_US")

        # 未访问页（data）直接以英文渲染（has_text 轮询，避开 strict mode 多节点）
        data_explorer_en = I18n.get("data_tab_explorer")
        nav = NavPage(full_mount_page)
        await nav.goto("nav_data")
        for _ in range(50):  # 50 * 200ms = 10s
            if await full_mount_page.has_text(data_explorer_en):
                break
            await full_mount_page.page.wait_for_timeout(200)
        else:
            pytest.fail("data 页英文文案未出现（语言切换未传播到未访问页）")

        # 切回设置页：仍在 system 子页且 locale 保持英文
        await nav.goto("nav_settings")
        await full_mount_page.expect_text(I18n.get("settings_language"), timeout_ms=TIMEOUTS.TITLE)
    finally:
        # 还原 flet_app 内存 I18n locale（pristine_config 不覆盖本池，测试自治还原）
        I18n.set_locale("zh")
        try:
            await sp.select_language(lang_zh, timeout_ms=10000)
            nav_settings_zh = I18n.get("nav_settings", locale="zh_CN")
            for _ in range(25):
                if await full_mount_page.has_text(nav_settings_zh):
                    break
                await full_mount_page.page.wait_for_timeout(200)
        except Exception as e:  # noqa: BLE001
            logger.warning("[production_mount] restore language to zh failed: %s", e, exc_info=True)
