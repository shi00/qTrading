"""Tests for scripts/verify_finbert_gguf.py（N0-3 GGUF 一致性门禁）。

覆盖：
- 纯函数：语料构造 / softmax / 余弦 / 最大绝对差 / 标签一致率 / 参考头 / 分词比较 / 耗时汇总；
- 判据阈值边界（含 f16 缺省跳过分支）；
- `run_gate` + `main` 端到端接线（monkeypatch 掉 torch / llama_cpp 重型链路）。

重型链路（torch / transformers / llama_cpp）在单测中一律不走真实模型：CI 无
torch，且真实模型文件体积大。

脚本模块经 ``sys.path.insert(ROOT / "scripts")`` 导入（与 tests/unit/scripts 同惯例）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_finbert_gguf as vfg  # noqa: E402


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def test_build_probe_corpus_default_is_200_deterministic() -> None:
    """默认语料为 200 条且可复现。"""
    first = vfg.build_probe_corpus()
    second = vfg.build_probe_corpus()
    assert len(first) == 200
    assert first == second
    assert all(isinstance(item, str) and item for item in first)


def test_build_probe_corpus_rejects_out_of_range() -> None:
    """n_titles 越界（<=0 或超池容量）必须报错，不得静默截断。"""
    with pytest.raises(ValueError, match="n_titles 必须为正整数"):
        vfg.build_probe_corpus(0)
    with pytest.raises(ValueError, match="超出确定性语料池容量"):
        vfg.build_probe_corpus(10**6)


def test_softmax_rows_sum_to_one_and_stable() -> None:
    """softmax 行和为 1，且大 logits 不下溢。"""
    logits = np.array([[1.0, 2.0, 3.0], [1000.0, 1000.0, 1000.0]], dtype=np.float64)
    probs = vfg.softmax(logits)
    np.testing.assert_allclose(probs.sum(axis=1), np.ones(2), rtol=1e-12)
    np.testing.assert_allclose(probs[1], np.full(3, 1 / 3), rtol=1e-12)


def test_row_cosine_identity_and_opposite() -> None:
    """相同向量余弦为 1，反向为 -1。"""
    a = np.array([[1.0, 0.0], [0.0, 2.0]], dtype=np.float32)
    np.testing.assert_allclose(vfg.row_cosine(a, a), np.ones(2), rtol=1e-6)
    np.testing.assert_allclose(vfg.row_cosine(a, -a), -np.ones(2), rtol=1e-6)


def test_row_cosine_rejects_bad_inputs() -> None:
    """形状不一致或零范数行必须报错。"""
    with pytest.raises(ValueError, match="shape 不一致"):
        vfg.row_cosine(np.ones((2, 3)), np.ones((2, 4)))
    with pytest.raises(ValueError, match="存在零范数行"):
        vfg.row_cosine(np.zeros((1, 3)), np.ones((1, 3)))


def test_max_abs_diff_and_agreement_rate() -> None:
    """最大绝对差与标签一致率取值正确，非法输入报错。"""
    assert vfg.max_abs_diff(np.array([0.0, 1.0]), np.array([0.1, 0.9])) == pytest.approx(0.1)
    assert vfg.agreement_rate(np.array([0, 1, 2, 2]), np.array([0, 1, 1, 2])) == pytest.approx(0.75)
    with pytest.raises(ValueError, match="shape 不一致"):
        vfg.agreement_rate(np.array([0, 1]), np.array([0]))
    with pytest.raises(ValueError, match="空序列无一致率"):
        vfg.agreement_rate(np.array([], dtype=int), np.array([], dtype=int))


def test_max_relative_l2_error_value_and_guards() -> None:
    """逐行相对 L2 最大误差取值正确，shape 不一致 / 零范数行报错。"""
    reference = np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float64)
    candidate = np.array([[3.0, 4.0], [0.0, 1.0]], dtype=np.float64)
    # 第 0 行无偏差；第 1 行 ||diff||=1, ||ref||=2 -> 0.5
    assert vfg.max_relative_l2_error(reference, candidate) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="shape 不一致"):
        vfg.max_relative_l2_error(reference, np.zeros((1, 2), dtype=np.float64))
    with pytest.raises(ValueError, match="参考侧存在零范数行"):
        vfg.max_relative_l2_error(np.zeros((1, 2), dtype=np.float64), np.zeros((1, 2), dtype=np.float64))


def test_build_reference_head_deterministic_and_shaped() -> None:
    """参考头同种子可复现，形状 3×768，行范数等于 gain。"""
    weight_a, bias_a = vfg.build_reference_head(seed=7, gain=3.0)
    weight_b, _ = vfg.build_reference_head(seed=7, gain=3.0)
    assert weight_a.shape == (3, 768)
    assert bias_a.shape == (3,)
    np.testing.assert_array_equal(weight_a, weight_b)
    np.testing.assert_allclose(np.linalg.norm(weight_a, axis=1), np.full(3, 3.0), rtol=1e-6)


def test_apply_head_returns_probabilities() -> None:
    """参考头输出为概率分布。"""
    weight, bias = vfg.build_reference_head()
    features = np.random.default_rng(1).standard_normal((5, 768)).astype(np.float32)
    probs = vfg.apply_head(features, weight, bias)
    assert probs.shape == (5, 3)
    assert np.all(probs >= 0.0) and np.all(probs <= 1.0)
    # float32 softmax 求和存在 ~1e-7 舍入误差，容差取 1e-6
    np.testing.assert_allclose(probs.sum(axis=1), np.ones(5), atol=1e-6)


def test_apply_head_with_pre_applies_tanh() -> None:
    """非线性头：先 tanh(features @ pw.T + pb)，再线性 + softmax。"""
    rng = np.random.default_rng(11)
    features = rng.standard_normal((4, 768)).astype(np.float32)
    weight, bias = vfg.build_reference_head()
    pw = rng.standard_normal((768, 768)).astype(np.float32)
    pb = rng.standard_normal(768).astype(np.float32)
    probs = vfg.apply_head(features, weight, bias, pre=(pw, pb))
    expected = vfg.softmax(np.tanh(features @ pw.T + pb) @ weight.T + bias)
    np.testing.assert_allclose(probs, expected, rtol=1e-6)


def test_load_head_npz_linear_and_shape_guard(tmp_path: Path) -> None:
    """linear 头载入返回 pre=None，形状不符须拒绝加载。"""
    path = tmp_path / "head.npz"
    np.savez(path, mode="linear", labels=np.array(["利空", "中性", "利好"]), cw=np.zeros((3, 768)), cb=np.zeros(3))
    weight, bias, pre = vfg.load_head_npz(path)
    assert weight.shape == (3, 768)
    assert bias.shape == (3,)
    assert pre is None

    bad = tmp_path / "bad.npz"
    np.savez(bad, mode="linear", labels=np.array(["a", "b", "c"]), cw=np.zeros((3, 10)), cb=np.zeros(3))
    with pytest.raises(ValueError, match="head.npz 形状不符"):
        vfg.load_head_npz(bad)


def test_load_head_npz_nonlinear_returns_pre(tmp_path: Path) -> None:
    """非线性头载入返回 (pw, pb) 预处理对。"""
    path = tmp_path / "head.npz"
    np.savez(
        path,
        mode="mlp",
        labels=np.array(["利空", "中性", "利好"]),
        cw=np.zeros((3, 768)),
        cb=np.zeros(3),
        pw=np.zeros((768, 768)),
        pb=np.zeros(768),
    )
    weight, bias, pre = vfg.load_head_npz(path)
    assert weight.shape == (3, 768)
    assert pre is not None
    assert pre[0].shape == (768, 768)
    assert pre[1].shape == (768,)


def test_head_scale_sensitivity_rows() -> None:
    """灵敏度扫描逐点输出：标签一致率与增益无关，概率差为正且随增益整体放大（非严格单调）。"""
    pt = np.random.default_rng(5).standard_normal((12, 768)).astype(np.float32)
    gguf = pt + np.random.default_rng(6).standard_normal((12, 768)).astype(np.float32) * 1e-3
    rows = vfg.head_scale_sensitivity(3, pt, gguf)
    assert [row["gain"] for row in rows] == list(vfg.SENSITIVITY_GAINS)
    agreements = {row["label_agreement"] for row in rows}
    assert len(agreements) == 1  # 尺度不敏感
    diffs = [row["probability_max_abs_diff"] for row in rows]
    assert all(np.isfinite(d) and d >= 0.0 for d in diffs)
    # 最大增益处的概率差应不小于最小增益处（整体放大趋势）
    assert diffs[-1] >= diffs[0]


def test_compare_token_ids_match_and_mismatch() -> None:
    """分词比较正确识别一致 / 不一致，并给出样例（最多 5 条）。"""
    hf = [[101, 1, 102]] * 6
    same = vfg.compare_token_ids(hf, [list(x) for x in hf])
    assert same["match"] is True
    assert same["match_rate"] == pytest.approx(1.0)

    llama = [list(x) for x in hf]
    for idx in range(6):
        llama[idx] = [101, 99, 102]
    diff = vfg.compare_token_ids(hf, llama)
    assert diff["match"] is False
    assert diff["matched"] == 0
    assert diff["match_rate"] == pytest.approx(0.0)
    assert len(diff["mismatch_examples"]) == 5


def test_compare_token_ids_rejects_length_mismatch() -> None:
    """两侧样本数不一致须报错。"""
    with pytest.raises(ValueError, match="两侧样本数不一致"):
        vfg.compare_token_ids([[101, 102]], [])


def test_summarize_latency() -> None:
    """耗时汇总空输入返回 None 值，非空返回统计量。"""
    empty = vfg.summarize_latency([])
    assert empty["count"] == 0
    assert empty["median_ms"] is None

    summary = vfg.summarize_latency([10.0, 20.0, 30.0])
    assert summary["count"] == 3
    assert summary["median_ms"] == pytest.approx(20.0)
    assert summary["min_ms"] == pytest.approx(10.0)
    assert summary["within_expected_band"] is True

    slow = vfg.summarize_latency([100.0])
    assert slow["within_expected_band"] is False


# --------------------------------------------------------------------------- #
# 判据阈值
# --------------------------------------------------------------------------- #
def _criteria_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tokenizer_consistent": True,
        "tokenizer_detail": "ok",
        "cos_f16": 0.9995,
        "cos_q8": 0.996,
        "prob_diff_f16": 0.01,
        "prob_diff_q8": 0.01,
        "label_rate_f16": 1.0,
        "label_rate_q8": 1.0,
        "embedding_dim": 768,
    }
    base.update(overrides)
    return base


def test_evaluate_criteria_all_pass() -> None:
    """全项达标时门禁通过。"""
    criteria = vfg.evaluate_criteria(**_criteria_kwargs())
    assert vfg.is_gate_passed(criteria) is True
    assert criteria["cls_cosine_f16"]["ok"] is True


def test_evaluate_criteria_threshold_boundaries() -> None:
    """阈值边界：余弦须严格大于、概率差须严格小于、标签一致率可达等号。"""
    at_cos = vfg.evaluate_criteria(**_criteria_kwargs(cos_q8=vfg.COSINE_MIN_Q8))
    assert at_cos["cls_cosine_q8"]["ok"] is False

    at_prob = vfg.evaluate_criteria(**_criteria_kwargs(prob_diff_q8=vfg.PROBABILITY_MAX_ABS_DIFF))
    assert at_prob["probability_max_abs_diff_q8"]["ok"] is False

    at_label = vfg.evaluate_criteria(**_criteria_kwargs(label_rate_q8=vfg.LABEL_AGREEMENT_MIN))
    assert at_label["label_agreement_q8"]["ok"] is True


def test_evaluate_criteria_dimension_and_tokenizer() -> None:
    """嵌入维度必须为 768；分词兜底后视为一致。"""
    bad_dim = vfg.evaluate_criteria(**_criteria_kwargs(embedding_dim=512))
    assert bad_dim["embedding_dim_768"]["ok"] is False

    bad_tok = vfg.evaluate_criteria(**_criteria_kwargs(tokenizer_consistent=False))
    assert bad_tok["tokenizer_ids_consistent"]["ok"] is False


def test_evaluate_criteria_f16_skipped_when_absent() -> None:
    """f16 缺省时相关判据标记 skipped，且不影响整体通过判定。"""
    criteria = vfg.evaluate_criteria(**_criteria_kwargs(cos_f16=None, prob_diff_f16=None, label_rate_f16=None))
    assert criteria["cls_cosine_f16"] == {
        "ok": None,
        "value": None,
        "threshold": vfg.COSINE_MIN_F16,
        "skipped": True,
    }
    assert vfg.is_gate_passed(criteria) is True


def test_is_gate_passed_detects_failure() -> None:
    """任一已评估判据失败即门禁失败。"""
    criteria = vfg.evaluate_criteria(**_criteria_kwargs(cos_q8=0.1))
    assert vfg.is_gate_passed(criteria) is False


# --------------------------------------------------------------------------- #
# 端到端接线（monkeypatch 重型链路）
# --------------------------------------------------------------------------- #
class _FakeLlama:
    def __init__(self, dim: int = vfg.EMBEDDING_DIM) -> None:
        self._dim = dim
        self.n_batch = vfg.DEFAULT_N_CTX
        self.closed = False

    def n_embd(self) -> int:
        return self._dim

    def close(self) -> None:
        self.closed = True


def _patch_heavy(monkeypatch: pytest.MonkeyPatch, *, direct_match: bool, dim: int = 768) -> np.ndarray:
    """拦掉所有重型依赖，返回被复用的 PyTorch CLS 矩阵。"""
    n = 6
    hf_ids = [[101, 500 + i, 102] for i in range(n)]
    cls = np.random.default_rng(3).standard_normal((n, dim)).astype(np.float32)

    monkeypatch.setattr(vfg, "_assert_gguf_file", lambda path: None)
    monkeypatch.setattr(vfg, "_load_hf_reference", lambda reference_model: (object(), object()))
    monkeypatch.setattr(vfg, "_hf_input_ids", lambda tokenizer, texts, max_length: hf_ids)
    monkeypatch.setattr(vfg, "_pytorch_cls", lambda model, tokenizer, texts, max_length: cls)
    monkeypatch.setattr(vfg, "_load_gguf", lambda path, n_threads, n_ctx: (_FakeLlama(dim), object()))
    monkeypatch.setattr(
        vfg,
        "_gguf_tokenize_internal",
        lambda llm, texts: [list(x) for x in hf_ids] if direct_match else [[101, 7, 102] for _ in hf_ids],
    )
    monkeypatch.setattr(vfg, "_gguf_cls_via_hf_ids", lambda llm, mod, ids, timing_sink=None: cls)
    return cls


def test_run_gate_passes_with_direct_tokenizer_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """直连分词一致时走直连路径，门禁 PASS。"""
    _patch_heavy(monkeypatch, direct_match=True)
    args = vfg._parse_args(["--n-titles", "6", "--q8-gguf", "q8.gguf", "--f16-gguf", "f16.gguf"])
    report = vfg.run_gate(args)
    assert report["tokenizer"]["direct_match"] is True
    assert report["tokenizer"]["fallback_used"] is False
    assert report["embedding"]["dim"] == 768
    assert report["verdict"] == "PASS"
    assert report["criteria"]["tokenizer_ids_consistent"]["ok"] is True


def test_run_gate_engages_fallback_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """直连分词不一致时自动兜底，token 判据经兜底成立。"""
    _patch_heavy(monkeypatch, direct_match=False)
    args = vfg._parse_args(["--n-titles", "6", "--q8-gguf", "q8.gguf"])
    report = vfg.run_gate(args)
    assert report["tokenizer"]["direct_match"] is False
    assert report["tokenizer"]["fallback_used"] is True
    assert report["criteria"]["tokenizer_ids_consistent"]["ok"] is True
    assert report["verdict"] == "PASS"
    assert report["criteria"]["cls_cosine_f16"]["skipped"] is True


def test_main_writes_report_and_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main 落盘报告并按 verdict 返回退出码。"""
    _patch_heavy(monkeypatch, direct_match=True)
    report_path = tmp_path / "report.json"
    code = vfg.main(["--n-titles", "6", "--q8-gguf", "q8.gguf", "--report", str(report_path)])
    assert code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"
    assert payload["head"]["kind"] == "deterministic-reference"
    assert payload["inputs"]["corpus"].startswith("synthetic-deterministic")


def test_main_returns_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """判据失败时 main 返回 1。"""
    cls = _patch_heavy(monkeypatch, direct_match=True)
    # 让 GGUF 侧与 PyTorch 侧完全正交，余弦极低
    monkeypatch.setattr(vfg, "_gguf_cls_via_hf_ids", lambda llm, mod, ids, timing_sink=None: -cls)
    code = vfg.main(["--n-titles", "6", "--q8-gguf", "q8.gguf", "--report", str(tmp_path / "r.json")])
    assert code == 1
