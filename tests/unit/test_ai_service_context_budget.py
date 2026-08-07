# pyright: reportOptionalSubscript=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportCallIssue=false
# 本文件含 mock/monkey-patch 的测试替身模式；pyright 无法验证替身与生产类型兼容性，
# 统一在此文件局部禁用相关告警，测试行为由用例本身验证。
#
# Issue #70: 模型上下文感知的 Token 化全局预算。
# 覆盖 _estimate_tokens / _get_model_context_window / _apply_context_budget /
# _compute_analysis_budget 及 analyze_stock 集成点。

import pytest
from unittest.mock import AsyncMock, patch

import services.ai_service as ai_mod
from services.ai_service import (
    AIService,
    CONTEXT_RESERVE_TOKENS,
    DEFAULT_CONTEXT_WINDOW,
    _apply_context_budget,
    _estimate_tokens,
    _get_model_context_window,
    _reset_token_estimator,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_estimator():
    """每个用例重置 tiktoken 模块缓存，避免跨用例污染（R7 测试隔离）。"""
    _reset_token_estimator()
    yield
    _reset_token_estimator()


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------
class TestEstimateTokens:
    def test_none_returns_zero(self):
        assert _estimate_tokens(None) == 0

    def test_empty_string_returns_zero(self):
        assert _estimate_tokens("") == 0

    def test_non_string_returns_zero(self):
        assert _estimate_tokens(123) == 0
        assert _estimate_tokens({"a": 1}) == 0

    def test_list_content_parts_summed(self):
        # 多模态 list content parts：只累加 str 的 text 部分
        parts = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "world"},
        ]
        total = _estimate_tokens(parts)
        assert total == _estimate_tokens("hello") + _estimate_tokens("world")

    def test_tiktoken_path_used(self):
        text = "hello world"
        assert _estimate_tokens(text) > 0
        assert _estimate_tokens(text) == _estimate_tokens(text)

    def test_fallback_path_on_tiktoken_error_not_underestimate_cjk(self):
        """tiktoken 加载失败时回退 len(text)//1，CJK 字符不得被低估（每字至少 1 token）。"""
        with (
            patch.object(ai_mod, "_tiktoken_enc_error", False),
            patch(
                "builtins.__import__",
                side_effect=ImportError("tiktoken unavailable"),
            ),
        ):
            _reset_token_estimator()
            cjk = "平安银行股份有限公司"  # 9 个 CJK 字符
            assert _estimate_tokens(cjk) >= len(cjk)

    def test_fallback_caches_error(self):
        """首次 tiktoken 失败后置 error 标志，后续走回退不再重复尝试。"""
        with patch("builtins.__import__", side_effect=ImportError("offline")):
            _reset_token_estimator()
            _estimate_tokens("abc")
            assert ai_mod._tiktoken_enc_error is True
            # 第二次：即使 import 恢复，仍走回退（error 已缓存，不再触发 import）
            with patch("builtins.__import__") as mock_imp:
                assert _estimate_tokens("abc") == 3
                assert mock_imp.call_count == 0  # error 已缓存，不再 import tiktoken

    def test_reset_clears_error_flag(self):
        with patch("builtins.__import__", side_effect=ImportError("offline")):
            _reset_token_estimator()
            _estimate_tokens("abc")
            assert ai_mod._tiktoken_enc_error is True
        _reset_token_estimator()
        assert ai_mod._tiktoken_enc_error is False


# ---------------------------------------------------------------------------
# _get_model_context_window
# ---------------------------------------------------------------------------
class TestGetModelContextWindow:
    # 使用 LLM_PROVIDERS 中真实注册且 context 不同的模型（P2-1）
    def test_known_model_returns_context(self):
        llm_config = {"provider": "deepseek", "model": "deepseek-v4-flash"}
        ctx = _get_model_context_window(llm_config)
        # deepseek-v4-flash 在 LLM_PROVIDERS 中声明 context=1000000
        assert ctx == 1_000_000

    def test_known_model_returns_context_different(self):
        """验证不同 context 的模型返回各自值（P2-1 核心）。"""
        llm_config = {"provider": "mistral", "model": "mistral-small-latest"}
        assert _get_model_context_window(llm_config) == 131072

    def test_unknown_model_falls_back(self):
        llm_config = {"provider": "deepseek", "model": "does-not-exist-xyz"}
        assert _get_model_context_window(llm_config) == DEFAULT_CONTEXT_WINDOW

    def test_zero_context_falls_back(self):
        llm_config = {"provider": "custom", "model": "custom-model"}
        assert _get_model_context_window(llm_config) == DEFAULT_CONTEXT_WINDOW

    def test_model_override_with_provider_prefix(self):
        """failover 生效模型形如 'provider/model'，应拆分后查 provider+model。"""
        llm_config = {"provider": "deepseek", "model": "deepseek-v4-flash"}
        ctx = _get_model_context_window(llm_config, model_override="deepseek/deepseek-v4-flash")
        assert ctx == 1_000_000

    def test_model_override_unknown_falls_back(self):
        llm_config = {"provider": "deepseek", "model": "deepseek-v4-flash"}
        assert _get_model_context_window(llm_config, model_override="nope/nope-x") == DEFAULT_CONTEXT_WINDOW

    # --- P2-2: per-model context 覆盖 ---
    def test_custom_model_context_override_used(self):
        """自定义模型显式声明 context 时，运行时以此为准（优先于回退）。"""
        llm_config = {
            "provider": "custom",
            "model": "my-custom-model",
            "custom_model_contexts": {"custom": {"my-custom-model": 32000}},
        }
        assert _get_model_context_window(llm_config) == 32000

    def test_custom_model_context_override_with_prefix(self):
        """ "provider/model" 拆分的生效模型也能命中覆盖映射。"""
        llm_config = {
            "provider": "custom",
            "model": "my-custom-model",
            "custom_model_contexts": {"custom": {"my-custom-model": 64000}},
        }
        assert _get_model_context_window(llm_config, model_override="custom/my-custom-model") == 64000

    def test_custom_model_context_override_invalid_falls_back(self):
        """覆盖值非 int 或 <=0 时忽略，回退内置信息/默认窗口。"""
        for bad in (0, -5, "32k"):
            llm_config = {
                "provider": "custom",
                "model": "my-custom-model",
                "custom_model_contexts": {"custom": {"my-custom-model": bad}},
            }
            assert _get_model_context_window(llm_config) == DEFAULT_CONTEXT_WINDOW

    def test_custom_model_context_non_dict_provider_ignored_without_error(self):
        """provider 的覆盖值非 dict（如 list/str 非法配置）时不抛错，静默回退（评审 Rvd-2）。"""
        for bad in ([32000], "32k"):
            llm_config = {
                "provider": "custom",
                "model": "my-custom-model",
                "custom_model_contexts": {"custom": bad},
            }
            assert _get_model_context_window(llm_config) == DEFAULT_CONTEXT_WINDOW

    def test_custom_model_context_override_precedence_over_builtin(self):
        """覆盖优先于内置 LLM_PROVIDERS 信息。"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "custom_model_contexts": {"deepseek": {"deepseek-v4-flash": 50000}},
        }
        assert _get_model_context_window(llm_config) == 50000


# ---------------------------------------------------------------------------
# _apply_context_budget
# ---------------------------------------------------------------------------
class TestApplyContextBudget:
    def _mk(self, name, priority, text, truncatable=True, min_chars=0, max_chars=None):
        return (name, priority, truncatable, text, max_chars, min_chars)

    def test_no_overflow_returns_all_in_order(self):
        sections = [
            self._mk("stock_info", 0, "S", truncatable=False),
            self._mk("tech", 0, "T", truncatable=False),
            self._mk("financials", 1, "F"),
            self._mk("history", 3, "H"),
        ]
        text, names = _apply_context_budget(sections, budget_tokens=1_000_000)
        assert names == ["stock_info", "tech", "financials", "history"]
        assert "S" in text and "H" in text

    def test_truncatable_low_priority_cut_first(self):
        """超预算时，可截断 + 低优先级（priority 大）先被裁掉，不可截断保留。"""
        stock = self._mk("stock_info", 0, "A" * 50, truncatable=False)
        tech = self._mk("tech", 0, "B" * 50, truncatable=False)
        financials = self._mk("financials", 1, "C" * 50, truncatable=True)
        news = self._mk("news", 5, "N" * 50, truncatable=True)
        # budget 小到只够保留不可截断 + 少量
        text, names = _apply_context_budget([stock, tech, financials, news], budget_tokens=1)
        assert "stock_info" in names
        assert "tech" in names
        # news 优先级最低，最先被裁
        assert "news" not in names

    def test_min_chars_zero_means_droppable(self):
        """min_chars=0 的可截断 section 可被整体丢弃；min>0 的保留非空下界。"""
        keep = self._mk("keep", 0, "K" * 100, truncatable=False)
        # min_chars=50：最多裁到 50 字符，不会整体消失
        semi = self._mk("semi", 1, "S" * 100, truncatable=True, min_chars=50)
        drop = self._mk("drop", 5, "D" * 100, truncatable=True, min_chars=0)
        text, names = _apply_context_budget([keep, semi, drop], budget_tokens=1)
        assert "drop" not in names
        assert "semi" in names
        # semi 保留长度 >= 50
        seg = text.split("\n\n")
        semi_text = [s for s in seg if "S" in s][0]
        assert len(semi_text) >= 50

    def test_finite_termination_when_no_reducible(self):
        """全部不可截断或已到 min 下界时，有限终止并接受超限（不无限循环）。"""
        big = self._mk("stock_info", 0, "A" * 1000, truncatable=False)
        text, names = _apply_context_budget([big], budget_tokens=1)
        assert names == ["stock_info"]
        assert "A" in text  # 超限但保留，未崩溃

    def test_order_invariant_preserved(self):
        """存活 section 顺序与输入一致（XML 标签结构保序）。"""
        sections = [
            self._mk("stock_info", 0, "A" * 100, truncatable=False),
            self._mk("tech", 0, "B" * 100, truncatable=False),
            self._mk("history", 3, "H" * 100, truncatable=True),
            self._mk("strategy", 0, "S" * 100, truncatable=False),
        ]
        _, names = _apply_context_budget(sections, budget_tokens=1)
        expected = ["stock_info", "tech", "strategy"]  # history 被裁
        assert names == expected

    def test_empty_text_sections_skipped(self):
        sections = [
            self._mk("stock_info", 0, "", truncatable=False),
            self._mk("tech", 0, "REAL", truncatable=False),
        ]
        _, names = _apply_context_budget(sections, budget_tokens=1_000_000)
        assert names == ["tech"]

    def test_low_priority_small_cut_before_high_priority_large(self):
        """大体积高优先级段不应被先掏空：优先级为主、token 数为辅（评审 Rvd-1）。"""
        # financials 优先级更高（priority 小）但体积巨大；news 优先级低（priority 大）但体积小。
        # 预算极小：应优先裁低优先级的小段 news，而非先掏空高优先级的大段 financials。
        financials = self._mk("financials", 1, "F" * 100_000, truncatable=True)
        news = self._mk("news", 5, "N" * 50, truncatable=True)
        _, names = _apply_context_budget([financials, news], budget_tokens=1)
        # 优先级低的 news 先被整体丢弃（min_chars=0）
        assert "news" not in names
        # 高优先级 financials 保留（即便它体积最大）
        assert "financials" in names


# ---------------------------------------------------------------------------
# _compute_analysis_budget
# ---------------------------------------------------------------------------
class TestComputeAnalysisBudget:
    def _make_svc(self, failover=None):
        svc = AIService.__new__(AIService)
        svc._litellm_config = {"provider": "deepseek", "model": "deepseek-v4-flash"}
        return svc

    def test_primary_is_budget_basis(self):
        """P2-3：预算以 primary 模型 context 为基准，不被短 fallback 拖小。"""
        svc = self._make_svc()
        failover = {
            "primary": "deepseek/deepseek-v4-flash",  # 1M
            "fallbacks": ["mistral/mistral-small-latest"],  # 131072（更小）
        }
        with patch("services.ai_service.ConfigHandler.get_failover_config", return_value=failover):
            budget = svc._compute_analysis_budget()
        # 只取 primary=1M，忽略更小的 fallback
        assert budget == max(1, 1_000_000 - CONTEXT_RESERVE_TOKENS)

    def test_primary_override_context_used(self):
        """primary 命中 per-model 覆盖时以其 context 为基准。"""
        svc = self._make_svc()
        svc._litellm_config = {
            "provider": "custom",
            "model": "my-custom-model",
            "custom_model_contexts": {"custom": {"my-custom-model": 32000}},
        }
        failover = {"primary": "custom/my-custom-model", "fallbacks": []}
        with patch("services.ai_service.ConfigHandler.get_failover_config", return_value=failover):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, 32000 - CONTEXT_RESERVE_TOKENS)

    def test_failover_read_error_falls_back(self):
        svc = self._make_svc()
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            side_effect=Exception("config read failed"),
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, DEFAULT_CONTEXT_WINDOW - CONTEXT_RESERVE_TOKENS)

    def test_empty_failover_uses_default(self):
        svc = self._make_svc({})
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            return_value={"primary": "", "fallbacks": []},
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, DEFAULT_CONTEXT_WINDOW - CONTEXT_RESERVE_TOKENS)

    def test_budget_at_least_one(self):
        """预算下限为 1，避免除零/负预算。"""
        svc = self._make_svc({})
        # 制造一个 context 很小（<=reserve）的模型
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            return_value={"primary": "custom/tiny-model", "fallbacks": []},
        ):
            budget = svc._compute_analysis_budget()
        assert budget >= 1


# ---------------------------------------------------------------------------
# analyze_stock 集成
# ---------------------------------------------------------------------------
class TestAnalyzeStockBudgetIntegration:
    @pytest.mark.asyncio
    async def test_user_custom_instructions_survive_tiny_budget(self):
        """<user_custom_instructions> 置于预算外，tiny budget 下仍存活。"""
        svc = AIService.__new__(AIService)
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})

        with (
            patch.object(AIService, "_compute_analysis_budget", return_value=1),
            patch.object(AIService, "is_cloud_available", return_value=True),
            patch("services.ai_service.ConfigHandler") as mock_ch,
            patch("core.prompt_base.get_base_prompt", return_value="prompt"),
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="user custom content"),
        ):
            mock_ch.get_ai_system_prompt.return_value = "SYSTEM"
            mock_ch.get_setting.return_value = False
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                ui_prompt_override="custom",
            )
        messages = svc._chat_completion.await_args.args[0]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("<user_custom_instructions>" in m["content"] for m in user_msgs)

    @pytest.mark.asyncio
    async def test_available_data_matches_surviving_sections(self):
        """预算后 available_data 只声明仍存活的 section（被裁段不出现）。"""
        svc = AIService.__new__(AIService)
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})

        # spy：记录每次 build_available_data_block 收到的 labels，验证重派生
        calls: list[list] = []
        real_builder = ai_mod.build_available_data_block

        def spy(labels):
            calls.append(list(labels))
            return real_builder(labels)

        with (
            patch.object(AIService, "_compute_analysis_budget", return_value=1),
            patch.object(AIService, "is_cloud_available", return_value=True),
            patch("services.ai_service.ConfigHandler") as mock_ch,
            patch("core.prompt_base.get_base_prompt", return_value="prompt"),
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="safe"),
            patch("services.ai_service.build_available_data_block", side_effect=spy),
        ):
            mock_ch.get_ai_system_prompt.return_value = "SYSTEM"
            mock_ch.get_setting.return_value = False
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[{"source": "s", "publish_time": "2024-01-01", "title": "t"}],
                include_learning_context=False,
            )

        messages = svc._chat_completion.await_args.args[0]
        user_content = [m for m in messages if m["role"] == "user"][0]["content"]
        # news 是可截断低优先级，tiny budget 下被裁掉，其 section 不应出现
        assert "<recent_news>" not in user_content
        # stock_info 恒存活
        assert "<stock_info>" in user_content
        # 预算后只调用一次 build_available_data_block（P1-1：预算前渲染已被移除）
        assert len(calls) == 1
        # 重派生的 labels 不含被裁掉的 news
        assert "ai_label_news" not in calls[0]
