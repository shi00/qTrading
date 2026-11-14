"""D37：固定 slugger 缺失/导入故障的具名诊断与环境偏离负例（AC15 / LA10、LA13）。

验收边界：
- 缺包、导入故障、UnicodeEncodeError 三类环境偏离必须各自具名输出，不能只靠
  CompatGithubSlugger 回退实例「仍能解析」来认证固定后端可用；
- 固定后端可导入时不产生该诊断；
- 兼容模式与固定后端的 slug 等价验收只在固定后端真实可用时执行，否则显式 skip，
  不得把 compat 结果当作等价证据。

隔离方式：环境偏离的分类发生在 ``docs_markdown`` 导入期，无法在同进程内重新触发，
故三类负例均以子进程 + ``sys.meta_path`` 拦截器受控注入（不改仓库文件、不依赖真实包）。
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import check_docs_consistency as cdc
import docs_markdown as dm

pytestmark = pytest.mark.docs_pure

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

# 受控注入：拦截 github_slugger 的导入并按模式抛出对应异常，隔离 sys.modules 影响。
_BLOCKER_CODE = """
import sys

class _SluggerBlocker:
    def __init__(self, mode):
        self.mode = mode

    def find_spec(self, name, path=None, target=None):
        if name != "github_slugger" and not name.startswith("github_slugger."):
            return None
        if self.mode == "missing-package":
            raise ModuleNotFoundError("No module named 'github_slugger'", name="github_slugger")
        if self.mode == "import-failure":
            raise ImportError("github_slugger 初始化失败（受控注入）")
        if self.mode == "unicode-error":
            raise UnicodeEncodeError("utf-8", "\\ud80a", 0, 1, "surrogates not allowed")
        raise AssertionError(f"未知注入模式: {self.mode}")

sys.meta_path.insert(0, _SluggerBlocker(__MODE__))

import docs_markdown as dm  # noqa: E402  受控注入后导入，使导入期分类命中
print(dm.SLUGGER_BACKEND)
print(dm.SLUGGER_BACKEND_DEVIATION)
print(dm.slugger_backend_diagnostic() or "")
"""

# 固定后端可用（假包）：验证「正常固定后端行为不变、不产生诊断」。
_FAKE_BACKEND_CODE = """
import docs_markdown as dm

print(dm.SLUGGER_BACKEND)
print(dm.SLUGGER_BACKEND_DEVIATION)
print(dm.slugger_backend_diagnostic() or "")
print(type(dm.create_slugger()).__module__)
print(dm.CompatGithubSlugger is not None)
"""

_FAKE_PACKAGE_SOURCE = """
class GithubSlugger:
    def __init__(self):
        self.reset()

    def reset(self):
        self._seen = {}

    def slug(self, value, maintain_case=False):
        return value
"""

# 等价验收用例（含检视点名的 setup/setup-1 与中文标题、重复标题序号）
_EQUIVALENCE_HEADINGS = (
    "Setup",
    "Setup",
    "setup-1",
    "安装 指南",
    "安装 指南",
    "A_B-C",
)


def _run_isolated(code: str, *, extra_pythonpath: list[Path] | None = None) -> subprocess.CompletedProcess[str]:
    """在隔离子进程运行受控代码，返回完成结果（PYTHONPATH 注入 scripts 与临时假包）。"""
    env = dict(os.environ)
    paths = [str(SCRIPTS_DIR), *(str(path) for path in extra_pythonpath or [])]
    existing = env.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def _deviation_report(mode: str) -> tuple[str, str, str]:
    """执行受控注入并返回（后端声明, 偏离类别, 诊断文本）。"""
    proc = _run_isolated(_BLOCKER_CODE.replace("__MODE__", repr(mode)))
    assert proc.returncode == 0, proc.stderr
    backend, deviation, diagnostic = proc.stdout.splitlines()
    assert backend == "compat", proc.stdout
    return backend, deviation, diagnostic


def test_missing_package_backend_deviation_is_named() -> None:
    """缺包负例：固定包未安装 → 具名 missing-package 诊断，明确不等于固定后端验收。"""
    _backend, deviation, diagnostic = _deviation_report("missing-package")
    assert deviation == "missing-package", diagnostic
    assert "未安装固定依赖 github-slugger" in diagnostic, diagnostic
    assert "ModuleNotFoundError" in diagnostic, diagnostic
    assert "SLUGGER_BACKEND=compat" in diagnostic, diagnostic
    assert "兼容模式运行不等同固定后端验收" in diagnostic, diagnostic


def test_import_failure_backend_deviation_is_named() -> None:
    """导入故障负例（包已安装但导入失败）→ 具名 import-failure，不与缺包混为一谈。"""
    _backend, deviation, diagnostic = _deviation_report("import-failure")
    assert deviation == "import-failure", diagnostic
    assert "已安装但导入失败" in diagnostic, diagnostic
    assert "ImportError" in diagnostic, diagnostic
    assert "兼容模式运行不等同固定后端验收" in diagnostic, diagnostic


def test_unicode_error_backend_deviation_is_named() -> None:
    """UnicodeEncodeError 负例（可控注入，非静态推断）→ 具名 unicode-error。"""
    _backend, deviation, diagnostic = _deviation_report("unicode-error")
    assert deviation == "unicode-error", diagnostic
    assert "UnicodeEncodeError" in diagnostic, diagnostic
    assert "0.0.3" in diagnostic, diagnostic
    assert "兼容模式运行不等同固定后端验收" in diagnostic, diagnostic


def test_fixed_backend_available_has_no_diagnostic(tmp_path: Path) -> None:
    """固定后端可导入时不产生诊断，且 create_slugger 返回包实现（正常行为未改变）。"""
    package_dir = tmp_path / "github_slugger"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text(_FAKE_PACKAGE_SOURCE, encoding="utf-8")

    proc = _run_isolated(_FAKE_BACKEND_CODE, extra_pythonpath=[tmp_path])
    assert proc.returncode == 0, proc.stderr
    backend, deviation, diagnostic, slugger_module, compat_present = proc.stdout.splitlines()

    assert backend == "github-slugger", proc.stdout
    assert deviation == "None", proc.stdout
    assert diagnostic == "", f"固定后端可用时不得产生环境偏离诊断: {diagnostic}"
    assert slugger_module == "github_slugger", slugger_module
    assert compat_present == "True", compat_present


# ---------------------------------------------------------------------------
# CLI 诊断通道：stderr 具名输出、不污染 stdout、不改变退出码语义
# ---------------------------------------------------------------------------


def test_cli_reports_deviation_on_stderr_without_changing_exit_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI 入口在 stderr 输出具名诊断；检查通过仍退出 0，stdout 结果不被污染。"""
    monkeypatch.setattr(cdc.docs_markdown, "SLUGGER_BACKEND_DEVIATION", "missing-package")
    monkeypatch.setattr(
        cdc.docs_markdown,
        "SLUGGER_BACKEND_DEVIATION_DETAIL",
        "ModuleNotFoundError: No module named 'github_slugger'",
    )
    assert cdc.main([], plan=[]) == 0
    captured = capsys.readouterr()
    assert "未安装固定依赖 github-slugger" in captured.err, captured.err
    assert "兼容模式运行不等同固定后端验收" in captured.err, captured.err
    assert "偏离固定依赖合同" not in captured.out, "环境诊断不得进入 stdout 结果"


def test_cli_deviation_diagnostic_does_not_mask_failures(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """环境诊断不改变失败语义：检查失败仍退出 1，且两类信息同时如实输出。"""
    monkeypatch.setattr(cdc.docs_markdown, "SLUGGER_BACKEND_DEVIATION", "import-failure")
    monkeypatch.setattr(cdc.docs_markdown, "SLUGGER_BACKEND_DEVIATION_DETAIL", "ImportError: 受控注入")
    plan = [(frozenset({"document"}), lambda: ["锚点检查失败"])]
    assert cdc.main([], plan=plan) == 1
    captured = capsys.readouterr()
    assert "已安装但导入失败" in captured.err, captured.err
    assert "锚点检查失败" in captured.err, captured.err


def test_cli_no_diagnostic_when_backend_fixed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """固定后端可用（无偏离）时 CLI 不输出环境诊断。"""
    monkeypatch.setattr(cdc.docs_markdown, "SLUGGER_BACKEND_DEVIATION", None)
    monkeypatch.setattr(cdc.docs_markdown, "SLUGGER_BACKEND_DEVIATION_DETAIL", "")
    assert cdc.docs_markdown.slugger_backend_diagnostic() is None
    assert cdc.main([], plan=[]) == 0
    captured = capsys.readouterr()
    assert captured.err == "", captured.err


# ---------------------------------------------------------------------------
# 等价验收（固定后端 vs compat）：仅在固定后端真实可用时执行
# ---------------------------------------------------------------------------


def test_compat_backend_known_slug_cases() -> None:
    """compat 已知用例如实固定（setup/setup-1 等），但不据此宣称固定后端等价。"""
    slugger = dm.CompatGithubSlugger()
    slugs = [slugger.slug(heading) for heading in ("Setup", "Setup", "安装 指南", "安装 指南")]
    assert slugs == ["setup", "setup-1", "安装-指南", "安装-指南-1"], slugs


def test_fixed_vs_compat_slug_equivalence_when_backend_available() -> None:
    """固定后端可用时逐项对比 slug；不可用则显式 skip（compat 不能自证等价）。"""
    try:
        fixed_slugger_class = importlib.import_module("github_slugger").GithubSlugger
    except (ImportError, UnicodeEncodeError) as exc:
        pytest.skip(
            f"固定后端 github-slugger 当前不可用（{type(exc).__name__}），等价验收未执行；"
            "compat 运行结果不得当作固定后端等价证据"
        )

    fixed = fixed_slugger_class()
    compat = dm.CompatGithubSlugger()
    paired = [(heading, compat.slug(heading), fixed.slug(heading)) for heading in _EQUIVALENCE_HEADINGS]
    # 已知用例与检视点名一致（compat 侧）
    assert [compat_slug for _heading, compat_slug, _fixed_slug in paired[:2]] == ["setup", "setup-1"], paired
    diffs = [
        (heading, compat_slug, fixed_slug) for heading, compat_slug, fixed_slug in paired if compat_slug != fixed_slug
    ]
    assert diffs == [], f"固定后端与 compat 的 slug 结果存在差异（如实记录，不掩盖）: {diffs}"
