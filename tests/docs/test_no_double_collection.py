r"""D9 双收集守卫（检视 §5.3：兼容转发与测试分层）。

检视 5.3 合同：
- 「同一 job 只能选择迁移后的纯测试或旧兼容转发之一，不能同时收集两份后将
  重复执行计为新增覆盖」；
- 「旧节点名的参数化与标记保持」。

迁移落地（D9）：
- tests/unit/test_docs_consistency.py → 纯文档用例迁至 tests/docs/test_consistency.py，
  旧文件保留 2 个依赖应用符号导入的合同类 + 模块级转发（51 类）；
- tests/unit/test_docs_canonical_examples.py → 整体迁至 tests/docs/test_canonical_examples.py，
  旧文件仅保留模块级转发（3 类）。

本守卫从**实现对象 identity** 证明转发语义：旧入口收集到的每个迁移类必须与
新实现为同一类对象（re-export），而非复制。若有人把迁移类复制回旧文件（或新
文件又出现留守类的第二份实现），同一测试将以两个不同节点名各持一份实现被
双收集——守卫在此处失败，把「双收集」从静默重复执行变为显式红灯。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = ROOT / "tests" / "docs"
for _p in (str(ROOT), str(_DOCS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_canonical_examples as canonical_new  # noqa: E402
import test_consistency as consistency_new  # noqa: E402


def _load_legacy_module(module_name: str, location: Path) -> object:
    """按文件路径加载旧入口转发模块（绕开 tests 包结构）。

    不经 ``import tests.unit.*``：tests/docs 纯文档环境禁止业务/包结构导入
    （test_isolation.py 静态 + 运行时双重守护），旧转发模块的模块级代码只依赖
    sys.path 注入后的顶层 ``test_consistency`` / ``test_canonical_examples``
    导入，按路径加载即可与其共享同一实现对象（identity 断言的前提）。
    """
    spec = importlib.util.spec_from_file_location(module_name, location)
    assert spec is not None and spec.loader is not None, f"无法构造加载 spec: {location}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canonical_old = _load_legacy_module(
    "d9_guard_legacy_canonical", ROOT / "tests" / "unit" / "test_docs_canonical_examples.py"
)
consistency_old = _load_legacy_module(
    "d9_guard_legacy_consistency", ROOT / "tests" / "unit" / "test_docs_consistency.py"
)

pytestmark = pytest.mark.docs_pure

# 旧 test_docs_consistency.py 中允许保留的本地实现类（依赖应用符号导入的合同，检视 §5.3）。
_RESIDUAL_CONSISTENCY_CLASSES = frozenset({"TestLLMProviderCount", "TestSingletonRegistryConsistency"})


def _test_classes(module: object) -> dict[str, type]:
    """提取模块命名空间中以 Test 开头的类（含转发 re-export 进来的类）。"""
    return {name: value for name, value in vars(module).items() if name.startswith("Test") and isinstance(value, type)}


class TestConsistencyForwardingIntegrity:
    """tests/unit/test_docs_consistency.py 转发完整性：旧节点 = 新实现，禁止第二份实现。"""

    def test_every_pure_class_is_forwarded_by_identity(self):
        """新模块每个纯文档类在旧模块必须存在且为同一类对象（re-export 而非复制）。"""
        new_classes = _test_classes(consistency_new)
        assert new_classes, "tests/docs/test_consistency.py 必须持有纯文档用例实现"
        old_classes = _test_classes(consistency_old)
        missing = sorted(set(new_classes) - set(old_classes))
        assert not missing, f"旧入口缺少转发（旧节点名未保转发）: {missing}"
        copied = sorted(name for name, cls in new_classes.items() if old_classes[name] is not cls)
        assert not copied, (
            "以下用例在新/旧两处各持一份实现（同一测试双收集）: "
            f"{copied}。旧文件只允许 re-export tests/docs/test_consistency.py 的实现。"
        )

    def test_residual_classes_have_no_second_implementation_in_new_module(self):
        """依赖应用环境的合同类只允许留在旧环境，不得在新模块出现第二份实现。"""
        new_classes = _test_classes(consistency_new)
        duplicated = sorted(_RESIDUAL_CONSISTENCY_CLASSES & set(new_classes))
        assert not duplicated, f"留守合同类不得迁入 tests/docs（否则两处实现双收集）: {duplicated}"
        old_classes = _test_classes(consistency_old)
        absent = sorted(_RESIDUAL_CONSISTENCY_CLASSES - set(old_classes))
        assert not absent, f"留守合同类必须保留在旧入口: {absent}"

    def test_old_module_holds_no_extra_local_test_classes(self):
        """旧模块除留守合同类与转发外，不得新增本地 Test 类（防复制回迁）。"""
        old_classes = _test_classes(consistency_old)
        new_classes = _test_classes(consistency_new)
        extra = sorted(set(old_classes) - set(new_classes) - _RESIDUAL_CONSISTENCY_CLASSES)
        assert not extra, f"旧入口出现未知本地实现（应迁至 tests/docs 并转发）: {extra}"


class TestCanonicalExamplesForwardingIntegrity:
    """tests/unit/test_docs_canonical_examples.py 整体迁移后的转发完整性。"""

    def test_every_class_is_forwarded_by_identity(self):
        """新模块全部类在旧模块必须存在且为同一类对象。"""
        new_classes = _test_classes(canonical_new)
        assert new_classes, "tests/docs/test_canonical_examples.py 必须持有实现"
        old_classes = _test_classes(canonical_old)
        missing = sorted(set(new_classes) - set(old_classes))
        assert not missing, f"旧入口缺少转发: {missing}"
        copied = sorted(name for name, cls in new_classes.items() if old_classes[name] is not cls)
        assert not copied, f"canonical 示例用例在新/旧两处各持一份实现（双收集）: {copied}"

    def test_old_module_is_forward_only(self):
        """旧模块不得持有新模块之外的本地 Test 类（整体迁移后仅转发）。"""
        old_classes = _test_classes(canonical_old)
        new_classes = _test_classes(canonical_new)
        extra = sorted(set(old_classes) - set(new_classes))
        assert not extra, f"旧入口出现未知本地实现: {extra}"
