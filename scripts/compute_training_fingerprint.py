"""FinBERT 训练指纹计算与指纹驱动发布路由（ADR-0015「内容寻址发布」）。

背景（FT-05 重复发布根因）
--------------------------
finbert-train.yml 的 model_version 为不可变发布标识（FT-05：同 tag 重发被 publish
步骤拒绝），但拒绝发生在训练之后——「输入无变化」的重跑仍会白白消耗 1~3 小时训练。
本脚本把发布判定前移为**内容寻址**：对训练输入计算指纹，与最新已发布 Release
随附的 ``fingerprint.json`` 对比——指纹相同即拒绝（exit 1），不同或不可比才放行。

指纹 spec v1（分量 = 训练输入的完整覆盖）
----------------------------------------
- ``corpus_sums_sha256``: 训练语料 Release 的 ``SHA256SUMS.txt`` **文件内容**哈希
  （语料内容经 ``sha256sum -c`` 闭环绑定到该清单）；
- ``script_sha256``: 脚本白名单**显式枚举**（禁 glob，防意外扩面）逐文件 sha256——
  训练 / 导出 / 补丁 / 双 Gate 五个产线脚本 + 本脚本自举；
- ``workflow_sha256``: ``finbert-train.yml`` 全文件哈希（覆盖依赖 pin、seed、
  超参、步骤编排等全部内联输入）；
- ``base_model_revision`` / ``llama_cpp_commit``: 从 workflow 文本 fail-safe 提取
  （与 workflow_sha256 冗余，但让 diff 可读——能直接看出「换基座」还是「换工具链」）。

四支柱：① canonical JSON（sort_keys + 紧凑分隔符，跨环境字节稳定）；
② runner 单环境闭环（指纹计算与对比在同一次 checkout 内由本脚本完成）；
③ 自举（本脚本自身哈希入白名单，逻辑变更即指纹变更）；
④ fail-safe（任一分量缺失 / 不可提取即 ``FingerprintError``，绝不出具部分指纹）。

**已知豁免（documented）**：runner 镜像（ubuntu-latest 滚动基线）不入指纹——
代价由 Release note 的环境 lineage 段兜底（ImageOS/ImageVersion + 全量依赖清单
SHA256），与 workflow 文件头注释的权衡一致。spec 演进：``FINGERPRINT_SPEC`` 升版
后旧指纹按 ``INCOMPARABLE_SPEC`` 放行（重建基线，不静默判等）。

Usage:
    # job1 指纹路由步骤（Checkout 之后、装依赖之前，python3 即可运行——
    # stdlib-only：F3 复用链传递依赖的 utils/core 模块当前亦全 stdlib，
    # 该链引入三方依赖时路由即 fail-closed，方向安全）
    python3 scripts/compute_training_fingerprint.py route \
        --corpus-tag "$CORPUS_TAG" --repo "$GITHUB_REPOSITORY" \
        [--model-version 0.2.0] [--force-retrain] \
        --output stage_a_out/fingerprint.json

    # 语料下载校验后的锚断言（F2）：本地 SHA256SUMS.txt 必须与指纹锚一致
    python scripts/compute_training_fingerprint.py verify-corpus-anchor \
        --fingerprint stage_a_out/fingerprint.json \
        --corpus-sums data/corpus/SHA256SUMS.txt

退出码：0 = 放行 / 锚断言通过；1 = 输入无变化（业务阻断，route 专属）；
2 = 错误（fail-closed：分量缺失 / gh 故障 / 锚错位 / 版本冲突）。

版本解析：显式 ``--model-version`` 原样使用，对应 Release tag 已存在时秒拒
（FT-05 fail-fast，不等训练后拒绝）；留空则从 semver 最大 ``finbert-gguf-v*`` tag
minor+1 并循环探测 tag 空位（前置消解撞 tag，最终防线仍是 publish 的 FT-05），
无历史 Release 时基线 0.0.0 自增 → 0.1.0。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

FINGERPRINT_SPEC = "v1"

# 脚本白名单：显式枚举（禁 glob——F4 对抗面，防意外扩面/漏面）。
# 第 6 项为本脚本自举：指纹逻辑变更即指纹变更。
SCRIPT_WHITELIST: tuple[str, ...] = (
    "scripts/train_finbert.py",
    "scripts/export_finbert_gguf.py",
    "scripts/patch_llama_cpp_pre_registry.py",
    "scripts/verify_finbert_gguf.py",
    "scripts/verify_finbert_effect_gate.py",
    "scripts/compute_training_fingerprint.py",
)

WORKFLOW_REL_PATH = ".github/workflows/finbert-train.yml"
RELEASE_TAG_PREFIX = "finbert-gguf-v"

# Release 列表一次查询条数上限；命中上限时无法保证取到 semver 最新 → fail-closed
# （语义对齐 scripts/verify_finbert_effect_gate.py RELEASE_LIST_LIMIT）。
RELEASE_LIST_LIMIT = 100

# dispatch 输入的防御性校验（validate_inputs 步骤已白名单一次，此处兜底防直接调用）。
_SEMVER_PATTERN = re.compile(r"\d+\.\d+\.\d+")

# workflow 锚点提取（fail-safe：匹配失败 = 指纹分量缺失 = 拒绝出具指纹）。
_BASE_REVISION_PATTERN = re.compile(r"^\s*FINBERT_BASE_REVISION:\s*([0-9a-f]{40})\s*$", re.MULTILINE)
_LLAMA_REPO_MARKER = "repository: ggml-org/llama.cpp"
_REF_PATTERN = re.compile(r"^\s*ref:\s*([0-9a-f]{40})\s*$", re.MULTILINE)


class FingerprintError(RuntimeError):
    """指纹计算 / 路由 / 锚断言失败（fail-closed 信号）。"""


@dataclass(frozen=True)
class RouteDecision:
    """指纹路由判定。

    ``action``: ``proceed``（继续训练发布）/ ``skip``（输入无变化，阻断）；
    ``decision``: ``NO_BASELINE`` / ``INCOMPARABLE_SPEC`` / ``COMPARABLE_DIFFERENT`` /
    ``COMPARABLE_SAME`` / ``COMPARABLE_SAME_FORCED``；
    ``diff``: 分量级差异（扁平路径 → old/new），仅 COMPARABLE_DIFFERENT 非空；
    ``model_version``: 解析后的版本号（skip 分支不解析，为 ``None``）。
    """

    action: str
    decision: str
    detail: str
    diff: dict[str, dict[str, str | None]]
    model_version: str | None


# --------------------------------------------------------------------------- #
# F3 复用：semver 解析 / 排序直接取自 verify_finbert_effect_gate（零新逻辑）
# --------------------------------------------------------------------------- #
def _load_effect_gate_module() -> ModuleType:
    name = "verify_finbert_effect_gate"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise FingerprintError(f"无法加载 {name}（路径: {path}）")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_effect_gate = _load_effect_gate_module()
parse_semver_tag: Callable[[str], tuple[int, int, int] | None] = _effect_gate.parse_semver_tag
select_latest_release_tag: Callable[[Iterable[str]], str | None] = _effect_gate.select_latest_release_tag


# --------------------------------------------------------------------------- #
# 规范化与哈希（纯函数）
# --------------------------------------------------------------------------- #
def canonical_json(payload: dict[str, Any]) -> str:
    """canonical JSON：键排序 + 紧凑分隔符，跨环境字节稳定（四支柱之一）。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    """文件内容的 SHA256（字节级，与 sha256sum 口径一致）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_sha256(components: dict[str, Any]) -> str:
    """指纹总哈希 = sha256(canonical_json(components))（spec 字段不入自身哈希）。"""
    return hashlib.sha256(canonical_json(components).encode("utf-8")).hexdigest()


def extract_base_model_revision(workflow_text: str) -> str:
    match = _BASE_REVISION_PATTERN.search(workflow_text)
    if match is None:
        raise FingerprintError("workflow 中未找到 FINBERT_BASE_REVISION 40 位锚点——指纹分量缺失，fail-safe")
    return match.group(1)


def extract_llama_cpp_commit(workflow_text: str) -> str:
    """取 ggml-org/llama.cpp 标记之后的首个 40 位 ref（主仓库 checkout 的 ref 不误取）。"""
    marker_pos = workflow_text.find(_LLAMA_REPO_MARKER)
    if marker_pos < 0:
        raise FingerprintError("workflow 中未找到 ggml-org/llama.cpp checkout 标记——指纹分量缺失，fail-safe")
    match = _REF_PATTERN.search(workflow_text, marker_pos)
    if match is None:
        raise FingerprintError("llama.cpp checkout 标记后未找到 40 位 commit ref——指纹分量缺失，fail-safe")
    return match.group(1)


def build_fingerprint(*, repo_root: Path, corpus_sums_path: Path) -> dict[str, Any]:
    """计算指纹（分量缺一即 ``FingerprintError``，不出具部分指纹）。"""
    components: dict[str, Any] = {}
    components["corpus_sums_sha256"] = sha256_file(corpus_sums_path)

    script_hashes: dict[str, str] = {}
    for rel in SCRIPT_WHITELIST:
        path = repo_root / rel
        if not path.is_file():
            raise FingerprintError(f"指纹白名单脚本缺失: {rel}——fail-safe（缺任一分量即失败）")
        script_hashes[rel] = sha256_file(path)
    components["script_sha256"] = script_hashes

    workflow_path = repo_root / WORKFLOW_REL_PATH
    if not workflow_path.is_file():
        raise FingerprintError(f"workflow 缺失: {WORKFLOW_REL_PATH}——指纹分量缺失，fail-safe")
    workflow_text = workflow_path.read_text(encoding="utf-8")
    components["workflow_sha256"] = sha256_file(workflow_path)
    components["base_model_revision"] = extract_base_model_revision(workflow_text)
    components["llama_cpp_commit"] = extract_llama_cpp_commit(workflow_text)

    return {"fingerprint_spec": FINGERPRINT_SPEC, "components": components}


# --------------------------------------------------------------------------- #
# 指纹对比（纯函数）
# --------------------------------------------------------------------------- #
def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _diff_components(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    """分量级 diff（嵌套段逐子键展平为 ``key/sub`` 扁平路径）。"""
    diff: dict[str, dict[str, str | None]] = {}
    for key in sorted(set(current) | set(baseline)):
        cur_value = current.get(key)
        base_value = baseline.get(key)
        if isinstance(cur_value, dict) or isinstance(base_value, dict):
            cur_map = cur_value if isinstance(cur_value, dict) else {}
            base_map = base_value if isinstance(base_value, dict) else {}
            for sub in sorted(set(cur_map) | set(base_map)):
                new = _as_str_or_none(cur_map.get(sub))
                old = _as_str_or_none(base_map.get(sub))
                if new != old:
                    diff[f"{key}/{sub}"] = {"old": old, "new": new}
        else:
            new = _as_str_or_none(cur_value)
            old = _as_str_or_none(base_value)
            if new != old:
                diff[key] = {"old": old, "new": new}
    return diff


def compare_fingerprints(current: dict[str, Any], baseline: Any, *, baseline_tag: str = "") -> RouteDecision:
    """四态判定：NO_BASELINE 之外的三态在此；spec 不兼容 / 结构缺失 → 放行。"""
    # 基线可为任意合法 JSON（[]/null/字符串等）：非对象形态一律 INCOMPARABLE_SPEC
    # 放行，不抛 AttributeError（否则 exit 1 与「输入无变化」业务码重叠，R21）。
    if not isinstance(baseline, dict):
        return RouteDecision(
            action="proceed",
            decision="INCOMPARABLE_SPEC",
            detail=(
                f"锚点 {baseline_tag or '<unknown>'} 指纹文件为 {type(baseline).__name__}（非对象）"
                "→ 不可比，放行（基线版本按既有 tag 自增）"
            ),
            diff={},
            model_version=None,
        )
    baseline_spec = baseline.get("fingerprint_spec")
    baseline_components = baseline.get("components")
    if baseline_spec != FINGERPRINT_SPEC or not isinstance(baseline_components, dict):
        return RouteDecision(
            action="proceed",
            decision="INCOMPARABLE_SPEC",
            detail=(
                f"锚点 {baseline_tag or '<unknown>'} 指纹 spec={baseline_spec!r} 与当前 "
                f"{FINGERPRINT_SPEC} 不兼容或结构缺失 → 不可比，放行（基线版本按既有 tag 自增）"
            ),
            diff={},
            model_version=None,
        )

    diff = _diff_components(current["components"], baseline_components)
    if not diff:
        return RouteDecision(
            action="proceed",
            decision="COMPARABLE_SAME",
            detail=f"输入与最新 Release {baseline_tag} 指纹完全一致",
            diff={},
            model_version=None,
        )
    return RouteDecision(
        action="proceed",
        decision="COMPARABLE_DIFFERENT",
        detail=f"与最新 Release {baseline_tag} 指纹不一致（{len(diff)} 个分量变化）→ 放行训练",
        diff=diff,
        model_version=None,
    )


# --------------------------------------------------------------------------- #
# 版本解析
# --------------------------------------------------------------------------- #
def release_tag_of(model_version: str) -> str:
    return f"{RELEASE_TAG_PREFIX}{model_version}"


def next_minor_version(model_version: str | None) -> str:
    """minor+1（patch 归零）；``None`` 视为基线 0.0.0 → 0.1.0。"""
    if model_version is None:
        return "0.1.0"
    major, minor, _patch = (int(part) for part in model_version.split("."))
    return f"{major}.{minor + 1}.0"


def resolve_auto_version(tags: Iterable[str]) -> str:
    """自动版本：semver 最大 tag minor+1；无模型 Release 时基线 0.0.0 → 0.1.0。"""
    latest = select_latest_release_tag(tags)
    if latest is None:
        return next_minor_version(None)
    return next_minor_version(latest[len(RELEASE_TAG_PREFIX) :])


# --------------------------------------------------------------------------- #
# gh CLI 交互（外部 IO，测试经 monkeypatch 注入）
# --------------------------------------------------------------------------- #
def _run_gh(args: Sequence[str]) -> str:
    """调用 ``gh`` CLI 返回 stdout；非零退出抛 ``FingerprintError``（fail-closed）。

    实现形态对齐 scripts/verify_finbert_effect_gate.py 的 ``_run_gh``（独立定义：
    两脚本各自进程运行，互不共享 gh 会话状态，测试亦互不串扰）。
    """
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise FingerprintError(f"gh 调用失败（exit {proc.returncode}）: gh {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _run_gh_raw(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """保留退出码与 stderr 的 gh 调用（供 tag 存在性等需要区分「未找到」的场景）。"""
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def list_release_tags(repo: str) -> list[str]:
    """列出仓库全部 Release tag（可能含语料等非模型 Release，由调用方过滤）。"""
    raw = _run_gh(["release", "list", "--repo", repo, "--limit", str(RELEASE_LIST_LIMIT), "--json", "tagName"])
    data = json.loads(raw)
    tags = [str(item["tagName"]) for item in data]
    if len(tags) >= RELEASE_LIST_LIMIT:
        raise FingerprintError(f"Release 列表达到查询上限 {RELEASE_LIST_LIMIT}，无法保证取到 semver 最新 → fail-closed")
    return tags


def tag_exists(repo: str, tag: str) -> bool:
    """tag 存在性：仅 ``release not found`` 判 False，其余错误 fail-closed（防误判）。"""
    proc = _run_gh_raw(["release", "view", tag, "--repo", repo])
    if proc.returncode == 0:
        return True
    stderr = proc.stderr or ""
    if "not found" in stderr.lower():
        return False
    raise FingerprintError(
        f"gh release view {tag} 失败（exit {proc.returncode}）: {stderr.strip()}——无法确认 tag 存在性，fail-closed"
    )


def download_release_asset(repo: str, tag: str, pattern: str, dest_dir: Path) -> Path | None:
    """按 asset 名精确下载；零命中返回 ``None``（区分「资产缺失」与「gh 故障」）。

    gh CLI 对 ``--pattern`` 零命中是**非零退出**（stderr "no assets match the
    file pattern"，见 cli/cli download.go），故须经 ``_run_gh_raw`` 保留退出码：
    no-match 类失败判零命中返回 None，其余错误 fail-closed（防把基础设施故障
    当资产缺失）。pattern 为字面文件名（无 glob 特殊字符），零退出 + 文件未落
    地按零命中兜底（双保险）。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    proc = _run_gh_raw(
        ["release", "download", tag, "--repo", repo, "--pattern", pattern, "--dir", str(dest_dir), "--clobber"]
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "no assets match" in stderr.lower() or "no assets to download" in stderr.lower():
            return None
        raise FingerprintError(
            f"gh release download {tag} --pattern {pattern} 失败（exit {proc.returncode}）: {stderr}"
            "——无法区分资产缺失与基础设施故障，fail-closed"
        )
    matches = sorted(dest_dir.glob(pattern))
    return matches[0] if matches else None


def download_corpus_sums(repo: str, corpus_tag: str, dest_dir: Path) -> Path:
    """语料 SHA256SUMS.txt（严格：缺失即失败——语料清单是指纹的必选分量）。"""
    path = download_release_asset(repo, corpus_tag, "SHA256SUMS.txt", dest_dir)
    if path is None:
        raise FingerprintError(f"corpus Release `{corpus_tag}` 缺少 SHA256SUMS.txt 资产——指纹分量缺失，fail-safe")
    return path


def download_baseline_fingerprint(repo: str, tag: str, dest_dir: Path) -> Path | None:
    """基线 Release 的 fingerprint.json（宽容：缺失返回 None → INCOMPARABLE_SPEC 放行）。"""
    return download_release_asset(repo, tag, "fingerprint.json", dest_dir)


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #
def _component_snapshot(components: dict[str, Any]) -> str:
    """分量快照（哈希截断 12 位）——「输入无变化」时给用户看分量级证据。"""
    lines: list[str] = []
    for key in sorted(components):
        value = components[key]
        if isinstance(value, dict):
            for sub in sorted(value):
                lines.append(f"  {key}/{sub}: {str(value[sub])[:12]}…")
        else:
            lines.append(f"  {key}: {str(value)[:12]}…")
    return "\n".join(lines)


def run_route(
    *,
    repo: str,
    corpus_tag: str,
    model_version: str | None,
    force_retrain: bool,
    repo_root: Path,
    output_path: Path,
) -> RouteDecision:
    """指纹路由全流程：算指纹 → 对比 → 版本解析。阻塞时 exit 1 由 main 翻译。"""
    with tempfile.TemporaryDirectory(prefix="finbert-fingerprint-") as tmp:
        tmp_dir = Path(tmp)
        sums_path = download_corpus_sums(repo, corpus_tag, tmp_dir / "corpus")
        fingerprint = build_fingerprint(repo_root=repo_root, corpus_sums_path=sums_path)

        # 先落指纹文件再对比：skip 分支亦保留指纹（排障 artifact 可归因）。
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(fingerprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        tags = list_release_tags(repo)
        latest = select_latest_release_tag(tags)
        if latest is None:
            decision = RouteDecision(
                action="proceed",
                decision="NO_BASELINE",
                detail=(
                    f"无 {RELEASE_TAG_PREFIX}* 历史 Release（共 {len(tags)} 个 Release）——首版发布，未施加指纹去重"
                ),
                diff={},
                model_version=None,
            )
        else:
            baseline_path = download_baseline_fingerprint(repo, latest, tmp_dir / "baseline")
            if baseline_path is None:
                decision = RouteDecision(
                    action="proceed",
                    decision="INCOMPARABLE_SPEC",
                    detail=(
                        f"锚点 {latest} 无 fingerprint.json 资产（旧版产线发布）→ 指纹不可比，"
                        "放行（基线版本按既有 tag 自增）"
                    ),
                    diff={},
                    model_version=None,
                )
            else:
                try:
                    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise FingerprintError(f"基线 fingerprint.json 不可解析（fail-closed）: {exc}") from exc
                decision = compare_fingerprints(fingerprint, baseline, baseline_tag=latest)
                if decision.decision == "COMPARABLE_SAME":
                    if not force_retrain:
                        return RouteDecision(
                            action="skip",
                            decision="COMPARABLE_SAME",
                            detail=(
                                f"输入无变化：与最新 Release {latest} 指纹一致。分量快照（截断 12 位）:\n"
                                f"{_component_snapshot(fingerprint['components'])}"
                            ),
                            diff={},
                            model_version=None,
                        )
                    decision = RouteDecision(
                        action="proceed",
                        decision="COMPARABLE_SAME_FORCED",
                        detail=f"指纹与 {latest} 一致，但 force_retrain=true → 强制重训",
                        diff={},
                        model_version=None,
                    )

        # 版本解析（仅 proceed 分支；skip 分支已在上方返回）。
        resolved: str
        if model_version:
            if _SEMVER_PATTERN.fullmatch(model_version) is None:
                raise FingerprintError(f"model_version 必须为纯数字 semver（如 0.2.0），实际: {model_version!r}")
            resolved = model_version
            if tag_exists(repo, release_tag_of(resolved)):
                raise FingerprintError(
                    f"{release_tag_of(resolved)} 已存在（FT-05 版本不可变）——拒绝发布，请提升 model_version；"
                    "若系首次发布中断致资产不完整，请人工删除该 Release（留痕）后重跑"
                )
        else:
            resolved = resolve_auto_version(tags)
            while tag_exists(repo, release_tag_of(resolved)):
                resolved = next_minor_version(resolved)

        return replace(decision, model_version=resolved)


# --------------------------------------------------------------------------- #
# F2 语料锚断言
# --------------------------------------------------------------------------- #
def verify_corpus_anchor(fingerprint_path: Path, corpus_sums_path: Path) -> None:
    """断言实际参与训练的 SHA256SUMS.txt 与指纹锚一致（错位 fail-fast）。

    防护面：corpus Release 资产在「路由步骤下载」与「语料步骤下载」之间被替换
    （tag 不可变但资产可编辑）时，指纹锚与训练输入错位——此处 fail-fast 阻断。
    """
    if not fingerprint_path.is_file():
        raise FingerprintError(f"指纹文件缺失: {fingerprint_path}——语料锚断言不可执行")
    if not corpus_sums_path.is_file():
        raise FingerprintError(f"语料 SHA256SUMS.txt 缺失: {corpus_sums_path}——语料锚断言不可执行")
    try:
        fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FingerprintError(f"指纹文件不可解析: {fingerprint_path}（{exc}）") from exc
    if not isinstance(fingerprint, dict) or not isinstance(fingerprint.get("components"), dict):
        raise FingerprintError(f"指纹文件结构非法（fingerprint/components 须为 JSON object）: {fingerprint_path}")
    expected = fingerprint["components"].get("corpus_sums_sha256")
    if not isinstance(expected, str):
        raise FingerprintError(f"指纹文件缺少 corpus_sums_sha256 分量（须为字符串）: {fingerprint_path}")
    actual = sha256_file(corpus_sums_path)
    if expected != actual:
        raise FingerprintError(
            "语料锚断言失败（F2）: 本地 SHA256SUMS.txt 与指纹锚不一致——"
            f"实际 {str(actual)[:12]}… 期望 {str(expected)[:12]}…，语料与指纹错位，fail-fast"
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit_github_outputs(decision: RouteDecision) -> None:
    """写 GITHUB_OUTPUT（供后续步骤引用 model_version / release_tag）并同步打印。"""
    lines = [f"action={decision.action}", f"decision={decision.decision}"]
    if decision.model_version is not None:
        lines.append(f"model_version={decision.model_version}")
        lines.append(f"release_tag={release_tag_of(decision.model_version)}")
    print("\n".join(lines))
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with Path(gh_output).open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinBERT 训练指纹计算与指纹驱动发布路由（ADR-0015）")
    sub = parser.add_subparsers(dest="command", required=True)

    route_parser = sub.add_parser("route", help="计算指纹并与最新 Release 对比，输出路由判定与解析版本")
    route_parser.add_argument("--corpus-tag", required=True, help="训练语料 Release tag")
    route_parser.add_argument("--repo", required=True, help="GitHub 仓库 owner/name")
    route_parser.add_argument("--model-version", default=None, help="显式模型版本（留空则自动 minor+1）")
    route_parser.add_argument("--force-retrain", action="store_true", help="指纹一致时仍强制重训")
    route_parser.add_argument("--repo-root", default=None, help="仓库根目录（默认按脚本位置推导）")
    route_parser.add_argument("--output", default="stage_a_out/fingerprint.json", help="指纹 JSON 输出路径")

    anchor_parser = sub.add_parser("verify-corpus-anchor", help="F2：断言本地语料 sums 与指纹锚一致")
    anchor_parser.add_argument("--fingerprint", required=True, help="route 步骤产出的 fingerprint.json")
    anchor_parser.add_argument("--corpus-sums", required=True, help="实际下载的 SHA256SUMS.txt")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "route":
            decision = run_route(
                repo=args.repo,
                corpus_tag=args.corpus_tag,
                model_version=args.model_version or None,
                force_retrain=bool(args.force_retrain),
                repo_root=Path(args.repo_root) if args.repo_root else _REPO_ROOT,
                output_path=Path(args.output),
            )
            _emit_github_outputs(decision)
            print(f"指纹路由判定: {decision.decision} → {decision.action}")
            print(decision.detail)
            if decision.diff:
                print("分量 diff:")
                for path in sorted(decision.diff):
                    change = decision.diff[path]
                    print(f"  {path}: {change['old']} → {change['new']}")
            if decision.action == "skip":
                print(
                    "::error::训练输入无变化——与最新已发布 Release 的训练指纹一致，"
                    "拒绝重复发布（ADR-0015 内容寻址去重）；如确需重训请勾选 force_retrain"
                )
                return 1
            return 0

        verify_corpus_anchor(Path(args.fingerprint), Path(args.corpus_sums))
        print("语料锚断言通过: 本地 SHA256SUMS.txt 与指纹锚一致")
        return 0
    except FingerprintError as exc:
        print(f"::error::{exc}")
        return 2
    except OSError as exc:
        # 指纹落盘 / sums 哈希读取 / GITHUB_OUTPUT 写入等 IO 故障：fail-closed（exit 2），
        # 不以裸 traceback 的 exit 1 与「输入无变化」业务码混淆（ADR-0015 退出码契约）。
        print(f"::error::IO 故障: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
