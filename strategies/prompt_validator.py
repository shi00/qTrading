"""
Prompt 数据声明校验器

用于确保 System Prompt 中声明的数据与实际注入的数据一致。
"""

import random
import typing
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DataDeclaration:
    """数据声明项"""

    name: str
    prompt_claim: str
    injector: typing.Callable[[], typing.Awaitable[bool]]
    status: str = "unknown"


async def validate_prompt_declarations(
    declarations: list[DataDeclaration],
) -> dict[str, bool]:
    """
    校验所有数据声明是否与实际注入一致。

    Returns:
        {declaration_name: is_valid}
    """
    results = {}
    for decl in declarations:
        try:
            has_data = await decl.injector()
            results[decl.name] = has_data
            decl.status = "available" if has_data else "missing"
        except Exception as e:
            results[decl.name] = False
            decl.status = f"error: {e}"
    return results


def generate_declaration_report(declarations: list[DataDeclaration]) -> str:
    """生成声明状态报告"""
    lines = ["# Prompt 数据声明状态报告\n"]
    lines.append("| 声明项 | Prompt 描述 | 实际状态 |")
    lines.append("|--------|-------------|----------|")

    for decl in declarations:
        status_icon = "✅" if decl.status == "available" else "❌"
        lines.append(f"| {decl.name} | {decl.prompt_claim} | {status_icon} {decl.status} |")

    return "\n".join(lines)


async def check_multi_period_data(field: str) -> bool:
    """
    检查多期财务数据是否可用。

    L1 修复：使用随机抽样代替硬编码探针股票，避免单只股票数据异常导致误判。
    抽样 5 只股票，多数（>=3）通过即判定为 available。
    """
    from data.cache.cache_manager import CacheManager

    cache = CacheManager()

    try:
        all_stocks_df = await cache.get_stock_basic()
        if all_stocks_df is None or all_stocks_df.empty:
            sample_codes = ["000001.SZ"]
        else:
            all_stocks = all_stocks_df["ts_code"].tolist()
            sample_codes = random.sample(all_stocks, min(5, len(all_stocks)))

        passed = 0
        for ts_code in sample_codes:
            try:
                df = await cache.get_financial_reports_history(ts_code, periods=8)
                if df is not None and not df.empty:
                    if field in df.columns and not df[field].isna().all():  # type: ignore[union-attr]
                        passed += 1
            except (ValueError, KeyError, RuntimeError) as e:
                logger.debug(f"[PromptValidator] check_field_populous sample {ts_code} failed: {e}")
                continue

        threshold = (len(sample_codes) + 1) // 2
        return passed >= threshold

    except Exception as e:
        logger.debug(f"[PromptValidator] check_field_populous failed: {e}")
        return False


async def check_field_exists(field: str) -> bool:
    """
    检查指定字段是否存在于财务数据中。

    L1 修复：使用随机抽样代替硬编码探针股票。
    """
    from data.cache.cache_manager import CacheManager

    cache = CacheManager()

    try:
        all_stocks_df = await cache.get_stock_basic()
        if all_stocks_df is None or all_stocks_df.empty:
            sample_codes = ["000001.SZ"]
        else:
            all_stocks = all_stocks_df["ts_code"].tolist()
            sample_codes = random.sample(all_stocks, min(5, len(all_stocks)))

        passed = 0
        for ts_code in sample_codes:
            try:
                df = await cache.get_financial_reports_history(ts_code, periods=1)
                if df is not None and not df.empty and field in df.columns:
                    passed += 1
            except (ValueError, KeyError, RuntimeError) as e:
                logger.debug(f"[PromptValidator] check_field_exists sample {ts_code} failed: {e}")
                continue

        threshold = (len(sample_codes) + 1) // 2
        return passed >= threshold

    except Exception as e:
        logger.debug(f"[PromptValidator] check_field_exists failed: {e}")
        return False


async def check_table_has_data(table_name: str) -> bool:
    """检查指定表是否有数据"""
    from data.cache.cache_manager import CacheManager

    cache = CacheManager()
    return await cache.check_table_has_data(table_name)


_DECLARATIONS: list[DataDeclaration] = []
_declarations_initialized = False


def get_declarations() -> list[DataDeclaration]:
    """懒加载数据声明列表，避免 import 时触发 ORM/缓存依赖"""
    global _DECLARATIONS, _declarations_initialized
    if not _declarations_initialized:
        _DECLARATIONS = _init_declarations()
        _declarations_initialized = True
    return _DECLARATIONS


def _init_declarations() -> list[DataDeclaration]:
    """初始化数据声明列表"""
    from data.persistence.models import (
        Dividend,
        FinaAudit,
        FinaMainbz,
        MacroEconomy,
        PledgeStat,
        ShiborDaily,
        StkHoldernumber,
        Top10Holders,
    )

    return [
        DataDeclaration(
            name="multi_period_roe",
            prompt_claim="近8季度ROE趋势",
            injector=lambda: check_multi_period_data("roe"),
        ),
        DataDeclaration(
            name="cashflow_vs_profit",
            prompt_claim="经营现金流与净利润对比",
            injector=lambda: check_field_exists("n_cashflow_act"),
        ),
        DataDeclaration(
            name="audit_opinion",
            prompt_claim="审计意见",
            injector=lambda: check_table_has_data(FinaAudit.__tablename__),
        ),
        DataDeclaration(
            name="dividend_history",
            prompt_claim="分红记录",
            injector=lambda: check_table_has_data(Dividend.__tablename__),
        ),
        DataDeclaration(
            name="pledge_ratio",
            prompt_claim="质押比例",
            injector=lambda: check_table_has_data(PledgeStat.__tablename__),
        ),
        DataDeclaration(
            name="macro_economy",
            prompt_claim="宏观经济指标",
            injector=lambda: check_table_has_data(MacroEconomy.__tablename__),
        ),
        DataDeclaration(
            name="shibor_rates",
            prompt_claim="Shibor利率",
            injector=lambda: check_table_has_data(ShiborDaily.__tablename__),
        ),
        DataDeclaration(
            name="top10_holders",
            prompt_claim="前十大股东",
            injector=lambda: check_table_has_data(Top10Holders.__tablename__),
        ),
        DataDeclaration(
            name="holder_number",
            prompt_claim="股东人数",
            injector=lambda: check_table_has_data(StkHoldernumber.__tablename__),
        ),
        DataDeclaration(
            name="main_business",
            prompt_claim="主营业务构成",
            injector=lambda: check_table_has_data(FinaMainbz.__tablename__),
        ),
    ]
