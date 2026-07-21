"""main.py 引导顺序集成测试（Phase 2 §3.4 红灯翻绿）。

测试分组（2 个）：
- 源码静态分析：inspect.getsource(main) 验证 prepare_database_runtime 调用顺序
- import 验证：源码含 from app.bootstrap import prepare_database_runtime

决策（D13）：main() 含 Flet UI 启动逻辑不可直接单测，用源码静态分析验证调用点
存在且顺序正确。完整 E2E 留待 Phase 3。
"""

from __future__ import annotations

import inspect

from main import main


def test_main_imports_prepare_database_runtime() -> None:
    """源码含 from app.bootstrap import prepare_database_runtime。"""
    source = inspect.getsource(main)
    assert "from app.bootstrap import prepare_database_runtime" in source, (
        f"main() 源码应含 'from app.bootstrap import prepare_database_runtime'，实际源码片段：\n{source[:1500]}"
    )


def test_prepare_database_runtime_called_before_cache_manager() -> None:
    """prepare_database_runtime 调用源码位置在 CacheManager() 之前。

    Phase 2 §3.4：必须先启动 EmbeddedPostgresService 并注入 URL，再构造
    CacheManager（CacheManager 构造时建引擎，依赖 DATABASE_URL）。
    """
    source = inspect.getsource(main)
    prepare_pos = source.find("prepare_database_runtime()")
    cache_manager_pos = source.find("CacheManager()")

    assert prepare_pos != -1, f"main() 源码应含 'prepare_database_runtime()' 调用，实际源码片段：\n{source[:1500]}"
    assert cache_manager_pos != -1, f"main() 源码应含 'CacheManager()' 调用，实际源码片段：\n{source[:1500]}"
    assert prepare_pos < cache_manager_pos, (
        "prepare_database_runtime() 必须在 CacheManager() 之前调用（Phase 2 §3.4）。"
        f"prepare_pos={prepare_pos}, cache_manager_pos={cache_manager_pos}"
    )
