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

from core.errors import AIPolicyNotAcknowledgedError
from core.i18n import Message
from services.ai_service.litellm_client import LiteLLMClient
from strategies.ai_mixin import (
    AIStrategyMixin,
    _build_egress_prompt_preview,
    _safe_preview_field,
)
from utils.egress_ack import collect_cloud_ack_providers, is_egress_acknowledged

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

    def test_preview_includes_providers_when_given(self):
        """SEC-01 复核修复: 传入 providers 时，预览头部展示主 + fallback 云端供应商集合。"""
        preview = _build_egress_prompt_preview(_candidates(), {}, providers=["deepseek", "qwen"])
        assert "deepseek" in preview
        assert "qwen" in preview

    def test_preview_unchanged_without_providers(self):
        """不传 providers 时行为不变（既有确认对话框文案兼容）。"""
        with_providers = _build_egress_prompt_preview(_candidates(), {}, providers=["deepseek"])
        without = _build_egress_prompt_preview(_candidates(), {})
        # providers 行是新增的独立段落，其余结构保持
        assert "deepseek" in with_providers
        assert "deepseek" not in without


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
            # 固定 failover 配置为空，保证 ack 对象 = 仅主 provider（测试确定性，不依赖真实配置）
            patch(
                "strategies.ai_mixin.ConfigHandler.get_failover_config",
                return_value={"primary": "deepseek/deepseek-chat", "fallbacks": []},
            ),
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
            patch(
                "strategies.ai_mixin.ConfigHandler.get_failover_config",
                return_value={"primary": "deepseek/deepseek-chat", "fallbacks": []},
            ),
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


# --- SEC-01 复核修复: failover 确认对象收集 ---


class TestCollectCloudAckProviders:
    @pytest.mark.parametrize(
        ("fallbacks", "expected"),
        [
            ([], ["deepseek"]),  # 无 failover → 仅主 provider
            (["qwen/qwen-max"], ["deepseek", "qwen"]),  # "/" 前缀 fallback → 追加
            (["qwen-max"], ["deepseek"]),  # 裸 model 名（主 provider 内部模型）→ 忽略
            (["qwen/qwen-max", "qwen/qwen-turbo"], ["deepseek", "qwen"]),  # 同 provider 去重
            (["deepseek/deepseek-r1"], ["deepseek"]),  # 与主 provider 同源 → 去重
        ],
    )
    def test_collects_ack_providers(self, fallbacks, expected):
        """failover 配置 → 收集主 + 不同云端 fallback provider，与 AIService 凭证预载语义一致。"""
        with patch(
            "utils.egress_ack.ConfigHandler.get_failover_config",
            return_value={"primary": "deepseek/deepseek-chat", "fallbacks": fallbacks},
        ):
            assert collect_cloud_ack_providers("deepseek") == expected

    def test_exception_falls_back_to_primary_only(self, caplog):
        """failover 配置读取异常 → 降级为仅主 provider（与 AIService 预载降级一致，不吞没主路径）。"""
        with patch(
            "utils.egress_ack.ConfigHandler.get_failover_config",
            side_effect=RuntimeError("config corrupt"),
        ):
            with caplog.at_level(logging.DEBUG, logger="utils.egress_ack"):
                assert collect_cloud_ack_providers("deepseek") == ["deepseek"]
        assert "failover config unavailable" in caplog.text


class TestRuntimeEgressAckFailover:
    """SEC-01 复核修复: failover 场景下确认对象 = 主 + fallback 全部 provider（授权语义不漂移）。"""

    async def _run_with_failover(self, *, fallbacks, ack_result=True):
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        on_progress = MagicMock()

        async def ack_request(_preview, _provider):
            return ack_result

        context = {
            "data_processor": dp,
            "on_progress": on_progress,
            "on_ai_egress_ack_request": ack_request,
        }
        with (
            patch("strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged", return_value=False),
            patch("strategies.ai_mixin.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch(
                "strategies.ai_mixin.ConfigHandler.get_failover_config",
                return_value={"primary": "deepseek/deepseek-chat", "fallbacks": fallbacks},
            ),
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

        return result, mock_set

    @pytest.mark.asyncio
    async def test_confirm_persists_all_failover_providers(self):
        """用户同意 → 主 + fallback 全部 provider 持久化为已确认，避免 fallback 触发未授权外发。"""
        result, mock_set = await self._run_with_failover(fallbacks=["qwen/qwen-max"])
        set_calls = {c[1]["provider"] for c in mock_set.call_args_list}
        assert set_calls == {"deepseek", "qwen"}
        assert all(c[1]["acknowledged"] is True for c in mock_set.call_args_list)
        # 已继续分析：结果不再处于 policy_not_acknowledged 跳过态
        assert "ai_status" not in result.columns or (result["ai_status"] != "policy_not_acknowledged").all()

    @pytest.mark.asyncio
    async def test_decline_persists_nothing_with_failover(self):
        """failover 存在但用户拒绝 → 任一 provider 均不持久化、跳过 AI。"""
        result, mock_set = await self._run_with_failover(fallbacks=["qwen/qwen-max"], ack_result=False)
        assert result.iloc[0]["ai_status"] == "policy_not_acknowledged"
        mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_unacknowledged_fallback_triggers_ack_with_providers_preview(self):
        """fallback 未确认 → 仍触发运行时确认，且预览中展示主 + fallback provider 集合（知情权）。"""
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        seen = {}

        async def ack_request(preview, provider):
            seen["preview"] = preview
            seen["provider"] = provider
            return True

        context = {"data_processor": dp, "on_ai_egress_ack_request": ack_request}
        with (
            patch("strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged", return_value=False),
            patch("strategies.ai_mixin.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch(
                "strategies.ai_mixin.ConfigHandler.get_failover_config",
                return_value={"primary": "deepseek/deepseek-chat", "fallbacks": ["qwen/qwen-max"]},
            ),
            patch("strategies.ai_mixin.ConfigHandler.set_ai_external_acknowledged"),
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news") as mock_news,
            patch("strategies.ai_mixin.AIService") as mock_ai,
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock(return_value={"score": 80, "summary": "good", "decision": "Buy"})
            mock_ai.return_value = mock_ai_instance
            mock_news.get_stock_news = AsyncMock()
            await s.run_ai_analysis(_candidates(), context)

        # 回调收到主 provider 名 + 包含全部确认对象的预览文本
        assert seen["provider"] == "deepseek"
        assert "deepseek" in seen["preview"]
        assert "qwen" in seen["preview"]


# --- SEC-01 集中出口门控（utils.egress_ack 统一判定 + LiteLLMClient 云端出口） ---


class TestCentralizedEgressAckGate:
    """SEC-01 gap3 补全：utils.egress_ack 统一判定确认对象集合是否全部已确认。"""

    def test_all_providers_acknowledged_returns_true(self):
        """主 + 全部跨 provider failover 都已确认 → 允许云端外发。"""
        with (
            patch("utils.egress_ack.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch(
                "utils.egress_ack.ConfigHandler.get_failover_config",
                return_value={"primary": "deepseek/deepseek-chat", "fallbacks": ["qwen/qwen-max"]},
            ),
            patch("utils.egress_ack.ConfigHandler.is_ai_external_acknowledged") as mock_ack,
        ):
            mock_ack.side_effect = lambda provider: provider in ["deepseek", "qwen"]
            assert is_egress_acknowledged() is True

    def test_unacknowledged_fallback_returns_false(self):
        """任一确认对象未确认（fallback 未授权）→ 禁止云端外发，避免授权语义漂移。"""
        with (
            patch("utils.egress_ack.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch(
                "utils.egress_ack.ConfigHandler.get_failover_config",
                return_value={"primary": "deepseek/deepseek-chat", "fallbacks": ["qwen/qwen-max"]},
            ),
            patch("utils.egress_ack.ConfigHandler.is_ai_external_acknowledged") as mock_ack,
        ):
            mock_ack.side_effect = lambda provider: provider == "deepseek"
            assert is_egress_acknowledged() is False


def _make_litellm_client() -> LiteLLMClient:
    """构造已配置云端可用的 LiteLLMClient（门控测试聚焦门控行为，非真实 IO）。"""
    svc = MagicMock()
    svc.is_cloud_available.return_value = True
    return LiteLLMClient(svc)


class TestLiteLLMClientEgressGate:
    """SEC-01 gap3 补全：services 层（news / web_search 出口）门控在未确认时阻断外发。"""

    @pytest.mark.asyncio
    async def test_news_cloud_unack_raises(self):
        """新闻分类云端出口未确认 → 抛 AIPolicyNotAcknowledgedError，绝不发起外部请求。"""
        client = _make_litellm_client()
        client._service._get_news_semaphore.return_value = asyncio.Semaphore(1)
        with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=False):
            with pytest.raises(AIPolicyNotAcknowledgedError) as exc_info:
                await client._chat_completion(
                    messages=[{"role": "user", "content": "分类这支新闻"}],
                    provider="cloud",
                    purpose="news",
                    json_mode=True,
                )
        # 门控拒绝语义：异常携带外发确认提示 i18n key，供表现层翻译
        assert exc_info.value.message.key == "ai_external_acknowledgment_prompt"

    @pytest.mark.asyncio
    async def test_web_search_unack_raises(self):
        """概念同步/网页搜索云端出口未确认 → 抛 AIPolicyNotAcknowledgedError。"""
        client = _make_litellm_client()
        with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=False):
            with pytest.raises(AIPolicyNotAcknowledgedError) as exc_info:
                await client.chat_with_web_search(messages=[{"role": "user", "content": "web"}])
        # 门控拒绝语义：异常携带外发确认提示 i18n key，供表现层翻译
        assert exc_info.value.message.key == "ai_external_acknowledgment_prompt"

    @pytest.mark.asyncio
    async def test_news_cloud_acknowledged_proceeds(self):
        """新闻分类云端出口已确认 → 放行正常分类，不抛策略异常。"""
        client = _make_litellm_client()
        client._service._get_news_semaphore.return_value = asyncio.Semaphore(1)
        client._service._chat_completion_litellm = AsyncMock(return_value={"content": '{"category": "tech"}'})
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True),
            patch.object(client, "_record_cloud_egress", new=AsyncMock()),
        ):
            result = await client._chat_completion(
                messages=[{"role": "user", "content": "分类这支新闻"}],
                provider="cloud",
                purpose="news",
                json_mode=True,
            )
        assert result == {"category": "tech"}
