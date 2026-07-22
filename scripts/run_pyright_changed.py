"""pre-commit hook: 对 staged .py 文件跑 pyright，error 级别阻断。

用法（由 pre-commit 调用，文件名作为参数传入）::

    python scripts/run_pyright_changed.py <file1> <file2> ...

仅检查传入的文件，error 级别诊断返回 1 阻断提交，warning 不阻断。
与 CI 全量 ``pyright`` 互补：CI 守护全局类型一致性，本 hook 守护本地秒级反馈。

退出码:
    0: 无 error（warning 不阻断）
    1: 发现 error 级别诊断
    2: pyright 调用失败（未安装/配置错误）
"""

import json
import subprocess
import sys
from pathlib import Path


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
    if sys.platform == "win32":
        python_exe = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        python_exe = project_root / ".venv" / "bin" / "python"
    if python_exe.is_file():
        cmd.extend(["--pythonpath", str(python_exe)])
    cmd.extend(files)
    return cmd


def main() -> int:
    files = [f for f in sys.argv[1:] if f.endswith((".py", ".pyi"))]
    if not files:
        return 0

    # pyright --outputjson 输出 JSON 到 stdout，退出码: 0=无 error, 1=有 error, 2=配置错误
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
        # 非 JSON 输出（如配置错误直接 stderr），透传输出
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode

    # 检查 summary.fatalError（配置错误时 JSON 中含此字段，退出码通常为 2）
    summary = data.get("summary", {})
    fatal_error = summary.get("fatalError")
    if fatal_error:
        print(f"✗ Pyright 配置错误：{fatal_error}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 2

    diagnostics = data.get("generalDiagnostics", [])
    errors = [d for d in diagnostics if d.get("severity") == "error"]

    if not errors:
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
