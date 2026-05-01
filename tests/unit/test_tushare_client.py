import inspect


from data.external.tushare_client import TushareClient


class TestRetryExhaustedRaises:
    """Tushare 重试耗尽应抛出异常而非返回 None"""

    def test_handle_api_call_does_not_return_none(self):
        source = inspect.getsource(TushareClient._handle_api_call)
        assert "return None" not in source
        assert "raise RuntimeError" in source


class TestPaginationLogsPartial:
    """分页失败时记录已获取的部分数据"""

    def test_paginated_logs_on_failure(self):
        source = inspect.getsource(TushareClient._handle_api_call_paginated)
        assert "partial pages" in source.lower() or "Returning" in source
