"""角色管理设置页 ViewModel（设置页「角色管理」tab，Task 8）。

职责（CLAUDE.md §3.2 MVVM + 设计 §7.1）：
- 把 :class:`services.agent_service.roles.RoleCatalogService` 的角色目录能力映射为
  不可变状态快照与命令：列表（预置行只读 + 自定义行）、新建、复制、编辑、启停；
- 预置角色 ``main`` / ``sub`` 固定排在最前且只读（展示名由 View 按
  ``agent_roles_preset_<role_id>`` 渲染）；
- 编辑保存按 CAS 版本提交；版本是否递增以服务返回的定义为准（不自行推断）；
- 冲突保留编辑态便于重试，不自动覆盖（设计 §7.1）；
- VM 不 import flet、不持有控件、不感知 locale（仅产出 i18n key）；
- ``CancelledError`` 原样传播（R2）；异常消息经 ``DataSanitizer`` 脱敏（R9），
  并按 ``classify_error`` / ``classify_severity`` 分级记录日志。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from types import EllipsisType
from typing import Any, Protocol

from core.agent_types import AgentErrorCode, AgentServiceError, RoleDefinition
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_models import ROLE_PROMPT_DEFAULTS
from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

#: 预置角色（固定置顶、只读，展示名由 View 用 i18n key 渲染）。
PRESET_ROLE_IDS: tuple[str, ...] = ("main", "sub")

#: 新建/复制的静态模板正文（设计 §7.1「新建以 ``sub`` 为模板复制」）。
SUB_ROLE_PROMPT_TEMPLATE: str = ROLE_PROMPT_DEFAULTS["sub"][0]

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

#: 服务错误码 → 页面错误 i18n key（其他错误码统一落到保存失败）。
ERROR_KEYS: dict[AgentErrorCode, str] = {
    AgentErrorCode.REVISION_CONFLICT: "agent_roles_conflict",
    AgentErrorCode.CONFIG_UNAVAILABLE: "agent_roles_unavailable",
    AgentErrorCode.INPUT_INVALID: "agent_roles_invalid",
}

ERROR_PRESET_READONLY = "agent_roles_preset_readonly"
ERROR_UNAVAILABLE = "agent_roles_unavailable"
ERROR_SAVE_FAILED = "agent_roles_save_failed"

#: 修订冲突的服务原因 → 更精确的页面错误键（未识别原因统一落到冲突）。
CONFLICT_REASON_KEYS: dict[str, str] = {
    "role_already_exists": "agent_roles_invalid",
    "role_not_found": "agent_roles_unavailable",
}

NOTICE_CREATED = "agent_roles_created"
NOTICE_SAVED = "agent_roles_saved"
NOTICE_VERSION_BUMPED = "agent_roles_version_bumped"
NOTICE_DISABLED = "agent_roles_disabled"
NOTICE_ENABLED = "agent_roles_enabled"


class RoleCatalogPort(Protocol):
    """角色目录读写的端口（签名与 :class:`RoleCatalogService` 一致，便于替身注入）。"""

    async def create_new(
        self, role_id: str, display_name: str, prompt_body: str, *, updated_by: str
    ) -> RoleDefinition: ...

    async def update(
        self,
        role_id: str,
        *,
        display_name: str | None = None,
        prompt_body: str | None = None,
        model_connection_id: str | EllipsisType | None = ...,
        expected_version: str,
        updated_by: str,
    ) -> RoleDefinition: ...

    async def disable(self, role_id: str, *, expected_version: str) -> None: ...

    async def enable(self, role_id: str, *, expected_version: str) -> None: ...

    async def list_roles(self) -> tuple[RoleDefinition, ...]: ...

    async def resolve(self, role_id: str) -> RoleDefinition | None: ...


@dataclass(frozen=True, slots=True)
class RoleRow:
    """角色列表行（预置行 ``display_name`` / ``role_version`` 为 ``None``）。"""

    role_id: str
    is_preset: bool
    display_name: str | None
    status: str
    role_version: str | None
    model_connection_id: str | None


@dataclass(frozen=True, slots=True)
class RoleDraft:
    """编辑态草稿；``role_version`` 为编辑对象的 CAS 基线（新建/复制为 ``None``）。"""

    role_id: str
    display_name: str
    prompt_body: str
    model_connection_id: str | None
    role_version: str | None
    is_new: bool
    copy_from: str | None


@dataclass(frozen=True, slots=True)
class RolesSettingsState:
    """「角色管理」页不可变状态快照。"""

    roles: tuple[RoleRow, ...] = ()
    editing: RoleDraft | None = None
    error_key: str | None = None
    notice_key: str | None = None
    pending: bool = False


class RolesSettingsViewModel(ObservableViewModelMixin[RolesSettingsState]):
    """设置页「角色管理」tab ViewModel（经 ``use_viewmodel(factory=)`` 消费）。"""

    def __init__(self, catalog: RoleCatalogPort, *, updated_by: str = "local") -> None:
        self._catalog = catalog
        self._updated_by = updated_by
        self._state: RolesSettingsState = RolesSettingsState()
        self._init_mixin_fields()

    # -- 读取 ---------------------------------------------------------------

    async def load(self) -> None:
        """读取角色目录：预置 ``main`` / ``sub`` 置顶，其后为服务返回顺序。"""

        self._set_state(pending=True, error_key=None)
        try:
            definitions = await self._catalog.list_roles()
        except asyncio.CancelledError:
            self._set_state(pending=False)
            raise  # R2: 取消必须原样传播
        except AgentServiceError as exc:
            self._fail(exc, fallback_key=ERROR_UNAVAILABLE)
            return
        except Exception as exc:
            self._fail(exc, fallback_key=ERROR_UNAVAILABLE)
            return
        self._set_state(roles=self._rows(definitions), pending=False)

    @staticmethod
    def _rows(definitions: tuple[RoleDefinition, ...]) -> tuple[RoleRow, ...]:
        """预置行 + 自定义行；预置不伪造版本/展示名（由 View 用 i18n 渲染）。"""

        presets = tuple(
            RoleRow(
                role_id=role_id,
                is_preset=True,
                display_name=None,
                status=STATUS_ACTIVE,
                role_version=None,
                model_connection_id=None,
            )
            for role_id in PRESET_ROLE_IDS
        )
        custom = tuple(
            RoleRow(
                role_id=definition.role_id,
                is_preset=False,
                display_name=definition.display_name,
                status=definition.status,
                role_version=definition.role_version,
                model_connection_id=definition.model_connection_id,
            )
            for definition in definitions
        )
        return presets + custom

    async def _resolve_definition(self, role_id: str) -> RoleDefinition | None:
        """读取定义；不存在返回 ``None``，读失败映射 ``agent_roles_unavailable``。"""

        try:
            definition = await self._catalog.resolve(role_id)
        except asyncio.CancelledError:
            raise  # R2
        except AgentServiceError as exc:
            self._fail(exc, fallback_key=ERROR_UNAVAILABLE)
            return None
        except Exception as exc:
            self._fail(exc, fallback_key=ERROR_UNAVAILABLE)
            return None
        if definition is None:
            self._set_state(error_key=ERROR_UNAVAILABLE)
        return definition

    # -- 编辑态命令 ---------------------------------------------------------

    def begin_create(self) -> None:
        """进入新建草稿（正文预填 ``sub`` 预置模板，设计 §7.1）。"""

        self._set_state(
            editing=RoleDraft(
                role_id="",
                display_name="",
                prompt_body=SUB_ROLE_PROMPT_TEMPLATE,
                model_connection_id=None,
                role_version=None,
                is_new=True,
                copy_from=None,
            ),
            error_key=None,
            notice_key=None,
        )

    async def copy(self, source_role_id: str) -> None:
        """以来源角色的提示词与模型覆盖为模板预填新建草稿。

        预置角色直接使用静态 ``sub`` 模板（不读目录，目录中无预置定义）；
        自定义角色读目录定义。
        """

        if source_role_id in PRESET_ROLE_IDS:
            self._set_state(
                editing=RoleDraft(
                    role_id="",
                    display_name="",
                    prompt_body=SUB_ROLE_PROMPT_TEMPLATE,
                    model_connection_id=None,
                    role_version=None,
                    is_new=True,
                    copy_from=source_role_id,
                ),
                error_key=None,
                notice_key=None,
            )
            return
        definition = await self._resolve_definition(source_role_id)
        if definition is None:
            return
        self._set_state(
            editing=RoleDraft(
                role_id="",
                display_name="",
                prompt_body=definition.prompt_body,
                model_connection_id=definition.model_connection_id,
                role_version=None,
                is_new=True,
                copy_from=source_role_id,
            ),
            error_key=None,
            notice_key=None,
        )

    async def begin_edit(self, role_id: str) -> None:
        """进入编辑草稿；预置行只读，非当前列表内角色按不可用处理。"""

        row = next((item for item in self._state.roles if item.role_id == role_id), None)
        if row is None:
            self._set_state(error_key=ERROR_UNAVAILABLE)
            return
        if row.is_preset:
            self._set_state(error_key=ERROR_PRESET_READONLY)
            return
        definition = await self._resolve_definition(role_id)
        if definition is None:
            return
        self._set_state(
            editing=RoleDraft(
                role_id=definition.role_id,
                display_name=definition.display_name,
                prompt_body=definition.prompt_body,
                model_connection_id=definition.model_connection_id,
                role_version=definition.role_version,
                is_new=False,
                copy_from=None,
            ),
            error_key=None,
            notice_key=None,
        )

    def set_role_id(self, value: str) -> None:
        self._update_draft(role_id=value)

    def set_display_name(self, value: str) -> None:
        self._update_draft(display_name=value)

    def set_prompt_body(self, value: str) -> None:
        self._update_draft(prompt_body=value)

    def set_model_connection_id(self, value: str | None) -> None:
        self._update_draft(model_connection_id=value)

    def cancel_edit(self) -> None:
        """退出编辑态并清除提示/错误。"""

        self._set_state(editing=None, error_key=None, notice_key=None)

    def _update_draft(self, **changes: Any) -> None:
        draft = self._state.editing
        if draft is None:
            return
        self._set_state(editing=replace(draft, **changes))

    # -- 保存 ---------------------------------------------------------------

    async def save(self) -> bool:
        """提交编辑态：新建/复制走创建，编辑走 CAS 更新；失败保留草稿便于重试。"""

        draft = self._state.editing
        if draft is None:
            return False
        self._set_state(pending=True, error_key=None, notice_key=None)
        try:
            if draft.is_new:
                # 草稿即真相：复制不再重读来源，正文/名称按用户编辑值落库
                await self._catalog.create_new(
                    draft.role_id, draft.display_name, draft.prompt_body, updated_by=self._updated_by
                )
                if draft.model_connection_id is not None:
                    # 新建固定 r1；模型覆盖单独写入（不改提示词、不升版本）
                    await self._catalog.update(
                        draft.role_id,
                        model_connection_id=draft.model_connection_id,
                        expected_version="r1",
                        updated_by=self._updated_by,
                    )
                notice_key = NOTICE_CREATED
            else:
                if draft.role_version is None:
                    # 编辑态缺少 CAS 基线（构造契约不可达）：如实失败，不伪造保存成功
                    self._set_state(pending=False, error_key=ERROR_SAVE_FAILED)
                    return False
                updated = await self._catalog.update(
                    draft.role_id,
                    display_name=draft.display_name,
                    prompt_body=draft.prompt_body,
                    model_connection_id=draft.model_connection_id,
                    expected_version=draft.role_version,
                    updated_by=self._updated_by,
                )
                # 版本是否递增以服务返回值为准：提示词变化才会升版本
                notice_key = NOTICE_VERSION_BUMPED if updated.role_version != draft.role_version else NOTICE_SAVED
        except asyncio.CancelledError:
            self._set_state(pending=False)
            raise  # R2
        except AgentServiceError as exc:
            self._fail(exc, fallback_key=ERROR_SAVE_FAILED)
            return False
        except Exception as exc:
            self._fail(exc, fallback_key=ERROR_SAVE_FAILED)
            return False
        # 先清草稿与提示，再由 load() 统一收敛 pending（避免 True→False→True 抖动）
        self._set_state(editing=None, notice_key=notice_key)
        await self.load()
        return True

    # -- 启停 ---------------------------------------------------------------

    async def toggle_status(self, role_id: str) -> bool:
        """按当前状态停用/启用角色；预置行不处理，未知角色报不可用。"""

        row = next((item for item in self._state.roles if item.role_id == role_id), None)
        if row is None:
            self._set_state(error_key=ERROR_UNAVAILABLE)
            return False
        if row.is_preset:
            return False
        if row.role_version is None:
            # 自定义行缺版本无法做 CAS（构造契约不可达）：如实报不可用
            self._set_state(error_key=ERROR_UNAVAILABLE)
            return False
        expected_version = row.role_version
        disabling = row.status == STATUS_ACTIVE
        self._set_state(pending=True, error_key=None, notice_key=None)
        try:
            if disabling:
                await self._catalog.disable(role_id, expected_version=expected_version)
            else:
                await self._catalog.enable(role_id, expected_version=expected_version)
        except asyncio.CancelledError:
            self._set_state(pending=False)
            raise  # R2
        except AgentServiceError as exc:
            self._fail(exc, fallback_key=ERROR_SAVE_FAILED)
            return False
        except Exception as exc:
            self._fail(exc, fallback_key=ERROR_SAVE_FAILED)
            return False
        self._set_state(
            notice_key=NOTICE_DISABLED if disabling else NOTICE_ENABLED,
            pending=False,
        )
        await self.load()
        return True

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _error_key_for(exc: AgentServiceError, fallback_key: str) -> str:
        """错误码 → i18n key；修订冲突按服务原因细分（重复 id / 不存在）。"""

        if exc.code is AgentErrorCode.REVISION_CONFLICT:
            return CONFLICT_REASON_KEYS.get(exc.sanitized_message, "agent_roles_conflict")
        return ERROR_KEYS.get(exc.code, fallback_key)

    def _fail(self, exc: Exception, *, fallback_key: str) -> None:
        """失败统一出口：错误码映射 + 分类记录 + 脱敏，不伪装成功（R9/R21）。"""

        key = self._error_key_for(exc, fallback_key) if isinstance(exc, AgentServiceError) else fallback_key
        sanitized = DataSanitizer.sanitize_error(exc)
        severity = classify_severity(exc, "agent_role")
        code = classify_error(exc, "agent_role").get("code", "unknown")
        if severity == "system":
            logger.error("[RolesSettingsVM] operation failed code=%s: %s", code, sanitized)
        else:
            logger.warning("[RolesSettingsVM] operation failed code=%s: %s", code, sanitized)
        self._set_state(pending=False, error_key=key)


__all__ = [
    "CONFLICT_REASON_KEYS",
    "ERROR_KEYS",
    "ERROR_PRESET_READONLY",
    "ERROR_SAVE_FAILED",
    "ERROR_UNAVAILABLE",
    "NOTICE_CREATED",
    "NOTICE_DISABLED",
    "NOTICE_ENABLED",
    "NOTICE_SAVED",
    "NOTICE_VERSION_BUMPED",
    "PRESET_ROLE_IDS",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "SUB_ROLE_PROMPT_TEMPLATE",
    "RoleCatalogPort",
    "RoleDraft",
    "RoleRow",
    "RolesSettingsState",
    "RolesSettingsViewModel",
]
