# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportPrivateUsage=false
"""N03：旧"数据"页浏览/导出不得绕过 Agent 公开投影（不连真实业务库）。

- 目录反射不得把 Agent 受管表（``agent_`` 前缀）纳入通用数据页：其公开表示只经
  Agent 读取 API 提供（Agent 详细设计 §9.10 + 迁移 0048 三表示），按原始全列读取
  会带出 ``body_text``（原值）/``model_text``（模型可见）等私有表示。
- 浏览与 CSV/XLSX 导出共用同一 ``query_table`` 入口，故 agent 表被拒时三者都不
  产生含私有字段的产物；普通行情表行为不变。
"""

import io

import pytest
import sqlalchemy as sa
from openpyxl import load_workbook

from data.persistence.data_explorer_query_client import DataExplorerQueryClient
from ui.viewmodels.data_explorer_view_model import DataExplorerViewModel

pytestmark = pytest.mark.unit

_METADATA = sa.MetaData()
_STOCK = sa.Table(
    "stock_basic",
    _METADATA,
    sa.Column("ts_code", sa.Text),
    sa.Column("name", sa.Text),
)
_AGENT_MESSAGE = sa.Table(
    "agent_message",
    _METADATA,
    sa.Column("message_id", sa.Text),
    sa.Column("body_text", sa.Text),
    sa.Column("model_text", sa.Text),
    sa.Column("search_text", sa.Text),
)

_PRIVATE_RAW = "raw-secret-value"
_PRIVATE_MODEL = "model-secret-value"
_PUBLIC = "public-value"


class _InlineThreadPool:
    """测试替身：直接把调用转发到同步实现（不启用真实线程池）。"""

    async def run_async(self, _task_type, func, *args, **kwargs):
        return func(*args, **kwargs)


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite://")
    _METADATA.create_all(eng)
    with eng.begin() as conn:
        conn.execute(_STOCK.insert(), [{"ts_code": "000001.SZ", "name": "平安银行"}])
        conn.execute(
            _AGENT_MESSAGE.insert(),
            [
                {
                    "message_id": "m1",
                    "body_text": _PRIVATE_RAW,
                    "model_text": _PRIVATE_MODEL,
                    "search_text": _PUBLIC,
                }
            ],
        )
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine):
    DataExplorerQueryClient._shared_engine = engine
    query_client = DataExplorerQueryClient()
    yield query_client
    query_client.close()
    DataExplorerQueryClient._shared_engine = None


@pytest.fixture
def vm(client):
    model = DataExplorerViewModel(db_manager=client, thread_pool=_InlineThreadPool())
    yield model
    model.dispose()


class TestAgentManagedTableExcludedFromExplorer:
    def test_catalog_excludes_agent_tables(self, client):
        """Agent 受管表不出现在数据页目录（浏览/导出的唯一入口）。"""
        assert client.get_all_tables() == ["stock_basic"]

    def test_browse_rejects_agent_table(self, client):
        """浏览路径不得返回 Agent 表结构，更不得带出私有列。"""
        with pytest.raises(ValueError, match="Invalid table name"):
            client.query_table("agent_message")
        assert client.get_table_schema("agent_message") == []

    @pytest.mark.asyncio
    async def test_csv_and_xlsx_export_do_not_contain_private_fields(self, vm):
        """导出（CSV/XLSX 共用 query_table）对 Agent 表无产物，私有字段不外泄。"""
        vm.set_table("agent_message")
        await vm.load_table_schema("agent_message")
        assert vm.state.table_columns == ()
        for current_page_only in (True, False):
            with pytest.raises(ValueError, match="Invalid table name"):
                await vm.export_data(current_page_only=current_page_only)

    @pytest.mark.asyncio
    async def test_market_table_browse_and_csv_xlsx_unchanged(self, vm):
        """普通行情表浏览/导出行为不变，且产物不含任何 Agent 私有值。"""
        await vm.init_tables()
        assert vm.state.tables_list == ("stock_basic",)

        await vm.load_table_schema("stock_basic")
        assert vm.state.table_columns == ("ts_code", "name")
        df = await vm.query_data(table_name="stock_basic")
        assert list(df.columns) == ["ts_code", "name"]

        csv_text = (await vm.export_data_bytes(df, "csv")).decode("utf-8-sig")
        assert "ts_code" in csv_text
        assert "000001.SZ" in csv_text
        assert _PRIVATE_RAW not in csv_text

        xlsx_bytes = await vm.export_data_bytes(df, "xlsx")
        workbook = load_workbook(io.BytesIO(xlsx_bytes), read_only=True)
        worksheet = workbook.active
        assert worksheet is not None
        values = [cell for row in worksheet.iter_rows(values_only=True) for cell in row]
        assert "ts_code" in values
        assert "000001.SZ" in values
        assert _PRIVATE_RAW not in values
