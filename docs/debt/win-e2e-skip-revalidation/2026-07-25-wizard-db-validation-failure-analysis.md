# test_wizard_db_validation_success Windows CI 失败根因分析

> **归档时间**：2026-07-25
> **分析对象**：`tests/e2e/test_onboarding_wizard.py::test_wizard_db_validation_success`
> **触发场景**：P3-WinE2E-Skip 复验（`--run-windows-skip` 临时取消 skipif markers）
> **CI run**：30138544395（commit af98983，no-sidecar matrix group，no-sidecar 被 cancelled） + 30145028141（commit 6163095，no-sidecar matrix group FAILED）
> **失败模式**：2 秒内失败（fill_textbox 阶段，非 fixture setup 阶段）
>
> **2026-07-25 更新**：基于 CI run 30145028141 实际日志证据，纠正原 B1 推测的根因 1（wizard_app fixture setup 失败）。实际根因为根因 5 变体（CanvasKit 字体网络加载失败致 textbox a11y 节点未渲染）。

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

## 3. 失败根因清单（基于 CI run 30145028141 实际日志证据）

### 根因 1（已确认，最高可能）：CanvasKit 中文字体网络加载失败 → textbox a11y 节点未渲染到 DOM

**CI 日志证据链**（CI run 30145028141，no-sidecar matrix group）：

1. **失败位置**：`tests/e2e/test_onboarding_wizard.py:151` 调用 `await wizard_page.fill_textbox(I18n.get("db_host"), db["host"])` 在 label="主机" 时失败
2. **失败异常**：`playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 3000ms exceeded.`
3. **失败 selector**：`[aria-label*="主机"] >> nth=0`（fallback 路径，state="attached"）
4. **主路径失败原因**：`loc_combined.wait_for(state="visible", timeout=8000)` 超时（textbox 不可见）
5. **浏览器控制台错误（关键证据）**：
   ```
   [BROWSER CONSOLE] warning: Flutter Web engine failed to complete HTTP request to fetch
   "https://fonts.gstatic.com/s/notosanssc/v37/k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaG9_FnYkldv7JjxkkgFsFSSOPMOkySAZ73y9ViAt3acb8NexQ2w.110.woff2":
   TypeError: Failed to fetch
   [BROWSER CONSOLE] error: Failed to load resource: net::ERR_FAILED
   ```
   日志中重复出现 30+ 次相同错误（多个 woff2 字体文件均加载失败）。

**机制**：
- Windows CI runner（windows-latest）网络环境无法访问 `fonts.gstatic.com`
- CanvasKit 渲染中文字体（NotoSansSC）依赖网络下载 woff2 字体文件
- 字体加载失败 → CanvasKit 无法渲染 textbox 文本 → a11y 语义树不生成对应节点
- `[aria-label*="主机"]` selector 找不到任何 DOM 节点 → `wait_for(state="attached")` 超时

**关键反证（证明非 fixture setup 失败）**：
- 同一 CI run 中 3 个 fake-sidecar 用例 PASSED（不依赖 fill_textbox）
- 同一 CI run 中 real-sidecar matrix group 3 用例全部 PASSED（同样不依赖 fill_textbox）
- wizard_app fixture 启动成功（否则所有依赖用例都会 setup 失败，但实际只有此 1 用例失败）
- 失败发生在用例 call 阶段（`fill_textbox` 调用），非 fixture setup 阶段

### 根因 2（已排除）：wizard_app session fixture 启动失败

**原 B1 推测**：fixture setup 阶段 3 秒内失败

**实际证据反驳**：
- CI 日志显示失败堆栈在 `tests/e2e/test_onboarding_wizard.py:151`（用例代码内），非 conftest.py fixture setup
- 同一 wizard_app fixture 也用于其他 wizard 测试（如 test_wizard_db_validation_failure），后者未失败
- session fixture 启动失败会让所有依赖用例 setup 失败，但实际只有此 1 用例失败

### 根因 3（已排除）：复验 CI 基础设施缺失

**原 B1 推测**：mock_assets 缺失 / Postgres 不可达

**实际证据反驳**：
- 同一 CI run 中 6 个 onboarding 用例（fake + real sidecar）PASSED，证明 CI 基础设施完整
- Postgres 服务正常启动（CI step 14 "Start PostgreSQL Service (Windows)" success）

### 根因 4（已排除）：`_parse_db_url` 解析失败

**原 B1 推测**：正则不匹配触发 pytest.skip

**实际证据反驳**：CI 显示 FAILED（非 SKIPPED），证明 `_parse_db_url` 解析成功

### 根因 5（已排除）：Flet 子进程崩溃

**实际证据反驳**：CI 日志显示 Flet 子进程正常响应 HTTP 200（`wait_until_ready` 通过），后续用例 call 阶段才失败

### 根因 6（已排除）：向导状态隔离问题

**实际证据反驳**：失败发生在第一个 `fill_textbox` 调用（主机字段），非状态污染导致的下游按钮找不到

## 4. skipif reason 更新判定

### 当前 reason
> "Windows Flet/Playwright CanvasKit textbox 渲染 + 向导状态隔离问题 (P3-WinE2E-Skip)"

### 判定：**需要更新**

**理由**：
1. 原部分正确：CanvasKit textbox 渲染问题确实存在，但触发机制是**字体网络加载失败**，非 Flet 本身渲染问题
2. 原部分错误：向导状态隔离问题已排除（失败发生在第一个 fill_textbox 调用，非状态污染）
3. 上游调研确认 Flet 0.86.2 engineRevision 未变，CanvasKit 行为预期与 0.86.0 相同
4. 技术债表已更新但 skipif reason 未同步

### 建议新 reason 文本

```python
reason = "Windows CI 环境 CanvasKit 中文字体（NotoSansSC）从 fonts.gstatic.com 网络加载失败（net::ERR_FAILED），textbox a11y 节点未渲染到 DOM，fill_textbox 在 wait_for(state='attached') 阶段超时 (P3-WinE2E-Skip)"
```

## 5. 诊断步骤（已完成）

1. **查看 CI run 的失败阶段日志**：✅ 失败堆栈在 `tests/e2e/test_onboarding_wizard.py:151`（用例 call 阶段，非 fixture setup）
2. **检查 CI 环境变量**：✅ `DATABASE_URL` 设置正确，`_parse_db_url` 解析成功（FAILED 非 SKIPPED）
3. **检查 Postgres 可达性**：✅ Postgres 服务正常启动，6 个 onboarding 用例 PASSED 证明 DB 可达
4. **检查浏览器控制台错误**：✅ 关键证据 - 30+ 次 NotoSansSC woff2 字体加载失败（`net::ERR_FAILED`）
5. **反证 fixture setup 失败**：✅ wizard_app fixture 启动成功（其他依赖用例未失败）

## 6. 核心结论

1. **根因确认**：Windows CI 环境下 CanvasKit 中文字体（NotoSansSC）从 `fonts.gstatic.com` 网络加载失败（`net::ERR_FAILED`），textbox a11y 节点未渲染到 DOM，`fill_textbox` 在 `wait_for(state="attached")` 阶段超时
2. **原 B1 推测修正**：原推测"fixture setup 阶段失败"错误；实际为"用例 call 阶段 fill_textbox 失败"
3. **skipif reason 更新**：从"CanvasKit textbox 渲染 + 向导状态隔离问题"更精确为"CanvasKit 中文字体网络加载失败致 textbox a11y 节点未渲染"
4. **修复方向（不在本 Phase 范围内）**：预下载 NotoSansSC woff2 字体到本地 `tests/e2e/mock_assets/fonts/`，配置 CanvasKit 离线字体加载（需修改 `tests/e2e/conftest.py` 启动配置）

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
