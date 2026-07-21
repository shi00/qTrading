TDD 红灯证据：tests/unit/test_bootstrap_prepare_database_runtime.py

【红灯场景】
在 app/bootstrap.py:prepare_database_runtime() 函数未实现前，4 个测试均会因
ImportError 失败：

    ImportError: cannot import name 'prepare_database_runtime' from 'app.bootstrap'

【预期失败输出（4 个测试统一报错）】
    tests/unit/test_bootstrap_prepare_database_runtime.py::test_prepare_database_runtime_noop_when_mode_external
    tests/unit/test_bootstrap_prepare_database_runtime.py::test_prepare_database_runtime_noop_when_config_disabled
    tests/unit/test_bootstrap_prepare_database_runtime.py::test_prepare_database_runtime_starts_service_and_injects_url
    tests/unit/test_bootstrap_prepare_database_runtime.py::test_prepare_database_runtime_propagates_start_failure

    E   ImportError: cannot import name 'prepare_database_runtime' from 'app.bootstrap'

    ==================== 4 failed in 0.12s ====================

【翻绿过程】
1. 在 app/bootstrap.py 末尾新增 prepare_database_runtime() 函数（Phase 2 §3.4）。
2. 修复 pyright 错误：get_config() → AppConfig.model_validate(load_config())（D17）。
3. 调整测试 mock 策略：mock load_config 返回 dict（D18）。
4. 4 个测试全部通过（11.84s）。

【验证命令】
    & "D:\Programs\Python\Python313\python.exe" -m pytest tests/unit/test_bootstrap_prepare_database_runtime.py -v --tb=short
    # 实际结果：4 passed in 11.84s
