"""判断变更路径是否命中辅助补偿检查（sidecar / pre-commit 配置）。

背景：``ci-aux-gate.yml`` 的 ``detect`` job 按变更路径决定是否调用 ``sidecar.yml`` 与
``precommit-config-ci.yml``。GitHub 对重命名/移动的文件只在新路径给出 ``filename``，旧路径
放在 ``previous_filename``；若只按新路径选择，把 sidecar 目录或 ``.pre-commit-config.yaml``
移出匹配路径时会漏选对应补偿检查（D26）。

CI 内用法::

    gh api --paginate "repos/$REPO/pulls/$PR_NUMBER/files" \
        --jq '.[] | [.filename, (.previous_filename // "")] | @tsv' \
        | python scripts/classify_aux_changed_paths.py

每行两列（``filename`` / ``previous_filename``，TAB 分隔）；纯换行分隔的路径输入（例如
``git diff --no-renames --name-only`` 的输出）同样兼容。两列都会参与选择，空列与空行忽略，
同一路径只计一次。输出两行 ``sidecar=true|false`` 与 ``precommit=true|false``，可直接追加到
``$GITHUB_OUTPUT``。分类异常由调用方（workflow）按 fail-closed 处理，本脚本不吞异常。
"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Iterable

# 与迁移前的 detect shell 正则保持一致，避免选择语义漂移。
SIDECAR_PATTERNS: tuple[str, ...] = (
    "sidecars/qtrading-pg-sidecar/**",
    ".github/workflows/sidecar.yml",
    ".github/workflows/ci-aux-gate.yml",
)
PRECOMMIT_PATTERNS: tuple[str, ...] = (
    ".pre-commit-config.yaml",
    ".github/workflows/precommit-config-ci.yml",
    ".github/workflows/ci-aux-gate.yml",
)


def collect_paths(lines: Iterable[str]) -> list[str]:
    """把每行（单路径或 TAB 分隔的双列）展开为去重后的路径列表。

    ``fnmatch`` 语义下 ``*`` 可匹配 ``/``，因此 ``sidecars/qtrading-pg-sidecar/**``
    覆盖该目录下的任意层级文件。
    """
    paths: list[str] = []
    seen: set[str] = set()
    for line in lines:
        for path in line.split("\t"):
            path = path.strip()
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def classify(paths: Iterable[str]) -> tuple[bool, bool]:
    """返回 ``(sidecar, precommit)``：任一变更路径命中对应规则即为 True。"""
    sidecar = False
    precommit = False
    for path in paths:
        sidecar = sidecar or _matches(path, SIDECAR_PATTERNS)
        precommit = precommit or _matches(path, PRECOMMIT_PATTERNS)
    return sidecar, precommit


def main() -> int:
    """从 stdin 读取路径（单列或双列）并输出两行 GITHUB_OUTPUT 可用的结果。"""
    sidecar, precommit = classify(collect_paths(sys.stdin))
    print(f"sidecar={'true' if sidecar else 'false'}")
    print(f"precommit={'true' if precommit else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
