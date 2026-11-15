"""Flet 日检脱敏安全门契约测试（D22-004）。

守护目标：
- `flet-nightly.yml` 的 `Sanitize E2E diagnostics` 步骤必须有稳定 id、`if: always()`，
  且调用跨平台 `scripts/sanitize_artifacts.py`；
- 其后的 `Upload E2E diagnostics` 必须同时满足 `always()` 与
  `steps.<id>.outcome == 'success'`，即脱敏失败/取消/跳过时不得回退上传未脱敏原始日志（R9）；
- 上传工件的名称、保留策略与路径不得被改动；
- 脱敏写失败时保持原文并抛错（使步骤非零退出），与上面的 outcome 门共同阻断上传。

测试只读解析 YAML 并在临时目录内验证脚本行为，不联网、不读取真实密钥。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.sanitize_artifacts import sanitize_file

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "flet-nightly.yml"

JOB_NAME = "flet-compatibility-check"
SANITIZE_STEP_NAME = "Sanitize E2E diagnostics"
UPLOAD_STEP_NAME = "Upload E2E diagnostics"
SANITIZE_STEP_ID = "sanitize_e2e_diagnostics"
SANITIZE_COMMAND = "python scripts/sanitize_artifacts.py logs/e2e-flet-app.log logs/main_trace.log"
UPLOAD_PATH = ["logs/e2e-flet-app.log", "logs/main_trace.log"]


def _steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert JOB_NAME in workflow["jobs"], f"flet-nightly.yml 缺少 {JOB_NAME} job"
    return workflow["jobs"][JOB_NAME].get("steps", [])


def _step(named: str) -> tuple[int, dict]:
    for index, step in enumerate(_steps()):
        if step.get("name") == named:
            return index, step
    raise AssertionError(f"{JOB_NAME} 缺少步骤: {named}")


def test_sanitize_step_is_always_and_has_stable_id() -> None:
    _, step = _step(SANITIZE_STEP_NAME)

    assert step.get("id") == SANITIZE_STEP_ID, "脱敏步骤必须有稳定 id，供 upload 条件引用"
    assert step.get("if") == "always()", "脱敏步骤必须 if: always() 覆盖测试失败路径"
    assert step.get("shell") == "bash"
    assert str(step.get("run", "")).strip() == SANITIZE_COMMAND


def test_upload_runs_only_after_successful_sanitize() -> None:
    sanitize_index, _ = _step(SANITIZE_STEP_NAME)
    upload_index, upload = _step(UPLOAD_STEP_NAME)

    assert sanitize_index == upload_index - 1, "脱敏步骤必须紧邻 upload 之前"
    condition = str(upload.get("if", ""))
    assert condition == f"always() && steps.{SANITIZE_STEP_ID}.outcome == 'success'", (
        "upload 须保留 always() 覆盖测试失败路径，并仅在脱敏成功后执行；脱敏失败/取消/跳过时不得回退上传未脱敏原始日志"
    )


def test_upload_artifact_contract_unchanged() -> None:
    _, upload = _step(UPLOAD_STEP_NAME)
    with_ = upload["with"]

    assert with_["name"] == "flet-nightly-e2e-diagnostics"
    assert with_["retention-days"] == 7
    assert with_["if-no-files-found"] == "ignore"
    assert [line.strip() for line in str(with_["path"]).splitlines() if line.strip()] == UPLOAD_PATH


def test_step_ids_are_unique_within_job() -> None:
    ids = [step.get("id") for step in _steps() if step.get("id")]

    assert len(ids) == len(set(ids)), "重复步骤 id 会使 steps.<id> 引用失效"


def test_write_failure_keeps_original_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只读目标导致写失败：原文保持不变且异常传播（步骤失败 → upload 门关闭）。"""
    artifact = tmp_path / "e2e-flet-app.log"
    original = "DSN=postgresql://qtrading_user:synthetic_marker_secret@127.0.0.1:55432/qtrading\n"
    artifact.write_text(original, encoding="utf-8")

    def _deny_write(self: Path, data: bytes) -> int:
        raise PermissionError("read-only artifact target")

    monkeypatch.setattr(Path, "write_bytes", _deny_write)

    with pytest.raises(PermissionError) as excinfo:
        sanitize_file(artifact)
    assert "read-only artifact target" in str(excinfo.value)
    assert artifact.read_text(encoding="utf-8") == original, "写失败时原文（含 token-like marker）不得被截断或半脱敏"
