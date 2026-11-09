"""G4-202 四主题截图工具（scripts/g4_theme_screenshots.py）单测。

只覆盖脚本自身的纯函数与参数契约（不启动 GUI、不依赖 Win32）：
- ``THEMES`` 与 ``ui.theme.ThemeName`` 保持同一套四主题取值；
- 后端开关只对 Skia 注入 ``FLET_NO_IMPELLER``，Impeller 必须清除该变量；
- 场景计划覆盖「自动形态 + 后端专属关键页」，Skia 回退按 §5.11 只重跑四项；
- 主题主色核验对 NAVY / DRACULA 这类相邻底色不得误判为通过；
- 差分在基线缺失时记为 ``missing_baseline``，不得等价成「无差异」（R21）。

主题名白名单校验属于 ``utils/config_models.py`` 的产品行为，其回归用例位于
``tests/unit/test_config_models.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

import scripts.g4_theme_screenshots as g4

pytestmark = [pytest.mark.unit, pytest.mark.meta]


def test_themes_match_theme_name_ssot() -> None:
    from ui.theme import ThemeName

    assert g4.THEMES == (ThemeName.DARK, ThemeName.LIGHT, ThemeName.NAVY, ThemeName.DRACULA)


def test_launch_config_sets_theme_and_skips_onboarding() -> None:
    config = g4.build_launch_config("navy")

    assert config["theme_name"] == "navy"
    assert config["onboarding_complete"] is True
    assert config["risk_disclaimer_acknowledged"] is True
    assert config["locale"] == "zh_CN"


def test_build_launch_config_rejects_unknown_theme() -> None:
    with pytest.raises(ValueError) as exc_info:
        g4.build_launch_config("solarized")
    assert "solarized" in str(exc_info.value)


def test_skia_backend_injects_no_impeller_and_impeller_clears_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FLET_NO_IMPELLER", "1")

    skia_env = g4.build_launch_env("dark", "skia", tmp_path / "settings.json")
    impeller_env = g4.build_launch_env("dark", "impeller", tmp_path / "settings.json")

    assert skia_env["FLET_NO_IMPELLER"] == "true"
    assert "FLET_NO_IMPELLER" not in impeller_env
    assert skia_env["ASTOCK_CONFIG_FILE"] == str(tmp_path / "settings.json")
    assert impeller_env["ASTOCK_CONFIG_FILE"] == str(tmp_path / "settings.json")


def test_scene_plan_covers_auto_plus_backend_specific_pages() -> None:
    impeller = g4.scene_plan("impeller")
    skia = g4.scene_plan("skia")

    assert impeller[: len(g4.AUTO_SCENES)] == g4.AUTO_SCENES
    assert skia[: len(g4.AUTO_SCENES)] == g4.AUTO_SCENES
    # §5.11：Skia 只需重跑图表、CodeEditor、中文输入、弹窗
    assert set(skia[len(g4.AUTO_SCENES) :]) == {"charts", "code_editor", "chinese_input", "dialog"}
    assert set(impeller[len(g4.AUTO_SCENES) :]) >= set(skia[len(g4.AUTO_SCENES) :])
    assert g4.scene_plan("skia", ("charts",)) == ("charts",)


def test_parse_choices_orders_by_allowed_and_rejects_unknown() -> None:
    assert g4.parse_choices("skia,impeller", g4.BACKENDS, "backends") == ("impeller", "skia")
    with pytest.raises(SystemExit) as exc_info:
        g4.parse_choices("impeller,vulkan", g4.BACKENDS, "backends")
    assert exc_info.value.code == 2


def test_hex_to_rgb_accepts_rgb_and_argb() -> None:
    assert g4.hex_to_rgb("#112233") == (0x11, 0x22, 0x33)
    assert g4.hex_to_rgb("FF112233") == (0x11, 0x22, 0x33)
    assert g4.hex_to_rgb("not-a-color") is None


def test_verify_theme_color_flags_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    surfaces = {"dark": "#1E1E1E", "light": "#FFFFFF", "navy": "#1E293B", "dracula": "#282A36"}
    monkeypatch.setattr(g4, "expected_surface_color", surfaces.get)

    matched = g4.verify_theme_color(Image.new("RGB", (128, 128), (0x1E, 0x29, 0x3B)), "navy")
    # NAVY 与 DRACULA 底色接近，必须靠「最接近主题」区分，不能因容差误判为通过
    mismatched = g4.verify_theme_color(Image.new("RGB", (128, 128), (0x28, 0x2A, 0x36)), "navy")

    assert matched["match"] is True
    assert matched["nearest_theme"] == "navy"
    assert mismatched["match"] is False
    assert mismatched["nearest_theme"] == "dracula"
    assert mismatched["reason"]
    assert matched["expected_surface"] == "#1E293B"


def test_verify_theme_color_records_reason_when_surface_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g4, "expected_surface_color", lambda _theme: None)

    result = g4.verify_theme_color(Image.new("RGB", (8, 8), (0, 0, 0)), "dark")

    assert result["match"] is None
    assert result["reason"]


def test_sha256_file_missing_returns_none(tmp_path: Path) -> None:
    assert g4.sha256_file(tmp_path / "missing.png") is None


def test_diff_images_identical_and_changed(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.png"
    identical = tmp_path / "identical.png"
    changed = tmp_path / "changed.png"
    Image.new("RGB", (10, 10), (10, 10, 10)).save(baseline)
    Image.new("RGB", (10, 10), (10, 10, 10)).save(identical)
    target = Image.new("RGB", (10, 10), (10, 10, 10))
    target.paste((200, 200, 200), (0, 0, 5, 2))  # 10 个像素
    target.save(changed)

    assert g4.diff_images(baseline, identical, 12)["changed_ratio"] == 0.0
    result = g4.diff_images(baseline, changed, 12)
    assert result["comparable"] is True
    assert result["changed_pixels"] == 10
    assert result["changed_ratio"] == pytest.approx(0.1)


def test_diff_images_size_mismatch_is_not_comparable(tmp_path: Path) -> None:
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(first)
    Image.new("RGB", (12, 10), (0, 0, 0)).save(second)

    result = g4.diff_images(first, second, 12)

    assert result["comparable"] is False
    assert result["reason"]


def test_compare_trees_marks_missing_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(baseline / "default.png")
    Image.new("RGB", (4, 4), (1, 2, 3)).save(candidate / "default.png")
    Image.new("RGB", (4, 4), (9, 9, 9)).save(candidate / "dialog.png")

    result = g4.compare_trees(baseline, candidate, 12)

    assert result["summary"]["identical"] == 1
    assert result["summary"]["missing_baseline"] == 1
    assert any(item["file"] == "dialog.png" and item["status"] == "missing_baseline" for item in result["items"])


def test_write_compare_report_emits_json_and_markdown(tmp_path: Path) -> None:
    result = {
        "baseline": "base",
        "candidate": "cand",
        "threshold": 12,
        "summary": {
            "total": 1,
            "identical": 0,
            "different": 1,
            "not_comparable": 0,
            "missing_baseline": 0,
            "missing_candidate": 0,
        },
        "items": [
            {
                "file": "dark/default.png",
                "status": "different",
                "detail": {"changed_ratio": 0.5, "max_channel_diff": 90},
            }
        ],
    }

    g4.write_compare_report(result, tmp_path)

    assert json.loads((tmp_path / "compare.json").read_text(encoding="utf-8")) == result
    markdown = (tmp_path / "compare.md").read_text(encoding="utf-8")
    assert "dark/default.png" in markdown
    assert "50.0000%" in markdown
