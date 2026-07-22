TDD 红灯证据：tests/unit/test_shutdown_step8.py

【红灯场景】
在 utils/shutdown.py 未追加 Step 8 定义 + 未实现 _step8_stop_embedded_postgres 前，
6 个测试会失败：

1. test_step8_in_cleanup_steps_at_index_8：
   - _CLEANUP_STEPS 只有 8 项（Step 0-7），索引 8 越界
   - AssertionError: _CLEANUP_STEPS 应至少 9 项，实际 8

2. test_step8_noop_when_service_not_registered：
   - AttributeError: 'ShutdownCoordinator' object has no attribute '_step8_stop_embedded_postgres'

3. test_step8_noop_when_service_not_initialized：
   - AttributeError: 同上

4. test_step8_calls_stop_sync_via_to_thread：
   - AttributeError: 同上

5. test_step8_logs_error_on_stop_failure：
   - AttributeError: 同上

6. test_step8_timeout_not_limited_by_min_logic：
   - _CLEANUP_STEPS[8] 越界 IndexError

【翻绿过程】
1. utils/shutdown.py:_CLEANUP_STEPS 末尾追加 ("Step 8", "_step8_stop_embedded_postgres", True, 35.0)
2. 新增 _step8_stop_embedded_postgres 方法（参考 _step7_close_database_managers 的 asyncio.to_thread 模式）
3. app/window_lifecycle.py line 184-185 修订：start_watchdog(60.0) + do_cleanup(timeout_s=50.0, step_timeout_s=35.0)
4. app/window_lifecycle.py line 257-258 修订：start_watchdog(60) + do_cleanup(timeout_s=50.0, step_timeout_s=35.0)
5. line 227 保持 step_timeout_s=1.0 不变（D11）
6. 6 个测试全部通过（9.58s）

【验证命令】
    & "D:\Programs\Python\Python313\python.exe" -m pytest tests/unit/test_shutdown_step8.py -v --tb=short
    # 实际结果：6 passed in 9.58s
