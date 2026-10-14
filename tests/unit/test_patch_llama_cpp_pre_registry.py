"""Tests for scripts/patch_llama_cpp_pre_registry.py（FinBERT2-base pre-tokenizer 登记注入）。

覆盖纯函数路径（单测环境无需真实 llama.cpp 仓库）：
- 正常注入（合成 base.py 片段 → 含目标 chkhsh 条目且位于锚点之前，ast 可解析）；
- 幂等重入（二次调用返回 already-patched，不重复注入）；
- 锚点缺失拒绝（上游结构漂移 → ValueError，不写文件）；
- 目标文件缺失拒绝（FileNotFoundError）；
- CLI 退出码契约（成功 0 / 锚点漂移 1）；
- 注入块常量与实测 chkhsh / pre 名一致（防手误漂移）。

定因背景见脚本 docstring：FinBERT2-base chkhsh 在 llama.cpp v0.3.0~v0.6.0 均未登记，
导出时 get_vocab_base_pre() 链尾 raise；本脚本在 CI 内对 checkout 的 v0.3.0 注入登记。
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import patch_llama_cpp_pre_registry as plpr  # noqa: E402

# 合成最小 conversion/base.py 片段：类→方法→8 空格登记链（与 v0.3.0 实测嵌套逐字一致）
_BASE_SNIPPET = (
    "import logging\n"
    "\n"
    "\n"
    "class BertModel:\n"
    "    def get_vocab_base_pre(self, tokenizer):\n"
    "        chkhsh = tokenizer.compute()\n"
    "        res = None\n"
    '        if chkhsh == "8e62295832751ca1e8f92f2226f403dea30dc5165e448b5bfa05af5340c64ec7":\n'
    '            res = "bert-bge-large"\n'
    "        if res is None:\n"
    '            logger.warning("\\n")\n'
    '            raise NotImplementedError("BPE pre-tokenizer was not recognized")\n'
)


def _write_base(tmp_path: Path, content: str = _BASE_SNIPPET) -> Path:
    base_file = tmp_path / "base.py"
    base_file.write_text(content, encoding="utf-8")
    return base_file


def test_patch_inserts_registration_before_raise(tmp_path: Path) -> None:
    """正常注入：登记条目含目标 chkhsh 与 pre 名，且插在链尾 raise 锚点之前。"""
    base_file = _write_base(tmp_path)
    assert plpr.patch_base_file(base_file) == "patched"
    patched = base_file.read_text(encoding="utf-8")
    assert f'if chkhsh == "{plpr.FINBERT_CHKHSH}":' in patched
    assert f'res = "{plpr.FINBERT_PRE_NAME}"' in patched
    assert "valuesimplex-ai-lab/FinBERT2-base" in patched
    # 注入条目位于锚点（if res is None）之前：保证命中链尾判断前生效
    assert patched.index(f'if chkhsh == "{plpr.FINBERT_CHKHSH}"') < patched.index("if res is None:")
    # 注入后仍为合法 Python（文件级 ast 校验）
    ast.parse(patched)


def test_patch_is_idempotent(tmp_path: Path) -> None:
    """幂等：已含目标 chkhsh 的文件二次调用返回 already-patched 且不重复注入。"""
    base_file = _write_base(tmp_path)
    assert plpr.patch_base_file(base_file) == "patched"
    once = base_file.read_text(encoding="utf-8")
    assert plpr.patch_base_file(base_file) == "already-patched"
    assert base_file.read_text(encoding="utf-8") == once
    assert once.count(plpr.FINBERT_CHKHSH) == 1  # 仅 if 判断 1 处（注释不含完整 hash）


def test_patch_rejects_missing_anchor(tmp_path: Path) -> None:
    """锚点缺失（上游结构漂移）→ ValueError，且文件保持原样不被破坏。"""
    drifted = _BASE_SNIPPET.replace("        if res is None:\n", "        if not res:\n")
    base_file = _write_base(tmp_path, drifted)
    with pytest.raises(ValueError, match="注入锚点未命中"):
        plpr.patch_base_file(base_file)
    assert plpr.FINBERT_CHKHSH not in base_file.read_text(encoding="utf-8")


def test_patch_rejects_missing_file(tmp_path: Path) -> None:
    """目标文件缺失 → FileNotFoundError，且异常消息携带缺失路径。"""
    missing = tmp_path / "no-such-base.py"
    with pytest.raises(FileNotFoundError, match="目标文件不存在") as excinfo:
        plpr.patch_base_file(missing)
    assert str(missing) in str(excinfo.value)


def test_cli_exit_codes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """CLI 契约：成功返回 0；锚点漂移返回 1 且记录 ERROR（不抛裸异常）。"""
    base_file = _write_base(tmp_path)
    assert plpr.main(["--base-file", str(base_file)]) == 0

    drifted = tmp_path / "drifted.py"
    drifted.write_text(_BASE_SNIPPET.replace("if res is None:", "if not res:"), encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="scripts.patch_llama_cpp_pre_registry"):
        assert plpr.main(["--base-file", str(drifted)]) == 1
    assert "patch 失败" in caplog.text


def test_constants_match_verified_values() -> None:
    """常量防漂移：chkhsh / pre 名与 2026-10-14 实测定因结论一致。"""
    assert plpr.FINBERT_CHKHSH == ("20a9d25f0d2de78daab377e5cab1ef5da96b5868f9f94891f011c0715c45cd9d")
    assert plpr.FINBERT_PRE_NAME == "bert-bge-large"
    # 锚点必须为「链尾判断 + 首条 warning」两行连用（v0.3.0 实测唯一）
    assert plpr._INSERT_ANCHOR == '        if res is None:\n            logger.warning("\\n")\n'
