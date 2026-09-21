# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""services/scheduled_jobs/review_backfill 单元测试（D2-4 / BIZ-03 / RV-04）。

延迟回填 job：先 T+1（打标签依据），再 T+5（远期收益），返回合计回填描述。
backfill 本身幂等（只回填 t1_pct / t5_pct IS NULL），故 job 无需 idempotency 标记。
RV-04: job 经 TaskManager 提交（结果对用户可见），测试捕获 coroutine_factory
手动执行以验证回填顺序、结果描述与基准诊断拼接。
"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from services.scheduled_jobs.review_backfill import build_review_backfill_job

pytestmark = pytest.mark.unit


class _FakeSvc:
    """最小 SchedulerService 替身：承载 register_job 注入的 callable。"""

    def __init__(self) -> None:
        self.job: object = None

    def register(self, job) -> None:
        self.job = job


def _make_mock_rm(t1_count: int = 7, t5_count: int = 42, diag: str | None = None) -> MagicMock:
    mock_rm = MagicMock()
    mock_rm.backfill_t1_returns = AsyncMock(return_value=t1_count)
    mock_rm.backfill_horizon_returns = AsyncMock(return_value=t5_count)
    mock_rm._benchmark_diag = diag
    return mock_rm


async def _submit_and_run(job, mock_tm_cls) -> str:
    """执行 job（提交任务）并模拟任务中心执行 coroutine_factory，返回结果字符串。"""
    await job(_FakeSvc())
    submit_kwargs = mock_tm_cls.return_value.submit_task.call_args.kwargs
    return await submit_kwargs["coroutine_factory"]("task-1")


class TestBuildReviewBackfillJob:
    @pytest.mark.asyncio
    async def test_job_calls_both_backfills_t1_first(self):
        """BIZ-03 顺序约束：T+1 先于 T+5（T+1 把 PENDING 推进 T1_DONE 后 T+5 通道才能取到）。"""
        job = build_review_backfill_job()
        with (
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls,
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm_cls,
        ):
            mock_rm = _make_mock_rm()
            mock_rm_cls.return_value = mock_rm
            result = await _submit_and_run(job, mock_tm_cls)
        mock_rm.assert_has_calls(
            [
                call.backfill_t1_returns(),
                call.backfill_horizon_returns(),
            ]
        )
        assert "T+1 backfilled: 7" in result
        assert "T+5 backfilled: 42" in result

    @pytest.mark.asyncio
    async def test_job_calls_backfill_and_returns_count(self):
        job = build_review_backfill_job()
        with (
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls,
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm_cls,
        ):
            mock_rm = _make_mock_rm(t1_count=3, t5_count=5)
            mock_rm_cls.return_value = mock_rm
            result = await _submit_and_run(job, mock_tm_cls)
        # 无参调用：assert_called_once_with() 验证恰好一次且零参数（强断言形式）
        mock_rm.backfill_t1_returns.assert_called_once_with()
        mock_rm.backfill_horizon_returns.assert_called_once_with()
        assert "T+1 backfilled: 3" in result
        assert "T+5 backfilled: 5" in result

    @pytest.mark.asyncio
    async def test_job_propagates_backfill_error(self):
        job = build_review_backfill_job()
        with (
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls,
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm_cls,
        ):
            mock_rm = MagicMock()
            mock_rm.backfill_t1_returns = AsyncMock(return_value=0)
            mock_rm.backfill_horizon_returns = AsyncMock(side_effect=RuntimeError("boom"))
            mock_rm._benchmark_diag = None
            mock_rm_cls.return_value = mock_rm
            with pytest.raises(RuntimeError, match="boom"):
                await _submit_and_run(job, mock_tm_cls)

    @pytest.mark.asyncio
    async def test_job_result_includes_benchmark_diag(self):
        """RV-04: 基准降级/缺失诊断拼进任务结果（用户可见），不再只有日志 warning。"""
        job = build_review_backfill_job()
        with (
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls,
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm_cls,
        ):
            mock_rm = _make_mock_rm(diag="基准指数 000985.CSI 无数据，已降级使用 000300.SH")
            mock_rm_cls.return_value = mock_rm
            result = await _submit_and_run(job, mock_tm_cls)
        assert "000985.CSI" in result
        assert "000300.SH" in result

    @pytest.mark.asyncio
    async def test_job_result_omits_diag_when_normal(self):
        """基准正常（diag=None）时结果不含诊断段。"""
        job = build_review_backfill_job()
        with (
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls,
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm_cls,
        ):
            mock_rm = _make_mock_rm(diag=None)
            mock_rm_cls.return_value = mock_rm
            result = await _submit_and_run(job, mock_tm_cls)
        assert "—" not in result
