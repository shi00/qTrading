"""CI 选择与门禁结构的独立轻量元检查（检视 D22 / F21 / AC16 / LA12）。

背景：既有合同测试（``tests/docs/test_consistency.py`` 的
``TestDocsWorkflowMergeQueueContract``、``tests/unit/test_ci_aux_gate_workflow.py``）
只断言触发、job 名称、并发组与权限，不读取验证 job 的实际步骤；因此把
``docs-ci.yml`` 中真实执行的 ``run`` 替换成 ``echo``，这些合同仍全部通过。仅改
workflow 时主分类器判 ``code=false``（``scripts/classify_changed_paths.py``），
这些合同测试也不会被选中，缺口无法由未选中的单元测试兜底。

本脚本对真实 workflow 文件做静态 + 语义解析，并由 ``ci-aux-gate.yml`` 的独立必达
job ``ci-meta-contract`` 执行（每次 PR / merge_group 稳定运行），覆盖四类负例：

1. **删除必需文件**：验证 job 选中的检查脚本 / 测试文件 / pytest 配置必须真实存在；
2. **``run`` 被替换为 ``echo``**：必需命令必须由真实执行的 step 承载，纯 ``echo`` / 注释
   文本、``--collect-only`` / ``--co`` 只收集不执行均不构成执行证据；
3. **禁用必要 job / step**：验证 job 不得被 job 级 ``if: false`` 关闭，承载必需命令的
   step 也不得被 step 级 ``if: false`` 等常量假值关闭；聚合 job 必须 ``needs`` 两侧；
4. **吞失败**：``run`` 中出现 ``|| true`` / ``|| :`` / ``set +e``，或步骤
   ``continue-on-error: true``，聚合 job 缺少 ``exit 1`` 均拒绝。

同时断言本检查自身已接入辅助门必达聚合（job 存在且命令一致、聚合 ``needs`` 含该
job、非 ``success`` 即失败），避免检查被静默摘除后仍保持绿色。

用法::

    python scripts/check_ci_meta_contract.py [--root <仓库根>]

退出码：0 = 合同满足；1 = 存在违规（逐条打印 ``FAIL:``）。
"""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

WORKFLOWS_DIR = Path(".github") / "workflows"
DOCS_CI_NAME = "docs-ci.yml"
AUX_GATE_NAME = "ci-aux-gate.yml"

DOCS_STATIC_JOB = "docs-static"
DOCS_CONTRACTS_JOB = "docs-application-contracts"
DOCS_AGGREGATE_JOB = "docs-check"

META_JOB_ID = "ci-meta-contract"
META_SCRIPT = "scripts/check_ci_meta_contract.py"
AUX_AGGREGATE_JOB = "ci-aux-gate"

# 吞失败形态（含前后空白容忍的简单文本匹配）。
SWALLOW_MARKERS: tuple[str, ...] = ("|| true", "|| :", "set +e")

# 只把参数当文本输出、不执行参数内容的 shell 前缀（不能充当执行证据）。
OUTPUT_ONLY_COMMAND_PREFIXES: tuple[str, ...] = ("echo", "printf")

# pytest 的只收集不执行开关。
COLLECT_ONLY_FLAGS: frozenset[str] = frozenset({"--collect-only", "--co"})

# step 级 `if` 的常量假值（GitHub Actions 表达式：false / 0）。
DISABLED_STEP_CONDITIONS: frozenset[str] = frozenset({"false", "0"})

# 不改变被调用命令的前置 shell 修饰词。
SHELL_QUALIFIERS: frozenset[str] = frozenset({"command", "builtin", "exec", "time"})


@dataclass(frozen=True)
class JobContract:
    """单个验证 job 的最小选择合同。"""

    job_id: str
    # 归一化空白后必须出现于该 job 全部 run 文本中的命令片段。
    required_commands: tuple[str, ...]
    # 必须真实存在的仓库相对路径。
    required_files: tuple[str, ...]


DOCS_JOB_CONTRACTS: tuple[JobContract, ...] = (
    JobContract(
        job_id=DOCS_STATIC_JOB,
        required_commands=(
            "python scripts/check_docs_consistency.py",
            "-m pytest -c tests/docs/pytest.ini --confcutdir=tests/docs tests/docs",
        ),
        required_files=(
            "scripts/check_docs_consistency.py",
            "tests/docs/pytest.ini",
            "tests/docs/test_consistency.py",
        ),
    ),
    JobContract(
        job_id=DOCS_CONTRACTS_JOB,
        required_commands=(
            "-m pytest tests/unit/test_docs_consistency.py "
            "tests/unit/test_docs_template_symbols.py "
            "tests/unit/test_docs_accessibility_baseline_api.py",
        ),
        required_files=(
            "tests/unit/test_docs_consistency.py",
            "tests/unit/test_docs_template_symbols.py",
            "tests/unit/test_docs_accessibility_baseline_api.py",
        ),
    ),
)


def _load_yaml(path: Path) -> dict[str, Any]:
    """BaseLoader 保留 ``on`` 字面键（safe_load 会按 YAML 1.1 解析为布尔 True）。"""
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(data, dict):
        raise ValueError(f"workflow 顶层应为 mapping: {path}")
    return data


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _job_runs(job: dict[str, Any]) -> list[str]:
    runs: list[str] = []
    steps = job.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                run = step.get("run")
                if isinstance(run, str):
                    runs.append(run)
    return runs


def _strip_shell_comment(line: str) -> str:
    """去掉行内 shell 注释（引号内、``${var#pattern}`` 等词内的 ``#`` 不算注释）。"""
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&("):
            return line[:index]
    return line


def _split_shell_segments(line: str) -> list[str]:
    """按引号外的 ``&&`` / ``||`` / ``;`` 拆分命令，引号内的分隔符视为普通文本。"""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
        elif char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
        elif line.startswith(("&&", "||"), index):
            segments.append("".join(current))
            current = []
            index += 2
        elif char == ";":
            segments.append("".join(current))
            current = []
            index += 1
        else:
            current.append(char)
            index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _shell_commands(run_text: str) -> list[str]:
    """按 shell 行与分隔符拆出命令，合并续行并丢弃注释。

    逐条返回（不跨命令拼接），使必需命令只能来自单条真实命令，而不是
    ``python -m pytest; tests/docs`` 这类被分隔符切碎的文本；引号内的命令文本
    （如 ``echo "python -m pytest ..."``）也不会被拆成「可执行命令」。
    """
    lines: list[str] = []
    pending = ""
    for raw in run_text.splitlines():
        line = _strip_shell_comment(raw).strip()
        if not line:
            continue
        if line.endswith("\\"):
            pending = f"{pending} {line[:-1]}".strip()
            continue
        lines.append(f"{pending} {line}".strip() if pending else line)
        pending = ""
    if pending:
        lines.append(pending)

    commands: list[str] = []
    for line in lines:
        commands.extend(_split_shell_segments(line))
    return commands


def _tokenize(command: str) -> list[str]:
    """按 shell 引号规则分词；引号不配对时退化为空白分词。"""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_output_only_command(command: str) -> bool:
    """echo / printf 只打印参数文本，不执行被打印的命令。

    跳过前置的环境变量赋值与 ``command`` / ``builtin`` / ``exec`` / ``time``，
    避免 ``FOO=1 echo "python -m pytest ..."``、``CMD="python -m pytest ..."``
    被误认为执行证据。没有可执行 token（纯赋值）同样不构成执行证据。
    """
    tokens = _tokenize(command)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        is_assignment = "=" in token and token.split("=", 1)[0].isidentifier()
        if token in SHELL_QUALIFIERS or is_assignment:
            index += 1
            continue
        break
    return index >= len(tokens) or tokens[index] in OUTPUT_ONLY_COMMAND_PREFIXES


def _executed_commands(run_text: str) -> list[str]:
    return [command for command in _shell_commands(run_text) if not _is_output_only_command(command)]


def _is_noop_run(run_text: str) -> bool:
    """run 块去掉空行/注释后全部是 echo/printf（或被替换为占位 echo）即视为空操作。"""
    commands = _shell_commands(run_text)
    return bool(commands) and all(_is_output_only_command(command) for command in commands)


def _disabled_step_condition(step: dict[str, Any]) -> str | None:
    """step 级 ``if`` 被写成常量假值（``false`` / ``0``）时返回原条件。"""
    condition = step.get("if")
    if not isinstance(condition, str):
        return None
    normalized = _normalize(condition)
    if normalized.startswith("${{") and normalized.endswith("}}"):
        normalized = normalized[3:-2].strip()
    normalized = normalized.strip("\"'").strip().lower()
    return condition if normalized in DISABLED_STEP_CONDITIONS else None


def _collect_only_flags(command: str) -> set[str]:
    return {token for token in _tokenize(command) if token in COLLECT_ONLY_FLAGS}


def _check_required_step(errors: list[str], label: str, job_id: str, job: dict[str, Any], command: str) -> None:
    """必需命令必须由该 job 内真实执行（未被关闭、非只收集）的 step 承载。"""
    needle = _normalize(command)
    candidates: list[tuple[int, dict[str, Any], list[str]]] = []
    steps = job.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            matched = [segment for segment in _executed_commands(run) if needle in _normalize(segment)]
            if matched:
                candidates.append((index, step, matched))

    if not candidates:
        errors.append(f"{label}: job `{job_id}` 未选择必需命令 `{needle}`（echo/注释不构成执行证据）")
        return

    problems: list[str] = []
    for index, step, matched in candidates:
        condition = _disabled_step_condition(step)
        if condition is not None:
            problems.append(f"step[{index}] 被 `if: {condition}` 关闭")
            continue
        collect_flags = {flag for segment in matched for flag in _collect_only_flags(segment)}
        if "pytest" in needle and collect_flags:
            problems.append(f"step[{index}] 使用 {'/'.join(sorted(collect_flags))} 只收集不执行")
            continue
        return
    errors.append(f"{label}: job `{job_id}` 的必需命令 `{needle}` 缺少真实执行证据：{'；'.join(problems)}")


def _check_job_not_disabled(errors: list[str], label: str, job_id: str, job: dict[str, Any]) -> None:
    condition = job.get("if")
    if isinstance(condition, str) and "false" in condition.lower():
        errors.append(f"{label}: 验证 job `{job_id}` 被条件关闭（if: {condition}），结果将不再代表真实执行")
    if not _job_runs(job):
        errors.append(f"{label}: 验证 job `{job_id}` 缺少可执行的 run 步骤（被禁用或清空）")


def _check_swallow(errors: list[str], label: str, job_id: str, job: dict[str, Any]) -> None:
    steps = job.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            continue_on_error = step.get("continue-on-error")
            if isinstance(continue_on_error, str) and continue_on_error.strip().lower() == "true":
                errors.append(f"{label}: job `{job_id}` step[{index}] 设置 continue-on-error: true，失败被吞掉")
            run = step.get("run")
            if isinstance(run, str):
                for marker in SWALLOW_MARKERS:
                    if marker in run:
                        errors.append(f"{label}: job `{job_id}` step[{index}] 含吞失败标记 `{marker}`")


def _check_docs_job(errors: list[str], root: Path, jobs: dict[str, Any], contract: JobContract) -> None:
    job = jobs.get(contract.job_id)
    if not isinstance(job, dict):
        errors.append(f"{DOCS_CI_NAME}: 缺少验证 job `{contract.job_id}`（job 图被删改）")
        return

    _check_job_not_disabled(errors, DOCS_CI_NAME, contract.job_id, job)
    _check_swallow(errors, DOCS_CI_NAME, contract.job_id, job)

    runs = _job_runs(job)
    if runs and all(_is_noop_run(run) for run in runs):
        errors.append(f"{DOCS_CI_NAME}: job `{contract.job_id}` 的 run 全部为空操作（如被替换为 echo），未执行真实检查")

    for command in contract.required_commands:
        _check_required_step(errors, DOCS_CI_NAME, contract.job_id, job, command)

    for rel in contract.required_files:
        if not (root / rel).is_file():
            errors.append(f"必需文件缺失: `{rel}`（被 `{contract.job_id}` 选中但不存在）")

    if not job.get("runs-on"):
        errors.append(f"{DOCS_CI_NAME}: job `{contract.job_id}` 缺少 runs-on")


def _check_docs_workflow(errors: list[str], root: Path, data: dict[str, Any]) -> None:
    triggers = data.get("on")
    if not isinstance(triggers, dict):
        errors.append(f"{DOCS_CI_NAME}: 缺少 `on` 触发块")
    else:
        for event in ("pull_request", "merge_group"):
            block = triggers.get(event)
            if not isinstance(block, dict):
                errors.append(f"{DOCS_CI_NAME}: 缺少 `{event}` 触发（required check 可能停在 Pending）")
                continue
            if "paths" in block:
                errors.append(f"{DOCS_CI_NAME}: `{event}` 触发使用 paths 过滤，未命中时 required check 不出现")

    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        errors.append(f"{DOCS_CI_NAME}: 缺少 `jobs` 块")
        return

    for contract in DOCS_JOB_CONTRACTS:
        _check_docs_job(errors, root, jobs, contract)

    aggregate = jobs.get(DOCS_AGGREGATE_JOB)
    if not isinstance(aggregate, dict):
        errors.append(f"{DOCS_CI_NAME}: 缺少聚合 job `{DOCS_AGGREGATE_JOB}`")
        return

    aggregate_needs = aggregate.get("needs")
    if not isinstance(aggregate_needs, list) or set(aggregate_needs) != {DOCS_STATIC_JOB, DOCS_CONTRACTS_JOB}:
        errors.append(
            f"{DOCS_CI_NAME}: 聚合 job `{DOCS_AGGREGATE_JOB}` 的 needs 必须精确为 "
            f"[{DOCS_STATIC_JOB}, {DOCS_CONTRACTS_JOB}]，实际 {aggregate_needs!r}"
        )

    aggregate_if = aggregate.get("if")
    if not isinstance(aggregate_if, str) or "always()" not in aggregate_if:
        errors.append(f"{DOCS_CI_NAME}: 聚合 job 必须 `if: always()`，否则上游失败会以 skipped 伪装通过")

    aggregate_run = _normalize("\n".join(_job_runs(aggregate)))
    if '"success"' not in aggregate_run and "'success'" not in aggregate_run:
        errors.append(f"{DOCS_CI_NAME}: 聚合 job 未要求各验证 job 结果为 success")
    if "exit 1" not in aggregate_run:
        errors.append(f"{DOCS_CI_NAME}: 聚合 job 缺少 `exit 1`，失败不会传播为门禁失败")
    if "fail=1" not in aggregate_run:
        errors.append(f"{DOCS_CI_NAME}: 聚合 job 缺少 `fail=1` 失败累计")
    _check_swallow(errors, DOCS_CI_NAME, DOCS_AGGREGATE_JOB, aggregate)

    # 验证 job 不得反向依赖聚合 job，避免图被改成自证。
    for contract in DOCS_JOB_CONTRACTS:
        job = jobs.get(contract.job_id)
        if isinstance(job, dict) and DOCS_AGGREGATE_JOB in (job.get("needs") or []):
            errors.append(f"{DOCS_CI_NAME}: job `{contract.job_id}` 反向依赖聚合 job `{DOCS_AGGREGATE_JOB}`")


def _collect_env(job: dict[str, Any]) -> dict[str, str]:
    """聚合 job 的 env 可写在 job 级或 step 级，两者合并判定。"""
    env: dict[str, str] = {}
    job_env = job.get("env")
    if isinstance(job_env, dict):
        env.update({str(k): str(v) for k, v in job_env.items()})
    steps = job.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("env"), dict):
                env.update({str(k): str(v) for k, v in step["env"].items()})
    return env


def _check_aux_gate_wiring(errors: list[str], data: dict[str, Any]) -> None:
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        errors.append(f"{AUX_GATE_NAME}: 缺少 `jobs` 块")
        return

    meta_job = jobs.get(META_JOB_ID)
    if not isinstance(meta_job, dict):
        errors.append(f"{AUX_GATE_NAME}: 缺少独立元检查 job `{META_JOB_ID}`")
        return

    _check_job_not_disabled(errors, AUX_GATE_NAME, META_JOB_ID, meta_job)
    _check_swallow(errors, AUX_GATE_NAME, META_JOB_ID, meta_job)
    _check_required_step(errors, AUX_GATE_NAME, META_JOB_ID, meta_job, f"python {META_SCRIPT}")
    if _job_runs(meta_job) and all(_is_noop_run(run) for run in _job_runs(meta_job)):
        errors.append(f"{AUX_GATE_NAME}: job `{META_JOB_ID}` 的 run 为空操作，元检查未真实执行")

    aggregate = jobs.get(AUX_AGGREGATE_JOB)
    if not isinstance(aggregate, dict):
        errors.append(f"{AUX_GATE_NAME}: 缺少聚合 job `{AUX_AGGREGATE_JOB}`")
        return

    needs = aggregate.get("needs")
    if not isinstance(needs, list) or META_JOB_ID not in needs:
        errors.append(f"{AUX_GATE_NAME}: 聚合 job `{AUX_AGGREGATE_JOB}` 的 needs 未包含 `{META_JOB_ID}`")

    env = _collect_env(aggregate)
    if env.get("META_RESULT") != f"${{{{ needs.{META_JOB_ID}.result }}}}":
        errors.append(
            f"{AUX_GATE_NAME}: 聚合 job 未以 `META_RESULT: ${{{{ needs.{META_JOB_ID}.result }}}}` 读取元检查结果"
        )

    aggregate_run = _normalize("\n".join(_job_runs(aggregate)))
    if '"$META_RESULT" != "success"' not in aggregate_run:
        errors.append(f"{AUX_GATE_NAME}: 聚合 job 未把 `$META_RESULT` 非 success 判为失败")
    if "exit 1" not in aggregate_run:
        errors.append(f"{AUX_GATE_NAME}: 聚合 job 缺少 `exit 1`，元检查失败不会阻断门禁")


def check_repo(root: Path) -> list[str]:
    """对给定仓库根执行全部元合同检查，返回错误列表（空列表即通过）。"""
    errors: list[str] = []
    workflows_dir = root / WORKFLOWS_DIR

    docs_ci_path = workflows_dir / DOCS_CI_NAME
    aux_gate_path = workflows_dir / AUX_GATE_NAME
    for path in (docs_ci_path, aux_gate_path):
        if not path.is_file():
            errors.append(f"workflow 文件缺失: `{path.relative_to(root).as_posix()}`")
    if errors:
        return errors

    _check_docs_workflow(errors, root, _load_yaml(docs_ci_path))
    _check_aux_gate_wiring(errors, _load_yaml(aux_gate_path))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI 选择与门禁结构元检查（D22）")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根目录（默认脚本所在仓库）")
    args = parser.parse_args(argv)

    errors = check_repo(args.root.resolve())
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("CI meta contract: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
