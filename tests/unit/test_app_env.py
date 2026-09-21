"""utils/app_env 判定单测（review03-C16 / DS-06）。"""

import pytest

from utils.app_env import is_e2e_mode

pytestmark = pytest.mark.unit


class TestIsE2eMode:
    def test_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        assert is_e2e_mode() is True

    def test_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("E2E_TESTING", raising=False)
        assert is_e2e_mode() is False

    def test_false_when_other_value(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "false")
        assert is_e2e_mode() is False

    def test_false_when_frozen_even_if_env_set(self, monkeypatch):
        """DS-06: 冻结产物（PyInstaller 分发版）忽略 E2E_TESTING——测试后门不得随产物分发。"""
        monkeypatch.setenv("E2E_TESTING", "true")
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert is_e2e_mode() is False

    def test_true_when_frozen_attr_absent_but_env_set(self, monkeypatch):
        """sys.frozen 不存在（源码运行，CI e2e 场景）→ 环境变量照常生效。"""
        monkeypatch.setattr("sys.frozen", False, raising=False)
        monkeypatch.setenv("E2E_TESTING", "true")
        assert is_e2e_mode() is True
