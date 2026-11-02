"""GitHub Actions 引用必须固定到不可变 commit SHA（D18-R01）。

守护目标：
- 全仓库 ``.github/workflows/*.yml`` 中每一处外部 ``uses:`` 引用都必须以
  40 位小写十六进制 commit SHA 结尾，不得使用可变 ref（分支 / tag / 语义版本）。
  可变 ref 可被上游移动（``stable`` 分支前移、``v1`` tag 被重打），使 CI 实际执行
  的 action 代码与评审时不一致，构成供应链绕过面。
- ``dtolnay/rust-toolchain`` 曾以 ``@stable`` 分支引用（含 ``sidecar.yml`` 中
  ``contents: write`` 的 release job），须固定为 ``stable`` 分支当时的不可变 SHA，
  并保留 ``# stable`` 注释（Dependabot 以该注释识别升级基线）；各处的
  ``with: toolchain:`` 输入保持不变。
- 本地复用 workflow（``./`` 前缀）与 ``docker://`` 引用不在此断言范围。

测试只读解析 YAML 文本，不执行 workflow、不访问网络。
"""

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_FILES = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))

# 不可变引用：GitHub 上 commit SHA 为 40 位小写十六进制。
_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_USES_LINE_RE = re.compile(r"^\s*uses:\s+", re.MULTILINE)
# dtolnay/rust-toolchain 固定形态：SHA + 保留的人类可读注释（Dependabot 升级基线）。
_RUST_TOOLCHAIN_LINE_RE = re.compile(r"uses:\s+dtolnay/rust-toolchain@([0-9a-f]{40})\s+#\s*stable\s*")


def _collect_uses(node: object, location: str = "") -> list[tuple[str, str]]:
    """递归收集 YAML 中全部 ``uses`` 值及其路径（job / step 层级均可）。"""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                found.append((f"{location}.{key}", value))
            else:
                found.extend(_collect_uses(value, f"{location}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_collect_uses(value, f"{location}[{index}]"))
    return found


def _load_uses(workflow_path: Path) -> list[tuple[str, str]]:
    return _collect_uses(yaml.safe_load(workflow_path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("workflow_path", WORKFLOW_FILES, ids=lambda path: path.name)
def test_external_action_refs_are_immutable_commit_shas(workflow_path: Path) -> None:
    """每一处外部 ``uses`` 都必须以 40 位 commit SHA 结尾，禁止分支 / tag 等可变 ref。"""
    mutable_refs = []
    for location, uses in _load_uses(workflow_path):
        if uses.startswith("./") or uses.startswith("docker://"):
            continue
        assert "@" in uses, f"{workflow_path.name}{location}: 外部引用缺少 @ref: {uses!r}"
        ref = uses.rsplit("@", 1)[1]
        if not _COMMIT_SHA_RE.fullmatch(ref):
            mutable_refs.append(f"{location}: {uses}")
    assert not mutable_refs, (
        f"{workflow_path.name} 存在非 40 位 SHA 的可变 action 引用（上游移动即可改变 CI 行为）: {mutable_refs}"
    )


def test_pin_scan_covers_every_uses_line() -> None:
    """防遍历漏检：YAML 解析到的 uses 数量须等于非注释 uses 行数（结构变更须同步本测试）。"""
    parsed_total = 0
    raw_total = 0
    for workflow_path in WORKFLOW_FILES:
        parsed_total += len(_load_uses(workflow_path))
        raw_total += len(_USES_LINE_RE.findall(workflow_path.read_text(encoding="utf-8")))
    assert parsed_total == raw_total, (
        f"uses 行解析数量与原文不一致（解析 {parsed_total} / 原文 {raw_total}）: 可能存在本测试未覆盖的 workflow 结构"
    )


def test_rust_toolchain_refs_are_sha_pinned_and_annotated() -> None:
    """dtolnay/rust-toolchain 必须固定为 SHA，并保留 ``# stable`` 可读性注释。"""
    rust_toolchain_lines = [
        (workflow_path.name, line.strip())
        for workflow_path in WORKFLOW_FILES
        for line in workflow_path.read_text(encoding="utf-8").splitlines()
        if "dtolnay/rust-toolchain@" in line
    ]
    # 工作流结构变更（移除 Rust 构建）须同步修订本测试，避免空集合恒真通过。
    assert rust_toolchain_lines, "未找到任何 dtolnay/rust-toolchain 引用"
    for workflow_name, line in rust_toolchain_lines:
        assert _RUST_TOOLCHAIN_LINE_RE.search(line), (
            f"{workflow_name}: dtolnay/rust-toolchain 引用未固定为 40 位 SHA 或缺少 '# stable' 注释: {line!r}"
        )
