# 向导页面 Loading Overlay 遮罩方案深度审查

## 审查范围

- [onboarding_wizard.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/ui/views/onboarding_wizard.py) — 遮罩核心实现
- [local_model_config_panel.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/ui/components/config_panels/local_model_config_panel.py) — 面板自身的加载态
- [database_config_panel.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/ui/components/config_panels/database_config_panel.py) — 数据库验证面板

---

## 一、架构设计评价

**总体方案是合理的：** 使用 `ft.Stack` 将 `loading_overlay` 叠加在主内容之上，配合 `_validation_in_progress` 标志位控制导航按钮的 `disabled` 状态。这种模式是 Flet 中常见且正确的遮罩实现。

---

## 二、发现的问题

### 🔴 BUG 1：数据库验证（Step 1）缺少遮罩

```python
# onboarding_wizard.py L972-983
async def _validate_and_save_database(self) -> bool:
    result = await self.database_panel.test_connection()  # ← 网络I/O耗时操作
    if result:
        config = self.database_panel.get_config()
        ConfigHandler.save_db_config(...)
    return result
```

`test_connection()` 会发起 PostgreSQL 连接测试，这是一个网络 I/O 耗时操作（网络超时可能数秒），但**没有调用 `_show_loading_overlay(True/False)`**。用户在等待数据库连接测试期间可以随意操作。

> [!IMPORTANT]
> `DatabaseConfigPanel.test_connection()` 内部虽然有自己的 `btn_test.disabled = True` 逻辑，但 **Wizard 的导航栏按钮（上一步/下一步）未被禁用**，用户仍可切换步骤。

---

### 🔴 BUG 2：Token 验证（Step 2）缺少遮罩

```python
# onboarding_wizard.py L985-1018
async def _validate_and_save_token(self) -> bool:
    # ...
    client = TushareClient(token=token)
    client.get_trade_dates(...)  # ← 同步网络请求，会阻塞 await 中的事件循环
    # ...
```

Token 验证做了 Tushare API 调用（HTTP 网络请求），同样**没有遮罩**。更严重的是 `get_trade_dates` 看起来是**同步调用**（非 `await`），在 `async` 函数中执行同步网络请求会阻塞事件循环。

> [!WARNING]
> 如果 `get_trade_dates` 是同步阻塞的，UI 在此期间会完全冻结（无法响应任何操作），但 overlay 也不会显示——用户会感到应用无响应。应考虑使用 `asyncio.to_thread()` 包装。

---

### 🟡 BUG 3：`_show_loading_overlay` 与面板自身加载态的竞争条件

在 **Local Model 步骤**中，wizard 调用了：
```python
# Wizard 显示 overlay
self._show_loading_overlay(True)
# 然后调用面板的验证方法
await self.local_model_panel.async_verify_model()
```

而 `LocalModelConfigPanel.async_verify_model()` 内部**也有自己的加载态管理**：
```python
# local_model_config_panel.py L378
self._set_loading_state(True)  # 禁用面板内的按钮、输入框
```

这导致**双重加载态叠加**，虽然不会造成功能错误，但存在以下问题：
- 面板内部显示了自己的 `ProgressRing`（通过 `progress_indicator`），同时 wizard 的 overlay 也显示了一个 `ProgressRing` —— 用户看到两个加载指示器
- `_set_loading_state(False)` 会在 `async_verify_model` 完成时被调用，但此时遮罩可能还在（取决于执行顺序）

---

### 🟡 BUG 4：导航栏遮罩不完整 — 底部按钮虽然 `disabled` 但不在 overlay 遮挡范围内

```python
# L223-240
self.content = ft.Stack(
    controls=[
        ft.Column(            # ← 主内容
            controls=[
                self.header_container,
                self.step_indicators,
                self.step_content_container,
                self.navigation_bar,    # ← 导航栏在主 Column 内
            ],
        ),
        self.loading_overlay,  # ← overlay 覆盖整个 Stack
    ],
)
```

`loading_overlay` 使用了 `expand=True`，在 `ft.Stack` 中应该能覆盖全部区域（包括导航栏区域）。**但需要注意：**

按钮的 `disabled` 属性在 `_show_loading_overlay` → `_update_navigation_buttons` → `_build_navigation_buttons` 链路中被正确处理了 ✅，但存在以下隐患：

- 如果 `ft.Stack` 中 overlay 无法完全阻断底层控件的鼠标事件（取决于 Flet 版本的事件穿透行为），用户可能仍能点击已 disabled 但视觉上未被遮挡的按钮

> [!TIP]
> 建议给 `loading_overlay` 添加 `on_click` 事件捕获（空 handler），确保事件不穿透：
> ```python
> self.loading_overlay = ft.Container(
>     ...,
>     on_click=lambda e: None,  # 阻止事件穿透
> )
> ```

---

### 🟡 BUG 5：面板内部按钮（验证/选择文件）在遮罩期间未被拦截

当 wizard 的 `loading_overlay` 显示时：
- `LocalModelConfigPanel` 内部的「验证模型」按钮和「选择文件」按钮
- `DatabaseConfigPanel` 内部的「测试连接」按钮
- `LLMConfigPanel` 内部的「测试连接」按钮

这些按钮在 overlay **下方**，理论上被遮罩覆盖，但：
1. 如果事件穿透（见 BUG 4），用户可以触发二次验证
2. 这些面板也可能在嵌入 settings tab 时独立使用，那里**没有** wizard 的 overlay

---

### 🟡 BUG 6：`loading_overlay` 文本未跟随 i18n 刷新

```python
# L204-221
self.loading_overlay = ft.Container(
    content=ft.Column([
        ft.ProgressRing(...),
        ft.Text(
            I18n.get("wizard_validating"),  # ← 构造时取值，后续语言切换不会刷新
            ...
        ),
    ]),
    ...
)
```

在 `_on_locale_change` 方法中**没有更新** overlay 内的文本。如果用户在验证期间切换语言（虽然概率低），overlay 文本不会翻译。

---

### 🟢 建议 1：使用 `try/finally` 保证遮罩关闭

当前 `_validate_and_save_cloud_ai` 和 `_validate_and_save_local_model` 方法中，`_show_loading_overlay(False)` 分散在多个分支中：

```python
async def _validate_and_save_cloud_ai(self) -> bool:
    self._show_loading_overlay(True)
    try:
        if not result.get("success"):
            self._show_loading_overlay(False)  # 分支1
            return False
        self._show_loading_overlay(False)      # 分支2
        return True
    except Exception:
        self._show_loading_overlay(False)      # 分支3
        return False
```

**推荐重构为 `try/finally` 模式**，避免遗漏：

```python
async def _validate_and_save_cloud_ai(self) -> bool:
    self._show_loading_overlay(True)
    try:
        result = await AIService.test_connection(...)
        if not result.get("success"):
            self.ai_status.value = result.get("message")
            self.ai_status.color = AppColors.ERROR
            self._safe_update()
            return False
        # ...success path...
        return True
    except Exception as e:
        # ...error handling...
        return False
    finally:
        self._show_loading_overlay(False)
        self._safe_update()
```

---

### 🟢 建议 2：`_validate_and_save_token` 中的同步网络调用应异步化

```python
# 当前（同步阻塞）
client = TushareClient(token=token)
client.get_trade_dates(start_date="20250101", end_date="20250101")

# 建议（异步化）
import asyncio
client = TushareClient(token=token)
await asyncio.to_thread(
    client.get_trade_dates, start_date="20250101", end_date="20250101"
)
```

这样才能在网络请求期间让 UI 保持响应，遮罩动画也才能正确渲染。

---

## 三、场景覆盖矩阵

| 步骤 | 验证方法 | 有网络 I/O？ | 有 overlay？ | 导航禁用？ | 问题 |
|------|---------|:-----------:|:----------:|:---------:|------|
| 0. Welcome | 无 | — | — | — | ✅ |
| 1. Database | `test_connection()` | ✅ | ❌ | ❌ | **遗漏** |
| 2. Token | Tushare API 调用 | ✅ | ❌ | ❌ | **遗漏** |
| 3. Cloud AI | `AIService.test_connection()` | ✅ | ✅ | ✅ | ✅ |
| 4. Local Model | `async_verify_model()` | ✅ | ✅ | ✅ | ✅（但有双重加载态） |
| 5. Data Sync | `initialize_system()` | ✅ | ❌ | ✅（sync_in_progress） | 同步步骤有独立的按钮控制 |
| 6. Schedule | 纯同步保存 | ❌ | ❌ | — | ✅（无需遮罩）|
| 7. Complete | 无 | — | — | — | ✅ |

---

## 四、修复优先级建议

| 优先级 | 问题 | 修复难度 |
|:------:|------|:-------:|
| 🔴 P0 | 数据库步骤缺少 overlay | 低 |
| 🔴 P0 | Token 步骤缺少 overlay | 低 |
| 🟡 P1 | token 验证同步阻塞事件循环 | 中 |
| 🟡 P1 | overlay 事件穿透防护 | 低 |
| 🟡 P2 | 本地模型双重加载指示器 | 低 |
| 🟡 P2 | overlay 文本 i18n 刷新 | 低 |
| 🟢 P3 | `try/finally` 重构确保遮罩释放 | 低 |
