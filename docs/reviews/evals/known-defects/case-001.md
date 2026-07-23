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
- **严重度**：P0（违反 [CLAUDE.md R4](../../../../CLAUDE.md) 红线，安全相关）
- **规则 ID**：
  - 项目红线 R4（asyncpg 必须用 `$1, $2, ...`）
  - [FIND-01](../../ai-review.md)（确定缺陷：违反安全规则）
  - [SEV-01](../../ai-review.md)（P0 灾难性）
- **位置**：函数 `get_user_by_name`，`%s` 占位符行
- **触发条件**：任意 name 输入
- **当前行为**：asyncpg 会抛出语法错误或语义错误；若改用 f-string 拼接则存在 SQL 注入
- **预期行为**：使用 `$1` 占位符 + 参数绑定
- **影响**：SQL 注入或运行时失败
- **证据**：asyncpg 官方文档明确只支持 `$1, $2, ...` 编号占位符
- **最小建议**：将 `%s` 改为 `$1`

## 评分要点

- **召回率**：AI 必须报告此项为确定缺陷，不得降级为"建议"
- **证据完整性**：必须引用 R4 并说明 `%s` 在 asyncpg 中无效
- **规则遵从率**：必须引用 R4 + FIND-01 + SEV-01，不得引用不存在的 ID

## 备注

陷阱：AI 若误以为 `%s` 是 Python DB-API 通用占位符则可能漏报。asyncpg 是 PostgreSQL 二进制协议，仅支持编号占位符。
