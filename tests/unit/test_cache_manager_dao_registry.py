"""CacheManager DAO 注册表契约测试。

覆盖三个维度：
1. R13 静态维度：_DAO_REGISTRY 必须覆盖 daos/ 下所有继承 BaseDao 的 DAO 类
2. pyright 推断契约：__init__ 源码必须包含 _DAO_REGISTRY 中每个属性名的显式赋值
   （避免循环 setattr 破坏类型推断，保留 IDE 自动补全）
3. 数量一致性：_DAO_REGISTRY 条目数 == __init__ 中显式赋值的 DAO 数

engine refresh / close cleanup 维度由 tests/integration/test_data_cache_manager.py
的 mvd_data fixture 隐式覆盖（CacheManager() 真实实例化 → _create_engine 循环更新
所有 DAO.engine → 测试中 DAO 操作 → close 循环清空）。
"""

import inspect
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_redlines import _extract_dao_classes  # noqa: E402 - sys.path 注入后导入
from data.cache.cache_manager import CacheManager  # noqa: E402 - sys.path 注入后导入


class TestCacheManagerDAORegistry:
    """_DAO_REGISTRY 单一权威列表契约。

    修订方案 C：保留 __init__ 17 行显式赋值（pyright 推断 + R13 静态检查兼容），
    仅 _create_engine / close 由 _DAO_REGISTRY 驱动循环化。
    """

    def test_dao_registry_covers_all_dao_files(self):
        """_DAO_REGISTRY 必须覆盖 daos/ 下所有继承 BaseDao 的 DAO 类（R13 静态维度契约）。

        新增 DAO 文件但未登记到 _DAO_REGISTRY 时，此测试失败，
        提示在 _DAO_REGISTRY 中追加 ("attr_name", NewDao) 条目。
        """
        daos_dir = ROOT / "data" / "persistence" / "daos"
        dao_class_names = set(_extract_dao_classes(daos_dir).keys())
        registry_class_names = {cls.__name__ for _, cls in CacheManager._DAO_REGISTRY}
        assert dao_class_names == registry_class_names, (
            f"_DAO_REGISTRY 与 daos/ 目录不一致："
            f"缺少 {dao_class_names - registry_class_names}，"
            f"多余 {registry_class_names - dao_class_names}"
        )

    def test_init_assigns_all_registry_daos(self):
        """__init__ 源码必须包含 _DAO_REGISTRY 中每个属性名的显式赋值。

        这保证 pyright 能从 self.xxx_dao = XxxDao(self.engine) 推断类型，
        避免 _create_engine / close 循环中 getattr(self, attr_name) 破坏类型推断。
        循环 setattr 会让 self.xxx_dao 推断为 Unknown，此测试守护显式赋值不被移除。
        """
        source = inspect.getsource(CacheManager.__init__)
        for attr_name, _ in CacheManager._DAO_REGISTRY:
            assert f"self.{attr_name} =" in source, (
                f"__init__ 缺少 self.{attr_name} = 的显式赋值，pyright 无法推断类型，IDE 自动补全失效"
            )

    def test_dao_registry_count_matches_init(self):
        """_DAO_REGISTRY 条目数应等于 __init__ 中显式赋值的 DAO 数。

        防止 _DAO_REGISTRY 与 __init__ 显式赋值出现数量漂移
        （如加了 _DAO_REGISTRY 条目但忘记在 __init__ 中显式赋值，或反之）。
        """
        source = inspect.getsource(CacheManager.__init__)
        init_assign_count = sum(1 for line in source.splitlines() if "_dao = " in line and "self." in line)
        assert init_assign_count == len(CacheManager._DAO_REGISTRY), (
            f"__init__ 显式赋值 {init_assign_count} 个 DAO，"
            f"_DAO_REGISTRY 有 {len(CacheManager._DAO_REGISTRY)} 个，不一致"
        )
