"""main.py 引导顺序集成测试（Phase 2 §3.4 红灯翻绿）。

测试分组（2 个）：
- 源码静态分析：inspect.getsource(main) 验证 prepare_database_runtime 调用顺序
- import 验证：源码含 from app.bootstrap import prepare_database_runtime

决策（D13）：main() 含 Flet UI 启动逻辑不可直接单测，用源码静态分析验证调用点
存在且顺序正确。完整 E2E 留待 Phase 3。
"""

from __future__ import annotations

import inspect
import re

from main import main


def test_main_imports_prepare_database_runtime() -> None:
    """源码含 from app.bootstrap import ... prepare_database_runtime（ruff isort 可能合并多符号导入）。"""
    source = inspect.getsource(main)
    assert re.search(r"from app\.bootstrap import .*prepare_database_runtime", source), (
        f"main() 源码应含 'from app.bootstrap import ... prepare_database_runtime'，实际源码片段：\n{source[:1500]}"
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


def test_main_wraps_prepare_database_runtime_in_try_except() -> None:
    """H2: main() 源码含 try/except 包裹 prepare_database_runtime()。

    失败时记 critical 日志 + log_exception_with_severity + sys.exit(1)，
    避免 CacheManager 在无 DB 状态下启动导致后续连锁失败。
    """
    source = inspect.getsource(main)
    assert "try:" in source, f"main() 源码应含 'try:'，实际源码片段：\n{source[:2000]}"
    assert "await prepare_database_runtime()" in source, (
        f"main() 源码应含 'await prepare_database_runtime()'，实际源码片段：\n{source[:2000]}"
    )
    assert "except Exception as e:" in source, (
        f"main() 源码应含 'except Exception as e:'，实际源码片段：\n{source[:2000]}"
    )
    assert "prepare_database_runtime failed" in source, (
        f"main() 源码应含 'prepare_database_runtime failed' 日志标签，实际源码片段：\n{source[:2000]}"
    )
    assert "log_exception_with_severity" in source, (
        f"main() 源码应含 'log_exception_with_severity' 调用，实际源码片段：\n{source[:2000]}"
    )
    assert "sys.exit(1)" in source, f"main() 源码应含 'sys.exit(1)' 强制退出，实际源码片段：\n{source[:2000]}"


def test_main_imports_log_exception_with_severity() -> None:
    """H2: main 模块顶层导入 log_exception_with_severity + sys（验证 import 存在）。

    inspect.getsource(main) 仅返回函数体，不含模块级 import；
    改用 hasattr 验证模块属性是否存在。
    """
    import main as main_mod

    assert hasattr(main_mod, "log_exception_with_severity"), (
        "main 模块应导入 log_exception_with_severity（用于 prepare_database_runtime 失败时记 severity 日志）"
    )
    assert hasattr(main_mod, "sys"), "main 模块应导入 sys（用于 sys.exit(1) 强制退出）"


def test_main_wraps_cache_manager_with_override_db_url() -> None:
    """D15（pg-plan §22）：main() 用 override_db_url 包裹 CacheManager() 构造。

    Phase 2 §3.4 旧实现：prepare_database_runtime 调 save_db_config 持久化 embedded URL
    D15 新实现：prepare_database_runtime 返回 URL，main 用 override_db_url(url) 包裹
    CacheManager() 构造（不再持久化到 config，避免污染用户配置）。

    源码静态分析断言：
    - main() 源码含 "override_db_url(" 调用
    - main() 源码含 "with override_db_url(" 上下文管理器用法
    """
    source = inspect.getsource(main)
    assert "override_db_url" in source, (
        f"main() 源码应含 'override_db_url'（D15：包裹 CacheManager 构造），实际源码片段：\n{source[:2000]}"
    )
    assert "with override_db_url(" in source, (
        f"main() 源码应含 'with override_db_url(' 上下文管理器用法（D15），实际源码片段：\n{source[:2000]}"
    )
