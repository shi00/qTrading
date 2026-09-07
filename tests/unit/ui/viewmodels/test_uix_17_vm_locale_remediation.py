"""Unit tests for UIX-17 VM locale remediation.

Verifies:
1. ScreenerViewModel: Cancelling retry uses i18n key 'screener_ai_incomplete' instead of hardcoded Chinese, and build_stream_card translates it.
2. EmbeddedStatusCardViewModel: Default state uses clean Message without hardcoded Chinese fallback dicts.
3. DataSourceViewModel: _classify_sync_error uses classify_error(context='sync') and code-driven dispatch without hardcoded Chinese strings.
"""

from unittest.mock import MagicMock
import pytest
import flet as ft

from core.i18n import I18n
from ui.viewmodels.embedded_status_card_view_model import EmbeddedStatusCardViewModel
from ui.viewmodels.data_source_view_model import DataSourceViewModel
from ui.viewmodels.screener_view_model import ScreenerViewModel, StreamCard
from ui.views.screener_view import build_stream_card


@pytest.fixture(autouse=True)
def init_i18n():
    I18n.initialize("zh_CN")


class TestUIX17ScreenerCardErrorLocale:
    """UIX-17: ScreenerViewModel placeholder card error i18n fallback."""

    def test_switch_strategy_during_retry_sets_i18n_key(self):
        """Switching strategy while retrying without prior error sets screener_ai_incomplete."""
        vm = ScreenerViewModel()
        vm.strategy_mgr = MagicMock()
        vm.strategy_mgr.get_strategy.return_value = None

        analyzing_card = StreamCard(name="茅台", content="", is_analyzing=True)
        vm._set_state(stream_cards=(analyzing_card,))
        vm._retrying = True
        vm._retrying_name = "茅台"
        vm._retrying_prev_error = None

        vm.select_strategy("new_strategy")

        assert vm.state.stream_cards[0].error == "screener_ai_incomplete"
        assert vm.state.stream_cards[0].is_analyzing is False

    def test_build_stream_card_translates_i18n_key(self):
        """build_stream_card translates screener_ai_incomplete according to locale."""
        card = StreamCard(name="茅台", content="", error="screener_ai_incomplete", is_analyzing=False)

        I18n.set_locale("zh_CN")
        card_control_zh = build_stream_card(card, on_retry=lambda _: None)
        col = card_control_zh.content
        text_node = col.controls[1]
        assert isinstance(text_node, ft.Text)
        assert text_node.value == "AI 分析未完成"

        I18n.set_locale("en_US")
        card_control_en = build_stream_card(card, on_retry=lambda _: None)
        col_en = card_control_en.content
        text_node_en = col_en.controls[1]
        assert isinstance(text_node_en, ft.Text)
        assert text_node_en.value == "AI analysis incomplete"

        # Arbitrary error is preserved as-is
        raw_card = StreamCard(name="茅台", content="", error="Custom error msg", is_analyzing=False)
        raw_control = build_stream_card(raw_card, on_retry=lambda _: None)
        assert raw_control.content.controls[1].value == "Custom error msg"


class TestUIX17EmbeddedStatusCardCleanMessage:
    """UIX-17: EmbeddedStatusCardViewModel default messages do not contain hardcoded Chinese default fallbacks."""

    def test_default_state_uses_clean_message_without_default_params(self):
        vm = EmbeddedStatusCardViewModel()
        state = vm.state

        assert state.status_message.key == "embedded_pg_ready"
        assert "default" not in state.status_message.params
        assert state.info_message.key == "embedded_pg_no_config_needed"
        assert "default" not in state.info_message.params


class TestUIX17DataSourceSyncErrorClassification:
    """UIX-17: DataSourceViewModel._classify_sync_error drives actions from classifier codes."""

    def test_quota_and_token_errors_return_probe_credits_action(self):
        vm = DataSourceViewModel()

        # Chinese quota error
        msg, action = vm._classify_sync_error(RuntimeError("API调用失败: 积分不足"))
        assert action == "snack_action_probe_credits"
        assert msg.key == "llm_err_insufficient_quota"

        # English quota error
        msg, action = vm._classify_sync_error(RuntimeError("HTTP 402: insufficient_quota"))
        assert action == "snack_action_probe_credits"
        assert msg.key == "llm_err_insufficient_quota"

        # Token invalid error
        msg, action = vm._classify_sync_error(RuntimeError("403 Forbidden: Invalid token"))
        assert action == "snack_action_probe_credits"
        assert msg.key == "wizard_err_token_invalid"

    def test_db_and_network_errors_return_check_health_action(self):
        vm = DataSourceViewModel()

        # Chinese DB error
        msg, action = vm._classify_sync_error(RuntimeError("数据库连接失败"))
        assert action == "snack_action_check_health"

        # English DB error
        msg, action = vm._classify_sync_error(RuntimeError("asyncpg connection refused"))
        assert action == "snack_action_check_health"

        # Network error
        msg, action = vm._classify_sync_error(RuntimeError("Connection timed out"))
        assert action == "snack_action_check_health"
        assert msg.key in ("common_err_network", "common_err_timeout")

    def test_unknown_error_falls_back_to_common_op_fail(self):
        vm = DataSourceViewModel()
        msg, action = vm._classify_sync_error(RuntimeError("Some completely unexpected error"))
        assert action is None
        assert msg.key == "common_op_fail"
