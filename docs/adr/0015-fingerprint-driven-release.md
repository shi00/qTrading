# ADR-0015: FinBERT 产线指纹驱动发布（内容寻址去重）

> Status: Accepted
> Date: 2026-10-16
> Owner: 架构维护者
> 关联: ADR-0012 / ADR-0013（新闻情绪打分产线，`finbert-train.yml` 为其训练/发布载体）

## Context

`finbert-train.yml` 的 `model_version` 为不可变发布标识（FT-05：publish 步骤对已存在
tag 拒绝覆盖，防 lineage——note ↔ 资产 digest ↔ 运行参数——静默错位），但拒绝发生在
训练**之后**：「输入无变化」的重跑（误触发 / 中断恢复 / 人为重复 dispatch）仍会先白跑
1~3 小时训练与全链 Gate，再被发布步骤拒绝。重复发布的本质是「训练输入相同却生成新发布」：
产线固定 seed，输入一致时产出模型等价，新 Release 仅是噪声。

已有的效果硬门槛（FT-02）与一致性 Gate（N0-3）校验「产出质量」，不校验「是否需要训练」；
发布链缺一个**发布前的输入级去重判定**。

## Decision

把发布判定前移为**内容寻址**：job1 在 Checkout 之后、装依赖之前运行指纹路由步骤
（`scripts/compute_training_fingerprint.py`，stdlib 运行——F3 复用链传递依赖的
utils/core 模块当前亦全 stdlib，引入三方依赖时路由即 fail-closed，方向安全；
runner 预置 `python3` 直接运行），
对训练输入计算指纹并与最新 `finbert-gguf-v*` Release 随附的 `fingerprint.json` 对比——
指纹相同即拒绝（exit 1），不同或不可比才放行。

### 1. 指纹 spec v1（分量 = 训练输入的完整覆盖）

- `corpus_sums_sha256`：训练语料 Release 的 `SHA256SUMS.txt` **文件内容**哈希（语料内容
  经 `sha256sum -c` 闭环绑定到该清单）；
- `script_sha256`：脚本白名单**显式枚举**（禁 glob，防意外扩面/漏面）逐文件 sha256——
  训练 / 导出 / 补丁 / 双 Gate 五个产线脚本 + 本脚本自举；
- `workflow_sha256`：`finbert-train.yml` 全文件哈希（覆盖依赖 pin、seed、超参、步骤编排
  等全部内联输入）；
- `base_model_revision` / `llama_cpp_commit`：从 workflow 文本 fail-safe 正则提取（与
  `workflow_sha256` 冗余，但让 diff 可读——能直接看出「换基座」还是「换工具链」）。

总指纹 = sha256(canonical JSON (sort_keys + 紧凑分隔符))；任一分量缺失 / 不可提取即
`FingerprintError`（fail-safe，绝不出具部分指纹）。

### 2. 四支柱

① **canonical JSON**（跨环境字节稳定）；② **runner 单环境闭环**（指纹计算与对比在同一次
checkout 内由本脚本完成，不依赖外部状态）；③ **自举**（本脚本自身哈希入白名单，指纹逻辑
变更即指纹变更）；④ **fail-safe**（缺任一分量即失败）。

**已知豁免（documented）**：runner 镜像（ubuntu-latest 滚动基线）不入指纹——滚动镜像每周
漂移会使指纹几乎永不相同、去重失效，而固定 container 需自维护镜像（FT-08 已论证不采用）；
代价由 Release note 环境 lineage 段兜底（ImageOS/ImageVersion + 全量依赖清单 SHA256）。

### 3. 路由语义与退出码契约

- **显式 `model_version`**：原样使用；对应 Release tag 已存在时秒拒（FT-05 fail-fast
  前移到训练前）；
- **留空**：semver 最新 tag minor+1，并循环探测 tag 空位（`tag_exists` 命中即继续
  minor+1）；无历史 Release 时基线 0.0.0 → 0.1.0；
- **对比判定**：无历史 Release（NO_BASELINE）/ 锚点无 `fingerprint.json` 资产（旧版产线
  发布）/ spec 不兼容 → 放行（INCOMPARABLE_SPEC，重建基线，不静默判等）；指纹一致且未
  勾选 `force_retrain` → exit 1 拒绝（打印分量快照供归因）；指纹不同 → 放行并打印分量级
  diff；`force_retrain` 为显式重训豁免（COMPARABLE_SAME_FORCED）；
- **退出码**：0 = 放行 / 锚断言通过；1 = 输入无变化（业务阻断）；2 = 错误（fail-closed：
  分量缺失 / gh 故障 / 锚错位 / 版本冲突）；
- **指纹先落盘再对比**（`stage_a_out/fingerprint.json`）：skip 分支同样保留指纹，排障
  artifact 可归因。

### 4. F2 语料锚断言

指纹路由下载的语料 sums 与语料步骤实际下载的资产之间存在时间窗——corpus Release 的 tag
不可变但**资产可编辑**，期间被替换会使指纹锚与训练输入错位。语料下载校验后以
`verify-corpus-anchor` 比对本地 `SHA256SUMS.txt` 与指纹锚，错位即 fail-fast（先于
corpus manifest 校验）。

### 5. F3 复用与 gh 层 fail-safe

semver 解析 / 最新 tag 选择经 importlib 复用 `verify_finbert_effect_gate.py` 的
`parse_semver_tag` / `select_latest_release_tag`（零新逻辑，单一事实源）。gh 层区分
「资产缺失」与「基础设施故障」：语料 sums 资产缺失即失败（必选分量）；基线
`fingerprint.json` 缺失 → INCOMPARABLE_SPEC 放行；`tag_exists` 仅 stderr 含 "not found"
判 False，其余错误 fail-closed。

## Consequences

- **正向**：「输入无变化」的重跑在装依赖前被拒绝（省 1~3h 与全链计算）；重复发布根因
  消除；`fingerprint.json` 随 Release 分发后，跨次训练的输入 lineage 可精确比对与归因
  （换语料 / 换脚本 / 换基座 / 换工具链在分量 diff 中一目了然）。
- **负向**：产线脚本 / workflow 的**任何**变更（含注释、措辞）都会翻转指纹并放行训练——
  保守方向，宁可多训不可漏训；spec v1 升版后旧基线全部按 INCOMPARABLE_SPEC 重建。
- **版本粒度**：自动递增仅用 minor（patch 归零）；major / patch 语义当前产线无消费场景，
  留待需要时扩展 spec。

## Alternatives

1. **维持现状（训练后 FT-05 拒绝）**：根因不消除，误触发仍白跑 1~3h，否决。
2. **发布产物哈希去重（对比历史 GGUF 文件）**：须先训练出产物才能对比，无法避免训练
   成本，且大文件下载比对成本高，否决。
3. **runner 镜像入指纹**：滚动 `ubuntu-latest` 每周漂移使指纹几乎永不相同，去重失效；
   固定 container 的权衡已在 FT-08 论证，以 note 环境 lineage 兜底，否决。
4. **glob 扫描 `scripts/` 目录**：新文件静默扩面 / 漏面（对抗面 F4），改显式枚举 +
   单测守护，否决。
