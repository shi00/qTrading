# Phase 2.5 Code Review Gate

**Phase**: 2.5 — 测试基础设施前置(删除旧桩,建立 V1 原生契约)
**Review date**: 2026-07-09
**Reviewer**: Lead (harness-work solo mode)
**Verdict**: APPROVE

---

## 1. 范围

Phase 2.5 包含 7 个 task(Task 2.5.1-2.5.7),建立声明式 UI 改造所需的测试基础设施:

| Task | 内容 | 状态 | 验证 |
|------|------|------|------|
| 2.5.1 | mock_flet.py 改造:删除 `set_page` helper + `_install_v1_compat` 全局桩;新增 MockI18nState/MockAppColorsState fixture | cc:完了 | grep `set_page\b`=0, grep `_install_v1_compat`=0(仅 1 处历史注释) |
| 2.5.2 | render_helper.py:`render_component()` 无状态组件渲染 | cc:完了 [143f1d5] | 4 tests green |
| 2.5.3 | flet_test_page fixture:`ft.run_async` + `wait_for_render` | cc:完了 | spike 验证可用, probe 测试 Windows skip/CI Linux 运行 |
| 2.5.4 | integration/conftest.py 协同改造:删除旧桩导入 | cc:完了 | grep=0, 963 collected |
| 2.5.5 | mock_app_colors 重写:依赖 mock_app_colors_state | cc:完了 | 57 tests green (mock_contracts + theme + ui_i18n) |
| 2.5.6 | Phase 2.5 回归验收 | cc:完了 | grep=0, 9028 collected 100%, 2432 unit/ui passed, E2E DOM 探针未触及 ui//tests/e2e/ |
| 2.5.7 | 本 review gate | — | — |

---

## 2. 红线违规检查(R1-R17)

### R1 架构越界
- Phase 2.5 改动全部在 `tests/` 和 `pyproject.toml`,未触及 `ui/`/`core/`/`data/`/`services/`/`strategies/` 任何生产代码 ✓
- `git diff --name-only HEAD` 确认:18 个文件全部在 tests/ + pyproject.toml ✓

### R2 异常吞没(asyncio.CancelledError)
- `flet_test_page` fixture finally 块:`except asyncio.CancelledError: pass`(L316)
- **判定**:合规。fixture teardown 时 task.cancel() 后 await task,CancelledError 是 task 被主动取消的预期行为。其他 Exception 不吞没,会传播暴露 flet app 真实错误 ✓
- **修正记录**:初版 `except (asyncio.CancelledError, Exception): pass` 过宽,review 时修正为只 catch CancelledError(R2 合规)

### R3 模糊压制(# type: ignore 无 reason)
- `grep "type: ignore" tests/integration/conftest.py tests/unit/ui/conftest.py`:0 matches ✓

### R6 过时类型注解(Union[X, Y] / Optional[X])
- `grep "Union\[|Optional\[" tests/integration/conftest.py tests/unit/ui/conftest.py tests/unit/ui/render_helper.py`:0 matches ✓
- 全部使用 `X | None` / `X | Y` ✓

### R7 测试状态污染(单例未隔离)
- `mock_i18n_state`/`mock_app_colors_state` 用 `monkeypatch.setattr` 注入,per-test 自动还原 ✓
- `_v1_page_compat` autouse fixture 用 monkeypatch,per-test 自动还原 ✓

### R11 跨循环复用同步原语(asyncio.Event/Lock 作为类属性)
- `flet_test_page` fixture 内 `ready = asyncio.Event()` 为局部变量,非类属性 ✓
- fixture 作用域 session,Event 绑定 session loop,不跨循环复用 ✓

### 其他红线(R4/R5/R8-R10/R12-R17)
- Phase 2.5 不涉及 SQL/DAO/批量写入/单例注册/策略注册/数据表/UI 事件处理器 ✓
- R16 UI 阻塞主循环: N/A(测试基础设施,不含 Flet 事件处理器)

---

## 3. 契约一致性

### 3.1 mock_flet V1 原生契约对齐(方案 §3.3.1)

| 项 | 状态 | 验证 |
|----|------|------|
| `_install_v1_compat_control_page_mock()` 全局桩删除 | ✓ | grep=0 |
| `set_page(control, page)` helper 删除 | ✓ | grep `set_page\b`=0 |
| `_v1_page_compat` autouse fixture 替代(unit/ui + integration 双侧) | ✓ | monkeypatch per-test 隔离 |
| MockFletPage 对齐 V1 原生 `ft.Page` 契约 | ✓ | 保留 MockClientStorage/MockSession/overlay/services 等字段 |
| MockI18nState fixture(monkeypatch I18n._state) | ✓ | `mock_i18n_state` 注入 I18nState(locale=DEFAULT_LOCALE) |
| MockAppColorsState fixture(monkeypatch AppColors._state) | ✓ | `mock_app_colors_state` 注入 AppColorsState(theme_name=DARK) |

### 3.2 render_helper 契约(方案 §3.3.2)

| 项 | 状态 | 验证 |
|----|------|------|
| `render_component(component, **props)` 函数签名 | ✓ | 返回 ft.Control |
| 无状态组件:通过 `__wrapped__` 绕过 Renderer | ✓ | L35: `getattr(component, "__wrapped__", component)` |
| 有状态组件:抛 RuntimeError | ✓ | docstring 明确,use_state/use_effect 走 flet_test_page |
| 限制说明清晰 | ✓ | docstring + spike 验证注释 |

### 3.3 flet_test_page fixture 契约(方案 §3.3.3)

| 项 | 状态 | 验证 |
|----|------|------|
| `FletTestPage` dataclass(page + wait_for_render) | ✓ | L256-280 |
| session 作用域,避免每次重启 app | ✓ | `@pytest_asyncio.fixture(scope="session")` |
| `wait_for_render(timeout=2.0, expected_controls=None)` | ✓ | 轮询 page.controls 长度,超时抛 TimeoutError |
| spike 验证:ProactorEventLoop 下 60s 内捕获 page | ✓ | `python -m tests.integration._spike_flet_run_async` |
| Windows selector loop 限制说明 | ✓ | docstring + probe skipif |
| `no_db` marker:跳过 DB autouse fixture | ✓ | probe 测试不依赖 DB |

### 3.4 mock_app_colors 重写(方案 §3.2 H2)

| 项 | 状态 | 验证 |
|----|------|------|
| 依赖 `mock_app_colors_state` 注入 AppColors._state | ✓ | fixture 参数依赖 |
| 保持 `create_autospec(AppColors, instance=True)` | ✓ | 命令式 View 旧测试兼容 |
| 复制公开数据属性(str/int/float) | ✓ | L111-116 dir() 遍历 |
| 声明式组件通过 `get_observable_state()` 拿到 mock state | ✓ | monkeypatch 注入 _state |

---

## 4. 回归验收(Task 2.5.6 DoD)

| DoD 项 | 结果 |
|--------|------|
| `grep -rn "set_page\b" --include=*.py tests/` = 0 | ✓ 0 matches |
| `pytest tests/unit/ tests/integration/ --co` 收集成功率 100% | ✓ 9028 tests collected |
| E2E DOM 透明性探针 | ✓ Phase 2.5 未触及 ui/ 和 tests/e2e/,DOM 选择器不受影响 |
| unit/ui 回归 | ✓ 2432 passed |

### 额外验证
- ruff check:通过 ✓
- ruff format:通过 ✓
- pyright:0 errors(4 warnings 均为既有问题,非本次引入)✓

---

## 5. 风险与限制

### 5.1 Windows selector loop 限制(已知)
- `ft.run_async` 的 socket server 不兼容 `WindowsSelectorEventLoop`(抛 NotImplementedError)
- pytest-asyncio 在 Windows 强制 selector policy(tests/conftest.py L25)
- 影响:Windows 本地无法运行依赖 `flet_test_page` fixture 的测试
- 缓解:probe 测试加 `skipif(win32)`,CI Linux 验证;本地 spike 工具 `_spike_flet_run_async.py` 用 ProactorEventLoop 验证

### 5.2 probe 测试 CI 依赖
- probe 测试(3 个)在 Windows skip,CI Linux(ubuntu-latest)真正运行
- 首次运行需下载 Flet bundle(~60s),CI 缓存预热后较快

### 5.3 `no_db` marker 新增
- pyproject.toml markers 新增 `no_db` 注册
- `db_schema_ready` autouse fixture 改延迟加载 `test_engine`,仅在需要 DB 的测试中创建
- 风险:若未来有测试忘记加 `no_db` marker 又依赖 DB 不可用环境,会失败 —— 但这是测试本身的契约问题,非基础设施问题

---

## 6. 结论

Phase 2.5 测试基础设施前置任务全部完成:
- 旧桩(`set_page` + `_install_v1_compat`)清零,V1 原生契约对齐
- 3 个新基础设施(render_helper / flet_test_page / mock state fixtures)契约清晰
- 回归验收全绿(9028 collected, 2432 unit/ui passed)
- 红线 R1-R17 无违规

**Verdict: APPROVE** — 可进入 Phase 3(声明式 View 重写)
