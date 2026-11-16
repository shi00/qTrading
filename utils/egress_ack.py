"""LLM 云端数据外发知情确认（SEC-01）统一门控。

``run_ai_analysis`` 已实现外发知情确认（确认对象 = provider + scope_version 持久化，
见 ``strategies/ai_mixin.py``）。本模块把「确认对象集合」与「当前是否已确认」判定抽为
跨层共享单一事实源，供 strategies 与 services 两层的云端出口复用——services 不能反向
导入 strategies（R1），若各自实现会造成 SEC-01 授权范围逻辑重复、漂移。

本模块属横切叶子层（utils），仅依赖 ``utils.config_handler`` 与 ``core`` 纯类型。

- ``collect_cloud_ack_providers``：主 provider + failover 不同云端 provider 的确认对象
  集合。与 AIService failover 凭证预载语义一致：仅提取带 ``/`` 前缀的 fallback provider
  （裸 model 名视为主 provider 内部模型，已由主确认覆盖）；failover 配置读取失败时降级
  为仅主 provider。
- ``is_egress_acknowledged``：当前主 provider 的确认对象集合全部已确认（当前
  ``AI_EGRESS_SCOPE_VERSION`` 下）→ 允许云端外发。scope 版本升级后旧确认自动失效，
  强制重新确认（UN-07）。旧链（``_chat_completion`` / ``_router_failover``）仍使用本判定。
- ``bind_egress_ack_permit`` / ``verify_egress_ack_permit``（D13-002）：统一链单次尝试的
  外发确认许可。旧判定读进程级「当前全局主 provider」，与本次尝试实际出站对象解耦——
  快照冻结连接 / 并发场景下可能用 provider A 的确认放行对 provider B 的出站（串味 /
  误放行）。新链路改为：许可在 S3 Executor 许可线性化点由本次 ``AttemptContext`` +
  ``ResolvedConnection`` 签发（纯绑定，无 IO），云适配器在凭据解析前按「实际 provider」
  重新读取用户确认并校验 attempt / invocation / owner / 连接 / provider / 模型 / 目标 /
  scope_version / 有效期，任一不符 fail-closed（零出站）。用户确认本身仍是同一条
  ``is_ai_external_acknowledged`` 持久记录，不放宽任何既有二次确认。
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from core.errors import AIPolicyNotAcknowledgedError
from core.i18n import Message
from core.model_management_types import (
    EgressAckPermit,
    normalize_egress_target,
)
from utils.config_handler import ConfigHandler
from utils.config_models import AI_EGRESS_SCOPE_VERSION

if TYPE_CHECKING:
    from core.model_management_types import AttemptContext, ResolvedConnection

logger = logging.getLogger(__name__)

# 单次外发许可有效期上限（秒）：许可只服务于当前尝试的即时出站，超时即失效，
# 防止许可对象被滞留后用于后续 attempt（实际还会被 attempt / invocation 身份校验拒绝，
# 此处为独立的纵深时间边界）。真实截止仍由 ``request.deadline`` 裁剪（≤ 单请求 deadline，
# 默认上限高于最长请求预算，避免许可在持久化等待中被误判过期）。
EGRESS_ACK_PERMIT_TTL_S = 300.0

_ACK_PROMPT_KEY = "ai_external_acknowledgment_prompt"


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

    仅服务旧链出口（``_chat_completion`` / ``_router_failover``）；统一链单次尝试请使用
    :func:`bind_egress_ack_permit` + :func:`verify_egress_ack_permit`（绑定实际出站对象）。
    """
    current = ConfigHandler.get_llm_provider()
    providers = collect_cloud_ack_providers(current)
    return all(ConfigHandler.is_ai_external_acknowledged(provider=p) for p in providers)


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def bind_egress_ack_permit(
    *,
    attempt_context: AttemptContext,
    resolved_connection: ResolvedConnection,
    now: float,
    deadline: float,
    scope_version: int = AI_EGRESS_SCOPE_VERSION,
    ttl_seconds: float = EGRESS_ACK_PERMIT_TTL_S,
) -> EgressAckPermit:
    """在许可线性化点为本次尝试签发外发确认许可（纯绑定，零 IO）。

    只做身份 / 目标 / 有效期的结构化绑定，不读取用户确认记录（确认在出站点按
    **实际 provider** 校验，撤销可即时生效）。上下文缺失 / 非法一律拒绝签发
    （``ValueError``，fail-closed：调用方不得继续出站）。

    ``expires_at`` = ``min(now + ttl_seconds, deadline)``（单调时钟口径）；deadline
    已耗尽时仍签发零有效期许可，由适配器既有 deadline 门给出 ``TimeoutError``，
    不在此处改变超时语义。
    """
    attempt_id = attempt_context.attempt_id
    invocation_id = attempt_context.invocation_id
    owner_session_id = attempt_context.owner_session_id
    owner_epoch = attempt_context.owner_epoch
    connection_id = resolved_connection.connection_id
    provider = resolved_connection.provider

    for name, value in (
        ("attempt_id", attempt_id),
        ("invocation_id", invocation_id),
        ("owner_session_id", owner_session_id),
        ("connection_id", connection_id),
        ("provider", provider),
        ("model", resolved_connection.model),
    ):
        if not _is_non_empty_str(value):
            raise ValueError(f"egress ack permit requires non-empty {name} (missing attempt/connection context)")
    if isinstance(owner_epoch, bool) or not isinstance(owner_epoch, int):
        raise ValueError("egress ack permit requires integer owner_epoch (missing attempt context)")

    remaining = max(deadline - now, 0.0)
    return EgressAckPermit(
        attempt_id=attempt_id,
        invocation_id=invocation_id,
        owner_session_id=owner_session_id,
        owner_epoch=owner_epoch,
        connection_id=connection_id,
        provider=provider,
        model=resolved_connection.model,
        target_fingerprint=normalize_egress_target(resolved_connection.api_base),
        scope_version=scope_version,
        issued_at=now,
        expires_at=now + min(max(ttl_seconds, 0.0), remaining),
    )


def _deny(reason: str, *, attempt_id: str | None) -> AIPolicyNotAcknowledgedError:
    """统一拒绝构造（SEC-01）：日志只含结构性原因与 attempt 标识，不含秘密。"""
    logger.warning(
        "[egress_ack] egress acknowledgment permit rejected (%s) for attempt=%s — cloud "
        "inference refused (no external requests initiated)",
        reason,
        attempt_id or "unknown",
    )
    return AIPolicyNotAcknowledgedError(
        Message(_ACK_PROMPT_KEY),
        detail=f"Egress acknowledgment permit rejected: {reason}. No external request was initiated.",
    )


def verify_egress_ack_permit(
    permit: EgressAckPermit | None,
    *,
    attempt_context: AttemptContext,
    resolved_connection: ResolvedConnection,
    invocation_id: str,
    now: float,
) -> None:
    """出站前校验本次尝试的外发确认许可（D13-002，fail-closed）。

    校验维度（任一不符 → ``AIPolicyNotAcknowledgedError``，零出站）：

    - **上下文存在**：许可缺失 / 非 ``EgressAckPermit``（含测试替身）一律拒绝；
    - **attempt 绑定**：attempt / invocation / owner session / owner epoch 与本次上下文一致
      （跨 attempt、跨会话不可复用）；
    - **对象**：许可 provider 与实际 ``resolved_connection.provider`` 一致，且该 provider
      在当前 ``AI_EGRESS_SCOPE_VERSION`` 下确已由用户确认（现场复查，撤销即时生效）；
    - **目标**：连接 id / 模型 / 规范化外发目标（scheme+host+port+base path）与本次实际
      出站连接一致（换连接 / 换目标不可复用）；
    - **有效期**：``issued_at <= now <= expires_at`` 且 scope_version 未过期。
    """
    attempt_id = getattr(attempt_context, "attempt_id", None)
    if not isinstance(permit, EgressAckPermit):
        raise _deny("missing or invalid permit bound to this attempt", attempt_id=attempt_id)

    ctx_attempt_id = getattr(attempt_context, "attempt_id", None)
    ctx_invocation_id = getattr(attempt_context, "invocation_id", None)
    ctx_owner_session_id = getattr(attempt_context, "owner_session_id", None)
    ctx_owner_epoch = getattr(attempt_context, "owner_epoch", None)

    if not _is_non_empty_str(ctx_attempt_id) or permit.attempt_id != ctx_attempt_id:
        raise _deny("attempt id mismatch", attempt_id=attempt_id)
    if (
        not _is_non_empty_str(ctx_invocation_id)
        or not _is_non_empty_str(invocation_id)
        or permit.invocation_id != ctx_invocation_id
        or permit.invocation_id != invocation_id
    ):
        raise _deny("invocation id mismatch", attempt_id=attempt_id)
    if not _is_non_empty_str(ctx_owner_session_id) or permit.owner_session_id != ctx_owner_session_id:
        raise _deny("owner session mismatch", attempt_id=attempt_id)
    if (
        isinstance(ctx_owner_epoch, bool)
        or not isinstance(ctx_owner_epoch, int)
        or type(permit.owner_epoch) is not int
        or permit.owner_epoch != ctx_owner_epoch
    ):
        raise _deny("owner epoch mismatch", attempt_id=attempt_id)

    if permit.connection_id != resolved_connection.connection_id:
        raise _deny("connection mismatch", attempt_id=attempt_id)
    if permit.provider != resolved_connection.provider:
        raise _deny("provider mismatch", attempt_id=attempt_id)
    if permit.model != resolved_connection.model:
        raise _deny("model mismatch", attempt_id=attempt_id)
    if permit.target_fingerprint != normalize_egress_target(resolved_connection.api_base):
        raise _deny("egress target mismatch", attempt_id=attempt_id)

    if permit.scope_version != AI_EGRESS_SCOPE_VERSION:
        raise _deny(f"scope version expired (permit={permit.scope_version})", attempt_id=attempt_id)

    try:
        issued_at = float(permit.issued_at)
        expires_at = float(permit.expires_at)
    except (TypeError, ValueError):
        raise _deny("invalid validity window", attempt_id=attempt_id) from None
    if not math.isfinite(issued_at) or not math.isfinite(expires_at) or expires_at < issued_at:
        # 非有限值（NaN/Inf）会使时间比较全部为 False，必须显式拒绝（fail-closed）
        raise _deny("invalid validity window", attempt_id=attempt_id)
    if now < issued_at:
        raise _deny("permit issued in the future", attempt_id=attempt_id)
    if now > expires_at:
        raise _deny("permit expired for this attempt", attempt_id=attempt_id)

    if not ConfigHandler.is_ai_external_acknowledged(provider=permit.provider):
        raise _deny(f"provider '{permit.provider}' not acknowledged for current scope", attempt_id=attempt_id)
