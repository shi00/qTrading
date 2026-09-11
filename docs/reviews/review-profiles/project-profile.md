# AStockScreener 项目 Profile

> 加载方式：检视 AStockScreener 项目代码时必须读取。包含项目覆盖规则与 reviewProfile 结构。

## 项目规则优先级

项目特定规则优先于 [ai-review.md](../ai-review.md) 通用建议。冲突时以 [CLAUDE.md](../../../CLAUDE.md) §3 红线 / §4 架构边界为准。

## 红线映射（R1-R22）

检视时必须检查以下红线违反（完整定义见 [CLAUDE.md §3.1](../../../CLAUDE.md#31--绝对禁止) 与 [docs/governance/redlines.yml](../../governance/redlines.yml)）：

| 红线 | 检视要点 |
|------|---------|
| R1 架构越界 | core 导入其他层；data 导入 services/strategies/ui；services 导入 strategies/ui；strategies 导入 ui |
| R2 异常吞没 | 吞没 `asyncio.CancelledError`（必须 raise） |
| R3 模糊压制 | `# type: ignore` 不带 `[reason]` |
| R4 SQL 注入 | asyncpg 原生查询用 `%s` 而非 `$1, $2, ...` |
| R5 僵尸引擎操作 | 在 disposed 引擎上执行数据库操作 |
| R6 过时类型注解 | 使用 `Union[X, Y]` / `Optional[X]` 而非 `X \| Y` |
| R7 测试状态污染 | 单例未隔离 |
| R8 废弃 API | 批量写入未用 `_save_upsert()` |
| R9 敏感信息泄露 | 日志/异常直接打印明文 Token/API Key/密码 |
| R10 硬编码密钥 | 代码或测试中硬编码 API Key/DB 密码 |
| R11 跨循环复用同步原语 | 直接将 `asyncio.Event/Lock` 作为类/实例属性（AST 扫描构造点；缓存点与跨循环使用人工评审） |
| R12 未注册数据表 | 新增表只改 models.py 不更新 data_dictionary.py |
| R13 未注册 DAO | 新增 DAO 不在 CacheManager.__init__ 实例化 |
| R14 未注册策略 | 新增策略不使用 `@register_strategy("key")` |
| R15 未注册单例 | 新增单例不使用 `@register_singleton`、不实现 `_reset_singleton` |
| R16 UI 阻塞主循环 | Flet 事件处理器中同步执行 IO/CPU 密集任务（CPU 密集仅限 Polars 等释放 GIL 库调用，纯 Python 计算须向量化） |
| R17 保留字作字段 | SQL 保留字作表名或列名 |
| R18 未隔离开发 | 新特性/重构未启用 git worktree 隔离 |
| R19 未配套测试的业务逻辑变更 | 新增/修改业务逻辑未同步新增或更新单测（覆盖率门槛与最小验证子集见 CONTRIBUTING.md「测试规范」） |
| R20 单位未核对的量纲比较 | 策略/回测对已知金额、数量列（`north_money`/`net_amount`/`amount`/`total_mv`/`circ_mv`/`vol`）的裸数值比较无显式单位换算（须经 `threshold_in_data_unit()`） |
| R21 缺失值伪装 | 业务语义字段缺失被填充合法具体值（`score`/`ai_score` 填 `0` 分、`confidence` 填 `50%`、空表视为「无限制」）。已修复缺陷（DATA-03→`suspend_data_absent`）不得再判为伪装 |
| R22 水位线单调性 | checkpoint/高水位持久化写入非单调（`set_app_state` 非 `*_max` 写水位；须优先 `*_max`/GREATEST 保护 + 乱序写入单测） |

## reviewProfile 结构

项目可定义覆盖规则（收紧或豁免通用要求）：

```yaml
reviewProfile:
  scopeRules: []
  architectureRules: []
  requiredDimensions: []
  optionalDimensions: []
  excludedPaths:
    - pattern: "<glob>"
      reason: "<why exclusion is safe>"
      authority: "<approver or governing rule>"
      expiresAt: "<RFC 3339 timestamp or null>"
  generatedPaths:
    - pattern: "<glob>"
      source: "<generator and source inputs>"
      verification: "<reproducibility or artifact check>"
  requiredChecks: []
  severityMapping: {}
  blockingPolicy:
    policyId: "<stable id>"
    policyVersion: "<immutable version>"
  documentationRequirements: []
```

项目规则可以收紧通用要求，也可以显式关闭不适用项，但必须说明理由、批准依据和有效期。法律、安全和基本正确性维度不得被普通路径排除关闭。
