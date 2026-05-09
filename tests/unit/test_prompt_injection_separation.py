import pathlib
import re


class TestUniversalRulesSeparateSystemMessage:
    def test_messages_structure_in_source(self):
        ai_service_path = pathlib.Path(__file__).resolve().parent.parent.parent / "services" / "ai_service.py"
        source = ai_service_path.read_text(encoding="utf-8")

        messages_pattern = re.search(
            r"messages\s*=\s*\[.*?\]",
            source,
            re.DOTALL,
        )
        assert messages_pattern is not None, "messages list not found in ai_service.py"
        messages_block = messages_pattern.group()

        assert "_UNIVERSAL_RULES" in messages_block, "_UNIVERSAL_RULES should be in messages construction"
        assert messages_block.count('"system"') >= 2, "Should have at least 2 system messages"

        rules_pos = messages_block.find("_UNIVERSAL_RULES")
        prompt_pos = messages_block.find("system_prompt")

        assert rules_pos < prompt_pos, "_UNIVERSAL_RULES must come before system_prompt in messages list"

    def test_no_universal_rules_concatenation_in_system_prompt(self):
        ai_service_path = pathlib.Path(__file__).resolve().parent.parent.parent / "services" / "ai_service.py"
        source = ai_service_path.read_text(encoding="utf-8")

        assert (
            "system_prompt += " not in source
            or "_UNIVERSAL_RULES" not in source.split("system_prompt += ")[-1].split("\n")[0]
        ), "_UNIVERSAL_RULES should NOT be concatenated into system_prompt"

        lines_with_rules_concat = [
            line
            for line in source.split("\n")
            if "_UNIVERSAL_RULES" in line
            and ("+=" in line or "+ '\\n\\n' +" in line or '+ "\\n\\n" +' in line)
            and "system_prompt" in line
        ]
        assert len(lines_with_rules_concat) == 0, (
            f"_UNIVERSAL_RULES should not be concatenated into system_prompt, found: {lines_with_rules_concat}"
        )

    def test_uses_get_base_prompt_not_resolve_prompt(self):
        ai_service_path = pathlib.Path(__file__).resolve().parent.parent.parent / "services" / "ai_service.py"
        source = ai_service_path.read_text(encoding="utf-8")

        assert "get_base_prompt" in source, "Should import get_base_prompt"
        assert "resolve_prompt" not in source, "Should NOT use resolve_prompt (which concatenates _UNIVERSAL_RULES)"

    def test_ui_override_does_not_merge_with_universal_rules(self):
        ai_service_path = pathlib.Path(__file__).resolve().parent.parent.parent / "services" / "ai_service.py"
        source = ai_service_path.read_text(encoding="utf-8")

        sanitized_block = source[source.find("sanitize_prompt") :]
        next_lines = sanitized_block.split("\n")[:5]
        for line in next_lines:
            assert "_UNIVERSAL_RULES" not in line or "system_prompt" not in line, (
                "sanitized prompt should be assigned to system_prompt directly, not merged with _UNIVERSAL_RULES"
            )

    def test_import_statement_uses_get_base_prompt(self):
        ai_service_path = pathlib.Path(__file__).resolve().parent.parent.parent / "services" / "ai_service.py"
        source = ai_service_path.read_text(encoding="utf-8")

        import_line = [line for line in source.split("\n") if "strategy_prompts" in line and "import" in line]
        assert len(import_line) > 0, "Should import from strategy_prompts"
        assert "get_base_prompt" in import_line[0], "Should import get_base_prompt from strategy_prompts"
