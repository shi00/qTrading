"""Task 10：研究会话模型选择器（会话级、真正生效）单测。

覆盖要点：
- 三种来源标签（会话键 / 角色覆盖 / 路由默认）；
- 会话键写入/读取/清除的成功路径与失败路径（失败显式报错、不回退、不改选中值，R21）；
- ``CancelledError`` 原样传播（R2）；
- 首问提交把会话级选择传入受理端口（None = 继承，不写键）；
- 会话头选择条的渲染（来源文案、继承文案、未接线提示）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import flet as ft
import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, Mode
from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import (
    AGENT_RESEARCH_SCENARIO_ID,
    SESSION_MODEL_SOURCE_DEFAULT,
    SESSION_MODEL_SOURCE_ROLE,
    SESSION_MODEL_SOURCE_SESSION,
    SESSION_MODEL_UNAVAILABLE_KEY,
    ModelConnectionOption,
    ResearchObserverState,
    ResearchObserverViewModel,
)
from ui.views.research_observer_conversation import build_session_model_bar

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------


def _session_read() -> Any:
    """最小会话首读：仅提供 ``_apply_session_read`` 需要的字段。"""

    return SimpleNamespace(
        summary=SimpleNamespace(
            title="研究 A",
            updated_at="2026-11-26T02:00:00.000000Z",
            draft_revision=0,
            cleaned_at=None,
            revision=1,
        ),
        executions=(),
        messages=SimpleNamespace(items=(), has_more=False, next_cursor=None),
        artifacts=SimpleNamespace(items=(), has_more=False, next_cursor=None),
        decisions=(),
    )


class _Service:
    """最小 facade 替身：只实现会话首读。"""

    def __init__(self) -> None:
        self.read_queries: list[Any] = []

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        self.read_queries.append(query)
        return _session_read()

    async def search(self, query: Any, *, user_scope: str) -> Any:
        return SimpleNamespace(hits=(), read_moment="2026-11-26T02:00:00.000000Z")


class _Store:
    """会话模型存储替身：精确记录读写参数，并支持故障注入（R2/R21 用例）。"""

    def __init__(
        self,
        value: str | None = None,
        *,
        read_error: Exception | None = None,
        write_error: Exception | None = None,
        clear_error: Exception | None = None,
    ) -> None:
        self.value = value
        self.read_error = read_error
        self.write_error = write_error
        self.clear_error = clear_error
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.clears: list[str] = []

    async def read(self, session_id: str) -> str | None:
        self.reads.append(session_id)
        if self.read_error is not None:
            raise self.read_error
        return self.value

    async def write(self, session_id: str, connection_id: str) -> None:
        self.writes.append((session_id, connection_id))
        if self.write_error is not None:
            raise self.write_error
        self.value = connection_id

    async def clear(self, session_id: str) -> bool:
        self.clears.append(session_id)
        if self.clear_error is not None:
            raise self.clear_error
        self.value = None
        return True


def _connection(connection_id: str, *, name: str, model: str = "gpt-4o", enabled: bool = True) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "name": name,
        "model": model,
        "provider": "openai",
        "enabled": enabled,
        "context_window": 128000,
    }


def _snapshot() -> Any:
    return SimpleNamespace(
        connections=(
            _connection("conn-a", name="连接 A"),
            _connection("conn-b", name="连接 B", model="gpt-4.1"),
            _connection("conn-off", name="连接 C", model="gpt-4.1", enabled=False),
        ),
        scenario_routes=(
            {
                "scenario_id": AGENT_RESEARCH_SCENARIO_ID,
                "enabled": True,
                "candidates": ["conn-a", "conn-b", "conn-off"],
            },
        ),
    )


class _Catalog:
    """研究场景连接目录替身（固定快照）。"""

    async def get_snapshot(self) -> Any:
        return _snapshot()


def _token_counter(_text: str, _model: str | None) -> tuple[int, bool]:
    return (1, True)


def _make_vm(
    *,
    store: _Store | None = None,
    resolver: Any = None,
    intake: Any = None,
    catalog: Any = None,
    service: _Service | None = None,
) -> ResearchObserverViewModel:
    return ResearchObserverViewModel(
        cast(Any, service if service is not None else _Service()),
        intake=intake,
        model_catalog=catalog if catalog is not None else cast(Any, _Catalog()),
        token_counter=_token_counter,
        session_models=cast(Any, store),
        role_model_resolver=resolver,
    )


async def _vm_on_session(store: _Store | None = None, **kwargs: Any) -> ResearchObserverViewModel:
    """构造并导航到会话 ``s1``（refresh 会触发一次会话模型读取）。"""

    vm = _make_vm(store=store, **kwargs)
    await vm.refresh(session_id="s1")
    return vm


class _RecordingIntake:
    """记录首问参数的受理端口替身（精确断言全部关键字参数）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def start_question(
        self,
        text: str,
        *,
        mode: Any,
        session_id: str | None = None,
        expected_revision: int | None = None,
        role_prompt_refs: Any = (),
        mcp_selection: Any = (),
        model_connection_id: str | None = None,
    ) -> Any:
        self.calls.append(
            {
                "text": text,
                "mode": mode,
                "session_id": session_id,
                "expected_revision": expected_revision,
                "role_prompt_refs": tuple(role_prompt_refs),
                "mcp_selection": tuple(mcp_selection),
                "model_connection_id": model_connection_id,
            }
        )
        return SimpleNamespace(main_turn=None, dispatch=None, plan_reference=None)


# ----------------------------------------------------------------------
# VM：来源标签
# ----------------------------------------------------------------------


class TestSessionModelSource:
    @pytest.mark.asyncio
    async def test_session_key_hit_marks_session_source(self) -> None:
        store = _Store("conn-a")

        vm = await _vm_on_session(store)
        await vm.load_session_model("s1")

        assert vm.state.session_model_connection_id == "conn-a"
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_SESSION
        assert vm.state.session_model_error_key is None

    @pytest.mark.asyncio
    async def test_missing_key_with_role_connection_marks_role_source(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store, resolver=lambda role_key: "conn-b" if role_key == "main" else None)
        await vm.load_model_selection()

        await vm.load_session_model("s1")

        assert vm.state.role_selection == "main"
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_ROLE

    @pytest.mark.asyncio
    async def test_load_model_selection_recomputes_role_source_when_no_session_key(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store, resolver=lambda role_key: "conn-b" if role_key == "main" else None)
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_DEFAULT

        await vm.load_model_selection()

        assert vm.state.role_selection == "main"
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_ROLE

    @pytest.mark.asyncio
    async def test_missing_key_without_resolver_marks_default_source(self) -> None:
        vm = await _vm_on_session(_Store(None))
        await vm.load_model_selection()

        await vm.load_session_model("s1")

        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_DEFAULT

    @pytest.mark.asyncio
    async def test_store_not_injected_is_default_source_without_error(self) -> None:
        vm = await _vm_on_session(None)

        assert vm.state.session_model_available is False
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_DEFAULT
        assert vm.state.session_model_error_key is None


# ----------------------------------------------------------------------
# VM：选择命令
# ----------------------------------------------------------------------


class TestSelectSessionModel:
    @pytest.mark.asyncio
    async def test_select_writes_session_key_once_and_updates_state(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store)
        await vm.load_model_selection()

        await vm.select_session_model("conn-a")

        assert store.writes == [("s1", "conn-a")]
        assert vm.state.session_model_connection_id == "conn-a"
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_SESSION
        assert vm.state.session_model_error_key is None

    @pytest.mark.asyncio
    async def test_unknown_connection_is_rejected_without_write(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store)
        await vm.load_model_selection()

        await vm.select_session_model("conn-x")

        assert store.writes == []
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_error_key == "agent_research_model_selection_unknown"

    @pytest.mark.asyncio
    async def test_disabled_connection_is_rejected_with_its_reason(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store)
        await vm.load_model_selection()

        await vm.select_session_model("conn-off")

        assert store.writes == []
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_error_key == "agent_research_model_selection_disabled"

    @pytest.mark.asyncio
    async def test_select_without_store_reports_unavailable_without_raising(self) -> None:
        vm = await _vm_on_session(None)
        await vm.load_model_selection()

        await vm.select_session_model("conn-a")

        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_error_key == SESSION_MODEL_UNAVAILABLE_KEY

    @pytest.mark.asyncio
    async def test_write_failure_reports_unavailable_and_keeps_selection(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store)
        await vm.load_model_selection()
        store.write_error = RuntimeError("boom")

        await vm.select_session_model("conn-a")

        assert store.writes == [("s1", "conn-a")]
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_error_key == SESSION_MODEL_UNAVAILABLE_KEY

    @pytest.mark.asyncio
    async def test_write_cancellation_propagates(self) -> None:
        store = _Store(None, write_error=asyncio.CancelledError())
        vm = await _vm_on_session(store)
        await vm.load_model_selection()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            assert vm.state.session_model_connection_id is None
            await vm.select_session_model("conn-a")

        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert vm.state.session_model_connection_id is None


# ----------------------------------------------------------------------
# VM：读取与清除
# ----------------------------------------------------------------------


class TestLoadAndClearSessionModel:
    @pytest.mark.asyncio
    async def test_read_failure_keeps_existing_connection_and_reports_error(self) -> None:
        store = _Store("conn-a")
        vm = await _vm_on_session(store)
        assert vm.state.session_model_connection_id == "conn-a"
        store.read_error = RuntimeError("boom")

        await vm.load_session_model("s1")

        assert vm.state.session_model_connection_id == "conn-a"
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_SESSION
        assert vm.state.session_model_error_key == SESSION_MODEL_UNAVAILABLE_KEY

    @pytest.mark.asyncio
    async def test_read_cancellation_propagates(self) -> None:
        store = _Store(None)
        vm = await _vm_on_session(store)
        store.read_error = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            assert vm.state.session_model_error_key is None
            await vm.load_session_model("s1")

        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_error_key is None

    @pytest.mark.asyncio
    async def test_clear_falls_back_to_role_source(self) -> None:
        store = _Store("conn-a")
        vm = await _vm_on_session(store, resolver=lambda role_key: "conn-b" if role_key == "main" else None)
        await vm.load_model_selection()

        await vm.clear_session_model()

        assert store.clears == ["s1"]
        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_ROLE
        assert vm.state.session_model_error_key is None

    @pytest.mark.asyncio
    async def test_clear_without_role_connection_falls_back_to_default(self) -> None:
        store = _Store("conn-a")
        vm = await _vm_on_session(store)
        await vm.load_model_selection()

        await vm.clear_session_model()

        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_DEFAULT

    @pytest.mark.asyncio
    async def test_clear_failure_keeps_selection_and_reports_error(self) -> None:
        store = _Store("conn-a")
        vm = await _vm_on_session(store)
        store.clear_error = RuntimeError("boom")

        await vm.clear_session_model()

        assert store.clears == ["s1"]
        assert vm.state.session_model_connection_id == "conn-a"
        assert vm.state.session_model_error_key == SESSION_MODEL_UNAVAILABLE_KEY

    @pytest.mark.asyncio
    async def test_clear_without_store_reports_unavailable(self) -> None:
        vm = await _vm_on_session(None)

        await vm.clear_session_model()

        assert vm.state.session_model_error_key == SESSION_MODEL_UNAVAILABLE_KEY

    @pytest.mark.asyncio
    async def test_no_session_refresh_resets_session_model_state(self) -> None:
        store = _Store("conn-a")
        vm = _make_vm(store=store)
        await vm.load_session_model("s1")
        assert vm.state.session_model_connection_id == "conn-a"

        await vm.refresh()

        assert vm.state.session_model_connection_id is None
        assert vm.state.session_model_source_key == SESSION_MODEL_SOURCE_DEFAULT
        assert vm.state.session_model_error_key is None


# ----------------------------------------------------------------------
# VM：首问提交把会话选择传入受理端口
# ----------------------------------------------------------------------


class TestFirstQuestionPassesSessionModel:
    @pytest.mark.asyncio
    async def test_first_question_forwards_session_connection(self) -> None:
        intake = _RecordingIntake()
        vm = _make_vm(store=_Store("conn-a"), intake=intake)
        await vm.refresh(session_id="s1")
        vm.set_composer_text("  研究银行股  ")

        await vm.send_first_question()

        assert intake.calls == [
            {
                "text": "研究银行股",
                "mode": Mode.EXECUTE,
                "session_id": "s1",
                "expected_revision": 0,
                "role_prompt_refs": (),
                "mcp_selection": (),
                "model_connection_id": "conn-a",
            }
        ]

    @pytest.mark.asyncio
    async def test_first_question_without_selection_inherits(self) -> None:
        intake = _RecordingIntake()
        vm = _make_vm(store=_Store(None), intake=intake)
        vm.set_composer_text("研究银行股")

        await vm.send_first_question()

        assert intake.calls[0]["model_connection_id"] is None

    @pytest.mark.asyncio
    async def test_first_question_auto_default_does_not_write_key(self) -> None:
        """自动默认选择不算用户显式选择：不写会话键（保持继承）。"""

        intake = _RecordingIntake()
        vm = _make_vm(store=_Store(None), intake=intake)
        await vm.refresh()
        await vm.load_model_selection()
        assert vm.state.session_id is None
        assert vm.state.model_selection == "conn-a"
        assert vm.state.model_selection_explicit is False
        vm.set_composer_text("研究银行股")

        await vm.send_first_question()

        assert intake.calls[0]["model_connection_id"] is None

    @pytest.mark.asyncio
    async def test_first_question_explicit_toolbar_selection_is_written(self) -> None:
        intake = _RecordingIntake()
        vm = _make_vm(store=_Store(None), intake=intake)
        await vm.refresh()
        await vm.load_model_selection()
        await vm.select_model_connection("conn-b")
        assert vm.state.model_selection == "conn-b"
        assert vm.state.model_selection_explicit is True
        vm.set_composer_text("研究银行股")

        await vm.send_first_question()

        assert intake.calls[0]["model_connection_id"] == "conn-b"

    @pytest.mark.asyncio
    async def test_first_question_read_error_does_not_override_stored_key(self) -> None:
        """会话模型读失败时即使显式选择过也传 None（不覆盖已存键）。"""

        intake = _RecordingIntake()
        store = _Store(None, read_error=AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "boom", "s1"))
        vm = _make_vm(store=store, intake=intake)
        await vm.refresh(session_id="s1")
        await vm.load_model_selection()
        await vm.load_session_model("s1")
        assert vm.state.session_model_error_key == SESSION_MODEL_UNAVAILABLE_KEY
        await vm.select_model_connection("conn-a")
        assert vm.state.model_selection_explicit is True
        vm.set_composer_text("研究银行股")

        await vm.send_first_question()

        assert intake.calls[0]["model_connection_id"] is None

    @pytest.mark.asyncio
    async def test_first_question_prefers_session_key_over_toolbar_selection(self) -> None:
        intake = _RecordingIntake()
        vm = _make_vm(store=_Store("conn-b"), intake=intake)
        await vm.refresh(session_id="s1")
        await vm.load_model_selection()
        await vm.load_session_model("s1")
        assert vm.state.model_selection == "conn-a"
        assert vm.state.session_model_connection_id == "conn-b"
        vm.set_composer_text("研究银行股")

        await vm.send_first_question()

        assert intake.calls[0]["model_connection_id"] == "conn-b"


# ----------------------------------------------------------------------
# View：会话头选择条
# ----------------------------------------------------------------------


def _walk(control: Any) -> list[Any]:
    """深度遍历控件树（含 Container.content / Row|Column.controls）。"""

    found: list[Any] = [control]
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        found.extend(_walk(content))
    children = getattr(control, "controls", None)
    if isinstance(children, list):
        for child in children:
            found.extend(_walk(child))
    return found


def _texts(control: Any) -> list[str]:
    return [item.value for item in _walk(control) if isinstance(item, ft.Text) and isinstance(item.value, str)]


def _option(connection_id: str, name: str, *, reject_key: str | None = None) -> ModelConnectionOption:
    return ModelConnectionOption(
        connection_id=connection_id,
        display_name=name,
        model="gpt-4o",
        context_window=128000,
        position=1,
        effective=True,
        reject_key=reject_key,
    )


class TestSessionModelBar:
    def test_bar_renders_session_source_and_current_connection(self) -> None:
        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            session_model_connection_id="conn-a",
            session_model_source_key=SESSION_MODEL_SOURCE_SESSION,
            model_options=(_option("conn-a", "连接 A"),),
        )

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)
        texts = _texts(bar)

        assert "连接 A" in texts
        assert I18n.get(SESSION_MODEL_SOURCE_SESSION) in texts
        assert I18n.get("agent_research_session_model_clear") in texts

    def test_bar_renders_inherit_when_no_session_selection(self) -> None:
        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            session_model_source_key=SESSION_MODEL_SOURCE_DEFAULT,
        )

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)

        assert I18n.get("agent_research_session_model_inherit") in _texts(bar)

    def test_bar_renders_unavailable_hint_without_store(self) -> None:
        state = ResearchObserverState(available=True, session_model_available=False)

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)

        assert I18n.get(SESSION_MODEL_UNAVAILABLE_KEY) in _texts(bar)

    def test_bar_does_not_report_unavailable_when_options_not_loaded(self) -> None:
        """候选尚未加载（空集）时不得把会话连接误报为不可用。"""

        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            session_model_connection_id="conn-x",
            session_model_source_key=SESSION_MODEL_SOURCE_SESSION,
            model_options=(),
        )

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)

        assert I18n.get("agent_research_session_model_unavailable") not in _texts(bar)

    def test_bar_dedupes_error_when_keys_match(self) -> None:
        """会话错误键与不可用提示同键时只渲染一行（不重复提示）。"""

        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            session_model_connection_id="conn-gone",
            session_model_source_key=SESSION_MODEL_SOURCE_SESSION,
            session_model_error_key=SESSION_MODEL_UNAVAILABLE_KEY,
            model_options=(),
        )

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)

        assert _texts(bar).count(I18n.get(SESSION_MODEL_UNAVAILABLE_KEY)) == 1

    def test_bar_reports_disabled_session_connection_and_keeps_name(self) -> None:
        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            session_model_connection_id="conn-off",
            session_model_source_key=SESSION_MODEL_SOURCE_SESSION,
            model_options=(_option("conn-off", "连接 C", reject_key="agent_research_model_selection_disabled"),),
        )

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)
        texts = _texts(bar)

        assert "连接 C" in texts
        assert I18n.get("agent_research_model_selection_disabled") in texts
        assert I18n.get("agent_research_session_model_clear") in texts

    def test_bar_reports_missing_session_connection_with_raw_id(self) -> None:
        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            session_model_connection_id="conn-gone",
            session_model_source_key=SESSION_MODEL_SOURCE_SESSION,
            model_options=(_option("conn-a", "连接 A"),),
        )

        bar = build_session_model_bar(state, on_select_model=lambda _v: None, on_clear_model=lambda: None)
        texts = _texts(bar)

        assert "conn-gone" in texts
        assert I18n.get(SESSION_MODEL_UNAVAILABLE_KEY) in texts

    def test_bar_dropdown_ignores_empty_value(self) -> None:
        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            model_options=(_option("conn-a", "连接 A"),),
        )
        selected: list[str] = []

        bar = build_session_model_bar(state, on_select_model=selected.append, on_clear_model=lambda: None)
        dropdown = next(item for item in _walk(bar) if isinstance(item, ft.Dropdown))
        callback = cast(Any, dropdown.on_select)
        callback(SimpleNamespace(control=SimpleNamespace(value="")))
        callback(SimpleNamespace(control=SimpleNamespace(value=None)))

        assert selected == []

    def test_bar_dropdown_lists_only_eligible_options_and_reports_selection(self) -> None:
        state = ResearchObserverState(
            available=True,
            session_model_available=True,
            model_options=(
                _option("conn-a", "连接 A"),
                _option("conn-off", "连接 C", reject_key="agent_research_model_selection_disabled"),
            ),
        )
        selected: list[str] = []

        bar = build_session_model_bar(state, on_select_model=selected.append, on_clear_model=lambda: None)
        dropdown = next(item for item in _walk(bar) if isinstance(item, ft.Dropdown))

        assert [option.key for option in dropdown.options] == ["conn-a"]
        callback = cast(Any, dropdown.on_select)
        callback(SimpleNamespace(control=SimpleNamespace(value="conn-a")))
        assert selected == ["conn-a"]

    def test_bar_clear_button_invokes_callback(self) -> None:
        state = ResearchObserverState(available=True, session_model_available=True)
        cleared: list[bool] = []

        bar = build_session_model_bar(
            state,
            on_select_model=lambda _v: None,
            on_clear_model=lambda: cleared.append(True),
        )
        button = next(item for item in _walk(bar) if isinstance(item, ft.TextButton))
        cast(Any, button.on_click)(None)

        assert cleared == [True]
