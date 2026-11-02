#!/usr/bin/env python
"""对 CI 上传的文本工件做 R9 凭证脱敏。

用法（仓库根目录执行）：

    python scripts/sanitize_artifacts.py logs/e2e-flet-app.log 'junit-*.xml'

- 参数按 glob 展开；不存在或未匹配的文件跳过，不使步骤失败（`if: always()`
  步骤可能在测试早期失败、日志尚未生成时执行）。
- 复用 `utils.sanitizers.DataSanitizer.sanitize_error`（R9 唯一脱敏入口），覆盖
  URL userinfo 密码（如 `postgresql://user:pass@host`）与 `password=` 等键值形态。
- 以 UTF-8 + surrogateescape 读写，保留原始换行与无法解码的字节。
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.sanitizers import DataSanitizer  # noqa: E402 - sys.path 注入后导入


def sanitize_file(path: Path) -> bool:
    """就地脱敏单个文本工件。

    Args:
        path: 待脱敏文件路径。

    Returns:
        True 表示文件存在且已完成脱敏；False 表示文件不存在、已跳过。
    """
    if not path.is_file():
        return False
    raw = path.read_bytes()
    sanitized = DataSanitizer.sanitize_error(raw.decode("utf-8", errors="surrogateescape"))
    path.write_bytes(sanitized.encode("utf-8", errors="surrogateescape"))
    return True


def main(argv: list[str] | None = None) -> int:
    """展开 glob 并就地脱敏所有存在的工件。

    Args:
        argv: 路径或 glob 列表；缺省读取命令行参数。

    Returns:
        进程退出码（正常情况下恒为 0）。
    """
    patterns = sys.argv[1:] if argv is None else argv
    sanitized_count = 0
    for pattern in patterns:
        for match in glob.glob(pattern):
            if sanitize_file(Path(match)):
                sanitized_count += 1
                print(f"Sanitized: {match}")
    if sanitized_count == 0:
        print("No artifact files found; nothing to sanitize.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
