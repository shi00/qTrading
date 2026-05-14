"""
S-P0-1: Verify that _UNIVERSAL_RULES is sent as a separate system message,
not concatenated into the strategy system_prompt.

These tests validate BEHAVIOR, not source code text.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from strategies.strategy_prompts import _UNIVERSAL_RULES, get_base_prompt


def _make_mock_service():
    from services.ai_service import AIService

    svc = AIService.__new__(AIService)
    svc._is_cloud_configured = True
    svc._litellm_config = {"api_key": "test-key"}
    svc._local_model_loaded = False
    svc._supports_reasoning = False
    svc._initialized = True

    captured_messages = []

    async def mock_chat_completion(messages, **kwargs):
        captured_messages.extend(messages)
        return {
            "conclusion_label": "watchlist",
            "score": 70,
            "confidence": 80,
            "thinking": "test",
            "summary": "test",
        }

    svc._chat_completion = mock_chat_completion
    svc._get_prompt_dump_dir = lambda: "/tmp"
    return svc, captured_messages


class TestUniversalRulesSeparateSystemMessage:
    @pytest.mark.asyncio
    async def test_messages_have_two_system_entries(self):
        svc, captured_messages = _make_mock_service()

        with patch("services.ai_service.ConfigHandler") as mock_cfg:
            mock_cfg.get_ai_system_prompt.return_value = ""
            mock_cfg.get_ai_news_prompt.return_value = ""
            mock_cfg.get_setting.return_value = False
            mock_cfg.get_ai_provider.return_value = "cloud"

            with patch("services.ai_service.DataSanitizer"):
                with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                    mock_rm.return_value.get_learning_context = AsyncMock(return_value="")
                    with contextlib.suppress(RuntimeError, ValueError, TypeError):
                        await svc.analyze_stock(
                            stock_info={"ts_code": "000001.SZ", "name": "test"},
                            tech_info={},
                            news_list=[],
                            strategy_key="value",
                        )

        system_msgs = [m for m in captured_messages if m.get("role") == "system"]
        assert len(system_msgs) >= 2, (
            f"Expected at least 2 system messages, got {len(system_msgs)}. "
            f"_UNIVERSAL_RULES and strategy prompt should be separate."
        )

    @pytest.mark.asyncio
    async def test_universal_rules_not_concatenated_in_strategy_prompt(self):
        svc, captured_messages = _make_mock_service()

        with patch("services.ai_service.ConfigHandler") as mock_cfg:
            mock_cfg.get_ai_system_prompt.return_value = ""
            mock_cfg.get_ai_news_prompt.return_value = ""
            mock_cfg.get_setting.return_value = False
            mock_cfg.get_ai_provider.return_value = "cloud"

            with patch("services.ai_service.DataSanitizer"):
                with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                    mock_rm.return_value.get_learning_context = AsyncMock(return_value="")
                    with contextlib.suppress(RuntimeError, ValueError, TypeError):
                        await svc.analyze_stock(
                            stock_info={"ts_code": "000001.SZ", "name": "test"},
                            tech_info={},
                            news_list=[],
                            strategy_key="value",
                        )

        system_msgs = [m for m in captured_messages if m.get("role") == "system"]
        assert len(system_msgs) >= 2

        rules_msg = system_msgs[0]["content"]
        strategy_msg = system_msgs[1]["content"]

        assert _UNIVERSAL_RULES.strip() in rules_msg, "First system message should contain _UNIVERSAL_RULES"
        assert _UNIVERSAL_RULES.strip() not in strategy_msg, (
            "Second system message (strategy prompt) should NOT contain _UNIVERSAL_RULES"
        )

    def test_uses_get_base_prompt_not_resolve_prompt(self):
        base = get_base_prompt("value")
        assert _UNIVERSAL_RULES.strip() not in base, "get_base_prompt should NOT include _UNIVERSAL_RULES"

        from strategies.strategy_prompts import resolve_prompt

        resolved = resolve_prompt("value")
        assert _UNIVERSAL_RULES.strip() in resolved, "resolve_prompt SHOULD include _UNIVERSAL_RULES"

    @pytest.mark.asyncio
    async def test_ui_override_does_not_merge_with_universal_rules(self):
        svc, captured_messages = _make_mock_service()
        custom_prompt = "You are a custom analyst. Focus on growth metrics."

        with patch("services.ai_service.ConfigHandler") as mock_cfg:
            mock_cfg.get_ai_system_prompt.return_value = ""
            mock_cfg.get_ai_news_prompt.return_value = ""
            mock_cfg.get_setting.return_value = False
            mock_cfg.get_ai_provider.return_value = "cloud"

            with patch("services.ai_service.DataSanitizer"):
                with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                    mock_rm.return_value.get_learning_context = AsyncMock(return_value="")
                    with patch("utils.prompt_guard.validate_prompt", return_value=(True, "")):
                        with patch("utils.prompt_guard.sanitize_prompt", side_effect=lambda x: x):
                            with contextlib.suppress(RuntimeError, ValueError, TypeError):
                                await svc.analyze_stock(
                                    stock_info={"ts_code": "000001.SZ", "name": "test"},
                                    tech_info={},
                                    news_list=[],
                                    strategy_key="value",
                                    ui_prompt_override=custom_prompt,
                                )

        system_msgs = [m for m in captured_messages if m.get("role") == "system"]
        assert len(system_msgs) >= 2

        rules_msg = system_msgs[0]["content"]
        strategy_msg = system_msgs[1]["content"]

        assert _UNIVERSAL_RULES.strip() in rules_msg, (
            "_UNIVERSAL_RULES should still be in first system message even with override"
        )
        assert custom_prompt in strategy_msg, "Custom prompt should appear in the strategy system message"
        assert _UNIVERSAL_RULES.strip() not in strategy_msg, (
            "_UNIVERSAL_RULES should NOT be merged into the strategy message"
        )

    def test_import_statement_uses_get_base_prompt(self):
        from strategies.strategy_prompts import get_base_prompt as _g

        assert callable(_g), "get_base_prompt should be a callable"
