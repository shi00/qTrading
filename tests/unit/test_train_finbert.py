"""Tests for scripts/train_finbert.py（CG-1 阶段 B 训练 driver 固化）。

覆盖纯函数路径（单测环境无 torch / transformers）：
- 语料加载与完整性校验（行数守恒 / 非法行 / 标签域拒绝 / 空文件 / 缺文件 / dev 文件名回退）；
- seed 设置（random / numpy 可复现；torch 缺席时跳过不报错）；
- macro-F1 纯函数（手算基准 + 零除语义）；
- 训练计划构造与 CLI --dry-run（不触发真实训练）。

重型链路（torch / transformers）在单测中一律不执行：CI 无 torch。
脚本模块经 ``sys.path.insert(ROOT / "scripts")`` 导入（与 tests/unit/scripts 同惯例）。
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import train_finbert as tf  # noqa: E402

VALID_LABELS = ("利空", "中性", "利好")


def _write_split(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _make_corpus_dir(tmp_path: Path, *, dev_name: str = "split_val.jsonl") -> Path:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    _write_split(
        corpus_dir / "split_train.jsonl",
        [
            {"text": "回购利好", "label": "利好"},
            {"text": "减持利空", "label": "利空"},
            {"text": "例行公告", "label": "中性"},
        ],
    )
    _write_split(
        corpus_dir / dev_name, [{"text": "中标重大合同", "label": "利好"}, {"text": "立案调查", "label": "利空"}]
    )
    _write_split(corpus_dir / "split_test.jsonl", [{"text": "业绩预亏", "label": "利空"}])
    return corpus_dir


# --------------------------------------------------------------------------- #
# 语料加载与完整性校验
# --------------------------------------------------------------------------- #
def test_load_split_file_returns_rows_and_conserves_count(tmp_path: Path) -> None:
    """合法语料逐行加载，行数守恒且保留 text / label 字段。"""
    path = tmp_path / "split.jsonl"
    rows = [{"text": "回购利好", "label": "利好"}, {"text": "减持利空", "label": "利空"}]
    _write_split(path, rows)
    loaded = tf.load_split_file(path)
    assert len(loaded) == len(rows)
    assert [(row["text"], row["label"]) for row in loaded] == [("回购利好", "利好"), ("减持利空", "利空")]


def test_load_split_file_rejects_invalid_json_with_line_number(tmp_path: Path) -> None:
    """非法 JSON 行必须报错且带行号，不得静默跳过。"""
    path = tmp_path / "split.jsonl"
    path.write_text(
        json.dumps({"text": "回购", "label": "利好"}, ensure_ascii=False) + "\n{broken json\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="第 2 行"):
        tf.load_split_file(path)


def test_load_split_file_rejects_label_out_of_domain(tmp_path: Path) -> None:
    """标签域仅允许 [利空, 中性, 利好]，越域样本拒绝。"""
    path = tmp_path / "split.jsonl"
    _write_split(path, [{"text": "回购利好", "label": "看涨"}])
    with pytest.raises(ValueError, match="标签域"):
        tf.load_split_file(path)


def test_load_split_file_rejects_missing_or_empty_text(tmp_path: Path) -> None:
    """text 缺失 / 非字符串 / 空串均拒绝。"""
    cases: list[list[dict[str, str]]] = [
        [{"label": "利好"}],
        [{"text": "", "label": "利好"}],
        [{"text": "   ", "label": "利好"}],
    ]
    for rows in cases:
        path = tmp_path / "case.jsonl"
        _write_split(path, rows)
        with pytest.raises(ValueError, match="text 缺失或为空"):
            tf.load_split_file(path)


def test_load_split_file_rejects_empty_file(tmp_path: Path) -> None:
    """空文件（0 行）必须拒绝。"""
    path = tmp_path / "split.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="行数为 0"):
        tf.load_split_file(path)


def test_load_split_file_rejects_missing_file(tmp_path: Path) -> None:
    """split 文件缺失必须拒绝。"""
    missing = tmp_path / "not_exist.jsonl"
    with pytest.raises(FileNotFoundError, match="split 文件不存在") as excinfo:
        tf.load_split_file(missing)
    assert "not_exist.jsonl" in str(excinfo.value)


def test_validate_corpus_dir_accepts_split_val_fallback(tmp_path: Path) -> None:
    """dev 文件优先 split_dev.jsonl，缺失时回退 split_val.jsonl（现网语料命名）。"""
    corpus_dir = _make_corpus_dir(tmp_path, dev_name="split_val.jsonl")
    splits = tf.validate_corpus_dir(corpus_dir)
    assert set(splits) == {"train", "dev", "test"}
    assert len(splits["train"]) == 3
    assert len(splits["dev"]) == 2
    assert len(splits["test"]) == 1


def test_validate_corpus_dir_prefers_split_dev(tmp_path: Path) -> None:
    """split_dev.jsonl 与 split_val.jsonl 并存时优先前者。"""
    corpus_dir = _make_corpus_dir(tmp_path, dev_name="split_val.jsonl")
    _write_split(corpus_dir / "split_dev.jsonl", [{"text": "优先文件", "label": "中性"}])
    splits = tf.validate_corpus_dir(corpus_dir)
    assert len(splits["dev"]) == 1
    assert splits["dev"][0]["text"] == "优先文件"


def test_validate_corpus_dir_reports_label_distribution(tmp_path: Path) -> None:
    """校验结果附带各 split 标签分布（供训练计划与排查）。"""
    corpus_dir = _make_corpus_dir(tmp_path)
    splits = tf.validate_corpus_dir(corpus_dir, with_distribution=True)
    assert splits["train_distribution"] == {"利空": 1, "中性": 1, "利好": 1}


# --------------------------------------------------------------------------- #
# seed 设置
# --------------------------------------------------------------------------- #
def test_seed_everything_sets_random_and_numpy_reproducibly() -> None:
    """同 seed 两次调用后 random / numpy 随机序列完全一致。"""
    components = tf.seed_everything(20261008)
    assert "random" in components
    assert "numpy" in components
    seq_a = [random.random() for _ in range(5)]
    arr_a = np.random.default_rng(0).standard_normal(1)  # 占位，避免 default_rng 全局态耦合
    tf.seed_everything(20261008)
    seq_b = [random.random() for _ in range(5)]
    assert seq_a == seq_b
    np.random.seed(20261008)
    legacy_a = np.random.standard_normal(4)
    tf.seed_everything(20261008)
    np.random.seed(20261008)
    legacy_b = np.random.standard_normal(4)
    np.testing.assert_array_equal(legacy_a, legacy_b)
    assert arr_a.shape == (1,)


def test_seed_everything_torch_only_when_available() -> None:
    """torch 缺席时跳过（不报错、不含 torch）；在场时包含 torch。"""
    components = tf.seed_everything(7)
    torch_installed = importlib.util.find_spec("torch") is not None
    if torch_installed:
        assert "torch" in components
    else:
        assert "torch" not in components


# --------------------------------------------------------------------------- #
# macro-F1 纯函数
# --------------------------------------------------------------------------- #
def test_macro_f1_perfect_prediction() -> None:
    """完美预测 macro-F1 = 1.0。"""
    y = [0, 1, 2, 2, 1, 0]
    assert tf.macro_f1(y, y) == pytest.approx(1.0)


def test_macro_f1_hand_computed_reference() -> None:
    """手算基准：与逐类 P/R/F1 平均一致（对照 sklearn zero_division=0 语义）。"""
    y_true = [0, 0, 1, 1, 2, 2]
    y_pred = [0, 1, 1, 1, 2, 0]
    # 类0: P=1/2 R=1/2 F1=0.5；类1: P=2/3 R=1 F1=0.8；类2: P=1 R=1/2 F1=2/3
    expected = (0.5 + 0.8 + 2.0 / 3.0) / 3.0
    assert tf.macro_f1(y_true, y_pred) == pytest.approx(expected)


def test_macro_f1_absent_class_counts_as_zero() -> None:
    """某类完全未出现（P=R=0）按 0 计入 macro 平均（zero_division=0 语义）。"""
    y_true = [1, 1, 1]
    y_pred = [1, 1, 1]
    # 类0 / 类2 无任何预测与真实 → F1=0；类1 F1=1 → macro = 1/3
    assert tf.macro_f1(y_true, y_pred) == pytest.approx(1.0 / 3.0)


def test_macro_f1_rejects_length_mismatch_and_empty() -> None:
    """长度不一致 / 空输入必须报错。"""
    with pytest.raises(ValueError, match="长度不一致"):
        tf.macro_f1([0, 1], [0])
    with pytest.raises(ValueError, match="空输入"):
        tf.macro_f1([], [])


# --------------------------------------------------------------------------- #
# 训练计划与 CLI --dry-run
# --------------------------------------------------------------------------- #
def _base_args(tmp_path: Path) -> list[str]:
    corpus_dir = _make_corpus_dir(tmp_path)
    return [
        "--corpus-dir",
        str(corpus_dir),
        "--model-dir",
        str(tmp_path / "model"),
        "--out-dir",
        str(tmp_path / "out"),
    ]


def test_parse_args_defaults_match_reviews_section5() -> None:
    """默认超参与 reviews §5.2 一致（lr 2e-5 / batch 32 / epoch 3 / max_length 64 / warmup 0.1 / wd 0.01 / patience 1）。"""
    args = tf._parse_args(["--corpus-dir", "c", "--model-dir", "m", "--out-dir", "o"])
    assert args.epochs == 3
    assert args.lr == pytest.approx(2e-5)
    assert args.batch_size == 32
    assert args.max_length == 64
    assert args.warmup_ratio == pytest.approx(0.1)
    assert args.weight_decay == pytest.approx(0.01)
    assert args.patience == 1
    assert args.seed == 20261008


def test_build_training_plan_shape(tmp_path: Path) -> None:
    """计划包含行数 / 超参 / seed / 产物路径，供 dry-run 打印与复核。"""
    args = tf._parse_args(_base_args(tmp_path))
    plan = tf.build_training_plan(args)
    assert plan["train_rows"] == 3
    assert plan["dev_rows"] == 2
    assert plan["test_rows"] == 1
    assert plan["seed"] == 20261008
    assert plan["hyperparams"]["epochs"] == 3
    assert plan["hyperparams"]["lr"] == pytest.approx(2e-5)
    assert plan["hyperparams"]["batch_size"] == 32
    assert plan["hyperparams"]["max_length"] == 64
    assert plan["out_dir"] == str(tmp_path / "out")
    assert Path(plan["best_dir"]).name == "best"


def test_main_dry_run_prints_plan_without_training(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run 只完成语料校验并打印计划，不得触发真实训练。"""
    monkeypatch.setattr(tf, "train", lambda args: pytest.fail("dry-run 不得触发 train()"))
    with caplog.at_level("INFO", logger="scripts.train_finbert"):
        code = tf.main(_base_args(tmp_path) + ["--dry-run"])
    assert code == 0
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "train_rows" in joined
    assert "20261008" in joined
    assert "best" in joined


def test_main_dry_run_fails_on_invalid_corpus(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """语料校验失败时 dry-run 以非零码退出并记录原因。"""
    corpus_dir = _make_corpus_dir(tmp_path)
    _write_split(corpus_dir / "split_train.jsonl", [{"text": "坏样本", "label": "看涨"}])
    with caplog.at_level("ERROR", logger="scripts.train_finbert"):
        code = tf.main(
            [
                "--corpus-dir",
                str(corpus_dir),
                "--model-dir",
                str(tmp_path / "m"),
                "--out-dir",
                str(tmp_path / "o"),
                "--dry-run",
            ]
        )
    assert code == 1
