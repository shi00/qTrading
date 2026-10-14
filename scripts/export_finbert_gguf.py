"""FinBERT2-base GGUF 导出流水线（CG-1，固化 ``reviews/新闻达标模型微调.md`` §6）。

三步：① 从 HF ``BertForSequenceClassification`` 权重抽取分类头 → ``head.npz``
（§8.2 正本格式）；② 调 llama.cpp ``convert_hf_to_gguf.py`` 把拆出的编码器转 f16；
③ 调 ``llama-quantize`` 转 Q8_0。本脚本自包含，不 import data/cache 层业务逻辑。

**head.npz 格式（§6.1 / §8.2 正本 + 实物考证）**：离线产物
（``data/corpus/stage_b_out/head.npz``）为 ``mode="bert"`` 非线性形态——
``pw`` = ``bert.pooler.dense.weight``（hidden×hidden）、``pb`` = pooler bias（hidden）、
``cw`` = ``classifier.weight``（3×hidden）、``cb`` = classifier bias（3）、
``labels = [利空, 中性, 利好]``（0=利空, 1=中性, 2=利好，§4.4 正本）。
``mode="linear"``（§5.1 阶段 A 探针形态：cw/cb 直接作用于 CLS 特征，无 pooler）仅在
权重已折算到 CLS 域时可选；从 ``BertForSequenceClassification`` 原始权重出发的默认
且语义正确的路径是 ``bert``（tanh 非线性不可折算为线性头）。产物可被
``scripts/verify_finbert_gguf.load_head_npz`` 读取（导出后应跑该门禁，reviews §7）。

**llama.cpp 工具链对齐查证（2026-10-13，GitHub API 实证）**：

- llama-cpp-python v0.3.35（pyproject 声明 ``>=0.3.35``，CI 按 uv lock 用 0.3.35）的
  ``vendor/llama.cpp`` submodule 指向 ggml-org/llama.cpp commit
  ``4df29be4f4c3673f428170fda944a5b19f743bb8``（2026-08-16，
  "ci : fix dry-run reporting in make-release job [no ci] (#27167)"）。
  证据：``https://api.github.com/repos/abetlen/llama-cpp-python/contents/vendor/llama.cpp?ref=v0.3.35``
  （``sha`` 字段即 submodule 指针）。
- 该 commit 无同名 tag；其后最近的覆盖型 semver tag 为 ``v0.3.0``
  （``c1d0e7a00401``，2026-08-25，"llama.cpp : bump version to 0.3.0 (#27696)"）。
  compare API ``.../compare/v0.3.0...4df29be`` 返回 ``ahead_by=0``、``behind_by=167``、
  ``merge_base=4df29be4…``——``merge_base`` 即 head 本身且 ``ahead_by=0``，
  直接证明 ``4df29be`` 为 ``v0.3.0`` 的直系祖先（``behind_by=167`` 单独不构成祖先
  证据，故以 merge_base + ahead_by=0 双证锚定；v0.3.0 领先该 commit 167 次提交 / 约 9 天）。
- **结论**：CI clone llama.cpp 时应 checkout tag ``v0.3.0``（更严格可直接 checkout
  上述精确 commit sha），``--llama-cpp-repo`` 指向该 clone；``--convert-script`` /
  ``--quantize-bin`` 未给定时按该仓库布局推导（``convert_hf_to_gguf.py`` 与
  ``build/bin/llama-quantize[.exe]``）。注意 v0.3.0 与该 commit 并非逐位等价
  （上游 167 提交），GGUF 格式随版本演进，导出产物必须先过
  ``scripts/verify_finbert_gguf.py`` 四判据门禁再发布（reviews §7）。

**依赖边界**：torch / transformers 仅离线导出使用，不进 pyproject 运行时依赖、不进 CI
单测环境，全部经 ``importlib`` 函数体内延迟导入；numpy 为主依赖可顶层导入。

Usage:
    python scripts/export_finbert_gguf.py --model-dir data/corpus/stage_b_out/best \\
        --out-dir data/corpus/stage_b_out \\
        --llama-cpp-repo /path/to/llama.cpp   # checkout tag v0.3.0

    # 只做输入 / 工具存在性校验并打印导出计划
    python scripts/export_finbert_gguf.py --model-dir ... --out-dir ... \\
        --convert-script /path/convert_hf_to_gguf.py --quantize-bin /path/llama-quantize --dry-run

退出码：成功 0；输入 / 工具校验或转换失败 1。
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

LOGGER = logging.getLogger("scripts.export_finbert_gguf")

EMBEDDING_DIM = 768
NUM_CLASSES = 3
LABELS = ("利空", "中性", "利好")  # 0=利空, 1=中性, 2=利好（reviews §4.4 / §8.2 正本）

HEAD_MODE_LINEAR = "linear"
HEAD_MODE_BERT = "bert"
HEAD_MODE_CHOICES = (HEAD_MODE_LINEAR, HEAD_MODE_BERT)

# llama.cpp 对齐结论（查证过程与证据 URL 见模块 docstring；勿改值前先复核证据链）
LLAMA_CPP_ALIGN_TAG = "v0.3.0"
LLAMA_CPP_ALIGN_COMMIT = "4df29be4f4c3673f428170fda944a5b19f743bb8"

# 前缀对齐现役产物命名：ai_models/finbert2-news-q8_0.gguf（通过 N0-3 四判据门禁的
# 现役产物，scripts/verify_finbert_gguf.py usage 同名正本）；ai_models 下的
# finbert2-sentiment-* 为已废弃 ONNX/OpenVINO PoC 路线遗留产物（PoC 判定失败），勿混淆
DEFAULT_OUT_PREFIX = "finbert2-news"

F16_SUFFIX = "-f16.gguf"
Q8_SUFFIX = "-q8_0.gguf"
HEAD_FILE_NAME = "head.npz"
ENCODER_DIR_NAME = "encoder"


def extract_head_from_state_dict(
    state_dict: Mapping[str, Any],
    *,
    num_classes: int = NUM_CLASSES,
    hidden_size: int = EMBEDDING_DIM,
    mode: str = HEAD_MODE_BERT,
) -> dict[str, Any]:
    """从 state_dict（值为 numpy 数组）抽取分类头（纯函数）。

    ``bert``（非线性）模式取 ``bert.pooler.dense.*`` 与 ``classifier.*``；
    ``linear`` 模式仅取 ``classifier.*``（语义：权重已折算到 CLS 域，§5.1）。
    state_dict 中所需张量之外的键（编码器各层等）静默忽略——本函数只抽取分类头
    所需张量，不校验全量权重。张量缺失或形状不符时以 ``ValueError`` 拒绝
    （不能静默降级，§8.2）。
    """
    if mode not in HEAD_MODE_CHOICES:
        raise ValueError(f"未知 head 模式: {mode!r}（允许 {list(HEAD_MODE_CHOICES)}）")

    def _require(key: str, shape: tuple[int, ...]) -> np.ndarray:
        if key not in state_dict:
            raise ValueError(f"state_dict 缺少必需张量: {key}")
        array = np.asarray(state_dict[key])
        if tuple(array.shape) != shape:
            raise ValueError(f"{key} 形状不符: {tuple(array.shape)}（期望 {shape}）")
        return np.ascontiguousarray(array, dtype=np.float32)

    classifier_weight = _require("classifier.weight", (num_classes, hidden_size))
    classifier_bias = _require("classifier.bias", (num_classes,))
    head: dict[str, Any] = {
        "mode": mode,
        "labels": np.array(LABELS),
        "cw": classifier_weight,
        "cb": classifier_bias,
    }
    if mode == HEAD_MODE_BERT:
        head["pw"] = _require("bert.pooler.dense.weight", (hidden_size, hidden_size))
        head["pb"] = _require("bert.pooler.dense.bias", (hidden_size,))
    return head


def write_head_npz(head: Mapping[str, Any], path: Path) -> None:
    """写出 head.npz（写前按 §8.2 契约校验形状，父目录自动创建）。"""
    mode = str(head["mode"])
    cw = np.asarray(head["cw"])
    hidden = int(cw.shape[1])
    if mode not in HEAD_MODE_CHOICES:
        raise ValueError(f"未知 head 模式: {mode!r}")
    if cw.shape != (NUM_CLASSES, hidden) or np.asarray(head["cb"]).shape != (NUM_CLASSES,):
        raise ValueError(f"head.npz 形状不符: cw={cw.shape} cb={np.asarray(head['cb']).shape}（期望 3×{hidden} / 3）")
    if mode == HEAD_MODE_BERT:
        pw = np.asarray(head["pw"])
        pb = np.asarray(head["pb"])
        if pw.shape != (hidden, hidden) or pb.shape != (hidden,):
            raise ValueError(f"head.npz 形状不符: pw={pw.shape} pb={pb.shape}（期望 {hidden}×{hidden} / {hidden}）")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {key: np.asarray(value) for key, value in head.items()}
    np.savez(path, **payload)


def resolve_tool_paths(
    *,
    convert_script: Path | str | None,
    quantize_bin: Path | str | None,
    llama_cpp_repo: Path | str | None,
) -> tuple[Path, Path]:
    """解析 llama.cpp 工具路径：显式参数优先，否则按 ``--llama-cpp-repo`` 布局推导。

    仓库布局：``convert_hf_to_gguf.py``（仓库根）与 ``build/bin/llama-quantize[.exe]``
    （Windows 取 .exe）。参数全缺或任一路径不存在时 ``ValueError`` 拒绝。
    对齐 tag 见模块 docstring / ``LLAMA_CPP_ALIGN_TAG``。
    """
    repo = Path(llama_cpp_repo) if llama_cpp_repo else None
    convert = Path(convert_script) if convert_script else (repo / "convert_hf_to_gguf.py" if repo else None)
    if quantize_bin:
        quantize = Path(quantize_bin)
    elif repo is not None:
        name = "llama-quantize.exe" if os.name == "nt" else "llama-quantize"
        quantize = repo / "build" / "bin" / name
    else:
        quantize = None
    if convert is None or quantize is None:
        raise ValueError(
            "必须提供 --convert-script 与 --quantize-bin，或提供 --llama-cpp-repo "
            f"（需 checkout tag {LLAMA_CPP_ALIGN_TAG} 或 commit {LLAMA_CPP_ALIGN_COMMIT}）"
        )
    missing = [path for path in (convert, quantize) if not path.is_file()]
    if missing:
        raise ValueError(
            f"工具路径不存在: {', '.join(str(path) for path in missing)}"
            f"（llama.cpp 需 checkout tag {LLAMA_CPP_ALIGN_TAG} 或 commit {LLAMA_CPP_ALIGN_COMMIT}）"
        )
    return convert, quantize


def _read_model_config(model_dir: Path) -> dict[str, Any]:
    """读取并校验 HF 模型 config.json（导出流程仅适用 BertForSequenceClassification）。"""
    config_path = model_dir / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"模型目录缺少 config.json: {model_dir}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if str(config.get("model_type", "")).lower() != "bert":
        raise ValueError(f"仅支持 Bert 结构（model_type=bert），实际: {config.get('model_type')!r}")
    architectures = [str(item) for item in (config.get("architectures") or [])]
    if "BertForSequenceClassification" not in architectures:
        raise ValueError(
            f"architectures 需含 BertForSequenceClassification，实际: {architectures}（纯编码器目录请直接交给 convert_hf_to_gguf.py）"
        )
    return config


def build_export_plan(args: argparse.Namespace) -> dict[str, Any]:
    """构造导出计划（dry-run 预检：输入 config / 工具路径存在性 + 期望产物路径）。"""
    model_dir = Path(args.model_dir)
    config = _read_model_config(model_dir)
    hidden = int(config.get("hidden_size", EMBEDDING_DIM))
    convert, quantize = resolve_tool_paths(
        convert_script=args.convert_script,
        quantize_bin=args.quantize_bin,
        llama_cpp_repo=args.llama_cpp_repo,
    )
    out_dir = Path(args.out_dir)
    is_bert_mode = args.head_mode == HEAD_MODE_BERT
    return {
        "model_dir": str(model_dir),
        "out_dir": str(out_dir),
        "encoder_dir": str(out_dir / ENCODER_DIR_NAME),
        "head_path": str(out_dir / HEAD_FILE_NAME),
        "head": {
            "mode": args.head_mode,
            "labels": list(LABELS),
            "cw_shape": [NUM_CLASSES, hidden],
            "cb_shape": [NUM_CLASSES],
            "pw_shape": [hidden, hidden] if is_bert_mode else None,
            "pb_shape": [hidden] if is_bert_mode else None,
        },
        "f16_gguf": str(out_dir / f"{args.out_prefix}{F16_SUFFIX}"),
        "q8_gguf": str(out_dir / f"{args.out_prefix}{Q8_SUFFIX}"),
        "convert_script": str(convert),
        "quantize_bin": str(quantize),
        "llama_cpp_alignment": {"tag": LLAMA_CPP_ALIGN_TAG, "commit": LLAMA_CPP_ALIGN_COMMIT},
    }


def export(args: argparse.Namespace) -> dict[str, str]:
    """执行完整导出（拆头 → 编码器 → f16 → Q8_0）；torch / transformers 延迟导入。"""
    importlib.import_module("torch")  # transformers 依赖 torch；触发导入以保证报错信息明确
    transformers = importlib.import_module("transformers")

    plan = build_export_plan(args)
    LOGGER.info("导出计划:\n%s", json.dumps(plan, ensure_ascii=False, indent=2))
    convert_script = plan["convert_script"]
    quantize_bin = plan["quantize_bin"]
    # hidden_size 与 dry-run 计划同源（config.hidden_size），保证计划与实际抽取一致
    hidden_size = int(plan["head"]["cw_shape"][1])

    model_dir = Path(args.model_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ① 分类头 → head.npz（reviews §6.1）
    model = transformers.AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    tokenizer = transformers.AutoTokenizer.from_pretrained(str(model_dir))
    state_dict = {key: tensor.detach().float().cpu().numpy() for key, tensor in model.state_dict().items()}
    head = extract_head_from_state_dict(
        state_dict, num_classes=NUM_CLASSES, hidden_size=hidden_size, mode=args.head_mode
    )
    head_path = out_dir / HEAD_FILE_NAME
    write_head_npz(head, head_path)
    LOGGER.info("head.npz 已写出: %s（mode=%s）", head_path, head["mode"])

    # 编码器拆分（§6.1：m.bert.save_pretrained，config 自动落为 BertModel）
    encoder_dir = out_dir / ENCODER_DIR_NAME
    model.bert.save_pretrained(str(encoder_dir))
    tokenizer.save_pretrained(str(encoder_dir))
    LOGGER.info("编码器已拆分: %s", encoder_dir)

    # ② f16 GGUF
    f16_path = out_dir / f"{args.out_prefix}{F16_SUFFIX}"
    subprocess.run(
        [sys.executable, convert_script, str(encoder_dir), "--outtype", "f16", "--outfile", str(f16_path)],
        check=True,
    )

    # ③ Q8_0 量化（POSIX 下先校验执行权限，给出 chmod +x 提示而非裸 PermissionError）
    q8_path = out_dir / f"{args.out_prefix}{Q8_SUFFIX}"
    if os.name != "nt" and not os.access(str(quantize_bin), os.X_OK):
        raise ValueError(f"量化工具无执行权限（POSIX 请 chmod +x）: {quantize_bin}")
    subprocess.run([quantize_bin, str(f16_path), str(q8_path), "Q8_0"], check=True)

    LOGGER.info("导出完成: %s / %s", f16_path, q8_path)
    return {
        "head_path": str(head_path),
        "encoder_dir": str(encoder_dir),
        "f16_gguf": str(f16_path),
        "q8_gguf": str(q8_path),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FinBERT2-base GGUF 导出流水线（reviews §6 固化）")
    parser.add_argument(
        "--model-dir", required=True, help="HF BertForSequenceClassification 权重目录（如 stage_b_out/best）"
    )
    parser.add_argument("--out-dir", required=True, help="输出目录（head.npz / encoder/ / f16 / q8_0）")
    parser.add_argument("--out-prefix", default=DEFAULT_OUT_PREFIX, help="GGUF 产物文件名前缀")
    parser.add_argument(
        "--head-mode",
        choices=HEAD_MODE_CHOICES,
        default=HEAD_MODE_BERT,
        help="head 模式：bert=非线性（pooler+classifier，§6.1/§8.2，实物形态）；linear=已折算 CLS 域线性头（§5.1）",
    )
    parser.add_argument("--convert-script", default=None, help="llama.cpp convert_hf_to_gguf.py 路径（CI 提供）")
    parser.add_argument("--quantize-bin", default=None, help="llama.cpp llama-quantize 可执行路径（CI 提供）")
    parser.add_argument(
        "--llama-cpp-repo",
        default=None,
        help=(
            "已 checkout tag v0.3.0（或 commit 4df29be4f4c3673f428170fda944a5b19f743bb8）的 llama.cpp 仓库根目录；"
            "--convert-script / --quantize-bin 未给定时按仓库布局推导"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="只做输入 / 工具存在性校验并打印导出计划，不执行导出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """脚本入口：dry-run 只校验并打印计划；真实路径执行导出。失败记录 ERROR 并返回 1。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    try:
        if args.dry_run:
            plan = build_export_plan(args)
            LOGGER.info("导出计划（dry-run，未执行导出）:\n%s", json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        result = export(args)
        LOGGER.info("导出完成:\n%s", json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, FileNotFoundError, ImportError, OSError, subprocess.CalledProcessError) as exc:
        LOGGER.error("导出失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
