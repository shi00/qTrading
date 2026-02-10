# Python Flet (v0.28.3+) 高性能 GUI 开发最佳实践指南

> **版本兼容性说明**: 本指南已针对 **Flet 0.28.3** 进行适配。Flet 0.21.0+ 引入了大量重构（如 `Control.on_...` 事件属性化），以下内容确保兼容最新 API。

## 1. 架构设计：MVVM 与关注点分离

### 1.1 核心原则

不要把所有逻辑都写在 `View` (UI组件) 中。保持 UI 层尽可能“薄”，只负责渲染和用户交互。

### 1.2 推荐架构

- **Model (数据层)**: 负责数据存储、API 调用、数据库操作。
- **ViewModel (业务逻辑层)**: 持有 UI 状态，处理数据转换，不依赖具体 UI 控件。
- **View (UI 层)**: 绑定 ViewModel 数据，通过回调或事件通知 ViewModel。

*你目前的 `HomeViewModel` (supervising controller)做得很好，建议在 `ScreenerView` 中也抽离出 `ScreenerViewModel`，将 `_full_results` (DataFrame) 和排序逻辑移出 View。*

## 2. 渲染性能优化 (Critical)

### 2.1 细粒度更新 (Granular Updates)

**这是 Flet 性能优化的核心。**

- **错误做法**: 修改了一个 Text 的值，却调用 `page.update()` 或父容器的 `self.update()`。这会导致整个页面或容器内的所有控件重绘。
- **最佳实践**: 仅调用变更控件的 `update()` 方法。

```python
# 👎 低效：更新整个页面/容器
self.status_text.value = "Loading..."
self.update() 

# 👍 高效：只更新文本控件
self.status_text.value = "Loading..."
self.status_text.update()
```

### 2.2 列表虚拟化 (Virtualization)

- **Column/Row**: 渲染所有子控件。如果有 1000 个子控件，即使只显示 10 个，也会全部渲染，导致极度卡顿。
- **ListView/GridView**: 支持按需渲染（虚拟化）。仅渲染屏幕可见区域的控件。
- **DataTable**: Flet 的 DataTable 目前**不是**虚拟化的。如果行数超过 100 行，性能会显著下降。
  - **解决方案**: 必须像你现在这样使用**分页 (Pagination)**。如果需要无限滚动表格，建议自行用 `ListView` + `Row` 模拟表格，或者等待 Flet 后续更新。

### 2.3 减少控件层级

Flutter/Flet 的渲染树越深，布局计算越耗时。

- 避免不必要的 `Container` 嵌套。
- 使用 `ControlRef` 来引用控件，而不是保存大量 `self.xxx` 变量，虽然 Python 中引用开销不大，但清晰的引用管理有助于内存释放。

## 3. 并发与异步 (Concurrency & Async)

Python 的 GIL (全局解释器锁) 会限制多线程计算性能，而 GUI 也就是主线程必须保持响应。

### 3.1 耗时操作绝不阻塞主线程

在 `ScreenerView` 中，如果 `DataProcessor.init_data()` 或 pandas 的 `sort_values` 涉及大量数据计算：

- **IO 密集型 (网络/磁盘)**: 使用 `async/await`。
- **CPU 密集型 (Pandas 计算/策略回测)**: 必须放入线程池或进程池，否则 UI 会假死。

```python
# 👎 阻塞 UI 线程
self._full_results.sort_values(...) 

# ✅ 最佳实践：使用 asyncio.to_thread 将计算放入线程池
await asyncio.to_thread(self.run_heavy_calculation) 

def run_heavy_calculation(self):
    # 这里运行复杂的 Pandas 运算
    df.sort_values(...)
    return df
```

### 3.2 避免过于频繁的 UI 更新

在流式接收数据时（如你的 AI 策略 `on_stream_result`），如果每秒触发 100 次 `update()`，UI 线程会崩溃。

- **解决方案**: 使用**节流 (Throttling)** 或 **批量更新 (Batching)**。缓存 0.5 秒内的数据，一次性添加到表格并 update。

## 4. Flet 0.28.3+ 特定变更与优化

### 4.1 事件处理器属性化

Flet 0.21.0+ 将 `on_click` 等事件从构造函数参数变为属性。虽然为了兼容性构造函数仍然支持传参，但建议尽量使用属性赋值或在构造函数中明确指定。
*你现在的代码 `ft.IconButton(on_click=...)` 是完全兼容且推荐的写法。*

### 4.2 Canvas (cv) 绘图性能

Flet 0.28.x 对 `cv.Canvas` 进行了优化。对于复杂的自定义绘图（如分时图、K线图），**强烈建议使用 `cv.Canvas`** 而不是堆砌 `Stack` 和 `Container`。

- **Paint 对象复用**: `cv.Paint` 对象创建有开销，应该在 `__init__` 中预创建好，绘图时反复使用。
- **Path 对象复用**: 同样，复杂的 `cv.Path` 对象也应该尽量复用或增量更新。

### 4.3 覆盖层 (Overlay)

你已经在代码中使用了 `page.overlay.append(dialog)`，这是 0.21+ 引入的正确做法，替代了旧的 `page.dialog = ...`。确保在 `page.overlay.remove(dialog)` 手动清理关闭的对话框，防止内存泄漏。

### 4.4 异步支持

Flet 0.28.3 对异步事件处理器的支持非常完善。确保你的事件处理器（如 `on_click`）如果是异步的，定义为 `async def`，Flet 会自动调度它们。

## 5. 状态管理与资源释放

### 5.1 生命周期 hooks

利用 `did_mount` 和 `will_unmount` 管理资源。

- 在 `did_mount` 启动定时器、订阅事件。
- 在 `will_unmount` 取消订阅、停止定时器、关闭连接。
*(你已经在 `ScreenerView` 中使用了 `I18n.unsubscribe`，非常好)*

### 5.2 避免内存泄漏

- Flet 的控件是 Python 对象。如果在一个长列表（如日志 Log View）中不断 append `Text` 控件而不清理，内存会无限增长。
- **策略**: 限制日志行数（例如只保留最近 1000 行），或者使用 `ListView` 的虚拟化特性。

## 6. 打包与发布 (Styling & Packaging)

### 6.1 样式统一

使用 `Trace` 级别的常量或 `Theme` 类管理样式，避免散落在代码各处的硬编码颜色和字体。Flet 支持全局 Theme 设置，通过 `page.theme` 配置。

### 6.2 生产环境构建

- 使用 `flet build` 打包为独立可执行文件。
- 对于 Windows，确保设置 `--product` 参数以去除调试控制台。
- 确实使用 `assets` 目录管理图片和静态资源，而不是绝对路径。
