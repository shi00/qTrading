# P1-2 污染探测与 function 作用域降级耗时评估

> 关联文档：[unit_test.md](./unit_test.md) P1-2、[CLAUDE.md](../../CLAUDE.md) §7.2
> 创建日期：2026-06-19

---

## 1. P1-2 问题：会话级共享事件循环导致跨测试状态污染

### 1.1 现状

`pyproject.toml` 配置：

```toml
asyncio_default_fixture_loop_scope = "session"
asyncio_default_test_loop_scope = "session"
```

所有单元测试共享一个 session 级事件循环。绑定到循环的对象（`asyncio.Event` / `asyncio.Lock` / `asyncio.Semaphore`、loop-local 缓存）会在测试间泄漏。

### 1.2 当前缓解措施

`tests/conftest.py` 通过一组 autouse fixture "打扫"会话级共享状态留下的脏数据：

| Fixture | 作用 | 位置 |
|---------|------|------|
| `reset_loop_local_cache` | 清理 `utils.loop_local` 的 loop-bound 缓存 | `tests/conftest.py` |
| `reset_config_cache` | 清理 `ConfigHandler._config_cache` | `tests/conftest.py` |
| `_reset_mock_keyring_store` | 清理 mock keyring 密码存储 | `tests/conftest.py` |
| `_reset_all_singletons` | 重置所有注册单例 | `tests/unit/conftest.py` |

`reset_loop_local_cache` 的注释直接坦白了污染：

> Tests like TestAIServiceSemaphoreSeparation.test_reload_config_invalidates_both_semaphores
> store string values in the cache which break subsequent tests expecting asyncio.Semaphore.

### 1.3 风险

这类隐藏耦合最危险：单独跑某个测试通过，全量跑或换执行顺序就失败，排查成本极高。

---

## 2. 污染探测测试方案

### 2.1 测试文件

`tests/unit/test_pollution_detection.py`

### 2.2 探测策略

1. **选取核心子集**：AI 服务 / 任务管理器 / 数据处理器相关测试（频繁使用单例、事件循环、loop-local 缓存，最可能暴露跨测试污染）：

   ```
   tests/unit/test_ai_service.py
   tests/unit/test_ai_service_failover.py
   tests/unit/test_ai_service_prompt_dump_retention.py
   tests/unit/test_ai_mixin.py
   tests/unit/test_ai_strategy.py
   tests/unit/test_ai_history_text.py
   tests/unit/test_task_manager.py
   tests/unit/test_data_processor.py
   ```

2. **随机打乱顺序**：用 `subprocess` 调用 pytest，通过 `random.Random(seed).shuffle()` 打乱文件顺序，每次运行用不同种子（`42 + run_idx`，固定种子保证可复现）。使用 `-p no:randomly` 显式禁用随机排序插件，确保测试按指定文件顺序执行。

3. **多次运行**：跑 3 次，每次顺序不同。

4. **比较结果**：解析 pytest `-v` 输出，提取每个测试的 PASSED/FAILED/ERROR/SKIPPED 状态。若某测试在某次运行中通过、在另一次运行中失败，判定为污染。

5. **错误消息**：探测到污染时，列出状态不一致的测试，并提示可能的污染源（单例未隔离 / loop-local 缓存泄漏 / 事件循环绑定对象跨测试复用 / 模块级状态泄漏）。

### 2.3 标记

- `@pytest.mark.slow`：因多次跑耗时（实测约 100s）。
- 快速检查 `test_core_subset_files_exist`（未标记 slow）：验证核心子集文件存在，不触发 subprocess。

### 2.4 与 test_infra_loop_isolation.py 的关系

两者互补：

| 测试文件 | 验证目标 |
|---------|---------|
| `test_infra_loop_isolation.py` | `reset_loop_local_cache` fixture 的清理有效性（单元级） |
| `test_pollution_detection.py` | 清理有效性在实际多测试场景下是否足够（集成级） |

### 2.5 验证结果

```
python -m pytest tests/unit/test_pollution_detection.py -v -m "slow"
→ 1 passed in 100.47s
```

3 次运行（8 文件 × 3 = 24 文件次）均无污染，说明当前 autouse fixture 有效。

---

## 3. 降为 function 作用域的耗时评估方法

### 3.1 评估方法

`pyproject.toml` 已配置 `addopts = "--durations=20 --durations-min=1.0"`，可直接量化前后耗时差异。

#### 步骤 1：量化当前（session 作用域）耗时

```bash
python -m pytest tests/unit/ -m "not slow" --durations=20 --durations-min=1.0
```

记录：
- 总耗时
- 最慢的 20 个测试/fixture 的耗时
- session 级 fixture 的 setup/teardown 耗时（应为一次性）

#### 步骤 2：临时降为 function 作用域

修改 `pyproject.toml`：

```toml
asyncio_default_fixture_loop_scope = "function"
asyncio_default_test_loop_scope = "function"
```

#### 步骤 3：量化降级后耗时

```bash
python -m pytest tests/unit/ -m "not slow" --durations=20 --durations-min=1.0
```

记录同上。重点关注：
- 总耗时增量（每个测试创建新事件循环的开销 × 测试数）
- 是否有测试因作用域变更而失败（这些测试可能隐式依赖 session 级共享循环）

#### 步骤 4：对比分析

| 指标 | session 作用域 | function 作用域 | 增量 |
|------|---------------|-----------------|------|
| 总耗时 | 待量化 | 待量化 | - |
| 最慢 fixture | - | - | - |
| 失败测试数 | 0 | 待量化 | - |

### 3.2 预期影响

- **耗时增加**：每个测试创建/销毁事件循环的开销约 1-5ms，6000+ 测试预计增加 6-30s。
- **可删除的 fixture**：降级后可删除 `reset_loop_local_cache` autouse fixture（`tests/conftest.py`），以及 `test_infra_loop_isolation.py` 和 `test_pollution_detection.py` 两个探测测试。
- **潜在失败**：少数测试可能隐式依赖 session 级共享循环（如跨测试共享 `asyncio.Queue`），需逐一修复。

---

## 4. 不实际降级的理由

### 4.1 当前缓解措施有效

污染探测测试（3 次随机打乱顺序运行核心子集）验证当前 autouse fixture 有效控制了污染。在探测测试发现污染之前，降级属于预防性重构，优先级低于功能开发。

### 4.2 降级成本未量化

降级需要：
1. 量化 6000+ 测试的耗时影响（§3.1 步骤）
2. 修复因作用域变更而失败的测试（数量未知）
3. 删除 `reset_loop_local_cache` fixture 及相关探测测试
4. 全量回归验证

这是一个需要专门重构窗口的工作，不宜在功能开发中夹带。

### 4.3 根因未消除

即使降为 function 作用域，生产代码中仍存在全局可变状态（`ConfigHandler` 静态方法 + 全局缓存、众多 `@register_singleton` 单例、模块级 `CONFIG_FILE`）。降级只是从测试侧消除 loop-local 泄漏，不从生产代码侧消除可测性差的根因（参见 `unit_test.md` A1）。

### 4.4 路线图

按 `unit_test.md` A7 路线图：

| 阶段 | 事项 | 状态 |
|------|------|------|
| 现在 | 污染探测测试（本文件） | ✅ 已完成 |
| 近期 | 探测测试纳入 CI slow 轨道 | 待办 |
| 中期 | 量化降级耗时影响（§3.1） | 待办 |
| 中长期 | 实际降为 function 作用域 + 删除清理 fixture | 待办 |
| 长期 | 生产代码依赖注入改造，消除全局可变状态 | 待办 |

---

## 5. 参考

- [CLAUDE.md](../../CLAUDE.md) §7.2 测试规范 — 事件循环策略与 P1-2 技术债说明
- [unit_test.md](./unit_test.md) P1-2 — 会话级共享事件循环导致跨测试状态污染
- [unit_test.md](./unit_test.md) A1 — 测试可测性差是根因
- [unit_test.md](./unit_test.md) A7 — 架构改进路线图
- `tests/unit/test_infra_loop_isolation.py` — reset_loop_local_cache fixture 清理有效性验证
- `tests/unit/test_pollution_detection.py` — 跨测试污染探测（本文件对应测试）
