"""SEC-010: Tests for safe_open_url whitelist enforcement on ft.Markdown on_tap_link."""

from unittest.mock import MagicMock, patch

from ui.components._markdown_safe import (
    ALLOWED_DOMAINS,
    _is_allowed_domain,
    safe_open_url,
)
import pytest


pytestmark = pytest.mark.unit


class TestAllowedDomains:
    def test_contains_expected_domains(self):
        assert "eastmoney.com" in ALLOWED_DOMAINS
        assert "sina.com.cn" in ALLOWED_DOMAINS
        assert "tushare.pro" in ALLOWED_DOMAINS


class TestIsAllowedDomain:
    def test_eastmoney_exact_match(self):
        assert _is_allowed_domain("https://eastmoney.com/stock") is True

    def test_eastmoney_subdomain(self):
        assert _is_allowed_domain("https://finance.eastmoney.com/stock") is True

    def test_sina_subdomain(self):
        assert _is_allowed_domain("https://finance.sina.com.cn/news") is True

    def test_tushare_pro(self):
        assert _is_allowed_domain("https://tushare.pro/document") is True

    def test_non_whitelisted_domain(self):
        assert _is_allowed_domain("https://evil.com/phish") is False

    def test_lookalike_domain_rejected(self):
        # noteastmoney.com should NOT match eastmoney.com
        assert _is_allowed_domain("https://noteastmoney.com") is False

    def test_suffix_lookalike_rejected(self):
        # "fakeeastmoney.com" does not end with ".eastmoney.com"
        assert _is_allowed_domain("https://fakeeastmoney.com") is False

    def test_invalid_url(self):
        assert _is_allowed_domain("not a url") is False

    def test_empty_url(self):
        assert _is_allowed_domain("") is False

    def test_no_hostname(self):
        assert _is_allowed_domain("/relative/path") is False

    def test_uppercase_hostname_matched(self):
        assert _is_allowed_domain("https://EASTMONEY.COM/stock") is True


class TestSafeOpenUrl:
    def _make_event(self, url):
        e = MagicMock()
        e.data = url
        return e

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_whitelisted_url_opened(self, mock_open):
        url = "https://finance.eastmoney.com/stock/000001"
        safe_open_url(self._make_event(url))
        mock_open.assert_called_once_with(url)

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_non_whitelisted_url_not_opened(self, mock_open):
        safe_open_url(self._make_event("https://evil.com/phish"))
        mock_open.assert_not_called()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_empty_data_does_nothing(self, mock_open):
        e = MagicMock()
        e.data = ""
        safe_open_url(e)
        mock_open.assert_not_called()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_missing_data_attr_does_nothing(self, mock_open):
        class EmptyEvent:
            pass

        safe_open_url(EmptyEvent())
        mock_open.assert_not_called()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_all_three_whitelist_domains_opened(self, mock_open):
        for url in (
            "https://eastmoney.com/a",
            "https://sina.com.cn/b",
            "https://tushare.pro/c",
        ):
            mock_open.reset_mock()
            safe_open_url(self._make_event(url))
            mock_open.assert_called_once_with(url)


class TestSafeOpenUrlSnackBar:
    """SEC-010: 非白名单链接应弹窗提示"链接已拦截"。"""

    def _make_event_with_page(self, url):
        e = MagicMock()
        e.data = url
        e.control = MagicMock()
        e.control.page = MagicMock()
        e.control.page.overlay = []
        return e

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_non_whitelisted_url_shows_snack_bar(self, mock_open):
        e = self._make_event_with_page("https://evil.com/phish")
        safe_open_url(e)
        mock_open.assert_not_called()
        # SnackBar 应被添加到 page.overlay
        assert len(e.control.page.overlay) == 1
        # page.update 应被调用以渲染 SnackBar
        e.control.page.update.assert_called_once()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_whitelisted_url_does_not_show_snack_bar(self, mock_open):
        e = self._make_event_with_page("https://eastmoney.com/stock")
        safe_open_url(e)
        mock_open.assert_called_once_with("https://eastmoney.com/stock")
        # 白名单链接不应触发 SnackBar
        assert len(e.control.page.overlay) == 0
        e.control.page.update.assert_not_called()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_non_whitelisted_url_without_page_falls_back_to_log(self, mock_open):
        e = MagicMock()
        e.data = "https://evil.com/phish"
        e.control = None
        e.page = None
        safe_open_url(e)
        mock_open.assert_not_called()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_snack_bar_exception_falls_back_to_log(self, mock_open):
        e = self._make_event_with_page("https://evil.com/phish")
        e.control.page.update.side_effect = RuntimeError("page closed")
        safe_open_url(e)
        mock_open.assert_not_called()
