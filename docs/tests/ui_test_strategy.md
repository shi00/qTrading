# UI 测试分层策略

> 适用范围：`tests/unit/ui/` 下的 View 与 ViewModel 测试。
> 对应 CLAUDE.md §6.8 MVVM 表现层、§7 测试规范。

## 1. 分层原则

| 层 | 测试目标 | 允许的断言 | 禁止的断言 |
|----|---------|-----------|-----------|
| **ViewModel** | 业务状态流转（输入 → 状态变更 → 输出） | 状态字段、回调调用、DataFrame 内容、加载标记 | — |
| **View** | 冒烟：渲染不崩溃、关键控件存在 | 控件属性、回调被调用、`page` 交互 | 内部缓存结构、控件树深度、私有字段布局 |

- **ViewModel 测试**覆盖业务逻辑：构造输入 → 调用方法 → 断言状态字段/回调/输出。
- **View 测试**只做冒烟：实例化不抛异常、关键控件（`btn_prev`/`scroll_area` 等）非 None、事件回调转发正确。View 不持有业务状态，无需断言业务流转。

## 2. 反模式

以下写法耦合内部实现细节，重构 View 时会大量误报，应避免：

```python
# ❌ 反模式 1：断言缓存内部结构
assert len(layout._view_cache) == 3
assert 0 in layout._view_cache

# ❌ 反模式 2：直接操纵缓存来构造测试前置
layout._view_cache[1] = mock_view  # 绕过公共 API，耦合缓存机制

# ❌ 反模式 3：断言控件树深度/类型层级
assert len(view.scroll_area.controls) == 5
assert isinstance(view.scroll_area.controls[0].content, ft.Container)
```

## 3. 重构指南

将过度断言的 View 测试降级为行为断言的步骤：

1. **识别内部细节**：找出测试中对 `_view_cache`、`_controls`、私有字段结构 的读写。
2. **改用公共 API**：用 `_get_view(index)`、`_refresh_ui(tasks)` 等公共方法触发状态变更，替代直接写缓存。
3. **断言可观察行为**：断言公共属性（`current_tab_index`、`pagination_row.visible`）、回调调用（`mock_view.update_theme.assert_called_once()`）、渲染输出（`scroll_area.controls` 非空），而非缓存结构。
4. **保留边缘用例**：测试"控件缺少某方法时降级"等防御逻辑时，可保留对缓存的直接注入（这是唯一合理的直接操纵场景）。

## 4. 示例

### ✅ 好的 View 测试（行为断言）

```python
def test_get_view_returns_object_and_caches(self, mock_page, index):
    """_get_view 返回非 None 对象，二次调用返回同一对象。"""
    layout = self._make_layout(mock_page)
    view1 = layout._get_view(index)
    view2 = layout._get_view(index)
    assert view1 is not None
    assert view1 is view2  # 缓存生效：同一索引返回同一对象
```

通过公共 API `_get_view` 两次调用，断言返回对象同一性，间接验证缓存行为，不触及 `_view_cache` 内部结构。

```python
def test_update_theme_propagates_to_cached_views(self, mock_page):
    layout = self._make_layout(mock_page)
    mock_view = layout._get_view(0)  # 通过公共 API 创建并缓存视图
    layout.update_theme()
    mock_view.update_theme.assert_called_once()
```

通过 `_get_view` 触发缓存写入，再断言 `update_theme` 被传播，不直接操纵 `_view_cache`。

### ❌ 坏的 View 测试（实现细节断言）

```python
def test_update_theme_propagates_to_cached_views(self, mock_page):
    layout = self._make_layout(mock_page)
    mock_view = MagicMock()
    layout._view_cache[0] = mock_view  # 直接操纵内部缓存
    layout.update_theme()
    mock_view.update_theme.assert_called_once()
```

直接写 `_view_cache` 绕过 `_get_view` 的创建/缓存逻辑，若缓存机制重构（如改为 LRU、懒加载策略变更），测试会误报。

```python
def test_run_strategy_from_home_switches_tab(self, mock_page):
    layout = self._make_layout(mock_page)
    with patch("ui.app_layout.ScreenerView", FakeSV):
        layout._view_cache[1] = FakeSV()  # 直接注入缓存
        await layout.run_strategy_from_home("test_strategy")
        assert layout._current_tab_index == 1
```

`run_strategy_from_home` 内部已调用 `_get_view` 创建视图，直接注入缓存是冗余且耦合实现细节。
