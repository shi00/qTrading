"""utils/app_env 判定单测（review03-C16）。"""

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
