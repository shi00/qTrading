# 单元测试重构与优化设计方案 (Unit Test Refactoring & Optimization Design)

## 背景 (Background)
AStockScreener 项目目前拥有 120+ 单元测试文件。但在持续集成（CI）和本地开发测试运行中，测试套件遇到了以下瓶颈：
1. **内存泄漏与崩溃 (MemoryError)**：单例生命周期未在测试之间隔离，造成后台线程、缓存和事件循环持续泄漏。在多进程并发（`pytest-xdist`）或长序列运行时，频繁抛出 `MemoryError`。
2. **全局 Mock 耗时过高**：每次测试函数执行都会动态装载/卸载数个 `patch`，产生了极大的额外 CPU 和调用栈管理开销。
3. **单例初始化冗余 I/O**：AI 服务单例初始化时，每次都会触发磁盘扫描和配置读取。
4. **测试文件碎片化**：多达 8 个模块拥有独立的 `*_coverage.py` 覆盖率测试文件，破坏了单元测试与主体逻辑的紧密耦合。

---

## 根本原因与改进设计 (Root Causes & Improvements)

### 1. 单例生命周期彻底隔离 (Singleton Isolation)
*   **根本原因**：[tests/unit/conftest.py](file:///d:/workspace/qTrading/tests/unit/conftest.py) 仅在 `autouse` 级别重置了 `AIService`，其余 10 个使用 `@register_singleton` 的单例（如 `ThreadPoolManager`、`CacheManager`、`TaskManager` 等）均未自动重置，导致测试间状态交叉污染与进程内存膨胀。
*   **改进设计**：
    在 `tests/unit/conftest.py` 中，编写全局的 `reset_all_singletons_autouse` 夹具，它会在每个单元测试的前后，利用 `utils.singleton_registry.reset_all_singletons()` 重置所有注册单例。同时移除原先的 `reset_ai_singleton` 夹具（因为 `AIService` 已被包含在全局单例重置中）。

### 2. 静态/Session 级 Mock 优化 (Global Mock Optimization)
*   **根本原因**：[tests/conftest.py](file:///d:/workspace/qTrading/tests/conftest.py) 中的 `mock_external_services` fixture 是 `scope="function"` 且 `autouse=True` 的。每次测试函数运行都会执行数个 `patch.start()` / `patch.stop()`，对 Python 的 `sys.modules` 和类属性进行频繁篡改，产生大量无谓开销。
*   **改进设计**：
    1. 将 `NewsFetcher` 的 `get_stock_news` 与 `get_us_major_moves`、`ReviewManager` 的 `get_learning_context` 移至 `pytest_configure` 中进行全局 Session 级 Patch（或通过模块覆盖），并在 `pytest_unconfigure` 中停用。
    2. 对于需要测试这些服务本身的特殊测试文件（如 `test_news_fetcher.py`），提供局部的还原 fixture（如 `unmock_news_fetcher`），通过显式调用还原 original 属性，从而免除 99% 的单元测试对 patch 频繁启停的无谓消耗。

### 3. 测试环境下的冗余磁盘清理屏蔽 (Redundant I/O Elimination)
*   **根本原因**：`AIService` 初始化时会调用 `_cleanup_prompt_dumps()`。每次重置 AI 实例再次构建时，都会触发 `ConfigHandler.get_setting` 和 `os.listdir()` 磁盘文件时间扫描。
*   **改进设计**：
    在 [services/ai_service.py](file:///d:/workspace/qTrading/services/ai_service.py) 中进行运行环境识别：
    ```python
    def _cleanup_prompt_dumps(self) -> None:
        if "pytest" in sys.modules:
            return
        # 原有磁盘扫描逻辑
    ```

### 4. 碎片化 Coverage 文件物理合并 (Fragmented Test Files Merge)
*   **根本原因**：为了达成单文件覆盖率 (≥ 80%) 门禁，历史开发将异常分支和边缘用例剥离到了 `*_coverage.py` 中，增加了维护两个测试文件的开销。
*   **改进设计**：
    将以下 8 对测试文件中的 `*_coverage.py` 内容物理合并至对应的主测试文件中，合并后删除 `*_coverage.py`：
    - `test_ai_service_coverage.py` -> [test_ai_service.py](file:///d:/workspace/qTrading/tests/unit/test_ai_service.py)
    - `test_base_dao_coverage.py` -> [test_base_dao.py](file:///d:/workspace/qTrading/tests/unit/test_base_dao.py)
    - `test_health_mixin_coverage.py` -> [test_health_mixin.py](file:///d:/workspace/qTrading/tests/unit/test_health_mixin.py)
    - `test_historical_sync_coverage.py` -> [test_historical_sync.py](file:///d:/workspace/qTrading/tests/unit/test_historical_sync.py)
    - `test_local_model_manager_coverage.py` -> [test_local_model_manager.py](file:///d:/workspace/qTrading/tests/unit/test_local_model_manager.py)
    - `test_oversold_strategy_coverage.py` -> [test_strategy_oversold_context.py](file:///d:/workspace/qTrading/tests/unit/test_strategy_oversold_context.py)
    - `test_scheduler_service_coverage.py` -> [test_scheduler_service.py](file:///d:/workspace/qTrading/tests/unit/test_scheduler_service.py)
    - `test_tushare_client_coverage.py` -> [test_tushare_client.py](file:///d:/workspace/qTrading/tests/unit/test_tushare_client.py)

### 5. 采用 `pytest.mark.parametrize` 重构以精简冗长用例
*   **改进设计**：
    在合并测试文件的过程中，识别出输入输出结构单一、但重复编写了多个测试方法的用例。使用 `@pytest.mark.parametrize` 统一其输入参数与预期断言，减少 30%+ 的冗余代码行数，提高测试的可读性与扩展性。

---

## 验证计划 (Verification Plan)

### 自动化测试 (Automated Tests)
1. **单元测试完整性运行**：
   ```bash
   pytest tests/unit
   ```
   *预期结果*：所有单元测试（包含已合并的覆盖率分支测试用例）全部通过。
2. **多进程并发测试稳定性运行**：
   ```bash
   pytest tests/unit -n 4
   ```
   *预期结果*：多进程模式能够稳定跑通，无 `MemoryError` 且单例重置正常工作。
3. **覆盖率门禁校验**：
   ```bash
   python scripts/check_per_file_coverage.py
   ```
   *预期结果*：单文件覆盖率（≥ 80%）与全局覆盖率（≥ 85%）继续完全达标。
4. **性能对比校验**：
   对比优化前后的单元测试总耗时，验证测试速度提升的幅度。
