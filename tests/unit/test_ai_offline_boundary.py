"""MAJOR-04（review09-24 维度04）：离线 AI 能力边界契约测试。

校验「仅本地模式下个股深度分析不可用」对外承诺的一致性：

- zh_CN / en_US locale 均含能力边界提示键，文案不同于「未配置」，避免误导；
- README 不再宣称核心投研（含 AI 个股深度分析）可完全离线，且明确该模式下不可用。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
_LOCALES = ROOT / "locales"
_README = ROOT / "README.md"

_BOUNDARY_KEYS = (
    "ai_local_only_capability_boundary",
    "ai_local_only_deep_analysis_unsupported",
)


def _load_locale(locale: str) -> dict[str, str]:
    return json.loads((_LOCALES / locale / "strings.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_locale_has_local_only_boundary_keys(locale: str) -> None:
    strings = _load_locale(locale)
    for key in _BOUNDARY_KEYS:
        assert key in strings, f"{locale} 缺少 i18n 键 {key}"
        assert strings[key].strip(), f"{locale}.{key} 为空"


def test_boundary_text_differs_from_not_configured() -> None:
    # 能力边界文案必须区别于「未配置」，避免仅本地模式被误导为「未配置」。
    zh = _load_locale("zh_CN")
    en = _load_locale("en_US")
    for key in _BOUNDARY_KEYS:
        assert zh[key] != zh["ai_not_configured"]
        assert en[key] != en["ai_not_configured"]


def test_readme_states_local_only_deep_analysis_unavailable() -> None:
    readme = _README.read_text(encoding="utf-8")
    # 旧承诺（核心投研可完全离线）已被删除。
    assert "核心投研可完全离线运行" not in readme
    # 新承诺：明确仅本地模式这一能力边界。
    assert "仅本地模式" in readme
