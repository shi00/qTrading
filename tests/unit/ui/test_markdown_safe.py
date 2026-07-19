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


class TestSafeOpenUrlToast:
    """SEC-010: 非白名单链接应 toast 提示"链接已拦截"。"""

    def _make_event_with_page(self, url):
        e = MagicMock()
        e.data = url
        e.control = MagicMock()
        e.control.page = MagicMock()
        return e

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_non_whitelisted_url_shows_toast(self, mock_open):
        e = self._make_event_with_page("https://evil.com/phish")
        safe_open_url(e)
        mock_open.assert_not_called()
        # 应通过 page.show_toast 显示拦截提示
        assert e.control.page.show_toast.called

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_non_whitelisted_url_toast_uses_i18n_key(self, mock_open):
        """P3-25: toast 文案应经 I18n.get('markdown_link_blocked') 国际化。"""
        e = self._make_event_with_page("https://evil.com/phish")
        with patch("ui.components._markdown_safe.I18n") as mock_i18n:
            mock_i18n.get.return_value = "Link blocked"
            safe_open_url(e)
            mock_open.assert_not_called()
            mock_i18n.get.assert_called_once_with("markdown_link_blocked")
            e.control.page.show_toast.assert_called_once_with("Link blocked", type="error")

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_whitelisted_url_does_not_show_toast(self, mock_open):
        e = self._make_event_with_page("https://eastmoney.com/stock")
        safe_open_url(e)
        mock_open.assert_called_once_with("https://eastmoney.com/stock")
        # 白名单链接不应触发 toast
        assert not e.control.page.show_toast.called

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_non_whitelisted_url_without_page_falls_back_to_log(self, mock_open):
        e = MagicMock()
        e.data = "https://evil.com/phish"
        e.control = None
        e.page = None
        safe_open_url(e)
        mock_open.assert_not_called()

    @patch("ui.components._markdown_safe.webbrowser.open")
    def test_toast_exception_falls_back_to_log(self, mock_open):
        """show_toast 抛异常时降级为 logger.warning。"""
        e = self._make_event_with_page("https://evil.com/phish")
        e.control.page.show_toast.side_effect = RuntimeError("page closed")
        with patch("ui.components._markdown_safe.logger") as mock_logger:
            safe_open_url(e)
            mock_open.assert_not_called()
            mock_logger.warning.assert_called()
