"""Pre-commit hook: forbid unittest.IsolatedAsyncioTestCase in tests.

用法: python scripts/check_no_isolated_asyncio.py <files...>

判定方式：用标准库 tokenize 扫描源码，只有 NAME token 恰为
IsolatedAsyncioTestCase 才算违规；注释（COMMENT）与字符串/文档字符串（STRING）
天然不产生 NAME token，因此无需按文本前缀猜测字符串边界，也不存在「行内出现
某个备注短语即放行」的绕过路径。

退出码:
- 0: 未发现违规。
- 1: 任一文件出现 IsolatedAsyncioTestCase，或无法完成 Python token 检查
  （TokenError / SyntaxError / UnicodeDecodeError，fail-closed）。
文件读取失败（OSError）沿用既有行为跳过。
"""

import io
import sys
import tokenize
from pathlib import Path

_FORBIDDEN_NAME = "IsolatedAsyncioTestCase"


def _check_file(filepath: str) -> list[str]:
    """检查单个文件并返回违规描述列表。"""
    try:
        source = Path(filepath).read_text(encoding="utf-8")
    except OSError:
        # 沿用既有行为：文件不可读（含已删除）时跳过
        return []
    except UnicodeDecodeError:
        # 无法按 UTF-8 解码即无法完成 token 检查：fail-closed 而非静默放行
        return [f"{filepath}: 无法完成 Python token 检查（UnicodeDecodeError: 源码不是合法 UTF-8）"]

    violations: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.NAME and tok.string == _FORBIDDEN_NAME:
                violations.append(f"{filepath}:{tok.start[0]}: {tok.line.strip()}")
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError) as exc:
        # token 解析失败时不能静默跳过，否则真实继承可能被漏检
        detail = str(exc).replace("\n", " ")
        violations.append(f"{filepath}: 无法完成 Python token 检查（{type(exc).__name__}: {detail}）")
    return violations


def main() -> None:
    # Windows 控制台编码可能无法输出中文违规信息，统一转 UTF-8 避免编码异常掩盖结果
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    violations: list[str] = []
    for filepath in sys.argv[1:]:
        violations.extend(_check_file(filepath))

    if violations:
        print("IsolatedAsyncioTestCase is forbidden (use pytest-asyncio style instead):")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)


if __name__ == "__main__":
    main()
