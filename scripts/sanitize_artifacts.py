#!/usr/bin/env python
"""对 CI 上传的文本工件做 R9 凭证脱敏。

用法（仓库根目录执行）：

    python scripts/sanitize_artifacts.py logs/e2e-flet-app.log 'junit-*.xml' e2e-artifacts sidecar-logs

- 参数按 glob 展开；目录参数递归遍历；不存在或未匹配的路径跳过，不使步骤失败
  （`if: always()` 步骤可能在测试早期失败、日志尚未生成时执行）。
- 仅处理文本扩展名白名单（`TEXT_ARTIFACT_SUFFIXES`）；PNG/zip/tar.gz 等二进制
  工件按扩展名直接跳过，从不读取或改写。
- 复用 `utils.sanitizers.DataSanitizer.sanitize_error`（R9 唯一脱敏入口），覆盖
  URL userinfo 密码（如 `postgresql://user:pass@host`）与 `password=` 等键值形态。
- 以 UTF-8 + surrogateescape 读写，保留原始换行与无法解码的字节。
"""

from __future__ import annotations

import glob
import sys
from collections.abc import Iterator
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.sanitizers import DataSanitizer  # noqa: E402 - sys.path 注入后导入

TEXT_ARTIFACT_SUFFIXES = frozenset({".log", ".txt", ".xml", ".json", ".html", ".csv", ".yml", ".yaml", ".md", ".out"})


def _is_text_artifact(path: Path) -> bool:
    """按扩展名白名单判定是否可安全地做文本脱敏。"""
    return path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES


def _iter_text_artifacts(path: Path) -> Iterator[Path]:
    """递归遍历目录下的白名单文本工件，按路径排序保证输出稳定。"""
    yield from (
        candidate for candidate in sorted(path.rglob("*")) if candidate.is_file() and _is_text_artifact(candidate)
    )


def sanitize_file(path: Path) -> bool:
    """就地脱敏单个文本工件；二进制/非白名单扩展名不读取。

    Args:
        path: 待脱敏文件路径。

    Returns:
        True 表示文件存在且已完成脱敏；False 表示文件不存在或按白名单跳过。
    """
    if not path.is_file() or not _is_text_artifact(path):
        return False
    raw = path.read_bytes()
    sanitized = DataSanitizer.sanitize_error(raw.decode("utf-8", errors="surrogateescape"))
    path.write_bytes(sanitized.encode("utf-8", errors="surrogateescape"))
    return True


def _iter_matches(pattern: str) -> Iterator[Path]:
    """展开参数：目录递归，glob/文件按匹配结果逐个产出。"""
    literal = Path(pattern)
    if literal.is_dir():
        yield from _iter_text_artifacts(literal)
        return
    for match in sorted(glob.glob(pattern)):
        candidate = Path(match)
        if candidate.is_dir():
            yield from _iter_text_artifacts(candidate)
        elif candidate.is_file():
            yield candidate


def main(argv: list[str] | None = None) -> int:
    """展开路径/glob/目录并就地脱敏所有存在的文本工件。

    Args:
        argv: 路径、glob 或目录列表；缺省读取命令行参数。

    Returns:
        进程退出码（正常情况下恒为 0）。
    """
    patterns = sys.argv[1:] if argv is None else argv
    sanitized_count = 0
    processed: set[Path] = set()
    for pattern in patterns:
        for match in _iter_matches(pattern):
            resolved = match.resolve()
            if resolved in processed:
                continue
            processed.add(resolved)
            if sanitize_file(match):
                sanitized_count += 1
                print(f"Sanitized: {match}")
    if sanitized_count == 0:
        print("No artifact files found; nothing to sanitize.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
