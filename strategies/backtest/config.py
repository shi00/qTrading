"""回测配置数据类"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import ClassVar, Literal

import polars as pl

from data.constants import DEFAULT_BENCHMARK_INDEX
from data.domain_services.transaction_cost import TransactionCostConfig


@dataclass(frozen=True)
class DataWarning:
    """回测数据警告（结构化）

    用于记录 enrichment 失败时的详细信息，便于用户理解数据质量问题。
    自 MAJOR-01 起新增 ``category`` 维度，将原本混装的「撮合日志 / 性能提示」与
    「真实数据质量 / 终止条件」分离，供 UI 可信度分级（``_assess_credibility``）使用。
    """

    warning_type: Literal[
        "suspend_enrich_failed",
        "limit_enrich_failed",
        "suspend_data_absent",
        "limit_data_absent",
        "benchmark_data_absent",
        "benchmark_data_partial",
        "portfolio_wiped_out",
        # 由 portfolio/engine 历史字符串类型化的真实 type（MAJOR-01）。
        "stale_estimate",
        "range_quality_gaps",
        "preload_range_too_wide",
        "range_preload_failed",
        "range_preload_error",
    ]
    start_date: str
    end_date: str
    affected_stock_count: int
    error_message: str
    # MAJOR-01: 告警类型化维度（决策①）。
    # - data_quality：enrich 失败 / 停牌·涨跌停数据缺失 / 基准缺失·部分缺失 /
    #   区间缺口 range_quality_gaps / 长期旧价估值 stale-estimate → unreliable。
    # - termination：portfolio_wiped_out（爆仓）→ unreliable。
    # - performance_path：preload_range_too_wide / range_preload_failed /
    #   range_preload_error（慢路径）→ 仅提示，不升级级别。
    # 默认 None：经 ``__post_init__`` 从 ``WarningCategory._TYPE_TO_CATEGORY``
    # 按 warning_type 解析（历史构造点无需逐一补参）；未知 warning_type 回退
    # data_quality（fail-closed，避免把已知异常伪装成「无信息」，R21）。
    category: Literal["data_quality", "termination", "performance_path"] | None = None

    def __post_init__(self) -> None:
        if self.category is None:
            object.__setattr__(
                self,
                "category",
                WarningCategory._TYPE_TO_CATEGORY.get(self.warning_type, WarningCategory.DATA_QUALITY),
            )

    def __str__(self) -> str:
        return (
            f"[{self.warning_type}] {self.start_date}-{self.end_date}: "
            f"{self.affected_stock_count} stocks affected. {self.error_message}"
        )


# MAJOR-01: 告警类型化助手——把「已有 warning_type 的结构化 DataWarning」或
# 「历史上落库的无类型字符串」归一到 category，供 UI 分级 / 兼容解析。
# 依赖方向：仅依赖本模块数据结构，供 ui 层引用（strategies → ui 被允许，R1 合规）。
# 说明：DataWarning.category 缺省时经本类的 __post_init__ 查表解析；此处为常量命名空间 +
# 判责助手，纯类属性/类方法，无实例字段，故不用 dataclass。
class WarningCategory:
    """DataWarning.category 三分类常量 + 判责助手（fail-closed 白名单）。"""

    DATA_QUALITY = "data_quality"
    TERMINATION = "termination"
    PERFORMANCE_PATH = "performance_path"

    # 结构化 warning_type → 默认 category 映射（若 DataWarning 已显式带 category，
    # 以显式值为准，这里仅作解析兜底）。含由历史字符串类型化的新 type（MAJOR-01）。
    _TYPE_TO_CATEGORY: ClassVar[dict[str, str]] = {
        "suspend_enrich_failed": "data_quality",
        "limit_enrich_failed": "data_quality",
        "suspend_data_absent": "data_quality",
        "limit_data_absent": "data_quality",
        "benchmark_data_absent": "data_quality",
        "benchmark_data_partial": "data_quality",
        "portfolio_wiped_out": "termination",
        "stale_estimate": "data_quality",
        "range_quality_gaps": "data_quality",
        "preload_range_too_wide": "performance_path",
        "range_preload_failed": "performance_path",
        "range_preload_error": "performance_path",
    }
    # fail-closed 历史异常关键词白名单：无 [type] 前缀的真实异常一定能兜底归类，
    # 满足 R21 BT-03（不以「无前缀一律非 unreliable」为默认默认值）。
    _FAIL_CLOSED_PHRASES: tuple[tuple[str, str], ...] = (
        ("valued at last known price", "data_quality"),  # stale-estimate，D1-C1
        ("suspension", "data_quality"),
        # 历史无括号格式 "suspend_*/limit_*: ..."（真实数据问题，fail-closed 兜底）
        ("suspend_data_absent", "data_quality"),
        ("limit_data_absent", "data_quality"),
        ("suspend_enrich_failed", "data_quality"),
        ("limit_enrich_failed", "data_quality"),
        # 基准缺失/部分缺失；preload 慢路径（性能，非正确性）
        ("benchmark", "data_quality"),
        ("preload_range_too_wide", "performance_path"),
        ("range_preload_failed", "performance_path"),
        ("range_preload_error", "performance_path"),
    )

    @classmethod
    def category_of(cls, warning: str | DataWarning) -> str | None:
        """把结构化或历史字符串告警归一为 category；命中失败返回 None（非 unreliable）。"""
        if isinstance(warning, DataWarning):
            return warning.category
        return cls._category_of_str(warning)

    @staticmethod
    def _category_of_str(text: str) -> str | None:
        # 1) [type] 前缀命中已知 warning_type 全集 → 映射 category。
        if text.startswith("["):
            closing = text.find("]")
            if closing > 1:
                wtype = text[1:closing]
                if wtype in WarningCategory._TYPE_TO_CATEGORY:
                    return WarningCategory._TYPE_TO_CATEGORY[wtype]
                return None
        # 2) fail-closed 历史异常关键词白名单（含 stale-estimate / 慢路径）。
        for phrase, category in WarningCategory._FAIL_CLOSED_PHRASES:
            if phrase in text:
                return category
        # 3) 其余无前缀旧字符串（旧撮合 skip 噪音）→ None（非 unreliable，仅 degraded/提示）。
        return None


@dataclass(frozen=True)
class BacktestConfig:
    """回测配置（不可变，确保回测可复现）"""

    start_date: date
    end_date: date
    initial_capital: float = 1_000_000.0

    commission_rate: float = 3e-4
    commission_min: float = 5.0
    stamp_duty_rate: float | None = None  # None=自动按政策分段，显式值覆盖
    stamp_duty_buy: bool = False
    transfer_fee_rate: float = 1e-5

    slippage_model: Literal["fixed_bps", "volume_ratio", "sqrt_volume"] = "fixed_bps"
    slippage_bps: float = 5.0

    rebalance_freq: Literal["daily", "weekly", "monthly", "signal"] = "signal"
    position_sizing: Literal["equal_weight", "market_cap_weight", "rank_weighted"] = "equal_weight"
    max_position_count: int = 50
    max_single_weight: float = 0.1
    on_empty_signal: Literal["liquidate", "hold"] = "hold"
    """再平衡日无信号时的持仓处理（D1-M5）。

    旧实现：再平衡日信号为空 → 无条件全清仓（静默，无配置项、用户不可见），
    策略失效/数据异常时会误清空已持仓。

    修复后：
    - hold（默认）：保持现有持仓不动，仅累加 empty_signal_days 计数（供 UI 次级提示，
      不写 data_warnings，不升级可信度级别）；
    - liquidate：保留下沉到「全清仓」语义（沿用旧行为，供显式选择）。
    """

    renormalize_after_cap: bool = False
    """截顶后是否重新归一化到满仓（BT-03）。False=保留现金（默认，风控优先）；
    True=全部触顶时放宽单票上限、等比放大到满仓，权总=1（资金效率优先，但会超出
    max_single_weight 硬上限）。配合回测指标 avg_invested_pct / cash_drag_days 观察现金流。"""

    delist_recovery_rate: float = 0.3
    """退市清算回收率（0, 1]。

    A 股退市股票进入退市整理期后普遍连续跌停，按退市前最后已知价全额变现会系统性
    高估收益（永远高估、从不低估，且对低估值/低市值类策略放大更严重）。0.3 为保守
    经验值，默认按回收价进行强制清算，可配置以覆盖不同风险假设。
    """

    benchmark_code: str = DEFAULT_BENCHMARK_INDEX
    risk_free_rate: float = 0.02
    fail_fast: bool = True
    disable_ai: bool = True
    persist_artifacts: bool = False
    price_adjustment: Literal["qfq"] = "qfq"
    execution_price: Literal["next_open", "next_close"] = "next_open"
    allow_limit_up_buy: bool = False
    allow_limit_down_sell: bool = False
    cash_reserve_pct: float = 0.1
    """现金预留比例。语义：先从总资产扣除该比例，剩余部分才按目标权重分配。
    整手取整产生的余数计入现金，因此实际现金比例 >= cash_reserve_pct。
    """
    min_rebalance_delta_pct: float = 0.01  # 权重变化小于该百分比不交易，避免噪声换手
    preload_max_days: int = 366

    min_ic_sample_size: int = 10
    """单日 IC 计算所需的最小候选样本数（BT-07）。

    原硬编码 n>=3：n=3 的 Spearman 秩相关只能取 4 个离散值（3! 排列），
    是纯噪声却被等权计入 ic_mean/ic_ir。对信号稀疏的策略，大部分交易日的 IC
    都落在噪声区间，ic_mean 由噪声主导、ic_ir 因高方差被系统性低估。

    默认 10（常见统计意义的保守下限，n>=20 更稳妥可自行调高）。样本数不足的
    交易日被剔除（不产 IC），避免噪声伪装成「弱信号」；ic_mean 同时按样本数
    加权（Σ(nᵢ·icᵢ)/Σ(nᵢ)），让样本更充分的日期权重更高。
    """

    reallocate_unfilled: bool = False
    """单笔预算不足一手的标的，其释放预算是否在其余标的中补分配（BT-05）。

    默认 False：保持旧行为——`lot_size_indivisible` 标的被跳过、预算闲置，
    数值与既有回测完全一致（向后兼容）。

    True：按信号强度降序对高价位标的按原目标金额重新尝试买入，把整手取整
    释放的预算重新投入组合。会改变收益数值（释放的闲置预算被用出），
    与默认配置的结果不可比；开启时每次补分配都追加 data_warning 提示。
    """

    def validate(self) -> list[str]:
        errors = []
        if self.start_date >= self.end_date:
            errors.append("start_date must be before end_date")
        if self.initial_capital <= 0 or not math.isfinite(self.initial_capital):
            errors.append("initial_capital must be positive")
        if self.commission_rate < 0 or self.commission_rate > 0.01:
            errors.append("commission_rate should be between 0 and 1%")
        if self.max_single_weight <= 0 or self.max_single_weight > 1:
            errors.append("max_single_weight must be in (0, 1]")
        if self.on_empty_signal not in ("liquidate", "hold"):
            errors.append("on_empty_signal must be one of 'liquidate' or 'hold'")
        if not 0 < self.delist_recovery_rate <= 1:
            errors.append("delist_recovery_rate must be in (0, 1]")
        if self.cash_reserve_pct < 0 or self.cash_reserve_pct >= 1:
            errors.append("cash_reserve_pct must be in [0, 1)")
        if self.min_rebalance_delta_pct < 0 or self.min_rebalance_delta_pct >= 1:
            errors.append("min_rebalance_delta_pct must be in [0, 1)")
        if self.max_position_count <= 0:
            errors.append("max_position_count must be positive")
        if self.preload_max_days < 30:
            errors.append("preload_max_days must be at least 30")
        return errors

    def get_cost_config(self) -> TransactionCostConfig:
        """获取交易成本配置。"""
        return TransactionCostConfig(
            commission_rate=self.commission_rate,
            commission_min=self.commission_min,
            stamp_duty_rate=self.stamp_duty_rate,
            stamp_duty_buy=self.stamp_duty_buy,
            transfer_fee_rate=self.transfer_fee_rate,
            slippage_model=self.slippage_model,
            slippage_bps=self.slippage_bps,
        )


@dataclass(frozen=True)
class BacktestResult:
    """回测结果（不可变，确保回测可复现）"""

    config: BacktestConfig
    strategy_name: str
    params_snapshot: dict

    nav_curve: pl.DataFrame
    daily_returns: pl.Series
    benchmark_returns: pl.Series

    trades: pl.DataFrame
    positions: pl.DataFrame
    skipped_orders: pl.DataFrame

    metrics: dict[str, float | None]
    ic_series: pl.Series
    period_stats: pl.DataFrame
    # MAJOR-01: 类型放宽 —— 引擎新路径尽量产出结构化 DataWarning（带 category），
    # 历史/防御性字符串仅兼容保留；消费方经 WarningCategory 统一归一（决策③⑥）。
    data_warnings: tuple[str | DataWarning, ...]
    failed_signal_dates: tuple[dict, ...]

    run_id: str
    executed_at: datetime
    duration_ms: int

    # IC 序列对应的信号日期（仅内存透传，不持久化）。
    # 置于 dataclass 末尾并带默认值，避免破坏既有测试/调用方的关键字构造点。
    ic_dates: pl.Series = field(default_factory=lambda: pl.Series(dtype=pl.Date))
    # BT-01: 信号是否来自独立打分（存在 score/signal_score/rank_score/ai_score 列）。
    # False 表示 IC 仅基于策略排序字段的「排序 IC」，UI 据此调整呈现与 tooltip。
    has_real_score: bool = True

    # BT-02: 退市清算分项统计（置于末尾带默认值，避免破坏既有关键字构造点）。
    # delist_liquidation_count: 触发的退市强制清算笔数。
    # delist_loss_amount: 因 delist_recovery_rate 折扣相对全额变现被扣减的账面金额，
    # 用于让用户评估「退市假设」对收益的影响权重（recovery 本身为经验估计值）。
    delist_liquidation_count: int = 0
    delist_loss_amount: float = 0.0

    def with_warnings(self, warnings: list[str | DataWarning] | tuple[str | DataWarning, ...]) -> BacktestResult:
        warnings_tuple = tuple(warnings) if isinstance(warnings, list) else warnings
        return BacktestResult(
            config=self.config,
            strategy_name=self.strategy_name,
            params_snapshot=self.params_snapshot,
            nav_curve=self.nav_curve,
            daily_returns=self.daily_returns,
            benchmark_returns=self.benchmark_returns,
            trades=self.trades,
            positions=self.positions,
            skipped_orders=self.skipped_orders,
            metrics=self.metrics,
            ic_series=self.ic_series,
            period_stats=self.period_stats,
            data_warnings=warnings_tuple,
            failed_signal_dates=self.failed_signal_dates,
            run_id=self.run_id,
            executed_at=self.executed_at,
            duration_ms=self.duration_ms,
            ic_dates=self.ic_dates,
            delist_liquidation_count=self.delist_liquidation_count,
            delist_loss_amount=self.delist_loss_amount,
            has_real_score=self.has_real_score,
        )

    def to_persist_dict(self) -> dict:
        """生成持久化所需的字典（不含 app_version，由调用方补充）。

        将 BacktestResult 与 BacktestConfig 中需要落库的字段平铺为单层 dict，
        供 BacktestService._persist_result 调用，避免在服务层散落字段映射逻辑。

        BT-03: 新增 config_json / quality_json 两个 JSONB 结构，完整落一组
        回测配置与可信度元数据，使历史记录可复现、可信度不因持久化而单向蒸发。
        """
        return {
            "run_id": self.run_id,
            "strategy_name": self.strategy_name,
            # D1-m3: 回收率假设并入 params_snapshot 落库（backtest_results.params_snapshot 为 JSONB，
            # 免新增列/migration），使「同一策略在不同 delist_recovery_rate 下的结果」可追溯复现。
            "params_snapshot": {
                **self.params_snapshot,
                "delist_recovery_rate": self.config.delist_recovery_rate,
            },
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "initial_capital": self.config.initial_capital,
            "metrics": self.metrics,
            "nav_curve": self.nav_curve,
            "trades": self.trades,
            "period_stats": self.period_stats,
            "duration_ms": self.duration_ms,
            "execution_price": self.config.execution_price,
            "allow_limit_up_buy": self.config.allow_limit_up_buy,
            "allow_limit_down_sell": self.config.allow_limit_down_sell,
            "slippage_model": self.config.slippage_model,
            # BT-09: 不再平铺 on_empty_signal 等无落库列的配置字段（历史死代码）——
            # 完整配置由下方 config_json（asdict 快照）承载，避免「看起来可追溯实则未落库」
            # 的误导性信号。平铺列仅保留有实际 DB 索引/查询意义的字段（execution_price 等）。
            # BT-03: 完整回测配置快照（dataclasses.asdict），含 date 对象，DAO 侧递归序列化。
            # 作为完整配置的唯一来源，平铺的 execution_price 等列保留做索引用。
            "config_json": asdict(self.config),
            # BT-03: 可信度元数据快照，供历史列表/详情页区分「干净」与「带警告」的回测。
            # MAJOR-01 决策④：落库统一转字符串（DataWarning → str(w)），DAO
            # _serialize_jsonb_value 不处理 DataWarning，直接落会静默失败；字符串向后兼容。
            "quality_json": {
                "data_warnings": [str(w) if isinstance(w, DataWarning) else w for w in self.data_warnings],
                "failed_signal_dates": list(self.failed_signal_dates),
                "skipped_order_count": 0 if self.skipped_orders.is_empty() else len(self.skipped_orders),
                "delist_liquidation_count": self.delist_liquidation_count,
                "delist_loss_amount": self.delist_loss_amount,
                "has_real_score": self.has_real_score,
            },
        }
