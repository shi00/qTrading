TDD Red Light Evidence — tests/unit/test_embedded_postgres_service.py
Phase 2 实施前运行（2026-07-21），全部 5 个测试因 `data.persistence.embedded_postgres` 模块不存在而 ImportError。

命令：
  D:\Programs\Python\Python313\python.exe -m pytest tests/unit/test_embedded_postgres_service.py -v --tb=short

结果：5 failed, 2 warnings in 10.35s

失败列表（均 ModuleNotFoundError: No module named 'data.persistence.embedded_postgres'）：
- TestEmbeddedPostgresServiceSingleton::test_reset_singleton_clears_instance
- TestEmbeddedPostgresServiceSingleton::test_atexit_cleanup_registered
- TestEmbeddedPostgresServiceStart::test_start_returns_connection_info
- TestEmbeddedPostgresServiceStop::test_stop_is_idempotent
- TestEmbeddedPostgresServiceStart::test_from_config_constructs_service

Pytest warnings（实施时一并修复）：
- test_reset_singleton_clears_instance 非 async 但有 @pytest.mark.asyncio 标记（pytestmark 全局）
- test_atexit_cleanup_registered 同上

实现 EmbeddedPostgresService 后，同样命令应全绿。
