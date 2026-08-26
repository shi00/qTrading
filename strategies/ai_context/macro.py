"""AI 宏观环境上下文渲染器（review01-A5a 自 ai_mixin 移出）。

含 Shibor / LPR / M2 / CPI / PPI / GDP 段落，按子段落分别 stale 标注。
"""

from __future__ import annotations

import logging
import typing

import pandas as pd

from core.i18n import I18n
from strategies.ai_context.common import _build_stale_section
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
async def _build_macro_context(cache: typing.Any, as_of_date: str | None = None) -> str:
    """
    构建宏观经济环境上下文。

    L3 修复：新增 Shibor 利率注入，对价值投资和固收相关策略有重要参考价值。
    B-P1-1 修复：新增 as_of_date 参数，在历史回放场景下按日期截断宏观数据，防止前视偏差。

    Phase 2A.1 §4.4.5 v1.6.0 P0-1：按子段落分别 stale 标注
    - shibor 段落（对应 ai_label_shibor，points_120）：shibor API 在 points_120
      覆盖内，正常注入（无 stale 标注）
    - m2/cpi/ppi 段落（对应 ai_label_macro_full，points_2000）：cn_m/cn_cpi/cn_ppi
      在 points_2000 覆盖内；points_120 降级时按子段落 stale 标注注入历史数据

    Args:
        cache: 数据缓存实例
        as_of_date: 截止日期（含），None 表示不限制

    Returns:
        宏观经济环境文本
    """
    lines = [I18n.get("ai_section_wrapper", title=I18n.get("macro_env_title"))]
    has_data = False

    try:
        macro = await cache.get_macro_economy(as_of_date=as_of_date)
        if macro is not None and not macro.empty:
            # Phase 2D §3.2.6 修复：m2 行与 GDP 行 period 不同（月度 vs 季度末日），
            # 作为独立行存储。DAO 返回最多 2 行，需分别定位月度行和 GDP 行。
            # 用 pd.notna() 判断字段是否可用，避免 NaN 被 `is not None` 误判为有效值。
            m2_row = (
                macro.dropna(subset=["m2_yoy"]).iloc[0]
                if "m2_yoy" in macro.columns and not macro.dropna(subset=["m2_yoy"]).empty
                else None
            )
            gdp_row = (
                macro.dropna(subset=["gdp_yoy"]).iloc[0]
                if "gdp_yoy" in macro.columns and not macro.dropna(subset=["gdp_yoy"]).empty
                else None
            )

            # Phase 2A.1 §4.4.5：m2/cpi/ppi 段落对应 ai_label_macro_full（points_2000），
            # cn_m/cn_cpi/cn_ppi 在 points_2000 覆盖内；points_120 降级时按子段落 stale 标注。
            # cn_m 作为整个 macro 段落的代理（三者档位一致）
            macro_lines: list[str] = []
            if m2_row is not None:
                m2_yoy = m2_row.get("m2_yoy")
                if pd.notna(m2_yoy):
                    macro_lines.append(f"- {I18n.get('macro_m2_yoy')}: {m2_yoy:.2f}%")

                cpi = m2_row.get("cpi")
                if pd.notna(cpi):
                    macro_lines.append(f"- {I18n.get('macro_cpi')}: {cpi:.2f}")

                ppi = m2_row.get("ppi")
                if pd.notna(ppi):
                    macro_lines.append(f"- {I18n.get('macro_ppi')}: {ppi:.2f}")

            if macro_lines:
                macro_text = "\n".join(macro_lines)
                # 用 _build_stale_section 统一标注（cn_m 作为代理 API，date_column="period"）
                macro_section = _build_stale_section(
                    "cn_m",
                    macro,
                    lambda _df: macro_text,
                    date_column="period",
                )
                if macro_section:
                    lines.append(macro_section)
                    has_data = True

            # Phase 2D §3.2.6：cn_gdp 段落（季度数据，period 为季度末日）
            # GDP 行与 m2 行 period 不同，分别 stale 标注
            gdp_lines: list[str] = []
            if gdp_row is not None:
                gdp_yoy = gdp_row.get("gdp_yoy")
                if pd.notna(gdp_yoy):
                    # 从 period（季度末日）推断 quarter 字符串，如 2024-12-31 → "2024Q4"
                    period = gdp_row.get("period")
                    quarter_str = ""
                    if hasattr(period, "year") and hasattr(period, "month"):
                        q = (period.month - 1) // 3 + 1
                        quarter_str = f"（{period.year}Q{q}）"
                    gdp_lines.append(f"- {I18n.get('macro_gdp_yoy')}{quarter_str}: {gdp_yoy:.2f}%")

                    pi_yoy = gdp_row.get("pi_yoy")
                    if pd.notna(pi_yoy):
                        gdp_lines.append(f"- {I18n.get('macro_pi_yoy')}: {pi_yoy:.2f}%")

                    si_yoy = gdp_row.get("si_yoy")
                    if pd.notna(si_yoy):
                        gdp_lines.append(f"- {I18n.get('macro_si_yoy')}: {si_yoy:.2f}%")

                    ti_yoy = gdp_row.get("ti_yoy")
                    if pd.notna(ti_yoy):
                        gdp_lines.append(f"- {I18n.get('macro_ti_yoy')}: {ti_yoy:.2f}%")

            if gdp_lines:
                gdp_text = "\n".join(gdp_lines)
                # cn_gdp 作为 GDP 段落代理 API，date_column="period"
                gdp_section = _build_stale_section(
                    "cn_gdp",
                    macro,
                    lambda _df: gdp_text,
                    date_column="period",
                )
                if gdp_section:
                    lines.append(gdp_section)
                    has_data = True

        shibor = await cache.get_shibor_latest(as_of_date=as_of_date)
        if shibor is not None and not shibor.empty:
            shibor_latest = shibor.iloc[0]

            shibor_lines: list[str] = []
            on_rate = shibor_latest.get("on_rate")
            if on_rate is not None:
                shibor_lines.append(f"- {I18n.get('macro_shibor_overnight')}: {on_rate:.2f}%")

            w1_rate = shibor_latest.get("week_1")
            if w1_rate is not None:
                shibor_lines.append(f"- {I18n.get('macro_shibor_1w')}: {w1_rate:.2f}%")

            m3_rate = shibor_latest.get("month_3")
            if m3_rate is not None:
                shibor_lines.append(f"- {I18n.get('macro_shibor_3m')}: {m3_rate:.2f}%")

            if shibor_lines:
                shibor_text = "\n".join(shibor_lines)
                # shibor 段落对应 ai_label_shibor（points_120），shibor API 在 points_120 覆盖内
                # points_120 降级时 shibor 仍可正常注入（无 stale 标注）
                shibor_section = _build_stale_section(
                    "shibor",
                    shibor,
                    lambda _df: shibor_text,
                    date_column="record_date",
                )
                if shibor_section:
                    lines.append(shibor_section)
                    has_data = True

            # Phase 3G §4.3.4：LPR 段落（与 shibor 同表 shibor_daily，独立 stale 标注）
            # shibor_lpr 在 points_120 覆盖内，正常注入（无 stale 标注）
            lpr_lines: list[str] = []
            lpr_1y = shibor_latest.get("lpr_1y")
            if lpr_1y is not None:
                lpr_lines.append(f"- {I18n.get('macro_lpr_1y')}: {lpr_1y:.2f}%")

            lpr_5y = shibor_latest.get("lpr_5y")
            if lpr_5y is not None:
                lpr_lines.append(f"- {I18n.get('macro_lpr_5y')}: {lpr_5y:.2f}%")

            if lpr_lines:
                lpr_text = "\n".join(lpr_lines)
                lpr_section = _build_stale_section(
                    "shibor_lpr",
                    shibor,
                    lambda _df: lpr_text,
                    date_column="record_date",
                )
                if lpr_section:
                    lines.append(lpr_section)
                    has_data = True

    # NOTE(lazy): except Exception 保留(已合理日志). ceiling: 该 try 块抛出宏观上下文构建异常. upgrade: 策略层重构时统一走 classify_error.
    except Exception as e:
        logger.warning("[ai_context] Failed to build macro context: %s", DataSanitizer.sanitize_error(e))

    if has_data:
        return "\n".join(lines) + "\n"
    return ""
