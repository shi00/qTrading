# Tasks：E-01 有界分片（每片独立可验证、达标即提交）

> 对应 `spec.md`。批次目标：E-01 DoD D1–D5。每片遵循：契约 → RED（契约/行为失败）→ 最小实现 → GREEN + 紧耦合回归 → 自查 → 原子提交。
> 分支：`feat/agent-runtime`｜worktree：`d:\workspace\qTrading\.worktrees\feat-agent-runtime`

- [x] 分片 E-01-1：core 来源读取与 D06 预算契约（提交 `85bd872aa`，26 用例）

- 需求：D1（限额常量/恰等超一）、D4（`xlsx_cell_meta` 全键）、D5（失败不伪已读的字段语义）
- 文件：`core/agent_types.py`、`tests/unit/test_agent_source_read_types.py`
- 内容：`SourceRef`/`XlsxCellMeta`/`FactValue`/`SourceRead`/`EvidenceLink`/`AttachmentKind`/`AttachmentBudget` + 严格 codec
- RED：先写 codec 全键/额外键/null/类型拒绝与 `AttachmentBudget` 恰等/超一用例，确认因符号缺失或行为不符失败
- GREEN 判据：新增用例全绿；`-k agent` 相关既有用例不回归；ruff/pyright 0 errors
- 提交：`feat(agent): E-01-1 来源读取与 D06 预算契约`

- [x] 分片 E-01-2：附件解析 worker（提交 `a03b1772d`，47 用例；PDF 依赖 pypdf 待授权）

- 需求：D1（已读未读/原值/单位/可得时点/真实退出）、D4（双表示/缓存逐字段）
- 文件：`services/agent_service/attachments.py`、`services/agent_service/attachment_worker.py`、`services/agent_service/__init__.py`、`tests/unit/test_agent_attachment_worker.py`
- 内容：内容嗅探、逐文件限额、范围选择、CSV 多行记录、JSON 精确数值、XLSX 同源双读与 `(sheet,cell)` 配对、子进程真实退出/超时/取消、PDF/图片 gated（`CAPABILITY_UNVERIFIED`）
- RED：合成夹具（有/无/空/错误/过期缓存、日期、`00123`、非公式、源版替换、双读失败、超一限额、worker 不退出的 runner）
- GREEN 判据：目标用例全绿；进程退出断言（`worker_exited=true` 且 PID 不可再查）；既有 agent 单测不回归
- 提交：`feat(agent): E-01-2 附件解析 worker 与 D06 限额`

- [x] 分片 E-01-3：业务来源注册 / PIT / 未选零使用（提交 `4e293ff4f`，单测 11 + 集成 7）

- 需求：D2、D1（可得时点）、AC02
- 文件：`services/agent_service/sources.py`、`data/persistence/daos/agent_dao.py`、`tests/unit/test_agent_sources.py`、`tests/integration/test_agent_dao_business_sources.py`
- 内容：`register_business_sources` 单事务 asset+link、幂等/冲突、PIT 拒绝、仅 selected 落库
- RED：先写集成用例（同事务可见性、幂等、异输入冲突、PIT 晚于 cutoff 0 行、未选 0 行/0 边）
- GREEN 判据：新增集成全绿（真 sidecar PG）；既有 `test_agent_dao_*` 不回归
- 提交：`feat(agent): E-01-3 业务来源注册与 PIT 时点门`

- [x] 分片 E-01-4：单位换算注入与数量兼容扩展（提交 `b226c6205`，36 用例含 northbound 回归）

- 需求：D3、R20
- 文件：`strategies/utils.py`、`services/agent_service/units.py`、`app/agent_wiring.py`、`tests/unit/test_agent_units.py`、`tests/unit/test_threshold_in_data_unit_quantity.py`
- 内容：数量分支（lot/share + 已批准证据匹配）、注入 gateway、未知拒绝、`unverified` 原因、旧金额回归
- RED：200 share→2 lot、2 lot→200 share、无证据 lot→拒绝、未知币种→拒绝、金额旧例逐字不变
- GREEN 判据：策略既有 `test_northbound_strategy` 单位用例全绿；import-linter 不报 services 上行
- 提交：`feat(agent): E-01-4 单位换算注入与数量兼容扩展`

- [x] 分片 E-01-5：回测严格读四分支适配（提交 `c4d8feec3`，8 用例）

- 需求：D5
- 文件：`services/agent_service/business_sources.py`、`tests/unit/test_agent_business_sources.py`
- 内容：四分支（存在/不存在/DB 失败/disposed/取消），失败零已读/已核验来源
- RED：fake DAO 注入 `EngineDisposedError` / `asyncio.CancelledError` / DB 异常 / 空结果
- GREEN 判据：四分支断言全绿；原策略/`BacktestDao` 旧用例不回归
- 提交：`feat(agent): E-01-5 回测严格读来源适配`

## 分片 E-01-6：门禁、计划与检查点

- [x] 独立检视阻断项 B1-B6 修复（提交 `6bd112190`）：CSV 多段累计行限额 / 跨表行段不误判重叠 / XLSX 超时部分读取契约合法原子 + 父进程 `decode_selected_range` 复核 / 取消只终止本请求子进程且不阻塞事件循环 / 回测 `executed_at` 与 `available_at_unknown`（R21）/ worker 校验 source_digest
- [x] 修复验证：E-01 单元 76 passed、真 PG 集成 7 passed、ruff/format/pyright(0 warnings)/pre-commit 15 hooks 全绿
- [x] 门禁执行：ruff check/format 全绿、pyright 0 errors、agent+单位 928 passed、集成 7 passed、i18n 11 passed；**全量 `pytest tests/unit` = 21182 passed / 1 failed / 45 skipped / 50 subtests passed（610.11s，`--timeout=900`）**
  - 唯一失败 `test_redline_checks.py::TestGBKEncodingCompatibility::test_no_unicode_error_under_gbk` 为**既存并发敏感测试**：`check_redlines.py` 单跑 26s、该测试 `-n 0` 单跑 26.89s 通过；全量 8 worker 满载时该脚本 >60s 触发硬编码阈值 → 环境竞争，非本次改动引入（脚本本身无变慢证据）
  - 误判记录：此前三次「99% 停滞」实为 `test_pollution_detection.py::test_no_cross_test_pollution_in_core_subset`（单跑 406.98s 元测试）未等足时长所致，非死锁
  - 等价分批旁证：子目录批 3053 passed、ui 批 5649 passed、redline 串行 257 passed；`LITELLM_LOCAL_MODEL_COST_MAP=True` 消除测试期远程价格表抓取超时
  - PDF 依赖待授权
- 文件：`Plans.md`（§A4 E-01 状态、§A7 证据）
- 内容：全量门禁命令、证据摘要、残余（PDF 等待 pypdf 授权；app service 组装留 F 系列）
- 提交：无（Plans.md 为 gitignored 本地进度源）

- [x] 里程碑独立检视复核 PASS（第二轮，`6bd112190`）：B1 CSV 多段行限额累计、B2 跨表行段不误判重叠、B3 XLSX 超时原子前缀块 + 父进程复核 `decode_selected_range`、B4 取消只终止本请求子进程且不阻塞事件循环、B5 `executed_at`/`available_at_unknown`（R21）、B6 worker 校验 `source_digest` 逐条闭环
  - 非阻断残余 N1-N5（父进程越界负向用例、worker/parent deadline 同值竞态、超时文案硬编码 30 秒、64KiB 边界 off-by-one、`_json_safe` 摘要碰撞）见 `docs/debt/known-technical-debt.md`
  - 交付前提：全量单测结果入账（本轮后台复跑中）；PDF 能力仍待 pypdf 授权

---

# Tasks：E-02 共享 MCP（静态可验证部分已完成，needs-spike 显式保留）

- 已完成分片（本地任务分支 `feat/agent-runtime`）：
  - [x] E-02-1 `97fbe5977` core 契约：服务身份/配置版本稳定 key、deadline 1—120 默认 30、八族预算未冻结即不准入、工具交集真空、只读审核绑定实现指纹（29 用例）
  - [x] E-02-2 `b2f264783` 连接管理：transport 合同、保存 0 连接、测试 0 研究工具、预算+审核准入、在途拒停、断线不重连（16 用例）
  - [x] E-02-3 `46b0ce6a6` 预算执行：八族累计恰等/超一、分块早停、解析前深度扫描、连续 progress、单 deadline 不续期（14 用例）
  - [x] E-02-4 `c4f6a60c8` 调用门：目录/白名单/schema 端口/claim 仲裁/unknown 不重试/取消传播（11 用例）
- [x] E-02-5 真实 FastMCP transport 实证 — 提交 `f06f2d523`
  - stdio 插入点（绝对命令 + 最小 OS 基线环境 + `log_file` FD 级 stderr）、HTTP/SSE 传输构造、逐 send 门（关闭/deadline 过期在 SDK 请求前拒绝）、四诊断通道、真实发现/调用（5 passed）
  - 完整 jsonschema 适配器：`JsonSchemaToolValidator` 接入真实 `jsonschema>=4.26.0`（正式依赖，锁文件已再生；10 passed）
  - 仍未冻结（不得宣称）：八族预算数值需维护者批准后回填 `MCPBudgetProfile`（当前测试用局部值，产品默认未定）；原始接收字节计量（类型化客户端在库内解析）、子进程退出码可观测性、HTTP/SSE 真实往返 → 已登记 `docs/debt/known-technical-debt.md`
- [x] E-02-6 里程碑独立检视 FAIL→修复→PASS（`719174f06` B1-B3、`f9d65e1b0` 编码逃逸/FAILED 分支用例）：B1 结果不可编码（二进制/自定义对象/孤立代理项/超深结构）→ `unknown` 且不重试；B2 `single_result_bytes` 改为按单条比较（不跨调用累计）；B3 门内强制单一 `asyncio.timeout`（挂起调用切断为 `unknown`）；残余 M1-M12 登记 `known-technical-debt.md`（P2-AgentMCP-Residual-Risks）
  - 验证：MCP 工具/预算单测 30 passed（含挂起/不可编码/孤立代理项/FAILED 分支/连续小结果）；ruff/pyright(0)/pre-commit 全绿

---

# Tasks：E-03 Skill 管理 / Windows 只读 backend / 角色版本

- [x] E-03-1 `43e869802` core 四阈值 + 角色分配契约（13 用例）
- [x] E-03-2 `2eada966d` Windows 受管只读工作区同句柄最终路径（10 用例）
- [x] E-03-3 `d56d49a9f` Skill 包预检/不可变版本发布/脚本 0 执行/失败保旧（13 用例）
- [x] E-03-4a `d182afbc2` SKILL.md 前台/根布局/许可/依赖/同名判定（38 用例；Harness v2.66.0 合同）
  - 根目录恰好一个 SKILL.md；缺失/仅嵌套/多候选拒绝并提示拆包
  - description 必填；布尔仅 true/false；Skill 名 NFKC/≤64/小写+单连字符
  - 未知/缺失许可拒绝（Apache-2.0/MIT/BSD-2-Clause/BSD-3-Clause/LGPL-3.0-only）
  - dependencies 合法且去重；行为字段只记录不赋权
  - 同名判定 NFKC+casefold；规范内容摘要顺序不敏感
  - 导入决策：新建/未变化/需确认更新/确认更新
- [x] E-03-4b `b10c2cc9f` Skill 资产落库与角色版本引用（契约 25 + 编排 12 + 真 PG 集成 11）
  - core `RoleSkillAssignment` 严格 wire/decode/digest（默认⊆启用、空集真空、版本不可变）
  - `SourceRelationKind.ROLE_SKILL_BINDING`；引用阻删由 FK RESTRICT 集成证明
  - DAO `register_skill_asset`（同内容幂等/异内容冲突/元数据不复制脚本）
  - DAO `save_role_skill_assignment` 单事务（角色资产 + 引用边；缺失/禁用/缺依赖拒绝）
  - 服务 `SkillCatalogService` 导入编排 + 登记失败补偿删除
  - 验证：Skill 相关回归 136 passed/1 skipped；ruff/format/pyright(0)/pre-commit 全绿
- [ ] E-03-4c 管理入口：随 F-05 UI/VM（角色分配保存/取消/重开准确查回）
- [x] E-03-5 里程碑独立检视 PASS（`5050931c9`）：五轮复核关闭 B1-B7（对象计数含隐含/空目录、规范化与 Windows 等价路径、10 秒预算覆盖解析与暂存、目录导入、保留名、设备别名）；仍存 7 项非阻断风险见 Plans §A7。
  - 修复提交：`60a5bf627`、`32bf982a5`、`f7c082d31`、`5050931c9`
  - 证据：Skill 单测 104 passed/1 skipped、真 PG 集成 13 passed、agent 单元 1280 passed/2 skipped、ruff/format/pyright(0)/pre-commit 全绿

# Tasks：全量门禁存量失败修复（已完成）

- [x] `69629d9e4` R2 三处/R11 两处允许理由、LeafRuntimeRegistry R7 复位、派生翻译显式登记、SQ-02/SQ-04 治理 ID
- [x] `d6cec647c` PDF 魔数拆分避免治理 ID 误采集

---

# Tasks：E-04 Harness 计划/摘要/窗口（依赖已解锁，待批准执行）

> 契约正本：`spec.md` 计划 C。DoD（DD §13.2 E-04）：TextContent 首次/恢复映射；summary 一账；早纠正/工具配对/禁用谱系保；摘要出口源禁用 vs claim 各 100 轮按 C-04 单胜者；空摘要明确阻。
> 环境：venv 已装 `pydantic-ai-harness[skills]==0.66.0`（`f1dd72e74`）；真 PG 集成须 `$env:SIDECAR_BINARY_PATH` + `-n 0`。

- [x] E-04-1 core 计划提案 wire 类型（`PLAN_STORE_VERSION`/`PlanItemSnapshot`/`PlanProposal` + 严格 decode/normalize/digest/`plan_asset_id`）— 提交 `9effad46d`，36 用例
- [x] E-04-2 DAO 计划提案读写（绑 execution + plan_revision，CAS，不可变版本）— 提交 `75b3a8a6b`，真 PG 集成 14 用例
- [x] E-04-3 PlanStore 六方法适配器 — 提交 `e77b212bd`，20 用例
- [x] E-04-4 Planning/摘要装配与 ResearchBoundary — 提交 `710c57e6c`，30 用例 + 模型桥回归 52 passed
- [x] E-04-5 里程碑独立检视 **PASS_WITH_NITS（阻断 0）**（基线 `f1dd72e74`，检视对象 `9effad46d`…`710c57e6c`）
  - 结论：C2/C3/C4 逐条一致（不可变只 INSERT、执行行锁+CAS、同内容不增版、坏行 fail-closed、不回写 `agent_round`、`unverified`）；R2/R4/R5/R21 无违规
  - 非阻断闭环 `6efba72f6`：N1 摘要 `model=None` 对齐正本继承语义；N2 head 版本解析去 CAST 并 fail-closed；N3 读入要求 `read_state=ready`；另修 E-01 PDF 用例的 venv 依赖敏感性
  - 接受未改：N4（TextContent 多段以 `\n` 连接、空序列产出空 user 消息；已有映射用例覆盖；`agent_round` 零更新由「只 INSERT 计划资产」与旧版可读用例间接证明）
  - 证据：计划提案真 PG 集成 16 passed；能力/worker/映射 67 passed；`tests/unit -k agent` 1529 passed/3 skipped；ruff/format/pyright 0；pre-commit 15 hooks 全绿
- 明确残留（不得宣称已完成）：`agent_operation.call_kind='summary'` 落库随运行装配；root workflow launch 归 F-01-6；真实窗口/tokenizer 归 G-01

# Tasks：E-06 联合清理

- [x] E-06-1 `e0e8c651a` 准入/批次/fence/tombstone 契约（17 用例）
- [x] E-06-2a `f0246e35b` 确认绑定原预览/修订与 fence 单次提交（12 用例）
- [x] E-06-2b `c2b83800c` DAO 静态合同：九类常量 SQL + 空/超限前置拒绝（23 用例）
- [x] E-06-2c `e9064bc72` 九类原生目标真实删除 + NOT NULL tombstone（真 sidecar PG）
  - 契约纠正：系统表删除改走 DBOS 公开 `Client.delete_workflows`（§10.7），删除 E-06-2b 的 `dbos.*` 私有 DML；Datasource 仍固定参数化 DML
  - 新增 `data/persistence/agent_runtime_store.py`（运行库独立引擎：九域计数/主键域预检/datasource 删除，集合按单参数 `text[]` 绑定 `ANY(:ids)`）
  - 新增 `services/agent_service/cleanup_native.py`（公开系统删除 → Datasource DML → 逐域查回；经受管线程 R16；空集合零副作用）
  - `AgentDao.tombstone_cleanup_text`：session.title / asset display_name+digest 固定 tombstone，草稿/来源/元数据/正文引用清除
  - 验证：清理三单测 52 passed；真 sidecar 集成 3 passed（预检九域/删除全 0/集合外不变/重试幂等/tombstone 集合外不变）；ruff+pyright 全绿；pre-commit 全通过
- [x] E-06-3a `46b62e07d` 作业持久化 + fence 单一提交（core codec + AgentDao 四方法 + DaoCleanupJobStore；真 PG 6 用例）
- [x] E-06-3b `fe48bff1c` 清理编排 CleanupExecutionService（8 单测）
- [x] E-06-4 `57355b8f6` 端到端集成（真 job+真九类删除+tombstone→verified，4 用例；两组集成合并 10 passed）
- [x] **E-06-5a 正文域清理（已批准范围，无需 Q6）**（§10.7 字段级最小留存表第 1—3 行：删除正文及受管副本）— 提交 `efb882890`
  - 范围：`agent_message.body_text/body_ref/payload/search_text`、`agent_artifact.summary/body_text/body_ref/result_payload/result_digest`、`agent_round.frozen_manifest/actual_input/model_binding` 中的正文、`agent_operation.result_payload/request_ref/result_ref`、`agent_body_payload` 准确行删除（行/正文受管副本，非 identity/fence/计量）
  - 约束：NOT NULL 列用固定 tombstone；不清 fence/身份/phase/计量；集合外不变；仍受 200 上限与 fence 准入
  - 反例标准：同一私人原句填入正文/标题/名称/摘要/metadata/回执，清理后各域均查不到该句（§10.7 末段）
  - 当前落点缺口：`AgentDao.tombstone_cleanup_text` 只覆盖 `agent_session`/`agent_asset_version`；需扩至上述域
  - **实现要点（只读勘验结论，签名不变）**：调用方 `CleanupExecutionService` 以 `(user_scope, *, session_ids, asset_keys)` 调用，扩展全部在 DAO 方法体内，端口 Protocol 无需改
    - `agent_message`：`body_text/body_ref/payload/search_text` 均可 NULL → 置 NULL（`body_exclusive` CHECK 仍满足）
    - `agent_artifact`：`summary/body_text/body_ref/result_payload` 置 NULL；`result_digest`（NOT NULL）**未改**，换固定 tombstone 属 E-06-5b
    - `agent_round`：`frozen_manifest/actual_input/model_binding/sdk_history_ref` 置 NULL（四列均可 NULL）；`frozen_digest/actual_digest` **未改**，属 E-06-5b
    - `agent_operation`：`result_payload/request_ref/result_ref` 置 NULL；`parameter_digest` **未改**，属 E-06-5b
    - `agent_body_payload`：按受清 owner（message/artifact/asset_version）收集 `body_ref` 后**准确行 DELETE**；须同一事务内先清 `agent_message.body_ref`/`agent_artifact.body_ref`（FK DEFERRED）再删行
    - 会话域经 `session_id`，round 经 `execution_id ∈ (SELECT execution_id FROM agent_execution WHERE session_id IN ...)`；资产域经 `(asset_id, asset_version)`
    - 集合外不变；`touched` 计数扩展为逐域 key
- [x] **E-06-5b 结构回执保留键白名单**（已按 §10.7 表 2—4 行冻结并实现：`dd32d2c44`；白名单 = 归属/禁用/unknown/计量类键保留，命令/决策/操作回执正文清空，NOT NULL digest 用固定 tombstone；`content_manifest` 属 §9.11 固定集合不清）
  - 冻结决定：`agent_round`/`agent_body_payload` 不纳入 `CleanupIdentitySet`（正文域经会话/执行/资产键定位，`agent_body_payload` 按受清 owner 的 `body_ref` 准确行删除）
  - 证据：真 sidecar 集成 6 passed（`dd32d2c44`）+ 阶段单调拒绝回退集成（`c320374f4`）
- [x] E-06-6 门禁与里程碑独立检视（覆盖 E-06-1…E-06-5 累计 diff）
  - [x] 已完成范围检视（E-06-1…E-06-5a，基线 `e0e8c651a^..efb882890`）：2 阻断（B1 重试缺失豁免、B2 artifact `cleaned_at`）+ 1 红线缺口（R5 store 引擎守卫）已修复于 `a44cfea42`；R1/R2/R4 登记 `P2-AgentCleanup-Residual-Risks`
  - [x] 修复差异 `a44cfea42` 独立复核：B2 成立并关闭；**B1 判定不成立**（`confirm` 首次即提交 fence → 首次缺失失败转 retry_pending 后二次确认被误放行，违反「查不到不是已清」）→ 已改用 job 持久化 `error_category` 作证据重做于 `4d5f0d25f`，并新增「预检阶段缺失仍拒绝」反例用例；R5 语义失效（运行库引擎 `is_disposed` 恒 False）登记债项于 `44733c66c`
  - [x] `4d5f0d25f` B1 重做版独立复核 → **五轮收敛闭环**：`4d5f0d25f` 复核发现 preflight 异常被通用 except 记成 `cleanup_execution_failed`（绕过）→ `627180613` 独立记 `cleanup_preflight_failed` → 复核发现外层 try 二次捕获覆盖 → `e084966b0` 移出外层 try → 复核发现缺失拒绝分支仍在删除阶段 try → `e627fd4fb` 整体移入预检 try 并加拒绝写入失败用例 → **第五轮复核结论「可关闭」**（写入点位 4 处、允许类别均只在 delete 之后；`save_cleanup_phase` 单条 UPDATE 无部分成功；R2 成立）
  - [x] E-06-5b 完成后补检 §10.7 表 2/4 行与白名单：第四轮独立复核 PASS_WITH_NITS（阻断 0，八条契约通过；R2/R4/R5/R21/R22 无违规），N2/N3 当轮修复，N1 登记技术债 ⑤，N4/N5 接受

---

# Tasks：F-01 股票/策略/API 单链与旧直接场景（实施中）

> DoD（DD-F34 / HANDOFF §11.1）：单股/批次一 intent 一账；0.005 confidence 只换一次；score 不放大；缺失/bool/NaN 拒绝；原新闻/FinBERT 路径不强会话化。
> 依赖说明：D-03 已完成；E-02/E-04 仍受 needs-spike / Q5 阻塞，故凡是需要 AgentService.submit→原生 root workflow launch 的接线（StockAgentCall 身份透传、每股唯一研究调用、停止选股终止关联）暂不实施，不造占位半链。

- [x] F-01-1 `6397e8eb6` 股票 final 输出归一化契约（30 用例；RED 8 例先行）
  - `services/ai_service/output.py`：score/confidence 显式拒绝 bool 与非有限数（NaN/±Inf/"nan"/"inf"），0.005→0.5 只换一次（JSON 往返仍 0.5）、1/1.0 不换算、score 不缩放不钳位、conclusion_label 权威
  - `tests/unit/test_ai_output_normalization.py`；紧耦合回归 502 passed
- [x] F-01-2 `b06ea31f3` 一账回归 + 直接路径非会话化守卫（12 用例 + test_ai_mixin 301 passed）
  - 场景链结果（Recorder 已入账、无 usage/cost）不得二次落账；六个新闻/FinBERT/洞察模块不得 import agent_service 或引用 execute_chat_turn/AgentService
- [x] F-01-3 `348468791` AgentService 只读门面 + AgentDao.read_command 原事实（单测 7 + 真 PG 集成 6）
  - `services/agent_service/service.py`：read_command/read_session/get_snapshot/read_events/observe_session + aclose（幂等，关闭后 fail-closed PERSISTENCE_UNAVAILABLE，不释放共享 Engine）
  - `AgentDao.read_command`：ref 域校验、0 行/跨 target 歧义 INPUT_INVALID、坏枚举 OPERATION_UNKNOWN、next_action 恒 None
  - 写路径 submit/control/answer/search 不提供占位实现（依赖 root workflow 派发）
- [x] F-01-4 `5e4a1a3d7` 停止选股终止关联回归守卫（test_ai_mixin 290 passed）
  - 45 候选/3 批、并发=1 确定性序列：第二批前取消 → 仅前两只派发；返回行不伪造 analyzed/score；context 无 resume/paused
- [x] F-01-5 `10b5e8c43` AgentService.submit 受理入口与命令引用一致性（facade 单测 15）
  - 5 字段逐一核对，不一致 INPUT_INVALID 且不触达 DAO；输入非法转 INPUT_INVALID；关闭 fail-closed；原样返回受理回执（派发待 Q5）
- [ ] F-01-6 暂缓（依赖根 research workflow launch 所需的完整运行装配：模型桥 + Recorder + 工具/授权 + UI 入口；E-04 Q5 已于 2026-11-15 解除，但运行装配与 F-05 UI/VM 未实施，不造占位半链）
  - StockAgentCall（业务批次/股票身份/提交键/冻结输入）两层透传；批次先存身份 + 每股唯一研究调用 + retry 新轮关联
  - **接链约束（检视「中」项登记）**：`validate_ai_analysis_response` 只可对模型原始响应调用一次（非幂等：二次归一化 0.5→50.0）；接线时不得对已归一化结果再调用，也不得在重试路径复用同一响应对象两次
- [x] F-01-7 里程碑独立检视 PASS（F-01-1…F-01-5 累计 diff；F-01-6 阻塞子项显式标注待续）
  - 结论：中 1（归一化非幂等）+ 低 3（read_command 引用核对、test_ai_mixin 弱断言、直接路径守卫漏项）
  - 非幂等裁决：不改输出语义，docstring 明确单次调用 + F-01-6 接链约束（见上）
- [x] F-01-8 `fd72079a3` 检视低风险项①闭环：read_command 引用与存行同源
  - expected_revision 比对命令行；source_module/business_request_id 在存行链到根/比较路 execution（意图键非空）时比对，叶执行不存意图键不做无依据比对
  - 新增 4 用例（三字段不符 + 无意图键仅核预期版）；集成 10 passed、门面/类型单测 37 passed、ruff/pyright/pre-commit 全绿
- [x] F-01-9 `06cbb5793` 检视低风险项②③闭环：守卫扩展 + 弱断言加强 + 归一化调用约束
  - 直接路径守卫：`gguf_sentiment_adapter.py` 入清单；`from services import agent_service` 拼全路径；3 组守守卫合成 AST 用例
  - test_ai_mixin：停止用例断言对齐 docstring + 分数/状态恒等；一账用例补新链形态断言 + 旧链对照（`add_cost_cents(4)` 恰一次）
  - `validate_ai_analysis_response` docstring 明确单次调用约束（不改输出语义）
  - 验证：三文件 339 passed、ruff/format/pyright(0)/pre-commit 全绿

## F-03 草稿持久契约（进行中）

- [x] F-03-1 `4a5204484` `save_draft`：`DraftPayload`/`DraftSaveCommand`/`DraftReceipt` 严格 codec + 输入摘要；`AgentDao.save_draft` 会话行锁单事务（CAS 冲突零新事实、同键同输入查回原回执、异输入 INPUT_CONFLICT、跨用户域 INPUT_INVALID、会话 revision/updated_at 同事务推进）；`AgentService.save_draft` 边界校验 + 关闭 fail-closed
  - 证据：契约单测 33、门面 52、真 PG 集成 9（含并发单胜者）、相邻集成 21、`-k agent` 1094 passed/1 skipped、ruff/format/pyright(0)/pre-commit 全绿
  - 工程冻结：不透明选择片段（model_selection/send_target/role_prompt_selection）只冻结信封与安全约束（拒 float、递归禁凭据/批准键），子结构归 F-05/F-03-3
- [x] F-03-2 `649b4790a` `change_session`：`SessionAction` 八值 + `SUPPORTED_SESSION_ACTIONS` 五动作（create/rename/pin/archive/unarchive）；`AgentDao.change_session` 单事务（create 预期版 0/目标已存在 INPUT_INVALID/并发 IntegrityError→INPUT_CONFLICT；元数据行锁 CAS，REVISION_CONFLICT 零新事实；归档不改执行/草稿/标题）；revise/branch 显式 unsupported_action
  - 证据：契约单测 23、门面 80、真 PG 集成 13（含并发单胜者）、相邻集成 35、ruff/format/pyright(0)/pre-commit 全绿
- [x] F-03-4 `04315a355` 草稿来源谱系边写入方：`SourceRelationKind.DRAFT_REFERENCE`；`save_draft` 同事务 `_sync_draft_source_links`（只引用已受管源版本、未登记 INPUT_INVALID 整体回滚；先删本会话 draft 边再写当前集合，consumer_version=新草稿版；禁用源可入草稿，拦截在派发门）；F-02 草稿上下文缺口关闭
  - 证据：草稿集成 14（5 新增）、来源读取端到端 1 新增（保存后当前修订可读/旧修订拒绝）、相邻集成 35、ruff/format/pyright(0)/pre-commit 全绿
- [x] F-03-3 `859d3e9ee` `transfer_draft`/`read_draft_transfer`：双方预期版 + fence + 唯一可发送 owner（§4.2 DraftTransfer 协议）
  - 契约：`DraftTransferSourceKind`/`DraftTransferPhase`/`SendableOwner` + `DraftTransferCommand`（11 键）/`DraftTransferReceipt`（16 键）严格 codec；`transfer_id` 沿原命令派生（user_scope+command_id+source_kind+source_id+target）；`payload_digest` 必须等于载荷规范摘要；`confirmed` 必须 true；`CommandRef.expected_revision==expected_target_draft_revision`；`session_draft` 要求 `source_session_id==source_id` 且目标≠源；`business_preparation` 禁止 `source_session_id`；`is_empty_draft_payload`（正文空但有未发送选择不是空位）
  - DAO：`transfer_draft` 单显式事务直达 completed——双方会话按 id 排序行锁、锁内命令键重放/异输入冲突；双方 CAS 冲突零新事实；目标非空/归档拒绝；会话稿源版与载荷逐字节核验、空源拒绝；`transfer_source_*` claim 与阶段同事务（部分唯一索引拒同源版第二转移）；目标安装+来源边；源清空+`draft_transfer_ref` 不可发送恢复记录+源边清空。`read_draft_transfer` 只查原回执（非转移/未知命令 INPUT_INVALID、坏回执 OPERATION_UNKNOWN）
  - 工程冻结（Q7 待裁决项，按可辩护最小语义推进）：`source_id` 在 session_draft 即源会话身份；成功源保留列为 `draft_payload=NULL` + `draft_transfer_ref`，完整 payload 留在命令 `request_payload`；`business_preparation` 仅 claim（本库无源行，receipt `source_revision=None` 不伪造可靠值）；单事务实现只落 `completed`，其余相位供分阶段实现/重启核查
  - `save_draft` 同事务清空 `draft_transfer_ref`（新版本不是被移入旧版本，不被发送门误判）
  - 证据：契约单测 33、门面 98（新增 8）、真 PG 集成 21、相邻集成 59、`-k agent` 1163 passed/1 skipped、ruff/format/pyright(0)/pre-commit 全绿
- [x] F-03-2b `1aff5ce95` `revise`/`branch`：消息身份/版本核验 + 新因果前缀/父子会话与 branch_metadata
  - 契约：`SUPPORTED_SESSION_ACTIONS` 增 revise/branch；`SessionChangeCommand` 增 `source_message_id`/`source_message_revision`/`causal_cutoff_message_id`（wire 8 键）；revise/branch 必须源消息身份+版本（版本映射 `agent_message.subject_revision`，不用可变行 revision）；branch 预期版 0 且必须标题；revise 拒绝标题；其他动作携带源字段即拒绝；截点=源消息显式拒绝、由 DAO 校验严格早于源消息
  - DAO：`revise` 会话行锁 CAS + 核源消息属本会话/版本匹配 → `causal_head_ref`=源消息之前最近一条（或显式截点；首条→空前缀），不改消息/不建会话/不派发；`branch` 新建会话 `parent_session_id`+`branch_metadata`（冻结键集 schema_version/source_session_id/source_message_id/source_message_version/causal_cutoff_message_id，只引用不含正文）+`causal_head_ref`，不复制正文；重复目标不静默复用；跨用户域/跨会话源 INPUT_INVALID、版本不符 REVISION_CONFLICT 零新事实；同命令重放查回原回执
  - 证据：契约 57（新增 19+改 1）、门面 77（新增/改 1）、真 PG 集成 27（新增 12 + change_session 回归）、`-k agent` 1185 passed/1 skipped，ruff/format/pyright(0)/pre-commit 全绿
  - 工程冻结（Q7）：原消息「身份/版本」= `agent_message.message_id` + `subject_revision`（不用可变行 revision）；`branch_metadata` 键集 = schema_version/source_session_id/source_message_id/source_message_version/causal_cutoff_message_id（只引用不含正文）；revise 写本会话 `causal_head_ref` 并由该命令推进 session revision
  - 独立检视闭环：`dc4efa243`（change_session 并发同键重放）、`d152b3b2a`（约束名无关复查 + 空稿默认 EXECUTE）、`df3a9398e`（移除 create 预检查窗口）；最终复核「阻断项全部关闭，同意 F-03 里程碑通过」；复验 1186 passed/1 skipped

## F-02 读取补充（read_source / read_artifact / read_operation）

> 合同决定见 `spec.md` 计划 B（B1–B5）。三片均为纯读、零派发，不依赖 Q5/E-04。

- [x] F-02-1 `3f37ca0f4` `read_source`：`SourceReadQuery`（4 身份 + 互斥上下文）+ `AgentDao.read_source` + `AgentService.read_source`
  - 文件：`core/agent_types.py`（追加类型与严格 codec）、`data/persistence/daos/agent_dao.py`、`services/agent_service/service.py`
  - 行为：asset 版本/摘要/locator 三方对齐；body_text 或 body_ref 载荷（长度+SHA-256）核验；未准备返回 unread/unavailable 原因；disabled/tombstoned → available=False；坏事实 OPERATION_UNKNOWN
  - 证据：单测 11（`tests/unit/test_agent_source_read_query.py`）、真 PG 集成 12（`tests/integration/test_agent_dao_read_source.py`）、agent 单测回归 1034、ruff/format/pyright/pre-commit 全绿
- [x] F-02-2 `9dc52c249` `read_artifact`：`ArtifactReadQuery`/`ArtifactRead` + 码点分页游标 + `AgentDao.read_artifact` + 服务门面
  - 证据：单测 14（`tests/unit/test_agent_read_query_paging.py`）、集成 10（`tests/integration/test_agent_dao_read_artifact.py`）、agent 单测回归 1048、门禁全绿
- [x] F-02-4 `51678f6c0` 独立检视缺陷闭环（2 MAJOR/5 MINOR）：
  - 上下文登记核对 `_assert_source_registered`（草稿/执行谱系边 + 轮次归属 + input_revision）
  - 游标身份绑定 `b:{artifact_id}:{version}:{start}` / `o:{operation_id}:{start}`
  - 正文 read_state 门控 + source_ref.read_state 一致；digest 三方核对；schema_version 拒 bool；available=False 必有原因；空页语义固化；result_payload 原子返回
  - 证据：单测 30、真 PG 集成 41（来源 18/成果 12/操作 11）、agent 单测 1053 passed、门禁全绿
- [x] F-02-5 `55e85c9cb` 第二轮复核闭环：
  - 草稿上下文改为「来源所属会话 `agent_session.draft_revision` == 请求修订 + session_id 非空边」；核实无 draft 边写入方（属 F-03），记为已知缺口不造写入路径
  - 正文门控收窄为 unread/unknown；disabled/unavailable 返回已存正文 + available=False + 原因（禁用≠清理）
  - 游标 docstring 与实现对齐（用户域由查询过滤、不入游标）；来源集成 19 passed、agent 单测 1053、门禁全绿
- [x] F-02-6 `942dc29f6` 第三轮复核 PASS + 残余覆盖：unknown/unavailable 正文语义参数化用例（来源集成 21 passed）
- [x] **F-03 承接（已于 F-03-1/F-03-4 完成）**：草稿来源登记写入方（`consumer_kind='draft'` + `session_id` + `consumer_version=draft_revision`）与 `agent_session.draft_revision` UPDATE 写入方均已落地（`4a5204484`/`04315a355`），草稿上下文端到端用例通过。

## 里程碑 E-05：成果交付与 Windows 目标保护

- [x] E-05-1 `10fbe0d3d` 交付契约：格式/阶段/目标策略、`DeliveryTarget` 三态自洽、`ArtifactDeliveryCommand` 稳定身份与预期版交叉校验、30,000 UTF-16 分段（非 BMP 不拆代理对）、`DeliveryReceipt` 阶段/效果自洽；36 单测
- [x] E-05-2 `73f5c468b` Windows adapter：同卷暂存+fsync+回读、确认后目标/父目录变化拒绝、重解析点（含 junction 父目录）拒绝、`os.replace` 后回读不符→unknown、取消只清暂存；12 真文件用例（1 symlink 权限跳过）
- [x] E-05-4a `edce55a71` 交付 manifest 与阶段 CAS：`DeliveryManifest` 16 键严格 wire、阶段迁移表（unknown/终态吸收）、`AgentDao.save_delivery_phase` 行锁 + revision CAS（同内容幂等/异内容冲突/跨用户/异 delivery/坏行拒绝）；单测 19 + 真 PG 集成 8 passed
- [x] E-05-3 `7eb581043` 三格式渲染与逐字段回读（22 单测）
  - 工程冻结：渲染输入为通用 `DeliveryField` 序列（record:pointer + 显式类型 + 分段），不绑定 `ResearchResult` 具体键集；E-04 冻结 payload 后无需改渲染器
  - core `build_delivery_fields`：元数据 18 键固定顺序显式 null；`next_cursor` 未取全正文 → `page_not_complete`；`result_payload=None` 不补造字段、空容器显式行（R21）；数字保留原词法；30,000 UTF-16 分段
  - services `delivery_render.py`：MD/CSV(BOM)/XLSX 渲染 + `read_delivery_fields` + `verify_delivery_readback`；XLSX 公式单元格 0
- [x] E-05-4b `30a19654f` DeliveryService 编排：读全成果（跨游标，不截断）→`manifest_saved`→渲染→同卷暂存（核回读摘要）→`staged`→逐字段回读→`validated`→新鲜核来源→`publish_claimed`→原子替换→`published_confirmed`；发布前可确证失败保旧阶段不发布，取消先胜落 `cancelled_before_publish` 并只清本交付暂存，认领后不可确证落 `unknown` 且不重试；22 单测
- [x] E-05-4b-2 `e26cea133` 真文件+真 PG 集成 4 用例：目标无暂存残留、250,000 码点跨游标完整交付、取消保旧目标字节、非标准来源状态按 `unknown` 记录
- [x] E-05-4c `0ed60349b` 原交付查回：core `DeliveryRead`（operation 修订 + manifest，`None`=尚未开始）、`AgentDao.read_delivery`（跨用户/缺失 INPUT_INVALID、坏行/身份不符 OPERATION_UNKNOWN、只读不重推）、`AgentService.read_delivery` 门面；门面单测 2 + 真 PG 集成 6
- [x] E-05-5 里程碑独立检视 PASS（三轮；修复 `d26f204d9` B1 目标身份 / `78f8a281d` N1+N2+R5 / `6c6d95ef1` 父目录身份）
  - 阻断闭环：暂存文件自身 `(volume_serial,file_id)` 入 manifest 证据链、替换后身份断言（同内容他文件不得报成功）、`os.replace` 异常一律 unknown、零文件 ID 卷（含父目录）fail-closed、暂存名派生标识校验、同阶段异事实改写 `INPUT_CONFLICT`
  - 残余登记：`docs/debt/known-technical-debt.md`（P2-AgentDelivery-Residual-Facts / P3-AgentDelivery-Resume-NotImplemented）；范围外续接/按成果查 unknown/残留清理见该条目
  - 验证：交付单测 42 passed/1 skipped；交付集成 19 passed（真 sidecar + 真文件）；文档一致性 419 passed；ruff/pyright(0)/pre-commit 全绿
  - 台账修复：`a90daf21a`（singleton 文档 21 个、governance-ids 登记 SQ-09、治理 ID 扫描排除许可证标识）
- 门禁：E-05-1/2 各自 ruff/format/pyright(0)/pre-commit 全绿；E-05-1+E-05-2 合跑 47 passed/1 skipped；agent 相关单测 1355 passed/3 skipped；全量 pyright 25 errors 均为 dbos/pydantic_ai 未安装的 reportMissingImports（环境缺依赖），改动文件 0 errors
- 风险：DD-G03 非协作竞争未被证明为零窗口；当前以「确认后变化→拒绝」「替换后回读不符→unknown」作为可确证边界，待独立检视评估是否需 OS 级排他







## E-06-5b / 阶段单调（2026-11-15）

- `dd32d2c44` feat(agent): E-06-5b 结构回执与内容摘要清理（§10.7 表第 2—4 行）
  - 清空：`agent_command.request_payload/receipt_payload`、`agent_decision.question_payload/answer_schema/data_consent_payload`、`agent_operation.worker_refs/delivery_manifest/data_consent_claims`、`agent_source_link.locator_payload`
  - 置 NULL：`agent_execution.intent_digest`、`agent_round.frozen_digest/actual_digest`、`agent_operation.parameter_digest`、`agent_decision.parameter_digest/source_scope_digest`
  - tombstone：`agent_artifact.actual_input_digest/result_digest`、`agent_command.input_digest`
  - ~~`verified` 时清 `agent_cleanup_job.content_manifest`~~（已撤回：属 §9.11 固定集合，检视 B1）
  - 保留（第 5 行归属/禁用/unknown/计量）：`claim_ref`、`attempt_refs`、`unknown_refs`、grant 三列、phase/display_state/计数
  - 证据：集成 6 passed（真 sidecar PG）；单元清理 67 passed；agent 单测 1440 passed/3 skipped；pyright 改动文件 0/0；pre-commit 全绿
- `c320374f4` fix(agent): 清理阶段单调——已 verified 拒绝回退
  - 非 verified 写入追加 `phase <> 'verified'`；新集成用例断言拒绝与行不变
  - 证据：cleanup+cleanup_job 集成 13 passed（真 sidecar PG）；ruff/format/pre-commit 全绿
- 待办：E-06-6 增量独立检视（`e627fd4fb..c320374f4`）；Q5 两包授权、E-02-5 spike 仍待裁决

## E-06-6 检视两轮与修复（2026-11-15 续）

- 第一轮（基线 `e627fd4fb..c320374f4`）→ FAIL：B1 content_manifest、B2 delivery_manifest、B3 fence 回退
  - 修复 `c95108c63`：撤回 B1/B2 整列置空；B3 加 verified 守卫 + `test_fence_write_rejected_after_verified`
- 第二轮复核 → B1/B3 可关闭；B2 仅关闭「撒谎」层，§10.7 第 3 行留存仍阻断；新增三条建议
  - `9209dc324`：`read_operation` 清理后报 `result_cleaned`（按会话 `cleaned_at`），`test_cleaned_session_reports_result_cleaned_not_bad_row`
  - `8250515da`：`commit_cleanup_fence` 拒绝 verified 记录；三条回归护栏
    - `test_verified_keeps_content_manifest`
    - `test_fence_rejects_verified_record`
    - `test_cleanup_keeps_delivery_manifest_and_reports_result_cleaned`
- 证据：cleanup+cleanup_job+read_operation+清理单元 **52 passed**（真 sidecar PG）
- **E-06-6 未签署**（待裁决）：
  1. 交付回执 cleaned receipt 契约：冻结 `DeliveryRead` cleaned 态（或书面批准私有 target 路径留存），否则清理后 `delivery_manifest` 的私有路径仍可查
  2. §10.7 第 5 行摘要留存逐项裁决（命令/轮次/执行摘要 tombstone 导致清理后重放统一 `INPUT_CONFLICT`）
- 其他仍待授权：Q5 两包安装（E-04/E-01 PDF）、E-02-5 真实 FastMCP spike

## E-06 收尾硬化（2026-11-15 第三段）

- `2839471b9` fix(agent)：content_manifest 纳入同身份重放核对；并发 verified 时
  `_mark_retry` 不掩盖原始失败
  - 证据：affected 集成+单元 56 passed；`tests/unit -k agent` 1441 passed / 3 skipped；pre-commit 全绿
- `767e29ecf` test(agent)：资产闭包来源边覆盖（tuple 资产键分支）
  - 证据：`test_agent_dao_cleanup.py` 8 passed（真 sidecar PG）
- E-06 待签署唯一阻断：交付回执 cleaned receipt 契约（`DeliveryRead` cleaned 态 / 书面批准留存）
- 其他待授权：Q5 两包安装（E-04/E-01 PDF）、E-02-5 真实 FastMCP spike

## E-06 里程碑签署（2026-11-15 第四轮独立复核 PASS_WITH_NITS）

- `38e37b185` feat：交付回执随清理清空并按 `manifest_cleaned` 读回
- `f51de3fa0` fix：`manifest_cleaned` 仅对曾交付（artifact 身份已写）的操作成立；`content_manifest` 规范 JSON 身份比较
- `711c448d4` fix：`save_cleanup_job` 入口即校验规范 JSON；交付清理回读用例补种 round/artifact 走真实写路径
- `6f12de2bb` fix：fence 不改写 `phase`（N3）；`content_manifest` 落库取规范反解析值（N2）；N1 登记技术债 ⑤
- 第四轮结论：阻断 0；八条契约通过；R2/R4/R5/R21/R22 自查无违规
  - N2/N3 当轮修复 + 回归用例；N4/N5 接受为已知残余
- 证据：`test_agent_dao_cleanup_job.py` 13 passed；cleanup 19 / delivery+read_operation 30 / cleanup 单元 15；pre-commit 全 hooks 通过
- 环境风险登记：5 文件合跑时 sidecar 曾在 delivery 模块前退出（18 × `ConnectionRefusedError`），分文件复跑全绿；判为测试宿主进程级不稳定，不据此判实现失败
