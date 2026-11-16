# pyright: reportArgumentType=false
# 本文件含测试替身/mock 模式，统一局部禁用参数类型告警（同 test_news_insight_view_model.py 范式）。

"""SentimentIndexViewModel 单测（N5-3）。

覆盖：select_stock 索引加载（ready/error/空参数）、select_day 明细展开与收起、
迟到结果丢弃（代次守卫）、R21（score/conf None 保留）、frozen state 契约。
"""

import asyncio
import dataclasses
import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.news_sentiment_index_service import DailySentimentPoint
from ui.viewmodels.sentiment_index_types import PHASE_ERROR, PHASE_IDLE, PHASE_READY, SentimentIndexState
from ui.viewmodels.sentiment_index_view_model import SentimentIndexViewModel, _rows_to_news_items

pytestmark = pytest.mark.unit

DAY = datetime.date(2026, 10, 12)
DAY2 = datetime.date(2026, 10, 13)


def _dp(trade_date: datetime.date = DAY, index: float | None = 0.5, n: int = 4, insufficient: bool = False):
    return DailySentimentPoint(trade_date=trade_date, index=index, sample_count=n, sample_insufficient=insufficient)


def _news_row(title: str) -> dict:
    return {
        "publish_time": None,
        "title": title,
        "content": None,
        "source_kind": None,
        "sentiment_score": 1.0,
        "sentiment_conf": 1.0,
    }


def _mk_service(points: list | None = None, day_rows: list | None = None) -> MagicMock:
    svc = MagicMock()
    svc.get_stock_daily_index = AsyncMock(return_value=list(points or []))
    svc.get_stock_day_news = AsyncMock(return_value=list(day_rows or []))
    return svc


@pytest.fixture
def vm_factory():
    created: list[SentimentIndexViewModel] = []

    def _make(service: MagicMock) -> SentimentIndexViewModel:
        vm = SentimentIndexViewModel(service=service)
        created.append(vm)
        return vm

    yield _make
    for vm in created:
        vm.dispose()


class TestSelectStock:
    @pytest.mark.asyncio
    async def test_loads_points_to_ready(self, vm_factory):
        svc = _mk_service(points=[_dp()])
        vm = vm_factory(svc)
        vm.select_stock("600519.SH", "贵州茅台")
        assert vm.state.phase == "loading"
        await asyncio.sleep(0)
        assert vm.state.phase == PHASE_READY
        assert vm.state.ts_code == "600519.SH"
        assert vm.state.stock_name == "贵州茅台"
        assert len(vm.state.points) == 1
        assert vm.state.points[0].index == pytest.approx(0.5)
        svc.get_stock_daily_index.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_ts_code_ignored(self, vm_factory):
        vm = vm_factory(_mk_service())
        vm.select_stock("")
        assert vm.state.phase == PHASE_IDLE

    @pytest.mark.asyncio
    async def test_service_error_sets_error_phase(self, vm_factory):
        svc = MagicMock()
        svc.get_stock_daily_index = AsyncMock(side_effect=RuntimeError("db down"))
        vm = vm_factory(svc)
        vm.select_stock("600519.SH")
        await asyncio.sleep(0)
        assert vm.state.phase == PHASE_ERROR
        assert vm.state.message is not None and vm.state.message.key == "sentiment_index_err"

    @pytest.mark.asyncio
    async def test_empty_points_sets_empty_message(self, vm_factory):
        vm = vm_factory(_mk_service(points=[]))
        vm.select_stock("600519.SH")
        await asyncio.sleep(0)
        assert vm.state.phase == PHASE_READY
        assert vm.state.points == ()
        assert vm.state.message is not None and vm.state.message.key == "sentiment_index_empty"


class TestSelectDay:
    @pytest.mark.asyncio
    async def test_expands_day_detail(self, vm_factory):
        rows = [
            {
                "publish_time": datetime.datetime(2026, 10, 12, 1, 0),
                "title": "利好公告",
                "content": "正文",
                "source_kind": "announcement",
                "sentiment_score": 0.8,
                "sentiment_conf": 0.9,
            }
        ]
        svc = _mk_service(points=[_dp()], day_rows=rows)
        vm = vm_factory(svc)
        vm.select_stock("600519.SH")
        await asyncio.sleep(0)
        vm.select_day(DAY)
        assert vm.state.selected_day == DAY
        assert vm.state.day_news_loading is True
        await asyncio.sleep(0)
        assert vm.state.day_news_loading is False
        assert len(vm.state.day_news) == 1
        assert vm.state.day_news[0].score == pytest.approx(0.8)
        svc.get_stock_day_news.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_same_day_click_collapses(self, vm_factory):
        svc = _mk_service(points=[_dp()])
        vm = vm_factory(svc)
        vm.select_stock("600519.SH")
        await asyncio.sleep(0)
        vm.select_day(DAY)
        await asyncio.sleep(0)
        vm.select_day(DAY)  # 同日再点 → 收起
        assert vm.state.selected_day is None
        assert vm.state.day_news == ()

    @pytest.mark.asyncio
    async def test_ignored_without_points(self, vm_factory):
        vm = vm_factory(_mk_service())
        vm.select_day(DAY)  # 未选股票
        assert vm.state.selected_day is None

    @pytest.mark.asyncio
    async def test_detail_error_sets_message(self, vm_factory):
        svc = MagicMock()
        svc.get_stock_daily_index = AsyncMock(return_value=[_dp()])
        svc.get_stock_day_news = AsyncMock(side_effect=RuntimeError("db down"))
        vm = vm_factory(svc)
        vm.select_stock("600519.SH")
        await asyncio.sleep(0)
        vm.select_day(DAY)
        await asyncio.sleep(0)
        assert vm.state.day_news_loading is False
        assert vm.state.message is not None and vm.state.message.key == "sentiment_index_err"

    @pytest.mark.asyncio
    async def test_cancel_after_detail_started_drops_late_success(self, vm_factory):
        """换股取消：服务已进入旧请求后再切换，旧任务必须真正取消且不写回。"""
        started = asyncio.Event()
        release = asyncio.Event()
        svc = MagicMock()
        svc.get_stock_daily_index = AsyncMock(return_value=[_dp()])

        async def _gated_day_news(ts_code, stock_name, day):
            if day == DAY:
                started.set()
                await release.wait()
                return [_news_row("迟到成功")]
            return []

        svc.get_stock_day_news = AsyncMock(side_effect=_gated_day_news)
        vm = vm_factory(svc)
        vm.select_stock("600519.SH")
        await asyncio.wait_for(vm._index_task, timeout=1.0)
        vm.select_day(DAY)
        detail_task = vm._detail_task
        assert detail_task is not None
        try:
            # 断言服务确实进入旧请求（停在受控 await），而非取消一个尚未启动的任务
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert not detail_task.done()
            vm.select_stock("000001.SZ")  # 旧明细在途时换股：取消旧任务 + 重置 selected_day
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(detail_task, timeout=1.0)
            assert detail_task.cancelled()
        finally:
            release.set()
            await asyncio.gather(detail_task, return_exceptions=True)
            if vm._index_task is not None:
                await asyncio.wait_for(vm._index_task, timeout=1.0)
        assert vm.state.ts_code == "000001.SZ"
        assert vm.state.selected_day is None
        assert vm.state.day_news == ()
        assert vm.state.day_news_loading is False

    @pytest.mark.asyncio
    async def test_late_detail_success_dropped_on_day_switch(self, vm_factory):
        """成功分支代次守卫：旧请求已进入服务后切到新 day，迟到成功不得写回状态或通知。"""
        started = asyncio.Event()
        release = asyncio.Event()
        svc = MagicMock()
        svc.get_stock_daily_index = AsyncMock(return_value=[_dp(), _dp(DAY2)])

        async def _gated_day_news(ts_code, stock_name, day):
            if day == DAY:
                started.set()
                await release.wait()
                return [_news_row("迟到成功")]
            return [_news_row("DAY2 正常")]

        svc.get_stock_day_news = AsyncMock(side_effect=_gated_day_news)
        vm = vm_factory(svc)
        snapshots: list[SentimentIndexState] = []
        unsub = vm.subscribe(snapshots.append)
        old_task: asyncio.Task | None = None
        try:
            vm.select_stock("600519.SH")
            await asyncio.wait_for(vm._index_task, timeout=1.0)
            # 直接驱动旧 DAY 请求（模拟绕过任务取消的迟到成功），停在服务入口
            old_task = asyncio.create_task(vm._load_detail_task("600519.SH", "", DAY))
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert not old_task.done()
            # 旧请求在途时切到新 day 并完成其明细加载
            vm.select_day(DAY2)
            await asyncio.wait_for(vm._detail_task, timeout=1.0)
            assert vm.state.selected_day == DAY2
            assert [item.title for item in vm.state.day_news] == ["DAY2 正常"]
            snapshots.clear()
            release.set()  # 放行旧请求，返回成功结果
            await asyncio.wait_for(old_task, timeout=1.0)
            assert not old_task.cancelled()
        finally:
            release.set()
            if old_task is not None:
                await asyncio.gather(old_task, return_exceptions=True)
            unsub()
        assert vm.state.selected_day == DAY2
        assert [item.title for item in vm.state.day_news] == ["DAY2 正常"]
        assert vm.state.day_news_loading is False
        assert vm.state.message is None
        # 订阅快照同样不得携带旧请求结果
        assert all(item.title != "迟到成功" for snap in snapshots for item in snap.day_news)

    @pytest.mark.asyncio
    async def test_late_detail_error_dropped_on_reselect(self, vm_factory):
        """异常分支代次守卫：selected_day 已切换时，迟到失败不写回当前选日状态。"""
        svc = MagicMock()
        svc.get_stock_daily_index = AsyncMock(return_value=[_dp()])

        def _fail_for_day(ts_code, stock_name, day):
            if day == DAY:
                raise RuntimeError("late failure")
            return []  # DAY2 正常成功

        svc.get_stock_day_news = AsyncMock(side_effect=_fail_for_day)
        vm = vm_factory(svc)
        vm.select_stock("600519.SH")
        await asyncio.sleep(0)
        vm.select_day(DAY2)
        await asyncio.sleep(0)  # DAY2 明细加载完成（day_news=()，message=None）
        assert vm.state.selected_day == DAY2
        assert vm.state.message is None
        # 直接驱动 DAY 任务的失败路径（模拟绕过任务取消的迟到失败）
        await vm._load_detail_task("600519.SH", "", DAY)
        assert vm.state.selected_day == DAY2
        assert vm.state.message is None  # DAY 的失败不得写进 DAY2 展示态
        assert vm.state.day_news == ()


class TestNewsItemMapping:
    def test_none_score_conf_preserved(self):
        """R21：未打分行 score/conf 保持 None（不填 0）。"""
        rows = [
            {
                "publish_time": None,
                "title": None,
                "content": "只有正文",
                "source_kind": "telegraph",
                "sentiment_score": None,
                "sentiment_conf": None,
            },
            {
                "publish_time": datetime.datetime(2026, 10, 12, 2, 0),
                "title": "T",
                "content": None,
                "source_kind": "news",
                "sentiment_score": -0.9,
                "sentiment_conf": 0.5,
            },
        ]
        items = _rows_to_news_items(rows)
        assert items[0].score is None and items[0].conf is None
        assert items[0].title is None and items[0].excerpt == "只有正文"
        assert items[1].score == pytest.approx(-0.9)
        assert items[1].publish_time != ""  # 有效时间已格式化


def test_state_is_frozen():
    state = SentimentIndexState()
    with pytest.raises(dataclasses.FrozenInstanceError) as exc_info:
        state.phase = PHASE_READY  # type: ignore[misc]  # [reason: 故意验证 frozen 契约]
    assert "phase" in str(exc_info.value)  # 冻结赋值错误应指向目标字段
    assert state.phase == PHASE_IDLE  # frozen 契约：赋值失败后字段保持原值
