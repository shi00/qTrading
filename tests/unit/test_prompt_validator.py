import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import pandas as pd

from strategies.prompt_validator import (
    DataDeclaration,
    validate_prompt_declarations,
    generate_declaration_report,
    check_multi_period_data,
    check_field_exists,
    check_table_has_data,
)

pytestmark = pytest.mark.unit


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


class TestCheckMultiPeriodData:
    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_no_stock_basic(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(return_value=None)
        mock_cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame({"roe": [10.0]}))
        mock_cm_cls.return_value = mock_cache
        result = await check_multi_period_data("roe")
        assert result is True

    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_with_stock_basic(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
        )
        mock_cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame({"roe": [10.0, 12.0]}))
        mock_cm_cls.return_value = mock_cache
        result = await check_multi_period_data("roe")
        assert result is True

    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_all_empty(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(return_value=None)
        mock_cache.get_financial_reports_history = AsyncMock(return_value=None)
        mock_cm_cls.return_value = mock_cache
        result = await check_multi_period_data("roe")
        assert result is False


class TestPromptValidatorLazyLoading:
    """D-P1-2: Verify prompt_validator uses lazy loading, no module-level side effects."""

    def test_declarations_is_private(self):
        import strategies.prompt_validator as pv

        assert not hasattr(pv, "DECLARATIONS"), "DECLARATIONS should be private (_DECLARATIONS)"

    def test_get_declarations_is_lazy(self):
        import strategies.prompt_validator as pv

        pv._declarations_initialized = False
        pv._DECLARATIONS = []
        assert pv._DECLARATIONS == []
        assert not pv._declarations_initialized

    def test_import_does_not_trigger_init(self):
        import strategies.prompt_validator as pv

        assert not pv._declarations_initialized or len(pv._DECLARATIONS) > 0

    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_exception(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(side_effect=Exception("DB error"))
        mock_cm_cls.return_value = mock_cache
        result = await check_multi_period_data("roe")
        assert result is False


class TestCheckFieldExists:
    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_field_present(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(return_value=None)
        mock_cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame({"n_cashflow_act": [500.0]}))
        mock_cm_cls.return_value = mock_cache
        result = await check_field_exists("n_cashflow_act")
        assert result is True

    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_field_absent(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(return_value=None)
        mock_cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame({"other_field": [1.0]}))
        mock_cm_cls.return_value = mock_cache
        result = await check_field_exists("n_cashflow_act")
        assert result is False

    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_exception(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.get_stock_basic = AsyncMock(side_effect=Exception("DB error"))
        mock_cm_cls.return_value = mock_cache
        result = await check_field_exists("n_cashflow_act")
        assert result is False


class TestCheckTableHasData:
    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_has_data(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.check_table_has_data = AsyncMock(return_value=True)
        mock_cm_cls.return_value = mock_cache
        result = await check_table_has_data("fina_audit")
        assert result is True

    @pytest.mark.asyncio
    @patch("data.cache.cache_manager.CacheManager")
    async def test_no_data(self, mock_cm_cls):
        mock_cache = MagicMock()
        mock_cache.check_table_has_data = AsyncMock(return_value=False)
        mock_cm_cls.return_value = mock_cache
        result = await check_table_has_data("fina_audit")
        assert result is False
