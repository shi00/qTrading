"""CacheManager DAO 注册契约测试（OSS-03：注册表按类型发现后重写）。

覆盖维度：
1. 遍历性覆盖：daos/ 下所有继承 BaseDao 的 DAO 类均在 CacheManager.__init__ 显式实例化
2. pyright 推断契约：__init__ 源码包含每个 DAO 类的 self.xxx_dao = XxxDao(...) 显式赋值
   （避免循环 setattr 破坏类型推断，保留 IDE 自动补全）
3. 数量一致性：daos/ 目录 DAO 类数 == __init__ 中显式赋值的 DAO 数
4. sync_engines 行为：按类型发现（isinstance BaseDao）同步 engine；非 DAO 属性不受影响

engine refresh / close cleanup 维度由 tests/integration/test_data_cache_manager.py
的 mvd_data fixture 隐式覆盖（CacheManager() 真实实例化 → _create_engine 循环更新
所有 DAO.engine → 测试中 DAO 操作 → close 循环清空）。
"""

import inspect
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_redlines import _extract_dao_classes  # noqa: E402 - sys.path 注入后导入
from data.cache.cache_manager import CacheManager  # noqa: E402 - sys.path 注入后导入
from data.cache.dao_registry import DaoRegistry  # noqa: E402 - review01-A4 Step2: 注册职责移入 DaoRegistry
from data.persistence.daos.base_dao import BaseDao  # noqa: E402


def _dao_attr_for_class(cls_name: str) -> str:
    """DAO 类名 → 约定实例属性名。

    支持驼峰多词：PledgeDetailDao → pledge_detail_dao；BacktestDAO → backtest_dao。
    """
    for suffix in ("DAO", "Dao"):
        if cls_name.endswith(suffix):
            base = cls_name[: -len(suffix)]
            break
    else:
        base = cls_name
    # 驼峰 → snake_case：连续大写/首字母大写处插入 '_'
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
    return snake + "_dao"


class TestCacheManagerDAORegistry:
    """DAO 注册契约：__init__ 显式实例化 + sync_engines 类型发现同步。"""

    def test_all_dao_classes_instantiated_in_init(self):
        """daos/ 下所有继承 BaseDao 的 DAO 类均应在 CacheManager.__init__ 显式实例化。

        R13 静态维度：新增 DAO 文件未在 __init__ 实例化时，此测试失败。
        """
        daos_dir = ROOT / "data" / "persistence" / "daos"
        dao_class_names = set(_extract_dao_classes(daos_dir).keys())
        source = inspect.getsource(CacheManager.__init__)
        instantiated = {cls_name for cls_name in dao_class_names if f"{cls_name}(" in source}
        missing = dao_class_names - instantiated
        assert not missing, (
            f"CacheManager.__init__ 未实例化的 DAO 类：{missing}"
            f"（应在 __init__ 中 self.<name>_dao = {next(iter(missing))}(self.engine)）"
        )

    def test_init_uses_explicit_assignments(self):
        """__init__ 中所有 DAO 均为 self.xxx_dao = XxxDao(...) 显式赋值（pyright 契约）。

        OSS-03 坚持显式实例化而非动态 setattr：循环 setattr 会让 self.xxx_dao
        推断为 Unknown，IDE 自动补全与类型守卫失效。
        """
        daos_dir = ROOT / "data" / "persistence" / "daos"
        dao_class_names = set(_extract_dao_classes(daos_dir).keys())
        source = inspect.getsource(CacheManager.__init__)
        for cls_name in sorted(dao_class_names):
            attr = _dao_attr_for_class(cls_name)
            assert f"self.{attr} = {cls_name}(" in source, (
                f"__init__ 缺少显式赋值 self.{attr} = {cls_name}(self.engine)，pyright 无法推断类型"
            )

    def test_dao_count_matches_init(self):
        """daos/ 目录 DAO 类数 == __init__ 显式赋值数（防数量漂移）。"""
        daos_dir = ROOT / "data" / "persistence" / "daos"
        dao_class_names = set(_extract_dao_classes(daos_dir).keys())
        source = inspect.getsource(CacheManager.__init__)
        init_assign_count = sum(1 for line in source.splitlines() if "_dao = " in line and "self." in line)
        assert init_assign_count == len(dao_class_names), (
            f"__init__ 显式赋值 {init_assign_count} 个 DAO，daos/ 目录有 {len(dao_class_names)} 个，不一致"
        )


class TestSyncEnginesTypeDiscovery:
    """sync_engines 按类型发现（OSS-03）：isinstance(BaseDao) 过滤，非 DAO 属性不受影响。"""

    class _FakeDao(BaseDao):
        pass

    class _FakeNonDao:
        def __init__(self) -> None:
            self.engine = None

    class _Holder:
        """最小宿主：可挂载任意属性，模拟 CacheManager 的 DAO 组合根。"""

        def __init__(self) -> None:
            self.dao_a: TestSyncEnginesTypeDiscovery._FakeDao | None = None
            self.dao_b: TestSyncEnginesTypeDiscovery._FakeDao | None = None
            self.other: TestSyncEnginesTypeDiscovery._FakeNonDao | None = None
            self.engine = None
            self._disposed = False

    def test_syncs_all_base_dao_instances(self):
        holder = self._Holder()
        dao = self._FakeDao(None)
        dao2 = self._FakeDao(None)
        non_dao = self._FakeNonDao()
        holder.dao_a = dao
        holder.dao_b = dao2
        holder.other = non_dao

        engine = object()
        DaoRegistry().sync_engines(holder, engine)

        assert dao.engine is engine
        assert dao2.engine is engine
        assert non_dao.engine is None  # 非 BaseDao 不受影响

    def test_sync_engines_none_clears(self):
        holder = self._Holder()
        dao = self._FakeDao(object())
        holder.dao_a = dao

        DaoRegistry().sync_engines(holder, None)

        assert dao.engine is None

    def test_sync_engines_ignores_non_dao_attrs(self):
        holder = self._Holder()
        holder.engine = None  # holder.engine 是 AsyncEngine 位置，非 BaseDao，不应被当 DAO
        holder._disposed = True
        DaoRegistry().sync_engines(holder, "new_engine")
        assert holder.engine is None
