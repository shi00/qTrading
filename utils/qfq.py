import polars as pl
import pandas as pd
from typing import Literal

# 复权因子「未知」标记列（UIR-X01-105）。由 ``qfq_unknown_expr`` 产出，
# 供回测引擎在结果级生成 DataWarning，并让消费方排除这些标的。
QFQ_UNKNOWN_COL = "qfq_adjustment_unknown"

QfqStatus = Literal["unknown", "identity", "adjusted"]
"""复权状态三态（UIR-X01-105）：

- ``unknown``：复权因子不可得（全组为空）或基准因子为 0（除零不可解）。**不是**恒等。
- ``identity``：因子有效但整段等于基准（未发生除权/送转）——合法的恒等复权。
- ``adjusted``：因子有效且发生变化，需按 ``因子/基准`` 缩放。
"""


def qfq_ratio_expr(
    col_name: str = "adj_factor",
    group_col: str | None = "ts_code",
    ref: Literal["last", "first"] = "last",
) -> pl.Expr:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio expression in Polars.

    ``ref`` selects the base factor used to normalize absolute price levels:
    - ``"last"``  (default): base = latest available date -> live/display and technical analysis.
    - ``"first"``: base = first trading day -> backtest Point-in-Time, so the absolute price
      level does NOT depend on future ex-right information (reproducible backtests).

    The base is derived from the FILLED factor series (forward/backward fill), so leading
    nulls never collapse the group to an unadjusted ratio. If factor values are missing, they
    are forward filled first to carry forward existing factors, then backward filled to handle
    any remaining leading nulls.

    UIR-X01-105 / R21：**未知因子返回 null，不再返回恒等 1.0**。
    「复权因子不可得」（整组全空）与「基准因子为 0」（除零不可解）都是**未知**，
    与「因子有效但整段未除权」（合法恒等 1.0）是两件事；把未知当作 1.0 会让回测/技术指标
    把未复权价当复权价使用（结论静默失真）。未知行同时可由 ``qfq_unknown_expr`` 标记。
    """
    expr = pl.col(col_name).forward_fill().backward_fill()
    if group_col:
        filled = expr.over(group_col)
        base = (expr.first() if ref == "first" else expr.last()).over(group_col)
    else:
        filled = expr
        base = expr.first() if ref == "first" else expr.last()

    # base 为 null 时 ``base == 0`` 求值为 null，``|`` 的 Kleene 语义仍能使整体为 True；
    # 若某分支回退为 null，``when`` 按 False 处理并落入 otherwise（filled / null = null），
    # 两种路径都得到「未知 → null」，不会伪造 1.0。
    unknown = filled.is_null() | base.is_null() | (base == 0)
    return pl.when(unknown).then(pl.lit(None, dtype=pl.Float64)).otherwise(filled / base).alias("qfq_ratio")


def qfq_unknown_expr(
    col_name: str = "adj_factor",
    group_col: str | None = "ts_code",
    ref: Literal["last", "first"] = "last",
) -> pl.Expr:
    """标记「复权因子未知」的布尔表达式（列名 ``qfq_adjustment_unknown``，UIR-X01-105）。

    未知判据（与 ``qfq_ratio_expr`` 完全同一套）：
    - 整组因子填充后仍为空（adj_factor 全 NULL）；
    - 基准因子为空或为 0（除零不可解，因子值本身异常）。

    该列是**结果级** warning 的唯一数据源：调用方在最靠近结果处（回测
    ``_load_quotes`` / 指标入口）据此产出 DataWarning，使「复权不可信」可被上层取得。
    """
    expr = pl.col(col_name).forward_fill().backward_fill()
    if group_col:
        filled = expr.over(group_col)
        base = (expr.first() if ref == "first" else expr.last()).over(group_col)
    else:
        filled = expr
        base = expr.first() if ref == "first" else expr.last()

    return (filled.is_null() | base.is_null() | (base == 0)).alias(QFQ_UNKNOWN_COL)


def qfq_series_status(
    series: pd.Series | None,
    ref: Literal["last", "first"] = "last",
) -> QfqStatus:
    """pandas 侧复权状态三态判定（UIR-X01-105）。

    ``qfq_ratio_series`` 用 ``None`` 同时表达「未知」与「无需调整（恒等）」，
    仅看返回值无法区分二者。消费方必须以本函数判定状态：

    - ``unknown``：``series`` 为空 / 全空 / 基准为 0 → 复权价**不可计算**，
      不得按 1.0 当作未除权（应显式置缺或标注结果不可信）；
    - ``identity``：因子有效且整段相同 → 复权与未复权等价，原值可直接使用；
    - ``adjusted``：需按 ``series / base`` 缩放。
    """
    if series is None or series.empty:
        return "unknown"

    filled = series.ffill().bfill()
    if filled.isna().all():
        return "unknown"

    base = filled.iloc[0] if ref == "first" else filled.iloc[-1]
    if pd.isna(base) or base == 0:
        return "unknown"

    if (filled == base).all():
        return "identity"

    return "adjusted"


def qfq_ratio_series(
    series: pd.Series,
    ref: Literal["last", "first"] = "last",
) -> pd.Series | None:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio series in Pandas.

    ``ref`` semantics match ``qfq_ratio_expr``:
    - ``"last"`` (default): base = latest available value -> live/display and technical analysis.
    - ``"first"``: base = first available value -> backtest Point-in-Time, so the absolute
      price level does NOT depend on future ex-right information (reproducible backtests).
      Calling a pandas-path consumer that needs PIT without ``ref="first"`` would silently
      degrade to ``"last"`` and introduce lookahead bias (the D4 defect this fixes).

    If factor values are missing, they are forward filled first to carry forward existing
    factors. Then backward fill is applied to handle any remaining leading nulls.

    Returns:
        pd.Series: The adjustment ratio series (``status == "adjusted"``).
        None: 无调整比率。``None`` 有两个**不同含义**，须用 ``qfq_series_status`` 区分：
            未知（``unknown``：因子全空 / 基准为 0）→ 复权价不可计算，不得当恒等；
            恒等（``identity``：因子有效但整段未除权）→ 与未复权等价。
    """
    if qfq_series_status(series, ref) != "adjusted":
        return None

    filled = series.ffill().bfill()
    base = filled.iloc[0] if ref == "first" else filled.iloc[-1]
    return filled / base
