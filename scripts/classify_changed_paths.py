"""判断 PR 变更文件是否属于代码类路径。

背景：main 分支的 required status check 是聚合门禁 ``CI Gate``。``ci_cd.yml`` 已不再用
``pull_request.paths`` 过滤（未命中 paths 时 workflow 不触发，required check 会永久停在
Pending 并阻塞合并），改为运行时分类：非代码类 PR 只跑轻量 job，重型 job 按需跳过。

CI 内用法::

    gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/files" --jq '.[].filename' \
        | python scripts/ci/classify_changed_paths.py

输出一行 ``code=true`` 或 ``code=false``，可直接追加到 ``$GITHUB_OUTPUT``。
分类异常由调用方（workflow）按 fail-closed 处理，本脚本不吞异常。
"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Iterable

# 与变更前 ci_cd.yml 的 pull_request.paths 白名单保持一致，避免分类语义漂移。
CODE_PATTERNS: tuple[str, ...] = (
    "*.py",
    "requirements*.txt",
    "requirements*.in",
    "pyproject.toml",
    "pyrightconfig.json",
    "alembic.ini",
    "alembic/**",
    "AStockScreener.spec",
    "installer.iss",
    "locales/**",
    "assets/**",
    "package.json",
    "package-lock.json",
    "tests/**",
    "scripts/**",
    ".github/workflows/ci_cd.yml",
)


def is_code_path(path: str) -> bool:
    """路径是否命中代码类白名单。

    ``fnmatch`` 语义下 ``*`` 可匹配 ``/``，因此 ``*.py`` 覆盖任意目录下的 Python 文件。
    """
    return any(fnmatch.fnmatch(path, pattern) for pattern in CODE_PATTERNS)


def classify(paths: Iterable[str]) -> bool:
    """任一变更路径属于代码类即返回 True；空集合返回 False。"""
    return any(is_code_path(path) for path in paths)


def main() -> int:
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(f"code={'true' if classify(paths) else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
