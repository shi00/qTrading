"""执行准入门禁（ADR-0008 §0.6 协调结果，默认关闭）。

批准事实只能由可信应用配置构造；模型/请求/Prompt/MCP 声明都不能写入。
"""

from dataclasses import dataclass
from typing import Literal

BUSINESS_PROVIDER_ALLOWLIST: tuple[str, ...] = ("stock", "pool", "strategy", "backtest", "news")

Mode = Literal["discuss", "plan", "execute"]


@dataclass(frozen=True, slots=True)
class ExecutionPermission:
    """不可变执行许可；默认两项均为 false。"""

    runtime_admitted: bool = False
    release_permission: bool = False

    def allows(self, mode: Mode) -> bool:
        if mode != "execute":
            return True
        return self.runtime_admitted and self.release_permission

    @staticmethod
    def is_provider_allowed(provider_key: str) -> bool:
        return provider_key in BUSINESS_PROVIDER_ALLOWLIST
