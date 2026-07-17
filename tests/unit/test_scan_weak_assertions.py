"""Tests for scripts/scan_weak_assertions.py 弱断言扫描与增量门禁。

验证：
- 弱断言扫描核心逻辑（assert True / assert 1 / 空 test / Mock 弱断言）
- 带理由白名单（`# noqa: weak-assertion <reason>`）识别
- baseline 文件加载/序列化
- --base diff 模式：新增弱断言被检测
- baseline 数量只能下降不能上升（git ref 对比）
- --update-baseline 模式：用当前扫描结果覆盖 baseline
"""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from scan_weak_assertions import (  # noqa: E402 - sys.path 注入后导入
    WeakAssertion,
    is_whitelisted,
    load_baseline,
    make_signature,
    save_baseline,
    scan_directory,
    scan_file,
)


# ============================================================================
# 工具：构造临时测试目录
# ============================================================================


def _write_test_file(root: Path, rel: str, content: str) -> Path:
    """在 root 下创建 rel 路径的测试文件，返回绝对路径。"""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# ============================================================================
# 弱断言扫描核心逻辑
# ============================================================================


class TestScanFile:
    """scan_file 核心检测逻辑。"""

    def test_detects_assert_true(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        issues = scan_file(f)
        assert any(i.issue_type == "weak_assert" for i in issues)

    def test_detects_assert_one(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert 1\n")
        issues = scan_file(f)
        assert any(i.issue_type == "weak_assert" for i in issues)

    def test_ignores_assert_with_condition(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    x = 1\n    assert x == 1\n")
        issues = scan_file(f)
        assert issues == []

    def test_detects_empty_test_body(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    pass\n")
        issues = scan_file(f)
        assert any(i.issue_type == "empty_test" for i in issues)

    def test_detects_weak_mock_assert(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m()\n    m.assert_called()\n",
        )
        issues = scan_file(f)
        assert any(i.issue_type == "weak_mock" for i in issues)

    def test_strong_mock_assert_not_flagged(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    m = Mock()\n    m(1)\n    m.assert_called_with(1)\n",
        )
        issues = scan_file(f)
        assert not any(i.issue_type == "weak_mock" for i in issues)

    def test_scan_file_returns_weak_assertion_with_source_line(self, tmp_path):
        f = _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        issues = scan_file(f)
        assert len(issues) == 1
        issue = issues[0]
        assert isinstance(issue, WeakAssertion)
        assert issue.line_no == 2
        assert issue.source_line.strip() == "assert True"

    def test_scan_file_carries_relative_path(self, tmp_path):
        f = _write_test_file(tmp_path, "sub/test_x.py", "def test_a():\n    assert True\n")
        issues = scan_file(f, rel_path="sub/test_x.py")
        assert len(issues) == 1
        assert issues[0].rel_path == "sub/test_x.py"


# ============================================================================
# 白名单（行内 noqa）
# ============================================================================


class TestWhitelist:
    """`# noqa: weak-assertion <reason>` 行内白名单。"""

    def test_whitelisted_with_reason(self):
        line = "    assert True  # noqa: weak-assertion guard for smoke test"
        assert is_whitelisted(line) is True

    def test_whitelisted_with_multi_word_reason(self):
        line = "    assert True  # noqa: weak-assertion smoke guard"
        assert is_whitelisted(line) is True

    def test_not_whitelisted_without_reason(self):
        line = "    assert True  # noqa: weak-assertion"
        assert is_whitelisted(line) is False

    def test_not_whitelisted_without_comment(self):
        line = "    assert True"
        assert is_whitelisted(line) is False

    def test_not_whitelisted_unrelated_noqa(self):
        line = "    assert True  # noqa: E501"
        assert is_whitelisted(line) is False

    def test_scan_file_skips_whitelisted(self, tmp_path):
        f = _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    assert True  # noqa: weak-assertion smoke guard\n",
        )
        issues = scan_file(f)
        assert issues == []


# ============================================================================
# 签名
# ============================================================================


class TestSignature:
    """make_signature 生成 (rel_path, type, normalized_line) 三元组。"""

    def test_signature_strips_and_lowercases(self):
        sig = make_signature("tests/test_x.py", "weak_assert", "    assert True\n")
        assert sig == ("tests/test_x.py", "weak_assert", "assert true")

    def test_signature_stable_across_indentation(self):
        sig1 = make_signature("a.py", "weak_assert", "    assert True")
        sig2 = make_signature("a.py", "weak_assert", "\tassert True\n")
        assert sig1 == sig2


# ============================================================================
# baseline 加载与保存
# ============================================================================


class TestBaseline:
    """load_baseline / save_baseline 往返。"""

    def test_save_and_load_roundtrip(self, tmp_path):
        entries = [
            WeakAssertion(
                rel_path="tests/test_a.py",
                line_no=2,
                issue_type="weak_assert",
                detail="assert True / assert 1",
                source_line="    assert True",
            )
        ]
        path = tmp_path / "baseline.json"
        save_baseline(path, entries)
        assert path.exists()
        loaded = load_baseline(path)
        assert len(loaded) == 1
        assert loaded[0].rel_path == "tests/test_a.py"
        assert loaded[0].issue_type == "weak_assert"
        assert loaded[0].source_line.strip() == "assert True"

    def test_baseline_file_includes_total_field(self, tmp_path):
        entries = [
            WeakAssertion(
                rel_path="tests/test_a.py",
                line_no=2,
                issue_type="weak_assert",
                detail="d",
                source_line="    assert True",
            ),
            WeakAssertion(
                rel_path="tests/test_b.py",
                line_no=5,
                issue_type="empty_test",
                detail="d",
                source_line="    pass",
            ),
        ]
        path = tmp_path / "baseline.json"
        save_baseline(path, entries)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["total"] == 2
        assert len(data["entries"]) == 2

    def test_load_baseline_missing_file_returns_empty(self, tmp_path):
        loaded = load_baseline(tmp_path / "nonexistent.json")
        assert loaded == []

    def test_load_baseline_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        loaded = load_baseline(path)
        assert loaded == []


# ============================================================================
# --base diff 模式：新增检测
# ============================================================================


class TestDiffMode:
    """scan_directory + baseline 对比，识别新增弱断言。"""

    def test_diff_detects_new_weak_assertion(self, tmp_path):
        _write_test_file(tmp_path, "test_new.py", "def test_a():\n    assert True\n")
        # baseline 为空
        new_issues = _diff(tmp_path, baseline_entries=[])
        assert len(new_issues) == 1
        assert new_issues[0].rel_path == "test_new.py"

    def test_diff_ignores_existing_in_baseline(self, tmp_path):
        _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        baseline = [
            WeakAssertion(
                rel_path="test_x.py",
                line_no=2,
                issue_type="weak_assert",
                detail="d",
                source_line="    assert True",
            )
        ]
        new_issues = _diff(tmp_path, baseline)
        assert new_issues == []

    def test_diff_ignores_whitelisted(self, tmp_path):
        _write_test_file(
            tmp_path,
            "test_x.py",
            "def test_a():\n    assert True  # noqa: weak-assertion smoke\n",
        )
        new_issues = _diff(tmp_path, baseline_entries=[])
        assert new_issues == []

    def test_diff_line_drift_tolerant(self, tmp_path):
        """行号漂移不影响识别（按签名匹配）。"""
        _write_test_file(
            tmp_path,
            "test_x.py",
            "# comment\n# comment\n# comment\ndef test_a():\n    assert True\n",
        )
        baseline = [
            WeakAssertion(
                rel_path="test_x.py",
                line_no=2,  # 旧行号
                issue_type="weak_assert",
                detail="d",
                source_line="    assert True",
            )
        ]
        new_issues = _diff(tmp_path, baseline)
        assert new_issues == []

    def test_diff_reports_multiple_new(self, tmp_path):
        _write_test_file(
            tmp_path,
            "test_a.py",
            "def test_a():\n    assert True\n",
        )
        _write_test_file(
            tmp_path,
            "test_b.py",
            "def test_b():\n    assert 1\n",
        )
        new_issues = _diff(tmp_path, baseline_entries=[])
        assert len(new_issues) == 2


def _diff(root: Path, baseline_entries: list) -> list:
    """辅助：扫描 root 并对比 baseline，返回新增列表。"""
    from scan_weak_assertions import compute_new_issues

    current = scan_directory(root)
    return compute_new_issues(current=current, baseline=baseline_entries)


# ============================================================================
# baseline 数量只能下降
# ============================================================================


class TestBaselineShrink:
    """check_baseline_shrink：对比当前 baseline 与 git ref 旧 baseline。"""

    def test_shrink_ok_when_total_equal(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(
            path,
            [
                WeakAssertion(
                    rel_path="a.py",
                    line_no=1,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert True",
                )
            ],
        )
        # mock git show 返回同样 total 的 baseline
        old_content = json.dumps({"version": 1, "total": 1, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is True
        assert current_total == 1
        assert old_total == 1

    def test_shrink_ok_when_total_decreased(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(path, [])  # 当前 0
        old_content = json.dumps({"version": 1, "total": 3, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is True
        assert current_total == 0
        assert old_total == 3

    def test_shrink_fails_when_total_increased(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(
            path,
            [
                WeakAssertion(
                    rel_path="a.py",
                    line_no=1,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert True",
                ),
                WeakAssertion(
                    rel_path="b.py",
                    line_no=2,
                    issue_type="weak_assert",
                    detail="d",
                    source_line="assert 1",
                ),
            ],
        )  # 当前 2
        old_content = json.dumps({"version": 1, "total": 1, "entries": []})
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: old_content,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        assert ok is False
        assert current_total == 2
        assert old_total == 1

    def test_shrink_skipped_when_git_unavailable(self, tmp_path, monkeypatch):
        path = tmp_path / "baseline.json"
        save_baseline(path, [])
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: None,
        )
        from scan_weak_assertions import check_baseline_shrink

        ok, current_total, old_total = check_baseline_shrink(path, ref="origin/main")
        # git 不可用时跳过 shrink 检查，返回 ok=True，old_total=-1 表示未获取
        assert ok is True
        assert current_total == 0
        assert old_total == -1


# ============================================================================
# --update-baseline 模式
# ============================================================================


class TestUpdateBaseline:
    """--update-baseline 用当前扫描结果覆盖 baseline。"""

    def test_update_writes_current_state(self, tmp_path):
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        _write_test_file(
            tmp_path,
            "test_b.py",
            "def test_b():\n    assert True  # noqa: weak-assertion smoke\n",
        )
        baseline_path = tmp_path / "baseline.json"
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)
        loaded = load_baseline(baseline_path)
        # 白名单的不进 baseline（已被允许，不算存量弱断言）
        assert len(loaded) == 1
        assert loaded[0].rel_path == "test_a.py"

    def test_update_overwrites_existing(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        # 旧 baseline 有 3 条
        save_baseline(
            baseline_path,
            [
                WeakAssertion(
                    rel_path="old.py",
                    line_no=i,
                    issue_type="weak_assert",
                    detail="d",
                    source_line=f"assert {i}",
                )
                for i in range(3)
            ],
        )
        # 当前只有 1 条
        _write_test_file(tmp_path, "test_new.py", "def test_a():\n    assert True\n")
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)
        loaded = load_baseline(baseline_path)
        assert len(loaded) == 1
        assert loaded[0].rel_path == "test_new.py"


# ============================================================================
# 集成测试：main()
# ============================================================================


class TestMainIntegration:
    """main() 命令行入口的集成测试。"""

    def test_main_base_mode_fails_on_new(self, tmp_path, capsys, monkeypatch):
        _write_test_file(tmp_path, "test_new.py", "def test_a():\n    assert True\n")
        baseline_path = tmp_path / "baseline.json"
        save_baseline(baseline_path, [])
        # 跳过 shrink 检查
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: None,
        )
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--base",
                str(baseline_path),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "新增" in out or "new" in out.lower()

    def test_main_base_mode_passes_on_clean(self, tmp_path, capsys, monkeypatch):
        _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        baseline_path = tmp_path / "baseline.json"
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: json.dumps({"version": 1, "total": 1, "entries": []}),
        )
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--base",
                str(baseline_path),
            ]
        )
        assert rc == 0

    def test_main_base_mode_fails_on_baseline_growth(self, tmp_path, capsys, monkeypatch):
        _write_test_file(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
        _write_test_file(tmp_path, "test_y.py", "def test_b():\n    assert 1\n")
        baseline_path = tmp_path / "baseline.json"
        current = scan_directory(tmp_path)
        save_baseline(baseline_path, current)  # 当前 2
        monkeypatch.setattr(
            "scan_weak_assertions._git_show_file",
            lambda ref, file_rel: json.dumps({"version": 1, "total": 1, "entries": []}),
        )
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--base",
                str(baseline_path),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "baseline" in out.lower() or "上升" in out

    def test_main_update_baseline_writes_file(self, tmp_path, capsys):
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        baseline_path = tmp_path / "baseline.json"
        from scan_weak_assertions import main

        rc = main(
            [
                "--path",
                str(tmp_path),
                "--update-baseline",
                str(baseline_path),
            ]
        )
        assert rc == 0
        assert baseline_path.exists()
        loaded = load_baseline(baseline_path)
        assert len(loaded) == 1

    def test_main_advisory_mode_still_works(self, tmp_path, capsys):
        """原有 advisory 模式（不带 --strict）应保持向后兼容。"""
        _write_test_file(tmp_path, "test_a.py", "def test_a():\n    assert True\n")
        from scan_weak_assertions import main

        rc = main(["--path", str(tmp_path)])
        # advisory 模式退出码 0（仅警告）
        assert rc == 0
