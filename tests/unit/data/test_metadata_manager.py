"""data.persistence.metadata_manager 单元测试。

验证 MetaDataManager 单例注册、_reset_singleton 以及 locale 隔离契约。
"""

import pytest

from core.i18n import I18n
from data.persistence.metadata_manager import MetaDataManager

pytestmark = pytest.mark.unit


class TestMetaDataManagerLocaleIsolation:
    """验证 MetaDataManager 在 locale 变更时的隔离契约。"""

    def test_get_raw_alias_isolates_by_locale(self):
        I18n.set_locale("zh_CN")
        alias_zh = MetaDataManager.get_raw_alias("close", "stock_basic")

        I18n.set_locale("en_US")
        alias_en = MetaDataManager.get_raw_alias("close", "stock_basic")

        assert alias_zh != alias_en, f"Expected different aliases for zh_CN and en_US, got {alias_zh!r}"

    def test_reset_singleton_clears_cache(self):
        MetaDataManager.get_table_alias("stock_basic")
        assert len(MetaDataManager._alias_cache) > 0

        MetaDataManager._reset_singleton()
        assert len(MetaDataManager._alias_cache) == 0, "_reset_singleton must clear _alias_cache"

    def test_get_raw_alias_handles_unhashable_term(self):
        """传递 list/dict 类型 term 不引发 TypeError: unhashable type."""
        result_list = MetaDataManager.get_raw_alias(["col1", "col2"], "stock_basic")
        assert result_list == ["col1", "col2"]

        result_dict = MetaDataManager.get_raw_alias({"key": "val"}, "stock_basic")
        assert result_dict == {"key": "val"}

    def test_get_raw_alias_handles_nested_unhashable_tuple(self):
        """tuple 顶层被 is_hashable 判定为可哈希，但内嵌 list 使 cache_key 不可哈希，
        须回退字符串键而非抛 TypeError（与顶层 unhashable 容错意图一致）。"""
        MetaDataManager.invalidate_cache()
        term = ("a", ["b"])
        result = MetaDataManager.get_raw_alias(term, "stock_basic")
        assert result == term

    def test_get_raw_alias_handles_dict_with_unhashable_items(self):
        """dict 的 value 含 dict（sorted 可排序但结果元组不可哈希）时不抛 TypeError。"""
        MetaDataManager.invalidate_cache()
        term = {"k": {"nested": 1}}
        result = MetaDataManager.get_raw_alias(term, "stock_basic")
        assert result == term
