"""Tests for scripts/export_finbert_gguf.py（CG-1 GGUF 导出流水线固化）。

覆盖纯函数路径（单测环境无 torch / transformers / llama.cpp 工具链）：
- 分类头抽取（合成微型权重 → mode / labels / cw / cb / pw / pb 字段与形状；形状不符拒绝）；
- head.npz 写出与读回一致（并与 ``verify_finbert_gguf.load_head_npz`` 契约交叉验证）；
- 工具路径解析（显式 / 由 --llama-cpp-repo 推导 / 缺失拒绝）；
- 导出计划与 CLI --dry-run（不触发真实导出）。

llama.cpp 对齐结论（查证于 2026-10-13，见脚本 docstring）：
llama-cpp-python v0.3.35 的 vendor/llama.cpp submodule 指向 ggml-org/llama.cpp
commit ``4df29be4f4c3673f428170fda944a5b19f743bb8``；最近的覆盖型 semver tag
为 ``v0.3.0``（c1d0e7a00401，2026-08-25；compare API ``v0.3.0...4df29be`` 返回
ahead_by=0 / behind_by=167 / merge_base=4df29be4…，证明该 commit 为其直系祖先，
v0.3.0 领先 167 次提交）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_finbert_gguf as efg  # noqa: E402
from verify_finbert_gguf import load_head_npz  # noqa: E402


def _synthetic_state_dict(hidden_size: int = 4, num_classes: int = 3) -> dict[str, np.ndarray]:
    """合成微型 BertForSequenceClassification 权重（纯 numpy，无 torch）。"""
    rng = np.random.default_rng(20261008)
    return {
        "bert.pooler.dense.weight": rng.standard_normal((hidden_size, hidden_size)).astype(np.float32),
        "bert.pooler.dense.bias": rng.standard_normal(hidden_size).astype(np.float32),
        "classifier.weight": rng.standard_normal((num_classes, hidden_size)).astype(np.float32),
        "classifier.bias": rng.standard_normal(num_classes).astype(np.float32),
    }


def _make_model_dir(tmp_path: Path) -> Path:
    """构造仅含 config.json 的伪 HF 模型目录（dry-run 只需 config 元数据）。"""
    model_dir = tmp_path / "best"
    model_dir.mkdir()
    config = {
        "architectures": ["BertForSequenceClassification"],
        "model_type": "bert",
        "hidden_size": 768,
        "num_hidden_layers": 12,
        "vocab_size": 34932,
    }
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return model_dir


# --------------------------------------------------------------------------- #
# 分类头抽取
# --------------------------------------------------------------------------- #
def test_extract_head_bert_mode_fields_and_shapes() -> None:
    """bert（非线性）模式：mode / labels / cw / cb / pw / pb 齐全且形状正确。"""
    state = _synthetic_state_dict(hidden_size=4, num_classes=3)
    head = efg.extract_head_from_state_dict(state, hidden_size=4, num_classes=3)
    assert head["mode"] == "bert"
    assert tuple(str(x) for x in head["labels"]) == ("利空", "中性", "利好")
    np.testing.assert_array_equal(head["cw"], state["classifier.weight"])
    np.testing.assert_array_equal(head["cb"], state["classifier.bias"])
    np.testing.assert_array_equal(head["pw"], state["bert.pooler.dense.weight"])
    np.testing.assert_array_equal(head["pb"], state["bert.pooler.dense.bias"])
    assert head["cw"].shape == (3, 4)
    assert head["cb"].shape == (3,)
    assert head["pw"].shape == (4, 4)
    assert head["pb"].shape == (4,)
    assert head["cw"].dtype == np.float32


def test_extract_head_linear_mode_requires_folded_head() -> None:
    """linear 模式：仅产出 cw / cb（语义：头已折算到 CLS 域），不携带 pw / pb。"""
    state = _synthetic_state_dict(hidden_size=4, num_classes=3)
    head = efg.extract_head_from_state_dict(state, hidden_size=4, num_classes=3, mode="linear")
    assert head["mode"] == "linear"
    np.testing.assert_array_equal(head["cw"], state["classifier.weight"])
    np.testing.assert_array_equal(head["cb"], state["classifier.bias"])
    assert "pw" not in head
    assert "pb" not in head


def test_extract_head_rejects_bad_classifier_shape() -> None:
    """classifier.weight 形状不符（类别数不匹配）必须拒绝。"""
    state = _synthetic_state_dict(hidden_size=4, num_classes=3)
    state["classifier.weight"] = np.zeros((2, 4), dtype=np.float32)
    with pytest.raises(ValueError, match="classifier.weight"):
        efg.extract_head_from_state_dict(state, hidden_size=4, num_classes=3)


def test_extract_head_rejects_missing_pooler_key() -> None:
    """bert 模式缺 pooler 张量必须拒绝（不能静默降级为 linear）。"""
    state = _synthetic_state_dict(hidden_size=4, num_classes=3)
    del state["bert.pooler.dense.weight"]
    with pytest.raises(ValueError, match="bert.pooler.dense.weight"):
        efg.extract_head_from_state_dict(state, hidden_size=4, num_classes=3)


def test_extract_head_rejects_bad_pooler_shape() -> None:
    """pooler 权重形状与 hidden_size 不符必须拒绝。"""
    state = _synthetic_state_dict(hidden_size=4, num_classes=3)
    state["bert.pooler.dense.weight"] = np.zeros((8, 8), dtype=np.float32)
    with pytest.raises(ValueError, match="bert.pooler.dense.weight"):
        efg.extract_head_from_state_dict(state, hidden_size=4, num_classes=3)


# --------------------------------------------------------------------------- #
# head.npz 写读一致 + verify 契约交叉验证
# --------------------------------------------------------------------------- #
def test_write_head_npz_roundtrip_and_gate_contract(tmp_path: Path) -> None:
    """写出后读回一致，且能被 Gate 脚本 load_head_npz 消费（非线性 pre 非 None）。"""
    state = _synthetic_state_dict(hidden_size=768, num_classes=3)
    head = efg.extract_head_from_state_dict(state)
    path = tmp_path / "head.npz"
    efg.write_head_npz(head, path)

    data = np.load(path)
    assert sorted(data.files) == ["cb", "cw", "labels", "mode", "pb", "pw"]
    assert str(data["mode"]) == "bert"
    assert [str(x) for x in data["labels"]] == ["利空", "中性", "利好"]
    assert data["cw"].shape == (3, 768) and data["cw"].dtype == np.float32
    assert data["cb"].shape == (3,) and data["cb"].dtype == np.float32
    assert data["pw"].shape == (768, 768) and data["pw"].dtype == np.float32
    assert data["pb"].shape == (768,) and data["pb"].dtype == np.float32
    np.testing.assert_array_equal(data["cw"], state["classifier.weight"])

    weight, bias, pre = load_head_npz(path)
    np.testing.assert_array_equal(weight, state["classifier.weight"])
    np.testing.assert_array_equal(bias, state["classifier.bias"])
    assert pre is not None
    np.testing.assert_array_equal(pre[0], state["bert.pooler.dense.weight"])


def test_write_head_npz_linear_roundtrip_gate_contract(tmp_path: Path) -> None:
    """linear 头写出后被 Gate 脚本以 pre=None 消费。"""
    state = _synthetic_state_dict(hidden_size=768, num_classes=3)
    head = efg.extract_head_from_state_dict(state, mode="linear")
    path = tmp_path / "head.npz"
    efg.write_head_npz(head, path)
    weight, bias, pre = load_head_npz(path)
    np.testing.assert_array_equal(weight, state["classifier.weight"])
    assert pre is None


# --------------------------------------------------------------------------- #
# 工具路径解析
# --------------------------------------------------------------------------- #
def test_resolve_tool_paths_explicit_argument(tmp_path: Path) -> None:
    """显式给定 --convert-script 时优先使用。"""
    convert = tmp_path / "convert_hf_to_gguf.py"
    convert.write_text("# stub", encoding="utf-8")
    result = efg.resolve_tool_paths(convert_script=convert, llama_cpp_repo=None)
    assert result == convert


def test_resolve_tool_paths_from_repo(tmp_path: Path) -> None:
    """仅给 --llama-cpp-repo 时按 llama.cpp 仓库布局推导。"""
    repo = tmp_path / "llama.cpp"
    convert = repo / "convert_hf_to_gguf.py"
    convert.parent.mkdir(parents=True)
    convert.write_text("# stub", encoding="utf-8")
    result = efg.resolve_tool_paths(convert_script=None, llama_cpp_repo=repo)
    assert result == convert


def test_resolve_tool_paths_missing_raises() -> None:
    """--convert-script / --llama-cpp-repo 全缺时必须报错。"""
    with pytest.raises(ValueError, match="llama-cpp-repo"):
        efg.resolve_tool_paths(convert_script=None, llama_cpp_repo=None)


def test_resolve_tool_paths_missing_file_raises(tmp_path: Path) -> None:
    """给定路径不存在时必须报错（不得把失败推迟到 subprocess）。"""
    with pytest.raises(ValueError, match="不存在"):
        efg.resolve_tool_paths(convert_script=tmp_path / "nope.py", llama_cpp_repo=None)


# --------------------------------------------------------------------------- #
# 导出计划与 CLI --dry-run
# --------------------------------------------------------------------------- #
def test_build_export_plan_shape(tmp_path: Path) -> None:
    """计划含 head 模式与期望形状、f16 产物路径与 llama.cpp 对齐元数据（f16 即发布产物）。"""
    model_dir = _make_model_dir(tmp_path)
    out_dir = tmp_path / "out"
    convert = tmp_path / "convert_hf_to_gguf.py"
    convert.write_text("# stub", encoding="utf-8")
    args = efg._parse_args(
        [
            "--model-dir",
            str(model_dir),
            "--out-dir",
            str(out_dir),
            "--convert-script",
            str(convert),
        ]
    )
    plan = efg.build_export_plan(args)
    assert plan["head"]["mode"] == "bert"
    assert plan["head"]["labels"] == ["利空", "中性", "利好"]
    assert plan["head"]["cw_shape"] == [3, 768]
    assert plan["head"]["pw_shape"] == [768, 768]
    assert plan["f16_gguf"].endswith("f16.gguf")
    assert "q8_gguf" not in plan
    assert "quantize_bin" not in plan
    assert plan["llama_cpp_alignment"]["tag"] == "v0.3.0"
    assert plan["llama_cpp_alignment"]["commit"] == "4df29be4f4c3673f428170fda944a5b19f743bb8"


def test_main_dry_run_prints_plan_without_export(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run 只做输入/工具存在性校验并打印计划，不得触发真实导出。"""
    model_dir = _make_model_dir(tmp_path)
    convert = tmp_path / "convert_hf_to_gguf.py"
    convert.write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(efg, "export", lambda args: pytest.fail("dry-run 不得触发 export()"))
    with caplog.at_level("INFO", logger="scripts.export_finbert_gguf"):
        code = efg.main(
            [
                "--model-dir",
                str(model_dir),
                "--out-dir",
                str(tmp_path / "out"),
                "--convert-script",
                str(convert),
                "--dry-run",
            ]
        )
    assert code == 0
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "f16.gguf" in joined
    assert "q8_0" not in joined
    assert "v0.3.0" in joined


def test_main_dry_run_fails_when_tools_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """工具路径不存在时 dry-run 报错退出（非零码）。"""
    model_dir = _make_model_dir(tmp_path)
    with caplog.at_level("ERROR", logger="scripts.export_finbert_gguf"):
        code = efg.main(
            [
                "--model-dir",
                str(model_dir),
                "--out-dir",
                str(tmp_path / "out"),
                "--convert-script",
                str(tmp_path / "missing.py"),
                "--dry-run",
            ]
        )
    assert code == 1


def test_main_dry_run_fails_on_non_bert_model(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """model_type 非 bert 时 dry-run 拒绝（导出流程仅适用 Bert 结构）。"""
    model_dir = tmp_path / "nonbert"
    model_dir.mkdir()
    config = {"architectures": ["GPT2LMHeadModel"], "model_type": "gpt2", "hidden_size": 768}
    (model_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    with caplog.at_level("ERROR", logger="scripts.export_finbert_gguf"):
        code = efg.main(
            [
                "--model-dir",
                str(model_dir),
                "--out-dir",
                str(tmp_path / "out"),
                "--convert-script",
                str(tmp_path / "c.py"),
                "--dry-run",
            ]
        )
    assert code == 1


def test_parse_args_rejects_removed_quantize_bin() -> None:
    """Q8_0 量化已移除（f16 交付）：CLI 不得再接受 --quantize-bin（防回归）。"""
    with pytest.raises(SystemExit) as excinfo:
        efg._parse_args(["--model-dir", "m", "--out-dir", "o", "--convert-script", "c", "--quantize-bin", "q"])
    # argparse 对未识别参数以 exit code 2 退出
    assert excinfo.value.code == 2


def test_parse_args_defaults_and_alignment_metadata() -> None:
    """默认参数：out-prefix / head-mode / 对齐 tag 与 sha 常量正确。"""
    args = efg._parse_args(["--model-dir", "m", "--out-dir", "o", "--convert-script", "c"])
    assert args.out_prefix == "finbert2-news"
    assert args.head_mode == "bert"
    assert efg.LLAMA_CPP_ALIGN_TAG == "v0.3.0"
    assert efg.LLAMA_CPP_ALIGN_COMMIT == "4df29be4f4c3673f428170fda944a5b19f743bb8"
