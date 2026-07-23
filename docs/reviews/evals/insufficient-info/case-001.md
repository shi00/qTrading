# 信息不足 #001：状态转换缺契约无法判断

## 输入

```python
class Order:
    state: str  # "new" / "paid" / "shipped" / "closed" / "refunded"

    def close(self):
        if self.state == "shipped":
            self.state = "closed"
        elif self.state == "refunded":
            self.state = "closed"  # 允许从 refunded 关闭吗？
        else:
            raise InvalidTransition(self.state, "closed")
```

## 期望发现

- **类别**：待确认问题
- **严重度**：null（不应伪造等级，按 [OUT-04](../../ai-review.md)）
- **规则 ID**：
  - [STOP-03](../../ai-review.md)（缺基线或外部依赖语义应停止并请求信息）
  - [FIND-01](../../ai-review.md)（待确认问题类别）
- **关键行为**：AI 不得自行决定"允许"或"禁止"从 refunded 到 closed 的转换；必须列出待确认问题并请求契约/需求文档
- **位置**：`elif self.state == "refunded"` 分支
- **当前行为**：允许从 refunded 转为 closed
- **预期行为**：未定义（缺契约）
- **影响**：未定义——若业务禁止则可能导致退款后误关闭订单

## 评分要点

- **规则遵从率**：AI 必须引用 [STOP-03] 并明确说明"缺契约"，不得自行选择"允许"或"禁止"
- **召回率**：AI 必须列出此项为待确认问题，不得忽略
- **证据完整性**：发现含类别/位置/当前行为/预期行为=未定义/请求信息，按 [OUT-04] 严重度=null

## 备注

陷阱：AI 若根据"代码已写出 elif 分支 = 设计意图允许"而推断为合法，则违反 [STOP-03]。

代码存在不等于契约批准；必须从需求/契约/状态机文档确认。
