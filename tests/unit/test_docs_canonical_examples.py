"""Tests for canonical UI examples in docs — 旧入口兼容转发（D9，检视 5.3）。

D9 按检视 §5.3 分层迁移后，原静态 canonical 代码块检查已整体迁往
tests/docs/test_canonical_examples.py（独立轻量环境）；本文件仅保留旧节点名
转发，维持既有应用环境选择器（`python -m pytest tests/unit/test_docs_canonical_examples.py`）
的收集兼容。转发完整性由 tests/docs/test_no_double_collection.py 守卫：
同一用例禁止在新/旧两处各持一份实现（双收集计为重复执行）。
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent

_DOCS_TESTS_DIR = ROOT / "tests" / "docs"
if str(_DOCS_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCS_TESTS_DIR))

from test_canonical_examples import (  # type: ignore[import-not-found]  # tests/docs 目录运行时经 sys.path 动态解析
    TestContributingCanonicalExamples,
    TestFletDocsCanonicalExamples,
    TestManFletBestPracticesCanonicalExamples,
    TestMvvmBridgeCanonicalExample,
)
