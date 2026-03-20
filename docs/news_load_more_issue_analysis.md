# 新闻加载更多按钮异常消失问题分析报告

## 一、问题描述

### 现象
1. 在市场概览界面查看实时市场快讯
2. 向下滚动到底后出现"加载更多"按钮
3. 点击按钮后成功加载了更多消息
4. 再次滚动到底后，"加载更多"按钮消失
5. 等待一段时间后，按钮又重新出现

### 环境
- 数据库 `market_news` 表记录数：34 条
- 分页大小：20 条

---

## 二、数据流分析

### 2.1 正常分页加载流程

```
用户点击"加载更多"
    ↓
HomeView._on_load_more_click()
    ↓
HomeViewModel.load_next_page()
    ↓
_fetch_news_batch(next_page) → processor.cache.get_market_news(limit=20, offset=N)
    ↓
返回 (new_batch, has_more)
    ↓
NewsFeed.append_news(new_batch, has_more)
```

### 2.2 分页状态计算逻辑

| 加载阶段 | offset | limit | 返回条数 | has_more 计算 | 结果 |
|----------|--------|-------|----------|---------------|------|
| 第一页 | 0 | 20 | 20 | 20 >= 20 | True |
| 第二页 | 20 | 20 | 14 | 14 >= 20 | False |

**正常情况**：第二页加载后，`has_more = False`，按钮应该消失，这是正确行为。

---

## 三、根因定位

### 3.1 问题触发链路

```
1. 用户点击"加载更多"
   └─ 加载 14 条 (offset=20)
   └─ has_more = False
   └─ 按钮消失 ✅ 正常

2. AI 后台处理完成（异步）
   └─ _processing_loop() 处理完一条新闻
   └─ _notify_listeners()
   └─ HomeViewModel._on_news_service_update()
   └─ refresh_news_if_visible()
   └─ vm.refresh_news()
      └─ news_page = 0  ← ❌ 重置分页状态
      └─ _fetch_news_page(0) → 加载 20 条
      └─ has_more = True (20 >= 20)
   └─ news_feed.set_news(20条, True)
   └─ 按钮重新出现 ❌ 异常
```

### 3.2 根因代码

**触发点 1: AI 处理完成后通知**

文件：[data/news_subscription.py:252](../data/news_subscription.py#L252)

```python
async def _processing_loop(self):
    ...
    # 3. Notify Listeners (Optional: to refresh UI with tags)
    # Since UI might already have the raw news, this update might be subtle.
    # If HomeView listens to DB changes or we just trigger a refresh:
    self._notify_listeners()  # ← 每处理完一条新闻都会触发
```

**触发点 2: 新新闻拉取后通知**

文件：[data/news_subscription.py:404](../data/news_subscription.py#L404)

```python
async def _fetch_and_notify(self):
    ...
    if new_items_found:
        # Notify UI of new content (Raw)
        self._notify_listeners()
```

**问题代码: 全量刷新重置分页**

文件：[ui/viewmodels/home_view_model.py:102-107](../ui/viewmodels/home_view_model.py#L102)

```python
async def refresh_news(self):
    """Full refresh of news (Page 0)."""
    self._load_generation += 1  # Invalidate pending loads
    self.news_page = 0  # ← ❌ 重置分页状态，丢失用户已加载的数据
    await self._fetch_news_page(0)
    return self.news_data, self.has_more_news
```

### 3.3 问题本质

| 场景 | 当前行为 | 预期行为 |
|------|----------|----------|
| AI 处理完成 | 全量刷新，重置分页 | 仅更新对应新闻项的标签 |
| 新新闻到达 | 全量刷新，重置分页 | 前置插入新新闻，保持分页状态 |
| 用户已加载34条 | 被覆盖为20条 | 保持34条 |

---

## 四、修复方案

### 方案 A: 区分通知类型（推荐）

**思路**：让 `_notify_listeners()` 传递通知类型，UI 根据类型决定刷新策略。

**修改文件**：

#### 1. news_subscription.py - 传递通知类型

```python
# 定义通知类型
class NewsUpdateType:
    NEW_ITEM = "new_item"      # 新新闻到达
    TAG_UPDATE = "tag_update"  # 标签更新
    INITIAL = "initial"        # 初始加载

def _notify_listeners(self, update_type=NewsUpdateType.NEW_ITEM, data=None):
    target = self._listeners
    if not target:
        return
    for listener in list(target):
        try:
            listener(update_type, data)  # 传递类型和数据
        except Exception as e:
            ...

# AI 处理完成后
self._notify_listeners(NewsUpdateType.TAG_UPDATE, {"ts_code": ..., "tags": tags})

# 新新闻到达
self._notify_listeners(NewsUpdateType.NEW_ITEM, new_item)
```

#### 2. home_view_model.py - 根据类型处理

```python
def _on_news_service_update(self, update_type=None, data=None):
    if self.on_news_update:
        self.on_news_update(update_type, data)

# HomeView 中
def refresh_news_if_visible(self, update_type=None, data=None):
    if update_type == "tag_update":
        # 仅更新标签，不重置分页
        self._update_news_tag(data)
    elif update_type == "new_item":
        # 前置插入新新闻
        self._prepend_new_news(data)
    else:
        # 全量刷新（初始加载或手动刷新）
        self._run_if_visible(self._refresh_news_data, "Refreshing news list")
```

---

### 方案 B: 简化方案 - 仅在真正需要时刷新

**思路**：AI 处理完成后不触发 UI 刷新，因为标签更新对用户来说不是关键信息。

**修改文件**：news_subscription.py

```python
async def _processing_loop(self):
    ...
    # 3. Notify Listeners (Optional: to refresh UI with tags)
    # 注释掉或删除此行，AI 处理完成后不再通知 UI
    # self._notify_listeners()
    
    # 或者：仅在配置要求时通知
    if ConfigHandler.get_config("refresh_on_tag_update", False):
        self._notify_listeners()
```

---

### 方案 C: 保持分页状态

**思路**：在 `refresh_news()` 中保持分页状态，而不是重置。

**修改文件**：home_view_model.py

```python
async def refresh_news(self, keep_page=False):
    """Full refresh of news (Page 0)."""
    self._load_generation += 1
    
    if not keep_page:
        self.news_page = 0
    
    await self._fetch_news_page(0)
    return self.news_data, self.has_more_news
```

---

## 五、推荐方案

**推荐方案 A**，原因：

| 方案 | 优点 | 缺点 |
|------|------|------|
| A | 精确控制，体验最佳 | 改动较大 |
| B | 改动最小，快速修复 | 标签更新不会实时显示 |
| C | 改动适中 | 可能导致数据不一致 |

---

## 六、相关代码文件索引

| 文件 | 说明 |
|------|------|
| [ui/views/home_view.py](../ui/views/home_view.py) | 首页视图，新闻展示 |
| [ui/viewmodels/home_view_model.py](../ui/viewmodels/home_view_model.py) | 首页视图模型，分页状态管理 |
| [ui/components/news_feed.py](../ui/components/news_feed.py) | 新闻列表组件 |
| [data/news_subscription.py](../data/news_subscription.py) | 新闻订阅服务，通知触发点 |
