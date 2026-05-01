"""
Tushare API Fields Tests

Tests that ensure API methods explicitly specify fields parameter
to prevent silent data loss when Tushare adds new fields.

Run: pytest tests/test_tushare_api_fields.py -v
"""

import inspect

from data.external.tushare_client import TushareClient
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.quote_dao import QuoteDao

from .helpers import extract_cols_from_method, extract_fields_from_api_method


class TestTushareApiFieldNames:
    """Test that Tushare API methods have correct field name documentation."""

    def test_limit_list_field_documentation(self):
        source = inspect.getsource(TushareClient.get_limit_list)
        assert "limit:" in source.lower() or "limit " in source.lower(), (
            "get_limit_list should document 'limit' field (not 'limit_type')"
        )
        assert "limit_type" not in source or "limit:" in source.lower(), (
            "get_limit_list should use 'limit' field name, not 'limit_type'"
        )

    def test_top_list_field_documentation(self):
        source = inspect.getsource(TushareClient.get_top_list)
        assert "top_list" in source.lower() or "dragon" in source.lower() or "lhb" in source.lower(), (
            "get_top_list should be documented"
        )

    def test_suspend_d_field_documentation(self):
        source = inspect.getsource(TushareClient.get_suspend_d)
        assert "suspend_type" in source, "get_suspend_d should document 'suspend_type' field"


class TestApiFieldsExplicit:
    """Test that API methods explicitly specify fields parameter to prevent silent data loss."""

    CRITICAL_APIS = [
        "get_top_list",
        "get_hk_hold",
        "get_moneyflow",
        "get_block_trade",
        "get_limit_list",
        "get_moneyflow_hsgt",
        "get_suspend_d",
        "get_margin_detail",
        "get_pledge_stat",
        "get_repurchase",
        "get_dividend",
        "get_shibor",
        "get_top10_holders",
        "get_stk_holdernumber",
        "get_fina_mainbz",
    ]

    def test_api_methods_should_specify_fields(self):
        apis_with_fields = []
        apis_without_fields = []

        for method_name in self.CRITICAL_APIS:
            if hasattr(TushareClient, method_name):
                source = inspect.getsource(getattr(TushareClient, method_name))
                if "fields=" in source or "fields =" in source:
                    apis_with_fields.append(method_name)
                else:
                    apis_without_fields.append(method_name)

        assert len(apis_without_fields) == 0, (
            f"API methods without explicit fields parameter: {apis_without_fields}. "
            f"This may cause silent data loss if Tushare adds new fields."
        )
        assert len(apis_with_fields) == len(self.CRITICAL_APIS), (
            f"All {len(self.CRITICAL_APIS)} APIs should have explicit fields"
        )


class TestApiFieldsMatchDaoCols:
    """Test that API fields parameter covers all DAO cols (field-level consistency)."""

    _COMPUTED_COLS: dict[str, set[str]] = {
        "save_holder_number": {"holder_num_change", "holder_num_ratio"},
    }

    API_DAO_MAPPINGS = [
        ("get_moneyflow", "save_moneyflow"),
        ("get_top_list", "save_top_list"),
        ("get_hk_hold", "save_hk_hold"),
        ("get_block_trade", "save_block_trade"),
        ("get_limit_list", "save_limit_list"),
        ("get_moneyflow_hsgt", "save_moneyflow_hsgt"),
        ("get_suspend_d", "save_suspend_d"),
        ("get_margin_detail", "save_margin_detail"),
        ("get_pledge_stat", "save_pledge_stat"),
        ("get_repurchase", "save_repurchase"),
        ("get_dividend", "save_dividend"),
        ("get_shibor", "save_shibor_daily"),
        ("get_top10_holders", "save_top10_holders"),
        ("get_stk_holdernumber", "save_holder_number"),
        ("get_fina_mainbz", "save_fina_mainbz"),
        ("get_index_daily", "save_index_daily"),
        ("get_index_weight", "save_index_weights"),
    ]

    def test_api_fields_cover_dao_cols(self):
        dao_map = {
            "QuoteDao": QuoteDao,
            "MarketDao": MarketDao,
            "HolderDao": HolderDao,
            "FinancialDao": FinancialDao,
        }

        issues = []

        for api_name, dao_name in self.API_DAO_MAPPINGS:
            if not hasattr(TushareClient, api_name):
                continue

            api_method = getattr(TushareClient, api_name)
            api_fields = extract_fields_from_api_method(api_method)

            if not api_fields:
                issues.append(f"{api_name}: no fields parameter found")
                continue

            for _dao_cls_name, dao_cls in dao_map.items():
                if hasattr(dao_cls, dao_name):
                    dao_method = getattr(dao_cls, dao_name)
                    dao_cols = extract_cols_from_method(dao_method)

                    if dao_cols is None:
                        continue

                    expected = dao_cols - {"updated_at", "created_at"}
                    expected -= self._COMPUTED_COLS.get(dao_name, set())
                    missing = expected - api_fields

                    if missing:
                        issues.append(f"{api_name} fields missing DAO cols: {missing}")
                    break

        assert not issues, "API fields do not cover DAO cols:\n" + "\n".join(issues)

    def test_moneyflow_has_net_mf_amount(self):
        api_fields = extract_fields_from_api_method(TushareClient.get_moneyflow)
        assert "net_mf_amount" in api_fields, "get_moneyflow must include net_mf_amount in fields"

    def test_index_daily_has_all_fields(self):
        api_fields = extract_fields_from_api_method(TushareClient.get_index_daily)
        expected = {
            "ts_code",
            "trade_date",
            "close",
            "open",
            "high",
            "low",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        }
        missing = expected - api_fields
        assert not missing, f"get_index_daily missing fields: {missing}"

    def test_index_weight_has_all_fields(self):
        api_fields = extract_fields_from_api_method(TushareClient.get_index_weight)
        expected = {"index_code", "con_code", "trade_date", "weight"}
        missing = expected - api_fields
        assert not missing, f"get_index_weight missing fields: {missing}"
