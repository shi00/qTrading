"""AI 历史行情上下文渲染器（review01-A5a 自 ai_mixin 移出）。"""

from __future__ import annotations

import datetime
import logging
import math

import pandas as pd

from core.i18n import I18n
from utils.limit_status import classify_limit_status, get_limit_pct
from utils.qfq import qfq_ratio_series
from utils.sanitizers import DataSanitizer
from utils.technical_analysis import TechnicalAnalysis

logger = logging.getLogger(__name__)

# stk_limit 查表键所需列（R21：缺列时降级到板块规则，不静默当作「无涨跌停」）
_LIMIT_KEY_COLUMNS = ("ts_code", "trade_date", "up_limit", "down_limit")


def _normalize_trade_date_key(value) -> datetime.date | None:
    """将 ``trade_date`` 归一为 ``datetime.date``，保证 stk_limit 查表键一致。

    ``stk_limit.trade_date`` 与历史行情的 ``trade_date`` 可能是 ``datetime.date`` /
    ``datetime.datetime`` / ``pd.Timestamp`` / ``str``（``YYYYMMDD`` 或 ISO），统一归一为
    ``date`` 后再作字典键；无法解析时返回 ``None``（该日不打标签，而非当作未涨停）。
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):  # 含 pd.Timestamp（其子类）
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.date.fromisoformat(s[:10])
        except ValueError:
            try:
                parsed = pd.to_datetime(s, errors="coerce")
            except (ValueError, TypeError):
                return None
            if parsed is None or pd.isna(parsed):
                return None
            return parsed.date()
    return None


def _is_valid_limit_price(value) -> bool:
    """涨跌停价是否有效：需为有限数且 ``> 0``。

    ``stk_limit`` 中 ``up_limit/down_limit`` 为 0 / 负 / NaN / Inf 均属**无效价**（占位或异常），
    按「该日无有效记录」处理（H2/R21：不把它伪装成「未涨停」，而是走缺失提示）。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def _build_limit_lookup(limit_df: pd.DataFrame | None) -> dict[tuple[str, datetime.date], tuple[float, float]]:
    """将 stk_limit 区间数据一次性构建为 ``{(ts_code, trade_date): (up_limit, down_limit)}``。

    O(1) 查表（非逐日扫 DataFrame）；``limit_df`` 为空或缺列时返回空字典（调用方降级）。
    逐行跳过无有效涨跌停价（``up_limit`` 或 ``down_limit`` 非有限或 ≤0）的记录，使其落入
    调用方的「无记录」态（不打标签 + 段末缺失提示，R21），而非静默当作「未涨停」。
    """
    if limit_df is None or limit_df.empty:
        return {}
    if not set(_LIMIT_KEY_COLUMNS).issubset(limit_df.columns):
        return {}
    lookup: dict[tuple[str, datetime.date], tuple[float, float]] = {}
    for code, td, up, down in zip(
        limit_df["ts_code"], limit_df["trade_date"], limit_df["up_limit"], limit_df["down_limit"], strict=False
    ):
        day = _normalize_trade_date_key(td)
        if day is None:
            continue
        if not (_is_valid_limit_price(up) and _is_valid_limit_price(down)):
            continue
        lookup[(str(code), day)] = (float(up), float(down))
    return lookup


def _resolve_name_as_of(name_ranges: list[tuple] | None, as_of) -> str | None:
    """在 ``(start_date, end_date, name)`` 升序区间列表中解析 as_of 当日生效名称（无则 None）。

    DS-02 P3：单股名称区间一次性批量载入内存后逐日解析「某日名称」（含 ST/*ST 前缀），
    供降级路径涨跌停判定（``utils.limit_status.get_limit_pct``）使用 as-of 名称而非当前
    名称，消除前视偏差。
    区间不重叠（每股每生效区间唯一）；升序遍历时最后一个 ``start <= as_of < end`` 即当日名称。
    """
    if not name_ranges or as_of is None:
        return None

    if isinstance(as_of, datetime.datetime):
        as_of = as_of.date()
    if not isinstance(as_of, datetime.date):
        return None  # 非日期无法解析，回退调用方当前名称
    resolved = None
    for start, end, name in name_ranges:
        if start is None or start > as_of:
            break
        if end is None or end > as_of:
            resolved = name
    return resolved


def _build_history_text(
    history_df: pd.DataFrame,
    ts_code: str = "",
    stock_name: str = "",
    name_ranges: list[tuple] | None = None,
    vol_ratio_threshold: float = 1.7,
    labels_out: list[str] | None = None,
    limit_df: pd.DataFrame | None = None,
) -> str:
    """
    Build a semantic summary of recent price action using quantitative factor extraction.
    This provides the LLM with "vision" into the actual OHLCV structure.

    NOTE: Output intentionally excludes XML wrapper tags because the caller
          (ai_service.py) already wraps this in <recent_price_action>.

    Args:
        labels_out: 输出参数，收集成功注入的标签 key；哨兵/异常时不注册
        name_ranges: DS-02 P3 — 该股 ``(start_date, end_date, name)`` 生效区间列表
            （start_date 升序，由 StockNameHistoryDao.get_name_ranges 一次性批量读取）。
            给每个历史交易日解析 as-of 名称以判定 ST 涨跌停；None 时空之（回退当前
            ``stock_name``），不引入逐日 DB 查询。
        limit_df: 交易所公布的逐日涨跌停价（``stk_limit`` 表，列
            ``ts_code/trade_date/up_limit/down_limit``）。三态语义：
            ① None 或空 → 走 ``get_limit_pct`` **降级路径**（按板块规则近似，
            段落末尾追加 ``ai_limit_price_approx`` 标注；``get_limit_pct`` 返回 None
            时该日不打标签）；
            ② 非空且能查到该 (ts_code, 交易日) → 用 ``classify_limit_status`` 按交易所
            公布价判定封板；
            ③ 非空但该日无记录 → 该日**不打标签**，段落末尾追加 ``ai_limit_price_missing``
            提示（R21：不把"数据缺失"当成"未涨停"）。

    价格口径（F1）：``stk_limit`` 的 ``up_limit/down_limit`` 是名义价（与 raw_close 同口径），
    而展示用的 ``close`` 经 ``_get_qfq_df`` 前复权。故涨跌停判定改用**名义 close**
    （= 复权 close / ``qfq_ratio_series``，同一正本、``ref="last"``），避免窗口内除权/送转
    使历史日复权 close 被缩小而静默漏标。``adj_factor`` 列缺失或 ratio 无效时名义 close
    退化为复权 close（等价于无复权）。
    """
    if history_df is None or history_df.empty:
        return I18n.get("ai_history_insufficient")

    try:
        # D11: Apply Forward Adjusted Prices (QFQ) to avoid split/dividend gaps fooling the AI
        df_qfq = TechnicalAnalysis._get_qfq_df(history_df)
        # Ensure chronological order
        df = df_qfq.sort_values("trade_date", ascending=True).reset_index(drop=True)  # type: ignore[union-attr]

        # F1 口径对齐：stk_limit 的 up_limit/down_limit 是**名义价**（与 raw_open/raw_close 同口径），
        # 而上面的 close 是前复权价（_get_qfq_df）。若直接在复权空间与名义价比较，窗口内发生除权/
        # 送转的历史日复权 close 被缩小，会静默漏标涨跌停（宪法 §2「看似正常却系统性失真」）。
        # 用同一正本 utils.qfq.qfq_ratio_series（ref="last"，末行 ratio=1，故当日判定与名义价一致）
        # 反推逐行名义 close = 复权 close / ratio；ratio 为 None / ≤0 / NaN 时退化为复权 close
        # （等价于无复权，即 adj_factor 列缺失时 _get_qfq_df 原样返回的正常路径）。
        # H3：必须在**切片前**的全序列上计算，保证与 _get_qfq_df 同源同输入（切片后重算会在
        # 「窗口首段 NaN + 因子递减」场景因 ffill/bfill 基准不同而偏离）。
        df["_nominal_close"] = df["close"]
        if "adj_factor" in df.columns:
            _ratio = qfq_ratio_series(df["adj_factor"])
            if _ratio is not None:
                _safe_ratio = _ratio.where(_ratio > 0)
                df["_nominal_close"] = (df["close"] / _safe_ratio).where(_safe_ratio.notna(), df["close"])

        # Compute Macro Horizon
        macro_cagr = "N/A"
        macro_mdd = "N/A"
        if len(df) > 60:
            # Compute long-term CAGR and Max Drawdown on `df`
            first_close_macro = df["close"].iloc[0]
            if first_close_macro > 0:
                macro_cagr = f"{((df['close'].iloc[-1] / first_close_macro) - 1) * 100:.1f}%"
            roll_max = df["close"].cummax()
            drawdown = (df["close"] - roll_max) / roll_max
            macro_mdd = f"{drawdown.min() * 100:.1f}%"

            # Slice for short-term K-line context
            df = df.tail(60).reset_index(drop=True)

        if len(df) < 5:
            # 哨兵：数据不足，不注册标签
            return I18n.get("ai_history_insufficient")

        # 1. Extract Base Series
        close = df["close"]

        # 全 NaN close → 无有效价格数据，返回哨兵不注册标签
        if close.isna().all():
            return I18n.get("ai_history_insufficient")

        has_vol = "vol" in df.columns
        has_pct_chg = "pct_chg" in df.columns

        # 2. Trend & Swing Factors (with division-by-zero guards)
        first_close = close.iloc[0]
        fifth_ago_close = close.iloc[-5]
        pct_all = ((close.iloc[-1] / first_close) - 1) * 100 if first_close > 0 else 0.0
        pct_5d = ((close.iloc[-1] / fifth_ago_close) - 1) * 100 if fifth_ago_close > 0 else 0.0

        # 20-Day MA Bias
        bias_str = "N/A (insufficient data)"
        if len(df) >= 20:
            ma20 = close.tail(20).mean()
            if ma20 > 0:
                bias = ((close.iloc[-1] - ma20) / ma20) * 100
                bias_str = f"{bias:+.2f}%"

        # Consecutive streaks (with NaN guard)
        consec_str = "N/A"
        if has_pct_chg:
            last_pct = df["pct_chg"].iloc[-1]
            # Guard against NaN
            if pd.isna(last_pct):
                last_pct = 0.0
            sign_last = 1 if last_pct > 0 else -1 if last_pct < 0 else 0
            consec_days = 0
            if sign_last != 0:
                for p in reversed(df["pct_chg"].tolist()):
                    if pd.isna(p):
                        break
                    if (p > 0 and sign_last > 0) or (p < 0 and sign_last < 0):
                        consec_days += 1
                    else:
                        break
            consec_str = (
                f"{I18n.get('ai_consecutive_up') if sign_last > 0 else I18n.get('ai_consecutive_down')} {consec_days} {I18n.get('ai_day_unit')}"
                if consec_days > 1
                else I18n.get("ai_sideways")
            )

        # 3. Drawdown Factor
        rolling_max = close.cummax()
        drawdowns = (close - rolling_max) / rolling_max
        mdd = drawdowns.min() * 100

        # 4. Volume Factor (graceful fallback if 'vol' column is missing)
        vol_line = f"- {I18n.get('ai_vol_unavailable')}"
        if has_vol:
            vol = df["vol"]
            vol_5d_avg = vol.tail(5).mean()
            vol_older_avg = vol.iloc[:-5].mean() if len(df) > 5 else 0.0
            # Guard NaN from mean of NaN-containing series
            if pd.isna(vol_5d_avg):
                vol_5d_avg = 0.0
            if pd.isna(vol_older_avg):
                vol_older_avg = 0.0
            vol_ratio_5d = vol_5d_avg / vol_older_avg if vol_older_avg > 0 else 1.0
            vol_desc = (
                I18n.get("ai_vol_significant_expand")
                if vol_ratio_5d > vol_ratio_threshold
                else I18n.get("ai_vol_significant_shrink")
                if vol_ratio_5d < 0.7
                else I18n.get("ai_vol_stable")
            )
            vol_line = I18n.get(
                "ai_vol_line_format",
                label=I18n.get("ai_vol_status_label"),
                desc=vol_desc,
                baseline=I18n.get("ai_vol_relative_base"),
                ratio_label=I18n.get("ai_vol_ratio_label"),
                ratio=f"{vol_ratio_5d:.2f}",
            )

        lines = [
            I18n.get(
                "ai_macro_cycle_header", title=I18n.get("ai_macro_cycle"), baseline=I18n.get("ai_config_baseline")
            ),
            f"- {I18n.get('ai_long_term')}: {I18n.get('ai_total_return')} {macro_cagr}，{I18n.get('ai_max_drawdown')} {macro_mdd}。",
            "",
            I18n.get(
                "ai_trend_vol_header",
                title=I18n.get("ai_trend_volatility"),
                days=len(df),
                unit=I18n.get("ai_trading_days"),
            ),
            f"- {I18n.get('ai_volatility')}: {I18n.get('ai_total_return')} {pct_all:+.2f}%，{I18n.get('ai_max_drawdown')} {mdd:.2f}%。",
            f"- {I18n.get('ai_short_momentum')}: {I18n.get('ai_5d_return')} {pct_5d:+.2f}%，{I18n.get('ai_current')} {consec_str}。",
            f"- {I18n.get('ai_ma20_bias')}: {bias_str}。",
            "",
            I18n.get("ai_section_wrapper", title=I18n.get("ai_volume_price")),
            vol_line,
            "",
            I18n.get("ai_section_wrapper", title=I18n.get("ai_recent_3d_kline")),
            I18n.get("ai_kline_header"),
        ]

        # 交易所公布价（stk_limit）优先：一次性构建 O(1) 查表，缺失时才降级到板块规则近似
        limit_lookup = _build_limit_lookup(limit_df)
        using_exchange_limit = bool(limit_lookup)
        limit_missing = False

        for r in df.tail(3).to_dict("records"):
            td = r.get("trade_date")
            if isinstance(td, (datetime.date, datetime.datetime)):
                d = td.strftime("%m%d")
            else:
                d = str(td or "")[-4:]
            c = f"{r.get('close', 0):.2f}"
            p_val = r.get("pct_chg", 0)
            p = f"{p_val:+.2f}%" if not pd.isna(p_val) else "N/A"
            v_val = r.get("vol", 0)
            v = f"{v_val:.0f}" if (has_vol and not pd.isna(v_val)) else "N/A"

            limit_tag = ""
            # F1：涨跌停判定一律在名义价空间进行（stk_limit 同口径），不得用复权 close 比名义价
            nominal_close = r.get("_nominal_close")
            if not pd.isna(p_val):
                status: str | None = None
                if using_exchange_limit:
                    day_key = _normalize_trade_date_key(r.get("trade_date"))
                    entry = limit_lookup.get((ts_code, day_key)) if day_key is not None else None
                    if entry is not None:
                        status = classify_limit_status(nominal_close, entry[0], entry[1])
                    else:
                        # R21: 交易所公布价缺失 → 该日不打标签（不伪装成「未涨停」），段末提示
                        limit_missing = True
                else:
                    # NOTE(lazy): stk_limit 缺失时按板块规则近似判定涨跌停. ceiling: 未覆盖「新股上市初期无涨跌幅限制」与「退市整理期首日不设涨跌幅」场景. upgrade: 交易所公布价稳定可用后移除降级路径，或补充无涨跌幅场景的状态字段.
                    # DS-02 P3: 降级路径逐日解析 as-of 生效名称（ST=5%），无历史区间回退当前名称
                    day_limit = get_limit_pct(
                        ts_code, _resolve_name_as_of(name_ranges, r.get("trade_date")) or stock_name
                    )
                    if day_limit is not None:
                        # F2：容差按涨跌停价「0.01 元取整」反推——取整误差上限 0.005 元，折算为相对
                        # pre_close 的百分点即 0.005 / pre_close × 100 = 0.5 / pre_close；而
                        # pre_close ≈ close / (1 + band/100)，故取 0.6 / nominal_close 作上界（含少量余量）。
                        # 中等价位实际 pct_chg 常略低于名义幅度（如 pre_close 3.33 → 涨停价 3.66 →
                        # pct 9.91% < 10），按价格反推的容差需覆盖这一取整损失才能正确标注。
                        # 封顶 0.5：容差不得比修复前的固定 0.5 个百分点更松，否则低价股会把普涨误标涨停
                        # （0.5 / close 随价格走低无界膨胀，如 close = 0.1 → 容差 5 个百分点），重新引入
                        # 本问题；close 无效（None/NaN/≤0）时同样取 0.5。宁可漏标不可误标（R21 精神）。
                        # NOTE(lazy): 降级路径容差用名义 close 近似 pre_close，并以 0.5 个百分点封顶. ceiling: 低价股（close < 1.2 元）容差退化为固定 0.5 个百分点，低于其真实取整误差占比故可能漏标真实封板；反向地，任何正容差都存在 [band - tol, band) 的误标窗口，band 越低窗口越显著（如 ST 主板 band=5 且 close < 1.2 时阈值降到 4.5，涨 4.6% 未封板即会被标记）. upgrade: stk_limit 稳定全市场覆盖后移除降级路径.
                        # 说明：上述双向风险是「幅度近似」路径的固有取舍，不可能靠调容差同时消除；主路径
                        # （交易所公布价 + classify_limit_status，容差 0.005 元）不存在该问题，故降级路径
                        # 仅作 stk_limit 不可用时的兜底，并由段末 ai_limit_price_approx 向用户明示为近似。
                        tol = (
                            min(0.6 / nominal_close, 0.5) if (nominal_close is not None and nominal_close > 0) else 0.5
                        )
                        if p_val >= day_limit - tol:
                            status = "up"
                        elif p_val <= -(day_limit - tol):
                            status = "down"

                if status == "up":
                    limit_tag = f" 🔴{I18n.get('ai_limit_up')}"
                elif status == "down":
                    limit_tag = f" 🟢{I18n.get('ai_limit_down')}"

            lines.append(f"{d} | {c} | {p}{limit_tag} | {v}")

        # 涨跌停判定口径说明：整段只加一次（近似/缺失互斥，不逐行重复）
        if not using_exchange_limit:
            lines.append(I18n.get("ai_limit_price_approx"))
        elif limit_missing:
            lines.append(I18n.get("ai_limit_price_missing"))

        # 实际数据产出，注册标签
        if labels_out is not None:
            labels_out.append("ai_label_kline")

        return "\n".join(lines)

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出历史文本构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning("[ai_context] Failed to build history text: %s", DataSanitizer.sanitize_error(e))
        if labels_out is not None:
            labels_out.clear()
        return I18n.get("ai_history_extract_error")
