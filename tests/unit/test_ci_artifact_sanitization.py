"""CI 工件脱敏契约测试（D18-R03）。

守护目标：
- 所有上传文本工件（`junit-*.xml` / `logs/e2e-flet-app.log` / `e2e-artifacts` /
  `sidecar-logs`）的 job，其 upload 步骤之前必须有 `if: always()` 的脱敏步骤；
- 三个 E2E 类 job 不再使用内联 pwsh 正则，统一调用跨平台
  `scripts/sanitize_artifacts.py`；脱敏步骤紧邻 upload 之前；
- 上传工件的名称与保留策略不得被脱敏改动改变；
- `scripts/sanitize_artifacts.py` 复用 `DataSanitizer`，支持目录递归与文本扩展名
  白名单：白名单文本就地脱敏、二进制字节完全不变、缺失路径不失败、普通文本不误伤。

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
SANITIZE_SCRIPT = "scripts/sanitize_artifacts.py"
ARTIFACT_PATH_MARKERS = ("junit-", LOG_PATH, "e2e-artifacts", "sidecar-logs")

# 每个应覆盖工件上传的 job 的脱敏命令（精确断言参数与顺序）
EXPECTED_SANITIZE_COMMANDS = {
    "ci-checks": f"python {SANITIZE_SCRIPT} 'junit-*.xml'",
    "ci-checks-windows": f"python {SANITIZE_SCRIPT} 'junit-*.xml'",
    "e2e-tests-windows": f"python {SANITIZE_SCRIPT} {LOG_PATH} 'junit-e2e.xml' e2e-artifacts",
    "embedded-tests": f"python {SANITIZE_SCRIPT} {LOG_PATH} 'junit-*.xml'",
    "windows-skip-revalidation": (
        f"python {SANITIZE_SCRIPT} {LOG_PATH} 'junit-skip-revalidation-${{{{ matrix.group }}}}.xml' e2e-artifacts"
    ),
    "e2e-sidecar-real-smoke": (
        f"python {SANITIZE_SCRIPT} {LOG_PATH} 'junit-sidecar-smoke.xml' e2e-artifacts sidecar-logs"
    ),
}

# 上传工件名称/保留策略快照（脱敏改造不得改变这些值）
EXPECTED_UPLOADS = {
    "ci-checks": (
        "test-reports-${{ runner.os }}-${{ matrix.python-version }}",
        14,
        ("coverage.xml", "coverage.json", "junit-*.xml"),
    ),
    "ci-checks-windows": ("test-reports-${{ runner.os }}", 14, ("junit-*.xml",)),
    "e2e-tests-windows": (
        "test-reports-e2e-windows-${{ matrix.group }}",
        14,
        ("junit-e2e.xml", "e2e-artifacts/", LOG_PATH),
    ),
    "windows-skip-revalidation": (
        "windows-skip-revalidation-${{ matrix.group }}",
        30,
        ("junit-skip-revalidation-${{ matrix.group }}.xml", "e2e-artifacts/", LOG_PATH),
    ),
    "e2e-sidecar-real-smoke": (
        "e2e-sidecar-real-smoke-reports",
        30,
        ("junit-sidecar-smoke.xml", "e2e-artifacts/", LOG_PATH, "sidecar-logs/"),
    ),
}


def _embedded_steps() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert "embedded-tests" in workflow["jobs"], "ci_cd.yml 缺少 embedded-tests job"
    return workflow["jobs"]["embedded-tests"].get("steps", [])


def _step_index(steps: list[dict], name: str) -> int:
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index
    raise AssertionError(f"embedded-tests 缺少步骤: {name}")


def _job_steps(job_name: str) -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert job_name in workflow["jobs"], f"ci_cd.yml 缺少 {job_name} job"
    return workflow["jobs"][job_name].get("steps", [])


def _sanitize_steps(steps: list[dict]) -> list[tuple[int, dict]]:
    return [(index, step) for index, step in enumerate(steps) if SANITIZE_SCRIPT in str(step.get("run", ""))]


def _artifact_upload_indexes(steps: list[dict]) -> list[int]:
    indexes = []
    for index, step in enumerate(steps):
        if not str(step.get("uses", "")).startswith("actions/upload-artifact"):
            continue
        path = str((step.get("with") or {}).get("path", ""))
        if any(marker in path for marker in ARTIFACT_PATH_MARKERS):
            indexes.append(index)
    return indexes


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
    run = steps[_step_index(steps, SANITIZE_STEP_NAME)].get("run", "").strip()

    assert run == EXPECTED_SANITIZE_COMMANDS["embedded-tests"]


def test_upload_artifact_name_and_retention_unchanged() -> None:
    steps = _embedded_steps()
    upload = steps[_step_index(steps, UPLOAD_STEP_NAME)]

    assert upload["with"]["name"] == "embedded-test-reports-${{ matrix.os }}"
    assert upload["with"]["retention-days"] == 7
    assert upload["with"]["if-no-files-found"] == "ignore"
    assert upload["with"]["path"].split() == [LOG_PATH]


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


def test_artifact_upload_jobs_are_fully_enumerated() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    actual_jobs = {name for name, job in workflow["jobs"].items() if _artifact_upload_indexes(job.get("steps", []))}

    assert actual_jobs == set(EXPECTED_SANITIZE_COMMANDS), "新增文本工件上传 job 时必须同步补齐脱敏契约"


@pytest.mark.parametrize("job_name", sorted(EXPECTED_SANITIZE_COMMANDS))
def test_each_artifact_upload_has_adjacent_always_sanitize_step(job_name: str) -> None:
    steps = _job_steps(job_name)
    upload_indexes = _artifact_upload_indexes(steps)
    assert upload_indexes, f"{job_name} 未发现上传文本工件的 upload 步骤"

    sanitize_steps = _sanitize_steps(steps)
    assert len(sanitize_steps) == 1, f"{job_name} 应只有一个脱敏步骤，实际 {len(sanitize_steps)}"
    sanitize_index, sanitize_step = sanitize_steps[0]

    for upload_index in upload_indexes:
        assert sanitize_index < upload_index, f"{job_name} 脱敏步骤必须在上传之前"
        assert sanitize_index == upload_index - 1, f"{job_name} 脱敏步骤必须紧邻 upload 之前"

    assert sanitize_step.get("if") == "always()", f"{job_name} 脱敏步骤必须 if: always()"
    assert sanitize_step.get("shell") == "bash", f"{job_name} 脱敏步骤须使用两平台可用的 bash"


@pytest.mark.parametrize("job_name", sorted(EXPECTED_SANITIZE_COMMANDS))
def test_sanitize_command_covers_expected_artifacts(job_name: str) -> None:
    runs = [str(step.get("run", "")).strip() for _, step in _sanitize_steps(_job_steps(job_name))]

    assert runs == [EXPECTED_SANITIZE_COMMANDS[job_name]], f"{job_name} 脱敏参数/顺序必须精确匹配"


@pytest.mark.parametrize(
    "job_name",
    ["e2e-tests-windows", "windows-skip-revalidation", "e2e-sidecar-real-smoke"],
)
def test_e2e_jobs_no_longer_use_inline_pwsh_regex(job_name: str) -> None:
    [(_, sanitize_step)] = _sanitize_steps(_job_steps(job_name))
    run = str(sanitize_step.get("run", ""))

    assert sanitize_step.get("shell") == "bash"
    assert "$pattern" not in run, f"{job_name} 不应保留内联 pwsh 正则变量"
    assert "Get-Content" not in run, f"{job_name} 不应保留内联 pwsh 文件改写"
    assert "-replace" not in run, f"{job_name} 不应保留内联替换表达式"


@pytest.mark.parametrize("job_name", sorted(EXPECTED_UPLOADS))
def test_upload_artifact_name_retention_and_paths_unchanged(job_name: str) -> None:
    expected_name, expected_retention, expected_paths = EXPECTED_UPLOADS[job_name]
    steps = _job_steps(job_name)
    upload_indexes = _artifact_upload_indexes(steps)
    assert len(upload_indexes) == 1, f"{job_name} 应只有一个含文本工件的 upload 步骤"

    upload = steps[upload_indexes[0]]["with"]
    assert upload["name"] == expected_name
    assert upload["retention-days"] == expected_retention
    actual_paths = [line.strip() for line in str(upload["path"]).splitlines() if line.strip()]
    assert actual_paths == list(expected_paths)


def test_main_recursively_sanitizes_whitelisted_text_only(tmp_path: Path) -> None:
    artifacts = tmp_path / "e2e-artifacts"
    nested = artifacts / "nested" / "deep"
    nested.mkdir(parents=True)

    html_file = artifacts / "seed-timeout.html"
    html_file.write_text("<pre>postgresql://user:html_secret@host:5432/db</pre>\n", encoding="utf-8")
    md_file = nested / "notes.md"
    md_file.write_text("DSN password=md_fixture_secret\n", encoding="utf-8")
    plain_text = nested / "plain.txt"
    plain_original = "普通文本没有敏感信息，服务启动正常。\n"
    plain_text.write_text(plain_original, encoding="utf-8")

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"postgresql://user:png_secret@host\n\x00\xff"
    png_file = artifacts / "screenshot.png"
    png_file.write_bytes(png_bytes)
    zip_bytes = b"PK\x03\x04" + b"postgresql://user:zip_secret@host\n"
    zip_file = artifacts / "bundle.zip"
    zip_file.write_bytes(zip_bytes)

    # 非白名单扩展名：即使内容是文本也不得读写
    cfg_file = artifacts / "server.cfg"
    cfg_original = "postgresql://user:cfg_secret@host:5432/db\n"
    cfg_file.write_text(cfg_original, encoding="utf-8")

    assert main([str(artifacts)]) == 0

    assert html_file.read_text(encoding="utf-8") == "<pre>postgresql://user:***@host:5432/db</pre>\n"
    assert md_file.read_text(encoding="utf-8") == "DSN password=***\n"
    assert plain_text.read_text(encoding="utf-8") == plain_original
    assert png_file.read_bytes() == png_bytes, "二进制 PNG 必须逐字节保持不变"
    assert zip_file.read_bytes() == zip_bytes, "二进制 zip 必须逐字节保持不变"
    assert cfg_file.read_text(encoding="utf-8") == cfg_original, "非白名单扩展名不得被改写"


def test_main_skips_missing_directory(tmp_path: Path) -> None:
    assert main([str(tmp_path / "missing-dir"), str(tmp_path / "missing" / "*.log")]) == 0
    assert not (tmp_path / "missing-dir").exists()


def test_sanitize_file_skips_binary_without_reading_or_rewriting(tmp_path: Path) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\npostgresql://user:binary_secret@host\n\x00\xff"
    artifact = tmp_path / "binary.png"
    artifact.write_bytes(png_bytes)

    assert sanitize_file(artifact) is False
    assert artifact.read_bytes() == png_bytes


def test_sanitize_file_skips_non_whitelisted_text_extension(tmp_path: Path) -> None:
    artifact = tmp_path / "config.cfg"
    original = "postgresql://user:cfg_secret@host:5432/db\n"
    artifact.write_text(original, encoding="utf-8")

    assert sanitize_file(artifact) is False
    assert artifact.read_text(encoding="utf-8") == original


def test_sanitize_file_preserves_undecodable_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "logs" / "broken.log"
    artifact.parent.mkdir()
    artifact.write_bytes(b"prefix\xff postgresql://user:byte_secret@host:5432/db\n")

    assert sanitize_file(artifact) is True

    sanitized = artifact.read_bytes()
    assert sanitized == b"prefix\xff postgresql://user:***@host:5432/db\n", (
        "非法 UTF-8 字节须经 surrogateescape 原样保留，仅脱敏密码"
    )
