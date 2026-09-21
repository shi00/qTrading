"""OSS-01 数据字典派生态回归测试。

背景（docs/reviews/开源组件使用检视报告.md §2）：数据字典的列级 i18n 标签从
``TABLE_DEFINITIONS`` 硬编码改为从 ORM ``Base.metadata`` + ``Column.info["i18n"]`` 派生，
``columns_of`` / ``column_i18n_key`` 为唯一解析入口。

本测试守护两条不变量：
1. **LEGACY 快照等价**：派生态对重构前硬编码逐列映射的等价性（避免 UI 显示回退）。
2. **解析顺序契约**：info 覆盖 > COMMON 兜底 > 派生约定（含 I18n.has 探测）。

Run: pytest tests/unit/test_data_dictionary_derivation.py -v
"""

import json
from pathlib import Path

import pytest

from data.data_dictionary import COMMON_COLUMNS, _NON_ORM_COLUMN_LABELS, column_i18n_key, columns_of

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_SNAPSHOT_PATH = REPO_ROOT / "tests" / "legacy_data_dictionary_columns.json"


def _load_legacy_snapshot() -> dict:
    with open(LEGACY_SNAPSHOT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert "tables" in data and "common" in data, "LEGACY 快照格式异常（应含 tables/common 两个顶层键）"
    return data


def test_legacy_snapshot_exists():
    """LEGACY 快照必须存在（重构前硬编码映射的权威基准）。"""
    assert LEGACY_SNAPSHOT_PATH.exists(), f"缺失 LEGACY 快照：{LEGACY_SNAPSHOT_PATH}"


class TestDerivedLabelsMatchLegacySnapshot:
    """派生结果必须与重构前的硬编码清单逐列相等（OSS-01 核心回归网）。"""

    @pytest.fixture()
    def snapshot(self):
        return _load_legacy_snapshot()

    def test_table_columns_match_legacy(self, snapshot):
        for table_name, legacy_cols in snapshot["tables"].items():
            for col, expected_key in legacy_cols.items():
                assert column_i18n_key(table_name, col) == expected_key, (
                    f"{table_name}.{col} 派生 key 与 LEGACY 快照不一致: "
                    f"expected={expected_key} got={column_i18n_key(table_name, col)}"
                )

    def test_common_columns_match_legacy(self, snapshot):
        for col, expected_key in snapshot["common"].items():
            assert column_i18n_key(None, col) == expected_key, (
                f"COMMON.{col} 派生 key 与 LEGACY 快照不一致: expected={expected_key} got={column_i18n_key(None, col)}"
            )


class TestColumnI18nKeyResolutionOrder:
    """column_i18n_key 解析顺序契约（OSS-01 实证与快照逐列一致的唯一顺序）。"""

    def test_info_override_wins_over_common(self):
        # top_list.pct_change 的 info 覆盖 COMMON 的 pct_change→col_pct_chg
        assert column_i18n_key("top_list", "pct_change") == "col_pct_change"

    def test_common_wins_over_derived(self):
        # 其他表的 pct_change（无 info）走 COMMON → col_pct_chg
        assert column_i18n_key(None, "pct_change") == "col_pct_chg"

    def test_derived_convention_when_key_defined(self):
        # ORM 列 col_<列名> 已定义 → 派生约定
        assert column_i18n_key("stock_basic", "ts_code") == "col_ts_code"

    def test_non_orm_table_explicit_labels(self):
        # alembic_version 不在 Base.metadata，走 _NON_ORM_COLUMN_LABELS
        explicit = _NON_ORM_COLUMN_LABELS
        assert "alembic_version" in explicit
        assert explicit["alembic_version"]["version_num"] == "col_version_num"

    def test_unknown_column_returns_none(self):
        # 未知列 + 无 COMMON + 派生 key 未定义 → None（调用方回退裸列名）
        assert column_i18n_key("stock_basic", "definitely_not_a_column_xyz") is None


class TestColumnsOf:
    """columns_of 纯结构查询，不触碰 I18n。"""

    def test_orm_table_columns(self):
        cols = columns_of("daily_quotes")
        assert "ts_code" in cols
        assert "trade_date" in cols
        assert isinstance(cols, frozenset)

    def test_non_orm_table_returns_explicit(self):
        assert columns_of("alembic_version") == frozenset({"version_num"})

    def test_unknown_table_returns_empty(self):
        assert columns_of("no_such_table_xyz") == frozenset()

    def test_no_i18n_pollution(self):
        # columns_of 不得污染 I18n._missing_keys（核心不变量：纯结构查询）
        from core.i18n import I18n

        I18n._missing_keys.clear()
        columns_of("daily_quotes")
        assert not I18n._missing_keys, "columns_of 不应触发 I18n 查询/污染 _missing_keys"


class TestNoPhantomDerivedTranslations:
    """派生态不得为"ORM 有而旧数据字典无"的列新增翻译（避免 UI 行为回归）。

    OSS-01 实证：ORM 全部列中，凡"旧数据字典（LEGACY 快照）未覆盖"的列，
    其 ``col_<c>`` 均未在 locales 中定义（2026-09 复查，命中数为 0）。
    本测试机械兜底：派生约定不得对"LEGACY 快照之外"的列静默启用——
    一旦未来新增 ORM 列且 locales 恰好定义了 ``col_<c>``，UI 会从裸列名悄悄
    变成翻译，本测试立即失败，强制该列显式登记（info/COMMON/快照）。
    """

    def test_orm_extra_columns_resolve_to_none_or_common(self):
        from data.persistence.models import Base

        snapshot = _load_legacy_snapshot()
        legacy_tables = snapshot["tables"]

        for table_name, table_obj in Base.metadata.tables.items():
            if table_name == "alembic_version":
                continue
            legacy_cols = legacy_tables.get(table_name, {})
            for col in table_obj.columns:
                key = column_i18n_key(table_name, col.name)
                if key is None:
                    continue
                # 非 None 的 key 必须来自显式三通道之一：info 覆盖 / COMMON 兜底 / LEGACY 快照登记
                legit = {
                    col.info.get("i18n"),
                    COMMON_COLUMNS.get(col.name),
                    legacy_cols.get(col.name),
                }
                assert key in legit, (
                    f"{table_name}.{col.name} 解析出快照之外的派生 key={key}"
                    f"（不在 info/COMMON/LEGACY 快照中，属新增翻译，需显式登记）"
                )
