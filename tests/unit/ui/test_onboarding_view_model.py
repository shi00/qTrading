"""Unit tests for OnboardingViewModel — MVVM-003 fix.

TDD RED phase: these tests define the expected ViewModel contract.
"""

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.onboarding_view_model import (
    OnboardingState,
    OnboardingViewModel,
    STEP_CONFIGS,
    StepConfig,
)

pytestmark = pytest.mark.unit


# --- Helpers ---


def _count_transitions(snapshots, field_getter, initial) -> int:
    """Count state transitions in snapshots for a given field.

    A transition occurs when consecutive snapshots (including the initial state)
    have different values for the field returned by field_getter(snapshots[i]).
    The `initial` argument represents the state value before any snapshots.
    """
    transitions = 0
    prev = initial
    for s in snapshots:
        value = field_getter(s)
        if value != prev:
            transitions += 1
            prev = value
    return transitions


# --- I18n mock (autouse, returns key as value) ---


@pytest.fixture(autouse=True)
def _mock_i18n():
    mock_i18n = MagicMock()
    mock_i18n.get = MagicMock(side_effect=lambda key, **kwargs: key)
    yield mock_i18n


# --- Fixtures ---


@pytest.fixture
def vm(monkeypatch):
    """Vanilla ViewModel with no deps injected.

    Mock ConfigHandler.get_init_history_years / get_auto_update_time 以隔离
    test_infra_history_config.py 的真实配置文件污染 (pre-existing flaky:
    该测试调用 ConfigHandler.set_init_history_years(5) 持久化污染 config.json,
    导致本测试 vm.state.init_history_years == 5 != OnboardingState() 默认 3).
    """
    monkeypatch.setattr(
        "ui.viewmodels.onboarding_view_model.ConfigHandler.get_init_history_years",
        staticmethod(lambda: 3),
    )
    monkeypatch.setattr(
        "ui.viewmodels.onboarding_view_model.ConfigHandler.get_auto_update_time",
        staticmethod(lambda: "16:30"),
    )
    # UX-09 / MAJOR-01: 隔离真实配置文件, 使默认 risk_acknowledged 恒为 False。
    monkeypatch.setattr(
        "ui.viewmodels.onboarding_view_model.ConfigHandler.is_risk_disclaimer_acknowledged",
        staticmethod(lambda: False),
    )
    return OnboardingViewModel()


@pytest.fixture
def snapshots():
    """List to collect OnboardingState snapshots from bound_vm."""
    return []


@pytest.fixture
def bound_vm(vm, snapshots):
    """ViewModel with fn_* callbacks and on_complete bound to MagicMocks/AsyncMocks.

    Phase 2 改造: on_* 通知回调移除,改用 state snapshot + subscribe。
    snapshots fixture 自动订阅 bound_vm 的 state 变化。
    """
    vm.bind(
        fn_validate_database=AsyncMock(return_value=True),
        fn_validate_token=AsyncMock(return_value=True),
        fn_validate_model_scenario=AsyncMock(return_value=True),
        fn_push_schedule_state=MagicMock(),
        on_complete=AsyncMock(),
    )
    vm.subscribe(lambda s: snapshots.append(s))
    return vm


@pytest.fixture
def mock_data_processor():
    with patch("ui.viewmodels.onboarding_view_model.DataProcessor") as cls:
        instance = MagicMock()
        instance.initialize_system = AsyncMock(return_value={"success": True})
        instance.stop = AsyncMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_config_handler():
    with patch("ui.viewmodels.onboarding_view_model.ConfigHandler") as cls:
        cls.save_config = MagicMock()
        yield cls


# =====================================================================
# Test: Init
# =====================================================================


class TestOnboardingVMInit:
    def test_default_initial_step(self, vm):
        assert vm.current_step == 0

    def test_default_step_validated_empty(self, vm):
        assert not vm.is_step_validated("database")

    def test_default_sync_in_progress_false(self, vm):
        assert vm.sync_in_progress is False

    def test_default_validation_in_progress_false(self, vm):
        assert vm.validation_in_progress is False

    def test_data_processor_lazy_init(self, vm):
        """B11: property 不再同步构造 DataProcessor（避免阻塞 UI 主线程 R16）。

        未触发懒构造时 data_processor 为 None；真实构造走 _ensure_processor()
        （IO 线程池 offload），见 test_ensure_processor_lazy_constructs。
        """
        assert vm._data_processor is None
        assert vm.data_processor is None

    @pytest.mark.asyncio
    async def test_ensure_processor_lazy_constructs(self, vm):
        """B11: _ensure_processor 首次调用经 IO 线程池构造 DataProcessor（双检+loop-local 锁）。"""
        assert vm._data_processor is None
        with (
            patch("ui.viewmodels.onboarding_view_model.DataProcessor") as cls,
            patch("ui.viewmodels.onboarding_view_model.ThreadPoolManager") as mock_tp,
        ):
            mock_instance = MagicMock()
            cls.return_value = mock_instance
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(return_value=mock_instance)
            mock_tp.return_value = tp_instance

            dp = await vm._ensure_processor()

        assert dp is mock_instance
        assert vm._data_processor is mock_instance
        tp_instance.run_async.assert_awaited_once_with(ANY, cls)

    @pytest.mark.asyncio
    async def test_ensure_processor_returns_existing_instance(self, vm):
        """已构造后再次调用直接返回现有实例，不重复经线程池构造（双检第二层）。"""
        with patch("ui.viewmodels.onboarding_view_model.DataProcessor") as cls:
            mock_instance = MagicMock()
            cls.return_value = mock_instance

            vm._data_processor = mock_instance  # 模拟已构造（含 DI 注入）

            dp = await vm._ensure_processor()

        assert dp is mock_instance
        cls.assert_not_called()

    def test_constructor_injection(self, mock_data_processor):
        vm = OnboardingViewModel(data_processor=mock_data_processor)
        assert vm._data_processor is mock_data_processor
        assert vm.data_processor is mock_data_processor

    def test_default_schedule_state(self, vm):
        assert vm._schedule_enabled is True
        assert vm._schedule_time == "16:30"

    def test_normalized_schedule_time_property(self, vm):
        assert vm.normalized_schedule_time == "16:30"


# =====================================================================
# Test: bind / dispose
# =====================================================================


class TestOnboardingVMBind:
    def test_bind_stores_callbacks(self, vm):
        cb = MagicMock()
        cb_async = AsyncMock()
        vm.bind(
            fn_validate_database=cb_async,
            fn_validate_token=cb_async,
            fn_validate_cloud_ai=cb_async,
            fn_validate_local_model=cb_async,
            fn_push_schedule_state=cb,
            on_complete=cb_async,
        )
        assert vm.fn_validate_database is cb_async
        assert vm.fn_push_schedule_state is cb
        assert vm.on_complete is cb_async

    def test_dispose_clears_callbacks(self, bound_vm):
        bound_vm.dispose()
        assert bound_vm.fn_validate_database is None
        assert bound_vm.fn_validate_token is None
        assert bound_vm.on_complete is None
        assert bound_vm.state == OnboardingState()

    def test_state_defaults(self, vm):
        assert vm.state == OnboardingState()

    def test_notifies_subscribers(self, vm):
        received: list = []
        unsub = vm.subscribe(lambda s: received.append(s))
        vm._set_state(current_step=3)
        assert len(received) == 1
        assert received[0].current_step == 3
        unsub()
        vm._set_state(current_step=5)
        assert len(received) == 1


# =====================================================================
# Test: Step Navigation
# =====================================================================


class TestOnboardingVMNavigation:
    async def test_next_step_advances(self, bound_vm, snapshots):
        # UX-09 / MAJOR-01: 欢迎步须先勾选风险提示确认才允许推进。
        bound_vm._set_state(risk_acknowledged=True)
        await bound_vm.next_step()
        assert bound_vm.current_step == 1
        assert bound_vm.state.current_step == 1
        assert any(s.current_step == 1 for s in snapshots)

    async def test_next_step_validates_required(self, bound_vm):
        bound_vm.current_step = 1  # database step
        bound_vm.validate_and_persist_current_step = AsyncMock(return_value=True)
        await bound_vm.next_step()
        bound_vm.validate_and_persist_current_step.assert_awaited_once()

    async def test_next_step_blocks_on_validation_failure(self, bound_vm):
        bound_vm.current_step = 1
        bound_vm.validate_and_persist_current_step = AsyncMock(return_value=False)
        await bound_vm.next_step()
        assert bound_vm.current_step == 1
        assert bound_vm.state.current_step == 1

    async def test_next_step_on_complete_calls_callback(self, bound_vm):
        bound_vm.current_step = 6  # complete step
        await bound_vm.next_step()
        bound_vm.on_complete.assert_awaited_once()

    async def test_next_step_does_not_exceed_max(self, bound_vm):
        bound_vm.current_step = 6
        # on_complete returns, doesn't advance
        await bound_vm.next_step()
        assert bound_vm.current_step == 6

    async def test_prev_step_goes_back(self, bound_vm, snapshots):
        bound_vm.current_step = 3
        await bound_vm.prev_step()
        assert bound_vm.current_step == 2
        assert bound_vm.state.current_step == 2
        assert any(s.current_step == 2 for s in snapshots)

    async def test_prev_step_does_not_go_below_zero(self, bound_vm):
        bound_vm.current_step = 0
        await bound_vm.prev_step()
        assert bound_vm.current_step == 0
        assert bound_vm.state.current_step == 0

    async def test_prev_step_resets_validation(self, bound_vm):
        bound_vm.current_step = 1  # database step validates_before_next
        bound_vm._step_validated["database"] = True
        await bound_vm.prev_step()
        assert not bound_vm.is_step_validated("database")

    async def test_skip_step_advances(self, bound_vm, snapshots):
        bound_vm.current_step = 4  # data_sync step
        await bound_vm.skip_step()
        assert bound_vm.current_step == 5
        assert bound_vm.state.current_step == 5
        assert any(s.current_step == 5 for s in snapshots)

    async def test_skip_step_from_model_scenario_advances(self, bound_vm, snapshots):
        """FR-UX-001 + S5 §11.6: 在 model_scenario 步骤执行 skip 命令可推进到 data_sync 步骤。"""
        bound_vm.current_step = 3  # model_scenario step
        await bound_vm.skip_step()
        assert bound_vm.current_step == 4
        assert bound_vm.state.current_step == 4
        assert any(s.current_step == 4 for s in snapshots)

    async def test_skip_step_does_not_exceed_max(self, bound_vm):
        bound_vm.current_step = 6
        await bound_vm.skip_step()
        assert bound_vm.current_step == 6

    def test_invalidate_step(self, bound_vm):
        bound_vm._step_validated["database"] = True
        bound_vm.invalidate_step("database")
        assert not bound_vm.is_step_validated("database")


# =====================================================================
# Test: Validation
# =====================================================================


class TestOnboardingVMValidation:
    async def test_validate_skips_if_already_validated(self, bound_vm):
        bound_vm.current_step = 1
        bound_vm._step_validated["database"] = True
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_database.assert_not_awaited()

    async def test_validate_returns_true_for_no_validator(self, bound_vm):
        bound_vm.current_step = 0  # welcome step
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True

    async def test_validate_calls_database_fn(self, bound_vm):
        bound_vm.current_step = 1
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_database.assert_awaited_once()
        assert bound_vm.is_step_validated("database") is True

    async def test_validate_calls_token_fn(self, bound_vm):
        bound_vm.current_step = 2
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_token.assert_awaited_once()
        assert bound_vm.is_step_validated("token") is True

    async def test_validate_calls_model_scenario_fn(self, bound_vm):
        bound_vm.current_step = 3  # model_scenario step (S5 §11.6 合并单步)
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_model_scenario.assert_awaited_once()
        assert bound_vm.is_step_validated("model_scenario") is True

    async def test_validate_database_failure(self, bound_vm):
        bound_vm.fn_validate_database = AsyncMock(return_value=False)
        bound_vm.current_step = 1
        result = await bound_vm.validate_and_persist_current_step()
        assert result is False
        assert not bound_vm.is_step_validated("database")

    async def test_validation_sets_validation_in_progress(self, bound_vm, snapshots):
        bound_vm.current_step = 1
        await bound_vm.validate_and_persist_current_step()
        assert bound_vm.validation_in_progress is False  # finally resets
        # validation_in_progress transitions: False→True, True→False
        transitions = 0
        prev = False
        for s in snapshots:
            if s.validation_in_progress != prev:
                transitions += 1
                prev = s.validation_in_progress
        assert transitions == 2

    async def test_validation_clears_progress_on_exception(self, bound_vm):
        bound_vm.fn_validate_database = AsyncMock(side_effect=RuntimeError("BOOM"))
        bound_vm.current_step = 1
        with pytest.raises(RuntimeError, match="BOOM"):
            await bound_vm.validate_and_persist_current_step()
        assert bound_vm.validation_in_progress is False


# =====================================================================
# Test: Schedule Validation
# =====================================================================


class TestOnboardingVMScheduleValidation:
    def _setup_schedule_vm(self, bound_vm, mock_ch):
        """Setup VM with mock push_state and schedule time/enabled."""
        bound_vm.current_step = 5  # schedule step
        bound_vm.set_schedule_state(enabled=True, time_str="16:30")
        return bound_vm

    async def test_valid_time(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        assert bound_vm.is_step_validated("schedule") is True
        assert bound_vm.state.normalized_schedule_time == "16:30"

    async def test_invalid_time_defaults(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=True, time_str="25:99")
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        assert bound_vm._schedule_time == "16:30"
        assert bound_vm.state.normalized_schedule_time == "16:30"

    async def test_empty_time_defaults(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=True, time_str="")
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        assert bound_vm._schedule_time == "16:30"
        assert bound_vm.state.normalized_schedule_time == "16:30"

    async def test_saves_to_config_handler(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=False, time_str="08:00")
        await bound_vm.validate_and_persist_current_step()
        mock_config_handler.save_config.assert_called_once_with(
            {
                "auto_update_enabled": False,
                "auto_update_time": "08:00",
            }
        )

    async def test_pushes_schedule_state_before_validation(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        await bound_vm.validate_and_persist_current_step()
        bound_vm.fn_push_schedule_state.assert_called_once()

    async def test_disabled_schedule(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=False, time_str="16:30")
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        mock_config_handler.save_config.assert_called_once_with(
            {
                "auto_update_enabled": False,
                "auto_update_time": "16:30",
            }
        )

    async def test_set_schedule_state(self, vm):
        vm.set_schedule_state(enabled=False, time_str="09:00")
        assert vm._schedule_enabled is False
        assert vm._schedule_time == "09:00"
        assert vm.normalized_schedule_time == "09:00"
        assert vm.state.schedule_enabled is False
        assert vm.state.schedule_time == "09:00"
        assert vm.state.normalized_schedule_time == "09:00"

    async def test_save_config_exception_returns_false(self, bound_vm, mock_config_handler):
        """ConfigHandler.save_config 抛异常时 _validate_and_save_schedule 返回 False.

        覆盖 except 分支 logger.error (L397-401), 异常被捕获不传播,
        step 不标记为 validated.
        """
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        mock_config_handler.save_config = MagicMock(side_effect=RuntimeError("disk full"))
        result = await bound_vm.validate_and_persist_current_step()
        assert result is False
        assert not bound_vm.is_step_validated("schedule")

    async def test_save_config_exception_blocks_next_step(self, bound_vm, mock_config_handler):
        """UIR-F02-001: 落盘异常时 next_step 返回 False 并停留 schedule 步。"""
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        mock_config_handler.save_config = MagicMock(side_effect=RuntimeError("disk full"))
        result = await bound_vm.next_step()
        assert result is False
        assert bound_vm.current_step == 5
        assert bound_vm.state.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False

    async def test_save_config_false_blocks_next_step(self, bound_vm, mock_config_handler):
        """UIR-F02-001: save_config 返回 False（不抛异常）时不得当作验证成功前进。

        ConfigHandler.save_config 在配置不可读 / AppConfig 校验失败 / 原子写失败时
        返回 False 而不抛异常；VM 必须检查布尔返回值，停留 schedule 步且不缓存验证。
        """
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        mock_config_handler.save_config = MagicMock(return_value=False)

        result = await bound_vm.next_step()

        assert result is False
        assert bound_vm.current_step == 5
        assert bound_vm.state.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False
        mock_config_handler.save_config.assert_called_once_with(
            {
                "auto_update_enabled": True,
                "auto_update_time": "16:30",
            }
        )

    async def test_save_config_true_advances_to_complete(self, bound_vm, mock_config_handler, snapshots):
        """UIR-F02-001: save_config 返回 True 时正常前进到 complete 步并缓存验证。"""
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        mock_config_handler.save_config = MagicMock(return_value=True)

        result = await bound_vm.next_step()

        assert result is True
        assert bound_vm.current_step == 6
        assert bound_vm.state.current_step == 6
        assert bound_vm.is_step_validated("schedule") is True
        assert any(s.current_step == 6 for s in snapshots)

    async def test_retry_after_failure_advances(self, bound_vm, mock_config_handler):
        """落盘失败后再次提交（重试）成功才前进：失败不得留下成功缓存。"""
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        mock_config_handler.save_config = MagicMock(return_value=False)

        assert await bound_vm.next_step() is False
        assert bound_vm.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False

        mock_config_handler.save_config = MagicMock(return_value=True)
        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6
        assert bound_vm.is_step_validated("schedule") is True

    async def test_invalid_time_normalized_payload_and_advances(self, bound_vm, mock_config_handler):
        """非法时间仍归一化为 16:30 落盘，并正常前进（归一化行为不变）。"""
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        mock_config_handler.save_config = MagicMock(return_value=True)
        bound_vm.set_schedule_state(enabled=True, time_str="25:99")

        result = await bound_vm.next_step()

        assert result is True
        assert bound_vm._schedule_time == "16:30"
        assert bound_vm.state.normalized_schedule_time == "16:30"
        assert bound_vm.current_step == 6
        mock_config_handler.save_config.assert_called_once_with(
            {
                "auto_update_enabled": True,
                "auto_update_time": "16:30",
            }
        )

    async def test_cancelled_error_propagates_and_stays_on_schedule(self, bound_vm):
        """R2: 落盘 await 被取消时 CancelledError 必须向外传播，不得当作失败吞没。"""
        bound_vm.current_step = 5
        bound_vm.set_schedule_state(enabled=True, time_str="16:30")
        with patch("ui.viewmodels.onboarding_view_model.ThreadPoolManager") as tp_cls:
            tp_cls.return_value.run_async = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await bound_vm.next_step()
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert bound_vm.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False
        assert bound_vm.validation_in_progress is False

    async def test_save_config_false_keeps_corrupt_config_untouched(self, bound_vm, tmp_path, monkeypatch):
        """UIR-F02-001: 真实 save_config 在配置不可读时返回 False，且不覆盖损坏现场。

        用临时配置文件模拟磁盘损坏：保存必须失败、schedule 步停留，文件字节内容不变；
        重试覆盖损坏配置现场被禁止。
        """
        import utils.config_handler as cfg

        config_file = tmp_path / "user_settings.json"
        corrupt_content = '{"auto_update_time": "08:00", BROKEN'
        config_file.write_text(corrupt_content, encoding="utf-8")
        monkeypatch.setattr(cfg, "CONFIG_FILE", str(config_file))

        bound_vm.current_step = 5
        bound_vm.set_schedule_state(enabled=True, time_str="09:15")

        result = await bound_vm.next_step()

        assert result is False
        assert bound_vm.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False
        assert config_file.read_text(encoding="utf-8") == corrupt_content


# =====================================================================
# Test: Schedule validation cache invalidation (UIR-F02-003)
# =====================================================================


class TestOnboardingVMScheduleCacheInvalidation:
    """UIR-F02-003: 完成页返回后修改计划草稿必须重新校验并落盘。

    缓存只允许覆盖「与最近一次成功落盘完全相同的草稿」；未改值时保持幂等（不重复写盘）。
    """

    @staticmethod
    def _bind_view_draft(bound_vm, draft: dict[str, object]) -> None:
        """模拟 View 注入的 fn_push_schedule_state：把当前局部草稿推给 VM。"""
        bound_vm.fn_push_schedule_state = MagicMock(
            side_effect=lambda: bound_vm.set_schedule_state(
                enabled=bool(draft["enabled"]),
                time_str=str(draft["time"]),
            )
        )

    def _setup(self, bound_vm, mock_config_handler, draft: dict[str, object]):
        bound_vm.current_step = 5  # schedule step
        mock_config_handler.save_config = MagicMock(return_value=True)
        self._bind_view_draft(bound_vm, draft)
        return bound_vm

    def _edit_draft(self, bound_vm, draft: dict[str, object], *, invalidate: bool, **changes: object) -> None:
        """按 View 语义修改草稿：更新局部草稿，并按需触发 on_change 缓存失效。"""
        draft.update(changes)
        if invalidate:
            bound_vm.invalidate_step("schedule")

    async def test_time_change_after_complete_return_resaves_latest_value(self, bound_vm, mock_config_handler):
        """完成页返回后（未触发 change 回调）改时间：仍按草稿快照差异重新落盘。"""
        draft: dict[str, object] = {"enabled": True, "time": "08:00"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6
        mock_config_handler.save_config.assert_called_once_with(
            {"auto_update_enabled": True, "auto_update_time": "08:00"}
        )

        await bound_vm.prev_step()
        assert bound_vm.current_step == 5
        assert bound_vm.is_step_validated("schedule") is True  # 返回不失效，缓存仍命中

        self._edit_draft(bound_vm, draft, invalidate=False, time="09:00")

        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6
        assert mock_config_handler.save_config.call_count == 2
        assert mock_config_handler.save_config.call_args.args[0] == {
            "auto_update_enabled": True,
            "auto_update_time": "09:00",
        }

    async def test_enabled_change_after_complete_return_resaves_latest_value(self, bound_vm, mock_config_handler):
        """只改开关（时间不变）也必须重新落盘，payload 反映最新开关值。"""
        draft: dict[str, object] = {"enabled": True, "time": "16:30"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6

        await bound_vm.prev_step()
        self._edit_draft(bound_vm, draft, invalidate=True, enabled=False)

        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6
        assert mock_config_handler.save_config.call_count == 2
        assert mock_config_handler.save_config.call_args.args[0] == {
            "auto_update_enabled": False,
            "auto_update_time": "16:30",
        }

    async def test_time_change_after_complete_return_resaves_when_cache_invalidated(
        self, bound_vm, mock_config_handler
    ):
        """View on_change 失效缓存后改时间：重新校验并落盘最新值。"""
        draft: dict[str, object] = {"enabled": True, "time": "08:00"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        await bound_vm.prev_step()
        self._edit_draft(bound_vm, draft, invalidate=True, time="09:00")
        assert bound_vm.is_step_validated("schedule") is False

        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6
        assert mock_config_handler.save_config.call_count == 2
        assert mock_config_handler.save_config.call_args.args[0] == {
            "auto_update_enabled": True,
            "auto_update_time": "09:00",
        }

    async def test_unchanged_draft_after_complete_return_skips_second_write(self, bound_vm, mock_config_handler):
        """未改值：缓存命中且草稿快照一致，不产生第二次写盘；草稿仍在缓存短路前推送。"""
        draft: dict[str, object] = {"enabled": True, "time": "08:00"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        await bound_vm.prev_step()
        assert await bound_vm.next_step() is True

        assert bound_vm.current_step == 6
        assert mock_config_handler.save_config.call_count == 1
        assert bound_vm.fn_push_schedule_state.call_count == 2
        assert bound_vm.is_step_validated("schedule") is True

    async def test_second_save_false_stays_on_schedule_and_not_validated(self, bound_vm, mock_config_handler):
        """第二次落盘返回 False：停留 schedule 步，不缓存成功。"""
        draft: dict[str, object] = {"enabled": True, "time": "08:00"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        await bound_vm.prev_step()
        self._edit_draft(bound_vm, draft, invalidate=True, time="09:00")
        mock_config_handler.save_config = MagicMock(return_value=False)

        assert await bound_vm.next_step() is False
        assert bound_vm.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False
        assert mock_config_handler.save_config.call_count == 1
        assert mock_config_handler.save_config.call_args.args[0] == {
            "auto_update_enabled": True,
            "auto_update_time": "09:00",
        }

    async def test_second_save_exception_stays_on_schedule_and_not_validated(self, bound_vm, mock_config_handler):
        """第二次落盘抛异常：停留 schedule 步，不缓存成功。"""
        draft: dict[str, object] = {"enabled": True, "time": "08:00"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        await bound_vm.prev_step()
        self._edit_draft(bound_vm, draft, invalidate=True, time="09:00")
        mock_config_handler.save_config = MagicMock(side_effect=RuntimeError("disk full"))

        assert await bound_vm.next_step() is False
        assert bound_vm.current_step == 5
        assert bound_vm.is_step_validated("schedule") is False

    async def test_failed_second_save_retry_writes_latest_draft(self, bound_vm, mock_config_handler):
        """第二次失败后重试：草稿快照未更新，重试必须再次写盘并前进。"""
        draft: dict[str, object] = {"enabled": True, "time": "08:00"}
        self._setup(bound_vm, mock_config_handler, draft)

        assert await bound_vm.next_step() is True
        await bound_vm.prev_step()
        self._edit_draft(bound_vm, draft, invalidate=True, time="09:00")
        mock_config_handler.save_config = MagicMock(return_value=False)
        assert await bound_vm.next_step() is False

        mock_config_handler.save_config = MagicMock(return_value=True)
        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 6
        assert mock_config_handler.save_config.call_count == 1
        assert mock_config_handler.save_config.call_args.args[0] == {
            "auto_update_enabled": True,
            "auto_update_time": "09:00",
        }

    def test_persisted_schedule_snapshot_not_shared_across_instances(self, vm, mock_config_handler):
        """缓存快照为实例私有：新向导实例既无验证缓存也无落盘快照（不跨实例泄漏）。"""
        assert vm._persisted_schedule is None
        vm.current_step = 5
        vm._persisted_schedule = (True, "08:00")
        vm._step_validated["schedule"] = True
        vm.dispose()

        fresh = OnboardingViewModel()
        assert fresh._persisted_schedule is None
        assert fresh.is_step_validated("schedule") is False


# =====================================================================
# Test: Language Save
# =====================================================================


class TestOnboardingVMLanguage:
    """save_language 异常路径覆盖 (L421-435 except 分支)."""

    async def test_save_language_exception_returns_false(self, vm, mock_config_handler):
        """ConfigHandler.set_locale 抛异常时返回 False (覆盖 L430-434 logger.error)."""
        mock_config_handler.set_locale = MagicMock(side_effect=RuntimeError("IO error"))
        result = await vm.save_language("zh_CN")
        assert result is False

    async def test_save_language_returns_false_when_set_locale_fails(self, vm, mock_config_handler):
        """ConfigHandler.set_locale 返回 False 时返回 False (L423-425 路径)."""
        mock_config_handler.set_locale = MagicMock(return_value=False)
        result = await vm.save_language("zh_CN")
        assert result is False

    async def test_save_language_success_returns_true(self, vm, mock_config_handler):
        """ConfigHandler.set_locale 返回 True 时返回 True (正常路径)."""
        mock_config_handler.set_locale = MagicMock(return_value=True)
        result = await vm.save_language("zh_CN")
        assert result is True


# =====================================================================
# Test: Risk disclaimer acknowledgement (UX-09 / MAJOR-01)
# =====================================================================


class TestOnboardingVMRiskDisclaimer:
    """欢迎步投资风险提示确认：持久化 + state 更新 + 未确认时阻断前进。"""

    async def test_update_acknowledged_success_updates_state_and_persists(self, vm, mock_config_handler):
        mock_config_handler.set_risk_disclaimer_acknowledged = MagicMock(return_value=True)
        result = await vm.update_risk_disclaimer_acknowledged(True)
        assert result is True
        assert vm.state.risk_acknowledged is True
        mock_config_handler.set_risk_disclaimer_acknowledged.assert_called_once_with(True)

    async def test_update_acknowledged_false_persists_false(self, vm, mock_config_handler):
        mock_config_handler.set_risk_disclaimer_acknowledged = MagicMock(return_value=True)
        await vm.update_risk_disclaimer_acknowledged(True)
        result = await vm.update_risk_disclaimer_acknowledged(False)
        assert result is True
        assert vm.state.risk_acknowledged is False

    async def test_update_acknowledged_returns_false_when_persist_fails(self, vm, mock_config_handler):
        """ConfigHandler.set_risk_disclaimer_acknowledged 返回 False → 方法返回 False。"""
        mock_config_handler.set_risk_disclaimer_acknowledged = MagicMock(return_value=False)
        result = await vm.update_risk_disclaimer_acknowledged(True)
        assert result is False

    async def test_update_acknowledged_exception_returns_false(self, vm, mock_config_handler):
        """持久化抛异常时返回 False 且不向外传播（R9 脱敏）。"""
        mock_config_handler.set_risk_disclaimer_acknowledged = MagicMock(side_effect=RuntimeError("IO error"))
        result = await vm.update_risk_disclaimer_acknowledged(True)
        assert result is False

    async def test_update_acknowledged_cancelled_reraises(self, vm):
        """R2: asyncio.CancelledError 必须向外传播, 不得被 except Exception 吞没。

        直接令 IO offload 的 await 抛 CancelledError, 精确覆盖 VM 的 try/except 分支
        （不依赖 run_in_executor 对 worker 异常的包装语义）。
        """
        import asyncio

        with patch("ui.viewmodels.onboarding_view_model.ThreadPoolManager") as tp_cls:
            tp_cls.return_value.run_async = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await vm.update_risk_disclaimer_acknowledged(True)
        assert isinstance(exc_info.value, asyncio.CancelledError)
        # 先即时更新 state 再持久化：取消发生在持久化阶段时勾选态仍已生效
        # （取消只中断落盘，不吞没也不回滚）。
        assert vm.state.risk_acknowledged is True

    async def test_next_step_blocks_welcome_without_ack(self, bound_vm):
        """欢迎步未勾选风险提示确认时不前进（step 0 守卫）。"""
        assert bound_vm.current_step == 0
        assert bound_vm.state.risk_acknowledged is False
        await bound_vm.next_step()
        assert bound_vm.current_step == 0

    async def test_next_step_advances_welcome_when_acknowledged(self, bound_vm):
        """欢迎步勾选确认后 next_step 正常推进到数据库步。"""
        with patch(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.set_risk_disclaimer_acknowledged",
            return_value=True,
        ):
            await bound_vm.update_risk_disclaimer_acknowledged(True)
        assert bound_vm.state.risk_acknowledged is True
        await bound_vm.next_step()
        assert bound_vm.current_step == 1

    def test_load_config_reads_risk_acknowledged(self, monkeypatch):
        """_load_config_to_state 读取已确认状态（老用户升级后不再强制重勾）。"""
        monkeypatch.setattr(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.get_init_history_years",
            staticmethod(lambda: 3),
        )
        monkeypatch.setattr(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.get_auto_update_time",
            staticmethod(lambda: "16:30"),
        )
        monkeypatch.setattr(
            "ui.viewmodels.onboarding_view_model.ConfigHandler.is_risk_disclaimer_acknowledged",
            staticmethod(lambda: True),
        )
        assert OnboardingViewModel().state.risk_acknowledged is True


# =====================================================================
# Test: Data Sync
# =====================================================================


class TestOnboardingVMSync:
    @pytest.fixture
    def sync_vm(self, bound_vm, mock_data_processor, mock_config_handler):
        bound_vm._data_processor = mock_data_processor
        return bound_vm

    async def test_start_sync_sets_state(self, sync_vm, snapshots):
        await sync_vm.start_sync(quick=True)
        assert sync_vm.sync_in_progress is False  # finally resets
        # sync_in_progress transitions: False→True (start), True→False (finally)
        transitions = _count_transitions(snapshots, lambda s: s.sync_in_progress, initial=False)
        assert transitions == 2

    async def test_start_sync_calls_initialize_system(self, sync_vm, mock_data_processor):
        await sync_vm.start_sync(quick=True)
        mock_data_processor.initialize_system.assert_awaited_once()
        call_kwargs = mock_data_processor.initialize_system.call_args[1]
        assert call_kwargs["quick"] is True

    async def test_start_sync_full(self, sync_vm, mock_data_processor):
        await sync_vm.start_sync(quick=False)
        call_kwargs = mock_data_processor.initialize_system.call_args[1]
        assert call_kwargs["quick"] is False

    async def test_start_sync_success_advances(self, sync_vm, mock_data_processor, snapshots):
        sync_vm.current_step = 4  # data_sync step
        with patch("ui.viewmodels.onboarding_view_model.asyncio.sleep", new_callable=AsyncMock):
            await sync_vm.start_sync(quick=True)
        # UIR-F02-002: 同步成功走专用完成过渡，只推进 data_sync(4) → schedule(5)。
        assert sync_vm.current_step == 5
        sync_vm.on_complete.assert_not_awaited()
        assert any(
            s.sync_progress == 1.0
            and s.sync_progress_message is not None
            and s.sync_progress_message.key == "wizard_status_done"
            for s in snapshots
        )

    async def test_start_sync_success_no_double_end_notification(self, sync_vm, mock_data_processor, snapshots):
        """sync_in_progress must transition exactly twice on success: True at start, False in finally.
        Before the fix, it was 3 transitions (start + manual end + finally end)."""
        sync_vm.current_step = 4
        with patch("ui.viewmodels.onboarding_view_model.asyncio.sleep", new_callable=AsyncMock):
            await sync_vm.start_sync(quick=True)
        assert sync_vm.current_step == 5
        # 2 transitions: False→True at start, True→False in finally
        transitions = _count_transitions(snapshots, lambda s: s.sync_in_progress, initial=False)
        assert transitions == 2

    async def test_start_sync_cancelled_result(self, sync_vm, mock_data_processor, snapshots):
        mock_data_processor.initialize_system = AsyncMock(return_value=False)
        await sync_vm.start_sync(quick=True)
        assert sync_vm.current_step == 0
        assert any(
            s.sync_progress == 0.0
            and s.sync_progress_message is not None
            and s.sync_progress_message.key == "wizard_status_cancelled"
            for s in snapshots
        )

    async def test_start_sync_exception(self, sync_vm, mock_data_processor, snapshots):
        mock_data_processor.initialize_system = AsyncMock(side_effect=RuntimeError("sync failed"))
        with (
            patch(
                "ui.viewmodels.onboarding_view_model.classify_error",
                return_value={"message_key": "common_err_unknown", "format_args": {}},
            ),
        ):
            await sync_vm.start_sync(quick=True)
        assert sync_vm.sync_in_progress is False
        # sync_in_progress transitioned to True at start, then to False in finally
        assert any(s.sync_in_progress is True for s in snapshots)

    async def test_start_sync_progress_callback(self, sync_vm, mock_data_processor, snapshots):
        await sync_vm.start_sync(quick=True)
        call_kwargs = mock_data_processor.initialize_system.call_args[1]
        assert "progress_callback" in call_kwargs
        cb = call_kwargs["progress_callback"]
        cb(75, 100, "Three quarters")
        assert any(
            s.sync_progress == 0.75
            and s.sync_progress_message is not None
            and s.sync_progress_message.key == "wizard_status_syncing"
            for s in snapshots
        )

    async def test_cancel_sync(self, sync_vm, mock_data_processor):
        await sync_vm.cancel_sync()
        mock_data_processor.stop.assert_awaited_once()
        assert sync_vm.sync_in_progress is False

    async def test_cancel_sync_no_processor(self, sync_vm):
        sync_vm._data_processor = None
        await sync_vm.cancel_sync()
        assert sync_vm.sync_in_progress is False

    async def test_cancel_sync_exception(self, sync_vm, mock_data_processor, snapshots):
        mock_data_processor.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        await sync_vm.cancel_sync()
        assert sync_vm.sync_in_progress is False
        # cancel_sync 在 finally 中调用 _set_state(sync_in_progress=False),产生 snapshot
        assert len(snapshots) >= 1

    async def test_skip_sync(self, sync_vm, snapshots):
        sync_vm.next_step = AsyncMock()
        await sync_vm.skip_sync()
        assert any(
            s.sync_progress == 0.0
            and s.sync_progress_message is not None
            and s.sync_progress_message.key == "wizard_status_skip"
            for s in snapshots
        )
        sync_vm.next_step.assert_awaited_once()

    async def test_start_sync_quick_check_progress_init(self, sync_vm, snapshots):
        await sync_vm.start_sync(quick=True)
        assert any(
            s.sync_progress == 0.0
            and s.sync_progress_message is not None
            and s.sync_progress_message.key == "wizard_status_init"
            for s in snapshots
        )


# =====================================================================
# Test: Sync step ownership (UIR-F02-002)
# =====================================================================

_SLEEP_PATCH = "ui.viewmodels.onboarding_view_model.asyncio.sleep"


class _ControlledSync:
    """确定性同步闸门：每次 initialize_system 用独立 started/released Event 控制时序。

    不使用 sleep 拼时序：测试显式等待 started，再显式释放 release，由事件循环驱动。
    """

    def __init__(self, runs: int) -> None:
        self.started = [asyncio.Event() for _ in range(runs)]
        self.release = [asyncio.Event() for _ in range(runs)]
        self.calls = 0
        self.stop = AsyncMock()

    async def initialize_system(self, **kwargs) -> bool:
        index = self.calls
        self.calls += 1
        self.started[index].set()
        await self.release[index].wait()
        return True


class TestOnboardingVMSyncOwnership:
    """UIR-F02-002: 同步进行中留在本步；同步成功仅由仍占有 data_sync 步的运行推进。"""

    @staticmethod
    def _install_gate(bound_vm, runs: int = 1) -> _ControlledSync:
        gate = _ControlledSync(runs)
        bound_vm._data_processor = gate
        bound_vm.current_step = 4  # data_sync step
        return gate

    async def test_manual_next_step_blocked_while_sync_in_progress(self, bound_vm, mock_config_handler):
        """a) 同步在途时手动 next_step 必须被拒：仍为 4 且不落盘计划。"""
        gate = self._install_gate(bound_vm)
        task = asyncio.create_task(bound_vm.start_sync(quick=True))
        try:
            await gate.started[0].wait()
            assert bound_vm.sync_in_progress is True

            assert await bound_vm.next_step() is False
            assert bound_vm.current_step == 4
            assert bound_vm.state.current_step == 4
            mock_config_handler.save_config.assert_not_called()
        finally:
            with patch(_SLEEP_PATCH, new_callable=AsyncMock):
                gate.release[0].set()
                await task

    async def test_skip_sync_blocked_while_sync_in_progress(self, bound_vm, mock_config_handler):
        """同步中「稍后」不得绕过守卫换步（按钮同时 disabled）。"""
        gate = self._install_gate(bound_vm)
        task = asyncio.create_task(bound_vm.start_sync(quick=True))
        try:
            await gate.started[0].wait()

            await bound_vm.skip_sync()

            assert bound_vm.current_step == 4
            assert bound_vm.state.sync_progress_message is not None
            assert bound_vm.state.sync_progress_message.key != "wizard_status_skip"
            mock_config_handler.save_config.assert_not_called()
        finally:
            with patch(_SLEEP_PATCH, new_callable=AsyncMock):
                gate.release[0].set()
                await task

    async def test_success_advances_only_to_schedule(self, bound_vm, mock_config_handler):
        """b) 释放同步成功：只到 5（schedule），不得跳到 6 或触发 on_complete。"""
        gate = self._install_gate(bound_vm)
        task = asyncio.create_task(bound_vm.start_sync(quick=True))
        await gate.started[0].wait()

        with patch(_SLEEP_PATCH, new_callable=AsyncMock):
            gate.release[0].set()
            await task

        assert bound_vm.current_step == 5
        assert bound_vm.state.current_step == 5
        assert bound_vm.sync_in_progress is False
        bound_vm.on_complete.assert_not_awaited()
        mock_config_handler.save_config.assert_not_called()

    async def test_stale_completion_ignored_after_step_change(self, bound_vm, mock_config_handler):
        """c) 同步成功前用户已换步：旧结果不提交计划、不进入 6。"""
        gate = self._install_gate(bound_vm)
        task = asyncio.create_task(bound_vm.start_sync(quick=True))
        await gate.started[0].wait()

        bound_vm.current_step = 5  # 用户已离开 data_sync 步

        with patch(_SLEEP_PATCH, new_callable=AsyncMock):
            gate.release[0].set()
            await task

        assert bound_vm.current_step == 5
        assert bound_vm.state.current_step == 5
        bound_vm.on_complete.assert_not_awaited()
        mock_config_handler.save_config.assert_not_called()

    async def test_completion_ignored_after_cancel(self, bound_vm, mock_config_handler):
        """d) 取消同步后旧完成结果到达：不跳转、不留忙碌标记。"""
        gate = self._install_gate(bound_vm)
        task = asyncio.create_task(bound_vm.start_sync(quick=True))
        await gate.started[0].wait()

        await bound_vm.cancel_sync()
        assert bound_vm.sync_in_progress is False

        with patch(_SLEEP_PATCH, new_callable=AsyncMock):
            gate.release[0].set()
            await task

        assert bound_vm.current_step == 4
        assert bound_vm.state.current_step == 4
        assert bound_vm.sync_in_progress is False
        bound_vm.on_complete.assert_not_awaited()

    async def test_older_generation_completion_ignored(self, bound_vm, mock_config_handler):
        """e) 连续两次同步：旧 generation 的完成结果不生效，也不清除新运行的忙碌标记。"""
        gate = self._install_gate(bound_vm, runs=2)
        first = asyncio.create_task(bound_vm.start_sync(quick=True))
        await gate.started[0].wait()
        second = asyncio.create_task(bound_vm.start_sync(quick=True))
        await gate.started[1].wait()

        with patch(_SLEEP_PATCH, new_callable=AsyncMock):
            gate.release[0].set()
            await first

            assert bound_vm.current_step == 4
            assert bound_vm.state.current_step == 4
            assert bound_vm.sync_in_progress is True

            gate.release[1].set()
            await second

        assert bound_vm.current_step == 5
        assert bound_vm.state.current_step == 5
        assert bound_vm.sync_in_progress is False
        bound_vm.on_complete.assert_not_awaited()

    async def test_completion_after_dispose_does_not_navigate(self, bound_vm, mock_config_handler):
        """卸载（dispose）后迟到结果不得导航（state 已重置，回调已清空）。"""
        gate = self._install_gate(bound_vm)
        task = asyncio.create_task(bound_vm.start_sync(quick=True))
        await gate.started[0].wait()

        bound_vm.dispose()

        with patch(_SLEEP_PATCH, new_callable=AsyncMock):
            gate.release[0].set()
            await task

        assert bound_vm.current_step == 0
        assert bound_vm.on_complete is None
        mock_config_handler.save_config.assert_not_called()

    async def test_manual_skip_and_next_still_work_when_not_syncing(self, bound_vm):
        """f) 未开始同步时「稍后 / 下一步」仍可跳过 data_sync 步。"""
        bound_vm.current_step = 4
        await bound_vm.skip_sync()
        assert bound_vm.current_step == 5
        assert bound_vm.state.sync_progress_message is not None
        assert bound_vm.state.sync_progress_message.key == "wizard_status_skip"

        bound_vm.current_step = 4
        assert await bound_vm.next_step() is True
        assert bound_vm.current_step == 5
        bound_vm.on_complete.assert_not_awaited()


# =====================================================================
# Test: Service Delegation
# =====================================================================


class TestOnboardingVMServiceDelegation:
    async def test_test_llm_connection(self):
        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": True})
            result = await OnboardingViewModel.test_llm_connection(
                provider="deepseek",
                model="chat",
                base_url="https://x",
                api_key="k",
            )
            mock_ai.test_connection.assert_awaited_once_with(
                provider="deepseek",
                model="chat",
                base_url="https://x",
                api_key="k",
            )
            assert result == {"success": True}

    async def test_test_llm_connection_with_kwargs(self):
        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": True})
            result = await OnboardingViewModel.test_llm_connection(
                provider="azure",
                model="gpt4",
                base_url="https://az",
                api_key="k",
                azure_resource_name="res",
            )
            assert result == {"success": True}
            call_kwargs = mock_ai.test_connection.call_args[1]
            assert call_kwargs["azure_resource_name"] == "res"

    async def test_verify_local_model(self):
        with patch("services.local_model_manager.LocalModelManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.load_model = AsyncMock(return_value=True)
            mock_mgr_cls.get_instance = AsyncMock(return_value=mock_mgr)
            result = await OnboardingViewModel.verify_local_model("/path/model.gguf", {"ctx": 2048})
            assert result is True
            mock_mgr.load_model.assert_awaited_once_with("/path/model.gguf", {"ctx": 2048}, is_verification=True)


# =====================================================================
# Test: validation_in_progress state transitions
# =====================================================================


class TestOnboardingVMValidationState:
    async def test_set_validation_in_progress_notifies(self, bound_vm, snapshots):
        bound_vm._set_validation_in_progress(True)
        assert bound_vm.validation_in_progress is True
        # validation_in_progress transitioned from False to True (1 transition)
        transitions = _count_transitions(snapshots, lambda s: s.validation_in_progress, initial=False)
        assert transitions == 1
        assert snapshots[-1].validation_in_progress is True

    async def test_clear_validation_in_progress_notifies(self, bound_vm, snapshots):
        bound_vm._set_validation_in_progress(False)
        assert bound_vm.validation_in_progress is False
        # _set_state fires _notify even when value is unchanged (False→False, no transition)
        # 但订阅者仍然收到通知
        assert len(snapshots) == 1
        assert snapshots[-1].validation_in_progress is False


# =====================================================================
# Test: Unbound validator blocks step (P2 fix)
# =====================================================================


class TestOnboardingVMUnboundValidator:
    async def test_unbound_database_validator_blocks(self, vm):
        """fn_validate_database=None must block the database step."""
        vm.current_step = 1  # database
        result = await vm.validate_and_persist_current_step()
        assert result is False

    async def test_unbound_token_validator_blocks(self, vm):
        vm.current_step = 2  # token
        result = await vm.validate_and_persist_current_step()
        assert result is False

    async def test_unbound_model_scenario_validator_passes(self, vm):
        """FR-UX-001 + S5 §11.6: model_scenario is optional (block_on_missing_validator=False); 未绑定 validator 允许通过."""
        vm.current_step = 3  # model_scenario
        result = await vm.validate_and_persist_current_step()
        assert result is True

    async def test_dispose_then_validate_blocks(self, bound_vm):
        """After dispose(), injected validators are None → must block."""
        bound_vm.dispose()
        bound_vm.current_step = 1
        result = await bound_vm.validate_and_persist_current_step()
        assert result is False

    async def test_welcome_step_passes_without_validator(self, vm):
        """Welcome step has no validator and should still pass."""
        vm.current_step = 0
        result = await vm.validate_and_persist_current_step()
        assert result is True

    async def test_data_sync_step_passes_without_validator(self, vm):
        """data_sync step has no validator and should still pass."""
        vm.current_step = 4
        result = await vm.validate_and_persist_current_step()
        assert result is True


class TestStepConfigContract:
    """StepConfig 数据结构与 STEP_CONFIGS 常量契约（合并自 tests/unit/test_onboarding_wizard.py）。"""

    def test_step_config_defaults(self):
        config = StepConfig(
            id="test",
            name="test_name",
            show_prev=True,
            show_next=True,
            next_text_key="next",
            next_icon="arrow_forward",
        )
        assert config.show_skip is False
        assert config.skip_text_key == ""
        assert config.required is False
        assert config.validate_before_next is False

    def test_step_config_all_fields(self):
        config = StepConfig(
            id="test",
            name="test_name",
            show_prev=True,
            show_next=True,
            next_text_key="next",
            next_icon="arrow_forward",
            show_skip=True,
            skip_text_key="skip",
            required=True,
            validate_before_next=True,
        )
        assert config.id == "test"
        assert config.required is True
        assert config.validate_before_next is True
        assert config.show_skip is True

    def test_step_configs_ids(self):
        expected_ids = [
            "welcome",
            "database",
            "token",
            "model_scenario",
            "data_sync",
            "schedule",
            "complete",
        ]
        actual_ids = [config.id for config in STEP_CONFIGS]
        assert actual_ids == expected_ids

    def test_required_steps(self):
        # FR-UX-001 + S5 §11.6: model_scenario 可选化（required=False），必填步骤仅剩 database/token
        required_steps = [config.id for config in STEP_CONFIGS if config.required]
        assert required_steps == ["database", "token"]

    def test_validate_before_next_steps(self):
        validate_steps = [config.id for config in STEP_CONFIGS if config.validate_before_next]
        assert validate_steps == [
            "database",
            "token",
            "model_scenario",
            "schedule",
        ]

    def test_step_configs_structure(self):
        # 首步是 welcome
        assert STEP_CONFIGS[0].id == "welcome"
        # 含 database 步
        assert any(c.id == "database" for c in STEP_CONFIGS)
        # 末步是 complete
        assert STEP_CONFIGS[-1].id == "complete"

    def test_welcome_step_no_prev(self):
        assert STEP_CONFIGS[0].show_prev is False

    def test_welcome_step_has_next(self):
        assert STEP_CONFIGS[0].show_next is True

    def test_data_sync_step_has_no_skip(self):
        """S5 §11.6: 跳过按钮移至 model_scenario 单步；data_sync 步不再显示跳过。"""
        assert STEP_CONFIGS[4].show_skip is False

    def test_model_scenario_step_has_skip(self):
        """FR-UX-001 + S5 §11.6: model_scenario 步骤显示跳过按钮，且非必填，未绑定 validator 时不阻断。"""
        config = STEP_CONFIGS[3]
        assert config.id == "model_scenario"
        assert config.show_skip is True
        assert config.skip_text_key == "wizard_btn_skip"
        assert config.required is False
        assert config.block_on_missing_validator is False


# =====================================================================
# Test: OnboardingWizard Module Contract (merged from test_onboarding_wizard.py)
# =====================================================================


class TestOnboardingWizardModuleContract:
    """onboarding_wizard.py 模块契约测试（合并自 tests/unit/ui/test_onboarding_wizard.py）。"""

    def test_set_onboarding_complete_not_in_onboarding_wizard_module(self):
        """set_onboarding_complete 不应在 onboarding_wizard.py 中调用，仅 main.py 调用。"""
        from pathlib import Path

        wizard_path = Path(__file__).resolve().parents[3] / "ui" / "views" / "onboarding_wizard.py"
        content = wizard_path.read_text(encoding="utf-8")
        assert "set_onboarding_complete" not in content, (
            "set_onboarding_complete should NOT be called in onboarding_wizard.py - "
            "it should only be called in main.py after service initialization"
        )
