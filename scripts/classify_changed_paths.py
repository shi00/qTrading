"""判断 PR 变更文件是否属于代码类路径。

背景：main 分支的 required status check 是聚合门禁 ``CI Gate``。``ci_cd.yml`` 已不再用
``pull_request.paths`` 过滤（未命中 paths 时 workflow 不触发，required check 会永久停在
Pending 并阻塞合并），改为运行时分类：非代码类 PR 只跑轻量 job，重型 job 按需跳过。

CI 内用法::

    gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/files" \
        --jq '.[] | [.filename, (.previous_filename // "")] | @tsv' \
        | python scripts/classify_changed_paths.py

每行两列（``filename`` / ``previous_filename``，TAB 分隔）。GitHub 对重命名文件只在
新路径上给出 ``filename``，旧路径在 ``previous_filename``；分类必须对两者取并集，否则
「代码 → 文档」的移动会丢失代码侧路径而被判为非代码类。空列（未重命名或旧路径缺失）
与空行均被忽略；纯换行分隔的路径输入仍然兼容。

输出一行 ``code=true`` 或 ``code=false``，可直接追加到 ``$GITHUB_OUTPUT``。
分类异常由调用方（workflow）按 fail-closed 处理，本脚本不吞异常。
"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Iterable

# 基线为变更前 ci_cd.yml 的 pull_request.paths 白名单，避免分类语义漂移。
# 另加 finbert-train.yml 精确路径（D18-S01）：该 workflow 仅 workflow_dispatch，
# PR 不触发自身；其契约测试 test_workflow_finbert_train_split.py 须随 unit 重型
# job 在合并前执行。仅精确路径，不泛化 .github/workflows/**——sidecar /
# precommit-config 已有独立 PR 补偿，避免把它们重新拉入重型 job。
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
    ".github/workflows/finbert-train.yml",
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
    """从 stdin 读取路径并输出分类结果。

    stdin 每行可为单路径，也可为 TAB 分隔的双列（``filename`` / ``previous_filename``）；
    两列都会参与分类，使重命名前后的任一代码路径都能命中代码类白名单。
    """
    paths = [path.strip() for line in sys.stdin for path in line.split("\t") if path.strip()]
    print(f"code={'true' if classify(paths) else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
