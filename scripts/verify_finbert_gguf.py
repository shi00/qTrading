"""FinBERT2-base GGUF 一致性门禁（M0 Gate，模型发布前手动执行，不进 CI）。

对比「PyTorch 原模型（HF transformers）」与「GGUF + llama-cpp-python」两条推理链路，
判定 Plan N0-3 的四项判据（正本见 `reviews/新闻达标模型微调.md` §7）：

1. 分词：HF tokenizer ids vs ``Llama.tokenize()`` 完全一致（含首尾 ``[CLS]`` / ``[SEP]``）；
2. CLS 余弦相似度：f16 > 0.999；Q8_0 > 0.995；
3. 三类概率最大绝对差 < 0.02；
4. 预测标签一致率 ≥ 99%。

同时实测 ``Llama.embed()`` CLS 池化返回维度（须 = 768，验证非 RANK 越界读取）与
单条耗时基线（预期 10 ~ 30 ms / 条，CPU + Q8_0 + 标题长度）。

**分词兜底（§7 / §8.3）**：llama.cpp 的 WordPiece 实现对「HF 基线词表 + 追加金融词」
（``len(tokenizer) = 34932``）的贪心切分与 HF 不一致；一旦第 1 项不达标，本脚本自动
切到兜底路径——用 HF ``tokenizers`` 生成 ids，经 ``llama_cpp.LlamaBatch.add_sequence`` +
``llama_decode`` + ``llama_get_embeddings_seq`` 直接取 CLS 向量（此时 ``n_embd`` 读取长度
是正确的），再重验第 2~4 项。生产打分子进程（N3-2）必须沿用该兜底路径，不得使用
``Llama.embed(text)`` 的内部分词。

**确定性参考头约定（重要）**：本 Gate 处于 M0 阶段，业务分类头要到 N3-1 才产出
（``head.npz``）。因此第 3 / 4 项默认使用一个**固定种子的确定性参考头**（linear 模式，
行向量为单位向量、增益 1.0（不做任意缩放）、零偏置），同一权重同时作用于 PyTorch 侧
与 GGUF 侧的**原始 CLS 特征**，使两侧概率在**同一映射**下可比。N3-1 产出 ``head.npz``
后，用 ``--head`` 指向该文件即可改用训练好的头（格式见 ``reviews/新闻达标模型微调.md``
§8.2：``mode`` / ``labels`` / ``cw`` / ``cb``，非线性另有 ``pw`` / ``pb``）。

> 概率差判据对参考头尺度敏感（近似线性正相关），标签一致率对尺度不敏感。为便于
> 复核，报告同时给出与头无关的原始偏差指标 ``cls_max_abs_diff`` / ``cls_relative_l2_max``
> 与参考头增益灵敏度扫描 ``head_scale_sensitivity``。本 Gate 结论仅证明「两条链路数值
> 等价」；正式发布前须用训练好的 ``head.npz`` 重跑本脚本（``--head``）。

**输入路径默认值**：``--reference-model`` / ``--q8-gguf`` 默认取仓库内 ``ai_models/``
（该目录已被 ``.gitignore`` 忽略）；``--f16-gguf`` 默认取环境变量 ``FINBERT_F16_GGUF``
（f16 对照件通常落在仓库外），未提供时跳过 f16 相关判据。

Usage:
    python scripts/verify_finbert_gguf.py \
        --reference-model ai_models/FinBERT2-base \
        --q8-gguf ai_models/finbert2-news-q8_0.gguf \
        --f16-gguf "$FINBERT_F16_GGUF" \
        --head ai_models/head.npz \
        --report ai_models/finbert2-news-q8_0.verify.json

（``--head`` 可省略：省略时使用确定性参考头；正式发布须提供训练好的 ``head.npz``。）

退出码：门禁全部通过为 0，任一判据失败为 1。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import numpy as np

# --- 判据常量（正本：reviews/新闻达标模型微调.md §7） ---
EMBEDDING_DIM = 768
NUM_CLASSES = 3
LABELS = ("利空", "中性", "利好")

COSINE_MIN_F16 = 0.999
COSINE_MIN_Q8 = 0.995
PROBABILITY_MAX_ABS_DIFF = 0.02
LABEL_AGREEMENT_MIN = 0.99

LATENCY_EXPECTED_BAND_MS = (10.0, 30.0)

# 参考头增益灵敏度扫描点（仅用于披露第 3 项判据对头尺度的依赖，不参与主判定）
SENSITIVITY_GAINS = (1.0, 2.0, 4.0, 6.0, 8.0)

DEFAULT_N_TITLES = 200
DEFAULT_SEED = 20261008
DEFAULT_MAX_LENGTH = 64
DEFAULT_HEAD_GAIN = 1.0
DEFAULT_N_THREADS = 6
DEFAULT_N_CTX = 512

_GGUF_MAGIC = b"GGUF"

# 探针语料：25 个标的 × 8 类事件 = 200 条确定性标题（不含随机性，可复现）。
# M0 阶段尚无 M1 语料库（N1-x 才产出），故使用确定性合成标题覆盖中文分词、
# 【】标点、英文（万科A / AI）、百分号与长数字等边界；不声称其为真实新闻语料。
_CORPUS_ENTITIES = (
    "【贵州茅台】",
    "【宁德时代】",
    "【比亚迪】",
    "【中国平安】",
    "【招商银行】",
    "【万科A】",
    "【隆基绿能】",
    "【中芯国际】",
    "【紫金矿业】",
    "【五粮液】",
    "【美的集团】",
    "【长江电力】",
    "【恒瑞医药】",
    "【立讯精密】",
    "【京东方A】",
    "【三一重工】",
    "【牧原股份】",
    "【东方财富】",
    "【海康威视】",
    "【药明康德】",
    "【中信证券】",
    "【上汽集团】",
    "【中国中免】",
    "【北方稀土】",
    "【工业富联】",
)
_CORPUS_EVENTS = (
    "公告拟回购股份不超过30亿元",
    "前三季度净利润同比下降45%",
    "获国家大基金二期增持",
    "关于召开2026年临时股东大会的通知",
    "发布2026年半年度业绩预告",
    "股东计划减持不超过2%股份",
    "中标重大项目金额约15.7亿元",
    "收到交易所关注函",
)
_CORPUS_MACRO = (
    "【市场】央行宣布下调存款准备金率0.5个百分点",
    "【市场】沪指收涨1.2%两市成交额突破1.5万亿元",
    "【行业】工信部发布AI芯片产业支持政策",
)


def _import_optional(module_name: str) -> Any:
    """延迟导入重型/可选依赖（torch / transformers / llama_cpp）。

    以 ``importlib`` 而非顶层 ``import``：这些依赖不在项目运行时依赖表内
    （torch / transformers 仅离线训练与导出使用），未安装时不应影响本模块被
    其它工具导入（间接保护 ``pyright`` 对未安装包的解析）。
    """
    return importlib.import_module(module_name)


# --------------------------------------------------------------------------- #
# 纯函数（可脱离 torch / llama_cpp 单测）
# --------------------------------------------------------------------------- #
def build_probe_corpus(n_titles: int = DEFAULT_N_TITLES) -> list[str]:
    """构造确定性探针语料（默认 200 条标题）。

    先在「标的 × 事件」笛卡尔积上顺序展开，不足 ``n_titles`` 时再补宏观标题；
    超出时截断。同一 ``n_titles`` 必然得到同一列表（无随机源），供发布记录复现。
    """
    pool = [entity + event for entity in _CORPUS_ENTITIES for event in _CORPUS_EVENTS]
    pool.extend(_CORPUS_MACRO)
    if n_titles <= 0:
        raise ValueError("n_titles 必须为正整数")
    if n_titles > len(pool):
        raise ValueError(f"n_titles={n_titles} 超出确定性语料池容量 {len(pool)}")
    return pool[:n_titles]


def softmax(logits: np.ndarray) -> np.ndarray:
    """按行做数值稳定 softmax。"""
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def row_cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """逐行余弦相似度（同形状 (N, D)）。"""
    if a.shape != b.shape:
        raise ValueError(f"shape 不一致: {a.shape} vs {b.shape}")
    a_norm = np.linalg.norm(a, axis=1)
    b_norm = np.linalg.norm(b, axis=1)
    denom = a_norm * b_norm
    if np.any(denom == 0):
        raise ValueError("存在零范数行，余弦相似度未定义")
    return np.sum(a * b, axis=1) / denom


def max_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """逐元素最大绝对差。"""
    return float(np.max(np.abs(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64))))


def agreement_rate(a: np.ndarray, b: np.ndarray) -> float:
    """两个等长标签序列的一致率。"""
    a_arr = np.asarray(a)
    b_arr = np.asarray(b)
    if a_arr.shape != b_arr.shape:
        raise ValueError(f"shape 不一致: {a_arr.shape} vs {b_arr.shape}")
    if a_arr.size == 0:
        raise ValueError("空序列无一致率")
    return float(np.mean(a_arr == b_arr))


def max_relative_l2_error(reference: np.ndarray, candidate: np.ndarray) -> float:
    """逐行相对 L2 误差最大值：max_i ||ref_i - cand_i|| / ||ref_i||（与分类头无关）。"""
    if reference.shape != candidate.shape:
        raise ValueError(f"shape 不一致: {reference.shape} vs {candidate.shape}")
    ref_norm = np.linalg.norm(reference, axis=1)
    if np.any(ref_norm == 0):
        raise ValueError("参考侧存在零范数行，相对误差未定义")
    return float(np.max(np.linalg.norm(reference - candidate, axis=1) / ref_norm))


def build_reference_head(
    dim: int = EMBEDDING_DIM,
    n_classes: int = NUM_CLASSES,
    seed: int = DEFAULT_SEED,
    gain: float = DEFAULT_HEAD_GAIN,
) -> tuple[np.ndarray, np.ndarray]:
    """构造确定性参考分类头（linear 模式）。

    行向量为固定种子生成的单位向量并乘以 ``gain``；偏置为 0。两侧（PyTorch /
    GGUF）使用**同一份**权重，使概率对比有定义。
    """
    rng = np.random.default_rng(seed)
    weight = rng.standard_normal((n_classes, dim)).astype(np.float32)
    weight /= np.linalg.norm(weight, axis=1, keepdims=True)
    bias = np.zeros(n_classes, dtype=np.float32)
    return (weight * np.float32(gain)).astype(np.float32), bias


def apply_head(
    features: np.ndarray,
    weight: np.ndarray,
    bias: np.ndarray,
    pre: tuple[np.ndarray, np.ndarray] | None = None,
) -> np.ndarray:
    """分类头：``softmax(features @ W.T + b)``；``pre`` 非空时先做 ``tanh(features @ pw.T + pb)``。

    与 ``reviews/新闻达标模型微调.md`` §8.2 的 ``mode="linear"`` / 非线性两形态一致。
    """
    x = features if pre is None else np.tanh(features @ pre[0].T + pre[1])
    return softmax(x @ weight.T + bias)


def load_head_npz(path: Path) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray] | None]:
    """载入 N3-1 产出的 ``head.npz``（格式正本见 ``reviews/新闻达标模型微调.md`` §8.2）。

    返回 ``(weight, bias, pre)``：``linear`` 模式 ``pre=None``；非线性模式 ``pre=(pw, pb)``。
    形状不符时拒绝加载（与 §8.2 的「不能静默降级」一致）。
    """
    data = np.load(path)
    mode = str(data["mode"])
    weight = np.asarray(data["cw"], dtype=np.float32)
    bias = np.asarray(data["cb"], dtype=np.float32)
    if weight.shape != (NUM_CLASSES, EMBEDDING_DIM) or bias.shape != (NUM_CLASSES,):
        raise ValueError(f"head.npz 形状不符: cw={weight.shape} cb={bias.shape}（期望 3×768 / 3）")
    if mode == "linear":
        return weight, bias, None
    pre = (
        np.asarray(data["pw"], dtype=np.float32),
        np.asarray(data["pb"], dtype=np.float32),
    )
    return weight, bias, pre


def head_scale_sensitivity(
    seed: int,
    pytorch_cls: np.ndarray,
    gguf_cls: np.ndarray,
    gains: tuple[float, ...] = SENSITIVITY_GAINS,
) -> list[dict[str, Any]]:
    """确定性参考头在不同增益下的概率差 / 标签一致率（透明披露第 3 项判据对头尺度的敏感性）。"""
    rows: list[dict[str, Any]] = []
    for gain in gains:
        weight, bias = build_reference_head(seed=seed, gain=gain)
        probs_pt = apply_head(pytorch_cls, weight, bias)
        probs_gguf = apply_head(gguf_cls, weight, bias)
        rows.append(
            {
                "gain": gain,
                "probability_max_abs_diff": max_abs_diff(probs_pt, probs_gguf),
                "label_agreement": agreement_rate(np.argmax(probs_pt, axis=1), np.argmax(probs_gguf, axis=1)),
            }
        )
    return rows


def compare_token_ids(
    hf_ids: list[list[int]],
    llama_ids: list[list[int]],
) -> dict[str, Any]:
    """比较 HF 与 llama.cpp 的分词 id 序列，返回一致率与不匹配样例。"""
    if len(hf_ids) != len(llama_ids):
        raise ValueError("两侧样本数不一致")
    mismatches: list[dict[str, Any]] = []
    for index, (hf, llama) in enumerate(zip(hf_ids, llama_ids, strict=True)):
        if list(hf) != list(llama):
            mismatches.append(
                {
                    "index": index,
                    "hf_len": len(hf),
                    "llama_len": len(llama),
                    "hf_head": list(hf[:12]),
                    "llama_head": list(llama[:12]),
                }
            )
    total = len(hf_ids)
    matched = total - len(mismatches)
    return {
        "match": not mismatches,
        "match_rate": matched / total if total else 0.0,
        "matched": matched,
        "total": total,
        "mismatch_examples": mismatches[:5],
    }


def summarize_latency(samples_ms: list[float]) -> dict[str, Any]:
    """单条耗时基线汇总（预期 10 ~ 30 ms，属观测基线、非硬门禁）。"""
    if not samples_ms:
        return {
            "count": 0,
            "min_ms": None,
            "median_ms": None,
            "mean_ms": None,
            "p95_ms": None,
            "within_expected_band": None,
        }
    arr = np.asarray(samples_ms, dtype=np.float64)
    low, high = LATENCY_EXPECTED_BAND_MS
    return {
        "count": int(arr.size),
        "min_ms": float(arr.min()),
        "median_ms": float(np.median(arr)),
        "mean_ms": float(arr.mean()),
        "p95_ms": float(np.percentile(arr, 95)),
        "expected_band_ms": [low, high],
        "within_expected_band": bool(low <= float(np.median(arr)) <= high),
    }


def evaluate_criteria(
    *,
    tokenizer_consistent: bool,
    tokenizer_detail: str,
    cos_f16: float | None,
    cos_q8: float,
    prob_diff_f16: float | None,
    prob_diff_q8: float,
    label_rate_f16: float | None,
    label_rate_q8: float,
    embedding_dim: int,
) -> dict[str, dict[str, Any]]:
    """按 §7 阈值判定各判据（纯函数，供单测覆盖边界）。"""
    criteria: dict[str, dict[str, Any]] = {
        "tokenizer_ids_consistent": {
            "ok": bool(tokenizer_consistent),
            "value": bool(tokenizer_consistent),
            "threshold": True,
            "detail": tokenizer_detail,
        },
        "embedding_dim_768": {
            "ok": embedding_dim == EMBEDDING_DIM,
            "value": embedding_dim,
            "threshold": EMBEDDING_DIM,
        },
        "cls_cosine_q8": {
            "ok": cos_q8 > COSINE_MIN_Q8,
            "value": cos_q8,
            "threshold": COSINE_MIN_Q8,
        },
        "probability_max_abs_diff_q8": {
            "ok": prob_diff_q8 < PROBABILITY_MAX_ABS_DIFF,
            "value": prob_diff_q8,
            "threshold": PROBABILITY_MAX_ABS_DIFF,
        },
        "label_agreement_q8": {
            "ok": label_rate_q8 >= LABEL_AGREEMENT_MIN,
            "value": label_rate_q8,
            "threshold": LABEL_AGREEMENT_MIN,
        },
    }
    optional = {
        "cls_cosine_f16": (cos_f16, COSINE_MIN_F16, ">"),
        "probability_max_abs_diff_f16": (prob_diff_f16, PROBABILITY_MAX_ABS_DIFF, "<"),
        "label_agreement_f16": (label_rate_f16, LABEL_AGREEMENT_MIN, ">="),
    }
    for name, (value, threshold, op) in optional.items():
        if value is None:
            criteria[name] = {"ok": None, "value": None, "threshold": threshold, "skipped": True}
            continue
        if op == ">":
            ok = value > threshold
        elif op == "<":
            ok = value < threshold
        else:
            ok = value >= threshold
        criteria[name] = {"ok": bool(ok), "value": value, "threshold": threshold}
    return criteria


def is_gate_passed(criteria: dict[str, dict[str, Any]]) -> bool:
    """所有**已评估**判据通过则门禁通过（skipped 项不参与判定）。"""
    return all(item["ok"] is not False for item in criteria.values())


# --------------------------------------------------------------------------- #
# 重型链路（延迟导入 torch / transformers / llama_cpp）
# --------------------------------------------------------------------------- #
def _load_hf_reference(reference_model: Path) -> tuple[Any, Any]:
    """加载 HF 参考编码器与分词器（仅编码器，忽略 MLM / 分类头）。"""
    transformers = _import_optional("transformers")
    tokenizer = transformers.AutoTokenizer.from_pretrained(str(reference_model))
    model = transformers.AutoModel.from_pretrained(str(reference_model))
    model.eval()
    return tokenizer, model


def _hf_input_ids(tokenizer: Any, texts: list[str], max_length: int) -> list[list[int]]:
    """HF 分词 ids（含特殊 token，与训练/导出同口径截断）。"""
    encoded = tokenizer(texts, truncation=True, max_length=max_length)
    return [[int(token_id) for token_id in ids] for ids in encoded["input_ids"]]


def _pytorch_cls(model: Any, tokenizer: Any, texts: list[str], max_length: int) -> np.ndarray:
    """PyTorch 参考侧 CLS 向量（BertModel.last_hidden_state[:, 0, :]）。"""
    torch = _import_optional("torch")
    encoded = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=max_length)
    with torch.no_grad():
        outputs = model(**encoded)
    cls = outputs.last_hidden_state[:, 0, :]
    return cls.to(torch.float32).cpu().numpy()


def _load_gguf(gguf_path: Path, n_threads: int, n_ctx: int) -> tuple[Any, Any]:
    """加载 GGUF 编码器（CLS 池化 embedding 模式）与 llama_cpp 模块句柄。"""
    llama_cpp = _import_optional("llama_cpp")
    llm = llama_cpp.Llama(
        model_path=str(gguf_path),
        embedding=True,
        pooling_type=llama_cpp.LLAMA_POOLING_TYPE_CLS,
        n_ctx=n_ctx,
        n_batch=n_ctx,
        n_ubatch=n_ctx,
        n_threads=n_threads,
        verbose=False,
    )
    return llm, llama_cpp


def _gguf_tokenize_internal(llm: Any, texts: list[str]) -> list[list[int]]:
    """llama.cpp 内置分词器（直接对比用；与 HF 不一致时触发兜底）。"""
    return [[int(token_id) for token_id in llm.tokenize(text.encode("utf-8"))] for text in texts]


def _gguf_cls_via_hf_ids(
    llm: Any,
    llama_cpp: Any,
    ids_list: list[list[int]],
    timing_sink: list[float] | None = None,
) -> np.ndarray:
    """兜底路径（§8.3）：HF ids → llama_batch → llama_decode → CLS 向量。

    直接复用 llama.cpp 的 ``LlamaBatch`` / ``LlamaContext`` 与
    ``llama_get_embeddings_seq``（此时按 ``n_embd`` 读取是正确的）。
    """
    n_embd = int(llm.n_embd())
    n_batch = int(llm.n_batch)
    vectors: list[np.ndarray] = []
    for ids in ids_list:
        if not ids:
            raise ValueError("空 token 序列无法取 CLS 向量")
        if len(ids) > n_batch:
            raise ValueError(f"token 数 {len(ids)} 超过 n_batch={n_batch}")
        started = time.perf_counter()
        llm._ctx.kv_cache_clear()
        llm._batch.reset()
        llm._batch.add_sequence(ids, 0, True)
        llm._ctx.decode(llm._batch)
        ptr = llama_cpp.llama_get_embeddings_seq(llm._ctx.ctx, 0)
        vectors.append(np.asarray(ptr[:n_embd], dtype=np.float32))
        llm._batch.reset()
        if timing_sink is not None:
            timing_sink.append((time.perf_counter() - started) * 1000.0)
    return np.vstack(vectors)


def _assert_gguf_file(path: Path) -> None:
    """轻量校验 GGUF 魔数（与 services/local_model_manager._validate_model_file 同口径）。"""
    if not path.is_file():
        raise FileNotFoundError(f"GGUF 文件不存在: {path}")
    with path.open("rb") as handle:
        if handle.read(4) != _GGUF_MAGIC:
            raise ValueError(f"非 GGUF 文件（魔数不匹配）: {path}")


def _verify_one_backend(
    *,
    gguf_path: Path,
    name: str,
    hf_ids: list[list[int]],
    pytorch_cls: np.ndarray,
    reference_head: tuple[np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray] | None],
    seed: int,
    n_threads: int,
    n_ctx: int,
    measure_latency: bool,
) -> dict[str, Any]:
    """对单个 GGUF（f16 / Q8_0）执行兜底 CLS 取向量与四项数值对比。"""
    _assert_gguf_file(gguf_path)
    llm, llama_cpp = _load_gguf(gguf_path, n_threads=n_threads, n_ctx=n_ctx)
    try:
        dim = int(llm.n_embd())
        timings: list[float] | None = [] if measure_latency else None
        gguf_cls = _gguf_cls_via_hf_ids(llm, llama_cpp, hf_ids, timing_sink=timings)
        cosines = row_cosine(pytorch_cls, gguf_cls)
        weight, bias, pre = reference_head
        probs_pt = apply_head(pytorch_cls, weight, bias, pre)
        probs_gguf = apply_head(gguf_cls, weight, bias, pre)
        return {
            "gguf": str(gguf_path),
            "name": name,
            "embedding_dim": dim,
            "cosine_min": float(cosines.min()),
            "cosine_mean": float(cosines.mean()),
            "cls_max_abs_diff": max_abs_diff(pytorch_cls, gguf_cls),
            "cls_relative_l2_max": max_relative_l2_error(pytorch_cls, gguf_cls),
            "probability_max_abs_diff": max_abs_diff(probs_pt, probs_gguf),
            "label_agreement": agreement_rate(np.argmax(probs_pt, axis=1), np.argmax(probs_gguf, axis=1)),
            "head_scale_sensitivity": head_scale_sensitivity(seed, pytorch_cls, gguf_cls),
            "latency_ms": summarize_latency(timings) if timings is not None else None,
        }
    finally:
        llm.close()


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    """执行完整门禁并返回结构化报告。"""
    texts = build_probe_corpus(args.n_titles)
    if args.head:
        reference_head = load_head_npz(Path(args.head))
        head_meta: dict[str, Any] = {
            "kind": "trained-npz",
            "path": str(args.head),
            "note": "N3-1 产出的训练好的分类头；第 3 / 4 项直接使用该头。",
        }
    else:
        weight, bias = build_reference_head(seed=args.seed, gain=args.head_gain)
        reference_head = (weight, bias, None)
        head_meta = {
            "kind": "deterministic-reference",
            "mode": "linear",
            "seed": args.seed,
            "gain": args.head_gain,
            "note": "M0 阶段无训练好的 head.npz（N3-1 产出）；同一固定头同时作用于两侧 CLS。"
            "正式发布前须用训练好的 head.npz 重跑本门禁（--head）。",
        }
    tokenizer, model = _load_hf_reference(Path(args.reference_model))
    hf_ids = _hf_input_ids(tokenizer, texts, args.max_length)
    pytorch_cls = _pytorch_cls(model, tokenizer, texts, args.max_length)

    # 第 1 项：直连分词一致性（llama.cpp 内置分词器 vs HF）
    q8_path = Path(args.q8_gguf)
    _assert_gguf_file(q8_path)
    probe_llm, _probe_mod = _load_gguf(q8_path, n_threads=args.n_threads, n_ctx=args.n_ctx)
    try:
        direct = compare_token_ids(hf_ids, _gguf_tokenize_internal(probe_llm, texts))
    finally:
        probe_llm.close()

    fallback_used = not direct["match"] or args.tokenizer_mode == "fallback"
    tokenizer_detail = (
        "HF ids 经 llama_batch 直送（§7/§8.3 兜底），两侧输入 token 完全一致"
        if fallback_used
        else "llama.cpp 内置分词器与 HF ids 完全一致"
    )

    backends: dict[str, Any] = {}
    if args.f16_gguf:
        backends["f16"] = _verify_one_backend(
            gguf_path=Path(args.f16_gguf),
            name="f16",
            hf_ids=hf_ids,
            pytorch_cls=pytorch_cls,
            reference_head=reference_head,
            seed=args.seed,
            n_threads=args.n_threads,
            n_ctx=args.n_ctx,
            measure_latency=False,
        )
    backends["q8_0"] = _verify_one_backend(
        gguf_path=q8_path,
        name="q8_0",
        hf_ids=hf_ids,
        pytorch_cls=pytorch_cls,
        reference_head=reference_head,
        seed=args.seed,
        n_threads=args.n_threads,
        n_ctx=args.n_ctx,
        measure_latency=True,
    )

    q8 = backends["q8_0"]
    f16 = backends.get("f16")
    criteria = evaluate_criteria(
        tokenizer_consistent=fallback_used or direct["match"],
        tokenizer_detail=tokenizer_detail,
        cos_f16=None if f16 is None else f16["cosine_min"],
        cos_q8=q8["cosine_min"],
        prob_diff_f16=None if f16 is None else f16["probability_max_abs_diff"],
        prob_diff_q8=q8["probability_max_abs_diff"],
        label_rate_f16=None if f16 is None else f16["label_agreement"],
        label_rate_q8=q8["label_agreement"],
        embedding_dim=q8["embedding_dim"],
    )

    return {
        "schema": "finbert-gguf-verification.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "inputs": {
            "reference_model": str(args.reference_model),
            "f16_gguf": args.f16_gguf,
            "q8_gguf": str(q8_path),
            "n_titles": args.n_titles,
            "seed": args.seed,
            "max_length": args.max_length,
            "tokenizer_mode": args.tokenizer_mode,
            "corpus": "synthetic-deterministic (25 entities × 8 events; 宏观标题仅在 n_titles > 200 时追加)",
        },
        "head": head_meta,
        "embedding": {"dim": q8["embedding_dim"], "expected_dim": EMBEDDING_DIM},
        "tokenizer": {
            "direct_match": direct["match"],
            "direct_match_rate": direct["match_rate"],
            "matched": direct["matched"],
            "total": direct["total"],
            "fallback_used": fallback_used,
            "mismatch_examples": direct["mismatch_examples"],
        },
        "backends": backends,
        "criteria": criteria,
        "verdict": "PASS" if is_gate_passed(criteria) else "FAIL",
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 68)
    print("FinBERT2-base GGUF 一致性门禁（N0-3）")
    print("=" * 68)
    tokenizer = report["tokenizer"]
    print(
        f"[分词] 直连一致: {tokenizer['direct_match']} "
        f"(rate={tokenizer['direct_match_rate']:.4f}, {tokenizer['matched']}/{tokenizer['total']}) "
        f"-> 兜底路径: {tokenizer['fallback_used']}"
    )
    print(f"[嵌入] 维度: {report['embedding']['dim']} (期望 {report['embedding']['expected_dim']})")
    for name, backend in report["backends"].items():
        print(
            f"[{name}] CLS 余弦 min={backend['cosine_min']:.6f} "
            f"概率最大绝对差={backend['probability_max_abs_diff']:.6f} "
            f"标签一致率={backend['label_agreement']:.4f}"
        )
        print(
            f"       CLS 原始偏差 max|d|={backend['cls_max_abs_diff']:.6f} "
            f"相对L2最大={backend['cls_relative_l2_max']:.6f}（与分类头无关）"
        )
        sensitivity = backend.get("head_scale_sensitivity") or []
        if sensitivity:
            pairs = " ".join(f"g={row['gain']:g}:dP={row['probability_max_abs_diff']:.4f}" for row in sensitivity)
            print(f"       参考头增益灵敏度（第3项随尺度变化）: {pairs}")
        if backend["latency_ms"] and backend["latency_ms"]["count"]:
            latency = backend["latency_ms"]
            print(
                f"       单条耗时 min={latency['min_ms']:.2f}ms "
                f"median={latency['median_ms']:.2f}ms p95={latency['p95_ms']:.2f}ms "
                f"(预期 {latency['expected_band_ms'][0]}~{latency['expected_band_ms'][1]}ms)"
            )
    print("-" * 68)
    for name, item in report["criteria"].items():
        mark = "SKIP" if item["ok"] is None else ("PASS" if item["ok"] else "FAIL")
        print(f"  {mark}  {name}: value={item['value']} threshold={item['threshold']}")
    print("-" * 68)
    print(f"VERDICT: {report['verdict']}")
    print("=" * 68)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinBERT2-base GGUF 一致性门禁（N0-3）")
    parser.add_argument("--reference-model", default="ai_models/FinBERT2-base", help="HF 参考模型目录（仅取编码器）")
    parser.add_argument("--q8-gguf", default="ai_models/finbert2-news-q8_0.gguf", help="发布用 Q8_0 GGUF")
    parser.add_argument(
        "--f16-gguf", default=os.environ.get("FINBERT_F16_GGUF"), help="f16 对照 GGUF（缺省时跳过 f16 判据）"
    )
    parser.add_argument("--report", default="ai_models/finbert2-news-q8_0.verify.json", help="报告输出路径")
    parser.add_argument("--head", default=None, help="训练好的分类头 head.npz（N3-1 产出；提供时取代确定性参考头）")
    parser.add_argument("--n-titles", type=int, default=DEFAULT_N_TITLES, help="探针标题数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="确定性参考头种子")
    parser.add_argument("--head-gain", type=float, default=DEFAULT_HEAD_GAIN, help="参考头权重增益")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH, help="分词截断长度")
    parser.add_argument("--n-threads", type=int, default=DEFAULT_N_THREADS, help="llama.cpp 线程数")
    parser.add_argument("--n-ctx", type=int, default=DEFAULT_N_CTX, help="llama.cpp 上下文长度")
    parser.add_argument(
        "--tokenizer-mode",
        choices=("auto", "direct", "fallback"),
        default="auto",
        help="auto: 直连不一致时自动兜底；direct: 强制内置分词器；fallback: 强制 HF ids 直送",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = run_gate(args)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"报告已写入: {report_path}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
