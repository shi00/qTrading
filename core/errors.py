"""
review05-E3: 结构化异常基类（AppError）与 ErrorInfo.

异常语义由异常自身携带（code / message_key / retryable），
而非推给 classify_error 事后用字符串匹配推断。

- core 层：各层皆可引用，不违反 R1 分层依赖。
- classify_error 的首分支（见 utils/error_classifier.py）对 AppError
  直接返回其 info，外部库异常仍走既有分类逻辑。
- 新增异常一律继承 AppError；存量异常在触及对应模块时顺带迁移
  （review05 报告 E3 执行建议：不专门开「迁移所有异常」的大重构任务）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.i18n import Message


@dataclass(frozen=True)
class ErrorInfo:
    """异常携带的结构化分类信息。"""

    code: str
    message_key: str
    retryable: bool = False
    format_args: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为与 classify_error 返回结构兼容的 dict。

        classify_error 现有返回形如 {"code": ..., "message_key": ...,
        "format_args": ...}，此处补 retryable 字段保持超集兼容。
        """
        info: dict[str, Any] = {"code": self.code, "message_key": self.message_key}
        if self.format_args:
            info["format_args"] = dict(self.format_args)
        if self.retryable:
            info["retryable"] = self.retryable
        return info


class AppError(Exception):
    """携带结构化分类信息的应用异常基类。

    detail 为面向开发/日志的详情；用户提示由 message_key 在表现层翻译。
    脱敏在日志边界统一处理（log_classified 会对异常整体 sanitize_error），
    core 层不反向依赖 utils（R1/§4.2），故不在构造时脱敏。
    """

    info: ErrorInfo

    def __init__(self, info: ErrorInfo, detail: str = "") -> None:
        self.info = info
        super().__init__(detail or info.code)

    def to_error_info(self) -> dict[str, Any]:
        """与 classify_error 返回结构兼容，供调用方映射用户提示。"""
        return self.info.to_dict()


class StrategyParamError(AppError):
    """策略业务参数不合法（如数值区间下限大于上限）。

    D3-3: 数值区间参数（如 ``pe_min``/``pe_max``）缺交叉校验时，矛盾参数
    会静默产生空集，UI 误读为"市场上没有这种股票"。本异常在 ``_filter_logic``
    入口抛出并显式透传至表现层，向用户呈现"参数区间无效"，而非伪造空结果。

    携带 ``Message``（i18n key + params）而非预翻译字符串，符合 CLAUDE.md §3.2
    "策略/VM 只产出 i18n key"。``info`` 与 ``self.message`` 同一来源，使
    ``classify_error`` 首分支可直接透传。
    """

    def __init__(self, message: Message, detail: str = "") -> None:
        self.message = message
        super().__init__(
            ErrorInfo(
                code="strategy_param_invalid",
                message_key=message.key,
                format_args=dict(message.params),
            ),
            detail,
        )


class AIBudgetError(AppError):
    """AI token 预算不可用（固定提示词+输出预留已超出模型上下文窗口）。

    D5-4: system 消息与不可裁剪固定块超窗时，若静默 capping 到 1，用户编辑过长的
    自定义提示词会绕过余量导致 API 400，却无法归因到"提示词过长"。本异常显式
    抛出，携带可操作信息（占用 token / 模型窗口），向用户呈现"自定义提示词过长"。

    携带 ``Message``（i18n key + params）而非预翻译字符串，符合 CLAUDE.md §3.2
    "策略/VM 只产出 i18n key"。``info`` 与 ``self.message`` 同一来源，使
    ``classify_error`` 首分支可直接透传。
    """

    def __init__(self, message: Message, detail: str = "") -> None:
        self.message = message
        super().__init__(
            ErrorInfo(
                code="ai_budget_unavailable",
                message_key=message.key,
                format_args=dict(message.params),
            ),
            detail,
        )


class AIConfigError(AppError):
    """AI 供应商配置不合法（如跨供应商 failover 目标无专属 API Key）。

    D8-1: 跨供应商 failover 时，若备用供应商未配置**自己的**专属凭证而全局
    ``ai_api_key`` 存在，旧逻辑会把主供应商的 key 作为 Authorization 发送到备用
    供应商的 endpoint（凭证跨域泄露）。本异常在 ``_build_litellm_params`` 显式抛出，
    使 failover 明确失败并给出可操作提示（"备用供应商未配置 API Key"），而非
    发出一个注定失败且泄露凭证的请求。

    携带 ``Message``（i18n key + params）而非预翻译字符串，符合 CLAUDE.md §3.2
    "策略/VM 只产出 i18n key"。``info`` 与 ``self.message`` 同一来源，使
    ``classify_error`` 首分支可直接透传。
    """

    def __init__(self, message: Message, detail: str = "") -> None:
        self.message = message
        super().__init__(
            ErrorInfo(
                code="ai_config_missing_credential",
                message_key=message.key,
                format_args=dict(message.params),
            ),
            detail,
        )
