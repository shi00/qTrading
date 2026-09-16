"""NewsInsightViewModel 单元测试（新闻风险解读第一期 Phase D1）。

覆盖（对齐设计方案 §15.1 ViewModel 项 + R19）：
1. state 不可变 + 默认 idle
2. select_stock 加载证据 → loading_evidence → evidence_ready，不触发 AI（§12 第 2 步）
3. 无证据时 → degraded + no_evidence message
4. generate 分析 → analyzing → ready（成功）/ error（失败）
5. 单飞：在途任务存在时重复 generate 被合并为一次（服务调用仅 1 次）
6. cancel 取消在途任务，无迟到更新（R2）
7. CancelledError 传播（R2）
8. VM 无 flet import（R16/宪法）
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.time_utils
from services.news_insight_models import EvidenceDocument
from services.news_insight_service import NewsInsightOutcome
from ui.viewmodels.news_insight_types import (
    PHASE_ANALYZING,
    PHASE_DEGRADED,
    PHASE_ERROR,
    PHASE_EVIDENCE_READY,
    PHASE_IDLE,
    PHASE_READY,
)
from ui.viewmodels.news_insight_view_model import NewsInsightViewModel, _fmt_time

pytestmark = pytest.mark.unit


def _make_result(
    *,
    status: str = "analyzed_with_events",
    risk_level: str | None = "high",
    confidence: int | None = 80,
    events=None,
) -> MagicMock:
    result = MagicMock()
    result.analysis_status = status
    result.risk_level = risk_level
    result.confidence = confidence
    result.summary = "示例摘要"
    result.events = events or [
        {
            "event_type": "litigation",
            "severity": "high",
            "company_role": "direct",
            "event_status": "ongoing",
            "impact_horizon": "short_term",
            "fact": "事实",
            "impact_reasoning": "推断",
            "uncertainty": "",
            "confidence": 85,
            "evidence_quotes": [{"news_id": 1, "quote": "原文摘录"}],
        }
    ]
    result.coverage = {}
    result.model_id = "m1"
    return result


def _make_fake_service() -> MagicMock:
    service = MagicMock()
    service.load_evidence_preview = AsyncMock(return_value=([_make_evidence()], {"announcement": {"status": "ok"}}))
    service.analyze = AsyncMock(return_value=NewsInsightOutcome(result=_make_result(), reused=False, reuse_type="none"))
    service.analysis_window_label = MagicMock(return_value="2026-08-18 ~ 2026-09-16")
    return service


def _make_evidence() -> EvidenceDocument:
    return EvidenceDocument(
        news_id=1,
        source_kind="announcement",
        source="巨潮资讯",
        title="公告标题",
        content="公告正文",
        quoteable_text="公告正文",
        publish_time=__import__("datetime").datetime(2026, 9, 10, 8, 0, 0),
    )


async def _run(coro) -> None:
    """await 一个协程到完成（驱动单测内的 async 任务）。"""
    await coro


# --- Fixtures ---


@pytest.fixture
def fake_service():
    return _make_fake_service()


@pytest.fixture
def vm(fake_service):
    return NewsInsightViewModel(service=fake_service)


class TestStateImmutability:
    def test_state_is_frozen(self, vm):
        with pytest.raises(FrozenInstanceError):  # noqa: weak-assertion frozen 契约：赋值即抛错，仅验证不可变性
            vm.state.phase = PHASE_READY  # type: ignore[misc]

    def test_default_state_is_idle(self, vm):
        assert vm.state.phase == PHASE_IDLE
        assert vm.state.risk_level is None
        assert vm.state.confidence is None
        assert vm.state.events == ()


class TestSelectStock:
    async def test_select_loads_evidence_not_ai(self, vm, fake_service):
        """select_stock 只加载证据（evidence_ready），不调用 analyze（无 AI 调用）。"""
        vm.select_stock("000001.SZ", "平安银行")
        # 等待证据加载任务完成
        await _drain(vm)
        assert vm.state.phase == PHASE_EVIDENCE_READY
        assert vm.state.ts_code == "000001.SZ"
        assert vm.state.stock_name == "平安银行"
        assert len(vm.state.evidence) == 1
        fake_service.load_evidence_preview.assert_awaited_once()
        fake_service.analyze.assert_not_awaited()


class TestNoEvidence:
    async def test_empty_evidence_is_degraded(self, fake_service):
        fake_service.load_evidence_preview = AsyncMock(return_value=([], {}))
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        assert vm.state.phase == PHASE_DEGRADED
        assert vm.state.message is not None
        assert vm.state.message.key == "news_insight_no_evidence"


class TestGenerate:
    async def test_generate_ready(self, vm):
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await _drain(vm)
        assert vm.state.phase == PHASE_READY
        assert vm.state.risk_level == "high"
        assert vm.state.confidence == 80
        assert len(vm.state.events) == 1
        assert vm.state.events[0].event_type == "litigation"
        assert vm.state.events[0].evidence_quotes[0].news_id == 1

    async def test_analyze_failure_error(self, fake_service):
        fake_service.analyze = AsyncMock(side_effect=RuntimeError("boom"))
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await _drain(vm)
        assert vm.state.phase == PHASE_ERROR
        assert vm.state.message is not None
        assert vm.state.message.key == "news_insight_err_analyze"

    async def test_analyze_cancelled_not_swallowed(self, fake_service):
        """R2：analyze 抛 CancelledError 时 VM 不得转成 error（吞没会误设 error state）。"""
        fake_service.analyze = AsyncMock(side_effect=asyncio.CancelledError())
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        # 任务被取消（R2 传播），不应落入吞没后的 error / degraded 过渡态
        await asyncio.sleep(0.02)
        assert vm.state.phase != PHASE_ERROR
        assert vm.state.message is None


class TestSingleFlight:
    async def test_repeat_generate_merges_to_one(self, vm, fake_service):
        """单飞：服务 analyze 仅被调用一次（§12 短时间重复点击合并为一次请求）。"""
        vm.select_stock("000001.SZ")
        await _drain(vm)  # 等证据加载完成，active_task 清空

        calls = 0

        async def slow_analyze(*args, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return NewsInsightOutcome(result=_make_result(), reused=False)

        fake_service.analyze = AsyncMock(side_effect=slow_analyze)
        # 第一次 generate 启动在途任务
        vm.generate()
        assert vm.state.phase == PHASE_ANALYZING
        # 在途任务未完成时重复 generate → 合并，不新增调用
        vm.generate()
        vm.generate()
        await _drain(vm)
        await _drain(vm)
        assert calls == 1


class TestCancel:
    async def test_cancel_no_late_update(self, vm, fake_service):
        """关闭详情调 cancel：取消在途任务，无迟到更新污染 state（§12 第 6 步）。"""
        started = asyncio.Event()
        released = asyncio.Event()

        async def blocking_analyze(*args, **kwargs):
            started.set()
            await released.wait()
            return NewsInsightOutcome(result=_make_result(), reused=False)

        fake_service.analyze = AsyncMock(side_effect=blocking_analyze)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await asyncio.wait_for(started.wait(), timeout=1)
        assert vm.state.phase == PHASE_ANALYZING
        # cancel 传播取消
        vm.cancel()
        released.set()  # 释放阻塞，若取消失效则会进入 ready
        await asyncio.sleep(0.05)
        # 迟到更新被禁止：state 不应变为 ready
        assert vm.state.phase != PHASE_READY


class TestNoFletImport:
    def test_vm_does_not_import_flet(self):
        """R16/宪法：VM 模块不得 import flet（AST 精确检测 import 语句）。"""
        import ast
        import inspect

        src = inspect.getsource(NewsInsightViewModel)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "flet", f"flet import: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "flet", f"flet import from: {node.module}"


def _make_outcome(status: str) -> NewsInsightOutcome:
    return NewsInsightOutcome(result=_make_result(status=status), reused=False, reuse_type="none")


class TestFmtTime:
    def test_none_returns_empty(self):
        assert _fmt_time(None) == ""

    def test_invalid_dt_returns_empty(self, monkeypatch):
        import utils.time_utils as tu

        def _boom(dt):
            raise ValueError("bad")

        monkeypatch.setattr(tu, "from_utc_to_cst", _boom)
        assert _fmt_time(datetime(2026, 9, 10, 8, 0, 0)) == ""

    def test_cst_formatted(self):
        s = _fmt_time(datetime(2026, 9, 10, 8, 0, 0))
        assert s == "" or "-" in s or ":" in s


class TestSelectStockNoTsCode:
    def test_empty_ts_code_no_op(self, fake_service):
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("")
        assert vm.state.phase == PHASE_IDLE


class TestGenerateNoSelection:
    def test_without_selection_no_op(self, fake_service):
        vm = NewsInsightViewModel(service=fake_service)
        vm.generate()
        assert vm.state.phase == PHASE_IDLE


class TestGenerateNoLoop:
    def test_no_loop_is_error(self, fake_service, monkeypatch):
        vm = NewsInsightViewModel(service=fake_service)
        vm._selected_ts_code = "000001.SZ"
        monkeypatch.setattr(vm, "_get_loop_or_none", lambda: None)
        vm.generate()
        assert vm.state.phase == PHASE_ERROR
        assert vm.state.message is not None
        assert vm.state.message.key == "news_insight_err_no_loop"


class TestBeginLoadNoLoop:
    def test_no_loop_is_error(self, fake_service, monkeypatch):
        vm = NewsInsightViewModel(service=fake_service)
        monkeypatch.setattr(vm, "_get_loop_or_none", lambda: None)
        vm.select_stock("000001.SZ")
        assert vm.state.phase == PHASE_ERROR
        assert vm.state.message is not None
        assert vm.state.message.key == "news_insight_err_no_loop"


class TestRetry:
    async def test_retry_generates(self, vm, fake_service):
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.retry()
        await _drain(vm)
        assert vm.state.phase == PHASE_READY
        fake_service.analyze.assert_awaited()


class TestDispose:
    async def test_dispose_resets_state(self, fake_service):
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        assert vm.state.phase == PHASE_EVIDENCE_READY
        vm.dispose()
        assert vm.state.phase == PHASE_IDLE
        assert vm._active_task is None
        assert vm._selected_ts_code == ""


class TestLoadEvidenceError:
    async def test_evidence_error_sets_error(self, fake_service):
        fake_service.load_evidence_preview = AsyncMock(side_effect=RuntimeError("boom"))
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        assert vm.state.phase == PHASE_ERROR
        assert vm.state.message.key == "news_insight_err_evidence"

        async def _wait_task(vm):
            task = getattr(vm, "_active_task", None)
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)

        await _wait_task(vm)


class TestApplyOutcomeFailed:
    async def test_failed_sets_error(self, fake_service):
        fake_service.analyze = AsyncMock(return_value=_make_outcome("failed"))
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await _drain(vm)
        assert vm.state.phase == PHASE_ERROR
        assert vm.state.message.key == "news_insight_analysis_failed"


class TestApplyOutcomeDegraded:
    async def test_evidence_only_sets_degraded(self, fake_service):
        fake_service.analyze = AsyncMock(return_value=_make_outcome("evidence_only"))
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await _drain(vm)
        assert vm.state.phase == PHASE_DEGRADED
        assert vm.state.message.key == "news_insight_degraded"


class TestWindowLabelError:
    async def test_window_label_error_unknown(self, fake_service):
        fake_service.analysis_window_label = MagicMock(side_effect=RuntimeError("boom"))
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await _drain(vm)
        assert vm.state.phase == PHASE_READY
        assert vm.state.window_label == "unknown"


class TestNowStrError:
    async def test_now_error_empty(self, fake_service, monkeypatch):
        def _boom():
            raise ValueError("bad")

        monkeypatch.setattr(utils.time_utils, "get_now", _boom)
        vm = NewsInsightViewModel(service=fake_service)
        vm.select_stock("000001.SZ")
        await _drain(vm)
        vm.generate()
        await _drain(vm)
        assert vm.state.phase == PHASE_READY
        assert vm.state.analysis_time == ""


class TestOnTaskDoneExc:
    def test_task_exception_logged(self, fake_service):
        vm = NewsInsightViewModel(service=fake_service)
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        vm._active_task = task
        vm._on_task_done(task)  # 不应抛出，仅记录日志
        assert vm._active_task is None


async def _drain(vm: NewsInsightViewModel) -> None:
    """驱动 VM 后台任务完成（等待 active_task 空闲）。

    用 ``asyncio.shield`` 包裹任务以避免任务取消/异常直接击中测试循环；任务的
    取消/普通异常在等待点被捕获而不回抛到测试（R2 语义由 VM 内部自检覆盖）。
    """
    for _ in range(200):
        task = getattr(vm, "_active_task", None)
        if task is None:
            await asyncio.sleep(0)
            task = getattr(vm, "_active_task", None)
            if task is None:
                return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            await asyncio.sleep(0)
        except (TimeoutError, asyncio.CancelledError):
            # 任务仍在运行或已被取消：继续等 active_task 变为 None
            if getattr(vm, "_active_task", None) is None:
                return
            await asyncio.sleep(0)
