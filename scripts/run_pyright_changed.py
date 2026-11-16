"""pre-commit hook: 对 staged .py 文件跑 pyright，error 级别阻断。

用法（由 pre-commit 调用，文件名作为参数传入）::

    python scripts/run_pyright_changed.py <file1> <file2> ...

仅检查传入的文件，error 级别诊断返回 1 阻断提交，warning 不阻断。
与 CI 全量 ``pyright`` 互补：CI 守护全局类型一致性，本 hook 守护本地秒级反馈。

退出码:
    0: 无 error（warning 不阻断）
    1: 发现 error 级别诊断，或 pyright 以退出码 1 报告失败
    2: pyright 调用失败（未安装/配置错误/输出结构异常）
"""

import json
import subprocess
import sys
from pathlib import Path


def _resolve_venv_python(project_root: Path) -> Path | None:
    """定位 .venv 内的 Python 解释器，支持 worktree 共享主工作区 .venv。

    R18 要求新特性在 worktree 中隔离开发，worktree 无独立 .venv，
    共享主工作区（``.worktrees`` 的父目录）的 ``.venv``。
    """
    # 1. 当前 project_root 的 .venv
    if sys.platform == "win32":
        candidate = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = project_root / ".venv" / "bin" / "python"
    if candidate.is_file():
        return candidate

    # 2. worktree 共享主工作区 .venv（.worktrees 的父目录）
    if ".worktrees" in project_root.parts:
        idx = project_root.parts.index(".worktrees")
        main_workspace = Path(*project_root.parts[:idx])
        if sys.platform == "win32":
            candidate = main_workspace / ".venv" / "Scripts" / "python.exe"
        else:
            candidate = main_workspace / ".venv" / "bin" / "python"
        if candidate.is_file():
            return candidate
    return None


def _build_pyright_cmd(files: list[str]) -> list[str]:
    """构造 pyright 命令，自动检测项目根 .venv 并传 --pythonpath。

    pyright CLI 默认不发现 .venv（IDE 模式由 IDE 注入 venv 信息），
    导致 reportMissingImports 误报。--pythonpath 指向 venv 内的 Python
    解释器，pyright 据此定位 site-packages，解决依赖解析问题。

    CI 已激活 venv，--pythonpath 冗余但无害。
    """
    cmd = ["pyright", "--outputjson"]
    # 项目根 = 脚本所在目录的父目录（scripts/ 的父）
    project_root = Path(__file__).resolve().parent.parent
    python_exe = _resolve_venv_python(project_root)
    if python_exe is not None:
        cmd.extend(["--pythonpath", str(python_exe)])
    cmd.extend(files)
    return cmd


def _validate_payload(data: object) -> str | None:
    """校验 ``--outputjson`` 的输出结构，返回错误描述；结构合法时返回 None。

    结构缺失（如只有 summary 没有 generalDiagnostics）不得回落为「无 error」，
    否则工具失败会被空数组掩盖成通过。
    """
    if not isinstance(data, dict):
        return "顶层不是 JSON 对象"
    if not isinstance(data.get("summary"), dict):
        return "缺少 summary 对象"
    diagnostics = data.get("generalDiagnostics")
    if not isinstance(diagnostics, list):
        return "缺少 generalDiagnostics 数组"
    if any(not isinstance(item, dict) for item in diagnostics):
        return "generalDiagnostics 含非对象元素"
    return None


def main() -> int:
    files = [f for f in sys.argv[1:] if f.endswith((".py", ".pyi"))]
    if not files:
        return 0

    # pyright --outputjson 输出 JSON 到 stdout：0=无 error，1=有 error，
    # 其余退出码（实测配置解析失败为 3、参数/文件错误为 4）均为工具失败。
    try:
        result = subprocess.run(
            _build_pyright_cmd(files),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        print(
            "✗ pyright 未安装或不在 PATH 中。\n"
            "  安装方式：uv pip install -r requirements-dev.txt（含 pyright>=1.1.411）",
            file=sys.stderr,
        )
        return 2

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        # 非 JSON 输出（如配置错误直接 stderr），透传输出后按工具失败处理
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        print(
            "✗ pyright 未输出可解析的 JSON（--outputjson 预期 JSON），按工具失败处理。",
            file=sys.stderr,
        )
        return 2

    structural_error = _validate_payload(data)
    if structural_error is not None:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        print(f"✗ pyright 输出结构异常：{structural_error}", file=sys.stderr)
        return 2

    # 检查 summary.fatalError（配置错误时 JSON 中含此字段，退出码通常为 2）
    summary = data.get("summary", {})
    fatal_error = summary.get("fatalError")
    if fatal_error:
        print(f"✗ Pyright 配置错误：{fatal_error}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 2

    # 除 0/1（0=通过、1=有 error 诊断）外的退出码均为工具/配置失败，
    # 不得因 JSON 中诊断为空而放行。
    if result.returncode not in (0, 1):
        print(
            f"✗ Pyright 调用失败（退出码 {result.returncode}），不因输出无 error 诊断而放行。",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 2

    diagnostics = data.get("generalDiagnostics", [])
    errors = [d for d in diagnostics if d.get("severity") == "error"]

    if not errors:
        if result.returncode == 1:
            print(
                "✗ Pyright 退出码为 1（报告失败），但 JSON 中无 error 级诊断；按工具异常处理。",
                file=sys.stderr,
            )
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return 1
        return 0

    print(f"✗ Pyright 发现 {len(errors)} 个 error（共 {len(diagnostics)} 个诊断）：\n")
    for d in errors:
        file_path = d.get("file", "?")
        range_info = d.get("range", {})
        start = range_info.get("start", {})
        line = start.get("line", 0) + 1
        col = start.get("character", 0) + 1
        message = d.get("message", "")
        rule = d.get("rule", "")
        rule_str = f" [{rule}]" if rule else ""
        print(f"  {file_path}:{line}:{col}: {message}{rule_str}")

    print(
        f"\n共 {len(errors)} 个 error。修复后重新提交；"
        "或用 `# type: ignore[错误码]  # 原因` 抑制（须带原因，R3 强制）。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
