"""BacktestConfig 默认值测试"""
# pyright: reportArgumentType=false

from datetime import date, datetime

import polars as pl

from strategies.backtest.config import (
    BacktestConfig,
    BacktestResult,
    DataWarning,
    WarningCategory,
)


class TestBacktestConfigDefaults:
    def test_default_stamp_duty_rate_is_auto(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.stamp_duty_rate is None

    def test_explicit_stamp_duty_rate(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            stamp_duty_rate=1e-3,
        )
        assert config.stamp_duty_rate == 1e-3

    def test_stamp_duty_rate_zero(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            stamp_duty_rate=0.0,
        )
        assert config.stamp_duty_rate == 0.0

    def test_get_cost_config_stamps_duty_none(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        cost_config = config.get_cost_config()
        assert cost_config.stamp_duty_rate is None

    def test_get_cost_config_stamps_duty_explicit(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            stamp_duty_rate=5e-4,
        )
        cost_config = config.get_cost_config()
        assert cost_config.stamp_duty_rate == 5e-4

    def test_default_commission_rate(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.commission_rate == 3e-4

    def test_default_initial_capital(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.initial_capital == 1_000_000.0

    def test_default_preload_max_days(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.preload_max_days == 366

    def test_custom_preload_max_days(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=730,
        )
        assert config.preload_max_days == 730


class TestBacktestConfigValidation:
    def test_validate_valid_config(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        errors = config.validate()
        assert len(errors) == 0

    def test_validate_invalid_date_range(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 12, 31),
            end_date=date(2023, 1, 1),
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "start_date must be before end_date" in errors[0]

    def test_validate_negative_capital(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            initial_capital=-1000.0,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "initial_capital must be positive" in errors[0]

    def test_validate_non_finite_capital_rejected(self) -> None:
        """UX-05 对抗检视 MAJOR: inf/nan 必须被 validate 拦截 (<=0 无法排除非有限数)。"""
        for bad in (float("inf"), float("nan")):
            config = BacktestConfig(
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                initial_capital=bad,
            )
            errors = config.validate()
            assert len(errors) == 1
            assert "initial_capital must be positive" in errors[0]

    def test_validate_preload_max_days_zero(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=0,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "preload_max_days must be at least 30" in errors[0]

    def test_validate_preload_max_days_below_minimum(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=29,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "preload_max_days must be at least 30" in errors[0]

    def test_validate_preload_max_days_at_minimum(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=30,
        )
        errors = config.validate()
        assert len(errors) == 0

    def test_validate_delist_recovery_rate_zero(self) -> None:
        """BT-02: delist_recovery_rate 必须 > 0。"""
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            delist_recovery_rate=0.0,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "delist_recovery_rate must be in (0, 1]" in errors[0]

    def test_validate_delist_recovery_rate_above_one(self) -> None:
        """BT-02: delist_recovery_rate 必须 <= 1。"""
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            delist_recovery_rate=1.5,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "delist_recovery_rate must be in (0, 1]" in errors[0]

    def test_validate_delist_recovery_rate_default_ok(self) -> None:
        """BT-02: 默认 0.3 落在 (0, 1]，应通过校验。"""
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        errors = config.validate()
        assert not any("delist_recovery_rate" in e for e in errors)


def _make_result(**overrides) -> BacktestResult:
    """构造 BacktestResult 测试实例，支持覆盖部分字段。"""

    # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
    # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
    # 测试行为由测试用例本身验证。

    config = overrides.pop("config", None) or BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
        execution_price="next_open",
        allow_limit_up_buy=False,
        allow_limit_down_sell=False,
        slippage_model="fixed_bps",
    )
    defaults = dict(
        config=config,
        strategy_name="test_strategy",
        params_snapshot={"param1": "value1"},
        nav_curve=pl.DataFrame({"trade_date": [date(2024, 1, 1)], "nav": [1_000_000.0]}),
        daily_returns=pl.Series([0.0]),
        benchmark_returns=pl.Series([0.0]),
        trades=pl.DataFrame(),
        positions=pl.DataFrame(),
        skipped_orders=pl.DataFrame(),
        metrics={"total_return": 0.01, "sharpe_ratio": 1.5},
        ic_series=pl.Series([0.02]),
        ic_dates=pl.Series(dtype=pl.Date),
        period_stats=pl.DataFrame(),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="test_run_001",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=1000,
    )
    defaults.update(overrides)
    return BacktestResult(**defaults)


class TestBacktestResultToPersistDict:
    """Task 6.10: BacktestResult.to_persist_dict() 持久化字典生成。"""

    def test_to_persist_dict_contains_all_required_fields(self) -> None:
        result = _make_result()
        d = result.to_persist_dict()
        expected_keys = {
            "run_id",
            "strategy_name",
            "params_snapshot",
            "start_date",
            "end_date",
            "initial_capital",
            "metrics",
            "nav_curve",
            "trades",
            "period_stats",
            "duration_ms",
            "execution_price",
            "allow_limit_up_buy",
            "allow_limit_down_sell",
            "slippage_model",
        }
        assert expected_keys.issubset(set(d.keys()))

    def test_to_persist_dict_excludes_app_version(self) -> None:
        """app_version 由调用方（BacktestService）补充，不应出现在结果字典中。"""
        result = _make_result()
        d = result.to_persist_dict()
        assert "app_version" not in d

    def test_to_persist_dict_flattens_config_fields(self) -> None:
        """config 中的字段应被平铺到顶层，而非嵌套在 config 子字典中。"""
        result = _make_result()
        d = result.to_persist_dict()
        assert d["start_date"] == result.config.start_date
        assert d["end_date"] == result.config.end_date
        assert d["initial_capital"] == result.config.initial_capital
        assert d["execution_price"] == result.config.execution_price
        assert d["allow_limit_up_buy"] == result.config.allow_limit_up_buy
        assert d["allow_limit_down_sell"] == result.config.allow_limit_down_sell
        assert d["slippage_model"] == result.config.slippage_model
        assert "config" not in d

    def test_to_persist_dict_references_result_attributes(self) -> None:
        """顶层结果字段应直接引用 BacktestResult 属性。"""
        result = _make_result()
        d = result.to_persist_dict()
        assert d["run_id"] == result.run_id
        assert d["strategy_name"] == result.strategy_name
        # D1-m3: params_snapshot 为并入 delist_recovery_rate 后的新 dict，不再与结果引用同一对象；
        # 保留原策略参数内容由 test_to_persist_dict_embeds_recovery_rate 覆盖。
        assert d["params_snapshot"]["param1"] == result.params_snapshot["param1"]
        assert d["metrics"] is result.metrics
        assert d["nav_curve"] is result.nav_curve
        assert d["trades"] is result.trades
        assert d["period_stats"] is result.period_stats
        assert d["duration_ms"] == result.duration_ms

    def test_to_persist_dict_embeds_recovery_rate_in_params_snapshot(self) -> None:
        """D1-m3: delist_recovery_rate 并入 params_snapshot 落库（JSONB，免迁移），
        使历史回测可复现该退市回收率假设；原策略参数不被覆盖。"""
        result = _make_result(
            config=BacktestConfig(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                delist_recovery_rate=0.15,
            )
        )
        d = result.to_persist_dict()
        assert d["params_snapshot"]["delist_recovery_rate"] == 0.15
        assert d["params_snapshot"]["param1"] == "value1"

    def test_to_persist_dict_embeds_config_json(self) -> None:
        """BT-03: config_json 全量快照包含完整 BacktestConfig（含 date 等不可平铺字段）。"""
        result = _make_result()
        d = result.to_persist_dict()
        cfg = d["config_json"]
        assert cfg is not None
        assert cfg["start_date"] == result.config.start_date
        assert cfg["end_date"] == result.config.end_date
        assert cfg["initial_capital"] == result.config.initial_capital
        assert cfg["rebalance_freq"] == result.config.rebalance_freq
        assert cfg["benchmark_code"] == result.config.benchmark_code
        assert cfg["risk_free_rate"] == result.config.risk_free_rate

    def test_to_persist_dict_embeds_quality_json(self) -> None:
        """BT-03: quality_json 快照包含可信度元数据，供列表/详情区分干净与带警告回测。"""
        result = _make_result(
            data_warnings=("suspend_data_absent: ...",),
            failed_signal_dates=({"signal_date": "2024-01-02"},),
            delist_liquidation_count=2,
            delist_loss_amount=150.5,
            has_real_score=False,
        )
        d = result.to_persist_dict()
        q = d["quality_json"]
        assert q is not None
        assert q["data_warnings"] == ["suspend_data_absent: ..."]
        assert q["failed_signal_dates"] == [{"signal_date": "2024-01-02"}]
        assert q["skipped_order_count"] == 0
        assert q["delist_liquidation_count"] == 2
        assert q["delist_loss_amount"] == 150.5
        assert q["has_real_score"] is False

    def test_to_persist_dict_quality_json_skipped_order_count(self) -> None:
        """BT-03: skipped_order_count 取 skipped_orders 行数（非空 DataFrame）。"""
        result = _make_result(
            skipped_orders=pl.DataFrame({"ts_code": ["000001.SZ"], "reason": ["up_limit"]}),
        )
        d = result.to_persist_dict()
        assert d["quality_json"]["skipped_order_count"] == 1

    def test_to_persist_dict_excludes_no_persist_column_flatten_fields(self) -> None:
        """BT-09: to_persist_dict 不得含无落库列的平铺配置字段（历史死代码 on_empty_signal）。

        on_empty_signal 等配置已由 config_json（asdict 完整快照）承载；平铺键若保留，
        读代码者会误判「无信号处理方式可追溯」——实际 _save_upsert 按 ORM 列取列，
        多余键被静默忽略。此处断言无落库列名的平铺字段不存在，与 DAO/模型列一致。
        """
        result = _make_result()
        d = result.to_persist_dict()
        assert "on_empty_signal" not in d
        # config_json 仍承载完整配置（含 on_empty_signal），保证可复现性不被破坏
        assert d["config_json"]["on_empty_signal"] == result.config.on_empty_signal


def _dw(warning_type: str, category: str | None = None) -> DataWarning:
    """构造 DataWarning 辅助：默认不传 category，验证 __post_init__ 解析。"""
    kwargs: dict[str, object] = {
        "warning_type": warning_type,
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
        "affected_stock_count": 1,
        "error_message": "test",
    }
    if category is not None:
        kwargs["category"] = category
    return DataWarning(**kwargs)


class TestWarningCategory:
    """MAJOR-01: DataWarning.category 类型化 + WarningCategory.category_of 解析一正一邪。"""

    def test_datawarning_omits_category_resolves_from_map(self) -> None:
        """未显式传 category → __post_init__ 按 warning_type 解析（data_quality）。"""
        w = _dw("suspend_data_absent")
        assert w.category == "data_quality"

    def test_datawarning_explicit_category_overrides_map(self) -> None:
        """显式传 category → 以显式值为准（性能路径不升级级别）。"""
        w = _dw("preload_range_too_wide", category=WarningCategory.PERFORMANCE_PATH)
        assert w.category == "performance_path"

    def test_datawarning_unknown_type_falls_back_fail_closed(self) -> None:
        """未知 warning_type → 回退 data_quality（fail-closed，R21）。"""
        w = _dw("some_future_type")
        assert w.category == "data_quality"

    def test_category_of_parses_legacy_bracket_prefix(self) -> None:
        """历史落库字符串 '[range_quality_gaps] ...' → data_quality。"""
        assert WarningCategory.category_of("[range_quality_gaps] 区间缺口") == "data_quality"
        assert WarningCategory.category_of("[preload_range_too_wide] 超宽") == "performance_path"

    def test_category_of_parses_fail_closed_phrase_and_unknown_none(self) -> None:
        """无 [type] 前缀：真实异常关键词 fail-closed；旧撮合噪音 → None（非 unreliable）。"""
        assert WarningCategory.category_of("suspend_data_absent: 600001") == "data_quality"
        assert WarningCategory.category_of("600001 valued at last known price for 30 days") == "data_quality"
        assert WarningCategory.category_of("some legacy skip noise") is None

    def test_category_of_structual_uses_field(self) -> None:
        """结构化 DataWarning → 直接用 .category 字段。"""
        assert WarningCategory.category_of(_dw("portfolio_wiped_out", category="termination")) == "termination"

    def test_to_persist_serializes_datawarning_as_str(self) -> None:
        """to_persist_dict 对 DataWarning 统一按 str(w) 落库，保留 [type] 前缀。"""
        result = _make_result(data_warnings=(_dw("suspend_data_absent"),))
        d = result.to_persist_dict()
        assert d["quality_json"]["data_warnings"] == [
            "[suspend_data_absent] 2024-01-01-2024-01-31: 1 stocks affected. test"
        ]
