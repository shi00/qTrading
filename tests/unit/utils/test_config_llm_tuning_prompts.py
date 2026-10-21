"""save_ai_tuning_and_prompts (T3) 单元测试。

验证 AI 调优参数与 prompts 的单次原子保存契约：
- 全成功：单次 save_config，payload 五键齐全，prompt 经 sanitize 后写入
- prompt 校验失败：返回 False 且 save_config 未被调用（整体未提交，含调优参数前键）
- save_config 失败（磁盘损坏/安全子域损坏 fail-closed）：透传 False，整体未提交
- 空 prompt：不触发校验、原样写入（与 save_ai_system_prompt / set_ai_news_prompt 语义一致）
"""

from unittest.mock import patch

import pytest

import utils.config.llm as llm_mod

pytestmark = pytest.mark.unit


class TestSaveAiTuningAndPrompts:
    """utils.config.llm.save_ai_tuning_and_prompts 单次原子保存契约 (T3)。"""

    def test_all_success_single_save(self):
        """三组配置全成功：单次 save_config 携带五键，prompt 经 sanitize。"""
        with (
            patch.object(llm_mod.cfg.ConfigHandler, "save_config", return_value=True) as mock_save,
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="sanitized-prompt") as mock_sanitize,
        ):
            result = llm_mod.save_ai_tuning_and_prompts(30, 2.0, False, "my prompt", "news prompt")

        assert result is True
        mock_save.assert_called_once_with(
            {
                "ai_max_candidates": 30,
                "strategy_min_turnover": 2.0,
                "ai_local_only_mode": False,
                "ai_system_prompt": "sanitized-prompt",
                "ai_news_prompt": "sanitized-prompt",
            }
        )
        assert mock_sanitize.call_count == 2

    def test_ai_prompt_invalid_no_write_at_all(self):
        """ai_prompt 校验失败：返回 False 且 save_config 未调用（整体未提交，含调优参数前键）。"""
        with (
            patch.object(llm_mod.cfg.ConfigHandler, "save_config", return_value=True) as mock_save,
            patch("utils.prompt_guard.validate_prompt", return_value=(False, "prompt_err_injection")),
        ):
            result = llm_mod.save_ai_tuning_and_prompts(30, 2.0, False, "bad prompt", "ok prompt")

        assert result is False
        mock_save.assert_not_called()

    def test_news_prompt_invalid_no_write_at_all(self):
        """news_prompt 校验失败：同样整体未提交（save_config 未调用）。"""
        with (
            patch.object(llm_mod.cfg.ConfigHandler, "save_config", return_value=True) as mock_save,
            patch(
                "utils.prompt_guard.validate_prompt",
                side_effect=[(True, ""), (False, "prompt_err_length")],
            ),
        ):
            result = llm_mod.save_ai_tuning_and_prompts(30, 2.0, True, "ok prompt", "bad prompt")

        assert result is False
        mock_save.assert_not_called()

    def test_save_config_failure_propagates_false(self):
        """save_config 失败（磁盘损坏 fail-closed）：透传 False，整体未提交。"""
        with (
            patch.object(llm_mod.cfg.ConfigHandler, "save_config", return_value=False) as mock_save,
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", side_effect=lambda p: p),
        ):
            result = llm_mod.save_ai_tuning_and_prompts(30, 2.0, False, "p", "n")

        assert result is False
        mock_save.assert_called_once_with(
            {
                "ai_max_candidates": 30,
                "strategy_min_turnover": 2.0,
                "ai_local_only_mode": False,
                "ai_system_prompt": "p",
                "ai_news_prompt": "n",
            }
        )

    def test_empty_prompts_written_as_is(self):
        """空 prompt 不触发校验、原样写入（与单键函数空值语义一致）。"""
        with (
            patch.object(llm_mod.cfg.ConfigHandler, "save_config", return_value=True) as mock_save,
            patch("utils.prompt_guard.validate_prompt") as mock_validate,
        ):
            result = llm_mod.save_ai_tuning_and_prompts(30, 2.0, False, "", "")

        assert result is True
        mock_validate.assert_not_called()
        payload = mock_save.call_args.args[0]
        assert payload["ai_system_prompt"] == ""
        assert payload["ai_news_prompt"] == ""


class TestConfigHandlerDelegation:
    """ConfigHandler.save_ai_tuning_and_prompts 委托 utils.config.llm 同名函数。"""

    def test_delegates_to_llm_module(self):
        from utils.config_handler import ConfigHandler

        with patch.object(llm_mod, "save_ai_tuning_and_prompts", return_value=True) as mock_delegate:
            result = ConfigHandler.save_ai_tuning_and_prompts(30, 2.0, False, "p", "n")

        assert result is True
        mock_delegate.assert_called_once_with(30, 2.0, False, "p", "n")
