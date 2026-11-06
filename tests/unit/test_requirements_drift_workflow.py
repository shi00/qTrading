"""CI-F04：requirements 漂移「PR 只检测、main-only 写回」契约。

守护目标（CI-F04，P2）：
- ``requirements-drift`` 在代码类 PR、push 到 main、任意 ``workflow_dispatch`` 上
  继续检测；
- ``update-requirements`` 仅在 push 到 main 或 main 上的 ``workflow_dispatch``
  且 ``drift=true``、非 Dependabot 时运行；同仓 PR / fork PR / 非 main dispatch
  一律不写回（不给予 PR 场景写权限）；
- 写回 job 的 checkout 显式落在 main 分支，自动 PR 的 ``base`` 固定为 main、
  分支名与标题可预测；
- 自动 PR body 保留 ``.github/PULL_REQUEST_TEMPLATE.md`` 的全部章节；
- requirements 重编译命令与漂移判定逻辑保持不变。

全部为 workflow YAML 的静态断言，不触发任何 PR / 写操作副作用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci_cd.yml"
TEMPLATE_PATH = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"

DRIFT_JOB = "requirements-drift"
UPDATE_JOB = "update-requirements"
CREATE_PR_STEP = "Create PR for requirements update"
CHECKOUT_STEP = "Checkout Code"
RECOMPILE_STEP = "Recompile requirements"
TARGET_BASE = "main"
TARGET_BRANCH = "chore/auto-requirements-sync"
TARGET_TITLE = "chore: sync requirements*.txt"

_TOKEN_RE = re.compile(r"'[^']*'|&&|\|\||==|!=|\(|\)|[A-Za-z_][A-Za-z0-9_.\-]*")

_GITHUB_KEYS = (
    "needs.requirements-drift.outputs.drift",
    "needs.changes.outputs.code",
    "github.event.pull_request.head.repo.full_name",
    "github.event_name",
    "github.actor",
    "github.ref",
    "github.repository",
)


class _ExpressionParser:
    """解析 GitHub Actions ``if`` 用到的布尔/比较子集（不依赖 eval）。"""

    def __init__(self, expression: str, scenario: dict[str, str]) -> None:
        self._tokens = self._tokenize(expression)
        self._scenario = scenario
        self._pos = 0

    @staticmethod
    def _tokenize(expression: str) -> list[str]:
        tokens: list[str] = []
        pos = 0
        while pos < len(expression):
            if expression[pos].isspace():
                pos += 1
                continue
            match = _TOKEN_RE.match(expression, pos)
            assert match is not None, f"无法解析 if 表达式片段: {expression[pos:]!r}"
            tokens.append(match.group(0))
            pos = match.end()
        return tokens

    def parse(self) -> bool:
        value = self._parse_or()
        assert self._pos == len(self._tokens), f"if 表达式未完全解析: {self._tokens[self._pos :]!r}"
        return bool(value)

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        assert self._peek() is not None, "if 表达式意外结束"
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _parse_or(self) -> bool | str:
        value = self._parse_and()
        while self._peek() == "||":
            self._next()
            # 先解析右操作数再合并：Python ``or`` 的短路会跳过解析，导致 token 未消费。
            right = self._parse_and()
            value = bool(value) or bool(right)
        return value

    def _parse_and(self) -> bool | str:
        value = self._parse_comparison()
        while self._peek() == "&&":
            self._next()
            right = self._parse_comparison()
            value = bool(value) and bool(right)
        return value

    def _parse_comparison(self) -> bool | str:
        left = self._parse_operand()
        operator = self._peek()
        if operator in ("==", "!="):
            self._next()
            right = self._parse_operand()
            return left == right if operator == "==" else left != right
        return left

    def _parse_operand(self) -> bool | str:
        token = self._next()
        if token == "(":
            value = self._parse_or()
            assert self._next() == ")", "if 表达式括号未闭合"
            return value
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        return self._scenario.get(token, "")


def _eval_if(expression: str, scenario: dict[str, str]) -> bool:
    return _ExpressionParser(expression, scenario).parse()


def _scenario(
    event_name: str,
    *,
    ref: str = "refs/heads/feature/example",
    actor: str = "contributor",
    head_repo: str = "shi00/qTrading",
    drift: str = "true",
    code: str = "true",
) -> dict[str, str]:
    return {
        "github.event_name": event_name,
        "github.ref": ref,
        "github.actor": actor,
        "github.repository": "shi00/qTrading",
        "github.event.pull_request.head.repo.full_name": head_repo,
        "needs.requirements-drift.outputs.drift": drift,
        "needs.changes.outputs.code": code,
    }


SCENARIOS: tuple[tuple[str, dict[str, str], bool, bool], ...] = (
    ("main push + drift", _scenario("push", ref="refs/heads/main"), True, True),
    ("main push + no drift", _scenario("push", ref="refs/heads/main", drift="false"), True, False),
    ("main dispatch + drift", _scenario("workflow_dispatch", ref="refs/heads/main"), True, True),
    ("main dispatch + no drift", _scenario("workflow_dispatch", ref="refs/heads/main", drift="false"), True, False),
    ("same-repo PR + code", _scenario("pull_request", ref="refs/pull/7/merge"), True, False),
    ("same-repo PR + non-code", _scenario("pull_request", ref="refs/pull/7/merge", code="false"), False, False),
    (
        "fork PR + code",
        _scenario("pull_request", ref="refs/pull/8/merge", head_repo="fork-owner/qTrading"),
        True,
        False,
    ),
    ("Dependabot PR", _scenario("pull_request", ref="refs/pull/9/merge", actor="dependabot[bot]"), True, False),
    ("non-main dispatch + drift", _scenario("workflow_dispatch", ref="refs/heads/feature/example"), True, False),
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """加载 CI workflow 的 YAML 结构（仅解析，不执行）。"""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    assert name in workflow["jobs"], f"ci_cd.yml 缺少 job {name}"
    return workflow["jobs"][name]


def _step(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    steps = _job(workflow, job)["steps"]
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1, f"{job} 中应恰好有一个步骤 {name}"
    return matches[0]


def _job_if(workflow: dict[str, Any], name: str) -> str:
    job = _job(workflow, name)
    assert "if" in job, f"{name} 缺少 if 条件"
    return str(job["if"])


@pytest.mark.parametrize("scenario_name,scenario,detect_expected,write_expected", SCENARIOS)
def test_detection_and_write_back_matrix(
    workflow: dict[str, Any],
    scenario_name: str,
    scenario: dict[str, str],
    detect_expected: bool,
    write_expected: bool,
) -> None:
    """逐场景断言：漂移检测是否运行、写回 job 是否允许运行。"""
    detect = _eval_if(_job_if(workflow, DRIFT_JOB), scenario)
    write = _eval_if(_job_if(workflow, UPDATE_JOB), scenario)
    assert detect == detect_expected, f"{scenario_name}: requirements-drift 检测判定不符"
    assert write == write_expected, f"{scenario_name}: update-requirements 写回判定不符"


def test_write_back_is_limited_to_main(workflow: dict[str, Any]) -> None:
    """写回条件必须显式包含 main push 与 main dispatch 两个分支。"""
    write_if = _job_if(workflow, UPDATE_JOB)
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in write_if
    assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in write_if
    assert "needs.requirements-drift.outputs.drift == 'true'" in write_if


def test_existing_security_guards_are_retained(workflow: dict[str, Any]) -> None:
    """Dependabot 与 fork PR 守卫保留为纵深防御。"""
    write_if = _job_if(workflow, UPDATE_JOB)
    assert "github.actor != 'dependabot[bot]'" in write_if
    assert "github.event.pull_request.head.repo.full_name == github.repository" in write_if


def test_pr_events_have_read_only_detection_permissions(workflow: dict[str, Any]) -> None:
    """检测 job 只读；写权限仅保留给 main-only 的写回 job。"""
    assert _job(workflow, DRIFT_JOB)["permissions"] == {"contents": "read"}
    assert _job(workflow, UPDATE_JOB)["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_update_checks_out_main_and_targets_main(workflow: dict[str, Any]) -> None:
    """checkout 落在 main 分支，自动 PR 的 base / 分支 / 标题可预测。"""
    checkout = _step(workflow, UPDATE_JOB, CHECKOUT_STEP)
    assert checkout["with"]["ref"] == TARGET_BASE
    with_ = _step(workflow, UPDATE_JOB, CREATE_PR_STEP)["with"]
    assert with_["base"] == TARGET_BASE
    assert with_["branch"] == TARGET_BRANCH
    assert with_["title"] == TARGET_TITLE


def test_auto_pr_body_keeps_full_template_sections(workflow: dict[str, Any]) -> None:
    """自动 PR body 必须保留仓库 PR 模板的全部章节标题。"""
    body = _step(workflow, UPDATE_JOB, CREATE_PR_STEP)["with"]["body"]
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    expected_headings = [line.strip() for line in template.splitlines() if line.startswith("## ")]
    assert expected_headings, "PR 模板应包含 H2 章节"
    missing = [heading for heading in expected_headings if heading not in body]
    assert not missing, f"自动 PR body 缺少模板章节: {missing}"
    assert "### 自动化测试" in body
    assert "### 手动验证" in body


def test_recompile_commands_and_drift_check_are_unchanged(workflow: dict[str, Any]) -> None:
    """要求不改变 requirements 重编译命令与漂移判定逻辑。"""
    commands = (
        "uv pip compile --universal --no-emit-index-url pyproject.toml -o requirements.txt",
        "uv pip compile --universal --no-emit-index-url --extra dev pyproject.toml -o requirements-dev.txt",
        "uv pip compile --universal --no-emit-index-url --extra optional pyproject.toml -o requirements-optional.txt",
    )
    for job in (DRIFT_JOB, UPDATE_JOB):
        run = _step(workflow, job, RECOMPILE_STEP)["run"]
        for command in commands:
            assert command in run, f"{job} 缺少原有重编译命令: {command}"
    drift_run = _step(workflow, DRIFT_JOB, "Check for drift")["run"]
    assert "git diff --exit-code requirements.txt requirements-dev.txt requirements-optional.txt" in drift_run
