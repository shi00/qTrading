"""AIService AI 标签 / 档位映射子模块（review01-A5b-1）。

自 ``services/ai_service.py`` 移出的 available_data 渲染、标签档位映射与过滤、
策略最低档位提示。集中管理 tier 相关映射（Phase 2A.1 §4.1 / §4.4.6）。
"""

from __future__ import annotations

import logging

from core.i18n import I18n

logger = logging.getLogger(__name__)

_AVAILABLE_DATA_LABEL_KEYS: set[str] = {
    "ai_label_quote_snapshot",
    "ai_label_tech",
    "ai_label_global",
    "ai_label_news",
    "ai_label_kline",
    "ai_label_learning",
    "ai_label_strategy_ctx",
    "ai_label_valuation",
    # Phase 2A.1 §4.1 v1.6.0 P0-1 拆分：ai_label_macro → ai_label_shibor + ai_label_macro_full
    "ai_label_shibor",
    "ai_label_macro_full",
    "ai_label_roe_trend",
    "ai_label_gross_margin_trend",
    "ai_label_revenue_growth_trend",
    "ai_label_profit_growth_trend",
    "ai_label_cf_profit_ratio",
    "ai_label_goodwill_ratio",
    "ai_label_monetary_capital",
    "ai_label_accounts_receiv",
    "ai_label_audit",
    "ai_label_main_business",
    "ai_label_dividend",
    "ai_label_pledge",
    # Phase 3B：股权质押明细（pledge_detail API，points_2000）
    "ai_label_pledge_detail",
    # Phase 3D：限售解禁（share_float API，points_5000）
    "ai_label_share_float",
    # Phase 3E：股东增减持（stk_holdertrade API，points_2000）
    "ai_label_holder_trade",
    "ai_label_top_holder",
    "ai_label_holder_count",
    "ai_label_main_flow",
    "ai_label_top_list",
    "ai_label_northbound",
    # Phase 3A：业绩预告（fina_forecast，forecast API，points_2000）
    "ai_label_forecast",
    # Phase 3C：龙虎榜机构席位（top_inst API，points_2000）
    "ai_label_top_inst",
    # Phase 3F-2：申万行业（index_classify / index_member_all API，points_2000）
    "ai_label_sw_industry",
    # Phase 3G §4.3.4：业绩快报（express API，points_2000）
    "ai_label_express",
}

AVAILABLE_DATA_LABELS: frozenset[str] = frozenset(_AVAILABLE_DATA_LABEL_KEYS)


def build_available_data_block(labels: list[str]) -> str:
    """Render <available_data> block from label key strings.

    Design decision (deviates from issue #41 spec v5 §2.2):
    The spec defines AVAILABLE_DATA_LABELS as translated strings
    ``{I18n.get(k) for k in _AVAILABLE_DATA_LABEL_KEYS}``, but the
    actual pipeline uses **key strings** throughout (ai_mixin →
    ai_service → this function) and only translates at render time.
    This is intentionally better because:
    1. Keys are locale-independent — tests compare keys vs keys.
    2. Translation happens once at render, avoiding stale cached
       translations if locale ever changes at runtime.
    Do NOT change AVAILABLE_DATA_LABELS to translated strings unless
    the entire pipeline is updated accordingly.
    """
    if not labels:
        return ""

    header = I18n.get("ai_available_data_header")
    items = []
    for label_key in labels:
        if label_key not in _AVAILABLE_DATA_LABEL_KEYS:
            logger.warning("[AIService] Unknown label key '%s' not in AVAILABLE_DATA_LABELS, skipping", label_key)
            continue
        display_text = I18n.get(label_key)
        items.append(f"- {display_text}")
    if not items:
        return ""
    return f"<available_data>\n{header}\n" + "\n".join(items) + "\n</available_data>"


# Phase 2A.1 §4.1：AI 标签档位映射 + 过滤函数
#
# label key → (最低档位, required_apis)
# required_apis 中的 API 必须 probe 验证可用（None = 未知，不阻塞）
# 最低档位基于 _TIER_API_COVERAGE：label 数据来源 API 在该档位覆盖内
#
# v1.9.0 P1-7 + v1.10.0 P1-4 修订：注释项与 Phase 2A.1 实施脱节说明
# Phase 2A.1 实施 filter_available_labels 时，_LABEL_TIER_MAP 只含已注册的标签
# （本 map 中**非注释**的项）。v1.9.0 P1-1 已将 filter_available_labels 改为
# fail-fast（raise ValueError），若 run_ai_analysis 传入注释状态的标签会触发 raise。
# 因此：每个 Phase 3X 取消注释时，必须同步 ① 取消 _LABEL_TIER_MAP 对应 key 注释；
# ② 在 _AVAILABLE_DATA_LABEL_KEYS 新增对应 key；③ 在 run_ai_analysis 内部对应策略的
# available_data_labels 列表追加该 key；④ 在 _build_*_text 新增对应数据预取逻辑。
_LABEL_TIER_MAP: dict[str, tuple[str, frozenset[str]]] = {
    # points_120 档位即可用（基础行情/日线/shibor）
    "ai_label_quote_snapshot": ("points_120", frozenset({"daily", "daily_basic"})),
    "ai_label_tech": ("points_120", frozenset({"daily"})),
    "ai_label_kline": ("points_120", frozenset({"daily", "adj_factor"})),
    "ai_label_valuation": ("points_120", frozenset({"daily_basic"})),
    # v1.6.0 拆分：shibor 段落独立标签（points_120，仅依赖 shibor API）
    # Phase 3G §4.3.4：required_apis 追加 shibor_lpr（LPR 与 shibor 同段落注入）
    "ai_label_shibor": ("points_120", frozenset({"shibor", "shibor_lpr"})),
    # v1.6.0 拆分：宏观完整段落（cn_m/cn_cpi/cn_ppi，points_2000）
    # Phase 2D §3.2.6：cn_gdp 全链路补全，required_apis 追加 cn_gdp
    "ai_label_macro_full": ("points_2000", frozenset({"cn_m", "cn_cpi", "cn_ppi", "cn_gdp"})),
    "ai_label_global": ("points_120", frozenset()),  # 无 API 依赖（新闻/外部）
    "ai_label_news": ("points_120", frozenset()),  # 无 API 依赖
    "ai_label_learning": ("points_120", frozenset()),  # 无 API 依赖
    "ai_label_strategy_ctx": ("points_120", frozenset()),  # 无 API 依赖
    # points_2000 档位可用（财务/股东/龙虎榜/概念/资金流/市场异动）
    "ai_label_roe_trend": ("points_2000", frozenset({"fina_indicator"})),
    "ai_label_gross_margin_trend": ("points_2000", frozenset({"fina_indicator"})),
    "ai_label_revenue_growth_trend": ("points_2000", frozenset({"income"})),
    "ai_label_profit_growth_trend": ("points_2000", frozenset({"income"})),
    "ai_label_cf_profit_ratio": ("points_2000", frozenset({"cashflow", "income"})),
    "ai_label_goodwill_ratio": ("points_2000", frozenset({"balancesheet"})),
    "ai_label_monetary_capital": ("points_2000", frozenset({"balancesheet"})),
    "ai_label_accounts_receiv": ("points_2000", frozenset({"balancesheet"})),
    "ai_label_audit": ("points_2000", frozenset({"fina_audit"})),
    "ai_label_main_business": ("points_2000", frozenset({"fina_mainbz"})),
    "ai_label_dividend": ("points_2000", frozenset({"dividend"})),
    # Phase 3B：pledge_stat（统计）与 pledge_detail（明细）拆分为独立标签，
    # 避免 pledge_detail 不可用时连 pledge_stat 段落也消失
    "ai_label_pledge": ("points_2000", frozenset({"pledge_stat"})),
    "ai_label_pledge_detail": ("points_2000", frozenset({"pledge_detail"})),
    "ai_label_top_holder": ("points_2000", frozenset({"top10_holders"})),
    "ai_label_holder_count": ("points_2000", frozenset({"stk_holdernumber"})),
    "ai_label_main_flow": ("points_2000", frozenset({"moneyflow", "moneyflow_hsgt"})),
    # 仅依赖 top_list；top_inst 由独立标签 ai_label_top_inst 承载（§4.2.3），
    # 不耦合进此处，否则 top_inst 不可用会误删 top_list 段落
    "ai_label_top_list": ("points_2000", frozenset({"top_list"})),
    "ai_label_northbound": ("points_2000", frozenset({"hk_hold"})),
    # Phase 3A：业绩预告（forecast API，points_2000）
    "ai_label_forecast": ("points_2000", frozenset({"forecast"})),
    # Phase 3C：龙虎榜机构席位（top_inst API，points_2000）
    # top_inst 独立标签，权限不足时仅此标签被过滤，不影响 ai_label_top_list（§4.2.3）
    "ai_label_top_inst": ("points_2000", frozenset({"top_inst"})),
    # Phase 3D：限售解禁（share_float API，points_5000）
    "ai_label_share_float": ("points_5000", frozenset({"share_float"})),
    # Phase 3E：股东增减持（stk_holdertrade API，points_2000）
    "ai_label_holder_trade": ("points_2000", frozenset({"stk_holdertrade"})),
    # Phase 3F-2：申万行业（index_classify / index_member_all API，points_2000）
    "ai_label_sw_industry": ("points_2000", frozenset({"index_classify", "index_member_all"})),
    # Phase 3G §4.3.4：业绩快报（express API，points_2000）
    "ai_label_express": ("points_2000", frozenset({"express"})),
    # 新增标签（Phase 3 追加时同步加入此 map）：
    # "ai_label_lpr": ("points_120", frozenset({"shibor_lpr"})),  # Phase 3G（已合并到 ai_label_shibor）
    # "ai_label_cyq_perf": ("points_10000", frozenset({"cyq_perf"})),  # Phase 3H 需独立购买
    # "ai_label_forecast_eps": ("points_10000", frozenset({"forecast_eps"})),  # Phase 3H 需独立购买
}


def filter_available_labels(
    labels: list[str],
    tier: str,
    unavailable_apis: set[str],
) -> list[str]:
    """按档位 + probe 状态过滤 AI 标签。

    Phase 2A.1 §4.1 实现：在 ``run_ai_analysis`` 调用 ``build_available_data_block``
    之前过滤标签，使 ``<available_data>`` 区块只列当前档位 + probe 双层验证通过的标签。

    规则:
        - label 不在 _LABEL_TIER_MAP → **raise ValueError**（v1.9.0 P1-1 修订，fail-fast）
        - label 最低档位 > 当前档位 → 移除（档位不足）
        - label required_apis 中有任一 API 不在档位覆盖内 → 移除
        - label required_apis 中有任一 API 在 unavailable_apis → 移除（probe 失败）
        - 其他 → 保留
    """
    from data.external.tushare_client import TushareClient

    client = TushareClient()
    tier_order = client.get_tier_order(tier)
    filtered = []
    for label in labels:
        tier_info = _LABEL_TIER_MAP.get(label)
        if tier_info is None:
            # v1.9.0 P1-1 修订：未注册标签 fail-fast（R14 红线扩展强制注册）
            raise ValueError(f"Label {label} not in _LABEL_TIER_MAP, must register (R14 红线扩展，见 §7.1)")
        min_tier, required_apis = tier_info
        # 第一层：档位覆盖检查
        if client.get_tier_order(min_tier) > tier_order:
            continue
        # 第二层：required_apis 必须在档位覆盖内（避免 ai_label_macro 类漏洞）
        if not all(client.is_api_covered_by_tier(api, tier) for api in required_apis):
            continue
        # 第三层：probe 验证检查
        if required_apis & unavailable_apis:
            continue
        filtered.append(label)
    return filtered


# Phase 2A.1 §4.4.6：策略档位适用性提示（非阻断式 UX 增强）
#
# 策略 key -> 建议最低档位。低于此档位时 UI 提示，但不阻断。
# 与 _LABEL_TIER_MAP 同处集中管理 tier 相关映射。
_STRATEGY_MIN_TIER: dict[str, str] = {
    # points_120：纯量价/技术，daily 即可支撑
    "oversold": "points_120",
    "volume_breakout": "points_120",
    # points_2000：基本面 / 资金流 / 龙虎榜 / 北向 / 综合策略
    "value": "points_2000",
    "growth": "points_2000",
    "dividend": "points_2000",
    "cashflow": "points_2000",
    "large_pe": "points_2000",
    "northbound_holding": "points_2000",
    "northbound_flow": "points_2000",
    "institutional": "points_2000",
    "block_trade": "points_2000",
    "ai_active": "points_2000",
}


def get_strategy_min_tier(strategy_key: str) -> str:
    """返回策略建议最低档位；未登记策略默认 points_120，避免误报。"""
    return _STRATEGY_MIN_TIER.get(strategy_key, "points_120")


def validate_strategy_tier_coverage(registered_keys: set[str]) -> None:
    """启动期校验已注册策略是否都在 _STRATEGY_MIN_TIER 中登记。

    不 **raise**（避免阻断启动），仅 warning 提示。分层说明（R1）：services/ 不可
    导入 strategies/，由 app/bootstrap.py 调用方注入 ``registered_keys`` 参数。
    """
    for key in registered_keys:
        if key not in _STRATEGY_MIN_TIER:
            logger.warning(
                "[AIService] strategy '%s' not in _STRATEGY_MIN_TIER, tier hint will default to points_120",
                key,
            )
