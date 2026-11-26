"""会话级模型选择（首选连接）存储。

- 复用既有配置域 ``app_state``（键前缀 ``agent.session.model.``），不新建表、不新增迁移；
- 本模块不 import ``data``：读写端口由组合根注入（同 :mod:`roles`）；
- 读取无值返回 ``None``（继承默认模型）；值为空串/仅空白视为损坏显式失败，
  不得伪装成「未配置」（R21）；
- 写入要求非空连接 ID，拒绝把空选择存成事实；
- 端口异常（含 ``CancelledError``）原样传播，不吞没（R2）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from core.agent_types import AgentErrorCode, AgentServiceError

__all__ = ["SessionModelStore"]

_MODEL_SELECTION_DECODABLE: Final[str] = "session model selection is not decodable"
_CONNECTION_ID_REQUIRED: Final[str] = "session model connection id must be non-empty text"


class SessionModelStore:
    """按 ``session_id`` 读写的会话模型选择存储（键前缀 ``agent.session.model.``）。

    端口契约（由组合根注入，本模块不感知持久化实现）：

    - ``read(key) -> str | None``：键不存在返回 ``None``；
    - ``write(key, value) -> None``：覆盖写；
    - ``delete(key) -> bool``：键存在并被删除返回 ``True``。

    三个端口均为异步，异常原样传播（含 ``asyncio.CancelledError``，R2）。
    """

    PREFIX: Final[str] = "agent.session.model."

    def __init__(
        self,
        *,
        read: Callable[[str], Awaitable[str | None]],
        write: Callable[[str, str], Awaitable[None]],
        delete: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._read = read
        self._write = write
        self._delete = delete

    @classmethod
    def key_for(cls, session_id: str) -> str:
        return f"{cls.PREFIX}{session_id}"

    async def read(self, session_id: str) -> str | None:
        """读取会话模型选择；无值 ``None``，空值显式失败（不伪装未配置）。"""

        text = await self._read(self.key_for(session_id))
        if text is None:
            return None
        normalized = text.strip()
        if not normalized:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                _MODEL_SELECTION_DECODABLE,
                session_id,
            )
        return normalized

    async def write(self, session_id: str, connection_id: str) -> None:
        """写入会话模型选择；连接 ID 必须是非空字符串（含非纯空白）。"""

        if not isinstance(connection_id, str) or not connection_id.strip():
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                _CONNECTION_ID_REQUIRED,
                session_id,
            )
        await self._write(self.key_for(session_id), connection_id)

    async def clear(self, session_id: str) -> bool:
        """清除会话模型选择（回到继承默认模型）；返回是否确有既有值被删除。"""

        return await self._delete(self.key_for(session_id))
