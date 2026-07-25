# test_wizard_db_validation_success Windows CI 失败根因分析

> **归档时间**：2026-07-25
> **分析对象**：`tests/e2e/test_onboarding_wizard.py::test_wizard_db_validation_success`
> **触发场景**：P3-WinE2E-Skip 复验（`--run-windows-skip` 临时取消 skipif markers）
> **CI run**：30138544395（commit af98983，no-sidecar matrix group）
> **失败模式**：3 秒内失败（fixture setup 阶段）

## 1. 用例代码逻辑

**文件**：`tests/e2e/test_onboarding_wizard.py` L124-164

```python
async def test_wizard_db_validation_success(wizard_page):
    db_url = os.environ.get("E2E_DATABASE_URL", _get_test_db_url())
    db = _parse_db_url(db_url)
    # 状态检查：若已在 DB 步骤则跳过点击 "开始使用"
    try:
        await wizard_page.expect_text(db_title, timeout_ms=2000)
    except Exception:
        await wizard_page.click_button(I18n.get("wizard_btn_start"))
        await wizard_page.expect_text(db_title)
    # 填表（5 个 textbox）
    await wizard_page.fill_textbox(I18n.get("db_host"), db["host"])
    # ...
    await wizard_page.page.wait_for_timeout(500)
    await wizard_page.click_button(I18n.get("wizard_btn_verify_next"))
    await wizard_page.expect_text(I18n.get("wizard_step1_title"))
```

**关键点**：
- `_parse_db_url` 不匹配时调用 `pytest.skip()`（不是 fail）
- 测试依赖 `wizard_page` fixture → `wizard_app` session fixture
- `wizard_app` 启动配置：`config={"locale": "zh"}`（未设 `onboarding_complete`）

## 2. wizard_page fixture 实现分析

**文件**：`tests/e2e/conftest.py` L1010-1019

```python
@pytest_asyncio.fixture(loop_scope="session")
async def wizard_page(e2e_browser, wizard_app: AppServer, request):
    fp = await _make_page(e2e_browser, wizard_app, request)  # 注意：未传 check_db_error=True
    yield fp
```

### 2.1 wizard_page 与 e2e_page 的关键差异

| 维度 | wizard_page | e2e_page |
|------|-------------|----------|
| 依赖 app fixture | `wizard_app` | `flet_app` |
| `check_db_error` 参数 | **未传（默认 False）** | `True`（fail-fast DB 错误检测） |
| app 配置 | `{"locale": "zh"}`（无 `onboarding_complete`） | `{"onboarding_complete": True, "locale": "zh"}` |

### 2.2 wizard_app 启动配置（conftest.py L968-983）

```python
@pytest.fixture(scope="session")
def wizard_app(tmp_path_factory):
    proc, url, cfg_file = _spawn(
        tmp_path_factory,
        config={"locale": "zh"},
        env_overrides={
            "TS_TOKEN": "e2e-dummy-token",
            "AI_API_KEY": "e2e-dummy-key",
            "DATABASE_URL": TEST_DATABASE_URL,
            "DB_PASSWORD": _E2E_DB_PASSWORD,
            "PYTHONKEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
```

`TEST_DATABASE_URL` 解析顺序：`DATABASE_URL` → `E2E_DATABASE_URL` → `_get_test_db_url()`。

### 2.3 start_flet_app 启动期检查

`start_flet_app`（app_launcher.py）内有两道 fail-fast 关卡：

1. **`wait_until_ready`**（L35-66）：轮询 HTTP 200，子进程崩溃立即 raise
2. **`_check_startup_errors`**（L96-127）：扫描日志错误模式
   - `_STARTUP_ERROR_PATTERNS`：`[Bootstrap] Database initialization failed` / `db_init_failed` / `Connection error getting revision` / `connection was closed in the middle of operation`
   - 任一错误模式命中 → `RuntimeError` → `proc.terminate()` → 异常上抛 → fixture setup 失败

## 3. 失败根因清单（按可能性排序）

### 根因 1（最高可能）：wizard_app session fixture 启动失败 → 用例 setup 阶段失败

**证据链**：
- "3 秒内失败"是典型的 fixture setup 失败特征（非用例 call 阶段超时）
- `start_flet_app` 启动期 `_check_startup_errors` 在 HTTP 200 后扫描日志 8 秒窗口，一旦命中 `db_init_failed` 立即 raise
- session 级 fixture 失败会让所有依赖用例 setup 失败

**可能触发场景**：
- CI 环境 `DATABASE_URL` 或 `E2E_DATABASE_URL` 设置但 Postgres 不可达
- `DatabaseMigrator.init_db` 失败（连接被拒绝、迁移失败）
- `DB_PASSWORD` 环境变量与 `TEST_DATABASE_URL` 中解析的密码不一致
- `_E2E_DB_PASSWORD` 经 `unquote_plus` 解码后与 CI 实际密码不匹配

### 根因 2（中等可能）：复验 CI 基础设施缺失

- `tests/e2e/mock_assets/canvaskit/canvaskit.wasm` 缺失（启动期断言）
- `tests/e2e/mock_assets/fonts/*.woff2` 缺失（启动期断言）
- 真实 Postgres 服务不可达

### 根因 3（中等可能）：`_parse_db_url` 解析失败但表现为 skip

- 正则 `postgresql\+asyncpg://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)` 严格匹配
- 不匹配会 `pytest.skip(...)`（CI 显示 skipped，不算 failed）
- 若 CI 显示是 "skipped" 而非 "failed"，则此根因成立

### 根因 4（较低可能）：Flet 子进程启动后立即崩溃

- Windows 环境下 `subprocess.Popen` 可能因路径分隔符、编码、权限等问题崩溃

### 根因 5（低可能）：CanvasKit textbox 渲染问题

**判定**：与"3 秒内失败"严重不符，textbox 渲染问题会表现为 `fill_textbox` 超时（30s+），**几乎可以排除**。

### 根因 6（低可能）：向导状态隔离问题

**判定**：状态隔离问题会让下游测试找不到按钮，表现为 `expect_text` 或 `click_button` 超时（30s+），与"3 秒失败"不符，**可排除**。

## 4. skipif reason 更新判定

### 当前 reason
> "Windows Flet/Playwright CanvasKit textbox 渲染 + 向导状态隔离问题 (P3-WinE2E-Skip)"

### 判定：**需要更新**

**理由**：
1. 与实际失败模式不符（实际 3 秒内失败 vs reason 声称的 textbox 渲染超时）
2. 上游调研确认 Flet 0.86.2 engineRevision 未变，CanvasKit 行为预期与 0.86.0 相同
3. 技术债表已更新但 skipif reason 未同步

### 建议新 reason 文本

```python
reason="Windows E2E 复验失败：实际失败模式为 fixture setup 阶段 3s 内失败（疑似 wizard_app 启动失败或环境变量缺失），非原登记的 CanvasKit textbox 渲染问题。详见 docs/debt/win-e2e-skip-revalidation/ 复验归档 (P3-WinE2E-Skip)"
```

## 5. 诊断步骤

1. **查看 CI run 的 setup 阶段日志**：
   - 检查失败堆栈是否在 `start_flet_app` / `wait_until_ready` / `_check_startup_errors`
   - 检查 `logs/e2e-flet-app.log` 中是否含 `db_init_failed` / `[Bootstrap] Database initialization failed`
2. **检查 CI 环境变量**：
   - `DATABASE_URL` / `E2E_DATABASE_URL` 是否设置
   - `DB_PASSWORD` 是否与 URL 中解析的密码一致
   - `CI_PG_PASSWORD` secret 是否正确注入
3. **检查 Postgres 可达性**：
   - CI job 是否有 Postgres service container
   - Windows runner 上 Postgres 端口是否可达
4. **检查 mock_assets**：
   - `tests/e2e/mock_assets/canvaskit/canvaskit.wasm` 是否在 worktree 中存在
   - `tests/e2e/mock_assets/fonts/*.woff2` 是否存在

## 6. 核心结论

1. **skipif reason 与实际失败模式严重不符**：reason 说"CanvasKit textbox 渲染 + 向导状态隔离问题"，但实际失败模式（3 秒内失败）更符合 fixture setup 阶段失败
2. **最可能根因**：`wizard_app` session fixture 启动失败（DB 不可达、环境变量缺失、PostgreSQL 服务问题）
3. **建议**：先诊断 CI 失败堆栈确定根因，再决定是更新 reason 文本还是修复环境/代码

## 7. 关键文件路径

- 测试用例：`tests/e2e/test_onboarding_wizard.py` L124-164
- `_parse_db_url`：同文件 L19-37
- `wizard_page` fixture：`tests/e2e/conftest.py` L1010-1019
- `wizard_app` fixture：同文件 L968-983
- `_make_page`：同文件 L367-476
- `start_flet_app`：`tests/e2e/helpers/app_launcher.py` L130-188
- `wait_until_ready`：同文件 L35-66
- `_check_startup_errors`：同文件 L96-127
- `fill_textbox`：`tests/e2e/helpers/flet_page.py` L133-217
- `--run-windows-skip` 实现：`tests/e2e/_windows_skip.py`
- 上游调研报告：`docs/debt/win-e2e-skip-revalidation/2026-07-25-upstream-research.md`
- 技术债表：`docs/debt/known-technical-debt.md` P3-WinE2E-Skip 条目
