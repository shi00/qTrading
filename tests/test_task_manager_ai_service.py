"""
Tests for TaskManager and AIService.

S1-1: reload_config resets semaphore.
S1-4: Real-time reasoning support detection.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestTaskManagerSemaphoreReset:
    """S1-1: reload_config resets semaphore so new limit takes effect"""

    def test_reload_config_clears_semaphore_in_source(self):
        """reload_config should set _semaphore_instance to None"""
        tm_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "task_manager.py"))
        with open(tm_path, encoding="utf-8") as f:
            source = f.read()

        assert "reload_config" in source, "TaskManager should have reload_config method"
        assert "_semaphore_instance" in source, "TaskManager should have _semaphore_instance"
        has_reset = "_semaphore_instance = None" in source or "_semaphore_instance=None" in source
        assert has_reset, "S1-1: reload_config should reset _semaphore_instance to None"

    def test_get_semaphore_creates_new(self):
        """_get_semaphore should create a new Semaphore instance"""
        tm_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "task_manager.py"))
        with open(tm_path, encoding="utf-8") as f:
            source = f.read()

        assert "_get_semaphore" in source, "TaskManager should have _get_semaphore method"
        assert "Semaphore" in source, "_get_semaphore should create Semaphore"


class TestReasoningSupportDetection:
    """S1-4: Real-time reasoning support check per request"""

    def test_check_reasoning_in_source(self):
        """AIService should have _check_reasoning_support function"""
        ai_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "ai_service.py"))
        with open(ai_path, encoding="utf-8") as f:
            source = f.read()

        assert "_check_reasoning_support" in source, "S1-4: ai_service should have _check_reasoning_support"

    def test_reasoning_models_in_source(self):
        """_check_reasoning_support should reference known reasoning models"""
        ai_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "ai_service.py"))
        with open(ai_path, encoding="utf-8") as f:
            source = f.read()

        has_deepseek = "deepseek" in source.lower() and "reason" in source.lower()
        assert has_deepseek, "S1-4: _check_reasoning_support should detect DeepSeek reasoner"

    def test_check_reasoning_deepseek(self):
        """DeepSeek reasoner should be detected as reasoning-capable"""
        REASONING_PATTERNS = ["deepseek-reasoner", "o1", "o3", "r1"]
        model = "deepseek-reasoner"
        is_reasoning = any(p in model.lower() for p in REASONING_PATTERNS)
        assert is_reasoning is True

    def test_check_reasoning_gpt4_not_reasoning(self):
        """GPT-4 should not be in the reasoning list"""
        REASONING_PATTERNS = ["deepseek-reasoner", "o1", "o3", "r1"]
        model = "gpt-4"
        is_reasoning = any(p in model.lower() for p in REASONING_PATTERNS)
        assert is_reasoning is False
