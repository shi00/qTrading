"""Agent 能力与资产解析（设计正本 §9.9 / P01）。

本模块只做维护型 Prompt 资产的纯解析：版本绑定、变量白名单与正文渲染；
stock 仍委托既有 ``get_base_prompt`` 的三级优先级，不复制第二套可编辑副本。
不调用模型、不派发工具、不读写外部状态（教程也只是帮助文本）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from core.prompt_base import AGENT_PROMPT_ASSETS, STRATEGY_PROMPTS, get_base_prompt


class PromptAssetError(ValueError):
    """Prompt 资产键/变量/正文不合法。"""

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


@dataclass(frozen=True, slots=True)
class PromptResolution:
    asset_key: str
    version: int
    body: str
    source: str
    allowed_variables: tuple[str, ...] = ()


def resolve_agent_prompt(asset_key: str, variables: Mapping[str, str]) -> PromptResolution:
    """按维护资产声明的变量白名单渲染正文；缺键/未知变量均失败。"""
    asset = AGENT_PROMPT_ASSETS.get(asset_key)
    if asset is None:
        raise PromptAssetError("unknown_asset_key", "asset_key")
    declared = set(asset.allowed_variables)
    supplied = set(variables)
    missing = declared - supplied
    if missing:
        raise PromptAssetError("missing_variable", sorted(missing)[0])
    unknown = supplied - declared
    if unknown:
        raise PromptAssetError("unknown_variable", sorted(unknown)[0])
    for key in declared:
        if not isinstance(variables[key], str):
            raise PromptAssetError("invalid_variable", key)
    return PromptResolution(
        asset_key=asset.asset_key,
        version=asset.version,
        body=asset.body.format_map({key: variables[key] for key in declared}),
        source="asset",
        allowed_variables=asset.allowed_variables,
    )


def get_agent_tutorial() -> PromptResolution:
    """纯帮助资产：无模型变量，不调用模型/工具，不执行任何示例。"""
    return resolve_agent_prompt("agent_tutorial", {})


def resolve_stock_prompt(
    strategy_key: str,
    get_user_prompt: Callable[[str], str | None],
    get_global_prompt: Callable[[], str],
) -> PromptResolution:
    """沿用既有 get_base_prompt 三级优先级，只补充来源标签，不新建副本。

    来源判定：用户自定义且与策略默认同文 → ``legacy_unknown``（无法区分旧迁移
    同文与真实自定义）；真实不同文 → ``custom``；缺用户 → ``default``；缺策略
    → ``global``。
    """
    body = get_base_prompt(strategy_key, get_user_prompt, get_global_prompt)
    user_prompt = get_user_prompt(strategy_key) or ""
    default_prompt = STRATEGY_PROMPTS.get(strategy_key) or ""
    if user_prompt.strip():
        source = (
            "legacy_unknown" if default_prompt.strip() and user_prompt.strip() == default_prompt.strip() else "custom"
        )
    elif default_prompt.strip():
        source = "default"
    else:
        source = "global"
    return PromptResolution(asset_key="stock", version=1, body=body, source=source)
