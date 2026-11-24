"""MCPServicesViewModel 单测（设置页「MCP 服务」页签）。

覆盖：
1. 未接线：state.available=False，命令只给出原因键、不发起任何调用；
2. load：配置 → 行投影（含连接状态），读取失败不伪装 loaded；
3. 表单：字段白名单 / 预算下标 / 核准工具只能来自本次发现；
4. 保存：草稿解析失败报字段；成功保存后回读列表；配置往返不丢字段；
5. 连接测试与启连：未保存/未测试时拒绝；启连核准集透传；
6. 断开：回执状态；
7. R2：CancelledError 一律传播，且 busy 复位。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import cast

import pytest

from core.agent_types import (
    MCP_BUDGET_FIELDS,
    AgentErrorCode,
    AgentServiceError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    RoleMCPAssignment,
    RoleMCPServiceBinding,
)
from services.agent_service.mcp import (
    MCPConfigSaveReceipt,
    MCPConnectionTestReceipt,
    MCPServerConfig,
    MCPTransportConfig,
)
from services.agent_service.mcp_config_store import (
    LISTING_PATH_UNAVAILABLE,
    LISTING_UNREADABLE,
    MCPConfigListEntry,
)
from services.agent_service.mcp_lifecycle import (
    MCPConnectReceipt,
    MCPServiceDeleteBlocked,
    MCPServiceDeletionReceipt,
    MCPServiceLifecycle,
)
from ui.viewmodels import Message
from ui.viewmodels.mcp_services_view_model import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    STATUS_WARNING,
    MCPRoleDraft,
    MCPServicesState,
    MCPServicesViewModel,
)

pytestmark = pytest.mark.unit

_TOOL_NAMES = ("read_quote", "read_news")


def _identity(service_id: str = "svc", version: int = 1) -> MCPServiceIdentity:
    return MCPServiceIdentity(service_id=service_id, config_version=version)


def _audit(identity: MCPServiceIdentity) -> MCPReadOnlyAudit:
    return MCPReadOnlyAudit(
        service=identity,
        tool_scope=_TOOL_NAMES,
        implementation_fingerprint="a" * 64,
        evidence_source="manual-review",
        reviewed_at="2026-11-20T00:00:00.000000Z",
        invalidation_conditions=("impl-change",),
    )


def _config(service_id: str = "svc", version: int = 1) -> MCPServerConfig:
    identity = _identity(service_id, version)
    return MCPServerConfig(
        identity=identity,
        display_name=f"{service_id} display",
        transport=MCPTransportConfig(kind=MCPTransportKind.STDIO, command="/bin/echo", cwd="/tmp"),
        budget=MCPBudgetProfile(**{name: 1024 for name in MCP_BUDGET_FIELDS}),
        audit=_audit(identity),
    )


class _FakeLifecycle:
    """结构性替身：只实现 VM 调用的方法，记录调用参数。"""

    def __init__(self, configs: Iterable[MCPServerConfig] = ()) -> None:
        self.configs: list[MCPServerConfig] = list(configs)
        self.connected_keys: set[str] = set()
        self.saved: list[MCPServerConfig] = []
        self.tested: list[MCPServiceIdentity] = []
        self.connect_calls: list[tuple[MCPServiceIdentity, tuple[str, ...]]] = []
        self.connect_receipts: list[MCPConnectionTestReceipt] = []
        self.disconnected: list[MCPServiceIdentity] = []
        self.residue_retries: list[MCPServiceIdentity] = []
        self.residue_closed = 1
        self.fail_with: BaseException | None = None
        #: 路径失效的配置身份（列表标注为不可用，但仍可载入编辑）。
        self.path_invalid_keys: set[str] = set()
        #: 连结构都无法解码的记录（列表跳过并计数）。
        self.extra_entries: list[MCPConfigListEntry] = []
        #: 使用点仍拒绝的身份（模拟存储读取期的 fail-closed）。
        self.reject_identities: set[str] = set()
        self.roles: list[object] = []
        self.role_saved: list[object] = []
        self.deleted: list[MCPServiceIdentity] = []
        self.delete_error: BaseException | None = None

    async def list_role_assignments(self) -> tuple[object, ...]:
        self._maybe_fail()
        return tuple(self.roles)

    async def save_role_assignment(self, assignment: object) -> object:
        self._maybe_fail()
        self.role_saved.append(assignment)
        self.roles = [
            existing
            for existing in self.roles
            if (getattr(existing, "role_id", None), getattr(existing, "role_version", None))
            != (getattr(assignment, "role_id", None), getattr(assignment, "role_version", None))
        ]
        self.roles.append(assignment)
        return assignment

    async def delete_service(self, identity: MCPServiceIdentity, *, confirmation: bool = False) -> object:
        self._maybe_fail()
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(identity)
        self.configs = [config for config in self.configs if config.identity != identity]
        return MCPServiceDeletionReceipt(
            identity=identity,
            deleted_config=True,
            deleted_grant=True,
            credential_references=("keyring:mcp-token",),
        )

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def list_services(self) -> tuple[MCPConfigListEntry, ...]:
        self._maybe_fail()
        entries = [
            MCPConfigListEntry(
                identity_key=config.identity.stable_key(),
                config=config,
                problem=(LISTING_PATH_UNAVAILABLE if config.identity.stable_key() in self.path_invalid_keys else None),
            )
            for config in self.configs
        ]
        return tuple(entries) + tuple(self.extra_entries)

    async def save_config(self, config: MCPServerConfig) -> MCPConfigSaveReceipt:
        self._maybe_fail()
        self.saved.append(config)
        self.configs = [existing for existing in self.configs if existing.identity != config.identity]
        self.configs.append(config)
        return MCPConfigSaveReceipt(identity=config.identity, connections_opened=0)

    async def connection_test(self, identity: MCPServiceIdentity) -> MCPConnectionTestReceipt:
        self._maybe_fail()
        if identity.stable_key() in self.reject_identities:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp config payload is not decodable",
                identity.stable_key(),
            )
        self.tested.append(identity)
        return MCPConnectionTestReceipt(
            identity=identity,
            tool_names=_TOOL_NAMES,
            catalog_digest="d" * 64,
            research_calls=0,
            closed=True,
            config_digest="c" * 64,
            generation=len(self.tested),
        )

    async def connect(
        self,
        identity: MCPServiceIdentity,
        *,
        approved_tools: Iterable[str],
        test_receipt: MCPConnectionTestReceipt,
    ) -> MCPConnectReceipt:
        self._maybe_fail()
        approved = tuple(sorted(approved_tools))
        self.connect_calls.append((identity, approved))
        self.connect_receipts.append(test_receipt)
        self.connected_keys.add(identity.stable_key())
        return MCPConnectReceipt(identity=identity, catalog_digest="d" * 64, tools=approved)

    async def retry_test_residue_close(self, identity: MCPServiceIdentity) -> int:
        self._maybe_fail()
        self.residue_retries.append(identity)
        return self.residue_closed

    async def disconnect(self, identity: MCPServiceIdentity) -> None:
        self._maybe_fail()
        self.disconnected.append(identity)
        self.connected_keys.discard(identity.stable_key())

    def is_connected(self, identity: MCPServiceIdentity) -> bool:
        return identity.stable_key() in self.connected_keys


def _vm(fake: _FakeLifecycle | None) -> MCPServicesViewModel:
    return MCPServicesViewModel(cast(MCPServiceLifecycle, fake) if fake is not None else None)


def _assert_immutable_state(state: MCPServicesState) -> None:
    assert isinstance(state.rows, tuple)
    assert isinstance(state.discovered_tools, tuple)
    assert isinstance(state.approved_tools, tuple)
    assert isinstance(state.draft.budget_values, tuple)


def _fill_minimal_draft(vm: MCPServicesViewModel) -> None:
    vm.set_draft_text("service_id", "svc")
    vm.set_draft_text("config_version", "1")
    vm.set_draft_text("display_name", "svc display")
    vm.set_draft_text("command", "/bin/echo")
    vm.set_draft_text("cwd", "/tmp")
    for index in range(len(MCP_BUDGET_FIELDS)):
        vm.set_budget_value(index, "1024")


class TestUnavailable:
    def test_state_reports_unavailable(self) -> None:
        vm = _vm(None)
        assert vm.state.available is False
        assert vm.state.unavailable_reason_key == "mcp_unavailable"
        _assert_immutable_state(vm.state)

    @pytest.mark.asyncio
    async def test_commands_do_not_fabricate_success(self) -> None:
        vm = _vm(None)
        await vm.load()
        await vm.save_config()
        await vm.connection_test()
        await vm.connect()
        await vm.disconnect()

        assert vm.state.loaded is False
        assert vm.state.busy is False
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_unavailable"
        assert vm.state.status_level == STATUS_WARNING


class TestLoad:
    @pytest.mark.asyncio
    async def test_load_projects_rows_and_connection_state(self) -> None:
        fake = _FakeLifecycle([_config("alpha", 1), _config("beta", 2)])
        fake.connected_keys.add(_identity("beta", 2).stable_key())
        vm = _vm(fake)

        await vm.load()

        assert vm.state.loaded is True
        assert [row.service_id for row in vm.state.rows] == ["alpha", "beta"]
        assert [row.connected for row in vm.state.rows] == [False, True]
        assert vm.state.rows[0].transport_kind == MCPTransportKind.STDIO.value
        _assert_immutable_state(vm.state)

    @pytest.mark.asyncio
    async def test_refresh_after_process_exit_shows_disconnected_without_connecting(self) -> None:
        """N11：进程自行退出后刷新不得再显示已连接，也不得顺手建立新连接。"""

        fake = _FakeLifecycle([_config()])
        fake.connected_keys.add(_identity().stable_key())
        vm = _vm(fake)
        await vm.load()
        assert [row.connected for row in vm.state.rows] == [True]

        fake.connected_keys.discard(_identity().stable_key())  # 子进程自行退出

        await vm.load()

        assert [row.connected for row in vm.state.rows] == [False]
        assert fake.connect_calls == []

    @pytest.mark.asyncio
    async def test_load_failure_keeps_loaded_false(self) -> None:
        fake = _FakeLifecycle([_config()])
        fake.fail_with = RuntimeError("db is down token=secret")
        vm = _vm(fake)

        await vm.load()

        assert vm.state.loaded is False
        assert vm.state.busy is False
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"
        assert vm.state.status_level == STATUS_ERROR
        assert "token=secret" not in str(vm.state.status.params.get("reason"))

    @pytest.mark.asyncio
    async def test_load_cancelled_propagates_and_clears_busy(self) -> None:
        fake = _FakeLifecycle()
        fake.fail_with = asyncio.CancelledError()
        vm = _vm(fake)

        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.load()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False


class TestPerRowTolerance:
    """A 路径失效 + B 健康：同一列表共存，A 可编辑修复，使用点仍拒绝（J25）。"""

    @pytest.mark.asyncio
    async def test_failed_row_does_not_hide_healthy_row(self) -> None:
        fake = _FakeLifecycle([_config("alpha", 1), _config("beta", 2)])
        fake.path_invalid_keys.add(_identity("alpha", 1).stable_key())
        vm = _vm(fake)

        await vm.load()

        assert vm.state.loaded is True
        assert [row.service_id for row in vm.state.rows] == ["alpha", "beta"]
        assert vm.state.rows[0].problem_key == "mcp_row_path_invalid"
        assert vm.state.rows[1].problem_key is None
        assert vm.state.unreadable_count == 0

    @pytest.mark.asyncio
    async def test_failed_row_stays_editable_and_operations_are_still_refused(self) -> None:
        key = _identity("alpha", 1).stable_key()
        fake = _FakeLifecycle([_config("alpha", 1), _config("beta", 2)])
        fake.path_invalid_keys.add(key)
        fake.reject_identities.add(key)
        vm = _vm(fake)
        await vm.load()

        vm.select_service(key)

        assert vm.state.draft.command == "/bin/echo"
        assert vm.state.draft.service_id == "alpha"
        assert vm.state.draft_saved is True

        await vm.connection_test()

        assert fake.tested == []
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"
        assert vm.state.status_level == STATUS_ERROR

        await vm.connect()

        assert fake.connect_calls == []

    @pytest.mark.asyncio
    async def test_healthy_row_remains_independently_manageable(self) -> None:
        fake = _FakeLifecycle([_config("alpha", 1), _config("beta", 2)])
        fake.path_invalid_keys.add(_identity("alpha", 1).stable_key())
        vm = _vm(fake)
        await vm.load()

        vm.select_service(_identity("beta", 2).stable_key())
        await vm.connection_test()

        assert fake.tested == [_identity("beta", 2)]
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_tested"

    @pytest.mark.asyncio
    async def test_undecodable_record_is_counted_not_listed(self) -> None:
        fake = _FakeLifecycle([_config("beta", 2)])
        fake.extra_entries.append(MCPConfigListEntry(identity_key="mcp_service_deadbeef", problem=LISTING_UNREADABLE))
        vm = _vm(fake)

        await vm.load()

        assert vm.state.loaded is True
        assert [row.service_id for row in vm.state.rows] == ["beta"]
        assert vm.state.unreadable_count == 1


class TestDraftEditing:
    @pytest.mark.asyncio
    async def test_select_service_round_trips_config(self) -> None:
        config = _config()
        fake = _FakeLifecycle([config])
        vm = _vm(fake)
        await vm.load()

        vm.select_service(config.identity.stable_key())
        assert vm.state.draft_saved is True
        assert vm.state.draft.service_id == "svc"
        assert vm.state.draft.budget_values == tuple("1024" for _ in MCP_BUDGET_FIELDS)
        assert vm.state.draft.audit_tool_scope_text == "\n".join(_TOOL_NAMES)

        await vm.save_config()
        assert fake.saved == [config]

    def test_unknown_text_field_is_ignored(self) -> None:
        vm = _vm(_FakeLifecycle())
        before = vm.state.draft
        vm.set_draft_text("not_a_field", "x")
        assert vm.state.draft == before

    def test_budget_index_out_of_range_is_ignored(self) -> None:
        vm = _vm(_FakeLifecycle())
        vm.set_budget_value(99, "1")
        assert vm.state.draft.budget_values == ("",) * len(MCP_BUDGET_FIELDS)

    @pytest.mark.asyncio
    async def test_approved_tools_only_from_discovered(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        vm.toggle_approved_tool("not-discovered")
        assert vm.state.approved_tools == ()

        await vm.connection_test()
        vm.toggle_approved_tool("read_news")
        assert vm.state.approved_tools == ("read_news",)


class TestOperations:
    @pytest.mark.asyncio
    async def test_save_invalid_draft_reports_field(self) -> None:
        fake = _FakeLifecycle()
        vm = _vm(fake)

        await vm.save_config()

        assert fake.saved == []
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_invalid_draft"
        assert vm.state.status_level == STATUS_ERROR

    @pytest.mark.asyncio
    async def test_save_success_reloads_rows(self) -> None:
        fake = _FakeLifecycle()
        vm = _vm(fake)
        _fill_minimal_draft(vm)

        await vm.save_config()

        assert len(fake.saved) == 1
        assert vm.state.loaded is True
        assert [row.service_id for row in vm.state.rows] == ["svc"]
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_saved"
        assert vm.state.status_level == STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_connection_test_requires_saved_config(self) -> None:
        fake = _FakeLifecycle()
        vm = _vm(fake)
        _fill_minimal_draft(vm)

        await vm.connection_test()

        assert fake.tested == []
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_need_save"

    @pytest.mark.asyncio
    async def test_connection_test_populates_tools(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.connection_test()

        assert fake.tested == [_identity()]
        assert vm.state.discovered_tools == _TOOL_NAMES
        assert vm.state.approved_tools == ()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_tested"
        assert vm.state.status_level == STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_connect_requires_test_first(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.connect()

        assert fake.connect_calls == []
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_need_test"

    @pytest.mark.asyncio
    async def test_connect_passes_approved_tools_and_refreshes_rows(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        await vm.connection_test()
        vm.toggle_approved_tool("read_quote")

        await vm.connect()

        assert fake.connect_calls == [(_identity(), ("read_quote",))]
        assert len(fake.connect_receipts) == 1
        assert fake.connect_receipts[0].generation == 1
        assert fake.connect_receipts[0].config_digest == "c" * 64
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_connected"
        assert vm.state.rows[0].connected is True

    @pytest.mark.asyncio
    async def test_connect_passes_the_latest_test_receipt(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        await vm.connection_test()
        await vm.connection_test()

        await vm.connect()

        assert [receipt.generation for receipt in fake.connect_receipts] == [2]

    @pytest.mark.asyncio
    async def test_selecting_or_editing_identity_drops_the_receipt(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        await vm.connection_test()
        assert vm.state.discovered_tools == _TOOL_NAMES

        vm.set_draft_text("transport_kind", "streamable_http")
        await vm.connect()

        assert fake.connect_calls == []
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_need_test"

    @pytest.mark.asyncio
    async def test_retry_test_residue_close_reports_status(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.retry_test_residue_close()

        assert fake.residue_retries == [_identity()]
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_residue_closed"
        assert vm.state.status.params["count"] == "1"
        assert vm.state.busy is False

    @pytest.mark.asyncio
    async def test_retry_test_residue_close_without_residue_reports_nothing_closed(self) -> None:
        fake = _FakeLifecycle([_config()])
        fake.residue_closed = 0
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.retry_test_residue_close()

        assert vm.state.status == Message("mcp_status_no_residue", {"service_id": _identity().service_id, "count": "0"})
        assert vm.state.busy is False

    @pytest.mark.asyncio
    async def test_disconnect_reports_status(self) -> None:
        fake = _FakeLifecycle([_config()])
        fake.connected_keys.add(_identity().stable_key())
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.disconnect()

        assert fake.disconnected == [_identity()]
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_disconnected"
        assert vm.state.rows[0].connected is False

    @pytest.mark.asyncio
    async def test_operation_cancelled_propagates(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        fake.fail_with = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.connection_test()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False


class TestGuardAndFailureBranches:
    async def test_begin_new_resets_and_unknown_select_keeps_state(self) -> None:
        vm = _vm(_FakeLifecycle([_config()]))
        await vm.load()
        vm.select_service(_identity().stable_key())

        before = vm.state.draft
        vm.select_service("missing-service@1")
        assert vm.state.draft == before
        assert vm.state.draft_saved is True

        vm.begin_new()
        assert vm.state.draft.service_id == ""
        assert vm.state.draft_saved is False

    async def test_role_edit_ignores_invalid_inputs(self) -> None:
        vm = _vm(_FakeLifecycle([_config()]))
        await vm.load()

        vm.set_role_text("display_name", "ignored")
        vm.toggle_role_service("missing-service@1")
        vm.set_role_default("missing-service@1")
        vm.set_role_tools("missing-service@1", "search")
        vm.select_role_assignment("missing", "1")

        assert vm.state.role_draft == MCPRoleDraft()

    async def test_toggle_role_service_remove_clears_default_and_tools(self) -> None:
        vm = _vm(_FakeLifecycle([_config()]))
        await vm.load()
        key = _identity().stable_key()
        vm.begin_role_new()
        vm.toggle_role_service(key)
        vm.set_role_default(key)
        vm.set_role_tools(key, "search")

        vm.toggle_role_service(key)

        assert vm.state.role_draft.available_keys == ()
        assert vm.state.role_draft.default_key is None
        assert vm.state.role_draft.tools_text == ()

    async def test_role_save_without_store_does_nothing(self) -> None:
        vm = MCPServicesViewModel(None)

        await vm.save_role_assignment()

        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_unavailable"
        assert vm.state.busy is False

    async def test_role_save_cancelled_and_failed(self) -> None:
        key = _identity().stable_key()
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.begin_role_new()
        vm.set_role_text("role_id", "main")
        vm.toggle_role_service(key)
        fake.fail_with = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.save_role_assignment()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False

        fake.fail_with = RuntimeError("save failed")
        await vm.save_role_assignment()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

    async def test_role_build_rejects_bad_version_and_missing_config(self) -> None:
        vm = _vm(_FakeLifecycle([_config()]))
        await vm.load()
        key = _identity().stable_key()

        vm.begin_role_new()
        vm.set_role_text("role_id", "main")
        vm.set_role_text("role_version", "x")
        await vm.save_role_assignment()
        assert vm.state.status is not None
        assert vm.state.status.params["field"] == "role_version"

        vm.set_role_text("role_version", "0")
        await vm.save_role_assignment()
        assert vm.state.status.params["field"] == "role_version"

        vm.set_role_text("role_version", "1")
        vm.toggle_role_service(key)
        vm._configs.clear()
        await vm.save_role_assignment()
        assert vm.state.status.params["field"] == "available"

    async def test_save_config_cancelled_and_failed(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        vm.set_draft_text("service_id", "svc-new")

        fake.fail_with = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.save_config()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False

        fake.fail_with = RuntimeError("save failed")
        await vm.save_config()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

    async def test_deadline_parsing_reports_field(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        for raw in ("", "x", "0"):
            vm.set_draft_text("connect_deadline_s", raw)
            await vm.save_config()
            assert vm.state.status is not None
            assert vm.state.status.params["field"] == "connect_deadline_s"
        assert fake.saved == []

    async def test_budget_parsing_optional_and_invalid(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        index = MCP_BUDGET_FIELDS[0]

        vm.set_budget_value(0, "")
        await vm.save_config()
        assert fake.saved[-1].budget is not None
        assert getattr(fake.saved[-1].budget, index) is None

        vm.set_budget_value(0, "x")
        await vm.save_config()
        assert vm.state.status is not None
        assert vm.state.status.params["field"] == f"budget.{index}"

        vm.set_budget_value(0, "-1")
        await vm.save_config()
        assert vm.state.status.params["field"] == f"budget.{index}"

    async def test_connection_test_failure_and_connect_cancel_or_fail(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        fake.fail_with = RuntimeError("test failed")

        await vm.connection_test()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

        fake.fail_with = None
        await vm.connection_test()
        fake.fail_with = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.connect()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False

        fake.fail_with = RuntimeError("connect failed")
        await vm.connect()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

    async def test_disconnect_cancelled_and_failed(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        fake.fail_with = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.disconnect()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False

        fake.fail_with = RuntimeError("disconnect failed")
        await vm.disconnect()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

    async def test_retry_residue_cancelled_and_failed(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        fake.fail_with = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as exc:
            await vm.retry_test_residue_close()
        assert type(exc.value) is asyncio.CancelledError
        assert vm.state.busy is False

        fake.fail_with = RuntimeError("close failed")
        await vm.retry_test_residue_close()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

    async def test_request_delete_without_identity_and_cancel(self) -> None:
        vm = _vm(_FakeLifecycle([_config()]))
        await vm.load()

        vm.request_delete()
        assert vm.state.delete_pending is False
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_invalid_draft"

        vm.select_service(_identity().stable_key())
        vm.request_delete()
        assert vm.state.delete_pending is True
        vm.cancel_delete()
        assert vm.state.delete_pending is False

    async def test_confirm_delete_ignored_when_not_pending_and_generic_failure(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.confirm_delete()
        assert fake.deleted == []

        vm.request_delete()
        fake.delete_error = RuntimeError("delete failed")
        await vm.confirm_delete()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"

    async def test_refresh_rows_failure_and_cancel(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()

        fake.fail_with = RuntimeError("list failed")
        await vm._refresh_rows()
        status = vm.state.status
        assert status is not None and (status.key, vm.state.status_level) == ("mcp_status_error", STATUS_ERROR)

        fake.fail_with = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as exc:
            await vm._refresh_rows()
        assert type(exc.value) is asyncio.CancelledError

        await MCPServicesViewModel(None)._refresh_rows()


class TestDeleteAndRoleAssignments:
    @pytest.mark.asyncio
    async def test_delete_blocked_lists_role_and_research_references(self) -> None:
        fake = _FakeLifecycle([_config()])
        fake.delete_error = MCPServiceDeleteBlocked(_identity(), role_refs=("main@1",), research_ids=("session-9",))
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        vm.request_delete()
        await vm.confirm_delete()

        assert vm.state.delete_pending is False
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_delete_blocked"
        assert vm.state.status.params["roles"] == "main@1"
        assert vm.state.status.params["researches"] == "session-9"
        assert vm.state.status_level == STATUS_WARNING
        assert fake.deleted == []

    @pytest.mark.asyncio
    async def test_delete_success_reports_revocation_refs(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        vm.request_delete()
        await vm.confirm_delete()

        assert fake.deleted == [_identity()]
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_deleted"
        assert vm.state.status.params["credentials"] == "keyring:mcp-token"
        assert vm.state.draft_saved is False
        assert vm.state.rows == ()

    @pytest.mark.asyncio
    async def test_role_assignment_saved_with_default_and_tools(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.begin_role_new()
        vm.set_role_text("role_id", "main")
        vm.set_role_text("role_version", "2")
        vm.toggle_role_service(_identity().stable_key())
        vm.set_role_default(_identity().stable_key())
        vm.set_role_tools(_identity().stable_key(), "read_news\nread_quote")

        await vm.save_role_assignment()

        assert len(fake.role_saved) == 1
        saved = cast(RoleMCPAssignment, fake.role_saved[0])
        assert saved.role_id == "main"
        assert saved.role_version == "2"
        assert saved.default_service == _identity()
        assert saved.available[0].tools == ("read_news", "read_quote")
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_role_saved"
        assert vm.state.role_assignments[0].role_id == "main"

    @pytest.mark.asyncio
    async def test_role_assignment_rejects_missing_role_id(self) -> None:
        fake = _FakeLifecycle([_config()])
        vm = _vm(fake)
        await vm.load()
        vm.begin_role_new()
        vm.toggle_role_service(_identity().stable_key())

        await vm.save_role_assignment()

        assert fake.role_saved == []
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_invalid_draft"

    @pytest.mark.asyncio
    async def test_role_rows_loaded_and_selected(self) -> None:
        fake = _FakeLifecycle([_config()])
        fake.roles = [
            RoleMCPAssignment(
                role_id="main",
                role_version="1",
                available=(RoleMCPServiceBinding(identity=_identity(), tools=("read_quote",)),),
                default_service=_identity(),
            )
        ]
        vm = _vm(fake)
        await vm.load()

        assert vm.state.roles_available is True
        assert vm.state.role_assignments[0].default_key == _identity().stable_key()

        vm.select_role_assignment("main", "1")

        assert vm.state.role_draft.role_id == "main"
        assert vm.state.role_draft.default_key == _identity().stable_key()
        assert dict(vm.state.role_draft.tools_text)[_identity().stable_key()] == "read_quote"
