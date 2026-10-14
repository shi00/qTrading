"""FinBERT2-base 新闻情绪三分类阶段 B 训练 driver（CG-1，固化 ``reviews/新闻达标模型微调.md`` §5.2）。

把离线手工完成的 CPU 全量微调固化为可复现脚本（固定 seed），供后续 CG-3 CI workflow
调用；产物为 HF ``save_pretrained`` 格式 best 权重，交由 ``scripts/export_finbert_gguf.py``
拆头并转 GGUF。本脚本自包含，不 import data/cache 层业务逻辑。

**标签序正本（reviews §4.4 / §6.1 / §8.2）**：``0=利空, 1=中性, 2=利好``，
统一 ``[利空, 中性, 利好]``——训练、导出、推理三处必须一致（与
``scripts/verify_finbert_gguf.py`` 的 ``LABELS`` 常量同源）。

**超参来源（reviews §5.2）**：lr 2e-5 / batch 32 / epoch 3 / warmup 10% / weight decay 0.01 /
``max_length`` 64（标题）/ 验证集 macro-F1 早停 patience=1；CPU ``torch.set_num_threads(16)``。
**类别权重（§5.2「损失=交叉熵+类别权重」；§4.5「中性常占 60%+：训练时使用类别权重」）**：
按 train split 类别频率以 balanced 语义（``w_c = N / (C·n_c)``）计算，经自定义
``Trainer.compute_loss`` 注入加权交叉熵（见 ``compute_class_weights``）。
离线实物（``data/corpus/stage_b_out/best/config.json``）为 ``BertForSequenceClassification``
（transformers 5.19.0），与本脚本流程一致。

**seed 依据**：离线产物未记录训练 seed（无 ``training_args.bin`` / ``trainer_state.json``，
无法考证），故取项目 FinBERT 工件链既有确定性种子 ``20261008``（同
``scripts/verify_finbert_gguf.py`` 的 ``DEFAULT_SEED``，方案 r3 修订日），保持链条统一；
重训时如需与离线权重逐位一致，须另行考证当时 seed。

**依赖边界**：torch / transformers 仅离线训练使用，不进 pyproject 运行时依赖、不进 CI
单测环境；全部经 ``importlib`` 函数体内延迟导入（同 ``verify_finbert_gguf._import_optional``
先例），单测环境无 torch 时本模块仍可导入。

Usage:
    python scripts/train_finbert.py --corpus-dir data/corpus \\
        --model-dir ai_models/FinBERT2-base --out-dir data/corpus/stage_b_out

    # 只做语料完整性校验并打印训练计划，不训练
    python scripts/train_finbert.py --corpus-dir data/corpus \\
        --model-dir ai_models/FinBERT2-base --out-dir out --dry-run

退出码：成功 0；语料 / 参数 / 导入异常 1。
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

LOGGER = logging.getLogger("scripts.train_finbert")

# 标签序正本：reviews §4.4 / §6.1 / §8.2（0=利空, 1=中性, 2=利好）
LABELS = ("利空", "中性", "利好")

# 语料 split 文件名（dev 现网语料目录使用 split_val 命名，作为回退候选）
TRAIN_FILE = "split_train.jsonl"
DEV_CANDIDATES = ("split_dev.jsonl", "split_val.jsonl")
TEST_FILE = "split_test.jsonl"

# seed 依据：离线产物未记录（见模块 docstring）；与 verify_finbert_gguf.DEFAULT_SEED 同源
DEFAULT_SEED = 20261008
# 以下超参正本均为 reviews §5.2
DEFAULT_EPOCHS = 3
DEFAULT_LR = 2e-5
DEFAULT_BATCH_SIZE = 32
DEFAULT_MAX_LENGTH = 64
DEFAULT_WARMUP_RATIO = 0.1
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_PATIENCE = 1
DEFAULT_NUM_THREADS = 16  # reviews §5.2：CPU torch.set_num_threads(16)

BEST_DIR_NAME = "best"


def load_split_file(path: Path) -> list[dict[str, str]]:
    """加载并校验单个 split 文件（纯函数）。

    逐行 JSON 合法性、``text`` 非空字符串、标签域仅允许 ``[利空, 中性, 利好]``、
    行数非零；空行跳过（不计入语料行）；非法行带行号拒绝，不做猜测性修复。
    以 ``utf-8-sig`` 读取，UTF-8 BOM 首行不误报。
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"split 文件不存在: {path}")
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} 第 {line_no} 行不是合法 JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path.name} 第 {line_no} 行不是 JSON 对象")
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"{path.name} 第 {line_no} 行 text 缺失或为空")
            label = item.get("label")
            if label not in LABELS:
                raise ValueError(f"{path.name} 第 {line_no} 行标签越域（标签域仅允许 {list(LABELS)}）: {label!r}")
            rows.append({"text": text, "label": label})
    if not rows:
        raise ValueError(f"{path.name} 行数为 0，拒绝空语料")
    return rows


def validate_corpus_dir(corpus_dir: Path | str, *, with_distribution: bool = False) -> dict[str, Any]:
    """校验语料目录三 split 完整性（纯函数）。

    ``dev`` 文件优先 ``split_dev.jsonl``，缺失时回退 ``split_val.jsonl``
    （现网语料目录命名，见 ``DEV_CANDIDATES``）；返回 ``train`` / ``dev`` / ``test``
    三组 ``{"text", "label"}`` 行，``with_distribution=True`` 时附 ``train_distribution``。
    """
    base = Path(corpus_dir)
    dev_path = next((base / name for name in DEV_CANDIDATES if (base / name).is_file()), None)
    if dev_path is None:
        raise FileNotFoundError(f"未找到 dev split 文件（候选: {', '.join(DEV_CANDIDATES)}）于 {base}")
    splits: dict[str, Any] = {
        "train": load_split_file(base / TRAIN_FILE),
        "dev": load_split_file(dev_path),
        "test": load_split_file(base / TEST_FILE),
    }
    if with_distribution:
        splits["train_distribution"] = {
            label: sum(1 for row in splits["train"] if row["label"] == label) for label in LABELS
        }
    return splits


def seed_everything(seed: int) -> list[str]:
    """固定随机源（random / numpy / torch 三方），返回已设置的组件名。

    torch 经延迟导入：未安装时跳过（单测 / 无 torch 环境正常现象），不报错。
    """
    components = ["random", "numpy"]
    random.seed(seed)
    np.random.seed(seed)
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        LOGGER.warning("torch 未安装，仅设置 random / numpy 种子（无 torch 环境正常现象）")
    else:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        components.append("torch")
    return components


def macro_f1(y_true: list[int], y_pred: list[int], num_classes: int = 3) -> float:
    """逐类 P/R/F1 后取宏平均（纯函数，与 sklearn ``f1_score(average='macro')`` 零除语义一致：0.0）。

    训练三分类仅 3 类且量级已知，自实现避免引入 scikit-learn 依赖（依赖边界见 docstring）。
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f"长度不一致: {len(y_true)} vs {len(y_pred)}")
    if not y_true:
        raise ValueError("空输入无法计算 macro-F1")
    f1_sum = 0.0
    for cls in range(num_classes):
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == cls and p == cls)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != cls and p == cls)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == cls and p != cls)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1_sum += (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return f1_sum / num_classes


def compute_class_weights(distribution: Mapping[str, int]) -> list[float]:
    """按 train split 类别频率计算 balanced 类别权重（纯函数，reviews §5.2 / §4.5）。

    语义与 sklearn ``class_weight="balanced"`` 一致：``w_c = N / (C·n_c)``；
    返回顺序与 ``LABELS``（0=利空, 1=中性, 2=利好）对齐。任一类别计数为 0 时拒绝
    （除零无意义，训练语料必须覆盖全部三类）。
    """
    counts = [int(distribution.get(label, 0)) for label in LABELS]
    if any(count == 0 for count in counts):
        paired = dict(zip(LABELS, counts, strict=True))
        raise ValueError(f"类别计数为 0，无法计算类别权重（训练语料须覆盖全部三类）: {paired}")
    total = sum(counts)
    num_classes = len(LABELS)
    return [total / (num_classes * count) for count in counts]


def build_training_plan(args: argparse.Namespace, *, splits: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造训练计划（含语料校验与行数 / 超参 / seed / 类别权重 / 产物路径），供 dry-run 打印与复核。

    ``splits`` 为调用方已加载的语料（须含 ``train_distribution``，即
    ``validate_corpus_dir(..., with_distribution=True)`` 的返回值）时直接复用，
    避免重复语料校验（``train()`` 路径）；缺省时内部自行校验。
    """
    if splits is None:
        splits = validate_corpus_dir(args.corpus_dir, with_distribution=True)
    return {
        "corpus_dir": str(args.corpus_dir),
        "train_rows": len(splits["train"]),
        "dev_rows": len(splits["dev"]),
        "test_rows": len(splits["test"]),
        "train_distribution": splits["train_distribution"],
        "class_weights": compute_class_weights(splits["train_distribution"]),
        "model_dir": str(args.model_dir),
        "out_dir": str(args.out_dir),
        "best_dir": str(Path(args.out_dir) / BEST_DIR_NAME),
        "seed": args.seed,
        "hyperparams": {
            "epochs": args.epochs,
            "lr": args.lr,
            "batch_size": args.batch_size,
            "max_length": args.max_length,
            "warmup_ratio": args.warmup_ratio,
            "weight_decay": args.weight_decay,
            "patience": args.patience,
            "num_threads": args.num_threads,
        },
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    """执行阶段 B 训练（reviews §5.2），保存 best 权重（HF 格式）与摘要。

    torch / transformers 在函数体内延迟导入；本函数不在单测环境执行。
    损失注入类别权重（§5.2「损失=交叉熵+类别权重」）：经 ``_WeightedCETrainer``
    施加 ``compute_class_weights``（balanced）权重。
    """
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")

    seed_everything(args.seed)
    torch.set_num_threads(args.num_threads)

    splits = validate_corpus_dir(args.corpus_dir, with_distribution=True)
    plan = build_training_plan(args, splits=splits)  # 复用 splits，语料仅校验一次
    LOGGER.info(
        "语料: train=%d dev=%d test=%d 分布=%s 类别权重=%s",
        plan["train_rows"],
        plan["dev_rows"],
        plan["test_rows"],
        splits["train_distribution"],
        plan["class_weights"],
    )

    tokenizer = transformers.AutoTokenizer.from_pretrained(str(args.model_dir))
    model = transformers.AutoModelForSequenceClassification.from_pretrained(str(args.model_dir), num_labels=len(LABELS))

    def _encode(rows: list[dict[str, str]]) -> dict[str, list[int]]:
        """预分词（训练 / 推理同口径截断，见 verify_finbert_gguf._hf_input_ids）。"""
        encoded = tokenizer([row["text"] for row in rows], truncation=True, max_length=args.max_length)
        return {key: list(value) for key, value in encoded.items()}

    label_ids = {label: index for index, label in enumerate(LABELS)}

    class _EncodedDataset(torch.utils.data.Dataset):
        """预分词数据集（动态 padding 交给 DataCollatorWithPadding）。"""

        def __init__(self, encoded: dict[str, list[int]], labels: list[int]) -> None:
            self._encoded = encoded
            self._labels = labels

        def __len__(self) -> int:
            return len(self._labels)

        def __getitem__(self, index: int) -> dict[str, Any]:
            item = {key: value[index] for key, value in self._encoded.items()}
            item["labels"] = self._labels[index]
            return item

    def _make_dataset(rows: list[dict[str, str]]) -> Any:
        encoded = _encode(rows)
        labels = [label_ids[row["label"]] for row in rows]
        return _EncodedDataset(encoded, labels)

    def _compute_metrics(eval_pred: Any) -> dict[str, float]:
        y_pred = [int(x) for x in np.argmax(eval_pred.predictions, axis=-1)]
        y_true = [int(x) for x in eval_pred.label_ids]
        return {"macro_f1": macro_f1(y_true, y_pred)}

    class_weights = torch.tensor(plan["class_weights"], dtype=torch.float32)

    class _WeightedCETrainer(transformers.Trainer):
        """交叉熵注入类别权重（reviews §5.2「损失=交叉熵+类别权重」）。

        基类透传的 ``num_items_in_batch`` 等关键字参数显式不消费——损失保持
        batch 内 mean 加权交叉熵语义。
        """

        def compute_loss(self, model: Any, inputs: dict[str, Any], return_outputs: bool = False, **kwargs: Any) -> Any:
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            logits = outputs.logits
            loss = torch.nn.functional.cross_entropy(logits, labels, weight=class_weights)
            return (loss, outputs) if return_outputs else loss

    training_args = transformers.TrainingArguments(
        output_dir=str(Path(args.out_dir) / "trainer"),
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        # transformers 5.x 移除 warmup_ratio，比例语义并入 warmup_steps（float，
        # [0, 1) 表示 total steps 比例；v5.19.0 training_args.py L796）。CLI 参数
        # 名保留 --warmup-ratio 以表达 reviews §5.2 的「warmup 10%」超参语义。
        warmup_steps=args.warmup_ratio,
        weight_decay=args.weight_decay,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
        save_total_limit=2,
        logging_steps=50,
        seed=args.seed,
        data_seed=args.seed,
        report_to=[],
    )
    trainer = _WeightedCETrainer(
        model=model,
        args=training_args,
        train_dataset=_make_dataset(splits["train"]),
        eval_dataset=_make_dataset(splits["dev"]),
        data_collator=transformers.DataCollatorWithPadding(tokenizer),
        compute_metrics=_compute_metrics,
    )
    trainer.add_callback(transformers.EarlyStoppingCallback(early_stopping_patience=args.patience))
    trainer.train()

    best_dir = Path(args.out_dir) / BEST_DIR_NAME
    best_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(best_dir))
    tokenizer.save_pretrained(str(best_dir))

    test_metrics = trainer.evaluate(eval_dataset=_make_dataset(splits["test"]), metric_key_prefix="test")
    best_macro_f1 = float(trainer.state.best_metric) if trainer.state.best_metric is not None else None
    summary = {
        "best_dir": str(best_dir),
        "seed": args.seed,
        "hyperparams": plan["hyperparams"],
        "class_weights": plan["class_weights"],
        "best_dev_macro_f1": best_macro_f1,
        "test_macro_f1": float(test_metrics.get("test_macro_f1", float("nan"))),
        "labels": list(LABELS),
    }
    (best_dir / "train_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if best_macro_f1 is not None:
        (best_dir / "best_f1.txt").write_text(f"{best_macro_f1:.4f}\n", encoding="utf-8")
    (best_dir / "test_macro_f1.txt").write_text(f"{summary['test_macro_f1']:.4f}\n", encoding="utf-8")
    return summary


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinBERT2-base 新闻情绪三分类阶段 B 训练（reviews §5.2 固化）")
    parser.add_argument(
        "--corpus-dir", required=True, help="含 split_train / split_dev(或 split_val) / split_test.jsonl 的语料目录"
    )
    parser.add_argument("--model-dir", required=True, help="HF 基座（FinBERT2-base）或微调续训权重目录")
    parser.add_argument("--out-dir", required=True, help="输出目录（best/ 权重与训练摘要写入其中）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="固定随机种子（取值依据见模块 docstring）")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS, help="训练轮数（reviews §5.2）")
    parser.add_argument("--lr", type=float, default=DEFAULT_LR, help="学习率（reviews §5.2）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="batch 大小（reviews §5.2）")
    parser.add_argument(
        "--max-length", type=int, default=DEFAULT_MAX_LENGTH, help="分词截断长度（reviews §5.2：标题 64）"
    )
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=DEFAULT_WARMUP_RATIO,
        help="warmup 比例（reviews §5.2：10%%；映射 transformers 5.x TrainingArguments.warmup_steps 的 float 比例语义）",
    )
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY, help="weight decay（reviews §5.2）")
    parser.add_argument("--patience", type=int, default=DEFAULT_PATIENCE, help="早停 patience（reviews §5.2）")
    parser.add_argument(
        "--num-threads", type=int, default=DEFAULT_NUM_THREADS, help="torch CPU 线程数（reviews §5.2：16）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只做语料校验并打印训练计划，不训练")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """脚本入口：dry-run 只校验并打印计划；真实路径执行训练。失败记录 ERROR 并返回 1。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    try:
        if args.dry_run:
            plan = build_training_plan(args)
            LOGGER.info("训练计划（dry-run，未执行训练）:\n%s", json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        summary = train(args)
        LOGGER.info("训练完成:\n%s", json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, ImportError, OSError) as exc:
        LOGGER.error("训练失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
