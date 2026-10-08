# ADR-0012: 新闻情绪打分的本地推理范式（CLS embedding + numpy 分类头）

> Status: Accepted
> Date: 2026-10-08
> Owner: 架构维护者
> Supersedes: 无（新增推理形态，无旧 ADR 对应）

## Context

项目现有 AI 能力集中在 LLM 文本分类（`services/ai_service/news_classifier.py`：
以 JSON 输出 `category_L1/L2` 与 `sentiment`，失败兜底 `neutral`）。为获得更稳定、
可离线运行的 A 股新闻情绪分，方案（`reviews/新闻达标模型微调.md`）新增一条链路：
离线微调 FinBERT2-base → 导出编码器 `GGUF(Q8_0)` + 分类头 `head.npz` → 在线用已锁定的
`llama-cpp-python` 推理，产出三分类概率 `[利空, 中性, 利好]`。

这引入了一种**新的推理形态**（encoder + 分类头）与一个**新的常驻子进程**，属于跨层范式
变更，须按 [ADR-0001](./0001-record-architecture-decisions.md) 记录「为什么这么做」。四项
待决选择：① 如何取句向量（RANK 分类头 vs CLS 池化 embedding）；② 如何产出三分类概率；
③ 是否需要独立常驻子进程；④ 输出是否进入执行路径（与 [ADR-0008](./0008-no-ai-execution.md)
的边界关系）。

M0 技术验证（N0-3 Gate，实跑 PASS）已提供决策依据：`llama-cpp-python` 0.3.35 下
`Llama.embed()` CLS 池化返回维度实测 = 768；单条耗时 9.5 ~ 16.8 ms（CPU + Q8_0）；
定制词表（标准中文 BERT 21128 + 13809 自定义金融词 = 34932）与 llama.cpp 内置 WPM 分词
不一致（直连命中 0/200）。

## Decision

### 1. CLS 池化 embedding 模式 + numpy 分类头，不使用 RANK 模式

`llama-cpp-python` 0.3.35 底层绑定虽已包含 `LLAMA_POOLING_TYPE_RANK` /
`llama_model_n_cls_out()` / `llama_model_cls_label()`（内置 llama.cpp 支持
`BertForSequenceClassification` 分类头），但高层 `Llama.embed()` 在非 NONE 池化下**固定按
`n_embd` 读取**（`ptr[:n_embd]`）——RANK 模式下实际只有 `n_cls_out` 个有效值，会越界读取。

故采用 `LLAMA_POOLING_TYPE_CLS` 取 768 维 CLS 向量，分类头以 numpy 实现
（linear 或 `tanh` 预激活），`softmax` 产出三分类概率；`head.npz` 载入后校验形状
（`cw` 为 `3×768`、`labels` 长度 3），不符即拒绝加载，不静默降级；非有限输出（NaN/Inf）
返回 `None`，由调用方按缺失处理（R21），不回填中性值。

### 2. 分词采用外部分词兜底路径

N0-3 Gate 实测：llama.cpp 内置 WPM 分词对本模型「HF 基线词表 + 追加金融词」的贪心切分与
HF 不一致（直连命中 0/200）。故生产打分路径**必须**用 HF `tokenizers` 生成 ids，经
`llama_cpp.LlamaBatch.add_sequence` + `llama_decode` +
`llama_get_embeddings_seq(ctx, i)[:n_embd]` 取 CLS 向量；禁用 `Llama.embed(text)` 的
内部自动分词。

### 3. 独立常驻子进程（与对话 LLM 进程分离）

打分在**独立常驻子进程**中运行，与对话 LLM 子进程分离：`embedding=True` 需独立的
`Llama` 实例，且避免与生成任务争抢算力。协议对齐既有
`services/local_model_manager.py::_persistent_worker`（请求 `("score", [texts])`，
响应 `("ok", [results])` / `("error", msg)`，`_SENTINEL` 优雅退出），新协议自带独立的
尺寸/类型校验，不复用既有 4-tuple 校验。`llama_cpp` 经 `importlib` 延迟导入（可选依赖，
未安装不影响 App 启动）。管理该子进程的服务落在 `services/` 层（R1），满足
R2（`CancelledError` 重抛）/ R7 / R11（loop-local 原语）/ R15（单例登记）/ R16（阻塞操作
经 `ThreadPoolManager`）/ R21，并按 `utils/shutdown.py` 的固定步骤表接入停机清理。

### 4. 仅展示边界（承接 ADR-0008）

模型输出**仅用于展示**（新闻流着色、情绪指数、新闻洞察面板），不进入选股排序、策略打分
或任何执行路径；实现时以单测断言选股 / 策略模块不读取 `sentiment_score`。若未来要让情绪分
参与选股，须另立 ADR。模型未配置或加载失败时，`sentiment` 回退到既有 LLM 结果，两者皆无
则为 `None`，UI 不做关键词猜测、按中性样式不着色（R21）。

## Consequences

**正向**
- 推理形态与依赖边界清晰：运行时只依赖已锁定的 `llama-cpp-python` + numpy，
  `torch` / `transformers` / `scikit-learn` 仅用于离线训练与导出（不进 `pyproject.toml`
  运行时依赖、不进 CI）。
- 规避 RANK 模式的 `Llama.embed()` 越界读取风险，CLS 维度经 Gate 实测确认为 768。
- 与对话 LLM 进程隔离，互不争抢；子进程沿用既有 `_persistent_worker` 范式，停机路径可控。
- 展示边界与 ADR-0008 一致，避免模型输出悄悄影响执行路径。

**负向 / 约束**
- 新增一个常驻子进程与一份 numpy 分类头实现，增加维护面；分类头的正确性由 N3-1 训练产物
  与发布前 Gate（`scripts/verify_finbert_gguf.py --head`）保证。
- 分词兜底路径使在线推理依赖 HF `tokenizers` 产出的 ids，不能用 `Llama.embed(text)` 捷径。
- 情绪分的绝对校准程度取决于训练数据与标注质量（属 M1/M2 范畴），本 ADR 只约束推理范式，
  不承诺精度。

## Alternatives

- **RANK 模式（直接用 llama.cpp 内置分类头）**：拒绝。底层虽支持，但高层 `Llama.embed()`
  在非 NONE 池化下按 `n_embd` 读取会造成越界读取（决策依据见「Decision §1」）。
- **复用对话 LLM 子进程承载打分**：拒绝。`embedding=True` 与生成任务语义互斥且争抢算力，
  无法在同一 `Llama` 实例上共存。
- **本地关键词着色兜底**：拒绝。R21 明确禁止「用本地关键词猜测」替代缺失的业务语义。
- **让情绪分进入选股 / 策略打分**：拒绝（超出本决策范围）。如需，须另立 ADR 并重新评估
  与 ADR-0008 的边界。

---

## 完成判定（canonical 入口）

- 满足 ADR-0001 触发条件（新增跨层推理范式与常驻子进程）时已新增 ADR，未悄悄改代码
- ADR 按模板填写 Context/Decision/Consequences/Alternatives，link 与 supersede 链完整
- 被 CONTRIBUTING.md 的 docs/adr/ 小节按 GDR-12 文件级登记，且被 `check_docs_consistency.py` 接受

_最小验证命令：_ `python scripts/check_docs_consistency.py` + `python -m pytest tests/unit/test_docs_consistency.py`。