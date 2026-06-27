"""
LimitList 死列清除验证测试

背景：limit_list_d API 不提供 amp/fc_ratio/fl_ratio/strth 四个字段，
但 ORM / data_dictionary / locales / API fields 参数中残留了这些定义，
导致 _save_upsert 每次同步都打印 "Missing columns in dataframe" WARNING，
且 API 请求中带不存在字段被 Tushare 静默忽略。

本测试守护修复成果，防止死列回归。

Run: pytest tests/unit/test_limit_list_dead_columns.py -v
"""

import json
import re
from pathlib import Path

import pytest

from data.data_dictionary import TABLE_DEFINITIONS
from data.external.tushare_client import TushareClient
from data.persistence.models import LimitList

from tests._helpers import extract_fields_from_api_method


pytestmark = pytest.mark.unit


# 死列常量：limit_list_d API 从未提供，必须从所有引用点清除
DEAD_COLUMNS: set[str] = {"amp", "fc_ratio", "fl_ratio", "strth"}

# 死列对应的 i18n 键（data_dictionary 中映射的目标）
DEAD_I18N_KEYS: set[str] = {"col_amp", "col_fc_ratio", "col_fl_ratio", "col_strth"}


class TestLimitListDeadColumnsRemoved:
    """验证 LimitList 表的 4 个死列已从所有引用点彻底清除。"""

    def test_limit_list_orm_no_dead_columns(self) -> None:
        """ORM 模型不得包含死列，否则 _save_upsert 会因 df 缺列触发 WARNING。"""
        orm_cols = {c.name for c in LimitList.__table__.columns}
        leaked = DEAD_COLUMNS & orm_cols
        assert not leaked, (
            f"LimitList ORM 仍含死列 {leaked}，这些字段 limit_list_d API 不提供，"
            f"会导致 _save_upsert 打印 'Missing columns' WARNING 并写入 NULL"
        )

    def test_limit_list_api_fields_no_dead_fields(self) -> None:
        """API fields 参数不得请求不存在的字段，否则被 Tushare 静默忽略。"""
        api_fields = extract_fields_from_api_method(TushareClient.get_limit_list)
        # 防御 helper 静默失败导致空真通过：helper 在 inspect.getsource 失败时返回 set()
        assert api_fields, "extract_fields_from_api_method 返回空集，可能 helper 失败导致断言空真通过"
        leaked = DEAD_COLUMNS & api_fields
        assert not leaked, (
            f"get_limit_list fields 仍请求死字段 {leaked}，这些字段 limit_list_d API 不提供，会被 Tushare 静默忽略"
        )

    def test_data_dictionary_limit_list_no_dead_cols(self) -> None:
        """data_dictionary 不得保留死列映射，否则与 ORM 不一致。"""
        dict_cols = set(TABLE_DEFINITIONS["limit_list"]["columns"].keys())
        leaked = DEAD_COLUMNS & dict_cols
        assert not leaked, f"data_dictionary['limit_list']['columns'] 仍含死列映射 {leaked}，应与 ORM 同步清除"

    @pytest.mark.parametrize("locale_folder", ["zh_CN", "en_US"])
    def test_locale_no_dead_col_keys(self, locale_folder: str) -> None:
        """i18n 资源文件不得保留死列对应的键，否则造成无用翻译条目。"""
        strings_path = Path(__file__).resolve().parents[2] / "locales" / locale_folder / "strings.json"
        with open(strings_path, encoding="utf-8") as f:
            strings = json.load(f)
        leaked = DEAD_I18N_KEYS & set(strings.keys())
        assert not leaked, (
            f"locales/{locale_folder}/strings.json 仍含死列 i18n 键 {leaked}，这些键对应的列已从 ORM 删除，应同步清除"
        )

    def test_limit_list_alembic_0001_no_dead_columns(self) -> None:
        """Alembic 0001 源码中 limit_list 表的列定义不得包含死列。

        0001 使用 _create_table_if_not_exists 幂等助手，新建库时直接生成干净 schema。
        若有人在 0001 中重新加回死列，新库会带死列，需此守护。
        """
        alembic_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0001_initial_schema.py"
        source = alembic_path.read_text(encoding="utf-8")

        # 提取 _create_table_if_not_exists("limit_list", ...) 块，
        # 非贪婪匹配到下一个 _create/_drop_table_if_exists 或文件末尾
        block_pattern = r'_create_table_if_not_exists\(\s*"limit_list",(.*?)(?=_create_table_if_not_exists|_drop_table_if_exists|\Z)'
        match = re.search(block_pattern, source, re.DOTALL)
        assert match, "未在 0001_initial_schema.py 中找到 limit_list 表定义"
        block = match.group(1)

        # 提取块内所有 sa.Column("xxx", ...) 的列名
        col_pattern = r'sa\.Column\(\s*"(\w+)"'
        alembic_cols = set(re.findall(col_pattern, block))

        leaked = DEAD_COLUMNS & alembic_cols
        assert not leaked, (
            f"alembic/versions/0001_initial_schema.py 中 limit_list 表定义仍含死列 {leaked}，应与 ORM 同步清除"
        )
