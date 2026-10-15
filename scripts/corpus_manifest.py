"""FinBERT 训练语料 manifest 校验 / 生成（FT-07）。

背景（检视报告 FT-07）
----------------------
原产线把语料 split 的行数期望值**硬编码**在 workflow（``finbert-train.yml`` 的
``Download training corpus (Release) and verify integrity`` step：8813 / 1102 / 1102），
只支持 corpus-v1 一种语料；``corpus_tag`` 虽为 dispatch 输入，换用样本量不同的语料即被
行数断言拒绝。维护者裁定：**支持多版本**——corpus Release 随附 manifest（含 split / 行数 /
digest / 版本），workflow 校验时读取 manifest 期望值替代硬编码。

manifest（``corpus_manifest.json``，schema ``finbert-corpus-manifest.v1``）
--------------------------------------------------------------------------
::

    {
      "schema": "finbert-corpus-manifest.v1",
      "corpus_tag": "corpus-v2",
      "corpus_version": "2",
      "splits": {
        "split_train.jsonl": {"rows": 9000, "sha256": "<64 位十六进制>"},
        "split_val.jsonl":   {"rows": 1100, "sha256": "<...>"},
        "split_test.jsonl":  {"rows": 1100, "sha256": "<...>"}
      }
    }

manifest 为行数 / digest 期望值的**唯一权威来源**；``SHA256SUMS.txt`` 仍是 Release 完整性
凭证，两者逐文件交叉校验（拦截「双事实源漂移」）。行数口径 = **非空行计数**，与
``scripts/train_finbert.load_split_file`` 的空行跳过口径对齐（``utf-8-sig`` 读取）。

校验项（fail-closed，任一不符即非零退出）
----------------------------------------
1. manifest 存在且 schema 兼容（未知 schema 拒绝沿用，不按未知语义校验）；
2. manifest 声明的 ``corpus_tag`` == 本次 dispatch 的 ``corpus_tag``（防串档）；``corpus_version``
   为非空字符串（版本是 lineage 的一部分，禁止缺省）；
3. manifest splits 恰为三个规范名（train/val/test）；
4. 每个 split 的非空行数 == manifest 声明值；
5. 每个 split 的 SHA256 == manifest 声明值；
6. manifest 声明的 SHA256 与 ``SHA256SUMS.txt`` 逐文件一致；
7. 语料目录不含非规范 dev 候选文件 ``split_dev.jsonl``（``train_finbert.DEV_CANDIDATES``
   首选该名——混入时训练消费的文件与 manifest 声明错位，fail-closed 拒绝）。

legacy 兜底（维护者裁定 方案 b）
--------------------------------
``corpus-v1`` 已发布且**未随附 manifest**（Release asset 实测无 ``corpus_manifest.json``）。
为保留既有「模型 ↔ 语料」lineage 复现路径（含已发布 ``finbert-gguf-v0.1.0``），对
**显式登记在 ``LEGACY_CORPUS_REGISTRY`` 的已知历史 tag** 采用登记期望值（行数）+
``SHA256SUMS.txt`` digest，并在报告与 Release note **显式声明「该语料无 manifest，采用
登记值，未做 manifest 校验」**——R21：不得把「未校验」伪装成「已校验」，亦不得静默回退
硬编码。任何**未登记且无 manifest** 的 tag 一律 FAIL 并给出修复指引；新增语料版本必须随附
manifest。

Usage:
    # 生成（语料 Release 创建流程，写 corpus_manifest.json 随附 Release；--corpus-version 必填）
    python scripts/corpus_manifest.py build --corpus-dir data/corpus --corpus-tag corpus-v2 --corpus-version 2

    # 校验（workflow 内，替代原硬编码行数断言）
    python scripts/corpus_manifest.py verify \
        --corpus-dir data/corpus \
        --corpus-tag "$CORPUS_TAG" \
        --report stage_b_out/corpus_manifest_report.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.error_classifier import classify_error, classify_severity  # noqa: E402 - sys.path 注入后导入
from utils.sanitizers import DataSanitizer  # noqa: E402 - sys.path 注入后导入

LOGGER = logging.getLogger(__name__)

MANIFEST_FILENAME = "corpus_manifest.json"
MANIFEST_SCHEMA = "finbert-corpus-manifest.v1"
REPORT_SCHEMA = "finbert-corpus-manifest-report.v1"
CHECKSUMS_FILENAME = "SHA256SUMS.txt"
# 语料 split 规范名（与 scripts/train_finbert.py 的 TRAIN_FILE / DEV_CANDIDATES / TEST_FILE 对齐）。
SPLITS = ("split_train.jsonl", "split_val.jsonl", "split_test.jsonl")

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

# legacy 语料显式登记表（维护者裁定 FT-07 方案 b）。
#
# 仅登记**已知历史语料**（已发布且无 manifest）。使用该路径时报告与 Release note 会显式
# 声明「无 manifest，采用登记值，未做 manifest 校验」——登记表**不是**常规通道：未登记的
# tag 若缺 manifest 直接 FAIL（见 verify_corpus）。新增语料必须随附 manifest，禁止扩表规避
# 校验（R21：不得静默回退硬编码）。表内行数取自 corpus-v1 Release note 登记值
# （8813 + 1102 + 1102 = 11017）与 scripts/train_finbert.py docstring 实测记录。
#
# 可选迁移路径（消除 legacy 特例）：为已发布 Release 补传 manifest asset
# （下载语料实测后 `python scripts/corpus_manifest.py build` 生成，`gh release upload
# <tag> corpus_manifest.json --clobber`），验证 verify 走 manifest 通道 PASS 后，
# 将该 tag 从本表移除。补传属对已发布 Release 的外部副作用，须经维护者单独授权执行。
LEGACY_CORPUS_REGISTRY: dict[str, dict[str, int]] = {
    "corpus-v1": {
        "split_train.jsonl": 8813,
        "split_val.jsonl": 1102,
        "split_test.jsonl": 1102,
    },
}


class CorpusManifestError(RuntimeError):
    """manifest 缺失 / 不兼容 / 校验失败（fail-closed 信号）。"""


# --------------------------------------------------------------------------- #
# 纯函数（不触网，供单测覆盖边界）
# --------------------------------------------------------------------------- #
def count_nonempty_lines(path: Path) -> int:
    """统计非空行数（``utf-8-sig`` 读取 + ``strip()``，与 ``train_finbert.load_split_file`` 口径一致）。"""
    total = 0
    with Path(path).open(encoding="utf-8-sig") as handle:
        for raw in handle:
            if raw.strip():
                total += 1
    return total


def sha256_file(path: Path) -> str:
    """分块计算文件 SHA256（16 进制小写）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256sums(path: Path) -> dict[str, str]:
    """解析 ``sha256sum`` 格式清单（``<digest>  <name>``）为 ``{name: digest}``。"""
    sums: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        digest, _, name = stripped.partition(" ")
        if _SHA256_PATTERN.fullmatch(digest.lower()) is None:
            raise CorpusManifestError(f"{CHECKSUMS_FILENAME} 行格式非法: {stripped!r}")
        sums[name.strip().lstrip("*")] = digest.lower()
    return sums


def load_manifest(path: Path) -> dict[str, Any]:
    """读取并校验 manifest 结构与 schema；不兼容 / 非法即抛 ``CorpusManifestError``。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CorpusManifestError(f"manifest 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CorpusManifestError("manifest 顶层必须为 JSON 对象")

    schema = data.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise CorpusManifestError(
            f"manifest schema 不兼容: 期望 {MANIFEST_SCHEMA!r}，实际 {schema!r}"
            "（未知 schema 拒绝沿用，避免按未知语义静默校验）"
        )

    corpus_tag = data.get("corpus_tag")
    if not isinstance(corpus_tag, str) or not corpus_tag.strip():
        raise CorpusManifestError(f"manifest corpus_tag 必须为非空字符串，实际 {corpus_tag!r}")

    # 语料版本（维护者裁定「manifest 含 split/行数/digest/版本」）：须显式声明，禁止缺省，
    # 以便 lineage 区分「同 tag 下不同语料版本」的语义（防版本漂移无法归因）。
    corpus_version = data.get("corpus_version")
    if not isinstance(corpus_version, str) or not corpus_version.strip():
        raise CorpusManifestError(f"manifest corpus_version 必须为非空字符串，实际 {corpus_version!r}")

    splits = data.get("splits")
    if not isinstance(splits, dict):
        raise CorpusManifestError("manifest splits 必须为 JSON 对象")
    if set(splits) != set(SPLITS):
        raise CorpusManifestError(f"manifest splits 必须恰为 {sorted(SPLITS)}，实际 {sorted(splits)}")
    for name, entry in splits.items():
        if not isinstance(entry, dict):
            raise CorpusManifestError(f"manifest splits[{name!r}] 必须为 JSON 对象")
        rows = entry.get("rows")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
            raise CorpusManifestError(f"manifest splits[{name!r}].rows 必须为正整数，实际 {rows!r}")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest.lower()) is None:
            raise CorpusManifestError(f"manifest splits[{name!r}].sha256 必须为 64 位十六进制，实际 {digest!r}")
    return data


def build_manifest(corpus_dir: Path, corpus_tag: str, corpus_version: str) -> dict[str, Any]:
    """由语料目录实测生成 manifest（行数 + SHA256 + 版本）；若同目录存在 ``SHA256SUMS.txt`` 则交叉校验。

    ``corpus_version`` 为必填（manifest schema 要求非空版本字段）：语料版本是 lineage 的一部分，
    禁止缺省导致「同 tag 不同语料」无法归因。
    """
    corpus_dir = Path(corpus_dir)
    if not isinstance(corpus_version, str) or not corpus_version.strip():
        raise CorpusManifestError(f"corpus_version 必须为非空字符串，实际 {corpus_version!r}")
    splits: dict[str, dict[str, Any]] = {}
    for name in SPLITS:
        path = corpus_dir / name
        if not path.is_file():
            raise CorpusManifestError(f"缺少 split 文件: {path}")
        splits[name] = {"rows": count_nonempty_lines(path), "sha256": sha256_file(path)}

    checksums_path = corpus_dir / CHECKSUMS_FILENAME
    if checksums_path.is_file():
        checksums = parse_sha256sums(checksums_path)
        for name, entry in splits.items():
            if name in checksums and checksums[name] != entry["sha256"]:
                raise CorpusManifestError(
                    f"{name} 实测 SHA256 与 {CHECKSUMS_FILENAME} 不一致: {entry['sha256']} != {checksums[name]}"
                )

    return {
        "schema": MANIFEST_SCHEMA,
        "corpus_tag": corpus_tag,
        "corpus_version": corpus_version,
        "splits": splits,
    }


def _expected_from_manifest(
    manifest: dict[str, Any],
    corpus_tag: str,
    checksums: dict[str, str],
    reasons: list[str],
) -> dict[str, tuple[int, str]]:
    """从 manifest 提取期望值，并交叉校验 manifest ↔ SHA256SUMS.txt（防双事实源漂移）。"""
    if manifest["corpus_tag"] != corpus_tag:
        reasons.append(f"manifest corpus_tag {manifest['corpus_tag']!r} != 本次 dispatch {corpus_tag!r}（防串档）")
    expected: dict[str, tuple[int, str]] = {}
    for name, entry in manifest["splits"].items():
        digest = str(entry["sha256"]).lower()
        expected[name] = (int(entry["rows"]), digest)
        if name not in checksums:
            reasons.append(f"{name} 不在 {CHECKSUMS_FILENAME} 中（manifest 与 checksums 事实源不一致）")
        elif checksums[name] != digest:
            reasons.append(f"{name} manifest sha256 {digest} != {CHECKSUMS_FILENAME} {checksums[name]}（双事实源漂移）")
    return expected


def verify_corpus(corpus_dir: Path, corpus_tag: str) -> dict[str, Any]:
    """校验语料 split 与 manifest（或 legacy 登记值）一致性，返回结构化报告。"""
    corpus_dir = Path(corpus_dir)
    reasons: list[str] = []

    checksums_path = corpus_dir / CHECKSUMS_FILENAME
    if not checksums_path.is_file():
        raise CorpusManifestError(f"缺少 {CHECKSUMS_FILENAME}: {checksums_path}（语料 Release 完整性凭证）")
    checksums = parse_sha256sums(checksums_path)

    # dev 候选名防错位（scripts/train_finbert.DEV_CANDIDATES 首选 split_dev.jsonl）：语料目录
    # 混入非规范 dev 候选文件时，训练实际消费该文件而 manifest 只声明 split_val.jsonl——
    # 校验对象与训练消费对象错位（即使全部行数/digest 校验通过），fail-closed 拒绝。
    dev_candidate = corpus_dir / "split_dev.jsonl"
    if dev_candidate.is_file():
        reasons.append(
            "语料目录含非规范 dev 候选文件 split_dev.jsonl（训练将优先消费它而 manifest 未声明，"
            "校验对象与训练输入错位）——请移除该文件或改用 split_val.jsonl 命名"
        )

    manifest_path = corpus_dir / MANIFEST_FILENAME
    manifest_schema: str | None = None
    manifest_version: str | None = None

    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        manifest_state = "manifest"
        manifest_schema = str(manifest["schema"])
        manifest_version = str(manifest["corpus_version"])
        expected = _expected_from_manifest(manifest, corpus_tag, checksums, reasons)
    elif corpus_tag in LEGACY_CORPUS_REGISTRY:
        manifest_state = "legacy-registry"
        registry_rows = LEGACY_CORPUS_REGISTRY[corpus_tag]
        # 登记表结构须完整（恰为三个规范 split）——缺项时清晰报错而非 KeyError 裸露。
        if set(registry_rows) != set(SPLITS):
            raise CorpusManifestError(
                f"legacy registry 对 `{corpus_tag}` 的登记不完整: 须恰为 {sorted(SPLITS)}，实际 {sorted(registry_rows)}"
            )
        expected = {}
        for name in SPLITS:
            if name not in checksums:
                reasons.append(f"{name} 不在 {CHECKSUMS_FILENAME} 中（legacy 语料完整性凭证缺失）")
                continue
            expected[name] = (registry_rows[name], checksums[name])
    else:
        raise CorpusManifestError(
            f"语料 `{corpus_tag}` 未随附 {MANIFEST_FILENAME}，且未登记于 legacy registry——"
            "拒绝静默沿用硬编码期望值。修复：为该语料 Release 附上 manifest"
            f"（`python scripts/corpus_manifest.py build --corpus-dir <dir> --corpus-tag {corpus_tag} "
            "--corpus-version <version>`），"
            "或若确为已知历史语料，将其显式登记入 scripts/corpus_manifest.py 的 LEGACY_CORPUS_REGISTRY。"
        )

    splits_report: dict[str, dict[str, Any]] = {}
    for name in SPLITS:
        path = corpus_dir / name
        if not path.is_file():
            reasons.append(f"缺少 split 文件: {name}")
            continue
        actual_rows = count_nonempty_lines(path)
        actual_sha = sha256_file(path)
        exp = expected.get(name)
        exp_rows, exp_sha = exp if exp is not None else (None, None)
        rows_ok = exp_rows is not None and actual_rows == exp_rows
        sha_ok = exp_sha is not None and actual_sha == exp_sha
        if not rows_ok:
            reasons.append(f"{name} 非空行数 {actual_rows} != 期望 {exp_rows}")
        if not sha_ok:
            reasons.append(f"{name} SHA256 {actual_sha} != 期望 {exp_sha}")
        splits_report[name] = {
            "expected_rows": exp_rows,
            "actual_rows": actual_rows,
            "rows_ok": rows_ok,
            "expected_sha256": exp_sha,
            "actual_sha256": actual_sha,
            "sha256_ok": sha_ok,
        }

    declaration = (
        "该语料无 manifest，采用登记值（legacy registry），未做 manifest 校验"
        if manifest_state == "legacy-registry"
        else f"已按 manifest 声明（schema `{manifest_schema}`）校验行数/digest"
    )
    verdict = "FAIL"
    if not reasons:
        verdict = "LEGACY_REGISTRY" if manifest_state == "legacy-registry" else "PASS"

    return {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_tag": corpus_tag,
        "manifest_state": manifest_state,
        "manifest_schema": manifest_schema,
        "manifest_version": manifest_version,
        "declaration": declaration,
        "splits": splits_report,
        "total_rows": sum(int(item["actual_rows"]) for item in splits_report.values()),
        "verdict": verdict,
        "reasons": reasons,
    }


def _log_failure(detail: str, exc: Exception) -> None:
    """外部 IO / 校验失败按严重度选日志级别（R9：错误消息经 DataSanitizer 脱敏）。"""
    info = classify_error(exc, context="corpus_manifest")
    severity = classify_severity(exc, context="corpus_manifest")
    level = logging.ERROR if severity == "system" else logging.WARNING
    LOGGER.log(
        level,
        "%s | code=%s | %s - %s",
        detail,
        info.get("code"),
        type(exc).__name__,
        DataSanitizer.sanitize_error(exc),
    )


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 68)
    print("FinBERT 训练语料 manifest 校验（FT-07）")
    print("=" * 68)
    print(f"corpus_tag: {report['corpus_tag']} | manifest_state: {report['manifest_state']}")
    print(f"声明: {report['declaration']}")
    for name, item in report["splits"].items():
        print(
            f"  {name}: rows {item['actual_rows']} (期望 {item['expected_rows']}, ok={item['rows_ok']})"
            f" | sha256 {item['actual_sha256'][:12]}… (ok={item['sha256_ok']})"
        )
    print(f"总行数: {report['total_rows']}")
    for reason in report["reasons"]:
        print(f"  - {reason}")
    print(f"VERDICT: {report['verdict']}")
    print("=" * 68)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinBERT 训练语料 manifest 校验 / 生成（FT-07）")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="由语料目录实测生成 manifest")
    build.add_argument("--corpus-dir", required=True, help="含三个 split 文件的语料目录")
    build.add_argument("--corpus-tag", required=True, help="语料 Release tag（写入 manifest）")
    build.add_argument("--corpus-version", required=True, help="语料版本标识（必填，写入 manifest）")
    build.add_argument("--out", default=None, help="输出路径（默认 <corpus-dir>/corpus_manifest.json）")

    verify = sub.add_parser("verify", help="校验语料与 manifest（或 legacy 登记值）一致性")
    verify.add_argument("--corpus-dir", required=True, help="含三个 split 文件与 SHA256SUMS.txt 的语料目录")
    verify.add_argument("--corpus-tag", required=True, help="本次 dispatch 的语料 Release tag")
    verify.add_argument("--report", default="corpus_manifest_report.json", help="报告输出路径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    try:
        if args.command == "build":
            corpus_dir = Path(args.corpus_dir)
            manifest = build_manifest(corpus_dir, args.corpus_tag, args.corpus_version)
            out_path = Path(args.out) if args.out else corpus_dir / MANIFEST_FILENAME
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"manifest 已写入: {out_path}")
            return 0

        report = verify_corpus(Path(args.corpus_dir), args.corpus_tag)
    except (CorpusManifestError, OSError) as exc:
        _log_failure("语料 manifest 校验失败（fail-closed）", exc)
        print(f"::error::语料 manifest 校验失败: {DataSanitizer.sanitize_error(exc)}")
        return 1

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"报告已写入: {report_path}")
    return 0 if report["verdict"] in ("PASS", "LEGACY_REGISTRY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
