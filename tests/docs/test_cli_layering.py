"""D5：CLI warning/error 分层与退出码对齐（检视 F19 / AC08·L04）。

F19：CLI warning 与 CI 零基线不得互相背书——
- warning-only 的检查输出 `::warning::` 且**不得**导致 CLI 非零退出（不阻断）；
- 真实错误必须非零退出；
- 分级口径在检查脚本内与实际退出路径一致（warning 计数不进 errors 聚合）。

本文件验证 check_docs_consistency 的 warning 包装（治理 ID 对照表渐进部署）：
monkeypatch 注入 warning-only / error 两种返回，断言 main() 退出码分层。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_docs_consistency as cdc  # noqa: E402

pytestmark = pytest.mark.docs_pure


def _run_main(monkeypatch: pytest.MonkeyPatch, glossary_result: tuple[list[str], list[str]]) -> int:
    """以 governance scope 运行 main()，仅执行 warning 包装检查。"""
    monkeypatch.setattr(cdc, "check_governance_id_glossary", lambda: glossary_result)
    plan = [(frozenset({"governance"}), cdc._glossary_check_with_warnings)]
    return cdc.main(argv=["--check", "--scope", "governance"], plan=plan)


def test_warning_only_exit_zero(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    """warning-only（治理 ID 对照表渐进部署）→ 退出码 0，输出 ::warning:: 前缀。"""
    code = _run_main(monkeypatch, ([], ["W1: 示例未登记警告"]))
    out = capsys.readouterr().out
    assert code == 0, "warning-only 不得导致 CLI 失败（F19：不阻断）"
    assert "::warning::" in out, "warning 必须以 ::warning:: 分级输出而非错误通道"


def test_error_exit_one(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    """真实错误 → 退出码 1（warning 分级不吞错误）。"""
    code = _run_main(monkeypatch, (["E1: 示例错误"], []))
    err = capsys.readouterr().err
    assert code == 1, "真实错误必须非零退出"
    assert "E1: 示例错误" in err


def test_warning_layering_declared_in_docstring():
    """F19 口径一致：warning 包装的实现说明须声明「warning 不阻断、错误非零」分层。"""
    doc = cdc._glossary_check_with_warnings.__doc__ or ""
    assert "warning" in doc.lower(), "warning 包装缺少分级说明"
    assert "不阻断" in doc or "非零" in doc, "warning 包装须声明退出码分层口径"


# ---------------------------------------------------------------------------
# D33：治理 ID 对义检查的 error/warning 通道分离（真实入口四例）
# ---------------------------------------------------------------------------

SYNONYM_GLOSSARY = "| P2-07 | Metadata 统一格式 |\n| P2-07 | ｍｅｔａｄａｔａ　统一格式。 |\n"
UNDISCLOSED_GLOSSARY = "| P2-07 | 元数据统一格式 |\n| P2-07 | 例外注册格式 |\n"
DISCLOSED_GLOSSARY = "| P1-04 | 含义 A（另义（同名异义双义登记）） |\n| P1-04 | 含义 B |\n"


def _stub_other_scope_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离无关检查，仅保留真实对义检查：走生产 _SCOPE_CHECK_NAMES / build_scope_plan / main。"""
    for name in dir(cdc):
        if not name.startswith("check_") or name == "check_governance_id_dual_meaning":
            continue
        if not callable(getattr(cdc, name)):
            continue
        if name == "check_governance_id_glossary":
            monkeypatch.setattr(cdc, name, lambda: ([], []))
        else:
            monkeypatch.setattr(cdc, name, lambda: [])
    monkeypatch.setattr(cdc, "_glossary_check_with_warnings", lambda: [])


def _run_real_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, glossary_text: str) -> int:
    """以真实治理源 + 生产 scope 计划（build_scope_plan）运行 main()，返回退出码。"""
    glossary = tmp_path / "governance-ids.md"
    glossary.write_text(glossary_text, encoding="utf-8")
    monkeypatch.setattr(cdc, "GOVERNANCE_IDS_PATH", glossary)
    _stub_other_scope_checks(monkeypatch)
    return cdc.main(argv=["--check"], plan=cdc.build_scope_plan())


def test_dual_meaning_synonym_duplicate_cli_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """同义重复：归一化语义相同 → CLI 退出 0，且不得出现对义诊断（D33 核心反例）。"""
    code = _run_real_plan(monkeypatch, tmp_path, SYNONYM_GLOSSARY)
    captured = capsys.readouterr()
    assert code == 0, f"同义重复不得让 CLI 失败，got exit={code}, err={captured.err!r}"
    assert "对义(" not in captured.out + captured.err, f"同义重复不应报对义, got: {captured.out!r}"
    assert "::warning::" not in captured.out, f"同义重复不应产生 warning, got: {captured.out!r}"


def test_dual_meaning_undisclosed_cli_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """未披露双义：ERROR 通道 → CLI 退出 1（不降级为 warning）。"""
    code = _run_real_plan(monkeypatch, tmp_path, UNDISCLOSED_GLOSSARY)
    captured = capsys.readouterr()
    assert code == 1, "未披露双义必须非零退出"
    assert "未声明双义登记" in captured.err, f"未披露双义应走错误通道, got: {captured.err!r}"


def test_dual_meaning_disclosed_warning_only_cli_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """已披露双义（仅 warning）：``::warning::`` 输出且 CLI 退出 0（warning 不置退出码）。"""
    code = _run_real_plan(monkeypatch, tmp_path, DISCLOSED_GLOSSARY)
    captured = capsys.readouterr()
    assert code == 0, f"仅 warning 不得导致 CLI 失败，got exit={code}, err={captured.err!r}"
    assert "::warning::" in captured.out, f"已披露双义须以 ::warning:: 分级输出, got: {captured.out!r}"
    assert "[FAIL]" not in captured.err, f"仅 warning 不应走失败路径, got: {captured.err!r}"


def test_repo_glossary_baseline_has_no_dual_meaning_warning():
    """仓库零-warning 基线：真实 governance-ids.md 的对义检查无 error 亦无 warning。"""
    errors, warnings = cdc.check_governance_id_dual_meaning()
    assert errors == [], f"仓库基线不应有对义 ERROR, got: {errors}"
    assert warnings == [], f"仓库基线不应有对义 WARNING, got: {warnings}"
