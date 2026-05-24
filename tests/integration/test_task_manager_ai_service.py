"""
Tests for TaskManager and AIService integration.

S3-3: TaskManager resets semaphore on task cancellation.
S3-4: AIService detects reasoning model support.
"""

import asyncio
from unittest.mock import patch

import pytest

from tests.conftest import reset_singleton


@pytest.fixture
def task_manager():
    from services.task_manager import TaskManager

    TaskManager._reset_singleton()
    with patch("services.task_manager.ConfigHandler") as mock_ch, patch("services.task_manager.ThreadPoolManager"):
        mock_ch.get_max_concurrent_tasks.return_value = 2
        tm = TaskManager()
        yield tm
    TaskManager._reset_singleton()


@pytest.fixture
async def semaphore(task_manager):
    return task_manager._get_semaphore()


class TestTaskManagerSemaphoreReset:
    """S3-3: TaskManager must reset semaphore on task cancellation"""

    @pytest.mark.asyncio
    async def test_semaphore_released_on_cancellation(self, semaphore):
        assert semaphore._value == 2

        async def long_task():
            async with semaphore:
                await asyncio.sleep(100)

        task = asyncio.create_task(long_task())
        await asyncio.sleep(0.05)
        assert semaphore._value == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(0.05)
        assert semaphore._value == 2

    @pytest.mark.asyncio
    async def test_semaphore_released_on_exception(self, semaphore):
        async def failing_task():
            async with semaphore:
                raise ValueError("test error")

        with pytest.raises(ValueError):
            await failing_task()

        assert semaphore._value == 2

    @pytest.mark.asyncio
    async def test_semaphore_released_on_normal_completion(self, semaphore):
        async def quick_task():
            async with semaphore:
                return 42

        result = await quick_task()
        assert result == 42
        assert semaphore._value == 2

    @pytest.mark.asyncio
    async def test_multiple_tasks_semaphore_tracking(self):
        from services.task_manager import TaskManager

        TaskManager._reset_singleton()
        with patch("services.task_manager.ConfigHandler") as mock_ch, patch("services.task_manager.ThreadPoolManager"):
            mock_ch.get_max_concurrent_tasks.return_value = 3
            tm = TaskManager()
            sem = tm._get_semaphore()
            assert sem._value == 3

            async def brief_task():
                async with sem:
                    await asyncio.sleep(0.01)
                    return True

            results = await asyncio.gather(brief_task(), brief_task(), brief_task())
            assert all(results)
            assert sem._value == 3

        TaskManager._reset_singleton()


_REASONING_MODEL_CASES = [
    ("deepseek-reasoner", "deepseek", "https://api.deepseek.com", True),
    ("o3", "openai", "https://api.openai.com/v1", True),
    ("o4-mini", "openai", "https://api.openai.com/v1", True),
    ("gpt-4o", "openai", "https://api.openai.com/v1", False),
    ("deepseek-chat", "deepseek", "https://api.deepseek.com", False),
]


class TestAIServiceReasoningSupport:
    """S3-4: AIService must detect reasoning model support"""

    @pytest.mark.parametrize(
        "model,provider,api_url,expected",
        _REASONING_MODEL_CASES,
        ids=[c[0] for c in _REASONING_MODEL_CASES],
    )
    def test_reasoning_detection(self, model, provider, api_url, expected):
        from services.ai_service import AIService

        with reset_singleton(AIService, extra_attrs=["_initialized"]):
            with patch("services.ai_service.ConfigHandler") as mock_ch:
                mock_ch.get_llm_config.return_value = {
                    "api_key": "test-key",
                    "provider": provider,
                    "model": model,
                    "base_url": api_url,
                }
                mock_ch.get_setting.return_value = False
                svc = AIService()
                assert svc._supports_reasoning is expected

    @pytest.mark.asyncio
    async def test_reasoning_model_attribute_set(self):
        from services.ai_service import AIService

        with reset_singleton(AIService, extra_attrs=["_initialized"]):
            with patch("services.ai_service.ConfigHandler") as mock_ch:
                mock_ch.get_llm_config.return_value = {
                    "api_key": "test-key",
                    "provider": "deepseek",
                    "model": "deepseek-reasoner",
                    "base_url": "https://api.deepseek.com",
                }
                mock_ch.get_setting.return_value = False
                svc = AIService()
                assert svc._supports_reasoning is True
                assert hasattr(svc, "_chat_completion_litellm")
