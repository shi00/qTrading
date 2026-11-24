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
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
)
from services.agent_service.mcp import (
    MCPConfigSaveReceipt,
    MCPConnectionTestReceipt,
    MCPServerConfig,
    MCPTransportConfig,
)
from services.agent_service.mcp_lifecycle import MCPConnectReceipt, MCPServiceLifecycle
from ui.viewmodels import Message
from ui.viewmodels.mcp_services_view_model import (
    STATUS_ERROR,
    STATUS_SUCCESS,
    STATUS_WARNING,
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
        self.tool_names: tuple[str, ...] = _TOOL_NAMES
        self.fail_with: BaseException | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def list_services(self) -> tuple[MCPServerConfig, ...]:
        self._maybe_fail()
        return tuple(self.configs)

    async def save_config(self, config: MCPServerConfig) -> MCPConfigSaveReceipt:
        self._maybe_fail()
        self.saved.append(config)
        self.configs = [existing for existing in self.configs if existing.identity != config.identity]
        self.configs.append(config)
        return MCPConfigSaveReceipt(identity=config.identity, connections_opened=0)

    async def connection_test(self, identity: MCPServiceIdentity) -> MCPConnectionTestReceipt:
        self._maybe_fail()
        self.tested.append(identity)
        return MCPConnectionTestReceipt(
            identity=identity,
            tool_names=self.tool_names,
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
    async def test_zero_tool_test_counts_as_tested_and_allows_connect(self) -> None:
        """N19：测试成功但目录为空是合法回执，不得被当成「从未测试」。"""

        fake = _FakeLifecycle([_config()])
        fake.tool_names = ()
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())

        await vm.connection_test()

        assert vm.state.discovered_tools == ()
        assert vm.state.approved_tools == ()
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_tested"
        assert vm.state.status.params["count"] == "0"
        assert vm.state.status_level == STATUS_SUCCESS

        await vm.connect()

        assert fake.connect_calls == [(_identity(), ())]
        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_connected"
        assert vm.state.status.params["count"] == "0"
        assert vm.state.status_level == STATUS_SUCCESS

    @pytest.mark.asyncio
    async def test_zero_tool_catalog_never_fabricates_tools(self) -> None:
        """零工具只如实呈现空目录：不生成可勾选工具、不产生假工具名。"""

        fake = _FakeLifecycle([_config()])
        fake.tool_names = ()
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        await vm.connection_test()

        vm.toggle_approved_tool("read_quote")

        assert vm.state.discovered_tools == ()
        assert vm.state.approved_tools == ()

    @pytest.mark.asyncio
    async def test_failed_test_still_blocks_connect(self) -> None:
        """测试失败不产生回执，启连仍必须重新测试。"""

        fake = _FakeLifecycle([_config()])
        fake.tool_names = ()
        vm = _vm(fake)
        await vm.load()
        vm.select_service(_identity().stable_key())
        fake.fail_with = RuntimeError("connection refused")

        await vm.connection_test()

        assert vm.state.status is not None
        assert vm.state.status.key == "mcp_status_error"
        fake.fail_with = None

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
