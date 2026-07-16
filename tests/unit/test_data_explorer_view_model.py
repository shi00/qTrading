"""Task 2.2: Data Explorer ViewModel error/empty/unknown 状态区分测试。

DoD:
1. DB 异常时 state 有错误消息,不显示"0 行/空表"为成功
2. 成功空查询仍返回空 DataFrame 且无 error
3. EngineDisposedError 在 VM 侧按 system severity 继续抛出
4. unknown/error/empty 三种状态可区分
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from data.persistence.daos.base_dao import EngineDisposedError
from ui.viewmodels import Message
from ui.viewmodels.data_explorer_view_model import DataExplorerViewModel
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit


class _FakeThreadPool:
    """伪造 ThreadPoolManager: run_async 直接调用同步函数。

    避免真实线程池调度,使测试确定且快速。
    """

    async def run_async(self, task_type: TaskType, func, *args, **kwargs):
        return func(*args, **kwargs)


def _make_vm(db_mock: MagicMock | None = None) -> DataExplorerViewModel:
    """创建带 mock db 和 fake thread pool 的 ViewModel。"""
    db = db_mock or MagicMock()
    return DataExplorerViewModel(db_manager=db, thread_pool=_FakeThreadPool())


def _db_with_schema() -> MagicMock:
    """返回带预设 schema 的 db mock(避免 query_data 中 schema 查询干扰)。"""
    db = MagicMock()
    db.get_table_schema.return_value = [{"name": "ts_code"}]
    return db


class TestInitialStateIsUnknown:
    """unknown 状态: 初始 state,未加载,error_message=None。"""

    def test_initial_state_is_unknown(self):
        vm = _make_vm()
        state = vm.state
        # unknown: 未加载,无错误,无数据
        assert state.error_message is None
        assert state.tables_loaded is False
        assert state.tables_list == ()
        assert state.table_rows == ()
        assert state.total_rows == 0


class TestInitTablesErrorSetsErrorMessage:
    """error 状态: DB 异常时 state.error_message 不为 None,不显示空表为成功。"""

    async def test_db_exception_sets_error_message(self):
        """DB 异常时 error_message 不为 None,tables_list 保持空,tables_loaded 保持 False。"""
        db = MagicMock()
        db.get_all_tables.side_effect = Exception("connection refused")
        vm = _make_vm(db)

        result = await vm.init_tables()
        assert result == []
        state = vm.state
        # error 状态: error_message 不为 None
        assert state.error_message is not None
        assert isinstance(state.error_message, Message)
        # 不显示"空表"为成功: tables_loaded 保持 False(未成功加载)
        assert state.tables_loaded is False
        assert state.tables_list == ()

    async def test_db_exception_does_not_show_empty_as_success(self):
        """DB 异常时不能出现"0 行/空表"为成功的假象。

        关键区分: error ≠ empty。
        - error: error_message 不为 None
        - empty: error_message 为 None,但结果为空
        """
        db = MagicMock()
        db.get_all_tables.side_effect = Exception("database error")
        vm = _make_vm(db)

        await vm.init_tables()
        state = vm.state
        # error 状态: 必须有 error_message
        assert state.error_message is not None
        # 不能同时表示"成功加载空表"
        assert state.tables_loaded is False


class TestInitTablesSuccessEmptyIsNotError:
    """empty 状态: 成功但表列表为空,error_message=None。"""

    async def test_success_empty_tables_no_error(self):
        """成功查询但数据库无表,error_message=None,tables_loaded=True。"""
        db = MagicMock()
        db.get_all_tables.return_value = []
        vm = _make_vm(db)

        result = await vm.init_tables()
        assert result == ()
        state = vm.state
        # empty: 无错误,但成功加载(空列表)
        assert state.error_message is None
        assert state.tables_loaded is True
        assert state.tables_list == ()


class TestQueryDataErrorSetsErrorMessage:
    """error 状态: query_data 异常时 error_message 不为 None。"""

    async def test_query_data_exception_sets_error_message(self):
        """query_data 时 DB 异常,error_message 不为 None,total_rows 不显示为 0 成功。"""
        db = MagicMock()
        db.get_table_count.side_effect = Exception("query failed")
        vm = _make_vm(db)
        # 预设 schema 已加载,避免 _build_filters 干扰
        vm._set_state(table_columns=("ts_code",))

        df = await vm.query_data()
        # 异常分支返回空 DataFrame
        assert df.empty
        state = vm.state
        # error 状态: error_message 不为 None
        assert state.error_message is not None


class TestQueryDataSuccessEmptyReturnsEmptyDf:
    """empty 状态: 成功空查询返回空 DataFrame 且无 error。"""

    async def test_success_empty_query_no_error(self):
        """成功查询但结果为空,返回空 DataFrame,error_message=None。"""
        db = MagicMock()
        db.get_table_count.return_value = 0
        db.query_table.return_value = pd.DataFrame()
        vm = _make_vm(db)
        vm._set_state(table_columns=("ts_code",))

        df = await vm.query_data()
        # 返回空 DataFrame
        assert df.empty
        state = vm.state
        # empty: 无错误
        assert state.error_message is None
        assert state.total_rows == 0
        assert state.table_rows == ()


class TestEngineDisposedErrorPropagates:
    """EngineDisposedError 按 system severity 继续抛出,不降级为 error_message。"""

    async def test_init_tables_engine_disposed_raises(self):
        """init_tables 时 EngineDisposedError 必须抛出,不写入 error_message。"""
        db = MagicMock()
        db.get_all_tables.side_effect = EngineDisposedError("Engine disposed")
        vm = _make_vm(db)

        with pytest.raises(EngineDisposedError):
            await vm.init_tables()
        # system severity: 不降级为 error_message
        assert vm.state.error_message is None

    async def test_query_data_engine_disposed_raises(self):
        """query_data 时 EngineDisposedError 必须抛出,不写入 error_message。"""
        db = _db_with_schema()
        db.get_table_count.side_effect = EngineDisposedError("Engine disposed")
        vm = _make_vm(db)

        with pytest.raises(EngineDisposedError):
            await vm.query_data()
        assert vm.state.error_message is None

    async def test_query_count_engine_disposed_raises(self):
        """query_count 时 EngineDisposedError 必须抛出。"""
        db = MagicMock()
        db.get_table_count.side_effect = EngineDisposedError("Engine disposed")
        vm = _make_vm(db)

        with pytest.raises(EngineDisposedError):
            await vm.query_count()
        assert vm.state.error_message is None


class TestThreeStatesDistinguishable:
    """unknown/error/empty 三种状态可区分(DoD #4)。"""

    def test_unknown_state(self):
        """unknown: 初始,未加载,无错误。"""
        vm = _make_vm()
        state = vm.state
        assert state.error_message is None
        assert state.tables_loaded is False

    async def test_error_state(self):
        """error: DB 异常后,error_message 不为 None。"""
        db = MagicMock()
        db.get_all_tables.side_effect = Exception("db error")
        vm = _make_vm(db)

        await vm.init_tables()
        assert vm.state.error_message is not None
        assert vm.state.tables_loaded is False

    async def test_empty_state(self):
        """empty: 成功但无数据,error_message=None,tables_loaded=True。"""
        db = MagicMock()
        db.get_all_tables.return_value = []
        vm = _make_vm(db)

        await vm.init_tables()
        assert vm.state.error_message is None
        assert vm.state.tables_loaded is True
        assert vm.state.tables_list == ()


class TestErrorMessageSanitized:
    """错误消息经 DataSanitizer.sanitize_error() 脱敏 (DoD + 约束)。"""

    async def test_format_args_sanitized(self):
        """error_message 的 format_args 中嵌入的敏感信息(如 URL 凭证)经脱敏。"""
        # ValueError 在 db context 下会产生 format_args={"error": error_str}
        # error_str 中包含敏感 URL 凭证
        sensitive_url = "postgresql://user:secret_password@host/db"
        db = MagicMock()
        db.get_all_tables.side_effect = ValueError(f"failed: {sensitive_url}")
        vm = _make_vm(db)

        await vm.init_tables()
        state = vm.state
        assert state.error_message is not None
        # format_args 中的敏感信息必须被脱敏
        params_str = str(state.error_message.params)
        # 原始密码不应出现
        assert "secret_password" not in params_str
        # 应出现脱敏标记
        assert "***" in params_str
