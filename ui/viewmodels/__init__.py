"""ViewModel 层公共契约。

Message dataclass 用于 VM state 中的 i18n 消息字段(方案 §3.1 整改原则 3.5):
VM 只产出 (key, params),不感知 locale;View 渲染时 I18n.get(msg.key, **msg.params)。

Message 现定义在 core/i18n.py (Task 3.1): services 层 AppTask 也要使用,
放 core 层避免 R1 架构越界 (services 不可导入 ui). 本模块重新导出保持向后兼容。

NOTE: CONTRIBUTING.md「MVVM 表现层」契约模板用 dict[str, object],但实际
View 消费端 I18n.get(msg.key, **msg.params) 解包 object 类型会触发 pyright
reportArgumentType warning。此处用 dict[str, Any] 规避,后续应同步更新
CONTRIBUTING.md 契约模板(独立任务,不在本次修复范围)。
"""

from core.i18n import Message

__all__ = ["Message"]
