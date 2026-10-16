"""模型执行参数兼容性服务（S3，模型统一.md §6.2，MM-06）。

职责：把快照中的连接定义解析为适配器可安全执行的参数——
- 本地 GGUF：``max_length`` clamp 至 ``n_ctx - 8``（防 llama 上下文越界）；
  ``timeout`` 下限 clamp 至 1.0s；``n_ctx`` 缺失或不足（<= 8）判不兼容；
- 远程 litellm：参数原样透传，不引入本地专属键。

纯函数（无 IO / 无状态），解析结果为浅拷贝（不修改快照原 mapping）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# llama 推理保留 token（BOS/系统开销）；max_length 上限 = n_ctx - 保留值
_CONTEXT_RESERVE_TOKENS = 8
# 适配器超时下限（秒）：低于该值视为配置错误，clamp 而非拒绝
_MIN_TIMEOUT_S = 1.0

_LOCAL_ADAPTER_KINDS = ("gguf_chat", "gguf_sentiment")


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    """兼容性判定：缺失/不足不伪装为可用（R21 邻域语义）。"""

    compatible: bool
    reason: str | None
    resolved_options: dict[str, Any]


class ModelCompatibilityService:
    """连接参数兼容性解析（无状态，DI 注入 ScenarioExecutor）。"""

    def resolve_execution_params(self, connection: dict[str, Any]) -> CompatibilityDecision:
        """按 adapter_kind 解析执行参数；本地 GGUF 做 clamp，远程透传。"""
        adapter_kind = str(connection.get("adapter_kind", ""))
        raw_options = dict(connection.get("adapter_options") or {})

        if adapter_kind not in _LOCAL_ADAPTER_KINDS:
            return CompatibilityDecision(compatible=True, reason=None, resolved_options=raw_options)

        n_ctx = raw_options.get("n_ctx")
        if not isinstance(n_ctx, int) or n_ctx <= _CONTEXT_RESERVE_TOKENS:
            return CompatibilityDecision(
                compatible=False,
                reason=f"n_ctx 缺失或不足（{n_ctx!r}），无法安全执行本地推理",
                resolved_options={},
            )

        max_length = raw_options.get("max_length")
        if isinstance(max_length, int):
            raw_options["max_length"] = min(max_length, n_ctx - _CONTEXT_RESERVE_TOKENS)

        timeout = raw_options.get("timeout")
        if isinstance(timeout, (int, float)) and timeout < _MIN_TIMEOUT_S:
            raw_options["timeout"] = _MIN_TIMEOUT_S

        return CompatibilityDecision(compatible=True, reason=None, resolved_options=raw_options)
