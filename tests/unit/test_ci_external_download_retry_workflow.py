"""CI：外部 Release 下载必须具备有界重试，且完整性校验不被重试绕过。

与 attestation 属同一失败类：单次外部网络调用失败即让整条 job 变红。盘点后仍无重试的
调用点有两个（其余同类已覆盖：``restore-cuda-wheel`` 有 3 次 2s/4s 退避回退，
``scripts/run_pip_audit.py`` 有传输层重试）：

- ``ci_cd.yml`` 的 ``Download sidecar binary``：embedded 变体取 sidecar 二进制与校验清单；
- ``finbert-train.yml`` 的 ``Download training corpus``：训练语料与校验清单。

两者共同要求，缺一即视为发出「可重试」的错误信号：
1. 有界重试（3 次）+ 退避，且每次重试前清理上一轮半截文件（否则 ``--clobber`` 与
   ``--skip-existing`` 语义会让损坏文件被误判为已完成）；
2. 重试不得替代校验：sidecar 仍须 SHA256 校验，语料仍须 ``sha256sum -c`` 与 manifest 校验；
3. 末次失败必须非零退出，不得 warning 后继续打包/训练（R21：不得把未校验伪装成已校验）。

测试只静态解析 workflow YAML，不执行下载、不产生网络副作用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"
FINBERT_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "finbert-train.yml"

SIDECAR_STEP = "Download sidecar binary (embedded variants only, Phase 5)"
CORPUS_STEP = "Download training corpus (Release) and verify integrity"


def _all_steps(workflow_path: Path) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def _step(workflow_path: Path, name: str) -> dict[str, Any]:
    matches = [step for step in _all_steps(workflow_path) if step.get("name") == name]
    assert len(matches) == 1, f"期望唯一步骤 {name!r}，实际 {len(matches)} 处"
    return matches[0]


def test_sidecar_download_has_bounded_retry_before_checksum() -> None:
    script = _step(CI_WORKFLOW_PATH, SIDECAR_STEP)["run"]
    loop = "for ($i = 1; $i -le 3; $i++)"
    assert loop in script
    # 重试前清掉半截文件，避免损坏下载被 SHA256 校验误报为供应链篡改。
    assert script.index("Remove-Item (Join-Path $targetDir $artifactName)") < script.index(
        "gh release download $tagName"
    )
    assert "Start-Sleep -Seconds $delay" in script
    # 有界重试后仍须失败退出，且校验必须发生在重试循环之后。
    assert "if (-not $downloadOk)" in script and "exit 1" in script
    checksum = script.index("Get-FileHash -Algorithm SHA256")
    assert script.index(loop) < checksum


def test_corpus_download_has_bounded_retry_before_verification() -> None:
    script = _step(FINBERT_WORKFLOW_PATH, CORPUS_STEP)["run"]
    loop = "for attempt in 1 2 3"
    assert "set -euo pipefail" in script
    assert loop in script
    # 重试前清空目录，避免 --clobber 跳过半截文件。
    assert script.index("rm -rf data/corpus") < script.index('gh release download "$CORPUS_TAG"')
    assert "sleep $((attempt * 5))" in script
    assert "exit 1" in script
    # 校验必须保留在重试之后，不得因重试而放宽。
    assert script.index(loop) < script.index("sha256sum -c SHA256SUMS.txt")
    assert "corpus_manifest" in script
