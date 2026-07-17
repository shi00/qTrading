# 标准开发工作流 (How-To)

> 来源：从 CONTRIBUTING.md 迁移

### 1. 新增一张数据表

1. 在 `data/persistence/models.py` 中添加 SQLAlchemy ORM 模型 (继承 `Base`)。
2. 在 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 中注册：表名 → 同步配置、质量监控配置、依赖关系。
3. 运行 `python -m alembic revision --autogenerate -m "add xxx table"`，**人工检查** 生成的迁移文件。
4. 运行 `python -m alembic upgrade head` 验证。
5. 若需要 DAO 访问，参考[新增一个 DAO](#2-新增一个-dao)。

### 2. 新增一个 DAO

1. 在 `data/persistence/daos/` 下创建 `xxx_dao.py`，继承 `BaseDao`。
2. 实现读写方法，**只用** `_read_db_select` / `_save_upsert` / `chunked_in_query`，禁止裸 SQL 字符串拼接。
3. 在 `data/cache/cache_manager.py` 的 `CacheManager.__init__` 中实例化：`self.xxx_dao = XxxDao(self.engine)`。
4. 在 `CacheManager._create_engine` 中更新 `.engine` 引用：`self.xxx_dao.engine = self.engine`。
5. 在 `tests/unit/` 下编写对应单测，使用 mock engine 隔离 DB。

### 3. 新增一个策略

1. 在 `strategies/` 下创建 `xxx_strategy.py`。
2. 使用 `@register_strategy("key")` 装饰器注册；继承 `BaseStrategy` (普通) 或 `PolarsBaseStrategy` (向量化)。
3. 声明 `required_context_keys` / `required_tables` / `required_history_days`。
4. 若需访问 LLM，使用 `AIStrategyMixin` 混入；Prompt 添加到 `strategies/strategy_prompts.py`。继承 `PolarsBaseStrategy` 时已自带 AI 阶段（可通过 `enable_ai_analysis = False` 关闭）。
5. 在 `strategies/all_strategies.py` 的 `_import_all_strategies()` 中导入该模块以触发自动注册。
6. 在 `locales/` 添加 `strategy_xxx` / `strategy_xxx_desc` 等 i18n key。
7. 在 `tests/unit/` 下编写单测。

### 4. 新增一个 UI 视图

1. 先确认 `ui.hooks.use_viewmodel` 是否已满足当前 ViewModel 消费需求；若未满足，先实现/扩展该 hook（见 [MVVM 表现层](#mvvm-表现层)）。
2. 在 `ui/viewmodels/` 下创建对应 ViewModel：暴露不可变 state snapshot、commands、`subscribe(callback) -> unsub`，禁止 import Flet、禁止持有 Flet 控件、禁止调 `page.update()`/`control.update()`。
3. 在 `ui/views/` 下创建 `@ft.component` 声明式 View：只读取 state 渲染控件树，只在事件中调用 commands；禁止 `did_mount`/`will_unmount`/`self.update()`/`UserControl`/`PageRefMixin`（见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范)）。
4. i18n 文案由 VM 输出 key + params，View 按当前 locale 渲染；locale 变化作为 View 层声明式状态源触发重渲染（见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范) 中的 i18n 状态驱动规则）。
5. 响应式布局优先使用声明式 state / props / `ResponsiveRow`，禁止新增 `handle_resize` 鸭子分发式命令式代码。
6. 若需注册新标签页，再修改 `ui/app_layout.py`。
7. UI 事件中的同步 IO/CPU 密集任务必须通过 `ThreadPoolManager.run_async()` 或 `TaskManager.submit_task()` 提交，避免阻塞 Flet 主循环（对应 CLAUDE.md §3.1 R16）。
8. UI 事件回调使用 `@log_ui_action` 装饰器埋点。
9. 按 [变更类型 → 最小验证子集](#变更类型--最小验证子集) 运行 UI 相关验证。

### 5. 新增一个外部数据源

1. 在 `data/external/` 下创建客户端模块，封装第三方 SDK 或 HTTP API。
2. 使用 `utils/rate_limiter.py` 提供的限流器避免触发对方风控。
3. 网络错误必须用 `classify_error(e, context="general")` 分类，自动处理重试。
4. 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)`。
5. 若需走代理，使用 `utils/proxy_manager.py`。

### 6. 新增与升级依赖

1. **编辑依赖配置**：
   - 编辑 `pyproject.toml`：
     - 运行时依赖加到 `[project] dependencies`
     - 开发依赖加到 `[project.optional-dependencies] dev`
     - 可选依赖加到 `[project.optional-dependencies] optional`
   - 若要升级已有依赖，可运行 `uv lock --upgrade` 更新锁文件。
2. **生成与编译 `requirements*.txt`**：
   - **自动化生成**：在 `git commit` 时，本地 pre-commit 钩子会自动运行 `uv pip compile` 重新编译所有的 `requirements*.txt`。
   - **手动即时生成（用于本地即时升级调试）**：若在 commit 前需要使升级或新依赖立即在本地生效，请手动编译：
     ```bash
     uv pip compile --universal --no-emit-index-url pyproject.toml -o requirements.txt
     uv pip compile --universal --no-emit-index-url --extra dev pyproject.toml -o requirements-dev.txt
     uv pip compile --universal --no-emit-index-url --extra optional pyproject.toml -o requirements-optional.txt
     ```
3. **本地安装新依赖**：运行以下命令将编译后的依赖同步到本地环境：
   ```bash
   uv pip install --system -r requirements.txt -r requirements-dev.txt
   # 如需可选功能：
   uv pip install --system -r requirements-optional.txt
   ```

### 7. 新增回测配置

1. 在 `strategies/backtest/config.py` 中定义回测参数 (`BacktestConfig`)。
2. 在 `strategies/backtest/adapter.py` 中适配待回测的策略。
3. 通过 `services/backtest_service.py` 的 `run_backtest()` 启动。
4. 结果通过 `BacktestDAO` 持久化，由 `ui/views/backtest_view.py` 展示。

### 8. 新增一个单例

1. 使用 `@register_singleton` 装饰器注册类（代码模板见[单例模式实现模板](#单例模式实现模板)）。
2. 实现 `_reset_singleton()` 类方法 (测试隔离必须)。
3. 实例创建必须受 `threading.Lock` 保护 (优先在 `__new__` 中持锁)。
4. 支持 `_initialized` 标志防止重复初始化。
5. 如需进程退出清理，实现 `_atexit_cleanup()` 类方法。
6. 在 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式) 的单例列表中补充新单例名称。
7. 在 `tests/unit/` 下编写单测；常规隔离由 `_reset_all_singletons` autouse fixture 自动处理，需精细控制单例初始化状态时使用 `singleton_state` 上下文管理器。
