"""CI 快速 lint 的 Ruff 版本一致性静态断言（D18-F03）。

守护目标：
- `ci_cd.yml` 的 `lint-fast` / `lint-fast-313` 两个 job 的
  `pip install ruff==<版本>` 精确 pin 必须等于 `requirements-dev.txt` 的锁定
  版本，避免快速 lint 使用与开发锁不一致的 Ruff 基线；
- workflow 内任意其他 job 的 `ruff==` pin 不得出现第三个版本（防漏改）；
- workflow 不得使用范围约束（`ruff>=` / `ruff~=` 等浮点版本）或未版本化
  的 bare `pip install ruff`；
- `pyproject.toml` dev 声明的 Ruff 下界必须被锁定版本满足（锁定版本不低于
  声明下界），二者共同构成可复现基线。

测试只读解析 YAML / TOML / requirements，不安装依赖、不执行 workflow。
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"
REQUIREMENTS_DEV_PATH = REPO_ROOT / "requirements-dev.txt"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

LINT_JOB_NAMES = ("lint-fast", "lint-fast-313")

_VERSION = r"[0-9][0-9A-Za-z.+!-]*"
_PIN_RE = re.compile(rf"^\s*ruff==({_VERSION})\s*$")
_FLOOR_RE = re.compile(rf"^\s*ruff>=({_VERSION})\s*$")
_RUN_PIN_RE = re.compile(rf"ruff==({_VERSION})")
_RUN_FLOATING_RE = re.compile(rf"ruff[<~^]=({_VERSION})")


def _version_key(version: str) -> tuple[int, ...]:
    """将版本号转为可比较的数值元组（仅覆盖本文件所需的数值前缀）。"""
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", version)
    assert match is not None, f"无法解析版本号: {version}"
    return tuple(int(part) for part in match.groups() if part is not None)


def _locked_dev_version() -> str:
    pins = [
        match.group(1)
        for line in REQUIREMENTS_DEV_PATH.read_text(encoding="utf-8").splitlines()
        if (match := _PIN_RE.match(line))
    ]
    assert len(pins) == 1, f"requirements-dev.txt 应恰有一处 ruff== 锁，实际: {pins}"
    return pins[0]


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _install_ruff_pins(workflow: dict, job_name: str) -> list[str]:
    job = workflow["jobs"][job_name]
    return [
        match.group(1)
        for step in job.get("steps", [])
        if isinstance((run := step.get("run")), str)
        for match in _RUN_PIN_RE.finditer(run)
    ]


@pytest.mark.parametrize("job_name", LINT_JOB_NAMES)
def test_lint_job_ruff_pin_matches_dev_lock(job_name: str) -> None:
    workflow = _load_workflow()
    assert job_name in workflow["jobs"], f"ci_cd.yml 缺少 job: {job_name}"

    pins = _install_ruff_pins(workflow, job_name)
    assert len(pins) == 1, f"{job_name} 应恰有一处 ruff== 精确 pin，实际: {pins}"
    assert pins[0] == _locked_dev_version()


def test_no_other_ruff_pin_in_workflow() -> None:
    workflow = _load_workflow()
    all_pins = {pin for job_name in workflow["jobs"] for pin in _install_ruff_pins(workflow, job_name)}
    assert all_pins == {_locked_dev_version()}, f"workflow 内存在不一致的 ruff pin: {sorted(all_pins)}"


def test_workflow_has_no_floating_ruff_constraint() -> None:
    workflow = _load_workflow()
    floating = {
        match.group(0)
        for job_name in workflow["jobs"]
        for step in workflow["jobs"][job_name].get("steps", [])
        if isinstance((run := step.get("run")), str)
        for match in _RUN_FLOATING_RE.finditer(run)
    }
    assert floating == set(), f"workflow 使用了浮动的 ruff 约束: {sorted(floating)}"


def test_pyproject_ruff_floor_satisfied_by_lock() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dev_requirements = pyproject["project"]["optional-dependencies"]["dev"]
    floors = [match.group(1) for requirement in dev_requirements if (match := _FLOOR_RE.match(requirement.strip()))]
    assert len(floors) == 1, f"pyproject dev 应恰有一处 ruff>= 下界，实际: {floors}"
    assert _version_key(_locked_dev_version()) >= _version_key(floors[0])
