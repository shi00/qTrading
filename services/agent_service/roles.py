"""研究 Agent 角色目录的 ``app_state`` 存储与唯一读写入口。

- 复用既有配置域 ``app_state``（键前缀 ``agent.role.def.``），不新建表、不新增迁移；
- 本模块不 import ``data``：读写端口由组合根注入（同 :mod:`mcp_role`）；
- 写入一律条件写（CAS）：``create`` 只插入，``replace`` / ``mark_disabled`` 以当前
  原始文本为 expected，避免「读-改-写」期间被并发覆盖；
- 读取无值返回 ``None``；载荷不可解码显式失败（不伪装「未配置」，R21）；
- 角色版本只在 ``prompt_body`` 变化时递增（``rN`` → ``rN+1``），名称/模型/启停不升。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace as dataclass_replace
from datetime import UTC, datetime
from types import EllipsisType
from typing import Any, Final

from core.agent_types import (
    ROLE_DEFINITION_VERSION,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    RoleDefinition,
    RoleMCPAssignment,
    RoleSkillAssignment,
    decode_role_definition,
    validate_role_definition,
)
from utils.prompt_guard import sanitize_prompt, validate_prompt

__all__ = ["RoleCatalogService", "RoleCatalogStore", "RolePolicy", "derive_role_policy"]

#: 规范 JSON 序列化参数：create 与 replace 必须逐字节一致，CAS 的 expected 才能命中。
_DUMPS: Final[dict[str, Any]] = {"ensure_ascii": False, "separators": (",", ":"), "sort_keys": True}

_ACTIVE: Final[str] = "active"
_DISABLED: Final[str] = "disabled"


def _now() -> str:
    """ISO-8601 UTC 时间戳（写入 updated_at / created_at）。"""

    return datetime.now(UTC).isoformat()


def _encode(definition: RoleDefinition) -> str:
    return json.dumps(definition.wire(), **_DUMPS)


def _decode(text: str, label: str) -> RoleDefinition:
    try:
        return decode_role_definition(json.loads(text))
    except (TypeError, ValueError) as exc:
        raise AgentServiceError(
            AgentErrorCode.CONFIG_UNAVAILABLE,
            "role definition is not decodable",
            label,
        ) from exc


def _bump_version(role_version: str) -> str:
    return f"r{int(role_version[1:]) + 1}"


class RoleCatalogStore:
    """按 ``role_id`` 读写的角色定义存储（键前缀 ``agent.role.def.``）。"""

    PREFIX = "agent.role.def."

    def __init__(
        self,
        *,
        read: Callable[[str], Awaitable[str | None]],
        write_if: Callable[[str, str | None, str], Awaitable[bool]],
        scan: Callable[[str], Awaitable[Mapping[str, str]]],
    ) -> None:
        self._read = read
        self._write_if = write_if
        self._scan = scan

    @classmethod
    def key_for(cls, role_id: str) -> str:
        return f"{cls.PREFIX}{role_id}"

    async def load(self, role_id: str) -> RoleDefinition | None:
        """读取并解码；无值 ``None``，载荷损坏明确失败（不伪装未配置）。"""

        text = await self._read(self.key_for(role_id))
        if text is None:
            return None
        return _decode(text, role_id)

    async def list_all(self) -> tuple[RoleDefinition, ...]:
        """列出全部自定义角色（按 ``role_id`` 排序）；单条损坏即显式失败。"""

        values = await self._scan(self.PREFIX)
        definitions = [_decode(text, key) for key, text in values.items()]
        return tuple(sorted(definitions, key=lambda item: item.role_id))

    async def create(self, definition: RoleDefinition) -> bool:
        """仅当键不存在时插入（目标已存在返回 ``False``）。"""

        return await self._write_if(self.key_for(definition.role_id), None, _encode(definition))

    async def replace(self, definition: RoleDefinition, *, expected_version: str) -> bool:
        """仅当当前原始文本对应 ``expected_version`` 时覆盖写。"""

        key = self.key_for(definition.role_id)
        current_text = await self._read(key)
        if current_text is None:
            return False
        if _decode(current_text, definition.role_id).role_version != expected_version:
            return False
        return await self._write_if(key, current_text, _encode(definition))

    async def mark_disabled(self, role_id: str, *, expected_version: str) -> bool:
        """仅当版本匹配时把状态置为 ``disabled``（不改变 ``role_version``）。"""

        key = self.key_for(role_id)
        current_text = await self._read(key)
        if current_text is None:
            return False
        current = _decode(current_text, role_id)
        if current.role_version != expected_version:
            return False
        disabled = dataclass_replace(current, status=_DISABLED, updated_at=_now())
        return await self._write_if(key, current_text, _encode(disabled))


class RoleCatalogService:
    """角色目录的唯一读写入口（CAS + 版本递增 + prompt 校验）。"""

    def __init__(self, store: RoleCatalogStore) -> None:
        self._store = store

    async def create_new(
        self,
        role_id: str,
        display_name: str,
        prompt_body: str,
        *,
        updated_by: str,
    ) -> RoleDefinition:
        """新建 ``r1`` 活跃角色；目标已存在即冲突。"""

        now = _now()
        definition = RoleDefinition(
            schema_version=ROLE_DEFINITION_VERSION,
            role_id=role_id,
            display_name=display_name,
            base="sub",
            status=_ACTIVE,
            role_version="r1",
            prompt_body=_guard_prompt(prompt_body, role_id),
            model_connection_id=None,
            capability_mode="explicit",
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
        _validate(definition)
        if not await self._store.create(definition):
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_already_exists", role_id)
        return definition

    async def copy_from(
        self,
        source_role_id: str,
        role_id: str,
        display_name: str,
        *,
        updated_by: str,
    ) -> RoleDefinition:
        """以既有角色为模板新建 ``r1`` 活跃角色（复制提示词与模型覆盖）。"""

        source = await self._store.load(source_role_id)
        if source is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "source_role_not_found", source_role_id)
        now = _now()
        definition = RoleDefinition(
            schema_version=ROLE_DEFINITION_VERSION,
            role_id=role_id,
            display_name=display_name,
            base="sub",
            status=_ACTIVE,
            role_version="r1",
            prompt_body=_guard_prompt(source.prompt_body, role_id),
            model_connection_id=source.model_connection_id,
            capability_mode="explicit",
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
        _validate(definition)
        if not await self._store.create(definition):
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_already_exists", role_id)
        return definition

    async def update(
        self,
        role_id: str,
        *,
        display_name: str | None = None,
        prompt_body: str | None = None,
        model_connection_id: str | EllipsisType | None = ...,
        expected_version: str,
        updated_by: str,
    ) -> RoleDefinition:
        """按 CAS 更新角色；``prompt_body`` 变化才升版本，``...`` 表示不改模型覆盖。"""

        current = await self._load_for_update(role_id, expected_version)
        new_prompt = current.prompt_body if prompt_body is None else _guard_prompt(prompt_body, role_id)
        definition = dataclass_replace(
            current,
            display_name=current.display_name if display_name is None else display_name,
            prompt_body=new_prompt,
            model_connection_id=(
                current.model_connection_id if model_connection_id is Ellipsis else model_connection_id
            ),
            role_version=(
                current.role_version if new_prompt == current.prompt_body else _bump_version(current.role_version)
            ),
            updated_at=_now(),
            updated_by=updated_by,
        )
        _validate(definition)
        if not await self._store.replace(definition, expected_version=expected_version):
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", role_id)
        return definition

    async def disable(self, role_id: str, *, expected_version: str) -> None:
        """停用角色（不升版本）；版本不匹配或不存在即冲突。"""

        await self._load_for_update(role_id, expected_version)
        if not await self._store.mark_disabled(role_id, expected_version=expected_version):
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", role_id)

    async def enable(self, role_id: str, *, expected_version: str) -> None:
        """启用角色（不升版本）；版本不匹配或不存在即冲突。"""

        current = await self._load_for_update(role_id, expected_version)
        definition = dataclass_replace(current, status=_ACTIVE, updated_at=_now())
        _validate(definition)
        if not await self._store.replace(definition, expected_version=expected_version):
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", role_id)

    async def list_roles(self) -> tuple[RoleDefinition, ...]:
        """全部自定义角色（含停用，按 ``role_id`` 排序）。"""

        return await self._store.list_all()

    async def list_active(self) -> tuple[RoleDefinition, ...]:
        """仅活跃角色（供可用集合），按 ``role_id`` 排序。"""

        return tuple(definition for definition in await self._store.list_all() if definition.status == _ACTIVE)

    async def resolve(self, role_id: str) -> RoleDefinition | None:
        """按 ``role_id`` 读取单个角色；不存在返回 ``None``。"""

        return await self._store.load(role_id)

    async def _load_for_update(self, role_id: str, expected_version: str) -> RoleDefinition:
        """读当前定义并校验版本；不存在或版本不匹配统一按修订冲突拒绝。"""

        current = await self._store.load(role_id)
        if current is None:
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_not_found", role_id)
        if current.role_version != expected_version:
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", role_id)
        return current


def _guard_prompt(prompt_body: str, role_id: str) -> str:
    """保存前过 prompt-guard：命中注入/超长即拒绝，通过后按模块净化函数归一。"""

    valid, reason = validate_prompt(prompt_body)
    if not valid:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, reason, role_id)
    return sanitize_prompt(prompt_body)


def _validate(definition: RoleDefinition) -> None:
    """结构校验失败转译为 ``INPUT_INVALID``（保留可识别原因码）。"""

    try:
        validate_role_definition(definition)
    except AgentValidationError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, exc.code, definition.role_id) from exc


@dataclass(frozen=True, slots=True)
class RolePolicy:
    """角色版本的能力上限：Skill 引用、MCP 服务范围与模型连接（缺失即空集）。"""

    role_id: str
    role_version: str
    #: 形如 ``skill:<skill_id>@<version>``。
    allowed_skills: frozenset[str]
    #: 形如 ``mcp_scope:<stable_key>``。
    allowed_mcp: frozenset[str]
    #: ``None`` 表示继承默认模型连接。
    model_connection_id: str | None


def derive_role_policy(
    definition: RoleDefinition,
    *,
    skills: RoleSkillAssignment | None,
    mcp: RoleMCPAssignment | None,
) -> RolePolicy:
    """角色定义 + 既有分配 → 能力上限；无分配记录即空集（不解释为全部，R21）。"""

    allowed_skills = frozenset(
        f"skill:{ref.skill_id}@{ref.version}" for ref in (skills.enabled if skills is not None else ())
    )
    allowed_mcp = frozenset(
        f"mcp_scope:{item.identity.stable_key()}" for item in (mcp.available if mcp is not None else ())
    )
    return RolePolicy(
        role_id=definition.role_id,
        role_version=definition.role_version,
        allowed_skills=allowed_skills,
        allowed_mcp=allowed_mcp,
        model_connection_id=definition.model_connection_id,
    )
