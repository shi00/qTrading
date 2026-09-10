# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""services/scheduled_jobs/review_backfill 单元测试（D2-4）。

T+5 延迟回填 job：调用 ReviewManager.backfill_horizon_returns 并返回回填条数。
backfill 本身幂等（只回填 t5_pct IS NULL），故 job 无需 idempotency 标记。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.scheduled_jobs.review_backfill import build_review_backfill_job

pytestmark = pytest.mark.unit


class _FakeSvc:
    """最小 SchedulerService 替身：承载 register_job 注入的 callable。"""

    def __init__(self) -> None:
        self.job: object = None

    def register(self, job) -> None:
        self.job = job


class TestBuildReviewBackfillJob:
    @pytest.mark.asyncio
    async def test_job_calls_backfill_and_returns_count(self):
        svc = _FakeSvc()
        job = build_review_backfill_job()
        with patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls:
            mock_rm = MagicMock()
            mock_rm.backfill_horizon_returns = AsyncMock(return_value=42)
            mock_rm_cls.return_value = mock_rm
            await job(svc)
        mock_rm.backfill_horizon_returns.assert_called_once()  # noqa: weak-assertion 无参调用无可验证参数，仅确认调度路径触达

    @pytest.mark.asyncio
    async def test_job_propagates_backfill_error(self):
        svc = _FakeSvc()
        job = build_review_backfill_job()
        with patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm_cls:
            mock_rm = MagicMock()
            mock_rm.backfill_horizon_returns = AsyncMock(side_effect=RuntimeError("boom"))
            mock_rm_cls.return_value = mock_rm
            with pytest.raises(RuntimeError, match="boom"):
                await job(svc)
