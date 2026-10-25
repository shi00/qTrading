# ADR-0013: 新闻情绪打分推理运行时选型终局（保持 GGUF，ORT / OpenVINO 双 PoC FAIL）

> Status: Accepted
> Date: 2026-10-13
> Owner: 架构维护者
> Supersedes: 无（记录换轨动议的否决终局，不取代既有推理范式决策）

## Context

[ADR-0012](./0012-news-sentiment-inference-paradigm.md) 确立了新闻情绪打分的本地推理
范式：离线微调 FinBERT2-base → 编码器导出 `GGUF(Q8_0)` + 分类头 `head.npz` → 在线以
`llama-cpp-python` 常驻子进程推理。该范式存在一项 GGUF 特有复杂度：llama.cpp 内置 WPM
分词对「HF 基线词表 + 追加金融词」的贪心切分与 HF 不一致（N0-3 Gate 实测直连命中
0/200），生产打分路径**必须**使用 HF `tokenizers` 生成 ids → `LlamaBatch` → CLS
embedding 的外部分词兜底路径。

2026-10-13 用户动议换轨 ONNX Runtime：模型导出为单图 `.onnx`（encoder + pooler +
classifier 全并入计算图），预期收益为 ① 分词与训练天然同源，消除外部分词兜底复杂度；
② 产物三件套收敛为两件套（`head.npz` 消灭）；③ CI 微调产线工具链简化（无需 llama.cpp
convert/quantize 工具链版本对齐）。

换轨可行性验证采用 Phase G 硬 Gate 四判据（全过才换轨）：split_test 标签一致率 ≥99%；
人工验证集（实测 40 条，文件名历史沿用）argmax 不一致 ≤1 条；三类概率 max-abs 差
<0.02；单条推理 P95 ≤30ms（对齐 N0-3 GGUF 基线）。为此执行两轮 PoC：

**O0-1 ONNX Runtime PoC（2026-10-13，FAIL）**，报告留档
`ai_models/finbert2-sentiment-onnx-poc.verify.json`：

- 无单一 INT8 配置同时通过四判据：三类概率 max-abs 差最优 0.0282（weight-only
  b8/bl32/sym，超 0.02 阈值）、单条 P95 最优 64.81ms（动态 INT8 默认，超 30ms 阈值）；
  标签一致率（0.9991）与人工验证集（不一致 1 条）的最优值分属不同配置。
- fp32 单图质量完美（概率差 3.1e-6）但 P95 151.87ms，同样远超耗时判据——速度瓶颈
  非量化所致。
- 根因有二：① ORT 默认 CPU EP kernel 效率不足（同机 GGUF q8_0 mean 14.30ms，ORT INT8
  最快档 64.81ms，差距 4.5~28×）；② 动态 INT8 激活量化崩坏 attention probs（softmax
  后 0~1 值域被量化，per-tensor 权重粒度标签一致率仅 0.7958）；weight-only 变体质量最
  接近达标，但 MatMulNBits kernel 极慢（单条 mean 134~406ms）。

**O0-3 OpenVINO PoC（Gate-B，2026-10-13，FAIL）**，报告留档
`ai_models/finbert2-sentiment-openvino-poc.verify.json`（openvino 2026.4.1 +
NNCF 3.4.0）：

- INT8 三候选（NNCF PTQ PERFORMANCE / MIXED / weight-only）最优值：概率差 0.1196 FAIL
  （weight-only；PTQ 两 preset 0.90~0.97 崩坏）、单条 P95 56.22ms FAIL（MIXED，128
  token 主口径；64 token 口径最优 30.50ms 亦压线超）；FP32 IR 质量无损（概率差 1.4e-3）
  但 P95 156.22ms。
- 双根因与 ORT 同构、相互独立：① PTQ 激活量化破坏 attention softmax（0~1 值域遭
  per-tensor minmax）；② oneDNN INT8 kernel 效率不足（对 fp32 加速比仅 2.78×；64
  token 口径 mean 27.02ms，仍慢于同机 GGUF q8_0 的 14.30ms）。

> 口径注记：两轮 PoC 为 128 token 真实新闻、单条 batch=1；同机 GGUF 基线 14.30ms 来自
> 既有 verify 门禁（64 token 合成语料，`finbert2-news-q8_0.verify.json`），O0-3 已补
> 64 token 同长度口径对照，FAIL 结论在两种口径下均成立（详见两份报告的口径声明）。

## Decision

### 1. 保持 GGUF 现状（零损失）

双 PoC 均未通过换轨判据，换轨动机不成立。继续沿用
[ADR-0012](./0012-news-sentiment-inference-paradigm.md) 的推理范式与既有 GGUF 产物
（已通过 N0-3 verify 门禁），`llama-cpp-python` 主依赖与外部分词兜底路径保持不动；
运行时换轨实施链（O1 系列）全部任务不启动。

### 2. 复评条件

仅当 ONNX Runtime / OpenVINO 的 CPU INT8 kernel 升级使单条推理 P95 **显著低于 30ms**
（对本机 GGUF 基线 14.30ms 具备量级竞争力）时，再启动换轨评估（独立 PoC Gate 重新
度量）。PoC 报告留档 `ai_models/finbert2-sentiment-onnx-poc.verify.json` 与
`ai_models/finbert2-sentiment-openvino-poc.verify.json`，作为复评的度量口径与基线正本。

### 3. 修订 ADR-0012「torch/transformers 不进 CI」条款

原条款「`torch` / `transformers` / `scikit-learn` 仅用于离线训练与导出（不进
`pyproject.toml` 运行时依赖、不进 CI）」修订为：**训练依赖（torch / transformers /
scikit-learn）仅限 `finbert-train.yml` 训练 job 内临时安装，不进 `pyproject.toml`
主依赖**。「不进运行时依赖」语义不变；「不进 CI」放松为「仅允许训练 job 内 job 级
临时安装，不进常驻依赖面」。
[ADR-0012](./0012-news-sentiment-inference-paradigm.md) 原条款处已加内联修订标注，
历史未重写。

### 4. CI 微调产线与推理运行时选型解耦

CI 微调产线与推理运行时选型解耦：无论未来运行时为何，产线均需落地。当前产物形态为
GGUF（`.gguf` + `head.npz`），GGUF 产线任务（脚本固化 / `finbert-train.yml` / 消费
对接）不受本选型终局阻塞，其训练依赖条款沿用本 ADR §3 修订后的语义。

### 5. 仅展示边界承袭（承接 ADR-0008）

新闻情绪打分**仅用于展示**（新闻流着色、情绪指数、新闻洞察面板），不进入选股排序、
策略打分或任何执行路径；本决策不改变 [ADR-0008](./0008-no-ai-execution.md) 边界。
若未来要让情绪分参与选股，须另立 ADR。

## Consequences

**正向**
- 避免了一次不达标的运行时迁移：双 PoC 证明换轨后质量（概率差）与延迟双 FAIL，保持
  GGUF 零损失，既有产物、verify 门禁与相关单测继续有效。
- 选型争议收敛：两轮 PoC 以同判据、同机对照（GGUF q8_0 mean 14.30ms）闭环，换轨否决
  有数据正本可溯，复评条件显式。
- CI 微调产线解耦落地：训练依赖条款修订后，产线可在不依赖推理运行时选型的前提下推进。

**负向 / 约束**
- GGUF 特有的外部分词兜底复杂度继续存在（HF `tokenizers` 产 ids → `LlamaBatch`），
  `head.npz` 双轨产物形态维持。
- 复评时点依赖上游引擎（ORT / OpenVINO）kernel 版本演进，本机（Tiger Lake，8 逻辑核）
  口径下的结论不外推至其他硬件。

## Alternatives

- **切换 ONNX Runtime（O0-1 路线）**：拒绝。无单一 INT8 配置同时通过概率差（最优
  0.0282 超 0.02）与单条 P95（最优 64.81ms 超 30ms）判据；fp32 质量完美但 P95
  151.87ms。
- **切换 OpenVINO oneDNN + NNCF PTQ（O0-3 路线）**：拒绝。概率差 0.1196 / P95 56.22ms
  双 FAIL，与 ORT 双根因同构（PTQ 激活量化崩坏 + INT8 kernel 效率不足）。
- **PTQ 部分算子排除（ignored_scope）继续调优**：拒绝。预期数值介于 PTQ 与
  weight-only 之间，无法同时满足概率差与耗时判据；两轮 PoC 已系统探索（PTQ 两 preset /
  weight-only / LATENCY hint / 线程口径）后收敛。
- **批量推理摊薄单条延迟**：不改变单条 P95 判据的结论，属吞吐基准另行立项，不在本
  决策范围。

---

## 完成判定（canonical 入口）

- 双 PoC FAIL 证据落盘（两份 verify.json），本 ADR 记录选型终局并被 CONTRIBUTING.md
  docs/adr/ 小节按 GDR-12 文件级登记，`check_docs_consistency.py` PASS
- ADR-0012 原条款处已加内联修订标注，原文保持可读，历史未重写

## Errata / 勘误

> 2026-10-25 核对

Context 与 Decision §2 引用的两份 PoC 留档（`ai_models/finbert2-sentiment-onnx-poc.verify.json`、`ai_models/finbert2-sentiment-openvino-poc.verify.json`）为**本地未入库产物**（`ai_models/` 目录不入版本控制），当前工作区已不存在（该目录仅保留模型权重）。复评时若需以本文的度量口径与基线为对照，必须重新运行 PoC 生成报告，不得假定留档仍在；判据阈值（概率差 <0.02、单条 P95 ≤30ms、标签一致率 ≥99%、人工验证集不一致 ≤1 条）与同机 GGUF 基线 14.30ms 仍以本文文字为准。

_最小验证命令：_ `python scripts/check_docs_consistency.py` + `python -m pytest tests/unit/test_docs_consistency.py`。
