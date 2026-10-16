"""FinBERT 训练指纹与指纹驱动发布路由（ADR-0015）单元测试。

守护目标（方案 v2，经对抗性检视修订）：
- **指纹 spec v1 完整性**：分量 = 语料 SHA256SUMS.txt 内容哈希 + 脚本白名单
  （5 产线脚本 + compute 脚本自举）逐文件 sha256 + workflow sha256 +
  base_model_revision + llama_cpp_commit（后两者从 workflow 文本 fail-safe 提取）；
  任何分量缺失/不可提取即 ``FingerprintError``（缺任一分量即失败，不得放行）。
- **canonical JSON 稳定性**：``sort_keys`` + 紧凑分隔符，键插入顺序不影响哈希。
- **对比四态**：``NO_BASELINE``（无历史 Release）/ ``INCOMPARABLE_SPEC``（锚点无
  fingerprint.json 资产或 spec 不兼容——放行）/ ``COMPARABLE_DIFFERENT``（放行 +
  分量 diff）/ ``COMPARABLE_SAME``（阻断：exit 1，除非 ``force_retrain``）。
- **版本解析**：显式 model_version 原样使用且 tag 已存在时秒拒（FT-05 fail-fast）；
  留空则从 semver 最大 tag minor+1 并**循环探测 tag 空位**（防撞 tag），无历史时
  基线 0.0.0 → 0.1.0。
- **F2 语料锚断言**：``verify-corpus-anchor`` 比对实际下载的 SHA256SUMS.txt 与
  指纹锚哈希，错位 fail-fast（exit 2）。
- **退出码契约**：0=放行；1=输入无变化（业务阻断）；2=错误（fail-closed）。
- **gh 层 fail-safe**：pattern 零命中（资产缺失）与 gh 调用失败（基础设施）区分
  处理——语料 sums 缺失即失败；基线 fingerprint.json 缺失按不可比放行；tag 存在性
  查询仅在「release not found」时判 False，其余错误 fail-closed。

全部为纯函数 / monkeypatch 注入的离线测试，不触网、不触发任何训练 / 发布副作用。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "compute_training_fingerprint.py"

BASE_REVISION = "804a488abcece861e9241d55e9d6fd1f867ebee1"
LLAMA_COMMIT = "c1d0e7a004015f23bc0233470b747b596f29b264"
OTHER_COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

SCRIPT_NAMES = (
    "train_finbert.py",
    "export_finbert_gguf.py",
    "patch_llama_cpp_pre_registry.py",
    "verify_finbert_gguf.py",
    "verify_finbert_effect_gate.py",
    "compute_training_fingerprint.py",
)


@pytest.fixture(scope="module")
def fp_module():
    # 以文件路径加载脚本模块（scripts 非包），供纯函数 / 编排断言。
    spec = importlib.util.spec_from_file_location("compute_training_fingerprint", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 夹具：仿真 repo 骨架（脚本白名单 + workflow 锚点行 + 语料 sums）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    for i, name in enumerate(SCRIPT_NAMES):
        (root / "scripts" / name).write_text(f"# {name} content v{i}\n", encoding="utf-8")
    (root / ".github" / "workflows" / "finbert-train.yml").write_text(
        _workflow_snippet(BASE_REVISION, LLAMA_COMMIT), encoding="utf-8"
    )
    return root


def _workflow_snippet(base_revision: str, llama_commit: str) -> str:
    return (
        "jobs:\n"
        "  train-export-gate:\n"
        "    steps:\n"
        f"      - uses: actions/checkout@{OTHER_COMMIT}  # v7.0.1\n"
        "        with:\n"
        f"          ref: {OTHER_COMMIT}\n"
        "      - name: Download base model (HF Hub pinned revision)\n"
        "        env:\n"
        "          FINBERT_BASE_REPO: valuesimplex-ai-lab/FinBERT2-base\n"
        f"          FINBERT_BASE_REVISION: {base_revision}\n"
        "      - name: Checkout llama.cpp v0.3.0\n"
        "        with:\n"
        "          repository: ggml-org/llama.cpp\n"
        f"          ref: {llama_commit}\n"
        "          path: llama.cpp\n"
    )


@pytest.fixture()
def corpus_sums(tmp_path: Path) -> Path:
    sums = tmp_path / "corpus" / "SHA256SUMS.txt"
    sums.parent.mkdir(parents=True)
    sums.write_text("aaa...  train.jsonl\nbbb...  dev.jsonl\n", encoding="utf-8")
    return sums


def _build(fp_module, fake_repo: Path, sums: Path) -> dict[str, Any]:
    return fp_module.build_fingerprint(repo_root=fake_repo, corpus_sums_path=sums)


def _baseline_from(fp_module, fingerprint: dict[str, Any]) -> dict[str, Any]:
    """以当前指纹为模板构造基线（默认与当前全同）。"""
    return json.loads(json.dumps(fingerprint, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# F3 复用 + 规范常量
# --------------------------------------------------------------------------- #
def test_semver_helpers_reused_from_effect_gate(fp_module) -> None:
    # F3：semver 解析/排序逻辑直接复用 verify_finbert_effect_gate（含 1.10 > 1.9
    # 的语义排序），不实现第二份。
    assert fp_module.parse_semver_tag("finbert-gguf-v1.10.0") == (1, 10, 0)
    tags = ["finbert-gguf-v1.9.0", "finbert-gguf-v1.10.0", "corpus-v1"]
    assert fp_module.select_latest_release_tag(tags) == "finbert-gguf-v1.10.0"
    assert fp_module.select_latest_release_tag(["corpus-v1"]) is None


def test_spec_and_whitelist_shape(fp_module) -> None:
    assert fp_module.FINGERPRINT_SPEC == "v1"
    # 白名单为显式枚举（禁 glob 展开），含产线 5 脚本 + compute 脚本自举。
    assert len(fp_module.SCRIPT_WHITELIST) == 6
    assert "scripts/compute_training_fingerprint.py" in fp_module.SCRIPT_WHITELIST
    assert "scripts/train_finbert.py" in fp_module.SCRIPT_WHITELIST
    assert all(not any(ch in rel for ch in "*?[") for rel in fp_module.SCRIPT_WHITELIST)


# --------------------------------------------------------------------------- #
# canonical JSON 与哈希
# --------------------------------------------------------------------------- #
def test_canonical_json_sorted_and_compact(fp_module) -> None:
    assert fp_module.canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_stable_across_insertion_order(fp_module) -> None:
    one = fp_module.canonical_json({"a": 1, "b": {"x": 1, "y": 2}})
    two = fp_module.canonical_json({"b": {"y": 2, "x": 1}, "a": 1})
    assert one == two


def test_sha256_file_hashes_exact_bytes(fp_module, tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_bytes(b"abc\n")
    assert fp_module.sha256_file(p) == hashlib.sha256(b"abc\n").hexdigest()


def test_fingerprint_sha256_deterministic_and_content_sensitive(fp_module) -> None:
    components = {"a": "1", "b": "2"}
    first = fp_module.fingerprint_sha256(components)
    assert first == fp_module.fingerprint_sha256({"b": "2", "a": "1"})
    assert first != fp_module.fingerprint_sha256({"a": "1", "b": "3"})


# --------------------------------------------------------------------------- #
# workflow 锚点提取（fail-safe）
# --------------------------------------------------------------------------- #
def test_extract_base_model_revision(fp_module) -> None:
    text = _workflow_snippet(BASE_REVISION, LLAMA_COMMIT)
    assert fp_module.extract_base_model_revision(text) == BASE_REVISION


def test_extract_base_model_revision_missing_fails(fp_module) -> None:
    text = _workflow_snippet("short", LLAMA_COMMIT)
    with pytest.raises(fp_module.FingerprintError, match="FINBERT_BASE_REVISION"):
        fp_module.extract_base_model_revision(text)


def test_extract_llama_cpp_commit_finds_ref_after_marker(fp_module) -> None:
    text = _workflow_snippet(BASE_REVISION, LLAMA_COMMIT)
    assert fp_module.extract_llama_cpp_commit(text) == LLAMA_COMMIT


def test_extract_llama_ignores_ref_before_marker(fp_module) -> None:
    # 对抗面：主仓库 checkout / 其他 action 的 40 位 ref 不得被误取——
    # 只认 ggml-org/llama.cpp 标记之后的首个 ref 行。
    text = _workflow_snippet(BASE_REVISION, LLAMA_COMMIT)  # 已含 marker 前的另一 ref
    assert OTHER_COMMIT != LLAMA_COMMIT
    assert fp_module.extract_llama_cpp_commit(text) == LLAMA_COMMIT


def test_extract_llama_cpp_missing_marker_or_ref_fails(fp_module) -> None:
    with pytest.raises(fp_module.FingerprintError, match="llama.cpp"):
        fp_module.extract_llama_cpp_commit("steps: []\n")
    text = _workflow_snippet(BASE_REVISION, LLAMA_COMMIT).replace(f"ref: {LLAMA_COMMIT}\n", "")
    with pytest.raises(fp_module.FingerprintError, match="llama.cpp"):
        fp_module.extract_llama_cpp_commit(text)


# --------------------------------------------------------------------------- #
# build_fingerprint：分量完整性与 fail-safe
# --------------------------------------------------------------------------- #
def test_build_fingerprint_components_complete(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    assert fp["fingerprint_spec"] == "v1"
    components = fp["components"]
    assert components["corpus_sums_sha256"] == fp_module.sha256_file(corpus_sums)
    assert components["workflow_sha256"] == fp_module.sha256_file(
        fake_repo / ".github" / "workflows" / "finbert-train.yml"
    )
    assert components["base_model_revision"] == BASE_REVISION
    assert components["llama_cpp_commit"] == LLAMA_COMMIT
    scripts = components["script_sha256"]
    assert set(scripts) == {f"scripts/{name}" for name in SCRIPT_NAMES}
    assert scripts["scripts/train_finbert.py"] == fp_module.sha256_file(fake_repo / "scripts" / "train_finbert.py")


def test_build_fingerprint_missing_script_fails(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    (fake_repo / "scripts" / "train_finbert.py").unlink()
    with pytest.raises(fp_module.FingerprintError, match="train_finbert.py"):
        _build(fp_module, fake_repo, corpus_sums)


def test_build_fingerprint_missing_workflow_fails(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    (fake_repo / ".github" / "workflows" / "finbert-train.yml").unlink()
    with pytest.raises(fp_module.FingerprintError, match="finbert-train.yml"):
        _build(fp_module, fake_repo, corpus_sums)


def test_fingerprint_changes_when_input_changes(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    before = fp_module.fingerprint_sha256(_build(fp_module, fake_repo, corpus_sums)["components"])
    text = (fake_repo / "scripts" / "train_finbert.py").read_text(encoding="utf-8")
    (fake_repo / "scripts" / "train_finbert.py").write_text(text + "# changed\n", encoding="utf-8")
    after = fp_module.fingerprint_sha256(_build(fp_module, fake_repo, corpus_sums)["components"])
    assert before != after


# --------------------------------------------------------------------------- #
# compare_fingerprints 四态 + 分量 diff
# --------------------------------------------------------------------------- #
def test_compare_same_has_empty_diff(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    decision = fp_module.compare_fingerprints(fp, _baseline_from(fp_module, fp), baseline_tag="finbert-gguf-v0.1.0")
    assert decision.decision == "COMPARABLE_SAME"
    assert decision.diff == {}


def test_compare_component_changed_reports_old_new(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline = _baseline_from(fp_module, fp)
    baseline["components"]["corpus_sums_sha256"] = "0" * 64
    decision = fp_module.compare_fingerprints(fp, baseline)
    assert decision.decision == "COMPARABLE_DIFFERENT"
    entry = decision.diff["corpus_sums_sha256"]
    assert entry == {"old": "0" * 64, "new": fp["components"]["corpus_sums_sha256"]}


def test_compare_script_subchange_uses_flat_path(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline = _baseline_from(fp_module, fp)
    baseline["components"]["script_sha256"]["scripts/train_finbert.py"] = "1" * 64
    decision = fp_module.compare_fingerprints(fp, baseline)
    assert decision.decision == "COMPARABLE_DIFFERENT"
    assert decision.diff["script_sha256/scripts/train_finbert.py"]["old"] == "1" * 64


def test_compare_added_and_removed_subkeys(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline = _baseline_from(fp_module, fp)
    del baseline["components"]["script_sha256"]["scripts/train_finbert.py"]
    baseline["components"]["script_sha256"]["scripts/ghost.py"] = "2" * 64
    decision = fp_module.compare_fingerprints(fp, baseline)
    assert decision.diff["script_sha256/scripts/train_finbert.py"]["old"] is None
    assert decision.diff["script_sha256/scripts/ghost.py"]["new"] is None


def test_compare_spec_mismatch_is_incomparable_and_proceeds(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline = _baseline_from(fp_module, fp)
    baseline["fingerprint_spec"] = "v0"
    decision = fp_module.compare_fingerprints(fp, baseline)
    assert decision.action == "proceed"
    assert decision.decision == "INCOMPARABLE_SPEC"


def test_compare_malformed_baseline_is_incomparable(fp_module, fake_repo: Path, corpus_sums: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    for baseline in ({"components": "not-a-dict"}, {"fingerprint_spec": "v1"}, {}):
        decision = fp_module.compare_fingerprints(fp, baseline)
        assert decision.decision == "INCOMPARABLE_SPEC", baseline
    # 非 dict 的合法 JSON（[]/null/字符串/数值）同样 INCOMPARABLE_SPEC 放行，
    # 不抛 AttributeError（否则 main 以 exit 1 退出，与「输入无变化」业务码重叠）
    for baseline in ([], None, "fingerprint", 42):
        decision = fp_module.compare_fingerprints(fp, baseline)
        assert decision.action == "proceed"
        assert decision.decision == "INCOMPARABLE_SPEC", baseline


# --------------------------------------------------------------------------- #
# 版本解析
# --------------------------------------------------------------------------- #
def test_next_minor_version(fp_module) -> None:
    assert fp_module.next_minor_version(None) == "0.1.0"  # 基线 0.0.0 自增
    assert fp_module.next_minor_version("0.1.5") == "0.2.0"
    assert fp_module.next_minor_version("1.2.3") == "1.3.0"


def test_resolve_auto_version_uses_latest_semver_tag(fp_module) -> None:
    tags = ["corpus-v1", "finbert-gguf-v1.9.0", "finbert-gguf-v1.10.0"]
    assert fp_module.resolve_auto_version(tags) == "1.11.0"


def test_resolve_auto_version_without_model_tags(fp_module) -> None:
    assert fp_module.resolve_auto_version(["corpus-v1", "docs-v2"]) == "0.1.0"


def test_release_tag_of(fp_module) -> None:
    assert fp_module.release_tag_of("0.2.0") == "finbert-gguf-v0.2.0"


# --------------------------------------------------------------------------- #
# gh 层：下载 / tag 存在性（fail-safe 语义）
# --------------------------------------------------------------------------- #
class _GhProc:
    """subprocess.CompletedProcess 最小替身（保留退出码 / stderr / stdout）。"""

    def __init__(self, returncode: int, stderr: str = "", stdout: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_download_release_asset_returns_path_or_none(monkeypatch, fp_module, tmp_path: Path) -> None:
    # gh release download --pattern 零命中是**非零退出**（stderr "no assets
    # match the file pattern"，见 cli/cli download.go）——按真行为造桩：
    # no-match → None（资产缺失），基础设施故障 → fail-closed，成功 → path。
    dest = tmp_path / "dl"

    def fake_gh_raw(args):
        assert "--pattern" in args and "fingerprint.json" in args
        (dest / "fingerprint.json").write_text("{}", encoding="utf-8")
        return _GhProc(0)

    monkeypatch.setattr(fp_module, "_run_gh_raw", fake_gh_raw)
    assert fp_module.download_release_asset("o/r", "t", "fingerprint.json", dest) == dest / "fingerprint.json"

    monkeypatch.setattr(
        fp_module,
        "_run_gh_raw",
        lambda args: _GhProc(1, stderr="gh: no assets match the file search pattern"),
    )
    assert fp_module.download_release_asset("o/r", "t", "fingerprint.json", tmp_path / "empty") is None

    monkeypatch.setattr(fp_module, "_run_gh_raw", lambda args: _GhProc(1, stderr="connection refused"))
    with pytest.raises(fp_module.FingerprintError, match="fail-closed"):
        fp_module.download_release_asset("o/r", "t", "fingerprint.json", tmp_path / "err")


def test_download_corpus_sums_strict_when_absent(monkeypatch, fp_module, tmp_path: Path) -> None:
    monkeypatch.setattr(
        fp_module,
        "_run_gh_raw",
        lambda args: _GhProc(1, stderr="no assets match the file search pattern"),
    )
    with pytest.raises(fp_module.FingerprintError, match="SHA256SUMS.txt"):
        fp_module.download_corpus_sums("o/r", "corpus-v1", tmp_path / "corpus")


def test_tag_exists_distinguishes_not_found_from_infra_error(monkeypatch, fp_module) -> None:
    monkeypatch.setattr(fp_module, "_run_gh_raw", lambda args: _GhProc(0))
    assert fp_module.tag_exists("o/r", "finbert-gguf-v0.1.0") is True
    monkeypatch.setattr(fp_module, "_run_gh_raw", lambda args: _GhProc(1, stderr="release not found"))
    assert fp_module.tag_exists("o/r", "finbert-gguf-v0.9.9") is False
    # 其余非零退出（网络/鉴权故障）不得误判为「不存在」→ fail-closed
    monkeypatch.setattr(fp_module, "_run_gh_raw", lambda args: _GhProc(1, stderr="connection refused"))
    with pytest.raises(fp_module.FingerprintError, match="无法确认 tag 存在性") as excinfo:
        fp_module.tag_exists("o/r", "finbert-gguf-v0.1.0")
    assert "connection refused" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# run_route 编排（patch gh 依赖，验证四态 + 版本解析）
# --------------------------------------------------------------------------- #
def _patch_gh_layer(
    monkeypatch,
    fp_module,
    *,
    tags: list[str],
    sums: Path,
    baseline_fp: Path | None,
    existing: set[str],
) -> None:
    monkeypatch.setattr(fp_module, "list_release_tags", lambda repo: list(tags))
    monkeypatch.setattr(fp_module, "download_corpus_sums", lambda repo, tag, dest: sums)
    if baseline_fp is None:
        monkeypatch.setattr(fp_module, "download_baseline_fingerprint", lambda repo, tag, dest: None)
    else:
        monkeypatch.setattr(fp_module, "download_baseline_fingerprint", lambda repo, tag, dest: baseline_fp)
    monkeypatch.setattr(fp_module, "tag_exists", lambda repo, tag: tag in existing)


def test_route_no_baseline_proceeds_auto_0_1_0(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    _patch_gh_layer(monkeypatch, fp_module, tags=["corpus-v1"], sums=corpus_sums, baseline_fp=None, existing=set())
    out = tmp_path / "stage_a_out" / "fingerprint.json"
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=False,
        repo_root=fake_repo,
        output_path=out,
    )
    assert (decision.action, decision.decision) == ("proceed", "NO_BASELINE")
    assert decision.model_version == "0.1.0"
    assert out.is_file() and json.loads(out.read_text(encoding="utf-8"))["fingerprint_spec"] == "v1"


def test_route_baseline_without_fingerprint_asset_incomparable(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    # 锚点 Release（finbert-gguf-v0.1.0）由旧版产线发布、无 fingerprint.json 资产
    # → 不可比放行；版本从既有 tag 自增（0.2.0）。
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=["finbert-gguf-v0.1.0"],
        sums=corpus_sums,
        baseline_fp=None,
        existing=set(),
    )
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=False,
        repo_root=fake_repo,
        output_path=tmp_path / "fp.json",
    )
    assert (decision.action, decision.decision) == ("proceed", "INCOMPARABLE_SPEC")
    assert decision.model_version == "0.2.0"


def test_route_same_fingerprint_blocks_without_force(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(_baseline_from(fp_module, fp), ensure_ascii=False), encoding="utf-8")
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=["finbert-gguf-v0.1.0"],
        sums=corpus_sums,
        baseline_fp=baseline_file,
        existing=set(),
    )
    out = tmp_path / "fp.json"
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=False,
        repo_root=fake_repo,
        output_path=out,
    )
    assert (decision.action, decision.decision) == ("skip", "COMPARABLE_SAME")
    # skip 分支仍须先落指纹文件（排障 artifact 可归因）
    assert out.is_file()


def test_route_same_fingerprint_forced_proceeds(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(_baseline_from(fp_module, fp), ensure_ascii=False), encoding="utf-8")
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=["finbert-gguf-v0.1.0"],
        sums=corpus_sums,
        baseline_fp=baseline_file,
        existing=set(),
    )
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=True,
        repo_root=fake_repo,
        output_path=tmp_path / "fp.json",
    )
    assert (decision.action, decision.decision) == ("proceed", "COMPARABLE_SAME_FORCED")
    assert decision.model_version == "0.2.0"


def test_route_different_fingerprint_proceeds_with_diff(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline = _baseline_from(fp_module, fp)
    baseline["components"]["corpus_sums_sha256"] = "f" * 64
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=["finbert-gguf-v0.1.0"],
        sums=corpus_sums,
        baseline_fp=baseline_file,
        existing=set(),
    )
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=False,
        repo_root=fake_repo,
        output_path=tmp_path / "fp.json",
    )
    assert (decision.action, decision.decision) == ("proceed", "COMPARABLE_DIFFERENT")
    assert "corpus_sums_sha256" in decision.diff
    assert decision.model_version == "0.2.0"


def test_route_explicit_version_existing_tag_rejected(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    # FT-05 fail-fast：显式版本对应的 Release tag 已存在 → 秒拒（不等训练 1~3h 后
    # 由 publish 步骤拒绝）。
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=[],
        sums=corpus_sums,
        baseline_fp=None,
        existing={"finbert-gguf-v0.2.0"},
    )
    with pytest.raises(fp_module.FingerprintError, match="已存在"):
        fp_module.run_route(
            repo="o/r",
            corpus_tag="corpus-v1",
            model_version="0.2.0",
            force_retrain=False,
            repo_root=fake_repo,
            output_path=tmp_path / "fp.json",
        )


def test_route_explicit_version_valid_passthrough(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    _patch_gh_layer(monkeypatch, fp_module, tags=[], sums=corpus_sums, baseline_fp=None, existing=set())
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version="7.7.7",
        force_retrain=False,
        repo_root=fake_repo,
        output_path=tmp_path / "fp.json",
    )
    # 显式版本原样透传（无历史 Release → NO_BASELINE，不阻断）
    assert decision.action == "proceed"
    assert decision.decision == "NO_BASELINE"
    assert decision.model_version == "7.7.7"


def test_route_explicit_version_invalid_semver_rejected(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    _patch_gh_layer(monkeypatch, fp_module, tags=[], sums=corpus_sums, baseline_fp=None, existing=set())
    with pytest.raises(fp_module.FingerprintError, match="semver"):
        fp_module.run_route(
            repo="o/r",
            corpus_tag="corpus-v1",
            model_version="v0.2",
            force_retrain=False,
            repo_root=fake_repo,
            output_path=tmp_path / "fp.json",
        )


def test_route_auto_version_probes_tag_vacancy_loop(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    # 循环探测：0.2.0 被占 → 继续自增到 0.3.0（防 2h 训练后撞 tag 的前置消解）。
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=["finbert-gguf-v0.1.0"],
        sums=corpus_sums,
        baseline_fp=None,
        existing={"finbert-gguf-v0.2.0"},
    )
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=False,
        repo_root=fake_repo,
        output_path=tmp_path / "fp.json",
    )
    assert decision.model_version == "0.3.0"


def test_route_baseline_spec_mismatch_proceeds(
    monkeypatch, fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path
) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    baseline = _baseline_from(fp_module, fp)
    baseline["fingerprint_spec"] = "v0"
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    _patch_gh_layer(
        monkeypatch,
        fp_module,
        tags=["finbert-gguf-v0.1.0"],
        sums=corpus_sums,
        baseline_fp=baseline_file,
        existing=set(),
    )
    decision = fp_module.run_route(
        repo="o/r",
        corpus_tag="corpus-v1",
        model_version=None,
        force_retrain=False,
        repo_root=fake_repo,
        output_path=tmp_path / "fp.json",
    )
    assert (decision.action, decision.decision) == ("proceed", "INCOMPARABLE_SPEC")


# --------------------------------------------------------------------------- #
# verify-corpus-anchor（F2 语料锚断言）
# --------------------------------------------------------------------------- #
def test_verify_corpus_anchor_match(fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    fp_file = tmp_path / "fingerprint.json"
    fp_file.write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    fp_module.verify_corpus_anchor(fp_file, corpus_sums)  # 不抛即通过


def test_verify_corpus_anchor_mismatch_fails(fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    fp["components"]["corpus_sums_sha256"] = "0" * 64
    fp_file = tmp_path / "fingerprint.json"
    fp_file.write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(fp_module.FingerprintError, match="语料锚"):
        fp_module.verify_corpus_anchor(fp_file, corpus_sums)


def test_verify_corpus_anchor_missing_inputs_fails(fp_module, tmp_path: Path) -> None:
    # 指纹文件缺失分支 fail-fast（错误消息须含路径，便于归因）
    with pytest.raises(fp_module.FingerprintError, match="指纹文件缺失") as excinfo:
        fp_module.verify_corpus_anchor(tmp_path / "no.json", tmp_path / "no-sums.txt")
    assert "no.json" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# main CLI：退出码契约 + GITHUB_OUTPUT
# --------------------------------------------------------------------------- #
def test_main_route_skip_exit_1_with_no_change_message(
    monkeypatch, fp_module, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    sample = fp_module.RouteDecision(
        action="skip", decision="COMPARABLE_SAME", detail="指纹一致", diff={}, model_version=None
    )
    monkeypatch.setattr(
        fp_module,
        "run_route",
        lambda **_kw: sample,
    )
    rc = fp_module.main(["route", "--corpus-tag", "corpus-v1", "--repo", "o/r", "--output", str(tmp_path / "fp.json")])
    assert rc == 1
    assert "输入无变化" in capsys.readouterr().out


def test_main_route_proceed_exit_0_and_github_outputs(monkeypatch, fp_module, tmp_path: Path) -> None:
    decision = fp_module.RouteDecision(
        action="proceed",
        decision="COMPARABLE_DIFFERENT",
        detail="输入有变化",
        diff={"corpus_sums_sha256": {"old": "a", "new": "b"}},
        model_version="0.2.0",
    )
    monkeypatch.setattr(fp_module, "run_route", lambda **_kw: decision)
    gh_output = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    rc = fp_module.main(["route", "--corpus-tag", "corpus-v1", "--repo", "o/r", "--output", str(tmp_path / "fp.json")])
    assert rc == 0
    lines = gh_output.read_text(encoding="utf-8").splitlines()
    assert "action=proceed" in lines
    assert "decision=COMPARABLE_DIFFERENT" in lines
    assert "model_version=0.2.0" in lines
    assert "release_tag=finbert-gguf-v0.2.0" in lines


def test_main_route_error_exit_2(monkeypatch, fp_module, tmp_path: Path) -> None:
    def _boom(**_kw):
        raise fp_module.FingerprintError("语料 Release 缺少 SHA256SUMS.txt")

    monkeypatch.setattr(fp_module, "run_route", _boom)
    rc = fp_module.main(["route", "--corpus-tag", "corpus-v1", "--repo", "o/r", "--output", str(tmp_path / "fp.json")])
    assert rc == 2


def test_main_verify_corpus_anchor_exit_codes(fp_module, fake_repo: Path, corpus_sums: Path, tmp_path: Path) -> None:
    fp = _build(fp_module, fake_repo, corpus_sums)
    fp_file = tmp_path / "fingerprint.json"
    fp_file.write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    argv = ["verify-corpus-anchor", "--fingerprint", str(fp_file), "--corpus-sums", str(corpus_sums)]
    assert fp_module.main(argv) == 0

    fp["components"]["corpus_sums_sha256"] = "0" * 64
    fp_file.write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    assert fp_module.main(argv) == 2


def test_route_decision_is_frozen(fp_module) -> None:
    decision = fp_module.RouteDecision(
        action="proceed", decision="NO_BASELINE", detail="d", diff={}, model_version="0.1.0"
    )
    with pytest.raises(FrozenInstanceError, match="cannot assign to field 'action'"):
        decision.action = "skip"  # type: ignore[misc]  # 冻结性断言本身需触发赋值
