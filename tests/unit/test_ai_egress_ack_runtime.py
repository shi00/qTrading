"""SEC-01 gap3：运行时 AI 外发确认（真实 prompt 预览 + 同 loop 桥接）的 R19 专项测试。

覆盖三条业务路径：
1. 预览构造：``_build_egress_prompt_preview`` / ``_safe_preview_field`` —— 脱敏、首股
   样例、字段缺失/空表容错。
2. 策略层 guard 运行时确认：注入 ``on_ai_egress_ack_request`` 回调后的 confirm /
   decline / exception-fallback 三分支。
3.（见 test_screener_view_model.py TestEgressAckBridge）VM 桥接 Future 生命周期。

安全语义（SEC-01 gap3 方案结论）：未经用户运行时确认绝不发起云端请求；确认通过后
持久化当前 provider+scope 版本；decline / 异常均回落为 policy_not_acknowledged 跳过。
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.i18n import Message
from strategies.ai_mixin import (
    AIStrategyMixin,
    _build_egress_prompt_preview,
    _safe_preview_field,
)

pytestmark = pytest.mark.unit


def _make_mock_dp() -> MagicMock:
    dp = MagicMock()
    dp.cache = MagicMock()
    dp.cache.get_concepts = AsyncMock(return_value={})
    dp.quote_dao = MagicMock()
    return dp


class _ConcreteStrategy(AIStrategyMixin):
    key = "test_egress_ack_strategy"

    def __init__(self) -> None:
        super().__init__()

    def get_ai_context(self, row):
        return f"Test context for {row.get('ts_code', '?')}"


def _candidates() -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"], "close": [10.5]})


# --- 预览构造 ---


class TestEgressPromptPreview:
    def test_builds_preview_with_header_and_sample(self):
        preview = _build_egress_prompt_preview(_candidates(), {})
        assert isinstance(preview, str)
        assert preview != ""
        # 首股样例与数据类别摘要均已呈现（i18n 默认文案 + 首股名）
        assert "平安银行" in preview
        assert "000001.SZ" in preview

    def test_empty_df_returns_empty(self):
        empty = pd.DataFrame(columns=["ts_code", "name", "close"])
        assert _build_egress_prompt_preview(empty, {}) == ""
        assert _build_egress_prompt_preview(None, {}) == ""

    def test_safe_preview_field_missing_fields_graceful(self):
        row = pd.Series({"ts_code": "600000.SH"})
        out = _safe_preview_field(row)
        # name 缺失回落 ts_code；close/pct_chg 缺失不拼接
        assert out != ""
        assert "600000.SH" in out
        assert "close=" not in out

    def test_safe_preview_field_returns_string(self):
        row = pd.Series({"ts_code": "000001.SZ", "name": "平安银行", "close": 10.5, "pct_chg": 1.2})
        parts = _safe_preview_field(row)
        assert isinstance(parts, str)
        assert "close=" in parts
        assert "pct_chg=" in parts


# --- 策略层 guard 运行时确认三分支 ---


class TestRuntimeEgressAckGuard:
    async def _run(self, ack_result, *, ack_raises=False):
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        on_progress = MagicMock()

        # 单一 ack_request 实现：ack_raises 时为异常分支，否则返回 ack_result（pyright 无重复声明）
        async def ack_request(_preview, _provider):
            if ack_raises:
                raise RuntimeError("ack bridge broke")
            return ack_result

        context = {
            "data_processor": dp,
            "on_progress": on_progress,
            "on_ai_egress_ack_request": ack_request,
        }

        with (
            patch("strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged", return_value=False),
            patch("strategies.ai_mixin.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch("strategies.ai_mixin.ConfigHandler.set_ai_external_acknowledged") as mock_set,
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news") as mock_news,
            patch("strategies.ai_mixin.AIService") as mock_ai,
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock(return_value={"score": 80, "summary": "good", "decision": "Buy"})
            mock_ai.return_value = mock_ai_instance
            mock_news.get_stock_news = AsyncMock()
            result = await s.run_ai_analysis(_candidates(), context)

        return result, on_progress, mock_set, mock_ai_instance

    @pytest.mark.asyncio
    async def test_decline_skips_ai_without_persisting(self):
        """用户拒绝 → 立即跳过、ai_status=policy_not_acknowledged、不持久化确认、不发云端请求。"""
        result, on_progress, mock_set, mock_ai_instance = await self._run(False)
        assert result.iloc[0]["ai_score"] is None
        assert result.iloc[0]["ai_status"] == "policy_not_acknowledged"
        mock_set.assert_not_called()
        mock_ai_instance.analyze_stock.assert_not_called()
        declined = any(
            len(call.args) >= 3
            and isinstance(call.args[2], Message)
            and call.args[2].key == "ai_external_acknowledgment_declined"
            for call in on_progress.call_args_list
        )
        assert declined

    @pytest.mark.asyncio
    async def test_confirm_persists_and_continues_analysis(self):
        """用户同意 → 持久化当前 provider 确认（acknowledged=True）并继续走云端分析。"""
        result, _on_progress, mock_set, _ai_inst = await self._run(True)
        call = mock_set.call_args_list[0]  # 空则 IndexError → 隐含证明被调用
        assert call[1]["provider"] == "deepseek"
        assert call[1]["acknowledged"] is True
        # 已继续分析：结果不再处于 policy_not_acknowledged 跳过态
        assert "ai_status" not in result.columns or (result["ai_status"] != "policy_not_acknowledged").all()

    @pytest.mark.asyncio
    async def test_ack_request_exception_falls_back_to_skip(self, caplog):
        """确认回调自身异常 → log_classified 降级为跳过，不持久化、不发云端请求（R2 感知取消）。"""
        with caplog.at_level(logging.ERROR, logger="strategies.ai_mixin"):
            result, on_progress, mock_set, mock_ai_instance = await self._run(None, ack_raises=True)
        assert result.iloc[0]["ai_status"] == "policy_not_acknowledged"
        mock_set.assert_not_called()
        mock_ai_instance.analyze_stock.assert_not_called()
        assert "Runtime egress ack request failed" in caplog.text

    @pytest.mark.asyncio
    async def test_confirm_cancelled_error_propagates(self):
        """确认回调抛 CancelledError 必须透传（R2），不得吞没后误判为跳过。"""
        s = _ConcreteStrategy()
        dp = _make_mock_dp()

        async def ack_request(_preview, _provider):
            raise asyncio.CancelledError()

        context = {"data_processor": dp, "on_ai_egress_ack_request": ack_request}
        with (
            patch("strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged", return_value=False),
            patch("strategies.ai_mixin.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch("strategies.ai_mixin.ConfigHandler.set_ai_external_acknowledged") as mock_set,
            patch("strategies.ai_mixin.AIService") as mock_ai,
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion 取消透传本身即 R2 断言；下行验未持久化
                await s.run_ai_analysis(_candidates(), context)
            # 取消透传：确认回调从未被持久化触发
            assert not mock_set.called
