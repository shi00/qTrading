"""研究观察层只读路径的模型隔离与人工许可「至多一次提交」精确断言。

E2E 层只能观察可见行为（输入、导航、面板状态），无法证明「读取历史/成果不
触发模型回合」与「重复提交只落一次」。本模块用计数替身把这两条钉在单测层：
读路径只经持久化读取，模型网关调用计数必须为 0；同一人工许可在途时第二次
提交被拒绝且动作只调度一次。
"""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from tests.unit.ui.test_research_observer_view_model import _FakeService
from ui.agent_decision_arbiter import (
    REFUSAL_BUSY,
    get_decision_arbiter,
    submit_arbitrated_action,
)
from ui.viewmodels.research_observer_view_model import ResearchObserverViewModel

pytestmark = pytest.mark.unit


class _CountingModelGateway:
    """模型网关替身：启动首问/计划即代表一次模型回合，读路径必须不触碰它。"""

    def __init__(self) -> None:
        self.start_question_calls = 0
        self.start_plan_calls = 0

    async def start_question(self, text: str, **_kwargs: Any) -> Any:
        self.start_question_calls += 1
        return object()

    async def start_plan(self, reference: Any, **_kwargs: Any) -> Any:
        self.start_plan_calls += 1
        return object()


class _RecordingPage:
    """最小页面替身：只记录被调度的协程，不真正运行（由测试显式驱动）。"""

    def __init__(self) -> None:
        self.scheduled: list[Awaitable[None]] = []

    def run_task(self, handler: Callable[[], Awaitable[None]]) -> None:
        self.scheduled.append(handler())


@pytest.fixture
def decision_slot():
    """隔离进程内唯一仲裁器：用例前后清空唯一槽位（thread-safe 单例）。"""
    arbiter = get_decision_arbiter()
    arbiter.reset()
    yield arbiter
    arbiter.reset()


class TestReadPathsNeverInvokeModel:
    @pytest.mark.asyncio
    async def test_reading_history_and_artifacts_starts_zero_model_turns(self) -> None:
        """刷新/会话/成果/草稿读取全部完成，模型网关调用计数仍为 0。"""
        service = _FakeService()
        gateway = _CountingModelGateway()
        vm = ResearchObserverViewModel(service, intake=gateway)

        await vm.refresh()
        await vm.load_sessions()
        await vm.open_session("s-1")
        await vm.load_artifact(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1")
        await vm.load_draft("s-1", 2)

        # 读取确实发生且投影到 state（不是空跑）
        assert vm.state.session_id == "s-1"
        assert vm.state.session_title == "研究 A"
        body = vm.state.artifact_body_for("a-1", 1)
        assert body is not None
        assert body.body_text == "完整成果正文"

        # 模型网关（首问/计划启动即模型回合）一次都未被触发
        assert gateway.start_question_calls == 0
        assert gateway.start_plan_calls == 0
        # 只读路径也不产生任何答复写入
        assert service.answer_calls == []


class TestToolAuthorizationSubmitsAtMostOnce:
    @pytest.mark.asyncio
    async def test_duplicate_authorization_submits_once_and_releases_slot(self, decision_slot) -> None:
        """在途期间的第二次授权被拒绝，动作只调度一次；完成后槽位释放。"""
        page = _RecordingPage()
        commits: list[str] = []

        async def commit_authorization() -> None:
            commits.append("committed")

        first = submit_arbitrated_action(
            page=page,
            owner="decision_center",
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
            action=commit_authorization,
        )
        second = submit_arbitrated_action(
            page=page,
            owner="decision_center",
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
            action=commit_authorization,
        )

        assert first is None
        assert second == REFUSAL_BUSY
        assert len(page.scheduled) == 1, "重复授权不得调度第二个提交任务"
        assert commits == [], "授权动作只能在被调度的任务内执行，不得同步提交"

        await page.scheduled[0]

        assert commits == ["committed"]
        assert decision_slot.owner is None
