"""LLM 云端数据外发知情确认（SEC-01）统一门控。

``run_ai_analysis`` 已实现外发知情确认（确认对象 = provider + scope_version 持久化，
见 ``strategies/ai_mixin.py``）。本模块把「确认对象集合」与「当前是否已确认」判定抽为
跨层共享单一事实源，供 strategies 与 services 两层的云端出口复用——services 不能反向
导入 strategies（R1），若各自实现会造成 SEC-01 授权范围逻辑重复、漂移。

本模块属横切叶子层（utils），仅依赖 ``utils.config_handler``，不含业务层引用。

- ``collect_cloud_ack_providers``：主 provider + failover 不同云端 provider 的确认对象
  集合。与 AIService failover 凭证预载语义一致：仅提取带 ``/`` 前缀的 fallback provider
  （裸 model 名视为主 provider 内部模型，已由主确认覆盖）；failover 配置读取失败时降级
  为仅主 provider。
- ``is_egress_acknowledged``：当前主 provider 的确认对象集合全部已确认（当前
  ``AI_EGRESS_SCOPE_VERSION`` 下）→ 允许云端外发。scope 版本升级后旧确认自动失效，
  强制重新确认（UN-07）。
"""

from __future__ import annotations

import logging

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


def collect_cloud_ack_providers(primary_provider: str) -> list[str]:
    """SEC-01：主 provider + failover 不同云端 provider 的确认对象集合。

    主 provider 调用失败时 failover 会切换到 fallback（如 deepseek → qwen），若 fallback
    未确认，数据会外发给未授权对象（授权语义漂移）。failover 配置读取失败时降级为仅主
    provider（failover 实际不可用时行为一致）。
    """
    providers = [primary_provider]
    try:
        failover = ConfigHandler.get_failover_config()
        for model_str in failover.get("fallbacks", []):
            if "/" in model_str:
                fb = model_str.split("/")[0]
                if fb and fb not in providers:
                    providers.append(fb)
    except Exception as exc:
        # failover 配置读取失败时降级为仅主 provider（与 AIService 凭证预载降级一致）；
        # 此时 failover 实际不可用，ack 范围与运行时行为保持一致。
        logger.debug(
            "[egress_ack] failover config unavailable (%s); ack scope = primary only",
            exc,
        )
    return providers


def is_egress_acknowledged() -> bool:
    """当前主 provider + failover 云端 provider 是否全部已确认（SEC-01）。

    任一未确认即返回 ``False``（禁止云端外发）。``is_ai_external_acknowledged`` 按
    provider + 当前 scope_version 判定，scope 升级后旧确认自动失效，需重新确认。
    """
    current = ConfigHandler.get_llm_provider()
    providers = collect_cloud_ack_providers(current)
    return all(ConfigHandler.is_ai_external_acknowledged(provider=p) for p in providers)
