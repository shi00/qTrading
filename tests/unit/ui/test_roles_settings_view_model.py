"""角色管理 ViewModel 单测（Task 8）。

覆盖：预置行只读与行顺序、新建/复制/编辑草稿填充、提示词变更升版本与提示键、
仅改名称不升版本、冲突与错误码映射、启停编排、预置行不触达服务、pending 收尾、
CancelledError 原样传播。

替身 ``_RecordingCatalog`` 复用真实 ``RoleCatalogService``（内存 ``RoleCatalogStore``），
只额外记录调用并可按方法注入异常；断言调用列表的精确内容，不使用 ``assert_called*``。
"""

from __future__ import annotations

import asyncio
from types import EllipsisType

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, RoleDefinition
from services.agent_service.roles import RoleCatalogService, RoleCatalogStore
from ui.viewmodels.roles_settings_view_model import (
    RoleDraft,
    RoleRow,
    RolesSettingsState,
    RolesSettingsViewModel,
)
from utils.config_models import ROLE_PROMPT_DEFAULTS

#: 新建/复制的期望模板正文（与设计 §7.1 的 ``sub`` 模板一致，取自配置正本）。
SUB_TEMPLATE = ROLE_PROMPT_DEFAULTS["sub"][0]

pytestmark = pytest.mark.unit


class _RecordingCatalog:
    """``RoleCatalogPort`` 测试替身：真实服务实现 + 调用记录 + 可注入异常。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.calls: list[tuple[object, ...]] = []
        self.raise_on: dict[str, BaseException] = {}
        self._service = RoleCatalogService(RoleCatalogStore(read=self._read, write_if=self._write_if, scan=self._scan))

    # -- 存储端口（内存实现，复用 RoleCatalogStore 的 CAS 语义）-----------------

    async def _read(self, key: str) -> str | None:
        return self._data.get(key)

    async def _write_if(self, key: str, expected: str | None, value: str) -> bool:
        if expected is None:
            if key in self._data:
                return False
            self._data[key] = value
            return True
        if self._data.get(key) != expected:
            return False
        self._data[key] = value
        return True

    async def _scan(self, prefix: str) -> dict[str, str]:
        return {key: value for key, value in self._data.items() if key.startswith(prefix)}

    # -- 测试辅助 -------------------------------------------------------------

    async def seed(
        self,
        role_id: str,
        display_name: str = "示例角色",
        prompt_body: str = "示例正文",
        *,
        model_connection_id: str | None = None,
    ) -> None:
        """直接经真实服务落一条角色（不写入调用记录）。"""

        await self._service.create_new(role_id, display_name, prompt_body, updated_by="seed")
        if model_connection_id is not None:
            await self._service.update(
                role_id,
                model_connection_id=model_connection_id,
                expected_version="r1",
                updated_by="seed",
            )

    def _maybe_raise(self, name: str) -> None:
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    # -- RoleCatalogPort ------------------------------------------------------

    async def create_new(
        self,
        role_id: str,
        display_name: str,
        prompt_body: str,
        *,
        updated_by: str,
        model_connection_id: str | None = None,
    ) -> RoleDefinition:
        self.calls.append(("create_new", role_id, display_name, prompt_body, updated_by, model_connection_id))
        self._maybe_raise("create_new")
        return await self._service.create_new(
            role_id,
            display_name,
            prompt_body,
            updated_by=updated_by,
            model_connection_id=model_connection_id,
        )

    async def copy_from(
        self, source_role_id: str, role_id: str, display_name: str, *, updated_by: str
    ) -> RoleDefinition:
        raise AssertionError("VM 不应再调用 copy_from（草稿即真相）")

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
        self.calls.append(
            ("update", role_id, display_name, prompt_body, model_connection_id, expected_version, updated_by)
        )
        self._maybe_raise("update")
        return await self._service.update(
            role_id,
            display_name=display_name,
            prompt_body=prompt_body,
            model_connection_id=model_connection_id,
            expected_version=expected_version,
            updated_by=updated_by,
        )

    async def disable(self, role_id: str, *, expected_version: str) -> None:
        self.calls.append(("disable", role_id, expected_version))
        self._maybe_raise("disable")
        await self._service.disable(role_id, expected_version=expected_version)

    async def enable(self, role_id: str, *, expected_version: str) -> None:
        self.calls.append(("enable", role_id, expected_version))
        self._maybe_raise("enable")
        await self._service.enable(role_id, expected_version=expected_version)

    async def list_roles(self) -> tuple[RoleDefinition, ...]:
        self.calls.append(("list_roles",))
        self._maybe_raise("list_roles")
        return await self._service.list_roles()

    async def resolve(self, role_id: str) -> RoleDefinition | None:
        self.calls.append(("resolve", role_id))
        self._maybe_raise("resolve")
        return await self._service.resolve(role_id)


def _row(state: RolesSettingsState, role_id: str) -> RoleRow:
    """按 ``role_id`` 取行（避免依赖行下标）。"""

    return next(row for row in state.roles if row.role_id == role_id)


async def _vm_with(catalog: _RecordingCatalog) -> RolesSettingsViewModel:
    vm = RolesSettingsViewModel(catalog=catalog)
    await vm.load()
    return vm


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_puts_presets_first_then_custom_roles() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "只核对风险。")
    await catalog.seed("news_editor", "新闻编辑", "只写摘要。")
    vm = RolesSettingsViewModel(catalog=catalog)
    assert vm.state.pending is False

    await vm.load()

    state = vm.state
    assert state.pending is False
    assert state.error_key is None
    assert [row.role_id for row in state.roles] == ["main", "sub", "news_editor", "risk_reviewer"]
    for row in state.roles[:2]:
        assert row.is_preset is True
        assert row.display_name is None
        assert row.role_version is None
        assert row.model_connection_id is None
        assert row.status == "active"
    custom = state.roles[2:]
    assert [row.is_preset for row in custom] == [False, False]
    assert [row.role_version for row in custom] == ["r1", "r1"]
    assert custom[0].display_name == "新闻编辑"
    assert custom[1].display_name == "风险复核"


@pytest.mark.asyncio
async def test_load_failure_maps_unavailable_and_keeps_pending_false() -> None:
    catalog = _RecordingCatalog()
    catalog.raise_on["list_roles"] = AgentServiceError(
        AgentErrorCode.CONFIG_UNAVAILABLE, "role definition is not decodable", "agent.role.def.x"
    )
    vm = RolesSettingsViewModel(catalog=catalog)

    await vm.load()

    assert vm.state.pending is False
    assert vm.state.error_key == "agent_roles_unavailable"
    assert vm.state.roles == ()


# ---------------------------------------------------------------------------
# 新建 / 复制
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_begin_create_prefills_sub_template_then_save_creates_r1() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    vm.begin_create()
    draft = vm.state.editing
    assert (draft.role_id, draft.display_name) == ("", "")
    assert draft.prompt_body == SUB_TEMPLATE
    assert draft.model_connection_id is None
    assert draft.role_version is None
    assert draft.is_new is True
    assert draft.copy_from is None

    vm.set_role_id("risk_reviewer")
    vm.set_display_name("风险复核")
    vm.set_prompt_body("只核对风险。")
    assert vm.state.pending is False

    assert await vm.save() is True

    state = vm.state
    assert state.pending is False
    assert state.editing is None
    assert state.error_key is None
    assert state.notice_key == "agent_roles_created"
    assert catalog.calls == [
        ("create_new", "risk_reviewer", "风险复核", "只核对风险。", "local", None),
        ("list_roles",),
    ]
    assert [row.role_id for row in state.roles] == ["main", "sub", "risk_reviewer"]
    created = _row(state, "risk_reviewer")
    assert created.is_preset is False
    assert created.role_version == "r1"
    assert created.status == "active"
    assert created.display_name == "风险复核"


@pytest.mark.asyncio
async def test_copy_prefills_source_prompt_and_model_then_saves_copy() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "只核对风险。", model_connection_id="conn-1")
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    await vm.copy("risk_reviewer")

    draft = vm.state.editing
    assert draft.role_id == ""
    assert draft.display_name == ""
    assert draft.prompt_body == "只核对风险。"
    assert draft.model_connection_id == "conn-1"
    assert draft.role_version is None
    assert draft.is_new is True
    assert draft.copy_from == "risk_reviewer"
    assert catalog.calls == [("resolve", "risk_reviewer")]

    catalog.calls.clear()
    vm.set_role_id("audit_viewer")
    vm.set_display_name("审计复核")

    assert await vm.save() is True

    # 草稿即真相：create_new 单次落库（含模型覆盖），不再二次 update。
    assert catalog.calls == [
        ("create_new", "audit_viewer", "审计复核", "只核对风险。", "local", "conn-1"),
        ("list_roles",),
    ]
    assert vm.state.notice_key == "agent_roles_created"
    copied = _row(vm.state, "audit_viewer")
    assert copied.role_version == "r1"
    assert copied.model_connection_id == "conn-1"


@pytest.mark.asyncio
async def test_copy_draft_body_edit_is_persisted() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.copy("risk_reviewer")
    vm.set_role_id("audit_viewer")
    vm.set_display_name("审计复核")
    vm.set_prompt_body("复制后改写正文")

    assert await vm.save() is True

    stored = await catalog.resolve("audit_viewer")
    assert stored.prompt_body == "复制后改写正文"
    assert _row(vm.state, "audit_viewer").role_version == "r1"


@pytest.mark.asyncio
async def test_copy_draft_model_override_is_persisted() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.copy("risk_reviewer")
    vm.set_role_id("audit_viewer")
    vm.set_display_name("审计复核")
    vm.set_model_connection_id("conn-2")

    assert await vm.save() is True

    stored = await catalog.resolve("audit_viewer")
    assert stored.model_connection_id == "conn-2"
    assert stored.role_version == "r1"


@pytest.mark.asyncio
async def test_copy_preset_uses_static_template_without_service() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    await vm.copy("sub")

    draft = vm.state.editing
    assert draft.prompt_body == SUB_TEMPLATE
    assert draft.model_connection_id is None
    assert draft.copy_from == "sub"
    assert vm.state.error_key is None
    assert catalog.calls == []


@pytest.mark.asyncio
async def test_copy_missing_source_reports_unavailable_without_editing() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    await vm.copy("ghost_role")

    assert vm.state.editing is None
    assert vm.state.error_key == "agent_roles_unavailable"
    assert catalog.calls == [("resolve", "ghost_role")]


# ---------------------------------------------------------------------------
# 编辑
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_begin_edit_fills_draft_from_definition() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "只核对风险。", model_connection_id="conn-1")
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    await vm.begin_edit("risk_reviewer")

    draft = vm.state.editing
    assert draft is not None
    assert (draft.role_id, draft.display_name, draft.prompt_body) == ("risk_reviewer", "风险复核", "只核对风险。")
    assert draft.model_connection_id == "conn-1"
    assert draft.role_version == "r1"
    assert draft.is_new is False
    assert draft.copy_from is None
    assert catalog.calls == [("resolve", "risk_reviewer")]


@pytest.mark.asyncio
async def test_edit_prompt_bumps_version_and_surfaces_notice() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    await vm.begin_edit("risk_reviewer")
    vm.set_prompt_body("新正文")

    assert await vm.save() is True

    state = vm.state
    assert _row(state, "risk_reviewer").role_version == "r2"
    assert state.notice_key == "agent_roles_version_bumped"
    assert state.error_key is None
    assert state.editing is None
    assert catalog.calls == [
        ("resolve", "risk_reviewer"),
        ("update", "risk_reviewer", "风险复核", "新正文", None, "r1", "local"),
        ("list_roles",),
    ]


@pytest.mark.asyncio
async def test_edit_name_only_keeps_version_and_saved_notice() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)

    await vm.begin_edit("risk_reviewer")
    vm.set_display_name("新名称")

    assert await vm.save() is True

    state = vm.state
    assert _row(state, "risk_reviewer").role_version == "r1"
    assert _row(state, "risk_reviewer").display_name == "新名称"
    assert state.notice_key == "agent_roles_saved"


@pytest.mark.asyncio
async def test_begin_edit_preset_is_readonly_and_skips_service() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    await vm.begin_edit("main")

    assert vm.state.editing is None
    assert vm.state.error_key == "agent_roles_preset_readonly"
    assert catalog.calls == []


@pytest.mark.asyncio
async def test_cancel_edit_clears_editing_and_keys() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    vm.set_prompt_body("草稿")

    vm.cancel_edit()

    assert vm.state.editing is None
    assert vm.state.error_key is None
    assert vm.state.notice_key is None


@pytest.mark.asyncio
async def test_save_without_editing_returns_false_and_skips_service() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    assert await vm.save() is False

    assert catalog.calls == []
    assert vm.state.pending is False


# ---------------------------------------------------------------------------
# 保存失败映射
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_keeps_editing_for_retry() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    vm.set_prompt_body("新正文")
    catalog.raise_on["update"] = AgentServiceError(
        AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", "risk_reviewer"
    )

    assert await vm.save() is False

    state = vm.state
    assert state.error_key == "agent_roles_conflict"
    assert state.editing is not None
    assert state.editing.prompt_body == "新正文"
    assert state.pending is False
    assert _row(state, "risk_reviewer").role_version == "r1"


@pytest.mark.asyncio
async def test_duplicate_role_id_maps_to_invalid_key() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)

    vm.begin_create()
    vm.set_role_id("risk_reviewer")
    vm.set_display_name("重复角色")
    vm.set_prompt_body("正文")

    assert await vm.save() is False

    assert vm.state.error_key == "agent_roles_invalid"
    assert vm.state.editing.role_id == "risk_reviewer"


@pytest.mark.asyncio
async def test_policy_denied_maps_to_model_unavailable_key() -> None:
    """角色模型覆盖不可用（POLICY_DENIED）如实映射专用错误键，保留草稿可重试。"""

    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    vm.begin_create()
    vm.set_role_id("risk_reviewer")
    vm.set_display_name("风险复核")
    vm.set_prompt_body("正文")
    catalog.raise_on["create_new"] = AgentServiceError(
        AgentErrorCode.POLICY_DENIED, "model_connection_unavailable", "risk_reviewer"
    )

    assert await vm.save() is False

    assert vm.state.error_key == "agent_roles_model_unavailable"
    assert vm.state.editing is not None


@pytest.mark.asyncio
async def test_role_not_found_maps_to_unavailable_key() -> None:
    """修订冲突中的 ``role_not_found`` 仍映射「目录不可用」。"""

    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    catalog.raise_on["update"] = AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "role_not_found", "risk_reviewer")

    assert await vm.save() is False

    assert vm.state.error_key == "agent_roles_unavailable"
    assert vm.state.editing is not None


@pytest.mark.asyncio
async def test_save_success_recovers_from_previous_load_failure() -> None:
    """加载失败后保存成功（内部重载成功）即恢复可用，不再残留错误。"""

    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.raise_on["list_roles"] = AgentServiceError(
        AgentErrorCode.CONFIG_UNAVAILABLE, "role_catalog_unavailable", "role_catalog"
    )
    await vm.load()
    assert vm.state.error_key == "agent_roles_unavailable"

    catalog.raise_on.clear()
    vm.begin_create()
    vm.set_role_id("risk_reviewer")
    vm.set_display_name("风险复核")
    vm.set_prompt_body("正文")

    assert await vm.save() is True
    assert vm.state.error_key is None
    assert any(row.role_id == "risk_reviewer" for row in vm.state.roles)


@pytest.mark.asyncio
async def test_save_success_but_reload_failure_reports_unavailable() -> None:
    """保存成功但随后重载失败：以后一次加载结果为准，如实显示不可用。"""

    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    vm.begin_create()
    vm.set_role_id("risk_reviewer")
    vm.set_display_name("风险复核")
    vm.set_prompt_body("正文")
    catalog.raise_on["list_roles"] = AgentServiceError(
        AgentErrorCode.CONFIG_UNAVAILABLE, "role_catalog_unavailable", "role_catalog"
    )

    assert await vm.save() is True
    assert vm.state.error_key == "agent_roles_unavailable"


@pytest.mark.asyncio
async def test_input_invalid_maps_to_invalid_key() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    catalog.raise_on["update"] = AgentServiceError(AgentErrorCode.INPUT_INVALID, "prompt_err_length", "risk_reviewer")

    assert await vm.save() is False

    assert vm.state.error_key == "agent_roles_invalid"
    assert vm.state.editing is not None


@pytest.mark.asyncio
async def test_config_unavailable_maps_to_unavailable_key() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    catalog.raise_on["update"] = AgentServiceError(
        AgentErrorCode.CONFIG_UNAVAILABLE, "role definition is not decodable", "risk_reviewer"
    )

    assert await vm.save() is False

    assert vm.state.error_key == "agent_roles_unavailable"
    assert vm.state.editing is not None


@pytest.mark.asyncio
async def test_unexpected_error_maps_to_save_failed_key() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    catalog.raise_on["update"] = RuntimeError("boom")

    assert await vm.save() is False

    assert vm.state.error_key == "agent_roles_save_failed"
    assert vm.state.editing is not None
    assert vm.state.pending is False


@pytest.mark.asyncio
async def test_cancelled_error_propagates_from_load() -> None:
    catalog = _RecordingCatalog()
    catalog.raise_on["list_roles"] = asyncio.CancelledError()
    vm = RolesSettingsViewModel(catalog=catalog)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await vm.load()

    assert type(excinfo.value) is asyncio.CancelledError
    assert vm.state.pending is False
    assert vm.state.roles == ()


@pytest.mark.asyncio
async def test_cancelled_error_propagates_from_save() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    await vm.begin_edit("risk_reviewer")
    catalog.raise_on["update"] = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await vm.save()

    assert type(excinfo.value) is asyncio.CancelledError
    assert vm.state.pending is False
    assert vm.state.editing == RoleDraft(
        role_id="risk_reviewer",
        display_name="风险复核",
        prompt_body="旧正文",
        model_connection_id=None,
        role_version="r1",
        is_new=False,
        copy_from=None,
    )


# ---------------------------------------------------------------------------
# 启停
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_status_disables_then_enables() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)

    catalog.calls.clear()
    assert vm.state.pending is False
    assert await vm.toggle_status("risk_reviewer") is True

    assert catalog.calls == [("disable", "risk_reviewer", "r1"), ("list_roles",)]
    assert vm.state.notice_key == "agent_roles_disabled"
    assert vm.state.error_key is None
    assert vm.state.pending is False
    assert _row(vm.state, "risk_reviewer").status == "disabled"

    catalog.calls.clear()
    assert await vm.toggle_status("risk_reviewer") is True

    assert catalog.calls == [("enable", "risk_reviewer", "r1"), ("list_roles",)]
    assert vm.state.notice_key == "agent_roles_enabled"
    assert _row(vm.state, "risk_reviewer").status == "active"


@pytest.mark.asyncio
async def test_toggle_status_conflict_maps_and_keeps_row_status() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    catalog.raise_on["disable"] = AgentServiceError(
        AgentErrorCode.REVISION_CONFLICT, "role_version_conflict", "risk_reviewer"
    )

    assert await vm.toggle_status("risk_reviewer") is False

    assert vm.state.error_key == "agent_roles_conflict"
    assert vm.state.pending is False
    assert _row(vm.state, "risk_reviewer").status == "active"


@pytest.mark.asyncio
async def test_toggle_unknown_role_maps_unavailable() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    assert await vm.toggle_status("ghost_role") is False

    assert vm.state.error_key == "agent_roles_unavailable"
    assert catalog.calls == []


@pytest.mark.asyncio
async def test_cancelled_error_propagates_from_toggle_status() -> None:
    catalog = _RecordingCatalog()
    await catalog.seed("risk_reviewer", "风险复核", "旧正文")
    vm = await _vm_with(catalog)
    catalog.raise_on["disable"] = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await vm.toggle_status("risk_reviewer")

    assert type(excinfo.value) is asyncio.CancelledError
    assert vm.state.pending is False
    assert _row(vm.state, "risk_reviewer").status == "active"


@pytest.mark.asyncio
async def test_toggle_status_ignores_preset_rows() -> None:
    catalog = _RecordingCatalog()
    vm = await _vm_with(catalog)
    catalog.calls.clear()

    assert await vm.toggle_status("main") is False
    assert await vm.toggle_status("sub") is False

    assert catalog.calls == []
    assert vm.state.error_key is None
    assert vm.state.notice_key is None
