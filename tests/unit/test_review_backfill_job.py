# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""services/scheduled_jobs/review_backfill 单元测试（D2-4）。

验证 T+5 延迟回填 job 编排：TaskManager 提交 → ReviewManager.backfill_horizon_returns。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.scheduled_jobs.review_backfill import build_review_backfill_job

pytestmark = pytest.mark.unit


class TestRunReviewBackfill:
    @pytest.mark.asyncio
    async def test_submits_task_with_unique_key(self):
        svc = MagicMock()
        job = build_review_backfill_job()
        mock_tm_instance = MagicMock()
        with (
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm,
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm,
        ):
            mock_tm.return_value = mock_tm_instance
            mock_rm_instance = MagicMock()
            mock_rm_instance.backfill_horizon_returns = AsyncMock(return_value=3)
            mock_rm.return_value = mock_rm_instance

            await job(svc)

            submit_kwargs = mock_tm_instance.submit_task.call_args.kwargs
            assert submit_kwargs.get("unique_key") == "review_t5_backfill"
            assert submit_kwargs.get("cancellable") is False
            assert callable(submit_kwargs.get("coroutine_factory"))

            # 执行 factory 验证真实编排：调用 backfill_horizon_returns 并推进进度
            factory = submit_kwargs["coroutine_factory"]
            result = await factory("task-1")
            mock_rm_instance.backfill_horizon_returns.assert_awaited_once()
            assert "3" in result

    @pytest.mark.asyncio
    async def test_factory_updates_progress(self):
        svc = MagicMock()
        job = build_review_backfill_job()
        mock_tm_instance = MagicMock()
        with (
            patch("services.scheduled_jobs.review_backfill.TaskManager") as mock_tm,
            patch("services.scheduled_jobs.review_backfill.ReviewManager") as mock_rm,
        ):
            mock_tm.return_value = mock_tm_instance
            mock_rm_instance = MagicMock()
            mock_rm_instance.backfill_horizon_returns = AsyncMock(return_value=0)
            mock_rm.return_value = mock_rm_instance

            await job(svc)
            factory = mock_tm_instance.submit_task.call_args.kwargs["coroutine_factory"]
            await factory("task-1")

            # 进度推进：至少调用 update_progress（开始 0.1 + 完成 1.0）
            assert mock_tm_instance.update_progress.call_count >= 2
