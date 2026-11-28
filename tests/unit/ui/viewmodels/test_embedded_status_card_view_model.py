"""EmbeddedStatusCardViewModel 单元测试 (P3-9).

覆盖:
1. EmbeddedStatusCardState frozen dataclass 字段 + 默认值
2. EmbeddedStatusCardViewModel 初始化默认状态 (embedded_pg_ready + info)
3. state property 返回 frozen snapshot
4. subscribe/dispose 协议 (ObservableViewModelMixin)
5. VM 不感知 locale (state 用 Message 产出 i18n key, 不调 I18n.get)
"""

from dataclasses import FrozenInstanceError

import pytest

from ui.viewmodels import Message
from ui.viewmodels.database_status_view_model import DatabaseStatusState, DatabaseStatusViewModel
from ui.viewmodels.embedded_status_card_view_model import (
    EmbeddedStatusCardState,
    EmbeddedStatusCardViewModel,
)

pytestmark = pytest.mark.unit


class TestEmbeddedStatusCardState:
    """EmbeddedStatusCardState frozen dataclass 契约测试。"""

    def test_state_is_frozen(self) -> None:
        """DoD: state 必须 frozen (MVVM §3.2 不可变 snapshot)。"""
        state = EmbeddedStatusCardState()
        with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
            state.status_message = Message("test")  # type: ignore[misc]

    def test_default_status_type_is_info(self) -> None:
        """默认 status_type=info。"""
        assert EmbeddedStatusCardState().status_type == "info"

    def test_default_status_message_is_none(self) -> None:
        """默认 status_message=None。"""
        assert EmbeddedStatusCardState().status_message is None

    def test_default_info_message_is_none(self) -> None:
        """默认 info_message=None。"""
        assert EmbeddedStatusCardState().info_message is None


class TestEmbeddedStatusCardViewModelInit:
    """EmbeddedStatusCardViewModel 初始化测试（接同一实时状态源，D1-12 ①）。"""

    def test_init_does_not_claim_ready_before_refresh(self) -> None:
        """DoD: 未取得 doctor 状态前不得显示「已自动准备」（R21 未知态）。"""
        vm = EmbeddedStatusCardViewModel()
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "embedded_pg_checking"

    def test_init_sets_info_status_type(self) -> None:
        """DoD: 未知态 status_type=info（非成功绿）。"""
        vm = EmbeddedStatusCardViewModel()
        assert vm.state.status_type == "info"

    def test_init_sets_info_message(self) -> None:
        """DoD: VM 初始化 info_message 含 embedded_pg_no_config_needed key。"""
        vm = EmbeddedStatusCardViewModel()
        assert vm.state.info_message is not None
        assert vm.state.info_message.key == "embedded_pg_no_config_needed"

    def test_init_does_not_import_i18n(self) -> None:
        """DoD: VM 不 import I18n (避免 flet 污染, MVVM §3.2 + i18n 分层矩阵)."""
        import ui.viewmodels.embedded_status_card_view_model as mod

        assert not hasattr(mod, "I18n"), "VM 不应 import I18n (应从 core.i18n 导入, 避免 flet 污染)"


def _database_vm(state: DatabaseStatusState) -> DatabaseStatusViewModel:
    """构造已提交指定 state 的 DatabaseStatusViewModel（不触发 doctor）。"""
    inner = DatabaseStatusViewModel()
    inner._state = state
    return inner


class TestEmbeddedStatusCardViewModelDerivation:
    """状态卡必须从 DatabaseStatusViewModel 实时状态派生（不得固定绿色 ready）。"""

    def test_ready_when_running_without_problems(self) -> None:
        inner = _database_vm(
            DatabaseStatusState(
                is_running=True,
                status_message=Message("db_status_running"),
                status_type="success",
                issues=[],
                restore_residuals=[],
                dump_partials=[],
            )
        )
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "embedded_pg_ready"
        assert vm.state.status_type == "success"

    def test_warning_when_diagnostics_reported(self) -> None:
        inner = _database_vm(
            DatabaseStatusState(
                is_running=True,
                status_message=Message("db_status_running"),
                status_type="warning",
                issues=[{"code": "disk_low", "severity": "warning", "message": "磁盘空间不足"}],
                restore_residuals=[],
                dump_partials=[],
            )
        )
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "embedded_pg_ready_with_issues"
        assert vm.state.status_message.params == {"count": 1}

    def test_warning_when_not_running(self) -> None:
        inner = _database_vm(
            DatabaseStatusState(
                status_message=Message("db_status_stopped"),
                status_type="warning",
            )
        )
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        assert vm.state.status_type == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "db_status_stopped"

    def test_error_when_refresh_failed(self) -> None:
        inner = _database_vm(
            DatabaseStatusState(error_message=Message("db_status_refresh_failed"), status_type="error")
        )
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "db_status_refresh_failed"

    def test_error_overrides_previous_success_snapshot(self) -> None:
        """刷新失败时不得沿用旧「已就绪」快照（已知不可信不可伪装成正常）。"""
        inner = _database_vm(
            DatabaseStatusState(
                is_running=True,
                status_message=Message("db_status_running"),
                status_type="success",
                issues=[],
                restore_residuals=[],
                dump_partials=[],
                error_message=Message("db_status_refresh_failed"),
            )
        )
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        assert vm.state.status_type == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "db_status_refresh_failed"

    def test_running_with_unknown_diagnostics_is_not_ready(self) -> None:
        """运行中但诊断渠道未知时不得宣称「无异常」（R21）。"""
        inner = _database_vm(
            DatabaseStatusState(
                is_running=True,
                status_message=Message("db_status_running"),
                status_type="success",
            )
        )
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        assert vm.state.status_type == "info"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "embedded_pg_checking"

    def test_subscribes_to_database_state_changes(self) -> None:
        inner = _database_vm(DatabaseStatusState())
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        updates: list[EmbeddedStatusCardState] = []
        vm.subscribe(updates.append)

        inner._set_state(
            is_running=True,
            status_message=Message("db_status_running"),
            status_type="success",
            issues=[],
            restore_residuals=[],
            dump_partials=[],
        )

        assert updates, "DatabaseStatusViewModel 状态变化必须转发到状态卡"
        assert updates[-1].status_message is not None
        assert updates[-1].status_message.key == "embedded_pg_ready"

    def test_dispose_releases_inner_view_model(self) -> None:
        inner = _database_vm(DatabaseStatusState())
        vm = EmbeddedStatusCardViewModel(database_status_view_model=inner)
        vm.dispose()

        assert inner.disposed is True
        assert len(inner._subscribers) == 0


class TestEmbeddedStatusCardViewModelProtocol:
    """EmbeddedStatusCardViewModel subscribe/dispose 协议测试 (ObservableViewModelMixin)。"""

    def test_state_property_returns_snapshot(self) -> None:
        """state property 返回当前 snapshot。"""
        vm = EmbeddedStatusCardViewModel()
        snapshot = vm.state
        assert snapshot is not None
        assert snapshot.status_type == "info"

    def test_subscribe_returns_unsub_callable(self) -> None:
        """subscribe 返回退订函数。"""
        vm = EmbeddedStatusCardViewModel()
        unsub = vm.subscribe(lambda _state: None)
        assert callable(unsub)

    def test_subscribe_callback_added_to_subscribers(self) -> None:
        """subscribe 将 callback 加入 _subscribers 列表。"""
        vm = EmbeddedStatusCardViewModel()
        callback = lambda _state: None  # noqa: E731
        unsub = vm.subscribe(callback)
        assert callback in vm._subscribers
        unsub()

    def test_unsub_removes_callback(self) -> None:
        """unsub 将 callback 从 _subscribers 移除。"""
        vm = EmbeddedStatusCardViewModel()
        callback = lambda _state: None  # noqa: E731
        unsub = vm.subscribe(callback)
        unsub()
        assert callback not in vm._subscribers

    def test_dispose_clears_subscribers(self) -> None:
        """dispose 清空 _subscribers。"""
        vm = EmbeddedStatusCardViewModel()
        vm.subscribe(lambda _state: None)
        vm.subscribe(lambda _state: None)
        vm.dispose()
        assert len(vm._subscribers) == 0
