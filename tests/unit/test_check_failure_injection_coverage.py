"""Tests for scripts/check_failure_injection_coverage.py (P3-Test-Scenario-Cross-Validation).

验证 Rust failure_injection.rs 测试场景编号与 reviews/pg_plan.md §17.6 矩阵场景编号的
cross-validation：
- 正例：Rust 11 个测试与矩阵一致时脚本通过（exit 0）
- 反例：Rust 缺 #3 时报告缺失（exit 1）
- 反例：Rust 多 #99 时报告冗余（exit 1）
- 兜底：§17.6 矩阵格式异常时友好报错（exit 2）

外部文件依赖通过 tmp_path 构造 mock 文件，避免依赖真实 reviews/pg_plan.md
（该文件被 .gitignore 忽略，不入仓，worktree/CI 中不存在）。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "check_failure_injection_coverage.py"


def _subprocess_env() -> dict[str, str]:
    """构造子进程环境，强制 UTF-8 IO 编码（Windows 默认 code page 可能不支持 §）。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# 真实仓库当前 Rust failure_injection.rs 的 11 个测试场景号
# （来源：sidecars/qtrading-pg-sidecar/tests/failure_injection.rs 头部注释
# "10 场景 + 1 对称补充：#3/#4/#8/#9/#23/#24/#26/#27/#28/#28b/#31"）
CURRENT_RUST_SCENARIOS = ["03", "04", "08", "09", "23", "24", "26", "27", "28", "28b", "31"]

# §17.6 矩阵中"应该由 Rust 集成测试覆盖"的场景号（归一化后，去掉前导 0 与 b 后缀）
# 与 CURRENT_RUST_SCENARIOS 归一化后一致：{3,4,8,9,23,24,26,27,28,31}
EXPECTED_MATRIX_SCENARIOS = ["3", "4", "8", "9", "23", "24", "26", "27", "28", "31"]


def _write_rust_file(path: Path, scenarios: list[str]) -> None:
    """构造 mock failure_injection.rs，每个场景号生成一个 `fn test_inject_NN_*` 函数。

    保留真实文件的 #[cfg(...)] 修饰与 #[test] 属性结构，确保解析正则覆盖。
    """
    lines = [
        "//! Rust sidecar 集成测试 — §17.6 失败注入场景。",
        "mod common;",
        "use common::*;",
        "",
    ]
    for sn in scenarios:
        lines.append(f"/// #{sn} 场景测试。")
        lines.append("#[test]")
        lines.append(f"fn test_inject_{sn}_scenario() {{")
        lines.append("    let _ = ();")
        lines.append("}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_pg_plan_file(path: Path, scenarios: list[str]) -> None:
    """构造 mock reviews/pg_plan.md，仅包含 §17.6 矩阵（前面有 §17.5，后面有 §18）。

    矩阵格式与真实 pg_plan.md §17.6 一致：
    | # | 测试场景 | 注入方式 | 预期行为 |
    """
    lines = [
        "# pg_plan.md (mock)",
        "",
        "### 17.5 安装包验证",
        "",
        "前置内容。",
        "",
        "### 17.6 失败注入测试",
        "",
        "验证异常路径的恢复能力。",
        "",
        "| # | 测试场景 | 注入方式 | 预期行为 |",
        "|---|----------|----------|----------|",
    ]
    for sn in scenarios:
        lines.append(f"| {sn} | 场景 {sn} | 注入 {sn} | 预期 {sn} |")
    lines.append("")
    lines.append("## 18. 风险与缓解")
    lines.append("")
    lines.append("| 风险 | 等级 | 缓解 |")
    lines.append("|---|---|---|")
    lines.append("| 示例 | 中 | 示例 |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _run_script(rust_path: Path, pg_plan_path: Path) -> subprocess.CompletedProcess[str]:
    """以子进程调用脚本，捕获 stdout/stderr 与退出码。"""
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--rust-path",
            str(rust_path),
            "--pg-plan-path",
            str(pg_plan_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
    )


# ============================================================================
# 正例：当前 11 场景一致 → exit 0
# ============================================================================


class TestCoverageConsistent:
    """正例：Rust 11 个测试（含 #28b 对称）与矩阵 10 个场景一致 → 脚本通过。"""

    def test_coverage_consistent(self, tmp_path: Path) -> None:
        rust_file = tmp_path / "failure_injection.rs"
        pg_plan_file = tmp_path / "pg_plan.md"
        _write_rust_file(rust_file, CURRENT_RUST_SCENARIOS)
        _write_pg_plan_file(pg_plan_file, EXPECTED_MATRIX_SCENARIOS)

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 0, (
            f"expected exit 0 for consistent coverage, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # 通过信息应包含 PASS 关键字与场景号摘要
        assert "pass" in result.stdout.lower(), f"expected pass message in stdout, got: {result.stdout}"


# ============================================================================
# 反例 1：Rust 缺 #3 → 报告缺失，exit 1
# ============================================================================


class TestCoverageMissingInRust:
    """反例：模拟 failure_injection.rs 缺失 #3 时，矩阵有 #3，脚本报告缺失。"""

    def test_coverage_missing_in_rust(self, tmp_path: Path) -> None:
        rust_scenarios = [s for s in CURRENT_RUST_SCENARIOS if s != "03"]
        rust_file = tmp_path / "failure_injection.rs"
        pg_plan_file = tmp_path / "pg_plan.md"
        _write_rust_file(rust_file, rust_scenarios)
        _write_pg_plan_file(pg_plan_file, EXPECTED_MATRIX_SCENARIOS)

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 1, (
            f"expected exit 1 for missing scenario, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # 报告应明确指出 #3 缺失
        combined = result.stdout + result.stderr
        assert "3" in combined, f"expected missing scenario #3 in report, got: {combined}"
        assert "missing" in combined.lower() or "缺失" in combined, (
            f"expected 'missing' keyword in report, got: {combined}"
        )


# ============================================================================
# 反例 2：Rust 多 #99 → 报告冗余，exit 1
# ============================================================================


class TestCoverageExtraInRust:
    """反例：模拟 failure_injection.rs 多出 #99 时，矩阵无 #99，脚本报告冗余。"""

    def test_coverage_extra_in_rust(self, tmp_path: Path) -> None:
        rust_scenarios = [*CURRENT_RUST_SCENARIOS, "99"]
        rust_file = tmp_path / "failure_injection.rs"
        pg_plan_file = tmp_path / "pg_plan.md"
        _write_rust_file(rust_file, rust_scenarios)
        _write_pg_plan_file(pg_plan_file, EXPECTED_MATRIX_SCENARIOS)

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 1, (
            f"expected exit 1 for extra scenario, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # 报告应明确指出 #99 冗余
        combined = result.stdout + result.stderr
        assert "99" in combined, f"expected extra scenario #99 in report, got: {combined}"
        assert "extra" in combined.lower() or "冗余" in combined, f"expected 'extra' keyword in report, got: {combined}"


# ============================================================================
# 兜底：§17.6 矩阵格式异常 → 友好报错，exit 2
# ============================================================================


class TestMalformedMarkdown:
    """兜底：pg_plan.md 中找不到 §17.6 矩阵时，脚本友好报错（exit 2）。"""

    def test_no_section_17_6(self, tmp_path: Path) -> None:
        rust_file = tmp_path / "failure_injection.rs"
        pg_plan_file = tmp_path / "pg_plan.md"
        _write_rust_file(rust_file, CURRENT_RUST_SCENARIOS)
        # pg_plan.md 没有 §17.6 章节
        pg_plan_file.write_text(
            "# pg_plan.md (mock)\n\n## 17. 测试计划\n\n无 §17.6 矩阵。\n",
            encoding="utf-8",
        )

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 2, (
            f"expected exit 2 for malformed markdown, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "17.6" in combined or "matrix" in combined.lower() or "§17.6" in combined, (
            f"expected §17.6/matrix mention in error, got: {combined}"
        )

    def test_no_matrix_table(self, tmp_path: Path) -> None:
        """§17.6 章节存在但无矩阵表格 → 报错。"""
        rust_file = tmp_path / "failure_injection.rs"
        pg_plan_file = tmp_path / "pg_plan.md"
        _write_rust_file(rust_file, CURRENT_RUST_SCENARIOS)
        pg_plan_file.write_text(
            "# pg_plan.md (mock)\n\n### 17.6 失败注入测试\n\n本章无矩阵表格。\n\n## 18. 风险\n",
            encoding="utf-8",
        )

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 2, (
            f"expected exit 2 for missing matrix table, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ============================================================================
# 文件不存在兜底
# ============================================================================


class TestFileNotFound:
    """rust/pg_plan 文件不存在时友好报错（exit 2）。"""

    def test_rust_file_not_found(self, tmp_path: Path) -> None:
        pg_plan_file = tmp_path / "pg_plan.md"
        _write_pg_plan_file(pg_plan_file, EXPECTED_MATRIX_SCENARIOS)
        rust_file = tmp_path / "nonexistent.rs"

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 2, (
            f"expected exit 2 for missing rust file, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "failure_injection" in combined.lower() or "rust" in combined.lower(), (
            f"expected rust file mention in error, got: {combined}"
        )

    def test_pg_plan_file_not_found(self, tmp_path: Path) -> None:
        rust_file = tmp_path / "failure_injection.rs"
        _write_rust_file(rust_file, CURRENT_RUST_SCENARIOS)
        pg_plan_file = tmp_path / "nonexistent.md"

        result = _run_script(rust_file, pg_plan_file)

        assert result.returncode == 2, (
            f"expected exit 2 for missing pg_plan file, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "pg_plan" in combined.lower() or "reviews" in combined.lower(), (
            f"expected pg_plan file mention in error, got: {combined}"
        )
