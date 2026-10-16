# FinBERT 模型产线流水线机制

> **定位**：FinBERT 新闻情绪模型产线（微调 → 导出 → 双 Gate → 指纹驱动发布）的**机制总览正本**——面向需要触发产线、修改产线脚本或排障的开发者与 AI 助手。
> **正本边界**：决策依据见 [ADR-0012](../adr/0012-news-sentiment-inference-paradigm.md)（本地推理范式）/ [ADR-0013](../adr/0013-runtime-selection-final-gguf.md)（运行时选型终局）/ [ADR-0015](../adr/0015-fingerprint-driven-release.md)（指纹驱动发布）；模型产物获取操作见 [how-to.md「13. 获取新闻情绪模型产物」](./how-to.md#13-获取新闻情绪模型产物finbert-gguf-v-release)；运行时集成见 [ai-service.md](../patterns/ai-service.md)；workflow 与脚本内注释为实现细节唯一事实源，本文不复制版本号等易漂移信息。

## 1. 产线全景

产线由 `.github/workflows/finbert-train.yml` 承载，**仅 `workflow_dispatch` 手动触发**，输入 `model_version`（可选，留空自动 minor+1）、`corpus_tag`（默认 `corpus-v1`）、`force_retrain`（默认 false）：

```text
workflow_dispatch
  │
  ▼
指纹路由（ADR-0015，装依赖之前）──────────────────────────────┐
  训练输入指纹 vs 最新 finbert-gguf-v* Release fingerprint.json │
  输入无变化 → exit 1 拒绝重训（省 1~3h）                        │
  ▼ 放行                                                        │
临时安装训练依赖（全量精确 pin，不进 pyproject）                 │
+ llama.cpp 工具链（commit 锚定）+ pre-tokenizer 登记补丁        │
  ▼
基座模型下载（HF Hub 固定 revision）
+ 语料下载 + 三重校验（SHA256 全量 / F2 锚断言 / manifest 校验）
  ▼
固定 seed 训练 → 拆头导出 GGUF（f16）+ head.npz
  ▼
一致性 Gate（N0-3 四判据）→ 效果 Gate（FT-02）
  ▼ 通过
SHA256SUMS + release note（lineage）→ 上传发布 artifact
  ▼
publish-release job（sha256 复验 → 发布 finbert-gguf-v*）
  ▼
fingerprint.json 随 Release 分发 → 成为下一轮训练的去重基线（闭环）
```

## 2. 阶段与脚本一览

job1 `train-export-gate`（全链路 `contents: read`）：

| 阶段 | 脚本 | 输入 → 输出 | 失败行为 |
|------|------|------------|---------|
| 输入校验 | — | dispatch inputs → `corpus_tag` | 非法 semver / tag 注入即失败（fail-fast） |
| 指纹路由 | `compute_training_fingerprint.py` | 仓库 + 语料 sums → `stage_a_out/fingerprint.json` + job outputs（`model_version` / `release_tag`） | 见 §3.2 退出码 |
| 依赖与工具链 | `patch_llama_cpp_pre_registry.py` | — | 依赖 pin / 工具链 / 补丁任一步失败即失败 |
| 基座与语料 | `corpus_manifest.py verify` + `verify-corpus-anchor` 子命令 | 语料 Release → `data/corpus` + 校验报告 | SHA256 / F2 锚 / manifest 三重校验任一失败即失败 |
| 训练 | `train_finbert.py` | 语料 + 基座 → `stage_b_out/best` | 超参固化在脚本内，seed 固定 |
| 导出 | `export_finbert_gguf.py` | `best` → f16 GGUF + `head.npz` | — |
| 一致性 Gate | `verify_finbert_gguf.py --head` | 产物 vs 训练参考 → `verify.json` | N0-3 任一判据 FAIL → 发布不执行 |
| 效果 Gate | `verify_finbert_effect_gate.py` | `train_summary.json` + 历史 Release note → `effect_gate.json` | 低于「锚点 − 0.03」FAIL；首版 NO_BASELINE 不阻断 |
| 发布准备 | — | → 发布 artifact（gguf / head / SHA256SUMS / fingerprint.json / note） | lineage 文件缺失即 fail-fast，不发布 |

job2 `publish-release`（唯一 `contents: write`，不 checkout、不执行仓库脚本）：下载 artifact → `sha256sum -c` 复验 → `gh release create`；同 tag 已存在即拒绝（FT-05）。

## 3. 指纹驱动发布（ADR-0015）

### 3.1 指纹 spec v1 分量

指纹 = canonical JSON（`sort_keys` + 紧凑分隔符）的 SHA256，分量覆盖全部训练输入：

| 分量 | 内容 |
|------|------|
| `corpus_sums_sha256` | 语料 Release `SHA256SUMS.txt` 文件内容哈希 |
| `script_sha256` | 六文件**显式枚举**白名单逐文件哈希（5 产线脚本 + compute 脚本自举；禁 glob） |
| `workflow_sha256` | `finbert-train.yml` 全文件哈希（覆盖依赖 pin / 超参 / 步骤编排等内联输入） |
| `base_model_revision` | HF 基座固定 revision（从 workflow 文本 fail-safe 提取） |
| `llama_cpp_commit` | llama.cpp checkout ref（同上） |

任一分量缺失 / 不可提取即 `FingerprintError`（fail-closed，不得放行）。runner 镜像不入指纹（滚动基线会使去重失效），环境漂移由 Release note 环境段逐 run 记录兜底（ADR-0015 Alternatives）。

### 3.2 路由四态与退出码

| 情形 | 行为 | 退出码 |
|------|------|--------|
| `NO_BASELINE` / `INCOMPARABLE_SPEC`（无历史 Release；锚点无 fingerprint.json 资产；spec 不兼容 / 非 dict 基线） | 放行 | 0 |
| `COMPARABLE_DIFFERENT`（指纹不同） | 放行 + 打印分量级 diff | 0 |
| `COMPARABLE_SAME`（指纹一致且未 `force_retrain`） | `::error` 拒绝重训 | 1 |
| 基础设施故障 / 分量错误 | fail-closed | 2 |

gh 层 fail-safe：`gh release download --pattern` 零命中（非零退出 + no-match 文案）判**资产缺失**返回 None（存量旧 Release 无指纹资产 → 不可比放行，不死锁）；其余 gh 错误一律 fail-closed；tag 存在性查询仅「release not found」判 False。

F2 语料锚断言：语料下载校验后 `verify-corpus-anchor` 比对**实际下载的** `SHA256SUMS.txt` 与指纹锚，错位 fail-fast（先于 manifest 校验）——防「路由下载」与「训练下载」之间资产被替换导致指纹与输入错位。

### 3.3 什么动作会改变指纹

- 修改任一白名单产线脚本（含 compute 脚本自身）；
- 修改 `finbert-train.yml`（依赖 pin、超参、seed、步骤编排）；
- 更换 `corpus_tag` 或语料 Release 资产内容；
- 更换基座 revision / llama.cpp commit。

以上动作指纹必变 → 正常放行重训。**反例**：仅提升 `model_version` 不改任何输入 → 指纹一致被路由拒绝（输入相同产物必同）——这正是把 FT-05 重复发布拒绝从训练后前移到训练前的目的。

### 3.4 `force_retrain` 适用边界

显式豁免开关：指纹一致仍继续训练。适用：训练环境变化需要对照产物、指纹分量未覆盖的因素（如 runner 镜像漂移验证）、人工端到端演练。产物发布仍须提升 `model_version`（FT-05 版本不可变不受影响）。

## 4. 语料多版本与 lineage（FT-07）

- `corpus_tag` 指向语料 Release（资产含语料 split 与 `SHA256SUMS.txt`；多版本共存）；
- `corpus_manifest.json`（schema `finbert-corpus-manifest.v1`）为行数 / digest 期望值**唯一权威来源**；**新增语料版本必须随附 manifest**；
- legacy 语料（无 manifest，如 corpus-v1）经 `scripts/corpus_manifest.py` 的 `LEGACY_CORPUS_REGISTRY` 显式登记兜底，Release note 显式声明「未做 manifest 校验」（R21：不把未校验伪装成已校验）；
- Release note lineage：语料 tag + SHA256 + 行数守恒、训练指纹、基座 revision、llama.cpp 工具链 commit、训练环境全量依赖清单 SHA256（FT-08）——模型 ↔ 语料 ↔ 环境可双向溯源。

## 5. 隔离与权限

- **与主 CI（ci_cd.yml）隔离三机制**：仅 `workflow_dispatch` 触发；concurrency 固定分组 `finbert-train` 且 `cancel-in-progress: false`（1~3h 训练不可被同组新 run 中断）；发布 tag 前缀 `finbert-gguf-` 不匹配主 CI `v*.*.*` tag 过滤（不误触发 build-windows）；
- **FT-06 最小权限拆分**：训练 / 导出 / Gate（job1）全链路 `contents: read`；写权限单独收敛到 publish-release（job2），仅消费已上传 artifact；
- **FT-05 版本不可变**：同 tag 拒绝覆盖发布（防 note ↔ 资产 digest ↔ 运行参数 lineage 静默错位）；首次发布中断须人工删除 Release 留痕后重跑；
- **训练依赖不进 pyproject**（ADR-0012 修订经 ADR-0013 承载）：job 内临时安装且全量精确 pin，版本清单以 workflow 安装步骤注释为唯一事实源（升级须整组重训验证后同步更新）。

## 6. 常见开发者任务

| 任务 | 操作 |
|------|------|
| 修改产线脚本 / 超参 | 直接修改——指纹必变，正常放行重训（白名单外新文件不影响指纹，也不会被执行） |
| 输入无变化但需重训 | dispatch 勾选 `force_retrain` |
| 新增语料版本 | 发布 `corpus-vN` Release（含 manifest）→ dispatch 传 `corpus_tag` |
| 首版模型 | 无历史 Release → `NO_BASELINE` 放行，版本自动 0.0.0 → 0.1.0 |
| 显式定版 | `model_version` 传纯数字 semver；对应 tag 已存在秒拒（FT-05 fail-fast） |
| 获取模型产物（运行时） | [how-to.md §13](./how-to.md#13-获取新闻情绪模型产物finbert-gguf-v-release)（`tokenizer.json` 不随 Release 分发，从 HF 基座取） |
| 运行时集成 / 展示边界 | [ai-service.md](../patterns/ai-service.md)；仅 App 内新闻情绪展示（ADR-0008），不进入选股 / 策略 / 执行路径 |

## 7. 排障

- **路由拒绝（exit 1）**：run 日志含 `::error` 输入无变化说明与分量快照 / diff——确认是否有意重训（是则 `force_retrain`）；
- **exit 2**：指纹分量缺失 / 不可提取 / gh 基础设施故障——按日志定位缺分量；
- **双 Gate 失败**：一致性 Gate 看 `verify.json`（分词一致 / CLS 余弦 / 概率差 <0.02 / 标签一致率 ≥99% 哪条判据挂）；效果 Gate 看 `effect_gate.json`（`reasons` + 锚点 tag；锚点不可解析为 fail-closed）；
- **排障 artifact**（`if: always()`，无论 Gate 成败）：`train_summary.json` / `verify.json` / `effect_gate.json` / `corpus_manifest_report.json` / `stage_a_out/fingerprint.json`；发布 artifact 仅 Gate 通过后存在（五文件，与排障 artifact 命名互不混用）；
- **发布 job 失败**：sha256 复验（artifact 传输完整性）或同 tag 已存在（FT-05）——后者按错误信息指引处理。
