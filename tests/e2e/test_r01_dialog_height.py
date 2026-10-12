"""R01 详情弹窗最小高度 E2E 验证（reviews/flet-review.md §5 R01 / §10.14）。

验证目标（报告原文）：1024x640、150%/200% 缩放下，详情弹窗长内容/加载/错误三
状态中「关闭按钮可达、滚动底部可达」；不能只验 ``_dialog_size`` 返回值。

可自动化性与仿真口径（本 docstring 即判定依据）：

1. **布局等价性**：E2E 为 Flutter web（CanvasKit）渲染，与 Windows 桌面共享同一
   Flutter ``AlertDialog`` 布局约束实现（``Dialog.insetPadding`` 屏幕钳制 +
   content 包 ``Flexible`` 弹性收缩 + title/actions 固定），布局行为一致。
2. **缩放仿真口径**：Playwright ``device_scale_factor`` 仅改渲染 DPR、不改逻辑
   像素布局，无法仿真 Windows DPI 缩放；缩放对布局的真实影响是「物理屏幕受限时
   窗口逻辑视口收缩」（1024x640 物理 / 150% ≈ 683x427 逻辑；/ 200% ≈ 512x320）。
   故采用**弹窗打开状态下 ``set_viewport_size`` 压缩视口**的等价仿真：直接驱动
   Flutter 对已挂载 overlay（AlertDialog）按新视口重新施加 insetPadding 钳制，
   同时覆盖报告修改方案中「resize 后重算」的要求。注：<1280 宽视口下选股前置
   链路（策略下拉 overlay 菜单被视口裁剪、选项语义节点不渲染，run1 实测）不可用，
   属 E2E 工具层限制，故不以小视口作为初始视口。
3. **E2E 与桌面的差异（更严苛侧）**：web 模式 ``page.window.width`` 为 None
   （flet Window.width 默认值，web 无原生窗口回填），``_dialog_size`` 走
   fallback (900, 700)——content 高 700 比桌面 1024x640 的 560 更高，dialog 总
   需求更大，验证场景更严苛；桌面 560 介于 (900,700) fallback 与更小视口场景
   之间，由更小 viewport 用例一并覆盖。
4. **错误态（图表加载失败文本）不可自动化构造**：当前 E2E 基建无 chart error
   mock 通道（news 场景池仅 success_events / success_no_events），该子项标注
   「仍未验证」；其渲染内容为单行错误文本，高度远小于长内容态，不构成额外挤压
   风险（风险判断，非实测结论）。

判据（行为级，非 ``_dialog_size`` 返回值）：

- **关闭按钮**（``AlertDialog.actions`` 区，非滚动内容）``expect_in_viewport``
  bbox 完整在视口内，且经 ``AnchorPage.click`` 真实鼠标物理点击（bbox 中心
  mouse.click；视口外坐标 Flutter 收不到，见 pages.py ``NewsRiskPage.generate``
  docstring E3 实证）成功关闭弹窗——可达 + 可操作双重证据。
- **滚动底部可达**：内容流最底部交互锚点（新闻面板 COVERAGE；证据模式下为
  GENERATE_BUTTON，E3 实证 y~1768 远超 900 视口）经 ``scroll_into_view``
  （mouse.wheel 驱动 Flutter 真实滚动，CanvasKit 下 DOM 滚动无效）后
  ``expect_in_viewport``；1024 基准视口额外以大步滚轮滚到底后断言「风险摘要」
  文本节点 bbox 进入视口（内容流真正的最后一个元素，无 anchor，经语义树
  evaluate 取 bbox）。
- **键盘关闭**：Escape（``AlertDialog`` on_dismiss 路径）。
- **加载态**：news AI analyzing（mock DELAY_MS=1500，ProgressRing 稳定窗口）
  下关闭按钮完整在视口。图表加载中（``_initial_chart_content``）为亚秒瞬态，
  竞态不可稳定捕获，由 analyzing 态代表「弹窗处于部分加载时的可操作性」。
"""

import pytest
import pytest_asyncio

from tests.e2e.conftest import _e2e_page_with_viewport
from tests.e2e.pages import NewsRiskPage
from tests.e2e.timeouts import TIMEOUTS
from ui.i18n import I18n
from ui.testing.e2e_ids import EIDS

pytestmark = [pytest.mark.timeout(300), pytest.mark.e2e, pytest.mark.timeout_e2e_standard]

# Windows DPI 缩放的布局等价视口：物理窗口 1024x640 受屏幕物理像素钳制后的逻辑视口。
VIEWPORT_1024X640 = (1024, 640)
VIEWPORT_150PCT = (683, 427)
VIEWPORT_200PCT = (512, 320)

_SCROLL_STEP = 2000
_SCROLL_SETTLE_MS = 300


@pytest_asyncio.fixture(loop_scope="session")
async def r01_page(request, e2e_browser, seed_nr_success, news_risk_app_success):
    """R01 专用 Page：与 news_risk 场景池共享 session app 进程，仅视口不同。"""
    viewport = request.param
    async with _e2e_page_with_viewport(e2e_browser, news_risk_app_success, request, viewport=viewport) as fp:
        yield fp


async def _scroll_dialog_content_to_bottom(nrp: NewsRiskPage) -> None:
    """把弹窗内容区滚到底（真实滚轮大步滚动；CanvasKit 下 DOM 滚动不驱动 Flutter）。"""
    page = nrp.page.page
    viewport = page.viewport_size or {"width": 1024, "height": 640}
    await page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)
    for _ in range(4):
        await page.mouse.wheel(0, _SCROLL_STEP)
        await page.wait_for_timeout(_SCROLL_SETTLE_MS)


async def _text_node_in_viewport(nrp: NewsRiskPage, text: str) -> bool:
    """语义树中含指定文本的节点 bbox 是否完整在视口内（滚到内容底部的证据）。"""
    page = nrp.page.page
    box = await page.evaluate(
        """(args) => {
            const els = document.querySelectorAll('flt-semantics');
            for (const el of els) {
                const t = ((el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')).trim();
                if (t.includes(args.text)) {
                    const r = el.getBoundingClientRect();
                    return {y: r.y, h: r.height};
                }
            }
            return null;
        }""",
        {"text": text},
    )
    if box is None:
        return False
    vh = (page.viewport_size or {}).get("height", 0)
    return box["y"] >= 0 and box["y"] + box["h"] <= vh


@pytest.mark.parametrize("r01_page", [VIEWPORT_1024X640], indirect=True)
async def test_r01_dialog_1024x640_states_and_close(r01_page):
    """1024x640 基准：长内容滚动底部可达 + 加载态关闭按钮可达 + 物理点击关闭。"""
    nrp = NewsRiskPage(r01_page)
    ap = nrp.ap
    close_btn = EIDS.DETAIL_DIALOG.CLOSE_BUTTON

    await nrp.open_detail()  # 内部已断言 CLOSE_BUTTON 可见

    # ① 长内容（证据模式）：内容流最底部交互锚点（生成按钮）滚入视口
    await ap.scroll_into_view(EIDS.NEWS_RISK.GENERATE_BUTTON)
    await ap.expect_in_viewport(EIDS.NEWS_RISK.GENERATE_BUTTON)

    # ② 加载态（analyzing，mock DELAY_MS=1500）：actions 区关闭按钮完整在视口
    await nrp.generate()
    await nrp.wait_analyzing()
    await ap.expect_in_viewport(close_btn)

    # ③ ready 满配长内容：大步滚到底，断言风险摘要（内容流最后元素，无 anchor；
    #    ready 态其余锚点均为 LABEL 不可作滚动目标）进入视口（滚动底部可达）。
    await nrp.wait_ready(timeout_ms=TIMEOUTS.BACKTEST)
    await _scroll_dialog_content_to_bottom(nrp)
    summary_text = I18n.get("news_insight_summary")
    assert await _text_node_in_viewport(nrp, summary_text), (
        f"风险摘要文本 {summary_text!r} 未随滚动进入视口（内容底部不可达）"
    )

    # ④ 关闭按钮完整在视口 + 真实鼠标物理点击关闭成功
    await ap.expect_in_viewport(close_btn)
    await nrp.close_detail()
    await ap.expect_hidden(close_btn)


@pytest.mark.parametrize("r01_page", [VIEWPORT_1024X640], indirect=True)
async def test_r01_dialog_1024x640_keyboard_escape(r01_page):
    """1024x640 基准：键盘 Escape 关闭弹窗（on_dismiss 路径）。"""
    nrp = NewsRiskPage(r01_page)
    ap = nrp.ap
    close_btn = EIDS.DETAIL_DIALOG.CLOSE_BUTTON

    await nrp.open_detail()
    await r01_page.page.keyboard.press("Escape")
    await ap.expect_hidden(close_btn)


async def _open_detail_with_retry(nrp: NewsRiskPage, attempts: int = 2) -> None:
    """打开详情弹窗，前置链路（选策略/跑选股）偶发吞点时整体重试（幂等）。

    run2 实证：4 worker 并发共享 app 进程时 CanvasKit 渲染时序抖动，select_option
    偶发 "option not found"（1024x640 视口亦偶发）；重试前先 Escape 复位可能的
    残留弹窗。重选同策略 / 重跑选股均幂等。
    """
    last_exc: Exception | None = None
    for _attempt in range(1, attempts + 1):
        try:
            await nrp.open_detail()
            return
        except Exception as exc:  # noqa: BLE001  # 记录末次异常，耗尽后抛原始异常
            last_exc = exc
            await nrp.page.page.keyboard.press("Escape")  # 复位残留弹窗（若有）
            await nrp.page.page.wait_for_timeout(500)
    raise last_exc if last_exc is not None else RuntimeError("open_detail_with_retry: exhausted")


async def _assert_dialog_usable_after_resize(nrp: NewsRiskPage, width: int, height: int) -> None:
    """在 1024x640 打开弹窗后把视口压缩到 (width, height)，断言弹窗仍可操作。

    等价仿真口径：Windows DPI 缩放下物理屏幕受限时窗口逻辑视口收缩；弹窗打开
    状态下压缩视口直接驱动 Flutter 对已挂载 overlay（AlertDialog）按新视口
    重新施加 Dialog.insetPadding 钳制——验证「resize 后弹窗不溢出、可操作」。

    注：不直接以 (width, height) 作为初始视口——<1280 宽视口下选股前置链路
    （策略下拉 overlay 菜单被视口裁剪、选项语义节点不渲染，run1 实测）不可用，
    属 E2E 工具层限制而非弹窗缺陷；弹窗验证不依赖该前置。
    """
    ap = nrp.ap
    close_btn = EIDS.DETAIL_DIALOG.CLOSE_BUTTON

    await _open_detail_with_retry(nrp)
    await nrp.page.page.set_viewport_size({"width": width, "height": height})
    await nrp.page.page.wait_for_timeout(800)  # 等 Flutter 按新视口重排 overlay

    # 长内容：内容流最底部交互锚点（生成按钮）滚入压缩后的逻辑视口
    await ap.scroll_into_view(EIDS.NEWS_RISK.GENERATE_BUTTON)
    await ap.expect_in_viewport(EIDS.NEWS_RISK.GENERATE_BUTTON)

    # 关闭按钮（actions 区，非滚动内容）完整在视口内 + 物理点击真实关闭
    await ap.expect_in_viewport(close_btn)
    await nrp.close_detail()
    await ap.expect_hidden(close_btn)


@pytest.mark.parametrize("r01_page", [VIEWPORT_1024X640], indirect=True)
async def test_r01_dialog_resize_to_150pct(r01_page):
    """150% 仿真（683x427 逻辑视口）：1024x640 打开弹窗后压缩视口仍可操作。"""
    nrp = NewsRiskPage(r01_page)
    await _assert_dialog_usable_after_resize(nrp, *VIEWPORT_150PCT)


@pytest.mark.parametrize("r01_page", [VIEWPORT_1024X640], indirect=True)
async def test_r01_dialog_resize_to_200pct(r01_page):
    """200% 仿真（512x320 逻辑视口）：1024x640 打开弹窗后压缩视口仍可操作。"""
    nrp = NewsRiskPage(r01_page)
    await _assert_dialog_usable_after_resize(nrp, *VIEWPORT_200PCT)
