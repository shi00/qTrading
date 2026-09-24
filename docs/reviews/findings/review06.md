# review06 — 安全、外部集成与供应链（结论登记）

> **来源报告**：`reviews/06-安全与供应链.md`（本地 gitignored 正文，检视日期 2026-08-24）
> **登记规则**：见 [README.md](./README.md)（GOV-04）。发现 `id` 沿用报告内编号（F9/F10/F12/F13/F16/F19），不带轮次前缀（避免与已登记治理 ID 混淆）。
> **登记范围**：仅登记本轮的 6 条**未关闭**安全相关发现；报告内已修复项（F1~F8、F11、F14、F15、F17、F20）与本地数据项 F18（sidecar 路径防误配置，非跨信任边界）不在本篇登记范围内。
> **severity 口径**：复述报告原值（`MAJOR` / `MINOR`）；报告未逐条映射项目 P 级，此处不臆造（报告 §8 Top3 另给 F9→P1、F19→P2 的优先级）。
> **ruleId 口径**：报告未对这些发现显式标注项目规则 ID，统一记 `-`（该轮相关红线为 R9/R10，但 F9~F19 均未直接对应）。

## 发现登记（6 项）

| id | ruleId | severity | location | summary | disposition | 可验证判据 |
|----|--------|----------|----------|---------|-------------|-----------|
| F9 | - | MAJOR | `data/external/tushare_client.py`；`data/external/akshare_concept_client.py` | 外部数据入库前缺 schema 校验（列名 / 类型 / 取值范围），上游字段漂移或异常值（负价格、未来日期、NaN）可静默入库；脏数据比崩溃更危险 | open | 外部数据入库路径已挂**行级 schema 校验**（非法行拒绝或隔离并 WARNING），无未校验直写路径 |
| F10 | - | MINOR | `data/external/tushare_client.py::_handle_api_call` | 重试机制（`max_retries` 默认 3 + 线性退避 + 速率限制 5–15s 随机 sleep）在外部服务故障时被并发任务放大请求压力，缺熔断器 | open | 外部服务调用链已加熔断/退避限流（连续 N 次失败后短路一段时间） |
| F12 | - | MAJOR | `utils/prompt_guard.py::validate_prompt` / `neutralize_external_text` | prompt 注入防御为正则黑名单，对新闻/公告正文植入指令固有局限；需结构化边界 + 输出侧交叉校验 | open | 已引入结构化边界（外部内容包裹为不可执行区域）+ 输出侧交叉校验 + 注入尝试可观测性，不再单靠正则黑名单 |
| F13 | - | MAJOR | `strategies/ai_mixin.py`；`services/ai_service.py::validate_ai_analysis_response` | LLM 输出影响选股排序，仅白名单校验 `score` / `recommendation` 两个字段，缺完整输出 schema 校验与 AI 免责声明 | open | LLM 输出已做完整 schema 校验（校验失败按「分析不可用」处理而非部分采信），且 UI 展示 AI 评分处标注 AI 免责声明 |
| F16 | - | MINOR | `ui/viewmodels/backup_restore_view_model.py::start_restore_wizard` | 备份恢复路径仅做存在性检查（`input_path.exists()`），未校验 pg_dump magic header，误选非法文件在恢复中途才失败 | open | 恢复前已校验 pg_dump 文件 magic header 并展示备份元信息（创建时间 / 大小 / DB 版本）后再执行 |
| F19 | - | MAJOR | `sidecars/qtrading-pg-sidecar/src/maint.rs`（pg_dump 流程） | 数据库备份文件未加密，默认路径 `<app data>/backups/qtrading-backup-*.dump` 含全量行情 + 选股策略参数 / 自选股 / AI 分析历史，易流向保护级别更低的共享 / 云同步目录 | open | UI 已有「备份未加密，请勿存放共享 / 云同步目录」提示，并提供可选加密备份能力（任一启用即视为关闭） |

## 关键证据片段（≤20 行 / 条）

### F9 外部数据入库前缺 schema 校验

报告取证——Akshare 客户端直返、无任何列名 / 类型 / 取值范围校验：

```python
# data/external/akshare_concept_client.py
return ak.stock_board_concept_name_em()
```

报告建议（轻量行级校验 + 隔离，而非完整 Pydantic 模型）：

```python
def validate_and_quarantine(df, schema):
    """返回 (合格行, 隔离行)。隔离行记录到单独表供人工排查。"""
```

要点：**隔离而非静默丢弃**（否则「上游改字段」表现为「数据慢慢变少」，极难定位）；隔离比例应影响 `@require_quality` 质量分；优先覆盖策略直接消费的核心表（行情 / 财务）。

### F10 重试放大请求压力缺熔断

```text
位置：data/external/tushare_client.py::_handle_api_call
现状：max_retries 默认 3（get_request_max_retries），网络错误线性退避，速率限制场景 5–15 秒随机 sleep。重试有界，不会无限循环。
风险：对已故障的上游，多个并发同步任务各自重试 3 次会显著放大请求量。
建议：连续 N 次失败后短路后续请求一段时间（熔断器），比调低 max_retries 更精准——正常时不影响重试，故障时快速止损；对非幂等操作（若有）显式禁用重试。
```

### F12 prompt 注入防御为正则黑名单

```text
现状：外部新闻/公告文本经 neutralize_external_text（去尖括号、PII 脱敏、长度截断）后入 prompt；
      用户自定义指令经 validate_prompt 正则黑名单检查越狱短语。
代码注释已自认局限：
  This function is a regex blacklist and only intercepts common jailbreak phrases.
  It is NOT a complete defense...
真实风险面：外部新闻/公告正文（不受控第三方内容，且直接影响选股评分）。
建议（防御深度，不追求完美）：结构化边界（外部内容包裹为不可执行区域）
      + 输出侧交叉校验（评分异常降权而非直接采信）+ 注入尝试可观测性；
      明确不建议继续扩展正则黑名单（与自然语言表达力对抗的必输游戏）。
```

### F13 LLM 输出缺完整 schema 校验

```python
# 已有输出校验（services/ai_service.py::validate_ai_analysis_response）仅覆盖两个字段：
if rec_lower not in VALID_RECOMMENDATIONS:
    response["recommendation"] = "neutral"
response[field] = _sanitize_free_text(val, max_len=free_text_max_len)
# 缺口：无完整输出 schema 校验——LLM 返回的 JSON 缺字段或类型不符时，
#      后续代码可能 KeyError 或类型错误（只校验了 score 与 recommendation）。
```

### F16 备份恢复仅存在性检查

```text
位置：ui/viewmodels/backup_restore_view_model.py::start_restore_wizard
现状：用户经 FilePicker 选择的路径仅检查 input_path.exists()，未规范化，直接传 sidecar restore --input。
      信任边界判定：文件由用户自选，不跨越信任边界；真实风险是误操作（选错非备份的 .dump 文件）。
建议：1) 恢复前校验 pg_dump magic header，非法文件在开始前拒绝；
      2) 显示备份元信息（创建时间 / 大小 / DB 版本）供用户确认后再执行。
```

### F19 备份文件未加密

```text
位置：sidecars/qtrading-pg-sidecar/src/maint.rs（pg_dump 流程）
现状：在线/离线备份均产出未加密的 .dump，默认 <app data>/backups/qtrading-backup-*.dump，
      内容含全量行情数据 + 用户选股策略参数 / 自选股列表 / AI 分析历史。
信任边界判定：跨越真实边界（备份常见去向是外接硬盘 / 云盘同步 / 邮件附件，保护级别通常更低）。
建议（按投入产出）：1) UI 提示「备份文件未加密，请勿存放共享或云同步目录」（零成本，覆盖大部分场景）；
      2) 可选加密备份（须警告「忘记 passphrase 则不可恢复」）；3) 备份目录 ACL 仅当前用户可访问。
```

> **来源**：报告 §8「最小验证子集」给出相关的验证命令（外部数据 schema 校验：`pytest tests/unit/test_tushare_client.py tests/unit/test_concept_sync.py -v`；prompt 边界：`pytest tests/unit/test_ai_mixin.py tests/unit/test_ai_service.py -v`）。