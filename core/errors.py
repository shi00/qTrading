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
