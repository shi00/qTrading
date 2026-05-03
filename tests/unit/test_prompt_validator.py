import pytest
from unittest.mock import AsyncMock

from strategies.prompt_validator import (
    DataDeclaration,
    validate_prompt_declarations,
    generate_declaration_report,
)


class TestDataDeclaration:
    def test_init(self):
        decl = DataDeclaration(
            name="test",
            prompt_claim="test data",
            injector=AsyncMock(return_value=True),
        )
        assert decl.name == "test"
        assert decl.status == "unknown"


class TestValidatePromptDeclarations:
    @pytest.mark.asyncio
    async def test_all_available(self):
        decls = [
            DataDeclaration("a", "desc a", AsyncMock(return_value=True)),
            DataDeclaration("b", "desc b", AsyncMock(return_value=True)),
        ]
        result = await validate_prompt_declarations(decls)
        assert result["a"] is True
        assert result["b"] is True

    @pytest.mark.asyncio
    async def test_missing_data(self):
        decls = [
            DataDeclaration("a", "desc a", AsyncMock(return_value=False)),
        ]
        result = await validate_prompt_declarations(decls)
        assert result["a"] is False
        assert decls[0].status == "missing"

    @pytest.mark.asyncio
    async def test_error_in_injector(self):
        decls = [
            DataDeclaration("a", "desc a", AsyncMock(side_effect=Exception("fail"))),
        ]
        result = await validate_prompt_declarations(decls)
        assert result["a"] is False
        assert "error" in decls[0].status


class TestGenerateDeclarationReport:
    def test_available(self):
        decls = [
            DataDeclaration("a", "desc a", AsyncMock(), status="available"),
        ]
        report = generate_declaration_report(decls)
        assert "✅" in report
        assert "a" in report

    def test_missing(self):
        decls = [
            DataDeclaration("a", "desc a", AsyncMock(), status="missing"),
        ]
        report = generate_declaration_report(decls)
        assert "❌" in report
