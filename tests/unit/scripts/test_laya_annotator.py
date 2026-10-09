"""本地 laya-multilingual 零样本标注器 + 人工抽检 Gate 单测（N2-1）。

覆盖（正本：`reviews/新闻达标模型微调.md` §4.4 路线 2 / §4.5 质控）：
choice 问题构造、不可信输出处理（extract_sentiment）、批量推理内核（predict_batch）、
抽检集抽样（sample_review_set）、人工抽检 Gate 评估（evaluate_gate，含「利空→中性」
漏判率）、CLI 冒烟。

测试约定：不在测试中真实加载 laya 模型（离线依赖、CPU 推理秒级）；agent 一律以
注入替身代替；``extract_sentiment`` 直接验证不可信输入处理（R21）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import laya_annotator  # noqa: E402 - sys.path 注入后导入
from data.external.news_sources.corpus import CorpusStore  # noqa: E402 - sys.path 注入后导入

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

LABELS = ("利好", "中性", "利空")


class _FakeAgent:
    """laya Agent 替身：按预置答案返回 choice 结果；可注入异常。"""

    def __init__(self, answers: Sequence[dict[str, Any] | Exception | None]) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[Any, Any]] = []

    def predict(self, state: Any, questions: Any) -> dict[str, Any]:
        item = self._answers.pop(0)
        self.calls.append((state, questions))
        if isinstance(item, Exception):
            raise item
        return {"answers": {"sentiment": item}}


def _answer(label: str, probs: dict[str, float]) -> dict[str, Any]:
    """构造 laya choice answer dict（PoC 实测形状）。

    非法 label（如「上涨」）在 probabilities 中可能无对应键，此时 answer_confidence
    用固定 0.9（测试只关心 extract 的 label 校验分支，不关心该值）。
    """
    return {
        "type": "choice",
        "choice": label,
        "probabilities": probs,
        "confidence": 0.6781,
        "answer_confidence": probs.get(label, 0.9),
        "action": {"act_probability": 1.0},
    }


def _pred(label: str, *, source_id: str = "id-0", source: str = "sina_7x24") -> dict[str, Any]:
    return {"source": source, "source_id": source_id, "text": "t", "label": label, "confidence": 0.9}


def _gold(label: str, *, source_id: str = "id-0", source: str = "sina_7x24") -> dict[str, Any]:
    return {"source": source, "source_id": source_id, "text": "t", "label": label}


# ------------------------------------------------- 1. choice 问题构造


def test_build_choice_question_contains_three_enum_criteria():
    question = laya_annotator.build_choice_question()
    assert question["sentiment"]["type"] == "choice"
    criteria = question["sentiment"]["criteria"]
    assert set(criteria) == set(LABELS)
    assert len(question["sentiment"]["instructions"]) > 0


def test_build_choice_question_instructions_cover_verdict_caliber():
    instructions = laya_annotator.build_choice_question()["sentiment"]["instructions"]
    # 判定口径：对该股票影响的倾向，而非文字语气；例行/不明确 → 中性
    assert "股票" in instructions
    assert "中性" in instructions
    assert "语气" in instructions


# ------------------------------------------------- 2. 不可信输出处理


@pytest.mark.parametrize(
    ("label", "probs", "expected"),
    [
        ("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}, ("利好", 0.7)),
        ("中性", {"利好": 0.1, "中性": 0.6, "利空": 0.3}, ("中性", 0.6)),
        ("利空", {"利好": 0.05, "中性": 0.05, "利空": 0.9}, ("利空", 0.9)),
    ],
)
def test_extract_valid_choice(label: str, probs: dict[str, float], expected: tuple[str, float]):
    assert laya_annotator.extract_sentiment(_answer(label, probs)) == expected


@pytest.mark.parametrize(
    "bad_answer",
    [
        _answer("上涨", {"利好": 0.7, "中性": 0.2, "利空": 0.1}),  # label 非三枚举
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}) | {"answer_confidence": float("nan")},
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}) | {"answer_confidence": float("inf")},
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}) | {"answer_confidence": -0.1},
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}) | {"answer_confidence": 1.5},
        {"type": "choice", "choice": "利好"},  # 缺 answer_confidence
        None,
    ],
)
def test_extract_rejects_untrusted_choice(bad_answer: dict[str, Any] | None):
    assert laya_annotator.extract_sentiment(bad_answer) is None


def test_extract_ignores_bool_confidence():
    # bool 不是合理置信度（laya.confidence 亦按此处理）
    answer = _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}) | {"answer_confidence": True}
    assert laya_annotator.extract_sentiment(answer) is None


def test_extract_uses_answer_confidence_not_entropy_confidence():
    # PoC 实测：answer_confidence = max(p)，confidence 是归一化熵（不可信）
    answer = _answer("利空", {"利好": 0.05, "中性": 0.05, "利空": 0.9})
    extracted = laya_annotator.extract_sentiment(answer)
    assert extracted is not None
    label, conf = extracted
    assert label == "利空"
    assert conf == pytest.approx(0.9)


# ------------------------------------------------- 3. 批量推理内核


def test_predict_batch_returns_valid_results():
    answers = [
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}),
        _answer("中性", {"利好": 0.1, "中性": 0.8, "利空": 0.1}),
        _answer("利空", {"利好": 0.05, "中性": 0.05, "利空": 0.9}),
    ]
    agent = _FakeAgent(answers)
    texts = ["【A】新闻一", "【B】新闻二", "【C】新闻三"]
    results = laya_annotator.predict_batch(agent, texts, batch_size=2)
    assert [r["index"] for r in results] == [0, 1, 2]
    assert [r["label"] for r in results] == ["利好", "中性", "利空"]
    assert all("confidence" in r for r in results)
    # 逐条调用（laya predict 一次一个问题，batch_size 只影响分批节奏）：逐条校验入参
    assert [state for state, _ in agent.calls] == [{"body": text} for text in texts]
    assert all(question == laya_annotator.build_choice_question() for _, question in agent.calls)


def test_predict_batch_drops_invalid_and_counts():
    answers = [
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}),
        _answer("乱码", {"利好": 0.7, "中性": 0.2, "利空": 0.1}),  # 非法 label
        None,  # 缺失
    ]
    agent = _FakeAgent(answers)
    stats = {"dropped": 0}
    results = laya_annotator.predict_batch(
        agent, ["t1", "t2", "t3"], batch_size=2, on_drop=lambda: stats.__setitem__("dropped", stats["dropped"] + 1)
    )
    assert len(results) == 1
    assert results[0]["label"] == "利好"
    assert stats["dropped"] == 2


def test_predict_batch_propagates_agent_exception():
    agent = _FakeAgent([RuntimeError("boom")])
    with pytest.raises(RuntimeError, match="boom"):
        laya_annotator.predict_batch(agent, ["t1"], batch_size=1)


def test_predict_batch_reports_original_index_for_alignment():
    # 中间项非法被丢弃后 results 变短，index 必须仍指向 texts 原始位置，
    # 否则调用方 zip 回填会给错误的新闻打上标签（静默错位）。
    answers = [
        _answer("利好", {"利好": 0.7, "中性": 0.2, "利空": 0.1}),
        None,  # 丢弃
        _answer("利空", {"利好": 0.05, "中性": 0.05, "利空": 0.9}),
    ]
    agent = _FakeAgent(answers)
    results = laya_annotator.predict_batch(agent, ["t0", "t1", "t2"], batch_size=3)
    assert [r["index"] for r in results] == [0, 2]
    assert [r["label"] for r in results] == ["利好", "利空"]


# ------------------------------------------------- 4. 抽检集抽样


def _make_store(tmp_path: Path, docs: list[dict[str, Any]]) -> CorpusStore:
    store = CorpusStore(tmp_path / "corpus.db")
    with store:
        store.add_documents(docs)
    return store


def _doc(source: str, source_id: str, text: str, *, ts_code: str | None = None) -> dict[str, Any]:
    return {"source": source, "source_id": source_id, "source_kind": "news", "text": text, "ts_code": ts_code}


def test_sample_review_set_respects_count_and_sources(tmp_path: Path):
    docs = (
        [_doc("sina_7x24", f"n{i}", f"快讯{i}", ts_code="000001.SZ") for i in range(10)]
        + [_doc("sina_stock", f"s{i}", f"个股{i}", ts_code="600519.SH") for i in range(10)]
        + [_doc("cninfo", f"a{i}", f"公告{i}", ts_code="300750.SZ") for i in range(10)]
    )
    store = _make_store(tmp_path, docs)
    with store:
        samples = laya_annotator.sample_review_set(store, n=12, seed=42)
    assert len(samples) == 12
    sources = {s["source"] for s in samples}
    assert {"sina_7x24", "sina_stock", "cninfo"} <= sources
    for s in samples:
        assert set(s) == {"source", "source_id", "text", "ts_code"}


def test_sample_review_set_reproducible_with_seed(tmp_path: Path):
    docs = [_doc("sina_7x24", f"n{i}", f"快讯{i}") for i in range(20)]
    store = _make_store(tmp_path, docs)
    with store:
        a = laya_annotator.sample_review_set(store, n=8, seed=7)
        b = laya_annotator.sample_review_set(store, n=8, seed=7)
    assert a == b


def test_sample_review_set_with_different_seed_differs(tmp_path: Path):
    docs = [_doc("sina_7x24", f"n{i}", f"快讯{i}") for i in range(20)]
    store = _make_store(tmp_path, docs)
    with store:
        a = laya_annotator.sample_review_set(store, n=8, seed=1)
        b = laya_annotator.sample_review_set(store, n=8, seed=2)
    assert a != b


def test_sample_review_set_returns_all_when_n_exceeds(tmp_path: Path):
    docs = [_doc("sina_7x24", f"n{i}", f"快讯{i}") for i in range(5)]
    store = _make_store(tmp_path, docs)
    with store:
        samples = laya_annotator.sample_review_set(store, n=100, seed=3)
    assert len(samples) == 5


def test_sample_review_set_empty_corpus(tmp_path: Path):
    store = _make_store(tmp_path, [])
    with store:
        assert laya_annotator.sample_review_set(store, n=10, seed=1) == []


def test_sample_review_set_preserves_none_ts_code(tmp_path: Path):
    docs = [_doc("sina_7x24", "n0", "快讯")]  # 无 ts_code
    store = _make_store(tmp_path, docs)
    with store:
        samples = laya_annotator.sample_review_set(store, n=1, seed=1)
    assert samples[0]["ts_code"] is None


# ------------------------------------------------- 5. 人工抽检 Gate 评估


def test_evaluate_agreement_passes_at_threshold():
    predictions = [_pred("利好", source_id="0"), _pred("中性", source_id="1"), _pred("利空", source_id="2")]
    gold = [_gold("利好", source_id="0"), _gold("中性", source_id="1"), _gold("利空", source_id="2")]
    report = laya_annotator.evaluate_gate(predictions, gold, threshold=0.85)
    assert report["agreement"] == pytest.approx(1.0)
    assert report["passed"] is True
    assert report["matched"] == 3


def test_evaluate_agreement_fails_below_threshold():
    predictions = [_pred("利好", source_id="0"), _pred("中性", source_id="1"), _pred("利空", source_id="2")]
    gold = [_gold("利好", source_id="0"), _gold("中性", source_id="1"), _gold("利好", source_id="2")]
    report = laya_annotator.evaluate_gate(predictions, gold, threshold=0.85)
    assert report["agreement"] == pytest.approx(2 / 3)
    assert report["passed"] is False


def test_evaluate_gate_matches_records_by_source_id_ignoring_order():
    predictions = [_pred("利空", source_id="0")]
    gold = [_gold("利空", source_id="0")]
    gold.reverse()  # 乱序
    report = laya_annotator.evaluate_gate(predictions, gold, threshold=0.85)
    assert report["agreement"] == pytest.approx(1.0)


def test_evaluate_report_exposes_confusion_focus():
    # 混淆矩阵须含「利空 → 中性」漏判率（评估重点，§11）
    predictions = [_pred("中性", source_id="0"), _pred("中性", source_id="1"), _pred("中性", source_id="2")]
    gold = [_gold("利空", source_id="0"), _gold("利空", source_id="1"), _gold("利空", source_id="2")]
    report = laya_annotator.evaluate_gate(predictions, gold, threshold=0.85)
    assert report["passed"] is False
    assert report["confusion"]["利空"]["中性"] == 3
    assert report["missed_negative_rate"] == pytest.approx(1.0)


def test_evaluate_gate_missing_prediction_skipped_not_penalized():
    # 预测缺失（模型未产出）→ 不参与分子也不参与分母（R21：不以 0 伪装失败）
    predictions = [_pred("利好", source_id="0")]
    gold = [_gold("利好", source_id="0"), _gold("中性", source_id="1")]
    report = laya_annotator.evaluate_gate(predictions, gold, threshold=0.85)
    assert report["matched"] == 1
    assert report["agreement"] == pytest.approx(1.0)


def test_evaluate_gate_rejects_empty_inputs():
    with pytest.raises(ValueError, match="无人工标签"):
        laya_annotator.evaluate_gate([], [], threshold=0.85)


# ------------------------------------------------- CLI 冒烟


class _FakeStore:
    """CorpusStore 替身：CLI 层测试只需 ``iter_documents`` 与上下文协议。

    CLI 层的契约是「参数解析 → 抽样 → 写文件 → 退出码」，语料库读写本身已由
    :func:`_make_store` 系列测试与 ``sample_review_set`` 用例覆盖；此处注入替身，
    避免在 pytest 进程内对 sqlite 文件做二次连接（与产品无关的环境噪声）。
    """

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __enter__(self) -> _FakeStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def iter_documents(self) -> Any:
        return iter(self._docs)


def test_main_sample_writes_review_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    docs = [_doc("sina_7x24", f"n{i}", f"快讯{i}") for i in range(5)]
    monkeypatch.setattr(laya_annotator, "CorpusStore", lambda _path: _FakeStore(docs))
    out = tmp_path / "review.jsonl"
    code = laya_annotator.main(
        ["sample", "--db", str(tmp_path / "corpus.db"), "-n", "5", "--seed", "1", "--out", str(out)]
    )
    assert code == 0
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 5
    assert all(line["label"] == "" for line in lines)


def test_main_evaluate_gate_fail_exit_code(tmp_path: Path):
    pred = tmp_path / "pred.jsonl"
    gold = tmp_path / "gold.jsonl"
    pred.write_text(json.dumps(_pred("利好", source_id="0"), ensure_ascii=False) + "\n", encoding="utf-8")
    gold.write_text(json.dumps(_gold("利空", source_id="0"), ensure_ascii=False) + "\n", encoding="utf-8")
    # 一致率 0 < 0.85 → 退出码 2
    code = laya_annotator.main(["evaluate", "--pred", str(pred), "--gold", str(gold), "--threshold", "0.85"])
    assert code == 2


def test_main_sample_empty_corpus_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(laya_annotator, "CorpusStore", lambda _path: _FakeStore([]))
    out = tmp_path / "review.jsonl"
    code = laya_annotator.main(
        ["sample", "--db", str(tmp_path / "empty.db"), "-n", "5", "--seed", "1", "--out", str(out)]
    )
    assert code == 1
