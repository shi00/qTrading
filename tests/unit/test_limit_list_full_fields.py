"""
LimitList 完整字段保存守护测试

背景：limit_list_d 接口（Tushare doc_id=298）官方文档定义 18 个输出字段。
此前项目仅保存 10 个字段，遗漏 8 个 API 实际返回的字段（industry, amount,
limit_amount, float_mv, total_mv, turnover_ratio, up_stat, limit_times），
导致后续策略层需要这些字段时无法直接读取，必须重新调 API。

本测试守护"API 返回的全部字段必须落表"原则，防止字段再次缺失。

注：amp/fc_ratio/fl_ratio/strth 属于旧 limit_list 接口，limit_list_d
不提供，由 test_limit_list_dead_columns.py 守护其永久删除状态。

Run: pytest tests/unit/test_limit_list_full_fields.py -v
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


# limit_list_d 接口（Tushare doc_id=298）官方定义的全部 18 个输出字段
# 顺序按官方文档列示，与 ORM 字段顺序无关
EXPECTED_LIMIT_LIST_FIELDS: set[str] = {
    "trade_date",
    "ts_code",
    "industry",
    "name",
    "close",
    "pct_chg",
    "amount",
    "limit_amount",
    "float_mv",
    "total_mv",
    "turnover_ratio",
    "fd_amount",
    "first_time",
    "last_time",
    "open_times",
    "up_stat",
    "limit_times",
    "limit",
}

# 2026-06-27 新增的 8 个字段（此前未保存）
NEWLY_ADDED_FIELDS: set[str] = {
    "industry",
    "amount",
    "limit_amount",
    "float_mv",
    "total_mv",
    "turnover_ratio",
    "up_stat",
    "limit_times",
}

# 新增字段对应的 i18n 键（data_dictionary 中映射的目标）
# col_industry/col_amount/col_float_mv/col_total_mv 已在 COMMON_COLUMNS 中，
# 由 test_i18n_keys_completeness.py 守护；本测试仅守护 limit_list 表特有的 4 个新增 i18n 键
NEW_I18N_KEYS: set[str] = {"col_limit_amount", "col_turnover_ratio", "col_up_stat", "col_limit_times"}

# ORM 元字段，不参与 API 字段对齐
ORM_META_COLUMNS: set[str] = {"updated_at", "created_at"}


class TestLimitListFullFields:
    """验证 limit_list_d 接口的 18 个字段全部落表，防止再出现"干一半"问题。"""

    def test_orm_contains_all_18_fields(self) -> None:
        """ORM 必须包含 limit_list_d 接口的全部 18 个数据字段。

        缺失任一字段意味着 _save_upsert 写入时该列会被忽略，导致数据丢失。
        """
        orm_cols = {c.name for c in LimitList.__table__.columns} - ORM_META_COLUMNS
        missing = EXPECTED_LIMIT_LIST_FIELDS - orm_cols
        assert not missing, (
            f"LimitList ORM 缺失 limit_list_d 接口字段 {missing}，会导致 _save_upsert 写入时丢失这些数据"
        )

    def test_api_fields_requests_all_18_fields(self) -> None:
        """get_limit_list 的 fields 参数必须请求全部 18 个字段。

        缺失任一字段意味着 Tushare 不会返回该字段，DataFrame 中无对应列。
        """
        api_fields = extract_fields_from_api_method(TushareClient.get_limit_list)
        # 防御 helper 静默失败导致空真通过
        assert api_fields, "extract_fields_from_api_method 返回空集，可能 helper 失败导致断言空真通过"
        missing = EXPECTED_LIMIT_LIST_FIELDS - api_fields
        assert not missing, (
            f"get_limit_list fields 参数未请求 limit_list_d 接口字段 {missing}，"
            f"Tushare 不会返回这些字段，DataFrame 中无对应列"
        )

    def test_data_dictionary_contains_all_18_fields(self) -> None:
        """data_dictionary 必须包含 limit_list 表全部 18 个字段的映射。

        缺失映射会导致 UI 展示时找不到对应 i18n 键，影响数据透明度。
        """
        dict_cols = set(TABLE_DEFINITIONS["limit_list"]["columns"].keys())
        missing = EXPECTED_LIMIT_LIST_FIELDS - dict_cols
        assert not missing, f"data_dictionary['limit_list']['columns'] 缺失字段映射 {missing}"

    @pytest.mark.parametrize("locale_folder", ["zh_CN", "en_US"])
    def test_locale_contains_new_i18n_keys(self, locale_folder: str) -> None:
        """i18n 资源文件必须包含新增字段对应的 i18n 键。

        col_industry/col_amount/col_float_mv/col_total_mv 已被其他表复用，
        无需新增；只需守护 col_limit_amount/col_turnover_ratio/col_up_stat/col_limit_times。
        """
        strings_path = Path(__file__).resolve().parents[2] / "locales" / locale_folder / "strings.json"
        with open(strings_path, encoding="utf-8") as f:
            strings = json.load(f)
        missing = NEW_I18N_KEYS - set(strings.keys())
        assert not missing, f"locales/{locale_folder}/strings.json 缺失新增 i18n 键 {missing}"


class TestAlembic0004Migration:
    """验证 0004 迁移完整包含 8 个新增字段的 add_column。"""

    def test_0004_migration_adds_all_new_fields(self) -> None:
        """0004 迁移必须包含全部 8 个新增字段的 add_column 调用。

        缺失任一字段意味着升级后历史库的 limit_list 表没有该列，
        新数据写入会因列不存在而失败。
        """
        alembic_path = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0004_add_limit_list_full_fields.py"
        )
        assert alembic_path.exists(), "0004_add_limit_list_full_fields.py 迁移文件不存在"
        source = alembic_path.read_text(encoding="utf-8")

        # 提取 _NEW_COLUMNS 列表块内所有 ("xxx", sa.XXX) 的列名
        # 限定到列表块，避免 docstring/注释中同模式文本误匹配
        list_pattern = r"_NEW_COLUMNS[^=]*=\s*\[(.*?)\]"
        list_match = re.search(list_pattern, source, re.DOTALL)
        assert list_match, "0004 迁移未找到 _NEW_COLUMNS 列表定义"
        col_pattern = r'\(\s*"(\w+)"\s*,\s*sa\.'
        migrated_cols = set(re.findall(col_pattern, list_match.group(1)))
        assert migrated_cols, "0004 迁移 _NEW_COLUMNS 列表为空，正则提取可能失败"

        missing = NEWLY_ADDED_FIELDS - migrated_cols
        assert not missing, f"0004 迁移未包含新增字段 {missing} 的 add_column"

    def test_0004_migration_revision_chain(self) -> None:
        """0004 迁移的 down_revision 必须指向 0003，保证迁移链单调。"""
        alembic_path = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0004_add_limit_list_full_fields.py"
        )
        source = alembic_path.read_text(encoding="utf-8")

        # 只断言变量名 + 赋值，不锁定中间类型注解形态（避免 alembic 模板风格调整误报）
        revision_match = re.search(r'^revision\s*:\s*str\s*=\s*"(\w+)"', source, re.MULTILINE)
        down_rev_match = re.search(r'^down_revision\s*:[^=]*=\s*"(\w+)"', source, re.MULTILINE)
        assert revision_match and revision_match.group(1) == "0004", "0004 迁移 revision 必须为 '0004'"
        assert down_rev_match and down_rev_match.group(1) == "0003", "0004 迁移 down_revision 必须指向 '0003'"
