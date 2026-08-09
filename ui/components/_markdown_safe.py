"""SEC-010: Safe URL opening for ft.Markdown ``on_tap_link`` callbacks.

LLM 生成的 Markdown 内容可能包含任意链接，用户点击后若直接打开会带来
钓鱼/恶意站点风险。本模块提供 ``safe_open_url`` 作为 ``ft.Markdown``
的 ``on_tap_link`` 回调，仅放行金融数据相关的白名单域名。
"""

import logging
import webbrowser
from urllib.parse import urlparse

from ui.i18n import I18n
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

# 白名单域名：金融数据相关站点，hostname 以这些域名结尾（含子域名）即放行。
ALLOWED_DOMAINS: tuple[str, ...] = ("eastmoney.com", "sina.com.cn", "tushare.pro")


def _is_allowed_domain(url: str) -> bool:
    """检查 URL 的 hostname 是否在白名单中（子域名也算）。"""
    try:
        hostname = urlparse(url).hostname
    except Exception as e:
        logger.debug("[MarkdownSafe] urlparse failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)
        return False
    if not hostname:
        return False
    hostname = hostname.lower()
    return hostname in ALLOWED_DOMAINS or any(hostname.endswith("." + allowed) for allowed in ALLOWED_DOMAINS)


def _show_blocked_toast(page) -> None:
    """在给定 page 上显示"链接已拦截"提示。

    CLAUDE.md §3.2 声明式 UI: 用 ``page.show_toast`` 替代 ``page.show_dialog(ft.SnackBar)``
    (main.py:251 动态挂载 show_toast).
    P3-25: 文案经 ``I18n.get("markdown_link_blocked")`` 国际化（en_US 下显示 "Link blocked"）。
    """
    if hasattr(page, "show_toast"):
        page.show_toast(I18n.get("markdown_link_blocked"), type="error")  # type: ignore[untyped]  # [reason: main.py 动态挂载, ft.Page 存根未声明]
    else:
        logger.warning("[MarkdownSafe] Blocked non-whitelisted URL (toast unavailable)")


def safe_open_url(e) -> None:
    """``ft.Markdown`` 的 ``on_tap_link`` 回调：仅打开白名单域名的链接。

    R16: ``webbrowser.open`` 是同步阻塞 IO，白名单 URL 通过 ``page.run_task``
    调度异步包装函数 ``_open_url_async``，由 ThreadPoolManager offload 到 IO 线程池。
    无 page 访问时降级为同步调用并 logger.warning（向后兼容，测试场景）。

    非白名单链接会通过 toast 提示"链接已拦截"；若事件对象无法访问
    page，降级为 ``logger.warning``。

    Args:
        e: flet ControlEvent，``e.data`` 为被点击的 URL 字符串。
    """
    url = getattr(e, "data", "") or ""
    if not url:
        return
    if not _is_allowed_domain(url):
        # 非白名单：优先弹窗提示，降级为日志
        # 项目约定通过 e.control.page 访问 page（见 backtest_config_panel.py）
        page = None
        control = getattr(e, "control", None)
        if control is not None:
            page = getattr(control, "page", None)
        if page is None:
            page = getattr(e, "page", None)
        if page is not None:
            try:
                _show_blocked_toast(page)
            except Exception as exc:
                logger.warning(
                    "[MarkdownSafe] Failed to show toast: %s", DataSanitizer.sanitize_error(exc), exc_info=True
                )
                logger.warning("[MarkdownSafe] Blocked non-whitelisted URL: %s", url)
        else:
            logger.warning("[MarkdownSafe] Blocked non-whitelisted URL: %s", url)
        return

    # 白名单 URL：通过 page.run_task 调度异步打开（R16 合规）
    page = None
    control = getattr(e, "control", None)
    if control is not None:
        page = getattr(control, "page", None)
    if page is None:
        page = getattr(e, "page", None)
    if page is not None and hasattr(page, "run_task"):
        page.run_task(_open_url_async, url)
    else:
        # 无 page 访问（测试场景或异常时态）：降级为同步调用并警告
        logger.warning("[MarkdownSafe] page.run_task unavailable, fallback to sync webbrowser.open")
        webbrowser.open(url)


async def _open_url_async(url: str) -> None:
    """异步打开 URL（R16: webbrowser.open 通过 ThreadPoolManager offload）。

    由 ``safe_open_url`` 经 ``page.run_task`` 调度，在 IO 线程池执行
    ``webbrowser.open``，避免阻塞 Flet 主循环。

    Raises:
        asyncio.CancelledError: R2 透传（由 ``await`` 自动传播）
    """
    await ThreadPoolManager().run_async(TaskType.IO, webbrowser.open, url)
