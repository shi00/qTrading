"""FinBERT 模型效果硬门槛（FT-02）——与最近一次已发布 Release 的 dev/test macro-F1 比较。

背景（检视报告 FT-02）
----------------------
原产线只把 dev/test macro-F1 写入训练摘要与 Release note，Release 准入仅依赖 GGUF
数值一致性 Gate（``scripts/verify_finbert_gguf.py``）——一致性只证明「两种表示的模型
相近」，不能证明分类效果足以发布。维护者已批准设置效果硬门槛，本脚本即该门槛的落地
实现，在 ``train-export-gate`` job（只读权限）内、``Generate release note`` 之前运行。

**闸门落点（发布路径，不可绕过）**：本步骤位于 job1（``train-export-gate``）内；
FAIL 时该步骤退出码非零 → job1 失败 → ``publish-release``（``needs`` + ``if: success``）
跳过 → Release 不产生。不把闸门放进 ``publish-release`` job，避免绕过 job1 的失败判定。

门槛口径（维护者批准）
----------------------
- **主门槛**：新模型 ``test macro-F1 >= 锚点 test macro-F1 - 0.03``，否则 FAIL；
- **dev 下限护栏**：新模型 ``dev macro-F1 >= 锚点 dev macro-F1 - 0.03``，否则 FAIL；
- 容差 ``0.03`` 出处：``reviews/新闻达标模型微调.md`` §5.2「阶段 B 在 400 条测试集上的
  macro-F1 比阶段 A 高 >= 3 个百分点」判据（项目内既有、已批准）。语义为**允许的噪声
  回退上限**——模型不得比上一版已发布模型回退超过 3 个百分点。
- **锚点**：最近一次已发布 Release（tag ``finbert-gguf-v*``，按 semver 降序取最新），
  其 dev/test macro-F1 **运行期从该 Release note 解析**——不在本脚本或 workflow 中
  硬编码具体数值（避免与未来 Release 漂移）。

锚点不可得的三态（R21：不得把「已知不可比」伪装成「已达标」）
--------------------------------------------------------------
1. 存在历史 Release 但 note 中 dev/test macro-F1 缺失 / 不可解析 / 非有限 → **FAIL**
   （fail-closed，不可归因不得放行）；锚点查询本身失败（``gh`` 不可用 / 网络故障 / 列表
   截断）同样 **FAIL**——无法确认「是否存在历史 Release」时不放行。
2. 确无任何历史 Release（首版）→ 不阻断，判定 ``NO_BASELINE``，须在 Release note 与
   PR 中显式声明「无基线可比，未施加效果门槛」。
3. 新模型自身 dev/test macro-F1 缺失 / NaN / 非有限 → **FAIL**（不得视为通过）。

退出码：0 = PASS / NO_BASELINE（可继续发布）；1 = FAIL（阻断发布）。

Usage:
    python scripts/verify_finbert_effect_gate.py \
        --train-summary stage_b_out/best/train_summary.json \
        --repo "$GITHUB_REPOSITORY" \
        --report stage_b_out/effect_gate.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.error_classifier import classify_error, classify_severity  # noqa: E402 - sys.path 注入后导入
from utils.log_decorators import PerfThreshold, track_performance  # noqa: E402 - sys.path 注入后导入
from utils.sanitizers import DataSanitizer  # noqa: E402 - sys.path 注入后导入

LOGGER = logging.getLogger(__name__)

# 效果门槛容差（主门槛与 dev 护栏共用）。出处：reviews/新闻达标模型微调.md §5.2
# 「阶段 B 比阶段 A 高 >= 3 个百分点」判据，语义 = 允许的噪声回退上限。
TOLERANCE = 0.03
TOLERANCE_SOURCE = (
    "reviews/新闻达标模型微调.md §5.2「阶段 B 在 400 条测试集上的 macro-F1 比阶段 A 高 "
    ">= 3 个百分点」判据（允许的噪声回退上限）"
)

# 锚点 Release tag 形态：finbert-gguf-v<major>.<minor>.<patch>。
_RELEASE_TAG_PATTERN = re.compile(r"finbert-gguf-v(\d+)\.(\d+)\.(\d+)")

# 锚点数值的**唯一**解析形态：Release note 中指标行（与 Generate release note 生成
# 形态逐字对齐）。整行匹配，避免误抓效果门槛 lineage 段里的其它数值（那些行用
# ``锚点 dev/test`` / ``本次 dev/test`` 措辞，不含 ``**dev macro-F1**:`` 前缀）。
_METRICS_LINE_PATTERN = re.compile(
    r"^-\s+\*\*dev macro-F1\*\*:\s*(\d+(?:\.\d+)?)\s*/\s*\*\*test macro-F1\*\*:\s*(\d+(?:\.\d+)?)\s*$",
    re.MULTILINE,
)

# Release 列表一次查询条数上限；命中上限时无法保证取到 semver 最新 → fail-closed。
RELEASE_LIST_LIMIT = 100


class EffectGateError(RuntimeError):
    """锚点解析 / ``gh`` 调用失败（fail-closed 信号）。"""


@dataclass(frozen=True)
class Anchor:
    """锚点状态：``available`` 有可比锚点 / ``no_baseline`` 无历史 Release / ``unreadable`` 不可解析。"""

    state: str
    tag: str | None
    dev_macro_f1: float | None
    test_macro_f1: float | None
    detail: str


# --------------------------------------------------------------------------- #
# 纯函数（不触网，供单测覆盖边界）
# --------------------------------------------------------------------------- #
def _finite_or_none(value: object) -> float | None:
    """把任意值规范为有限 ``float``；缺失 / 非数值 / NaN / Inf 一律返回 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_semver_tag(tag: str) -> tuple[int, int, int] | None:
    """解析 ``finbert-gguf-v<major>.<minor>.<patch>`` 为可比较元组；非该形态返回 ``None``。"""
    match = _RELEASE_TAG_PATTERN.fullmatch(tag)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def select_latest_release_tag(tags: Iterable[str]) -> str | None:
    """按 semver 降序取最新的模型 Release tag；无合法 tag 返回 ``None``。"""
    best_tag: str | None = None
    best_key: tuple[int, int, int] | None = None
    for tag in tags:
        key = parse_semver_tag(tag)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_tag, best_key = tag, key
    return best_tag


def parse_macro_f1_from_note(note: str) -> tuple[float | None, float | None]:
    """从 Release note 解析 ``(dev macro-F1, test macro-F1)``；缺失 / 不可解析返回 ``(None, None)``。"""
    match = _METRICS_LINE_PATTERN.search(note or "")
    if match is None:
        return (None, None)
    return (_finite_or_none(float(match.group(1))), _finite_or_none(float(match.group(2))))


def _build_report(
    *,
    current_dev: float | None,
    current_test: float | None,
    anchor: Anchor,
    tolerance: float,
    verdict: str,
    criteria: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "schema": "finbert-effect-gate.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "tolerance": tolerance,
        "tolerance_source": TOLERANCE_SOURCE,
        "anchor_state": anchor.state,
        "anchor_tag": anchor.tag,
        "anchor_detail": anchor.detail,
        "baseline_dev_macro_f1": anchor.dev_macro_f1,
        "baseline_test_macro_f1": anchor.test_macro_f1,
        "current_dev_macro_f1": current_dev,
        "current_test_macro_f1": current_test,
        "criteria": criteria,
        "verdict": verdict,
        "reasons": reasons,
    }


def evaluate_effect_gate(
    *,
    current_dev: float | None,
    current_test: float | None,
    anchor: Anchor,
    tolerance: float = TOLERANCE,
) -> dict[str, Any]:
    """按维护者批准口径判定（纯函数，供单测覆盖通过 / 不达标 / 三态边界）。

    判定优先级：本次指标缺失/NaN/非有限 FAIL → 无基线 NO_BASELINE → 锚点不可用 FAIL →
    否则比较 ``current >= baseline - tolerance``（主门槛 test + dev 下限护栏）。
    本次指标在此处统一经 ``_finite_or_none`` 归一（唯一入口），使 NaN / Inf / 缺失
    一律 FAIL——调用方传入原始值即可，不得依赖上游先做净化。
    """
    current_dev = _finite_or_none(current_dev)
    current_test = _finite_or_none(current_test)
    criteria: dict[str, Any] = {}
    reasons: list[str] = []
    verdict = "FAIL"

    if current_dev is None or current_test is None:
        reasons.append("本次模型 dev/test macro-F1 缺失或非有限值——不得视为通过（R21）")
    elif anchor.state == "no_baseline":
        verdict = "NO_BASELINE"
        reasons.append("无历史 Release 可比——未施加效果门槛（不等同「已达标」，须在 lineage/PR 显式声明）")
    elif anchor.state != "available":
        reasons.append(f"锚点不可用（fail-closed）：{anchor.detail}")
    else:
        baseline_dev = anchor.dev_macro_f1
        baseline_test = anchor.test_macro_f1
        if baseline_dev is None or baseline_test is None:
            reasons.append("锚点 dev/test macro-F1 非有限值（fail-closed）")
        else:
            test_floor = baseline_test - tolerance
            dev_floor = baseline_dev - tolerance
            test_ok = current_test >= test_floor
            dev_ok = current_dev >= dev_floor
            criteria["test_macro_f1"] = {
                "value": current_test,
                "baseline": baseline_test,
                "floor": test_floor,
                "ok": test_ok,
            }
            criteria["dev_macro_f1"] = {
                "value": current_dev,
                "baseline": baseline_dev,
                "floor": dev_floor,
                "ok": dev_ok,
            }
            if test_ok and dev_ok:
                verdict = "PASS"
            else:
                if not test_ok:
                    reasons.append(f"test macro-F1 {current_test} < 锚点 {baseline_test} - {tolerance}（主门槛 FAIL）")
                if not dev_ok:
                    reasons.append(
                        f"dev macro-F1 {current_dev} < 锚点 {baseline_dev} - {tolerance}（dev 下限护栏 FAIL）"
                    )

    return _build_report(
        current_dev=current_dev,
        current_test=current_test,
        anchor=anchor,
        tolerance=tolerance,
        verdict=verdict,
        criteria=criteria,
        reasons=reasons,
    )


# --------------------------------------------------------------------------- #
# 锚点解析（gh CLI，外部 IO）
# --------------------------------------------------------------------------- #
def _log_anchor_failure(detail: str, exc: Exception) -> None:
    """外部 IO 失败按严重度选日志级别（R9：错误消息经 DataSanitizer 脱敏）。"""
    info = classify_error(exc, context="finbert_effect_gate")
    severity = classify_severity(exc, context="finbert_effect_gate")
    level = logging.ERROR if severity == "system" else logging.WARNING
    LOGGER.log(
        level,
        "锚点解析失败（fail-closed）: %s | code=%s | %s - %s",
        detail,
        info.get("code"),
        type(exc).__name__,
        DataSanitizer.sanitize_error(exc),
    )


@track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK, operation_name="finbert_effect_gate.gh")
def _run_gh(args: Sequence[str]) -> str:
    """调用 ``gh`` CLI 并返回 stdout；非零退出抛 ``EffectGateError``（fail-closed）。"""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise EffectGateError(f"gh 调用失败（exit {proc.returncode}）: gh {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def list_release_tags(repo: str, *, limit: int = RELEASE_LIST_LIMIT) -> list[str]:
    """列出仓库 Release tag（可能含语料等非模型 Release，由调用方过滤）。"""
    raw = _run_gh(["release", "list", "--repo", repo, "--limit", str(limit), "--json", "tagName"])
    data = json.loads(raw)
    tags = [str(item["tagName"]) for item in data]
    if len(tags) >= limit:
        raise EffectGateError(f"Release 列表达到查询上限 {limit}，无法保证取到 semver 最新 → fail-closed")
    return tags


def fetch_release_note(repo: str, tag: str) -> str:
    """读取指定 Release 的 note 正文。"""
    return _run_gh(["release", "view", tag, "--repo", repo, "--json", "body", "--jq", ".body"])


def resolve_anchor(repo: str) -> Anchor:
    """解析锚点状态：查询失败 / 不可解析一律回落 ``unreadable``（fail-closed）。"""
    try:
        tags = list_release_tags(repo)
    except Exception as exc:  # 无法确认是否存在历史 Release → 不放行
        _log_anchor_failure("查询 Release 列表失败，无法确认是否存在历史 Release", exc)
        return Anchor(
            state="unreadable",
            tag=None,
            dev_macro_f1=None,
            test_macro_f1=None,
            detail="查询 Release 列表失败，无法确认是否存在历史 Release（fail-closed）",
        )

    latest = select_latest_release_tag(tags)
    if latest is None:
        return Anchor(
            state="no_baseline",
            tag=None,
            dev_macro_f1=None,
            test_macro_f1=None,
            detail=f"未发现 finbert-gguf-v* 历史 Release（共 {len(tags)} 个 Release）",
        )

    try:
        note = fetch_release_note(repo, latest)
    except Exception as exc:
        _log_anchor_failure(f"读取锚点 Release `{latest}` 的 note 失败", exc)
        return Anchor(
            state="unreadable",
            tag=latest,
            dev_macro_f1=None,
            test_macro_f1=None,
            detail=f"读取锚点 Release `{latest}` 的 note 失败（fail-closed）",
        )

    dev, test = parse_macro_f1_from_note(note)
    if dev is None or test is None:
        return Anchor(
            state="unreadable",
            tag=latest,
            dev_macro_f1=dev,
            test_macro_f1=test,
            detail=f"锚点 Release `{latest}` 的 note 中 dev/test macro-F1 缺失或不可解析（fail-closed）",
        )
    return Anchor(
        state="available",
        tag=latest,
        dev_macro_f1=dev,
        test_macro_f1=test,
        detail=f"锚点 `{latest}`",
    )


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def run_gate(*, train_summary_path: Path, repo: str) -> dict[str, Any]:
    """读取本次训练指标 + 解析锚点 + 判定，返回结构化报告。

    传入原始摘要值即可：缺失 / NaN / 非有限由 ``evaluate_effect_gate`` 统一归一为 FAIL。
    """
    summary = json.loads(train_summary_path.read_text(encoding="utf-8"))
    anchor = resolve_anchor(repo)
    return evaluate_effect_gate(
        current_dev=summary.get("best_dev_macro_f1"),
        current_test=summary.get("test_macro_f1"),
        anchor=anchor,
    )


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 68)
    print("FinBERT 模型效果硬门槛（FT-02）")
    print("=" * 68)
    print(f"锚点状态: {report['anchor_state']} | 锚点 tag: {report['anchor_tag']}")
    print(
        f"锚点 dev/test macro-F1: {report['baseline_dev_macro_f1']} / {report['baseline_test_macro_f1']}"
        f" | 本次 dev/test macro-F1: {report['current_dev_macro_f1']} / {report['current_test_macro_f1']}"
    )
    print(f"容差: {report['tolerance']}（{report['tolerance_source']}）")
    for name, item in report["criteria"].items():
        mark = "PASS" if item["ok"] else "FAIL"
        print(f"  {mark}  {name}: value={item['value']} baseline={item['baseline']} floor={item['floor']}")
    for reason in report["reasons"]:
        print(f"  - {reason}")
    print(f"VERDICT: {report['verdict']}")
    print("=" * 68)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinBERT 模型效果硬门槛（FT-02）")
    parser.add_argument("--train-summary", required=True, help="训练摘要 train_summary.json 路径（dev/test macro-F1）")
    parser.add_argument("--repo", required=True, help="GitHub 仓库 owner/name（用于查询已发布 Release 作锚点）")
    parser.add_argument("--report", default="stage_b_out/effect_gate.json", help="报告输出路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    try:
        report = run_gate(train_summary_path=Path(args.train_summary), repo=args.repo)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("效果门槛评估失败（fail-closed，拒绝发布）: %s", DataSanitizer.sanitize_error(exc))
        return 1

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"报告已写入: {report_path}")
    return 0 if report["verdict"] in ("PASS", "NO_BASELINE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
