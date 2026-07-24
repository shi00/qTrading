# 场景遗漏 #001：消息处理缺幂等保护

## 输入

```python
async def handle_order_created(msg: OrderCreated):
    # 处理订单创建消息，写库
    order = Order(id=msg.order_id, status="created")
    await db.save(order)
    await publish_event(OrderConfirmed(order.id))
```

## 期望发现

- **类别**：场景遗漏（消息重投导致重复处理）
- **严重度**：P1
- **规则 ID**：
  - [ROUND1-03](../../ai-review.md)（建立场景基线：注入失败与重复）
  - [FIND-01](../../ai-review.md)（场景遗漏类别）
  - 应加载 [messaging-data-pipeline.md](../../review-profiles/messaging-data-pipeline.md)
- **遗漏场景**：消息系统至少一次投递下，相同 `msg.order_id` 的消息被重投，导致重复创建 Order + 重复发布 OrderConfirmed
- **预期行为**：基于 `msg.order_id` 做幂等键校验，已处理则跳过
- **影响**：数据重复、下游事件重复触发
- **证据**：消息系统（如 Kafka/RabbitMQ）默认 at-least-once 投递语义

## 评分要点

- **召回率**：AI 必须主动指出"重投场景未保护"，不得只检查代码逻辑正确性
- **证据完整性**：必须说明 at-least-once 投递语义 + 缺少幂等键的具体位置
- **规则遵从率**：必须引用 [ROUND1-03] 和 [FIND-01]，必须主动加载 messaging-data-pipeline.md Profile

## 备注

陷阱：AI 若只看代码"实现正确"而漏掉"消息系统会重投"的场景，则召回失败。
