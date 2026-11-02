"""CI 工件脱敏契约测试（D18-R03）。

守护目标：
- `embedded-tests` job 在上传 `logs/e2e-flet-app.log` 前必须执行 R9 脱敏步骤；
- 脱敏步骤位于测试步骤之后、`Upload test reports` 之前，且 `if: always()`，
  保证测试失败路径同样脱敏；
- 脱敏步骤使用跨平台 `bash`（该 job matrix 同时含 ubuntu 与 windows runner）；
- 上传工件的名称与保留策略不得被脱敏改动改变；
- 新增的 `scripts/sanitize_artifacts.py` 复用 `DataSanitizer`，能对真实 DB
  连接串脱敏，且不误伤普通文本、文件缺失时不失败。

测试只读解析 YAML，并在临时目录内验证脚本行为，不发网络请求。
"""

from pathlib import Path

import pytest
import yaml

from scripts.sanitize_artifacts import main, sanitize_file

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"

SANITIZE_STEP_NAME = "Sanitize embedded test artifacts"
UPLOAD_STEP_NAME = "Upload test reports"
TEST_STEP_NAME = "Run E2E smoke subset (Linux only, review07 G16)"
LOG_PATH = "logs/e2e-flet-app.log"


def _embedded_steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert "embedded-tests" in workflow["jobs"], "ci_cd.yml 缺少 embedded-tests job"
    return workflow["jobs"]["embedded-tests"].get("steps", [])


def _step_index(steps: list[dict], name: str) -> int:
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index
    raise AssertionError(f"embedded-tests 缺少步骤: {name}")


def test_sanitize_step_precedes_upload() -> None:
    steps = _embedded_steps()
    sanitize_index = _step_index(steps, SANITIZE_STEP_NAME)
    upload_index = _step_index(steps, UPLOAD_STEP_NAME)
    test_index = _step_index(steps, TEST_STEP_NAME)

    assert test_index < sanitize_index, "脱敏步骤必须在测试步骤之后执行"
    assert sanitize_index < upload_index, "脱敏步骤必须在上传工件之前执行"


def test_sanitize_step_runs_always_with_cross_platform_shell() -> None:
    steps = _embedded_steps()
    step = steps[_step_index(steps, SANITIZE_STEP_NAME)]

    assert step.get("if") == "always()", "脱敏步骤必须 if: always() 覆盖测试失败路径"
    assert step.get("shell") == "bash", "embedded-tests matrix 含 windows，须用两平台均可用的 bash"


def test_sanitize_step_covers_log_and_junit_xml() -> None:
    steps = _embedded_steps()
    run = steps[_step_index(steps, SANITIZE_STEP_NAME)].get("run", "")

    assert "scripts/sanitize_artifacts.py" in run, "脱敏步骤必须复用 scripts/sanitize_artifacts.py"
    assert LOG_PATH in run, "脱敏步骤必须覆盖 embedded-tests 上传的日志"
    assert "junit-*.xml" in run, "若存在 junit XML 也应一并脱敏"


def test_upload_artifact_name_and_retention_unchanged() -> None:
    steps = _embedded_steps()
    upload = steps[_step_index(steps, UPLOAD_STEP_NAME)]

    assert upload["with"]["name"] == "embedded-test-reports-${{ matrix.os }}"
    assert upload["with"]["retention-days"] == 7
    assert upload["with"]["if-no-files-found"] == "ignore"
    assert LOG_PATH in upload["with"]["path"]


def test_sanitize_file_masks_postgres_dsn_password(tmp_path: Path) -> None:
    artifact = tmp_path / "e2e.log"
    secret = "s3cr3t_pa55"
    artifact.write_text(
        f"INFO connecting DSN=postgresql://qtrading_user:{secret}@127.0.0.1:55432/qtrading\n",
        encoding="utf-8",
    )

    assert sanitize_file(artifact) is True

    content = artifact.read_text(encoding="utf-8")
    assert content == "INFO connecting DSN=postgresql://qtrading_user:***@127.0.0.1:55432/qtrading\n", (
        "只遮蔽密码，保留 scheme/user/host 便于定位；且不得清空工件"
    )
    assert secret not in content


def test_sanitize_file_masks_compound_scheme_and_junit(tmp_path: Path) -> None:
    artifact = tmp_path / "junit-e2e.xml"
    secret = "my_db_password"
    artifact.write_text(
        f'<testcase name="connect"><system-out>postgresql+asyncpg://user:{secret}@host:5432/db</system-out></testcase>',
        encoding="utf-8",
    )

    assert sanitize_file(artifact) is True

    content = artifact.read_text(encoding="utf-8")
    assert secret not in content
    assert "postgresql+asyncpg://user:***@host:5432/db" in content


def test_sanitize_file_preserves_plain_text(tmp_path: Path) -> None:
    artifact = tmp_path / "plain.log"
    original = "普通文本没有敏感信息，服务启动正常。\n"
    artifact.write_text(original, encoding="utf-8")

    assert sanitize_file(artifact) is True
    assert artifact.read_text(encoding="utf-8") == original, "负例：普通文本不得被误伤"


def test_sanitize_file_skips_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.log"
    assert sanitize_file(missing) is False, "文件不存在时应跳过而非失败"
    assert not missing.exists()


def test_main_tolerates_unmatched_glob(tmp_path: Path) -> None:
    exit_code = main([str(tmp_path / "junit-*.xml"), str(tmp_path / "logs" / "*.log")])
    assert exit_code == 0, "无匹配文件时步骤不得失败"
