"""AI 上下文公共辅助（review01-A5a 自 ai_mixin 移出）。"""

from __future__ import annotations

import typing

import pandas as pd


def _build_stale_section(
    api_name: str,
    df: pd.DataFrame,
    formatter: typing.Callable[[pd.DataFrame], str],
    date_column: str = "ann_date",
) -> str:
    """统一 stale 标注格式。

    Phase 2A.1 §4.4.5 v1.6.0 P1-7：模块级辅助函数，供各 ``_build_*_text``
    方法复用，避免重复实现 stale 检查逻辑。

    Args:
        api_name: 该子段落对应的 API 名（如 "share_float" / "cn_m"）
        df: 该子段落的数据 DataFrame
        formatter: 格式化函数，接收 df 返回该子段落的文本
        date_column: df 中代表"最后更新日期"的列名，默认 "ann_date"。
            v1.8.0 P2-D 修订：由各 _build_*_text 调用时传入实际列名
            （如 trade_date/date/month）。

    Returns:
        - df 为空 → 返回空字符串（不注入）
        - api_name 不在当前档位覆盖内 → 返回 stale 前缀 + formatter(df)
        - api_name 在档位覆盖内 → 返回 formatter(df)（无 stale 标注）
    """
    if df.empty:
        return ""
    from data.external.tushare_client import TushareClient

    client = TushareClient()
    if not client.is_api_covered_by_tier(api_name):
        last_update = (
            pd.to_datetime(df[date_column].max()).strftime("%Y-%m-%d") if date_column in df.columns else "未知"
        )
        return f"【数据停止更新，最后更新：{last_update}】\n" + formatter(df)
    return formatter(df)
