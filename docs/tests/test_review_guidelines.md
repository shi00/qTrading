# 测试断言评审准则

> 关联规范：`CLAUDE.md` §7（测试规范）、§1.3（极简设计）
> 适用范围：所有 PR 评审中的 mock 断言检查

---

## 1. 弱断言定义

以下断言**不算有效断言**，仅验证"调用发生过"，不验证调用参数或副作用：

```python
# ❌ 弱断言（不验证参数，不验证副作用）
assert mock.called
mock.foo.assert_called()
mock.foo.assert_called_once()
```

**问题**：即使被调用方传错参数、传空参数、甚至传 `None`，弱断言依然通过，无法捕捉回归。

---

## 2. 有效断言标准

有效断言必须满足以下**至少一项**，建议两项兼具：

### 2.1 参数验证（必须）

```python
# ✅ 验证调用参数
mock.foo.assert_called_once_with(arg1, arg2)
mock.foo.assert_called_with(arg1)
mock.foo.assert_any_call(arg1)
```

### 2.2 副作用验证（建议附加）

验证调用产生的状态变更或输出变化：

```python
# ✅ 验证状态变更
view._handle_cancel("task-123")
mock_tm.cancel_task.assert_called_once_with("task-123")
assert view._current_page == 1  # 副作用：页码重置

# ✅ 验证其他 mock 被联动调用
view.did_mount()
mock_tm.subscribe.assert_called_once_with(view._on_tasks_updated)
assert view._mounted is True  # 副作用：挂载状态变更
```

---

## 3. PR 评审准则

| 场景 | 处理方式 |
|------|---------|
| 纯 `assert mock.called` / `assert_called_once()` 无副作用断言 | **要求升级**：补参数验证或副作用断言 |
| `assert_called_once_with(...` 已验证参数 | 通过 |
| `assert_not_called()` | 通过（验证"未调用"本身就是行为断言） |
| 被调用方法本身无参数（如 `cancel()`、`update()`） | 至少补一条副作用断言 |

---

## 4. 弱断言 vs 强断言对照

### 示例 1：订阅回调注册

```python
# ❌ 弱断言：不知道订阅了哪个回调
view.did_mount()
mock_tm.subscribe.assert_called_once()

# ✅ 强断言：验证回调身份 + 挂载状态
view.did_mount()
mock_tm.subscribe.assert_called_once_with(view._on_tasks_updated)
assert view._mounted is True
```

### 示例 2：任务调度

```python
# ❌ 弱断言：不知道调度了什么协程、传了什么数据
view._on_tasks_updated([])
mock_page.run_task.assert_called_once()

# ✅ 强断言：验证协程对象 + 任务数据
view._on_tasks_updated([])
mock_page.run_task.assert_called_once_with(view._safe_refresh, [])
```

### 示例 3：无参数方法的副作用

```python
# ❌ 弱断言：cancel() 无参数，仅验证调用发生
layout.change_tab(2)
mock_task.cancel.assert_called_once()

# ✅ 强断言：cancel() 无参数，补副作用断言
layout.change_tab(2)
mock_task.cancel.assert_called_once()
assert layout._pending_tab_index == 2  # 副作用：待切换索引已设置
```

---

## 5. 例外说明

以下场景 `assert_called_once()` 可接受，无需强制升级：

- 被调用方法无参数，且已通过其他副作用断言覆盖行为
- 测试目的仅为验证"接线"（wiring）而非业务逻辑，且接线正确性由类型系统保证
