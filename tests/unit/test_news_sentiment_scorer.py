# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import multiprocessing
import queue
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from services.news_sentiment_scorer import (
    _DEFAULT_MAX_LENGTH,
    _DEFAULT_N_CTX,
    _DEFAULT_N_THREADS,
    _EMBEDDING_DIM,
    _LABELS_REQUIRED,
    _NUM_CLASSES,
    _SENTINEL,
    NewsSentimentScorer,
    _chunked,
    _scoring_worker,
    _validate_score_request,
    apply_head_and_score,
    load_head_npz,
    softmax,
)

pytestmark = pytest.mark.unit

# Save real class references before any patching replaces them on the multiprocessing module
_RealProcess = multiprocessing.Process
_RealQueue = multiprocessing.Queue


# --------------------------------------------------------------------------- #
# 辅助构造
# --------------------------------------------------------------------------- #
def _config(enabled: bool = True, **overrides) -> dict:
    cfg = {
        "enabled": enabled,
        "model_path": "model.gguf",
        "head_path": "head.npz",
        "tokenizer_path": "tokenizer.json",
        "n_threads": 6,
        "max_length": 128,
        "timeout_s": 0.5,
    }
    cfg.update(overrides)
    return cfg


def _head(mode: str = "bert") -> dict:
    """构造合法 head dict（与 load_head_npz 返回结构一致）。"""
    rng = np.random.default_rng(7)
    cw = rng.standard_normal((3, _EMBEDDING_DIM)).astype(np.float32)
    cb = np.zeros(3, dtype=np.float32)
    head: dict = {"mode": mode, "labels": _LABELS_REQUIRED, "cw": cw, "cb": cb, "pre": None}
    if mode == "bert":
        pw = rng.standard_normal((_EMBEDDING_DIM, _EMBEDDING_DIM)).astype(np.float32)
        pb = np.zeros(_EMBEDDING_DIM, dtype=np.float32)
        head["pre"] = (pw, pb)
    return head


def _make_mgr(config: dict | None = None) -> NewsSentimentScorer:
    mgr = NewsSentimentScorer()
    mgr._config_reader = lambda: config if config is not None else _config()
    return mgr


def _write_npz(path, mode: str, *, cw_shape=(3, _EMBEDDING_DIM), labels=_LABELS_REQUIRED, nan=False) -> str:
    rng = np.random.default_rng(3)
    payload: dict = {
        "mode": np.array(mode),
        "labels": np.array(labels),
        "cw": rng.standard_normal(cw_shape).astype(np.float32),
        "cb": np.zeros(cw_shape[0], dtype=np.float32),
    }
    if nan:
        payload["cw"][0, 0] = np.nan
    if mode == "bert":
        payload["pw"] = rng.standard_normal((_EMBEDDING_DIM, _EMBEDDING_DIM)).astype(np.float32)
        payload["pb"] = np.zeros(_EMBEDDING_DIM, dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **payload)
    return str(path)


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
class TestSoftmax:
    def test_rows_sum_to_one(self):
        probs = softmax(np.array([[1.0, 2.0, 3.0], [-5.0, 0.0, 5.0]], dtype=np.float32))
        assert np.allclose(probs.sum(axis=1), 1.0)

    def test_numerical_stability_large_logits(self):
        probs = softmax(np.array([[1000.0, 1001.0, 1002.0]], dtype=np.float32))
        assert np.all(np.isfinite(probs))
        assert probs[0].argmax() == 2


class TestLoadHeadNpz:
    def test_linear_mode_roundtrip(self, tmp_path):
        path = _write_npz(tmp_path / "head.npz", "linear")
        head = load_head_npz(path)
        assert head["mode"] == "linear"
        assert head["pre"] is None
        assert head["labels"] == _LABELS_REQUIRED
        assert head["cw"].shape == (3, _EMBEDDING_DIM)

    def test_bert_mode_roundtrip(self, tmp_path):
        path = _write_npz(tmp_path / "head.npz", "bert")
        head = load_head_npz(path)
        assert head["mode"] == "bert"
        assert head["pre"] is not None
        assert head["pre"][0].shape == (_EMBEDDING_DIM, _EMBEDDING_DIM)

    def test_invalid_mode_rejected(self, tmp_path):
        path = _write_npz(tmp_path / "head.npz", "rank")
        with pytest.raises(ValueError, match="mode"):
            load_head_npz(path)

    def test_bad_cw_shape_rejected(self, tmp_path):
        path = _write_npz(tmp_path / "head.npz", "linear", cw_shape=(3, 128))
        with pytest.raises(ValueError, match="形状不符"):
            load_head_npz(path)

    def test_wrong_labels_order_rejected(self, tmp_path):
        # 标签顺序漂移会翻转 score 符号，必须拒绝
        path = _write_npz(tmp_path / "head.npz", "linear", labels=("利好", "中性", "利空"))
        with pytest.raises(ValueError, match="labels"):
            load_head_npz(path)

    def test_short_labels_rejected(self, tmp_path):
        path = _write_npz(tmp_path / "head.npz", "linear", labels=("利空", "中性"))
        with pytest.raises(ValueError, match="labels"):
            load_head_npz(path)

    def test_nan_weights_rejected(self, tmp_path):
        path = _write_npz(tmp_path / "head.npz", "linear", nan=True)
        with pytest.raises(ValueError, match="非有限"):
            load_head_npz(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError) as exc_info:
            load_head_npz(str(tmp_path / "missing.npz"))
        assert "missing.npz" in str(exc_info.value)

    def test_npz_without_pickle_payload(self, tmp_path):
        # allow_pickle=False：普通数组 npz 正常加载；pickle 载体被拒绝由 numpy 保证
        path = _write_npz(tmp_path / "head.npz", "linear")
        assert load_head_npz(path)["cb"].shape == (3,)


class TestApplyHeadAndScore:
    def test_linear_numeric(self):
        rng = np.random.default_rng(42)
        feats = rng.standard_normal((4, _EMBEDDING_DIM)).astype(np.float32)
        cw = rng.standard_normal((3, _EMBEDDING_DIM)).astype(np.float32)
        cb = rng.standard_normal(3).astype(np.float32)
        head = {"mode": "linear", "labels": _LABELS_REQUIRED, "cw": cw, "cb": cb, "pre": None}

        out = apply_head_and_score(feats, head)
        expected = softmax(feats @ cw.T + cb)
        assert len(out) == 4
        for i, row in enumerate(out):
            assert row is not None
            assert row["label"] == _LABELS_REQUIRED[int(np.argmax(expected[i]))]
            assert row["score"] == pytest.approx(float(expected[i][2] - expected[i][0]), rel=1e-5)
            assert row["confidence"] == pytest.approx(float(expected[i].max()), rel=1e-5)
            assert len(row["probs"]) == 3
            assert -1.0 <= row["score"] <= 1.0

    def test_bert_branch_applies_tanh_pre(self):
        rng = np.random.default_rng(42)
        feats = rng.standard_normal((3, _EMBEDDING_DIM)).astype(np.float32)
        pw = rng.standard_normal((_EMBEDDING_DIM, _EMBEDDING_DIM)).astype(np.float32)
        pb = rng.standard_normal(_EMBEDDING_DIM).astype(np.float32)
        cw = rng.standard_normal((3, _EMBEDDING_DIM)).astype(np.float32)
        cb = rng.standard_normal(3).astype(np.float32)
        head = {"mode": "bert", "labels": _LABELS_REQUIRED, "cw": cw, "cb": cb, "pre": (pw, pb)}

        out = apply_head_and_score(feats, head)
        expected = softmax(np.tanh(feats @ pw.T + pb) @ cw.T + cb)
        for i, row in enumerate(out):
            assert row is not None
            assert row["label"] == _LABELS_REQUIRED[int(np.argmax(expected[i]))]
            assert row["score"] == pytest.approx(float(expected[i][2] - expected[i][0]), rel=1e-5)

    def test_nan_feature_row_returns_none(self):
        # R21: 非有限特征 → None，不回填中性；正常行仍产出完整结构
        feats = np.zeros((3, _EMBEDDING_DIM), dtype=np.float32)
        feats[1] = np.nan
        head = _head("linear")
        out = apply_head_and_score(feats, head)
        assert out[1] is None
        for row in (out[0], out[2]):
            assert row is not None
            assert row["label"] in _LABELS_REQUIRED
            assert 0.0 <= row["confidence"] <= 1.0
            assert -1.0 <= row["score"] <= 1.0
            assert len(row["probs"]) == _NUM_CLASSES

    def test_inf_feature_row_returns_none(self):
        # Inf 特征 → softmax 产生 NaN 概率 → None（R21）
        feats = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        feats[0, 0] = np.inf
        out = apply_head_and_score(feats, _head("linear"))
        assert out == [None]

    def test_empty_batch(self):
        out = apply_head_and_score(np.zeros((0, _EMBEDDING_DIM), dtype=np.float32), _head())
        assert out == []

    def test_bad_feature_shape_raises(self):
        with pytest.raises(ValueError, match="特征形状"):
            apply_head_and_score(np.zeros((2, 128), dtype=np.float32), _head())


class TestValidateScoreRequest:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (("score", 0, ["a"]), True),
            (("score", 12345, ["a", "b"]), True),
            (("score", 1, []), True),
            (("chat", 1, ["a"]), False),
            (("score", "1", ["a"]), False),
            (("score", True, ["a"]), False),  # bool 是 int 子类，须拒绝
            (("score", 1, "a"), False),
            (("score", 1, ["a", 2]), False),
            (("score", 1), False),
            (("score", 1, ["a"], "extra"), False),
            ("not-a-tuple", False),
            (None, False),
        ],
    )
    def test_protocol(self, payload, expected):
        assert _validate_score_request(payload) is expected


class TestChunked:
    def test_empty(self):
        assert _chunked([], 32) == []

    def test_exact_split(self):
        assert [len(c) for c in _chunked(["x"] * 70, 32)] == [32, 32, 6]

    def test_smaller_than_chunk(self):
        assert _chunked(["a", "b"], 32) == [["a", "b"]]


# --------------------------------------------------------------------------- #
# 管理单例：降级路径（R21）
# --------------------------------------------------------------------------- #
class TestScoreTextsGuardClauses:
    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_list_even_if_disabled(self):
        mgr = _make_mgr(_config(enabled=False))
        assert await mgr.score_texts([]) == []

    @pytest.mark.asyncio
    async def test_not_enabled_returns_none(self):
        mgr = _make_mgr(_config(enabled=False))
        assert await mgr.score_texts(["利好公告"]) is None

    @pytest.mark.asyncio
    async def test_incomplete_paths_returns_none(self):
        mgr = _make_mgr(_config(head_path=""))
        assert await mgr.score_texts(["x"]) is None

    @pytest.mark.asyncio
    async def test_missing_optional_deps_returns_none(self):
        mgr = _make_mgr()
        with (
            patch("services.news_sentiment_scorer._HAS_LLAMA_CPP", False),
            patch("services.news_sentiment_scorer._HAS_TOKENIZERS", True),
        ):
            assert await mgr.score_texts(["x"]) is None

    @pytest.mark.asyncio
    async def test_invalid_model_file_returns_none(self):
        mgr = _make_mgr()
        with patch("services.news_sentiment_scorer._validate_model_file", return_value=False):
            assert await mgr.score_texts(["x"]) is None

    @pytest.mark.asyncio
    async def test_ensure_ready_failure_returns_none(self):
        mgr = _make_mgr()
        mgr._ensure_ready = AsyncMock(return_value=None)
        assert await mgr.score_texts(["x"]) is None

    @pytest.mark.asyncio
    async def test_chunk_failure_degrades_whole_batch(self):
        # 任一块失败 → 整体 None（调用方按「本次不可用」处理）
        mgr = _make_mgr()
        mgr._ensure_ready = AsyncMock(return_value=_head())

        async def fake_chunk(chunk):
            return None if len(chunk) == 32 else np.zeros((len(chunk), _EMBEDDING_DIM), dtype=np.float32)

        mgr._score_chunk = fake_chunk
        assert await mgr.score_texts(["x"] * 64) is None


class TestScoreTextsBatchSplitting:
    @pytest.mark.asyncio
    async def test_chunks_and_result_alignment(self):
        mgr = _make_mgr()
        mgr._ensure_ready = AsyncMock(return_value=_head("linear"))
        calls: list[list[str]] = []

        async def fake_chunk(chunk):
            calls.append(list(chunk))
            return np.zeros((len(chunk), _EMBEDDING_DIM), dtype=np.float32)

        mgr._score_chunk = fake_chunk
        texts = [f"text-{i}" for i in range(70)]
        out = await mgr.score_texts(texts)

        assert [c for c in calls] == _chunked(texts, 32)
        assert out is not None and len(out) == 70
        assert all(row is not None for row in out)


# --------------------------------------------------------------------------- #
# 管理单例：单块请求与响应配对
# --------------------------------------------------------------------------- #
def _install_queues(mgr: NewsSentimentScorer, responses: list) -> tuple[MagicMock, MagicMock]:
    req_q = MagicMock(spec=queue.Queue)
    res_q = MagicMock(spec=queue.Queue)
    if responses:
        res_q.get_nowait.side_effect = responses
    mgr._request_queue = req_q
    mgr._result_queue = res_q
    return req_q, res_q


class TestScoreChunk:
    @pytest.mark.asyncio
    async def test_ok_response_returns_vectors(self):
        mgr = _make_mgr(_config(timeout_s=2.0))
        vec = np.zeros((2, _EMBEDDING_DIM), dtype=np.float32)
        req_q, res_q = _install_queues(mgr, [("ok", 0, vec)])
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        out = await mgr._score_chunk(["a", "b"])

        assert out is vec
        req_q.put.assert_called_once_with(("score", 0, ["a", "b"]), timeout=5)

    @pytest.mark.asyncio
    async def test_mismatched_req_id_discarded_then_matched(self):
        # 响应配对：不匹配 req_id 的响应丢弃，继续等待自己的响应
        mgr = _make_mgr(_config(timeout_s=2.0))
        vec = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        _install_queues(mgr, [("ok", 999, vec), ("ok", 0, vec)])
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        out = await mgr._score_chunk(["a"])
        assert out is vec

    @pytest.mark.asyncio
    async def test_worker_error_terminates_and_returns_none(self):
        mgr = _make_mgr(_config(timeout_s=2.0))
        _install_queues(mgr, [("error", 0, "decode failed")])
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker") as sw:
            out = await mgr._score_chunk(["a"])
        assert out is None
        sw.assert_called_once_with()
        assert mgr._worker_ready is False

    @pytest.mark.asyncio
    async def test_timeout_terminates_and_returns_none(self):
        # DoD「超时」：轮询超时 → 终止 worker → None（下次调用重启）
        mgr = _make_mgr()
        _req_q, res_q = _install_queues(mgr, [])
        res_q.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        with (
            patch.object(mgr, "_resolve_timeout", return_value=0.05),  # 超时下限已单测，此处提速
            patch.object(mgr, "_shutdown_worker") as sw,
        ):
            out = await mgr._score_chunk(["a"])
        assert out is None
        sw.assert_called_once_with()
        assert mgr._worker_ready is False

    @pytest.mark.asyncio
    async def test_value_error_from_closed_queue_treated_as_empty(self):
        # C2：轮询中 ValueError（队列已关闭）与 Empty 同语义处理，不逃出轮询循环
        mgr = _make_mgr(_config(timeout_s=2.0))
        vec = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        _req_q, res_q = _install_queues(mgr, [])
        res_q.get_nowait.side_effect = [ValueError("Queue is closed"), ("ok", 0, vec)]
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        out = await mgr._score_chunk(["a"])
        assert out is vec

    @pytest.mark.asyncio
    async def test_worker_crash_returns_none(self):
        mgr = _make_mgr(_config(timeout_s=2.0))
        _req_q, res_q = _install_queues(mgr, [])
        res_q.get_nowait.side_effect = queue.Empty
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.return_value = False
        proc.exitcode = -11
        mgr._worker_proc = proc

        with patch.object(mgr, "_shutdown_worker") as sw:
            out = await mgr._score_chunk(["a"])
        assert out is None
        sw.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_crash_with_matching_residual_response_returns_vectors(self):
        # 崩溃前最后一次残留响应恰含匹配向量 → 仍可用
        mgr = _make_mgr(_config(timeout_s=2.0))
        vec = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        _req_q, res_q = _install_queues(mgr, [])
        res_q.get_nowait.side_effect = [queue.Empty, ("ok", 0, vec)]
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.return_value = False
        mgr._worker_proc = proc

        out = await mgr._score_chunk(["a"])
        assert out is vec


class TestConsumeResult:
    def test_ok_matching(self):
        vec = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        mgr = _make_mgr()
        assert mgr._consume_result(0, ("ok", 0, vec)) is vec

    def test_ok_mismatched_returns_none(self):
        vec = np.zeros((1, _EMBEDDING_DIM), dtype=np.float32)
        mgr = _make_mgr()
        assert mgr._consume_result(0, ("ok", 7, vec)) is None

    def test_ok_malformed_raises(self):
        mgr = _make_mgr()
        from services.news_sentiment_scorer import _WorkerError

        with pytest.raises(_WorkerError, match="Malformed ok"):
            mgr._consume_result(0, ("ok", 0))

    def test_ok_bad_vector_shape_raises(self):
        mgr = _make_mgr()
        from services.news_sentiment_scorer import _WorkerError

        with pytest.raises(_WorkerError, match="Malformed vectors"):
            mgr._consume_result(0, ("ok", 0, np.zeros((1, 3), dtype=np.float32)))

    def test_error_matching_raises(self):
        from services.news_sentiment_scorer import _WorkerError

        mgr = _make_mgr()
        with pytest.raises(_WorkerError, match="boom"):
            mgr._consume_result(0, ("error", 0, "boom"))

    def test_error_invalid_req_raises(self):
        # worker 协议校验失败用 rid=-1 广播 → 视为请求级失败
        from services.news_sentiment_scorer import _WorkerError

        mgr = _make_mgr()
        with pytest.raises(_WorkerError, match="bad format"):
            mgr._consume_result(0, ("error", -1, "bad format"))

    def test_error_mismatched_returns_none(self):
        mgr = _make_mgr()
        assert mgr._consume_result(0, ("error", 9, "boom")) is None

    def test_shutdown_raises(self):
        from services.news_sentiment_scorer import _WorkerError

        mgr = _make_mgr()
        with pytest.raises(_WorkerError, match="shut down"):
            mgr._consume_result(0, ("shutdown", "ok"))

    def test_unknown_status_raises(self):
        from services.news_sentiment_scorer import _WorkerError

        mgr = _make_mgr()
        with pytest.raises(_WorkerError, match="Unknown result status"):
            mgr._consume_result(0, ("weird",))


# --------------------------------------------------------------------------- #
# 管理单例：worker 生命周期
# --------------------------------------------------------------------------- #
class TestEnsureWorkerImpl:
    def test_reuses_ready_worker_for_same_model(self):
        mgr = _make_mgr()
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True
        mgr._worker_ready = True
        mgr._model_path = "model.gguf"
        mgr._core_signature = (_DEFAULT_N_THREADS, _DEFAULT_MAX_LENGTH)

        assert mgr._ensure_worker_impl("model.gguf", "tokenizer.json", {}) is True

    def test_restarts_when_model_path_changed(self):
        mgr = _make_mgr()
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True
        mgr._worker_ready = True
        mgr._model_path = "old.gguf"

        with (
            patch.object(mgr, "_shutdown_worker_locked"),
            patch("services.news_sentiment_scorer.multiprocessing.Process") as mock_proc_cls,
            patch("services.news_sentiment_scorer.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_queue_cls.side_effect = [MagicMock(spec=_RealQueue), MagicMock(spec=_RealQueue)]
            mock_proc = MagicMock(spec=_RealProcess)
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            assert mgr._ensure_worker_impl("new.gguf", "tokenizer.json", {}) is True
            mgr._shutdown_worker_locked.assert_called_once_with()
            args = (
                mock_proc_cls.call_args[1]["args"]
                if mock_proc_cls.call_args.kwargs.get("args")
                else mock_proc_cls.call_args[0][1]
            )
            assert args[0] == "new.gguf"
            assert args[1] == "tokenizer.json"

    def test_restarts_when_core_signature_changed(self):
        # S1 检视：n_threads / max_length 变更不重启则旧参数继续生效（模型已按
        # 旧参数构造），core_signature 参与短路条件后必须触发重启。
        mgr = _make_mgr()
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True
        mgr._worker_ready = True
        mgr._model_path = "model.gguf"
        mgr._core_signature = (6, 128)

        with (
            patch.object(mgr, "_shutdown_worker_locked") as sw,
            patch("services.news_sentiment_scorer.multiprocessing.Process") as mock_proc_cls,
            patch("services.news_sentiment_scorer.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_queue_cls.side_effect = [MagicMock(spec=_RealQueue), MagicMock(spec=_RealQueue)]
            mock_proc = MagicMock(spec=_RealProcess)
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            # 模型路径不变，仅核心参数变更 → 仍须重启
            assert mgr._ensure_worker_impl("model.gguf", "tokenizer.json", {"n_threads": 2, "max_length": 64}) is True
            sw.assert_called_once_with()
            assert mgr._core_signature == (2, 64)


class TestAwaitWorkerReady:
    @pytest.mark.asyncio
    async def test_ready(self):
        mgr = _make_mgr()
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._result_queue.get_nowait.return_value = ("ready", "model.gguf")
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        assert await mgr._await_worker_ready(timeout=1.0) is True
        assert mgr._worker_ready is True

    @pytest.mark.asyncio
    async def test_load_error_returns_false(self):
        mgr = _make_mgr()
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._result_queue.get_nowait.return_value = ("error", "model load failed")
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker") as sw:
            assert await mgr._await_worker_ready(timeout=1.0) is False
        sw.assert_called_once_with()
        assert mgr._worker_ready is False

    @pytest.mark.asyncio
    async def test_crash_returns_false(self):
        mgr = _make_mgr()
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.return_value = False
        proc.exitcode = -11
        mgr._worker_proc = proc

        with patch.object(mgr, "_shutdown_worker") as sw:
            assert await mgr._await_worker_ready(timeout=1.0) is False
        sw.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_no_result_queue_returns_false(self):
        mgr = _make_mgr()
        mgr._result_queue = None
        assert await mgr._await_worker_ready(timeout=1.0) is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self):
        mgr = _make_mgr()
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker") as sw:
            assert await mgr._await_worker_ready(timeout=0.05) is False
        sw.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_value_error_from_closed_queue_treated_as_empty(self):
        # 对抗性检视 C2：close()+join_thread() 后 get_nowait 实测抛 ValueError
        # （非 Empty/OSError），未捕获会以未分类异常逃出轮询循环。
        mgr = _make_mgr()
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._result_queue.get_nowait.side_effect = ValueError("Queue is closed")
        mgr._worker_proc = MagicMock(spec=_RealProcess)
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker") as sw:
            assert await mgr._await_worker_ready(timeout=0.05) is False
        sw.assert_called_once_with()


class TestShutdownWorker:
    def test_graceful_sends_sentinel(self):
        mgr = _make_mgr()
        req_q = MagicMock(spec=queue.Queue)
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.return_value = True
        mgr._request_queue = req_q
        mgr._worker_proc = proc
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._worker_ready = True

        mgr._shutdown_worker()

        req_q.put.assert_called_once_with(_SENTINEL, timeout=2.0)
        assert mgr._worker_proc is None
        assert mgr._worker_ready is False

    def test_graceful_terminates_if_join_times_out(self):
        mgr = _make_mgr()
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.side_effect = [True, True, False]
        mgr._request_queue = MagicMock(spec=queue.Queue)
        mgr._worker_proc = proc
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._worker_ready = True

        mgr._shutdown_worker()
        proc.terminate.assert_called_once_with()

    def test_graceful_kills_if_terminate_fails(self):
        mgr = _make_mgr()
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.side_effect = [True, True, True, False]
        mgr._request_queue = MagicMock(spec=queue.Queue)
        mgr._worker_proc = proc
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._worker_ready = True

        mgr._shutdown_worker()
        proc.kill.assert_called_once_with()

    def test_force_skips_sentinel(self):
        # N3-3 关机语义：force=True 直接 terminate（预算 1.5s + kill 0.5s）
        mgr = _make_mgr()
        req_q = MagicMock(spec=queue.Queue)
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.side_effect = [True, False]
        mgr._request_queue = req_q
        mgr._worker_proc = proc
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._worker_ready = True

        mgr._shutdown_worker(force=True)

        req_q.put.assert_not_called()
        proc.terminate.assert_called_once_with()
        proc.kill.assert_not_called()

    def test_force_kills_if_terminate_fails(self):
        mgr = _make_mgr()
        proc = MagicMock(spec=_RealProcess)
        proc.is_alive.side_effect = [True, True, False]
        mgr._request_queue = MagicMock(spec=queue.Queue)
        mgr._worker_proc = proc
        mgr._result_queue = MagicMock(spec=queue.Queue)
        mgr._worker_ready = True

        mgr._shutdown_worker(force=True)

        proc.terminate.assert_called_once_with()
        proc.kill.assert_called_once_with()

    def test_noop_if_no_proc(self):
        mgr = _make_mgr()
        mgr._worker_proc = None
        mgr._request_queue = None
        mgr._result_queue = None
        mgr._shutdown_worker()
        assert mgr._worker_proc is None
        assert mgr._worker_ready is False


# --------------------------------------------------------------------------- #
# 单例生命周期（R15 / R7）
# --------------------------------------------------------------------------- #
class TestSingletonLifecycle:
    @pytest.mark.asyncio
    async def test_get_instance_returns_same(self):
        a = await NewsSentimentScorer.get_instance()
        b = await NewsSentimentScorer.get_instance()
        assert a is b

    def test_atexit_cleanup_calls_shutdown(self):
        mgr = _make_mgr()
        with patch.object(mgr, "_shutdown_worker") as sw:
            NewsSentimentScorer._atexit_cleanup()
        sw.assert_called_once_with()

    def test_reset_singleton_clears_state(self):
        _make_mgr()
        NewsSentimentScorer._reset_singleton()
        assert NewsSentimentScorer._instance is None
        assert NewsSentimentScorer._initialized is False

    def test_reset_singleton_tolerates_shutdown_error(self):
        mgr = _make_mgr()
        with patch.object(mgr, "_shutdown_worker", side_effect=RuntimeError("boom")):
            NewsSentimentScorer._reset_singleton()  # 不应抛出
        assert NewsSentimentScorer._instance is None

    def test_worker_target_is_module_level_function(self):
        # spawn 子进程要求 target 可 pickle：模块级函数
        import inspect

        assert inspect.isfunction(_scoring_worker)
        assert _scoring_worker.__module__ == "services.news_sentiment_scorer"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
class TestConfig:
    def test_read_config_defaults_do_not_raise(self):
        # 未配置任何键时返回全默认值（N3-6 接入设置页前的默认态）
        cfg = NewsSentimentScorer._read_config()
        assert cfg["enabled"] is False
        assert cfg["model_path"] == ""
        assert cfg["n_threads"] == 6
        assert cfg["max_length"] == 128
        assert cfg["timeout_s"] == 30.0

    def test_core_config_clamps(self):
        mgr = _make_mgr(_config(n_threads=0, max_length=-4))
        core = mgr._core_config()
        assert core["n_threads"] == 1
        assert core["max_length"] == 8

    def test_core_config_max_length_capped_by_n_ctx(self):
        # C4 检视：max_length 超过 n_ctx 容量会使 add_sequence 反复溢出 →
        # worker 崩溃杀重启循环，上限 clamp 至 _DEFAULT_N_CTX - 8。
        mgr = _make_mgr(_config(max_length=9999))
        assert mgr._core_config()["max_length"] == _DEFAULT_N_CTX - 8

    def test_resolve_timeout_clamps_to_minimum(self):
        # C3 检视：timeout_s ≤ 0 会使轮询立即到期退化为忙抖动，下限 clamp 至 1s
        assert _make_mgr(_config(timeout_s=0.0))._resolve_timeout() == 1.0
        assert _make_mgr(_config(timeout_s=-5.0))._resolve_timeout() == 1.0
        assert _make_mgr(_config(timeout_s=2.5))._resolve_timeout() == 2.5


class TestScoreTextsConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_score_texts_serialize_on_loop_lock(self):
        # C1 检视：同 loop 并发 score_texts 共享一对队列，若不整程串行会互吞
        # 对方的响应（req_id 不匹配被丢弃 → 双双假超时降级）。锁覆盖 ensure +
        # chunk 循环全程，任一时刻至多一个 score_texts 处于请求-响应中。
        mgr = _make_mgr()
        mgr._ensure_ready = AsyncMock(return_value=_head("linear"))
        active = 0
        max_active = 0

        async def fake_chunk(chunk):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return np.zeros((len(chunk), _EMBEDDING_DIM), dtype=np.float32)

        mgr._score_chunk = fake_chunk

        out = await asyncio.gather(mgr.score_texts(["a"]), mgr.score_texts(["b"]))
        assert max_active == 1
        assert all(r is not None and len(r) == 1 for r in out)
