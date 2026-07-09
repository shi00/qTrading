"""ViewModel 层公共契约。

Message dataclass 用于 VM state 中的 i18n 消息字段(方案 §3.1 整改原则 3.5):
VM 只产出 (key, params),不感知 locale;View 渲染时 I18n.get(msg.key, **msg.params)。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Message:
    """带参数的 i18n 消息:VM 产出 (key, params),View 按当前 locale 渲染。

    符合 CONTRIBUTING.md「MVVM 表现层」契约:
    - VM 不感知 locale,只产出 key + params
    - View 渲染时调 I18n.get(msg.key, **msg.params)
    """

    key: str
    params: dict[str, Any] = field(default_factory=dict)
