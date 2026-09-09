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
import services.ai_service.token_budget as token_budget
from services.ai_service import (
    AIService,
    CONTEXT_RESERVE_TOKENS,
    DEFAULT_CONTEXT_WINDOW,
    _apply_context_budget,
    _estimate_tokens,
    _estimate_tokens_fallback,
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
            patch.object(token_budget, "_tiktoken_enc_error", False),
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
            assert token_budget._tiktoken_enc_error is True
            # 第二次：即使 import 恢复，仍走回退（error 已缓存，不再触发 import）
            with patch("builtins.__import__") as mock_imp:
                assert _estimate_tokens("abc") == 1
                assert mock_imp.call_count == 0  # error 已缓存，不再 import tiktoken

    def test_reset_clears_error_flag(self):
        with patch("builtins.__import__", side_effect=ImportError("offline")):
            _reset_token_estimator()
            _estimate_tokens("abc")
            assert token_budget._tiktoken_enc_error is True
        _reset_token_estimator()
        assert token_budget._tiktoken_enc_error is False


class TestTokenEstimatorFallbackCJK:
    """Issue D5-8: tiktoken 不可用时的分段估算（CJK 1:1，非 CJK 4:1）。"""

    def test_pure_cjk_fallback(self):
        """纯中文文本回退估算按 1 字符 = 1 token 计量。"""
        cjk = "平安银行股份有限公司"
        assert _estimate_tokens_fallback(cjk) == 10

    def test_cjk_with_chinese_punctuation(self):
        """中文字符与全角标点符号全部被识别为 CJK（1:1）。"""
        text = "平安银行，好！【优质标的】"
        # 汉字 8 个，中文标点 5 个（，！【】），共 13 个字符
        assert _estimate_tokens_fallback(text) == 13

    def test_pure_english_fallback_eliminates_overestimation(self):
        """纯英文文本按 4 字符 ≈ 1 token 估算，消除 5.56 倍高估。"""
        english = "This is an in-depth stock analysis financial summary for evaluation purposes only. " * 16
        # 83 字符/段 × 16 = 1328 字符；按 4 字符 ≈ 1 token 向上取整 = (1328 + 3) // 4 = 332
        assert _estimate_tokens_fallback(english) == 332
        # 边界护栏：估算 token 远小于字符数（不再按 1:1 高估 5 倍）
        assert len(english) // 3 > 332

    def test_mixed_cjk_and_ascii(self):
        """混合文本：CJK 字符与 ASCII 字符分段精确累计。"""
        # 2 个 CJK ('平安') + 12 个 ASCII (' (000001.SZ)')
        text = "平安 (000001.SZ)"
        # CJK = 2, non-CJK = 12 -> (12 + 3) // 4 = 3 -> total = 5
        assert _estimate_tokens_fallback(text) == 5

    def test_empty_and_short_strings(self):
        """空字符串为 0，单字符非 CJK 为 1，单字符 CJK 为 1。"""
        assert _estimate_tokens_fallback("") == 0
        assert _estimate_tokens_fallback(None) == 0  # type: ignore[arg-type]
        assert _estimate_tokens_fallback("a") == 1
        assert _estimate_tokens_fallback("中") == 1


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

    def test_failover_read_error_falls_back_to_active_model(self):
        """failover 配置读取异常时，安全降级至当前生效模型预算（D5-3）。"""
        svc = self._make_svc()
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            side_effect=Exception("config read failed"),
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, 1_000_000 - CONTEXT_RESERVE_TOKENS)

    def test_failover_read_error_no_active_model_falls_back_to_default(self):
        """failover 配置读取异常且无生效模型配置时，安全回退到默认窗口（D5-3）。"""
        svc = self._make_svc()
        svc._litellm_config = {}
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            side_effect=Exception("config read failed"),
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, DEFAULT_CONTEXT_WINDOW - CONTEXT_RESERVE_TOKENS)

    def test_empty_failover_uses_active_model_context(self):
        """未配置 failover 时，应以当前生效模型（1M）的 context 为基准，而非硬编码默认窗口（D5-3）。"""
        svc = self._make_svc()
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            return_value={"primary": "", "fallbacks": []},
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, 1_000_000 - CONTEXT_RESERVE_TOKENS)

    def test_empty_failover_with_small_model_budget(self):
        """未配置 failover 时，小模型（32k）应严格按其自身 context 计算预算，避免超窗导致 API 400（D5-3）。"""
        svc = self._make_svc()
        svc._litellm_config = {
            "provider": "custom",
            "model": "custom-32k",
            "custom_model_contexts": {"custom": {"custom-32k": 32000}},
        }
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            return_value={"primary": "", "fallbacks": []},
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, 32000 - CONTEXT_RESERVE_TOKENS)

    def test_empty_failover_unknown_model_falls_back_to_default(self):
        """未配置 failover 且当前生效模型未知时，安全回退到默认窗口（D5-3）。"""
        svc = self._make_svc()
        svc._litellm_config = {"provider": "unknown", "model": "unknown-model"}
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            return_value={"primary": "", "fallbacks": []},
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, DEFAULT_CONTEXT_WINDOW - CONTEXT_RESERVE_TOKENS)

    def test_empty_failover_whitespace_primary_uses_active_model(self):
        """failover primary 为纯空白字符串时，安全防御等同未指定，按当前生效模型计算（D5-3 对抗性检视）。"""
        svc = self._make_svc()
        svc._litellm_config = {
            "provider": "custom",
            "model": "custom-64k",
            "custom_model_contexts": {"custom": {"custom-64k": 64000}},
        }
        with patch(
            "services.ai_service.ConfigHandler.get_failover_config",
            return_value={"primary": "   ", "fallbacks": []},
        ):
            budget = svc._compute_analysis_budget()
        assert budget == max(1, 64000 - CONTEXT_RESERVE_TOKENS)

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


class TestComputeAnalysisBudgetDeductFixed:
    """Issue D5-4: 动态扣减 system 消息与不可裁剪固定块。"""

    def _make_svc(self, model="model-test", context=32000):
        svc = AIService.__new__(AIService)
        svc._litellm_config = {
            "provider": "custom",
            "model": model,
            "custom_model_contexts": {"custom": {model: context}},
        }
        return svc

    def test_system_messages_and_fixed_blocks_deducted(self):
        """模型窗口逐层扣减：窗口 - 输出预留(4000) - system 消息 - 不可裁剪块（D5-4）。"""
        svc = self._make_svc(model="model-32k", context=32000)
        failover = {"primary": "custom/model-32k", "fallbacks": []}

        # 构造约 500 token 的 system message
        sys_msgs = [
            {"role": "system", "content": "A" * 500},
            {"role": "system", "content": "B" * 500},
        ]
        # 构造约 200 token 的 fixed block
        fixed_block = "C" * 200

        with patch("services.ai_service.ConfigHandler.get_failover_config", return_value=failover):
            budget = svc._compute_analysis_budget(
                system_messages=sys_msgs,
                fixed_blocks=fixed_block,
                reserved_output_tokens=4000,
            )

        expected_deduct = _estimate_tokens("A" * 500) + _estimate_tokens("B" * 500) + _estimate_tokens("C" * 200) + 4000
        assert budget == 32000 - expected_deduct

    def test_fixed_blocks_as_list_deducted(self):
        """fixed_blocks 传入字符串列表时逐项累加扣减（D5-4）。"""
        svc = self._make_svc(model="model-32k", context=32000)
        failover = {"primary": "custom/model-32k", "fallbacks": []}

        fixed_list = ["BlockOne " * 10, "BlockTwo " * 20]
        with patch("services.ai_service.ConfigHandler.get_failover_config", return_value=failover):
            budget = svc._compute_analysis_budget(
                fixed_blocks=fixed_list,
                reserved_output_tokens=2000,
            )

        expected_deduct = sum(_estimate_tokens(b) for b in fixed_list) + 2000
        assert budget == 32000 - expected_deduct

    def test_overflow_fixed_blocks_capped_at_one(self, caplog):
        """当固定提示词 + 预留输出超过模型上下文上限时，保底返回 1 并输出 warning（D5-4 对抗性防护）。"""
        svc = self._make_svc(model="model-8k", context=8000)
        failover = {"primary": "custom/model-8k", "fallbacks": []}

        # 超大 system prompt（>8000 tokens，必然溢出 8k 窗口）
        sys_msgs = [{"role": "system", "content": "SuperLongPrompt " * 4000}]

        import logging

        with (
            patch("services.ai_service.ConfigHandler.get_failover_config", return_value=failover),
            caplog.at_level(logging.WARNING),
        ):
            budget = svc._compute_analysis_budget(
                system_messages=sys_msgs,
                reserved_output_tokens=4000,
            )

        assert budget == 1
        assert "exceeds model context window" in caplog.text

    def test_backward_compatible_no_args(self):
        """不传参数时 100% 保持向后兼容性（D5-4）。"""
        svc = self._make_svc(model="model-32k", context=32000)
        failover = {"primary": "custom/model-32k", "fallbacks": []}
        with patch("services.ai_service.ConfigHandler.get_failover_config", return_value=failover):
            budget = svc._compute_analysis_budget()
        assert budget == 32000 - CONTEXT_RESERVE_TOKENS


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

    @pytest.mark.asyncio
    async def test_analyze_stock_passes_system_and_fixed_blocks_to_budget(self):
        """analyze_stock 必须向 _compute_analysis_budget 传入 system_messages 与 fixed_blocks（D5-4）。"""
        svc = AIService.__new__(AIService)
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})

        with (
            patch.object(AIService, "_compute_analysis_budget", return_value=50000) as mock_budget,
            patch.object(AIService, "is_cloud_available", return_value=True),
            patch("services.ai_service.ConfigHandler") as mock_ch,
            patch("core.prompt_base.get_base_prompt", return_value="prompt"),
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="safe_custom"),
        ):
            mock_ch.get_ai_system_prompt.return_value = "SYSTEM_STRATEGY_RULES"
            mock_ch.get_setting.return_value = False
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                ui_prompt_override="override_prompt",
            )

        assert mock_budget.call_count == 1
        call_kwargs = mock_budget.call_args.kwargs
        assert "system_messages" in call_kwargs
        assert "fixed_blocks" in call_kwargs
        sys_msgs = call_kwargs["system_messages"]
        assert len(sys_msgs) >= 2
        # system messages 包含 strategy_rules
        assert any("SYSTEM_STRATEGY_RULES" in m["content"] for m in sys_msgs)
        # fixed_blocks 包含 user_custom_instructions
        assert any("safe_custom" in str(b) for b in call_kwargs["fixed_blocks"])
