# 策略参数场景化分组架构方案 (Strategy Parameter Grouping Architecture)

> **文档版本**: v1.2 | **最后更新**: 2026-03-21
>
> **实施状态**: ✅ 已完成

## 背景与问题陈述
我们要将参数展示从"平铺列表"升级为"逻辑卡片分组"。不仅要解决"超跌反弹"策略多维度参数的认知负荷问题，还要确保这是一个能吃下系统内所有异构策略的统一组件。**UI 分组的逻辑绝对不能在视图层写死**，必须由底层结构自适应匹配。

## 方案选型分析：参数协议扩充 (平滑演进)
保持 `BaseStrategy.get_parameters()` 返回 `List[Dict]` 的结构不变，为每个参数注入可选的 `group` 元数据字段。让协议自己开口说话，而不是让前端去猜。

---

## 核心架构设计与工程约束 (四大铁律)

### 1. 全局优先级排序表 (Global Ordering Registry)
在 `ui/constants.py` 或 [screener_view_model.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/ui/viewmodels/screener_view_model.py) 中维护一份**仅用于排序和退化UI垫底支持**的常量表。它决定了最终渲染时卡片的上下顺序，确保任何策略的参数呈现逻辑一致。

```python
PARAM_GROUP_ORDER = [
    "core_signal",    # 排在最顶：触发买点的核心指标
    "volume_confirm", # 其次：用于确认买量强度的过滤
    "fundamental",    # 再次：财报/基本面积淀
    "risk_control",   # 底部红线：回撤、止损等强制纪律
    "default",        # 未声明的孤儿零散参数
    "advanced",       # 总是垫底，默认折叠：大模型配置、Prompt调整等
]

# 内置兜底的翻译映射 (用于那些没有给 group_label_key 的策略)
DEFAULT_GROUP_LABELS = {
    "core_signal": "🎯 核心触发信号",
    "volume_confirm": "📊 量价资金确认",
    "fundamental": "🏢 基本面滤网",
    "risk_control": "🛑 严格风控红线",
    "default": "🎛️ 基础设置",
    "advanced": "⚙️ 高级调优"
}
```

### 2. 策略协议层自治声明 (Component Self-Governance)
以任何策略 (如 OversoldStrategy) 为例，在定义参数时声明所属分组。
**【高阶增强】允许自定义翻译键**：如果策略的业务逻辑非常特殊，不属于核心的内置组名，可以直接使用生造的 `group` 配合 `group_label_key`。

```python
def get_parameters(self):
    return [
        {
            "name": "rsi_threshold",
            "type": "slider",
            "group": "core_signal", # 使用全局内置的组
            ...
        },
        {
            "name": "special_event_window",
            "type": "slider",
            # 自创的未知组，不在全局已知列表内
            "group": "event_timing", 
            # 策略自己提供翻译词条映射，不再麻烦全局
            "group_label_key": "param_group_event_timing" 
        }
    ]
```

### 3. UI 渲染引擎自适应聚合算法 (Adaptive Aggregation)
在 [screener_view.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/ui/views/screener_view.py) 的重构中，[_render_strategy_params()](file:///D:/workspace/Quantitative%20Trading/astock_screener/ui/views/screener_view.py#L698-L942) 履行以下严谨的渲染生命周期：

1. **分拣与映射**
   遍历参数流，按 `group` 字段分发至不同的数组筐中。如果某个参数没有声明 `group`，则分配至 `"default"` 筐。
   同时记录下策略带出来的特殊 `group_label_key`。
2. **剔除空箱**
   如果该策略根本没有涉及 `volume_confirm` 等维度，其对应的筐会保持为空。渲染引擎在组装 UI 树时将**完全跳过该筐，绝不渲染空心空壳卡片**。
3. **依序着床**
   按照全局 `PARAM_GROUP_ORDER` 的顺序，依次渲染那些不为空的筐。
   - 如果遇到全局表中**没有记录**的生僻字组（如上文的 `event_timing`），则将这些未知组件排在 `default` 和 `advanced` 之间。
4. **标题本地化求解法则**
   渲染某一组的标题时，引擎优先读取参数里的 `group_label_key` -> 没有则寻找常量表 `DEFAULT_GROUP_LABELS` -> 依然没有则直接展示 `group` 原始英文字符串（作为最后防线容错）。

---

## 预期交互效果
优化后，界面的参数不再如同一滩散沙的瀑布流。用户（即便是量化新手）也会直观地看到：
* ✅ 顶部是一块名为【🎯 核心触发信号】的暗色护耳小卡片，包含 RSI 滑块。
* ✅ 中部下方是独立成块的【📊 量价资金确认】，包含量比滑块。
* ✅ 底端折叠起高深的深色区域【⚙️ 高级调优】。
这就是金融终端该有的专业感与层次感。

---

## 当前代码状态分析

### 现有架构评估

| 文件 | 当前状态 | 说明 |
|------|----------|------|
| [base_strategy.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/strategies/base_strategy.py) | ✅ 已支持任意字段扩展 | 协议层已就绪 |
| [oversold_strategy.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/strategies/oversold_strategy.py) | ✅ 参数已标注 `group` | 3 个参数已分组 |
| [screener_view.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/ui/views/screener_view.py) | ✅ 多级分组渲染已实现 | 支持自适应聚合 |
| [theme.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/ui/theme.py) | ✅ 全局常量表已部署 | PARAM_GROUP_ORDER, DEFAULT_GROUP_LABELS |
| [i18n.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/ui/i18n.py) | ✅ 分组标题翻译已添加 | param_group_* 条目 |

### 参数定义 (oversold_strategy.py)

```python
# 已完成分组标注
def get_parameters(self):
    return [
        {"name": "rsi_period", "group": "core_signal", ...},
        {"name": "rsi_threshold", "group": "core_signal", ...},
        {"name": "vol_ratio_threshold", "group": "volume_confirm", ...}
    ]
```

### 渲染逻辑 (screener_view.py)

```python
# 已实现多级分组渲染
def _render_strategy_params(self):
    # 1. 分拣参数到各组
    groups = {g: [] for g in PARAM_GROUP_ORDER}
    # 2. 按顺序渲染非空组
    # 3. 创建分组卡片
    group_card = ft.Container(
        content=ft.Column([
            ft.Text(title, ...),
            ft.Divider(...),
            ft.Column(controls, ...),
        ]),
        ...
    )
```

---

## 实施进度总览

| 阶段 | 任务 | 状态 | 涉及文件 |
|------|------|------|----------|
| **Phase 1** | 部署全局常量表 | ✅ 已完成 | `ui/theme.py` |
| **Phase 2** | 添加分组标题翻译 | ✅ 已完成 | `ui/i18n.py` |
| **Phase 3** | 策略参数标注 group | ✅ 已完成 | `strategies/oversold_strategy.py` |
| **Phase 4** | UI 渲染引擎重构 | ✅ 已完成 | `ui/views/screener_view.py` |

---

## 详细实施步骤

### Phase 1: 基础设施层 (Model层)

**目标**：部署全局排序表和默认翻译映射

**位置**：`ui/constants.py` (新建) 或 `ui/viewmodels/screener_view_model.py`

```python
# ui/constants.py
PARAM_GROUP_ORDER = [
    "core_signal",
    "volume_confirm",
    "fundamental",
    "risk_control",
    "default",
    "advanced",
]

DEFAULT_GROUP_LABELS = {
    "core_signal": "🎯 核心触发信号",
    "volume_confirm": "📊 量价资金确认",
    "fundamental": "🏢 基本面滤网",
    "risk_control": "🛑 严格风控红线",
    "default": "🎛️ 基础设置",
    "advanced": "⚙️ 高级调优",
}
```

### Phase 2: 国际化支持

**目标**：添加分组标题的 I18n 翻译键

**位置**：`ui/i18n.py`

```python
# 中文 (zh_CN)
"param_group_core_signal": "🎯 核心触发信号",
"param_group_volume_confirm": "📊 量价资金确认",
"param_group_fundamental": "🏢 基本面滤网",
"param_group_risk_control": "🛑 严格风控红线",
"param_group_default": "🎛️ 基础设置",
"param_group_advanced": "⚙️ 高级调优",

# 英文 (en_US)
"param_group_core_signal": "🎯 Core Signal",
"param_group_volume_confirm": "📊 Volume Confirmation",
"param_group_fundamental": "🏢 Fundamental Filter",
"param_group_risk_control": "🛑 Risk Control",
"param_group_default": "🎛️ Basic Settings",
"param_group_advanced": "⚙️ Advanced Tuning",
```

### Phase 3: 策略层标注

**目标**：为 oversold_strategy.py 的参数添加 group 字段

```python
# strategies/oversold_strategy.py
def get_parameters(self):
    return [
        {
            "name": "rsi_period",
            "label_key": "param_rsi_period",
            "type": "slider",
            "group": "core_signal",  # 新增
            "min": 2,
            "max": 30,
            "default": 14,
            "step": 1,
        },
        {
            "name": "rsi_threshold",
            "label_key": "param_rsi_threshold_oversold",
            "type": "slider",
            "group": "core_signal",  # 新增
            "min": 0,
            "max": 100,
            "default": 30,
            "step": 1,
        },
        {
            "name": "vol_ratio_threshold",
            "label_key": "param_vol_ratio_threshold",
            "type": "slider",
            "group": "volume_confirm",  # 新增
            "min": 0.8,
            "max": 3.0,
            "default": 1.5,
            "step": 0.1,
        },
    ]
```

### Phase 4: UI 渲染引擎重构

**目标**：重写 `_render_strategy_params()` 实现多级分组渲染

**核心算法**：

```python
def _render_strategy_params(self):
    from ui.constants import PARAM_GROUP_ORDER, DEFAULT_GROUP_LABELS
    
    params_def = self.vm.get_strategy_params(self.selected_strategy)
    if not params_def:
        return
    
    # 1. 分拣参数到各组
    groups = {g: [] for g in PARAM_GROUP_ORDER}
    custom_groups = {}  # {group_name: group_label_key}
    group_labels = {}   # 记录每组使用的标题
    
    for p in params_def:
        group = p.get("group", "default")
        if group not in groups:
            # 自定义组：排在 default 和 advanced 之间
            custom_groups[group] = p.get("group_label_key")
            groups[group] = []
        groups[group].append(p)
        
        # 记录标题来源
        if group not in group_labels:
            group_labels[group] = p.get("group_label_key")
    
    # 2. 按顺序渲染非空组
    self.params_container.controls.clear()
    
    for group_name in PARAM_GROUP_ORDER:
        if groups[group_name]:
            self._render_param_group(
                group_name, 
                groups[group_name],
                group_labels.get(group_name)
            )
    
    # 3. 渲染自定义组（排在 default 和 advanced 之间）
    for group_name in custom_groups:
        if groups[group_name]:
            self._render_param_group(
                group_name,
                groups[group_name],
                custom_groups[group_name]
            )
    
    self.params_container.update()

def _render_param_group(self, group_name: str, params: list, label_key: str = None):
    """渲染单个参数分组卡片"""
    from ui.constants import DEFAULT_GROUP_LABELS
    
    # 标题解析优先级：label_key > DEFAULT_GROUP_LABELS > group_name
    if label_key:
        title = I18n.get(label_key)
    elif group_name in DEFAULT_GROUP_LABELS:
        title = DEFAULT_GROUP_LABELS[group_name]
    else:
        title = group_name
    
    # 创建分组卡片
    group_card = ft.Container(
        content=ft.Column([
            ft.Text(title, size=14, weight=ft.FontWeight.W_500),
            ft.Divider(height=1, color=AppColors.DIVIDER),
            # 渲染参数控件...
        ]),
        padding=10,
        bgcolor=AppColors.CARD_BG,
        border_radius=8,
    )
    
    self.params_container.controls.append(group_card)
```

---

## 风险评估

| 风险项 | 等级 | 说明 | 缓解措施 |
|--------|------|------|----------|
| 旧策略无 group 字段 | 🟢 低 | 不影响功能 | 退化到 `default` 组 |
| UI 渲染性能 | 🟢 低 | 参数数量有限 | 无性能瓶颈 |
| 国际化遗漏 | 🟡 中 | 新增翻译键较多 | 同步更新 i18n.py |
| 自定义组排序 | 🟡 中 | 未知组位置不确定 | 明确排在 default 和 advanced 之间 |

---

## 架构优势总结

| 维度 | 评分 | 说明 |
|------|------|------|
| **扩展性** | ⭐⭐⭐⭐⭐ | 协议层平滑扩展，不破坏现有结构 |
| **解耦度** | ⭐⭐⭐⭐⭐ | 策略自治声明，UI 自适应聚合 |
| **可维护性** | ⭐⭐⭐⭐⭐ | 全局排序表集中管理，退化 UI 有兜底 |
| **国际化** | ⭐⭐⭐⭐⭐ | `group_label_key` 支持策略级自定义翻译 |
| **向后兼容** | ⭐⭐⭐⭐⭐ | 无 group 字段的参数自动归入 default 组 |
