"""
Tushare API Fields Tests

Tests that ensure API methods explicitly specify fields parameter
to prevent silent data loss when Tushare adds new fields.

Run: pytest tests/test_tushare_api_fields.py -v
"""

from data.external.tushare_client import TushareClient
from data.persistence.daos.express_dao import ExpressDao
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.pledge_detail_dao import PledgeDetailDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.share_float_dao import ShareFloatDao
from data.persistence.daos.stk_holdertrade_dao import StkHoldertradeDao
from data.persistence.daos.stk_limit_dao import StkLimitDao
from data.persistence.daos.top_inst_dao import TopInstDao

from tests._helpers import extract_cols_from_method, extract_fields_from_api_method
import pytest


pytestmark = pytest.mark.unit


class TestTushareApiFieldNames:
    """Test that Tushare API methods have correct field name documentation."""

    def test_limit_list_field_documentation(self):
        api_fields = extract_fields_from_api_method(TushareClient.get_limit_list)
        assert "limit" in api_fields, "get_limit_list should include 'limit' field (not 'limit_type')"

    def test_top_list_field_documentation(self):
        api_fields = extract_fields_from_api_method(TushareClient.get_top_list)
        assert len(api_fields) > 0, "get_top_list should specify explicit fields"

    def test_suspend_d_field_documentation(self):
        api_fields = extract_fields_from_api_method(TushareClient.get_suspend_d)
        assert "suspend_type" in api_fields, "get_suspend_d should include 'suspend_type' field"


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
        "get_share_float",
        "get_top_inst",
        "get_pledge_detail",
    ]

    def test_api_methods_should_specify_fields(self):
        apis_with_fields = []
        apis_without_fields = []

        for method_name in self.CRITICAL_APIS:
            if hasattr(TushareClient, method_name):
                api_fields = extract_fields_from_api_method(getattr(TushareClient, method_name))
                if api_fields:
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

    # R17: Tushare API 字段名与数据库列名不一致的映射（保留字场景）。
    # key = DAO 数据库列名，value = Tushare API 字段名。
    _DB_TO_API_COL_ALIASES: dict[str, str] = {
        "limit_type": "limit",  # limit 是 SQL 保留字，数据库列名映射为 limit_type
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
        ("get_top_inst", "save_top_inst"),
        ("get_share_float", "save_share_float"),
        ("get_pledge_detail", "save_pledge_detail"),
    ]

    # DAT-22：以 TushareClient.TABLE_TO_API_MAP（表名 → API 字段集名）作为表→API 单一真相源，
    # 显式派生每张表对应的 (get_ 方法名, DAO 类, save_ 方法名)。
    # 方法名此处显式写出，禁 "get_"+字段集名 字符串拼接（字段集名≠方法名，见 Task 约束 2）。
    # 必须覆盖 TABLE_TO_API_MAP 的 13 张表（新表缺失则 test_table_to_api_map_fully_covered 失败）。
    TABLE_TO_DAO: dict[str, tuple[str, type, str]] = {
        "moneyflow_hsgt": ("get_moneyflow_hsgt", MarketDao, "save_moneyflow_hsgt"),
        "northbound_holding": ("get_hk_hold", QuoteDao, "save_northbound"),
        "moneyflow_daily": ("get_moneyflow", QuoteDao, "save_moneyflow"),
        "top_list": ("get_top_list", QuoteDao, "save_top_list"),
        "top_inst": ("get_top_inst", TopInstDao, "save_top_inst"),
        "limit_list": ("get_limit_list", QuoteDao, "save_limit_list"),
        "margin_daily": ("get_margin_detail", QuoteDao, "save_margin_daily"),
        "block_trade": ("get_block_trade", QuoteDao, "save_block_trade"),
        "stk_limit": ("get_stk_limit", StkLimitDao, "save_stk_limit"),
        "pledge_detail": ("get_pledge_detail", PledgeDetailDao, "save_pledge_detail"),
        "share_float": ("get_share_float", ShareFloatDao, "save_share_float"),
        "stk_holdertrade": ("get_stk_holdertrade", StkHoldertradeDao, "save_stk_holdertrade"),
        "express": ("get_express", ExpressDao, "save_express"),
    }

    def test_api_fields_cover_dao_cols(self):
        dao_map = {
            "QuoteDao": QuoteDao,
            "MarketDao": MarketDao,
            "HolderDao": HolderDao,
            "FinancialDao": FinancialDao,
            "TopInstDao": TopInstDao,
            "ShareFloatDao": ShareFloatDao,
            "PledgeDetailDao": PledgeDetailDao,
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
                    # R17: 将数据库列名映射回 Tushare API 字段名后再比较
                    expected = {self._DB_TO_API_COL_ALIASES.get(c, c) for c in expected}
                    missing = expected - api_fields

                    if missing:
                        issues.append(f"{api_name} fields missing DAO cols: {missing}")
                    break

        assert not issues, "API fields do not cover DAO cols:\n" + "\n".join(issues)

    def test_table_to_api_map_fully_covered(self):
        """DAT-22 守卫：TABLE_TO_API_MAP 中的每张表都必须在 TABLE_TO_DAO 中有显式映射。

        TABLE_TO_API_MAP 是表→API 的唯一真相源。若某表缺映射（如 Phase3 新增表未经
        DAT-09 检视加入门禁的漏检场景），此处直接失败，堵死缺口。
        """
        unmatched = set(TushareClient.TABLE_TO_API_MAP) - set(self.TABLE_TO_DAO)
        assert not unmatched, (
            f"TABLE_TO_API_MAP 中有 {len(unmatched)} 张表未在 TABLE_TO_DAO 登记映射: "
            f"{sorted(unmatched)}。请为每张表显式补充 (get_方法, DAO类, save_方法)。"
        )

    def test_table_to_dao_fields_cover_dao_cols(self):
        """DAT-22 派生检查：对 TABLE_TO_API_MAP 每张表，用显式 get_/save_ 映射校验
        "API fields 覆盖 DAO cols"（与 test_api_fields_cover_dao_cols 相同语义）。

        TABLE_TO_DAO 是 TABLE_TO_API_MAP 的投影（每条 key 都能在 TABLE_TO_API_MAP 中找到），
        但保留显式方法名以兼容字段集名≠方法名的情况。新增表时须同时在两处登记。
        """
        issues = []

        for table in TushareClient.TABLE_TO_API_MAP:
            if table not in self.TABLE_TO_DAO:
                # 缺失由 test_table_to_api_map_fully_covered 统一报告，这里跳过避免重复
                continue

            api_name, dao_cls, save_name = self.TABLE_TO_DAO[table]

            if not hasattr(TushareClient, api_name):
                continue

            api_method = getattr(TushareClient, api_name)
            api_fields = extract_fields_from_api_method(api_method)

            if not api_fields:
                issues.append(f"{table} ({api_name}): no fields parameter found")
                continue

            if not hasattr(dao_cls, save_name):
                issues.append(f"{table}: DAO {dao_cls.__name__} missing save method {save_name}")
                continue

            dao_method = getattr(dao_cls, save_name)
            dao_cols = extract_cols_from_method(dao_method)

            if dao_cols is None:
                continue

            expected = dao_cols - {"updated_at", "created_at"}
            # R17: 将数据库列名映射回 Tushare API 字段名后再比较
            expected = {self._DB_TO_API_COL_ALIASES.get(c, c) for c in expected}
            missing = expected - api_fields

            if missing:
                issues.append(f"{table} ({api_name}) fields missing DAO cols: {missing}")

        assert not issues, "API fields do not cover DAO cols (TABLE_TO_API_MAP derived):\n" + "\n".join(issues)

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
