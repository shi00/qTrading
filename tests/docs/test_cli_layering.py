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
