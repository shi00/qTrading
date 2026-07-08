"""SEC-010: Safe URL opening for ft.Markdown ``on_tap_link`` callbacks.

LLM 生成的 Markdown 内容可能包含任意链接，用户点击后若直接打开会带来
钓鱼/恶意站点风险。本模块提供 ``safe_open_url`` 作为 ``ft.Markdown``
的 ``on_tap_link`` 回调，仅放行金融数据相关的白名单域名。
"""

import logging
import webbrowser
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 白名单域名：金融数据相关站点，hostname 以这些域名结尾（含子域名）即放行。
ALLOWED_DOMAINS: tuple[str, ...] = ("eastmoney.com", "sina.com.cn", "tushare.pro")


def _is_allowed_domain(url: str) -> bool:
    """检查 URL 的 hostname 是否在白名单中（子域名也算）。"""
    try:
        hostname = urlparse(url).hostname
    except Exception as e:
        logger.debug("[MarkdownSafe] urlparse failed: %s", e, exc_info=True)
        return False
    if not hostname:
        return False
    hostname = hostname.lower()
    return hostname in ALLOWED_DOMAINS or any(hostname.endswith("." + allowed) for allowed in ALLOWED_DOMAINS)


def _show_blocked_snack_bar(page) -> None:
    """在给定 page 上显示"链接已拦截"提示条。"""
    import flet as ft

    snack = ft.SnackBar(ft.Text("链接已拦截"), bgcolor="#d32f2f")
    page.show_dialog(snack)


def safe_open_url(e) -> None:
    """``ft.Markdown`` 的 ``on_tap_link`` 回调：仅打开白名单域名的链接。

    非白名单链接会通过 SnackBar 提示"链接已拦截"；若事件对象无法访问
    page，降级为 ``logger.warning``。

    Args:
        e: flet ControlEvent，``e.data`` 为被点击的 URL 字符串。
    """
    url = getattr(e, "data", "") or ""
    if not url:
        return
    if _is_allowed_domain(url):
        webbrowser.open(url)
        return
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
            _show_blocked_snack_bar(page)
        except Exception as exc:
            logger.warning("[MarkdownSafe] Failed to show snack bar: %s", exc, exc_info=True)
            logger.warning("[MarkdownSafe] Blocked non-whitelisted URL: %s", url)
    else:
        logger.warning("[MarkdownSafe] Blocked non-whitelisted URL: %s", url)
