"""scripts/corpus_manifest.py 纯函数与校验路径单测（FT-07）。

守护目标（检视报告 FT-07，P3 待决策 → 维护者裁定「支持多版本」）：
- manifest（schema ``finbert-corpus-manifest.v1``）为行数 / digest 期望值的唯一权威来源；
- manifest 缺失且 tag 未登记 → fail-closed（不静默回退硬编码）；
- legacy 语料（``corpus-v1``，无 manifest）经显式登记表兜底，报告显式声明「未做 manifest
  校验」（R21：不得把未校验伪装成已校验）；
- 对抗面：tag 串档 / 行数不符 / digest 不符 / 缺 split / manifest 与 SHA256SUMS.txt 双事实
  源漂移，均须 FAIL。

全部为纯函数 / 本地文件测试，不触网、不触发训练或发布副作用。
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "corpus_manifest.py"

SPLITS = ("split_train.jsonl", "split_val.jsonl", "split_test.jsonl")


@pytest.fixture(scope="module")
def cm():
    spec = importlib.util.spec_from_file_location("corpus_manifest", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_split(path: Path, rows: int) -> None:
    path.write_text(
        "\n".join('{"text": "样例", "label": "中性"}' for _ in range(rows)) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_corpus(root: Path, rows: dict[str, int]) -> Path:
    """按 ``rows`` 写出三个 split 与 SHA256SUMS.txt，返回语料目录。"""
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for name, count in rows.items():
        _write_split(corpus / name, count)
    sums = "\n".join(f"{_sha256(corpus / name)}  {name}" for name in rows)
    (corpus / "SHA256SUMS.txt").write_text(sums + "\n", encoding="utf-8")
    return corpus


def _write_manifest(corpus: Path, *, tag: str, rows: dict[str, int] | None = None, schema: str | None = None) -> Path:
    splits = {}
    for name in SPLITS:
        count = (
            rows[name]
            if rows
            else sum(1 for line in (corpus / name).read_text(encoding="utf-8").splitlines() if line.strip())
        )
        splits[name] = {"rows": count, "sha256": _sha256(corpus / name)}
    manifest = {
        "schema": schema or "finbert-corpus-manifest.v1",
        "corpus_tag": tag,
        "corpus_version": "2",
        "splits": splits,
    }
    path = corpus / "corpus_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_count_nonempty_lines_skips_blank_lines(cm, tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text('{"a": 1}\n\n   \n{"b": 2}\n', encoding="utf-8")
    # 空行与纯空白行不计入（与 train_finbert.load_split_file 口径一致）
    assert cm.count_nonempty_lines(path) == 2


def test_sha256_file_matches_hashlib(cm, tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"corpus-v1\n")
    assert cm.sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_parse_sha256sums_parses_and_rejects_malformed(cm, tmp_path: Path) -> None:
    path = tmp_path / "SHA256SUMS.txt"
    good = "a" * 64
    path.write_text(f"{good}  split_train.jsonl\n", encoding="utf-8")
    assert cm.parse_sha256sums(path) == {"split_train.jsonl": good}
    path.write_text("not-a-digest  split_train.jsonl\n", encoding="utf-8")
    with pytest.raises(cm.CorpusManifestError, match="行格式非法"):
        cm.parse_sha256sums(path)


def test_load_manifest_rejects_incompatible_schema(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 1, "split_val.jsonl": 1, "split_test.jsonl": 1})
    path = _write_manifest(corpus, tag="corpus-v2", schema="finbert-corpus-manifest.v99")
    with pytest.raises(cm.CorpusManifestError, match="schema 不兼容"):
        cm.load_manifest(path)


def test_load_manifest_requires_canonical_split_set(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 1, "split_val.jsonl": 1, "split_test.jsonl": 1})
    path = corpus / "corpus_manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "finbert-corpus-manifest.v1",
                "corpus_tag": "corpus-v2",
                "corpus_version": "2",
                "splits": {"split_train.jsonl": {"rows": 1, "sha256": "a" * 64}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(cm.CorpusManifestError, match="splits 必须恰为"):
        cm.load_manifest(path)


def test_load_manifest_requires_non_empty_corpus_version(cm, tmp_path: Path) -> None:
    # 裁定「manifest 含 split/行数/digest/版本」：版本缺失或空串即拒绝（版本是 lineage 一部分）。
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 1, "split_val.jsonl": 1, "split_test.jsonl": 1})
    path = _write_manifest(corpus, tag="corpus-v2")
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["corpus_version"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(cm.CorpusManifestError, match="corpus_version 必须为非空字符串"):
        cm.load_manifest(path)
    data["corpus_version"] = "  "
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(cm.CorpusManifestError, match="corpus_version 必须为非空字符串"):
        cm.load_manifest(path)


def test_load_manifest_rejects_non_positive_rows(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 1, "split_val.jsonl": 1, "split_test.jsonl": 1})
    manifest = {
        "schema": "finbert-corpus-manifest.v1",
        "corpus_tag": "corpus-v2",
        "corpus_version": "2",
        "splits": {name: {"rows": 0, "sha256": "a" * 64} for name in SPLITS},
    }
    path = corpus / "corpus_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(cm.CorpusManifestError, match="rows 必须为正整数"):
        cm.load_manifest(path)


def test_build_manifest_round_trip_then_verify_passes(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 3, "split_val.jsonl": 2, "split_test.jsonl": 1})
    manifest = cm.build_manifest(corpus, "corpus-v2", "2")
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = cm.verify_corpus(corpus, "corpus-v2")
    assert report["verdict"] == "PASS"
    assert report["manifest_state"] == "manifest"
    assert report["total_rows"] == 6
    assert all(item["rows_ok"] and item["sha256_ok"] for item in report["splits"].values())


def test_build_manifest_cross_checks_sha256sums(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    # 篡改 SHA256SUMS.txt 使实测 digest 与清单不一致 → 生成期即交叉校验拦截
    (corpus / "SHA256SUMS.txt").write_text(f"{'b' * 64}  split_train.jsonl\n", encoding="utf-8")
    with pytest.raises(cm.CorpusManifestError, match="与 SHA256SUMS.txt 不一致"):
        cm.build_manifest(corpus, "corpus-v2", "2")


def test_verify_detects_tag_mismatch(cm, tmp_path: Path) -> None:
    # 防串档：manifest 声明的 corpus_tag 必须等于本次 dispatch 的 tag。
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    _write_manifest(corpus, tag="corpus-v2")
    report = cm.verify_corpus(corpus, "corpus-v3")
    assert report["verdict"] == "FAIL"
    assert any("防串档" in reason for reason in report["reasons"])


def test_verify_detects_split_missing_from_checksums(cm, tmp_path: Path) -> None:
    # 双事实源不完整：manifest 声明的 split 不在 SHA256SUMS.txt 中（清单缺条目）→ FAIL。
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    _write_manifest(corpus, tag="corpus-v2")
    sums = [
        line
        for line in (corpus / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
        if not line.endswith("split_val.jsonl")
    ]
    (corpus / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    report = cm.verify_corpus(corpus, "corpus-v2")
    assert report["verdict"] == "FAIL"
    assert any("不在 SHA256SUMS.txt 中" in reason for reason in report["reasons"])


def test_verify_rejects_noncanonical_dev_candidate(cm, tmp_path: Path) -> None:
    # dev 候选名防错位（train_finbert.DEV_CANDIDATES 首选 split_dev.jsonl）：目录混入该文件时
    # 训练实际消费它而 manifest 只声明 split_val.jsonl——即使行数/digest 全部吻合也 FAIL。
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    _write_manifest(corpus, tag="corpus-v2")
    (corpus / "split_dev.jsonl").write_text('{"text": "干扰行", "label": "中性"}\n', encoding="utf-8")
    report = cm.verify_corpus(corpus, "corpus-v2")
    assert report["verdict"] == "FAIL"
    assert any("split_dev.jsonl" in reason for reason in report["reasons"])


def test_verify_detects_manifest_checksums_drift(cm, tmp_path: Path) -> None:
    # 双事实源漂移：manifest 声明 digest 与 SHA256SUMS.txt 不一致（即使文件本身匹配 manifest）。
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    manifest = cm.build_manifest(corpus, "corpus-v2", "2")
    manifest["splits"]["split_train.jsonl"]["sha256"] = "c" * 64
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = cm.verify_corpus(corpus, "corpus-v2")
    assert report["verdict"] == "FAIL"
    assert any("双事实源漂移" in reason for reason in report["reasons"])


def test_verify_detects_row_and_digest_mismatch(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 3, "split_val.jsonl": 2, "split_test.jsonl": 1})
    manifest = cm.build_manifest(corpus, "corpus-v2", "2")
    manifest["splits"]["split_train.jsonl"] = {"rows": 99, "sha256": "d" * 64}
    (corpus / "corpus_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = cm.verify_corpus(corpus, "corpus-v2")
    assert report["verdict"] == "FAIL"
    assert any("非空行数" in reason for reason in report["reasons"])
    assert any("SHA256" in reason for reason in report["reasons"])


def test_verify_detects_missing_split(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    _write_manifest(corpus, tag="corpus-v2")
    (corpus / "split_test.jsonl").unlink()
    report = cm.verify_corpus(corpus, "corpus-v2")
    assert report["verdict"] == "FAIL"
    assert any("缺少 split 文件" in reason for reason in report["reasons"])


def test_verify_missing_checksums_is_fail_closed(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    _write_manifest(corpus, tag="corpus-v2")
    (corpus / "SHA256SUMS.txt").unlink()
    with pytest.raises(cm.CorpusManifestError, match="缺少 SHA256SUMS.txt"):
        cm.verify_corpus(corpus, "corpus-v2")


def test_verify_unknown_tag_without_manifest_fails_closed(cm, tmp_path: Path) -> None:
    # 未登记 tag 且无 manifest → fail-closed + 修复指引（禁止静默沿用硬编码）。
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    with pytest.raises(cm.CorpusManifestError, match="未随附 corpus_manifest.json"):
        cm.verify_corpus(corpus, "corpus-v9")


def test_verify_legacy_registry_declares_no_manifest(cm, tmp_path: Path, monkeypatch) -> None:
    # legacy 路径：无 manifest，采用显式登记值 + SHA256SUMS digest，报告声明「未做 manifest 校验」。
    monkeypatch.setitem(
        cm.LEGACY_CORPUS_REGISTRY,
        "corpus-test-legacy",
        {"split_train.jsonl": 3, "split_val.jsonl": 2, "split_test.jsonl": 1},
    )
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 3, "split_val.jsonl": 2, "split_test.jsonl": 1})
    report = cm.verify_corpus(corpus, "corpus-test-legacy")
    assert report["verdict"] == "LEGACY_REGISTRY"
    assert report["manifest_state"] == "legacy-registry"
    assert "未做 manifest 校验" in report["declaration"]


def test_verify_legacy_registry_still_enforces_row_counts(cm, tmp_path: Path, monkeypatch) -> None:
    # legacy 登记值仍须与文件行数一致（不因无 manifest 而放弃守恒校验）。
    monkeypatch.setitem(
        cm.LEGACY_CORPUS_REGISTRY,
        "corpus-test-legacy",
        {"split_train.jsonl": 3, "split_val.jsonl": 2, "split_test.jsonl": 1},
    )
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 4, "split_val.jsonl": 2, "split_test.jsonl": 1})
    report = cm.verify_corpus(corpus, "corpus-test-legacy")
    assert report["verdict"] == "FAIL"
    assert any("非空行数" in reason for reason in report["reasons"])


def test_verify_legacy_registry_rejects_incomplete_registration(cm, tmp_path: Path, monkeypatch) -> None:
    # 登记表结构缺项须清晰报错（而非 KeyError 裸露）：登记须恰为三个规范 split。
    monkeypatch.setitem(cm.LEGACY_CORPUS_REGISTRY, "corpus-test-legacy", {"split_train.jsonl": 3})
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 3, "split_val.jsonl": 2, "split_test.jsonl": 1})
    with pytest.raises(cm.CorpusManifestError, match="legacy registry 对 `corpus-test-legacy` 的登记不完整"):
        cm.verify_corpus(corpus, "corpus-test-legacy")


def test_corpus_v1_registered_as_legacy(cm) -> None:
    # corpus-v1 为已发布、无 manifest 的历史语料，须显式登记（值取自其 Release note 登记行数）。
    # 断言整表内容：登记表扩容（新增 legacy tag 规避 manifest）必须经受审的测试变更——防「兜底
    # 路径被当作常规通道滥用」。
    assert cm.LEGACY_CORPUS_REGISTRY == {
        "corpus-v1": {
            "split_train.jsonl": 8813,
            "split_val.jsonl": 1102,
            "split_test.jsonl": 1102,
        }
    }


def test_main_exit_code_semantics(cm, tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path, {"split_train.jsonl": 2, "split_val.jsonl": 2, "split_test.jsonl": 2})
    # build 子命令（manifest 写入语料目录，供随后 verify 读取）
    out = corpus / "corpus_manifest.json"
    assert (
        cm.main(
            [
                "build",
                "--corpus-dir",
                str(corpus),
                "--corpus-tag",
                "corpus-v2",
                "--corpus-version",
                "2",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert out.is_file()
    # verify：manifest 通过 → 0
    report = tmp_path / "r.json"
    assert cm.main(["verify", "--corpus-dir", str(corpus), "--corpus-tag", "corpus-v2", "--report", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "PASS"
    # verify：tag 串档 → 1
    assert cm.main(["verify", "--corpus-dir", str(corpus), "--corpus-tag", "corpus-v3", "--report", str(report)]) == 1
