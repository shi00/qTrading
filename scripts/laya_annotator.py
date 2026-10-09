"""本地 laya-multilingual 零样本新闻情绪标注器 + 人工抽检 Gate（N2-1）。

正本：`reviews/新闻达标模型微调.md` §4.4 路线 2 / §4.5 质控。

背景
----
Laya 是「System 1」结构化决策模型（choice / score / noul），一次前向传播返回带
概率的 typed answer，无需生成自由文本。本工具用 **choice** 问题把一条新闻对
【标的】的短期股价影响判为三枚举之一（利好 / 中性 / 利空），并对输出做**不可信
输入**处理（R21：非法 label / 非有限 / 越界置信度一律丢弃计数，不做猜测性修复）。

**置信度口径（PoC 实测，laya 0.4.1）**：answer dict 有两个置信度字段——
``confidence`` 是归一化熵（官方标注「not calibrated」，跨选项数不可比）；
``answer_confidence`` 是 ``max(p)``（被选中选项的概率质量），是温度校准与
``min_confidence`` 门控所用量，也是本项目质控采用的口径。

**依赖边界（Plans.md §0 / N2-1）**：``laya`` / ``torch`` / ``transformers`` 仅用于
**离线标注**，不进 ``pyproject.toml`` 运行时依赖、不进 CI；运行环境为独立离线
venv（``D:\\workspace\\.venv_finbert``）。本模块顶层不得 ``import laya``——真实推理
仅在 ``annotate`` 子命令 / ``load_agent`` 中延迟导入，保证 import 本模块不拉起
torch 全栈。

**数据不出本机**：全部推理在本地完成，不发送新闻文本到任何外部服务；日志 / 异常
经 ``DataSanitizer`` 脱敏（R9）。

子命令
------
::

    python scripts/laya_annotator.py sample   --db data/corpus/news_corpus.db -n 40 \\
        --seed 42 --out data/corpus/review_sample.jsonl
    python scripts/laya_annotator.py annotate --db data/corpus/news_corpus.db \\
        --model ai_models/laya-multilingual --batch-size 8 --out data/corpus/annotated.jsonl
    python scripts/laya_annotator.py evaluate --pred data/corpus/annotated.jsonl \\
        --gold data/corpus/review_gold.jsonl --threshold 0.85

退出码：0 成功；1 参数 / IO / 数据错误；2 = evaluate 一致率未达阈值（Gate FAIL）。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any
from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.external.news_sources.corpus import CorpusStore  # noqa: E402 - sys.path 注入后导入
from utils.sanitizers import DataSanitizer  # noqa: E402 - sys.path 注入后导入

# 三分类标签（正本 §4.3，顺序固定：0 利空 / 1 中性 / 2 利好）
LABELS: tuple[str, str, str] = ("利好", "中性", "利空")
_LABEL_SET = frozenset(LABELS)

# 人工抽检 Gate 阈值（DoD：一致率 ≥85% 才允许批量标注）
DEFAULT_GATE_THRESHOLD = 0.85

DEFAULT_REVIEW_N = 40
DEFAULT_BATCH_SIZE = 8


# ---------------------------------------------------------------- 1. choice 问题


def build_choice_question() -> dict[str, Any]:
    """构造 laya choice 问题（三枚举 + 判定口径提示词）。

    ``criteria`` 值是对选项的**判据描述**（laya 用它做选项标记打分），
    判定口径必须覆盖：对【】中**股票的短期股价影响倾向**、例行/无实质影响/不明确
    一律中性、判断的不是文字语气（正本 §1 判定口径 / §4.3 边界规则）。
    """
    return {
        "sentiment": {
            "type": "choice",
            "instructions": (
                "判断这条新闻对【】中股票的短期股价影响倾向（可能推高/压低股价，"
                "或例行无实质影响）。例行公告、无实质影响、影响不明确一律判中性；"
                "判断的是对该股票股价的影响，而不是文字语气。"
            ),
            "criteria": {
                "利好": "可能推高该股股价的实质信息，如业绩超预期、大额中标、回购增持、获批、评级上调",
                "中性": "例行事项、无实质影响、影响相互抵消或不明确，如股东大会通知、行业统计",
                "利空": "可能压低该股股价的实质信息，如减持、立案调查、业绩预亏、商誉减值、评级下调、产品降价",
            },
        }
    }


# ------------------------------------------------- 2. 不可信输出处理（核心）


def extract_sentiment(answer: dict[str, Any] | None) -> tuple[str, float] | None:
    """从 laya choice answer dict 提取 ``(label, confidence)``。

    输出视为**不可信输入**（正本 §4.4）：以下任一情形返回 ``None``（调用方丢弃计数），
    **不做猜测性修复**（R21 缺失语义）：

    - ``answer`` 为 None / 缺 ``choice``；
    - ``choice`` 不属于三枚举；
    - ``answer_confidence`` 缺失、非 int/float（bool 不算）、非有限数、或不在 [0, 1]。

    置信度取 ``answer_confidence``（= max(p)，校准门控所用量；``confidence`` 为归一化
    熵，官方标注不可校准、跨选项数不可比，故不采用）。
    """
    if not isinstance(answer, dict):
        return None
    label = answer.get("choice")
    if label not in _LABEL_SET:
        return None
    conf = answer.get("answer_confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return None
    if not math.isfinite(float(conf)) or not (0.0 <= float(conf) <= 1.0):
        return None
    return str(label), float(conf)


def predict_batch(
    agent: Any,
    texts: list[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_drop: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """对文本列表分批调用 laya agent 的 choice 预测，返回合法结果列表。

    每批构造 ``{"body": text}`` state 与 :func:`build_choice_question`；对每条输出经
    :func:`extract_sentiment` 校验，**非法 / 缺失丢弃并计数**（``on_drop`` 回调），
    不猜测性修复。agent 抛出的异常**原样向上传播**（不吞没；由调用方按 R2 处理）。

    Returns:
        只含合法结果的 ``{"index", "label", "confidence"}`` 列表。``index`` 是该条在
        ``texts`` 中的原始下标——丢弃中间的非法项会使结果列表变短，调用方**必须**按
        ``index`` 回填原文，不能按下标直接 zip（否则标签与文本错位）。
    """
    question = build_choice_question()
    results: list[dict[str, Any]] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start : start + batch_size]
        for offset, text in enumerate(chunk):
            answer = agent.predict({"body": text}, question)["answers"].get("sentiment")
            extracted = extract_sentiment(answer)
            if extracted is None:
                if on_drop is not None:
                    on_drop()
                continue
            label, confidence = extracted
            results.append({"index": start + offset, "label": label, "confidence": confidence})
    return results


# ------------------------------------------------- 3. 抽检集抽样


def sample_review_set(
    store: CorpusStore,
    *,
    n: int = DEFAULT_REVIEW_N,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """从语料库**按源分层抽样** ``n`` 条待人工标注样本（可复现 seed）。

    每条为 ``{"source", "source_id", "text", "ts_code"}``（ts_code 可能为 None，R21
    透传不伪造）。分层使人工抽检覆盖三源（规格 §4.1 构成建议）；``n`` 超过语料条数
    时返回全部（不报错、不补齐）。
    """
    rng = random.Random(seed)
    docs = list(store.iter_documents())
    if not docs:
        return []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        by_source.setdefault(str(doc["source"]), []).append(doc)
    # 按源轮转均匀分配名额：每源先均分，余数按源顺序 +1
    names = sorted(by_source)
    slots: dict[str, int] = {}
    for i, name in enumerate(names):
        slots[name] = n // len(names) + (1 if i < n % len(names) else 0)
    picked: list[dict[str, Any]] = []
    for name in names:
        pool = by_source[name]
        count = min(slots[name], len(pool))
        chosen = rng.sample(pool, count) if count < len(pool) else pool
        for doc in chosen:
            picked.append(
                {
                    "source": str(doc["source"]),
                    "source_id": str(doc["source_id"]),
                    "text": str(doc["text"]),
                    "ts_code": doc.get("ts_code"),
                }
            )
    return picked


# ------------------------------------------------- 4. 人工抽检 Gate 评估


def evaluate_gate(
    predictions: list[dict[str, Any]],
    gold_labels: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_GATE_THRESHOLD,
) -> dict[str, Any]:
    """评估人工抽检一致率并输出混淆矩阵。

    按 ``(source, source_id)`` 将预测与人工标签对齐；**无预测 / 无人工标签的记录跳过**
    （不参与分子也不参与分母，R21：不以 0 伪装缺失）。全部无人工标签时抛
    ``ValueError``（无法评估）。重点关注「利空 → 中性」漏判率（正本 §11 评估重点：
    漏报利空代价最高）。

    Returns:
        ``{"matched", "agreement", "passed", "threshold", "confusion",
        "missed_negative_rate"}``。
    """
    if not gold_labels:
        raise ValueError("无人工标签，无法评估（Gate 需要 gold 标注）")
    gold_by_key = {(str(g["source"]), str(g["source_id"])): str(g["label"]) for g in gold_labels}
    confusion: dict[str, dict[str, int]] = {label: {other: 0 for other in LABELS} for label in LABELS}
    matched = 0
    correct = 0
    for pred in predictions:
        key = (str(pred["source"]), str(pred["source_id"]))
        gold = gold_by_key.get(key)
        if gold is None:
            continue  # 人工未标注该条 → 跳过
        predicted = pred.get("label")
        if predicted not in _LABEL_SET:
            continue  # 预测非法 → 跳过（缺失语义，不计入分母）
        matched += 1
        confusion[gold][predicted] += 1
        if gold == predicted:
            correct += 1
    if matched == 0:
        raise ValueError("预测与人工标签无任何匹配记录，无法评估")
    agreement = correct / matched
    missed_negative = confusion["利空"]["中性"]
    missed_negative_rate = missed_negative / max(sum(confusion["利空"].values()), 1)
    return {
        "matched": matched,
        "agreement": agreement,
        "passed": agreement >= threshold,
        "threshold": threshold,
        "confusion": confusion,
        "missed_negative_rate": missed_negative_rate,
    }


def _print_gate_report(report: dict[str, Any]) -> None:
    print(
        f"抽检匹配 {report['matched']} 条；一致率 {report['agreement']:.3f}"
        f"（阈值 {report['threshold']:.2f}）→ {'PASS' if report['passed'] else 'FAIL'}"
    )
    print(f"利空→中性漏判率 {report['missed_negative_rate']:.3f}")
    print("混淆矩阵（行=人工，列=预测）: " + json.dumps(report["confusion"], ensure_ascii=False))


# ------------------------------------------------- 5. agent 加载（延迟导入）


def load_agent(model_path: str | Path) -> Any:
    """延迟导入 laya 并加载本地模型（离线，数据不出本机）。

    仅在 ``annotate`` 子命令调用；模块顶层不 import laya（依赖边界）。
    ``USE_TF=0`` 避免 transformers 探测 TensorFlow 时挂起（laya README 已知问题）。
    """
    import os

    os.environ.setdefault("USE_TF", "0")
    import laya  # type: ignore[import-not-found]  # noqa: PLC0415 - 延迟导入（可选离线依赖）

    return laya.load(str(model_path), device="cpu")


# ------------------------------------------------- 6. 文件 IO 与 CLI


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _write_jsonl(path: str | Path, records: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records), encoding="utf-8")


def _cmd_sample(args: argparse.Namespace) -> int:
    with CorpusStore(args.db) as store:
        samples = sample_review_set(store, n=args.n, seed=args.seed)
    if not samples:
        print("语料库为空或抽取不到样本", file=sys.stderr)
        return 1
    for rec in samples:
        rec.setdefault("label", "")
    _write_jsonl(args.out, samples)
    print(f"已写出 {len(samples)} 条待人工标注样本 → {args.out}")
    return 0


def _cmd_annotate(args: argparse.Namespace) -> int:
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"模型路径不存在: {model_path}", file=sys.stderr)
        return 1
    with CorpusStore(args.db) as store:
        docs = list(store.iter_documents())
    if not docs:
        print("语料库为空，无可标注文档", file=sys.stderr)
        return 1
    texts = [str(doc["text"]) for doc in docs]
    dropped = 0

    def _count_drop() -> None:
        nonlocal dropped
        dropped += 1

    try:
        agent = load_agent(model_path)
        results = predict_batch(agent, texts, batch_size=args.batch_size, on_drop=_count_drop)
    except Exception as exc:  # noqa: BLE001 - 离线工具顶层出口，脱敏后返回错误（R9）
        print(f"标注失败: {DataSanitizer.sanitize_error(exc)}", file=sys.stderr)
        return 1
    # 按原始下标回填：丢弃中间非法项后 results 变短，不能按下标直接 zip（否则错位）
    records = [
        {
            "source": str(docs[res["index"]]["source"]),
            "source_id": str(docs[res["index"]]["source_id"]),
            "text": str(docs[res["index"]]["text"]),
            "label": res["label"],
            "confidence": res["confidence"],
        }
        for res in results
    ]
    _write_jsonl(args.out, records)
    print(f"已标注 {len(records)} 条（丢弃 {dropped} 条非法/缺失）→ {args.out}")
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    predictions = _read_jsonl(args.pred)
    gold = _read_jsonl(args.gold)
    try:
        report = evaluate_gate(predictions, gold, threshold=args.threshold)
    except ValueError as exc:
        print(f"评估失败: {exc}", file=sys.stderr)
        return 1
    _print_gate_report(report)
    if args.report:
        _write_jsonl(args.report, [report])
    # Gate FAIL 退出码 2（供编排区分参数错误与门禁未达）
    return 0 if report["passed"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地 laya-multilingual 零样本标注器 + 人工抽检 Gate（N2-1）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sample = sub.add_parser("sample", help="从语料库按源分层抽样待人工标注样本")
    p_sample.add_argument("--db", type=Path, default=Path("data/corpus/news_corpus.db"))
    p_sample.add_argument("-n", type=int, default=DEFAULT_REVIEW_N)
    p_sample.add_argument("--seed", type=int, default=None)
    p_sample.add_argument("--out", type=Path, required=True)

    p_annotate = sub.add_parser("annotate", help="用本地 laya 模型批量标注语料库（数据不出本机）")
    p_annotate.add_argument("--db", type=Path, default=Path("data/corpus/news_corpus.db"))
    p_annotate.add_argument("--model", type=Path, required=True, help="本地 laya-multilingual 目录")
    p_annotate.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p_annotate.add_argument("--out", type=Path, required=True)

    p_eval = sub.add_parser("evaluate", help="人工抽检 Gate：预测 vs 人工标签一致率")
    p_eval.add_argument("--pred", type=Path, required=True, help="标注器输出（label/confidence）")
    p_eval.add_argument("--gold", type=Path, required=True, help="人工标注（label）")
    p_eval.add_argument("--threshold", type=float, default=DEFAULT_GATE_THRESHOLD)
    p_eval.add_argument("--report", type=Path, default=None, help="写评估报告 JSON（可选）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sample":
        return _cmd_sample(args)
    if args.command == "annotate":
        return _cmd_annotate(args)
    if args.command == "evaluate":
        return _cmd_evaluate(args)
    parser.error(f"未知子命令: {args.command}")  # pragma: no cover - argparse error 已 exit
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
