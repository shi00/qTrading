import inspect


from data.cache.cache_manager import CacheManager
from data.persistence.daos.base_dao import BaseDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.sync_dao import SyncDao


class TestSuppressErrorsParameter:
    """BaseDao suppress_errors 参数存在性及默认值"""

    def test_read_db_has_suppress_errors_param(self):
        sig = inspect.signature(BaseDao._read_db)
        assert "suppress_errors" in sig.parameters
        assert sig.parameters["suppress_errors"].default is True

    def test_write_db_has_suppress_errors_param(self):
        sig = inspect.signature(BaseDao._write_db)
        assert "suppress_errors" in sig.parameters
        assert sig.parameters["suppress_errors"].default is True

    def test_save_upsert_has_suppress_errors_param(self):
        sig = inspect.signature(BaseDao._save_upsert)
        assert "suppress_errors" in sig.parameters
        assert sig.parameters["suppress_errors"].default is True


class TestSuppressErrorsPropagation:
    """suppress_errors 参数在调用链中的透传"""

    def test_quote_dao_get_daily_quotes_has_suppress_errors(self):
        sig = inspect.signature(QuoteDao.get_daily_quotes)
        assert "suppress_errors" in sig.parameters
        assert sig.parameters["suppress_errors"].default is True

    def test_cache_manager_get_daily_quotes_has_suppress_errors(self):
        sig = inspect.signature(CacheManager.get_daily_quotes)
        assert "suppress_errors" in sig.parameters
        assert sig.parameters["suppress_errors"].default is True

    def test_quote_dao_get_daily_quotes_passes_suppress_errors(self):
        source = inspect.getsource(QuoteDao.get_daily_quotes)
        assert "suppress_errors=suppress_errors" in source, "get_daily_quotes must pass suppress_errors to _read_db"

    def test_cache_manager_get_daily_quotes_passes_suppress_errors(self):
        source = inspect.getsource(CacheManager.get_daily_quotes)
        assert "suppress_errors=suppress_errors" in source, (
            "CacheManager.get_daily_quotes must pass suppress_errors to quote_dao"
        )


class TestRaiseOnErrorParameter:
    """raise_on_error 参数用于区分'数据不存在'和'查询失败'"""

    def test_check_data_exists_has_raise_on_error(self):
        sig = inspect.signature(QuoteDao.check_data_exists)
        assert "raise_on_error" in sig.parameters
        assert sig.parameters["raise_on_error"].default is False

    def test_get_completed_step4_has_raise_on_error(self):
        sig = inspect.signature(SyncDao.get_completed_step4_stocks)
        assert "raise_on_error" in sig.parameters
        assert sig.parameters["raise_on_error"].default is False

    def test_check_data_exists_passes_suppress_errors(self):
        source = inspect.getsource(QuoteDao.check_data_exists)
        assert "suppress_errors=not raise_on_error" in source, (
            "check_data_exists must pass suppress_errors=not raise_on_error to _read_db"
        )

    def test_get_completed_step4_passes_suppress_errors(self):
        source = inspect.getsource(SyncDao.get_completed_step4_stocks)
        assert "suppress_errors=not raise_on_error" in source, (
            "get_completed_step4_stocks must pass suppress_errors=not raise_on_error to _read_db"
        )

    def test_check_data_exists_reraises_on_raise_on_error(self):
        source = inspect.getsource(QuoteDao.check_data_exists)
        lines = source.split("\n")
        in_except = False
        has_reraise = False
        for line in lines:
            if "except Exception" in line:
                in_except = True
            if in_except and "if raise_on_error" in line:
                has_reraise = True
        assert has_reraise, "check_data_exists must re-raise when raise_on_error=True"

    def test_get_completed_step4_reraises_on_raise_on_error(self):
        source = inspect.getsource(SyncDao.get_completed_step4_stocks)
        lines = source.split("\n")
        in_except = False
        has_reraise = False
        for line in lines:
            if "except Exception" in line:
                in_except = True
            if in_except and "if raise_on_error" in line:
                has_reraise = True
        assert has_reraise, "get_completed_step4_stocks must re-raise when raise_on_error=True"
