from strategies.strategy_prompts import STRATEGY_PROMPTS, get_base_prompt, resolve_prompt


class TestStrategyPromptsKeyConsistency:
    def test_northbound_holding_key_exists(self):
        assert "northbound_holding" in STRATEGY_PROMPTS

    def test_northbound_flow_key_exists(self):
        assert "northbound_flow" in STRATEGY_PROMPTS

    def test_northbound_key_removed(self):
        assert "northbound" not in STRATEGY_PROMPTS

    def test_northbound_holding_prompt_not_empty(self):
        prompt = STRATEGY_PROMPTS["northbound_holding"]
        assert len(prompt.strip()) > 50

    def test_northbound_flow_prompt_not_empty(self):
        prompt = STRATEGY_PROMPTS["northbound_flow"]
        assert len(prompt.strip()) > 50

    def test_northbound_holding_mentions_holding(self):
        prompt = STRATEGY_PROMPTS["northbound_holding"]
        assert "持仓" in prompt or "增持" in prompt or "holding" in prompt.lower()

    def test_northbound_flow_mentions_flow(self):
        prompt = STRATEGY_PROMPTS["northbound_flow"]
        assert "资金流" in prompt or "flow" in prompt.lower()


class TestGetBasePrompt:
    def test_existing_key(self):
        result = get_base_prompt("value")
        assert result is not None
        assert len(result.strip()) > 0

    def test_nonexistent_key_returns_fallback(self):
        result = get_base_prompt("nonexistent_strategy")
        assert result is not None
        assert len(result.strip()) > 0


class TestResolvePrompt:
    def test_resolve_existing_key(self):
        result = resolve_prompt("value")
        assert result is not None
        assert len(result.strip()) > 0

    def test_resolve_northbound_holding(self):
        result = resolve_prompt("northbound_holding")
        assert result is not None
        assert len(result.strip()) > 0

    def test_resolve_northbound_flow(self):
        result = resolve_prompt("northbound_flow")
        assert result is not None
        assert len(result.strip()) > 0


class TestPromptDataBoundary:
    def test_all_prompts_have_data_boundary(self):
        for key, prompt in STRATEGY_PROMPTS.items():
            assert "【数据边界】" in prompt or "available_data" in prompt, (
                f"Strategy '{key}' missing runtime data boundary directive (【数据边界】 or available_data)"
            )

    def test_no_static_available_data_list(self):
        forbidden_headers = ["【可用数据】", "【你将收到的分析材料】"]
        for key, prompt in STRATEGY_PROMPTS.items():
            for header in forbidden_headers:
                assert header not in prompt, f"Strategy '{key}' still has static data enumeration '{header}'"
