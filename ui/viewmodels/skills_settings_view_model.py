"""Skills 设置页 ViewModel（设置页「Skills」页签）。

职责（§1.8 UI 路由 + §3.2 MVVM）：

- 把 ``AgentService`` 的 Skill 目录能力映射为不可变状态快照与命令：导入预览 /
  确认导入（含版本更新）、启用停用、卸载、角色可用与默认 Skill 选择；
- 导入前必须先预览（文件数/展开字节/许可/依赖/是否需确认），确认后才发布；
- 卸载被角色或会话引用时如实报错并转述引用方（R21：不伪装已卸载）；
- 角色默认⊆启用、保存前经服务校验并回读解析结果（复用
  ``resolve_role_skill_selection``），不静默替换版本；
- VM 不 import flet、不持有控件、不感知 locale（只产出 i18n key/params）；
- 同步文件读取与目录清理经 ``ThreadPoolManager`` 提交（R16）；
- ``CancelledError`` 原样传播（R2）；异常消息经 ``DataSanitizer`` 脱敏（R9），
  并按 ``classify_error`` / ``classify_severity`` 分级记录日志。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.agent_types import RoleSkillAssignment, SkillVersionRef, SkillVersionView
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

if TYPE_CHECKING:
    # N01：Agent 栈仅在类型检查时导入；运行时按需局部导入，
    # 依赖缺失时主壳与设置页壳仍可导入。
    from services.agent_service.service import AgentService
    from services.agent_service.skills import SkillImportReceipt, SkillPackageImportRequest

logger = logging.getLogger(__name__)

UNAVAILABLE_KEY = "skills_unavailable"

STATUS_INFO = "info"
STATUS_SUCCESS = "success"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"

IMPORT_KIND_ZIP = "zip"
IMPORT_KIND_DIRECTORY = "directory"
IMPORT_KINDS = (IMPORT_KIND_ZIP, IMPORT_KIND_DIRECTORY)


def _ref_key(skill_id: str, version: int) -> str:
    return f"{skill_id}@{version}"


def _parse_ref_key(key: str) -> SkillVersionRef | None:
    skill_id, separator, raw_version = key.rpartition("@")
    if not separator or not skill_id:
        return None
    try:
        version = int(raw_version)
    except ValueError:
        return None
    if version < 1:
        return None
    return SkillVersionRef(skill_id=skill_id, version=version)


def _format_error(exc: BaseException) -> str:
    return DataSanitizer.sanitize_error(exc if isinstance(exc, Exception) else str(exc))


def _read_archive(path: Path) -> bytes:
    """同步读取 ZIP 内容（由 ``ThreadPoolManager`` 在 IO 线程执行，R16）。"""

    if not path.is_file():
        raise ValueError("path")
    return path.read_bytes()


@dataclass(frozen=True)
class SkillVersionRow:
    """单个已安装版本（状态来自持久层投影，不推断）。"""

    version: int
    read_state: str
    file_count: int
    expanded_bytes: int
    missing_dependencies: tuple[str, ...]

    @property
    def enabled(self) -> bool:
        return self.read_state == "ready"

    @property
    def key(self) -> str:
        return f"{self.version}"


@dataclass(frozen=True)
class SkillRow:
    """一个 Skill 身份及其全部版本（最新版本仅用于展示，不自动迁移引用）。"""

    skill_id: str
    display_name: str
    versions: tuple[SkillVersionRow, ...]

    @property
    def latest_version(self) -> int:
        return max(row.version for row in self.versions) if self.versions else 0


@dataclass(frozen=True)
class SkillImportPreview:
    """导入预览（确认前不发布；``status`` 为服务判定原值）。"""

    status: str
    display_name: str
    skill_id: str
    version: int
    file_count: int
    expanded_bytes: int
    license_id: str
    dependencies: tuple[str, ...]
    missing_dependencies: tuple[str, ...]
    requires_confirmation: bool
    source_kind: str


@dataclass(frozen=True)
class SkillsSettingsState:
    """「Skills」页不可变状态快照。"""

    available: bool = False
    unavailable_reason_key: str = UNAVAILABLE_KEY
    loaded: bool = False
    rows: tuple[SkillRow, ...] = ()
    busy: bool = False
    import_path: str = ""
    import_kind: str = IMPORT_KIND_ZIP
    preview: SkillImportPreview | None = None
    role_id: str = ""
    role_version: str = ""
    role_loaded: bool = False
    role_enabled: tuple[str, ...] = ()
    role_defaults: tuple[str, ...] = ()
    role_effective: tuple[str, ...] = ()
    status: Message | None = None
    status_level: str = STATUS_INFO


class SkillsSettingsViewModel(ObservableViewModelMixin[SkillsSettingsState]):
    """设置页「Skills」页签 ViewModel（经 ``use_viewmodel(factory=)`` 消费）。"""

    def __init__(self, service: AgentService | None, *, user_scope: str) -> None:
        self._service = service
        self._user_scope = user_scope
        self._state = SkillsSettingsState(available=service is not None)
        self._init_mixin_fields()

    # -- 读取 ---------------------------------------------------------------

    async def load(self) -> None:
        """读取已安装 Skill；未接线时保持不可用（不伪造空列表）。"""

        if not self._guard():
            return
        service = self._service
        if service is None:
            return
        self._set_state(busy=True)
        try:
            views = await service.list_skills(user_scope=self._user_scope)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(rows=self._group_rows(views), loaded=True, busy=False)

    def set_import_path(self, value: str) -> None:
        self._set_state(import_path=value, preview=None)

    def set_import_kind(self, kind: str) -> None:
        if kind not in IMPORT_KINDS:
            return
        self._set_state(import_kind=kind, preview=None)

    def set_role_identity(self, field: str, value: str) -> None:
        if field == "role_id":
            self._set_state(role_id=value, role_loaded=False)
        elif field == "role_version":
            self._set_state(role_version=value, role_loaded=False)

    def toggle_role_enabled(self, key: str) -> None:
        """勾选/取消角色启用版本；取消启用同时移除其默认（默认⊆启用）。"""

        if _parse_ref_key(key) is None:
            return
        enabled = set(self._state.role_enabled)
        defaults = set(self._state.role_defaults)
        if key in enabled:
            enabled.discard(key)
            defaults.discard(key)
        else:
            enabled.add(key)
        self._set_state(role_enabled=tuple(sorted(enabled)), role_defaults=tuple(sorted(defaults)))

    def toggle_role_default(self, key: str) -> None:
        """勾选/取消角色默认版本（仅允许在已启用集合内）。"""

        if key not in self._state.role_enabled:
            return
        defaults = set(self._state.role_defaults)
        if key in defaults:
            defaults.discard(key)
        else:
            defaults.add(key)
        self._set_state(role_defaults=tuple(sorted(defaults)))

    # -- 导入 ---------------------------------------------------------------

    async def preview_import(self) -> None:
        """预览导入清单与权限/文件摘要；确认前不发布、不登记。"""

        if not self._guard():
            return
        service = self._service
        if service is None:
            return
        try:
            request = await self._build_request(confirm_update=False)
        except ValueError as exc:
            self._set_state(
                status=Message("skills_status_invalid_path", {"field": str(exc)}), status_level=STATUS_ERROR
            )
            return
        if request is None:
            self._set_state(status=Message("skills_status_invalid_path", {"field": "path"}), status_level=STATUS_ERROR)
            return
        self._set_state(busy=True, status=None, preview=None)
        try:
            receipt = await service.preview_skill_package(request)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(busy=False, preview=self._preview_of(receipt), status_level=STATUS_INFO)

    async def confirm_import(self) -> None:
        """确认导入（版本更新时以 ``confirm_update=True`` 发布新版本，旧版本保留）。"""

        if not self._guard():
            return
        service = self._service
        if service is None:
            return
        if self._state.preview is None:
            self._set_state(status=Message("skills_status_need_preview"), status_level=STATUS_WARNING)
            return
        try:
            request = await self._build_request(confirm_update=True)
        except ValueError as exc:
            self._set_state(
                status=Message("skills_status_invalid_path", {"field": str(exc)}), status_level=STATUS_ERROR
            )
            return
        if request is None:
            self._set_state(status=Message("skills_status_invalid_path", {"field": "path"}), status_level=STATUS_ERROR)
            return
        self._set_state(busy=True, status=None)
        try:
            receipt = await service.import_skill_package(request)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(
            busy=False,
            preview=None,
            status=Message(
                "skills_status_imported",
                {
                    "name": receipt.display_name,
                    "version": str(receipt.version),
                    "status": receipt.status.value,
                },
            ),
            status_level=STATUS_SUCCESS if receipt.status.value != "unchanged" else STATUS_INFO,
        )
        await self.load()

    # -- 启停 / 卸载 --------------------------------------------------------

    async def set_enabled(self, key: str, *, enabled: bool) -> None:
        """启用/停用某个已安装版本（停用受角色引用保护，失败如实报错）。"""

        if not self._guard():
            return
        service = self._service
        ref = _parse_ref_key(key)
        if service is None or ref is None:
            return
        self._set_state(busy=True, status=None)
        try:
            receipt = await service.set_skill_enabled(ref, user_scope=self._user_scope, enabled=enabled)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(
            busy=False,
            status=Message(
                "skills_status_enabled" if enabled else "skills_status_disabled",
                {"name": ref.skill_id, "version": str(receipt.version)},
            ),
            status_level=STATUS_SUCCESS,
        )
        await self.load()

    async def uninstall(self, key: str) -> None:
        """卸载版本；被角色/会话引用时服务拒绝，页面转述引用方且不假装已删。"""

        if not self._guard():
            return
        service = self._service
        ref = _parse_ref_key(key)
        if service is None or ref is None:
            return
        self._set_state(busy=True, status=None)
        try:
            receipt = await service.uninstall_skill(ref, user_scope=self._user_scope)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc, key="skills_status_uninstall_blocked")
            return
        self._set_state(
            busy=False,
            status=Message(
                "skills_status_uninstalled" if receipt.files_removed else "skills_status_uninstalled_files_pending",
                {"name": receipt.skill_id, "version": str(receipt.version)},
            ),
            status_level=STATUS_SUCCESS if receipt.files_removed else STATUS_WARNING,
        )
        await self.load()

    # -- 角色选择 -----------------------------------------------------------

    async def load_role_assignment(self) -> None:
        """按角色身份读回已保存的启用/默认集合；未配置则清空为「未配置」。"""

        if not self._guard():
            return
        service = self._service
        if service is None:
            return
        role_id = self._state.role_id.strip()
        role_version = self._state.role_version.strip()
        if not role_id or not role_version:
            self._set_state(status=Message("skills_status_role_identity_required"), status_level=STATUS_WARNING)
            return
        self._set_state(busy=True, status=None)
        try:
            assignment = await service.read_role_skill_assignment(
                user_scope=self._user_scope,
                role_id=role_id,
                role_version=role_version,
            )
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        if assignment is None:
            self._set_state(
                busy=False,
                role_loaded=True,
                role_enabled=(),
                role_defaults=(),
                role_effective=(),
                status=Message("skills_status_role_unconfigured"),
                status_level=STATUS_INFO,
            )
            return
        self._set_state(
            busy=False,
            role_loaded=True,
            role_enabled=tuple(sorted(_ref_key(ref.skill_id, ref.version) for ref in assignment.enabled)),
            role_defaults=tuple(sorted(_ref_key(ref.skill_id, ref.version) for ref in assignment.defaults)),
            role_effective=(),
            status=None,
            status_level=STATUS_INFO,
        )

    async def save_role_assignment(self) -> None:
        """保存前校验（已安装/可用/默认⊆启用/无冲突）→ 保存 → 回读解析结果。"""

        if not self._guard():
            return
        service = self._service
        if service is None:
            return
        role_id = self._state.role_id.strip()
        role_version = self._state.role_version.strip()
        if not role_id or not role_version:
            self._set_state(status=Message("skills_status_role_identity_required"), status_level=STATUS_WARNING)
            return
        enabled = tuple(ref for ref in (_parse_ref_key(key) for key in self._state.role_enabled) if ref is not None)
        defaults = tuple(ref for ref in (_parse_ref_key(key) for key in self._state.role_defaults) if ref is not None)
        assignment = RoleSkillAssignment(
            role_id=role_id,
            role_version=role_version,
            enabled=enabled,
            defaults=defaults,
        )
        self._set_state(busy=True, status=None)
        try:
            await service.validate_role_skill_assignment(assignment, user_scope=self._user_scope)
            await service.assign_role_skills(
                assignment,
                user_scope=self._user_scope,
                display_name=f"{role_id}@{role_version}",
            )
            effective = await service.resolve_role_skill_selection(
                user_scope=self._user_scope,
                role_id=role_id,
                role_version=role_version,
            )
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(
            busy=False,
            role_loaded=True,
            role_effective=tuple(sorted(_ref_key(ref.skill_id, ref.version) for ref in effective)),
            status=Message("skills_status_role_saved"),
            status_level=STATUS_SUCCESS,
        )

    # -- 内部 ---------------------------------------------------------------

    def _guard(self) -> bool:
        if self._service is None:
            self._set_state(status=Message(UNAVAILABLE_KEY), status_level=STATUS_WARNING)
            return False
        return not self._state.busy

    async def _build_request(self, *, confirm_update: bool) -> SkillPackageImportRequest | None:
        """由输入框构造导入请求；ZIP 读取在受管线程执行（R16）。"""

        from services.agent_service.skills import SkillPackageImportRequest

        raw = self._state.import_path.strip()
        if not raw:
            raise ValueError("path")
        path = Path(raw)
        if self._state.import_kind == IMPORT_KIND_DIRECTORY:
            if not path.is_absolute():
                raise ValueError("path")
            return SkillPackageImportRequest(
                user_scope=self._user_scope,
                directory_name=path.name,
                directory=str(path),
                confirm_update=confirm_update,
            )
        data = await ThreadPoolManager().run_async(TaskType.IO, _read_archive, path)
        return SkillPackageImportRequest(
            user_scope=self._user_scope,
            directory_name=path.stem,
            data=data,
            confirm_update=confirm_update,
        )

    @staticmethod
    def _preview_of(receipt: SkillImportReceipt) -> SkillImportPreview:
        return SkillImportPreview(
            status=receipt.status.value,
            display_name=receipt.display_name,
            skill_id=receipt.skill_id,
            version=receipt.version,
            file_count=receipt.file_count,
            expanded_bytes=receipt.expanded_bytes,
            license_id=receipt.license_id,
            dependencies=receipt.dependencies,
            missing_dependencies=receipt.missing_dependencies,
            requires_confirmation=receipt.requires_confirmation,
            source_kind=receipt.source_kind,
        )

    @staticmethod
    def _group_rows(views: tuple[SkillVersionView, ...]) -> tuple[SkillRow, ...]:
        grouped: dict[str, list[SkillVersionView]] = {}
        for view in views:
            grouped.setdefault(view.skill_id, []).append(view)
        rows: list[SkillRow] = []
        for skill_id in sorted(grouped):
            items = grouped[skill_id]
            rows.append(
                SkillRow(
                    skill_id=skill_id,
                    display_name=items[0].display_name or skill_id,
                    versions=tuple(
                        sorted(
                            (
                                SkillVersionRow(
                                    version=item.version,
                                    read_state=item.read_state,
                                    file_count=item.file_count,
                                    expanded_bytes=item.expanded_bytes,
                                    missing_dependencies=item.missing_dependencies,
                                )
                                for item in items
                            ),
                            key=lambda row: row.version,
                        )
                    ),
                )
            )
        return tuple(rows)

    def _fail(self, exc: BaseException, *, key: str = "skills_status_error") -> None:
        """失败统一出口：分类记录 + 脱敏展示，不伪装成功（R21/R9）。"""

        sanitized = _format_error(exc)
        severity = classify_severity(exc if isinstance(exc, Exception) else Exception(str(exc)), "agent_skill")
        code = classify_error(exc if isinstance(exc, Exception) else Exception(str(exc)), "agent_skill").get(
            "code", "unknown"
        )
        if severity == "system":
            logger.error("[SkillsSettingsVM] operation failed code=%s: %s", code, sanitized)
        else:
            logger.warning("[SkillsSettingsVM] operation failed code=%s: %s", code, sanitized)
        self._set_state(
            busy=False,
            status=Message(key, {"reason": sanitized, "code": str(code)}),
            status_level=STATUS_ERROR,
        )


__all__ = [
    "IMPORT_KINDS",
    "IMPORT_KIND_DIRECTORY",
    "IMPORT_KIND_ZIP",
    "STATUS_ERROR",
    "STATUS_INFO",
    "STATUS_SUCCESS",
    "STATUS_WARNING",
    "UNAVAILABLE_KEY",
    "SkillImportPreview",
    "SkillRow",
    "SkillVersionRow",
    "SkillsSettingsState",
    "SkillsSettingsViewModel",
]
