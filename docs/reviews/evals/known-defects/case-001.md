# 已知缺陷 #001：asyncpg 查询使用 %s 占位符

## 输入

```python
async def get_user_by_name(conn, name: str):
    # 直接拼接 %s 占位符——asyncpg 不支持
    rows = await conn.fetch(f"SELECT * FROM users WHERE name = %s", name)
    return rows
```

## 期望发现

- **类别**：确定缺陷
- **严重度**：P2（违反 [CLAUDE.md R4](../../../../CLAUDE.md) 红线（asyncpg 占位符契约），但给定场景的实际影响是**查询失败**（运行时报错）——对齐 [review-policy.yaml](../../review-policy.yaml) P2「重要：特定场景功能错误」，非注入、非 P0）
- **规则 ID**（仅取值 ai-review.md 已定义 ID）：`ruleId: "FIND-01"`
  - [FIND-01](../../ai-review.md)（确定缺陷：违反契约/不变量）
  - [SEV-01](../../ai-review.md)（P2 重要）
- **项目红线（projectRedlines）**：`["R4"]`（asyncpg 必须用 `$1, $2, ...`，见 [CLAUDE.md R4](../../../../CLAUDE.md)，不计入规则 ID 匹配）
- **位置**：函数 `get_user_by_name`，`%s` 占位符行
- **触发条件**：任意 name 输入
- **当前行为**：asyncpg 不识别 `%s`，执行时抛出语法/语义错误，该查询必然失败。输入代码为 **0 插值常量 f-string** 且 `name` 经参数绑定传递，SQL 文本不含用户输入插值，**无注入路径**（「若改用 f-string 拼接用户输入则存在注入」是假想的另一类变体，不是本例事实）
- **预期行为**：使用 `$1` 占位符 + 参数绑定
- **影响**：运行时失败（查询报错）；无注入影响
- **证据**：asyncpg 官方文档明确只支持 `$1, $2, ...` 编号占位符
- **最小建议**：将 `%s` 改为 `$1`

## 评分要点

- **召回率**：AI 必须报告此项为确定缺陷，不得降级为"建议"
- **证据完整性**：必须引用项目红线 R4（projectRedlines）并说明 `%s` 在 asyncpg 中无效
- **规则遵从率**：规则 ID 必须引用 FIND-01 + SEV-01（严重度按 **P2** 定级——确定缺陷不等于 P0），不得引用不存在的 ID；项目红线 R4 计入 projectRedlines 标注，不计入规则 ID 匹配

## 备注

陷阱：AI 若误以为 `%s` 是 Python DB-API 通用占位符则可能漏报。asyncpg 是 PostgreSQL 二进制协议，仅支持编号占位符。

反向陷阱：把「违反 R4」自动等同 P0 /「存在注入」——本例给定场景为查询失败（0 插值常量 f-string + 参数绑定，无注入路径），「真拼接用户输入」的安全变体是假想场景，**不得计成既成事实**；定级按实际影响（P2），不按规则名联想。
