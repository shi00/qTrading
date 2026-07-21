TDD 红灯证据：tests/unit/test_main_bootstrap_order.py

【红灯场景】
在 main.py 未插入 `from app.bootstrap import prepare_database_runtime` 与
`await prepare_database_runtime()` 调用前，2 个测试会失败：

1. test_main_imports_prepare_database_runtime：
   - AssertionError: main() 源码应含 'from app.bootstrap import prepare_database_runtime'

2. test_prepare_database_runtime_called_before_cache_manager：
   - AssertionError: main() 源码应含 'prepare_database_runtime()' 调用
   - 或 prepare_pos == -1（find 返回 -1 表示未找到）

【翻绿过程】
1. main.py line 103 与 111 之间插入：
   ```python
   # Phase 2 §3.4：embedded 模式下启动 sidecar 并注入 URL
   from app.bootstrap import prepare_database_runtime
   await prepare_database_runtime()
   ```
2. import 在 main() 函数内（避免顶层循环导入）
3. 顺序约束：ConfigHandler.ensure_defaults() → prepare_database_runtime() → CacheManager()
4. 2 个测试全部通过（13.04s）

【验证命令】
    & "D:\Programs\Python\Python313\python.exe" -m pytest tests/unit/test_main_bootstrap_order.py -v --tb=short
    # 实际结果：2 passed in 13.04s
