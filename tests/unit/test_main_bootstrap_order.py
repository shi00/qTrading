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
    """源码含 from app.bootstrap import ... prepare_database_runtime（ruff isort 可能合并多符号导入）。

    P0-1: prepare_database_runtime 的导入与调用已移到 _prepare_db_with_retry 函数，
    main() 内不再直接导入（仅导入 detect_embedded_pg_startup_scenario）。
    """
    from main import _prepare_db_with_retry

    source = inspect.getsource(_prepare_db_with_retry)
    assert re.search(r"from app\.bootstrap import .*prepare_database_runtime", source), (
        f"_prepare_db_with_retry() 源码应含 'from app.bootstrap import ... prepare_database_runtime'，"
        f"实际源码片段：\n{source[:1500]}"
    )


def test_prepare_db_with_retry_called_before_cache_manager() -> None:
    """_prepare_db_with_retry 调用源码位置在 CacheManager() 之前。

    Phase 2 §3.4：必须先启动 EmbeddedPostgresService 并注入 URL，再构造
    CacheManager（CacheManager 构造时建引擎，依赖 DATABASE_URL）。

    P0-1：prepare_database_runtime 的调用与错误处理已封装到 _prepare_db_with_retry，
    main() 直接调用 _prepare_db_with_retry(page, scenario)。
    """
    source = inspect.getsource(main)
    prepare_pos = source.find("_prepare_db_with_retry(")
    cache_manager_pos = source.find("CacheManager()")

    assert prepare_pos != -1, f"main() 源码应含 '_prepare_db_with_retry(' 调用，实际源码片段：\n{source[:1500]}"
    assert cache_manager_pos != -1, f"main() 源码应含 'CacheManager()' 调用，实际源码片段：\n{source[:1500]}"
    assert prepare_pos < cache_manager_pos, (
        "_prepare_db_with_retry() 必须在 CacheManager() 之前调用（Phase 2 §3.4）。"
        f"prepare_pos={prepare_pos}, cache_manager_pos={cache_manager_pos}"
    )


def test_main_calls_prepare_db_with_retry() -> None:
    """P0-1: main() 源码含 _prepare_db_with_retry 调用（替代直接 await prepare_database_runtime）。

    失败时不再直接 sys.exit(1)，而是渲染 PreInitErrorView 供用户 Retry/Exit。
    E2E/Web 模式仍保留 sys.exit(1) 快速失败路径（在 _prepare_db_with_retry 内部）。
    """
    source = inspect.getsource(main)
    assert "_prepare_db_with_retry" in source, (
        f"main() 源码应含 '_prepare_db_with_retry' 调用（P0-1：封装重试逻辑），实际源码片段：\n{source[:2000]}"
    )


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


def test_main_persists_embedded_db_url_in_config() -> None:
    """D15（pg-plan §22）：main() 永久设置 config.DB_URL 而非用 override_db_url 包裹。

    旧实现（已废弃）：prepare_database_runtime 返回 URL，main 用 override_db_url(url)
    上下文管理器包裹 CacheManager() 构造。问题：with 块退出后 config.DB_URL 被还原为空，
    导致 ConfigHandler.get_db_url() 在 with 块外返回空值，check_onboarding_needed 误判
    需要重新 onboarding（spec.md §1.4）。

    新实现：prepare_database_runtime 返回 URL 后，main 永久设置 config.DB_URL = embedded_url
    （运行时变量，不持久化到 config 文件，不设 DATABASE_URL 环境变量）。
    ConfigHandler.get_db_url() Priority 3 兜底返回 embedded URL。

    源码静态分析断言：
    - main() 源码含 "config.DB_URL = embedded_db_url"（永久设置）
    - main() 源码不含 "with override_db_url("（不再用上下文管理器）
    """
    source = inspect.getsource(main)
    assert "config.DB_URL = embedded_db_url" in source, (
        f"main() 源码应含 'config.DB_URL = embedded_db_url'（D15：永久设置 embedded URL），"
        f"实际源码片段：\n{source[:2000]}"
    )
    assert "with override_db_url(" not in source, (
        f"main() 源码不应含 'with override_db_url('（D15：已改为永久设置 config.DB_URL），"
        f"实际源码片段：\n{source[:2000]}"
    )


def test_main_pops_database_url_env_in_embedded_mode() -> None:
    """P3 根因修复：embedded 模式启动成功后应 pop 残留的 DATABASE_URL env var。

    根因：ContextVar per-asyncio-task，DataExplorer 同步调用链外（非 page.run_task）
    ContextVar 已丢失，get_db_url() 返回 env var 残留旧 URL（Priority 1 > Priority 3）。
    pop env var 后，Priority 1 失效，统一走 ContextVar 或 config.DB_URL。

    源码静态分析断言：
    - main() 源码含 'os.environ.pop("DATABASE_URL", None)'
    - 该调用在 'ConfigHandler._db_url_override.set' 之后
    """
    source = inspect.getsource(main)
    assert 'os.environ.pop("DATABASE_URL", None)' in source, (
        f"main() 源码应含 'os.environ.pop(\"DATABASE_URL\", None)'（P3：根因修复），实际源码片段：\n{source[:2500]}"
    )
    pop_pos = source.find('os.environ.pop("DATABASE_URL"')
    override_pos = source.find("ConfigHandler._db_url_override.set(")
    assert pop_pos != -1 and override_pos != -1 and override_pos < pop_pos, (
        "os.environ.pop 必须在 ConfigHandler._db_url_override.set 之后（P3：先设置 override 再 pop env var）"
    )
