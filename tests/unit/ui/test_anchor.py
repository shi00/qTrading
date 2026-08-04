"""ui.testing.anchor 契约测试。

守护 `anchored()` 函数的行为契约：
- 生产模式（E2E_TESTING 未设）: no-op，直接返回原控件（R16 不引入副作用）
- E2E 模式（E2E_TESTING=true）: 返回 `ft.Semantics(container=True, label=EID, content=control)`
- `@cache` 行为：env var 变更后需 `cache_clear()` 才生效（避免 pytest session 内漂移）

PR-1 范围：仅守护 anchored() 函数行为；INTERACTIVE/INPUT/LABEL/COMPLEX 四类的
CanvasKit DOM 生成行为由 e2e smoke test（tests/e2e/test_screener_anchor_smoke.py）
与 PoC verifier（reviews/poc/anchor_poc_verifier.py）联合守护。
"""

import flet as ft
import pytest

from ui.testing.anchor import _e2e_enabled, anchored
from ui.testing.e2e_ids import EIDS

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_e2e_cache():
    """每个测试前后清理 _e2e_enabled 缓存，避免 env var 漂移."""
    _e2e_enabled.cache_clear()
    yield
    _e2e_enabled.cache_clear()


class TestE2EEnabledCache:
    """@cache 行为契约（方案 §6.3 + §2 unknown 表 confirmed 项）."""

    def test_e2e_testing_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv("E2E_TESTING", raising=False)
        assert _e2e_enabled() is False

    def test_e2e_testing_true_returns_true(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        assert _e2e_enabled() is True

    def test_e2e_testing_other_value_returns_false(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "false")
        assert _e2e_enabled() is False

    def test_cache_does_not_reread_env_after_first_call(self, monkeypatch):
        """@cache 一旦缓存不重读 env，需 cache_clear 才能切换（已知陷阱）."""
        monkeypatch.setenv("E2E_TESTING", "true")
        assert _e2e_enabled() is True
        # 切换 env 但不 clear cache → 仍返回 True（缓存命中）
        monkeypatch.setenv("E2E_TESTING", "false")
        assert _e2e_enabled() is True
        # cache_clear 后重读 → 返回 False
        _e2e_enabled.cache_clear()
        assert _e2e_enabled() is False


class TestAnchoredProductionMode:
    """生产模式（E2E_TESTING 未设）: anchored() no-op 契约."""

    def test_returns_original_control_when_e2e_disabled(self, monkeypatch):
        monkeypatch.delenv("E2E_TESTING", raising=False)
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert result is btn, "生产模式必须返回原控件（identity 相等）"

    def test_does_not_wrap_in_semantics_when_e2e_disabled(self, monkeypatch):
        monkeypatch.delenv("E2E_TESTING", raising=False)
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert not isinstance(result, ft.Semantics), "生产模式不应包裹 Semantics（零性能/语义副作用）"


class TestAnchoredE2EMode:
    """E2E 模式（E2E_TESTING=true）: anchored() 包裹契约."""

    def test_returns_semantics_when_e2e_enabled(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert isinstance(result, ft.Semantics)

    def test_semantics_container_is_true(self, monkeypatch):
        """container=True 阻止父容器合并 anchor label（PoC A2 实证）."""
        monkeypatch.setenv("E2E_TESTING", "true")
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert isinstance(result, ft.Semantics)
        assert result.container is True

    def test_semantics_label_is_eid_string(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert isinstance(result, ft.Semantics)
        eid_str, _kind = EIDS.SCREENER.RUN_BUTTON
        assert result.label == eid_str

    def test_semantics_content_is_original_control(self, monkeypatch):
        monkeypatch.setenv("E2E_TESTING", "true")
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert isinstance(result, ft.Semantics)
        assert result.content is btn

    def test_does_not_set_on_tap(self, monkeypatch):
        """不设 on_tap → 事件穿透到内部控件（PoC A3 实证）."""
        monkeypatch.setenv("E2E_TESTING", "true")
        btn = ft.Button("run")
        result = anchored(EIDS.SCREENER.RUN_BUTTON, btn)
        assert isinstance(result, ft.Semantics)
        assert result.on_tap is None

    def test_works_for_complex_kind_dropdown(self, monkeypatch):
        """COMPLEX 类（Dropdown）也走同一 anchored() 路径（无分叉）."""
        monkeypatch.setenv("E2E_TESTING", "true")
        dd = ft.Dropdown(label="strategy")
        result = anchored(EIDS.SCREENER.STRATEGY_DROPDOWN, dd)
        assert isinstance(result, ft.Semantics)
        eid_str, _kind = EIDS.SCREENER.STRATEGY_DROPDOWN
        assert result.label == eid_str
        assert result.content is dd
        assert result.container is True
        assert result.on_tap is None, "COMPLEX 类也不应设 on_tap（事件穿透到 Dropdown）"
