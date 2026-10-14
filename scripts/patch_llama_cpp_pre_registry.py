"""向 llama.cpp ``conversion/base.py`` 注入 FinBERT2-base 的 BPE pre-tokenizer 登记。

**背景（2026-10-14 定因，见 reviews/新闻达标模型微调.md §6 与 run 38502872101 日志）**：
FinBERT2-base（vocab=21128，hfl/chinese-roberta-wwm-ext 系词表）的 pre-tokenizer
chkhsh 为 ``20a9d25f0d2de78daab377e5cab1ef5da96b5868f9f94891f011c0715c45cd9d``，
该 hash 在 llama.cpp 全上游未登记（GitHub code search 实证 v0.3.0~v0.6.0 均无），
``get_vocab_base_pre()`` 走到链尾 ``if res is None`` 分支直接 raise
``NotImplementedError``，GGUF 导出中断。

**修复方式**：checkout llama.cpp tag ``v0.3.0``（与 llama-cpp-python 0.3.35 对齐，
证据链见 ``scripts/export_finbert_gguf.py`` 模块 docstring）后，向
``conversion/base.py`` 登记链注入一条 ``20a9d25f…`` → ``"bert-bge-large"`` 映射。
选 ``"bert-bge-large"`` 的理由：运行时（llama-cpp-python 0.3.35）对 BERT 分类器
强制外部分词（``scripts/verify_finbert_gguf.py`` 的 HF ids → LlamaBatch 路径），
``tokenizer.ggml.pre`` 仅为元数据；复用运行时已识别的既有 pre 名即可，不新增
运行时词汇表类型。

**升级触发条件**：llama.cpp 上游若正式登记该 chkhsh（新增 convert_hf_to_gguf_update.py
条目或 base.py 链条变更导致锚点消失），本脚本以「锚点缺失 / 已登记」两种可检状态
显式退出，届时移除 CI 中的调用步骤即可。

Usage:
    python scripts/patch_llama_cpp_pre_registry.py --base-file llama.cpp/conversion/base.py

退出码：成功（含幂等重入）0；文件缺失 / 锚点漂移 / 注入后语法校验失败 1。
"""

from __future__ import annotations

import argparse
import ast
import logging
from pathlib import Path

LOGGER = logging.getLogger("scripts.patch_llama_cpp_pre_registry")

# FinBERT2-base pre-tokenizer 探针 hash（convert_hf_to_gguf.py 对 chktxt 编码后
# token 序列的 sha256；本地与 CI runner 双向实测一致，transformers 5.19.0）。
FINBERT_CHKHSH = "20a9d25f0d2de78daab377e5cab1ef5da96b5868f9f94891f011c0715c45cd9d"
# 复用运行时已识别的既有 pre 名（仅元数据；运行时强制外部分词，见模块 docstring）。
FINBERT_PRE_NAME = "bert-bge-large"

# 注入锚点：登记链尾判断 + 首条 warning（两行连用保证唯一，v0.3.0 实测各仅 1 处）。
_INSERT_ANCHOR = '        if res is None:\n            logger.warning("\\n")\n'

# 注入块（缩进与登记链条目一致，插在链尾 raise 之前即天然命中）。
_INJECT_BLOCK = (
    "        # AStockScreener patch: FinBERT2-base (vocab=21128) pre-tokenizer hash is\n"
    "        # registered in NO upstream llama.cpp release (checked v0.3.0~v0.6.0); runtime\n"
    "        # (llama-cpp-python 0.3.35) always tokenizes BERT classifiers externally, so\n"
    "        # the pre name here is metadata-only -- reuse an already-recognized pre name.\n"
    f'        if chkhsh == "{FINBERT_CHKHSH}":\n'
    "            # ref: https://huggingface.co/valuesimplex-ai-lab/FinBERT2-base\n"
    f'            res = "{FINBERT_PRE_NAME}"\n'
)


def patch_base_file(base_file: Path) -> str:
    """向 ``conversion/base.py`` 注入登记条目，返回动作描述（纯函数便于单测）。

    幂等：文件已含目标 chkhsh 时原样返回 ``already-patched``；锚点缺失视为
    上游版本漂移，抛 ``ValueError``；注入后用 :func:`ast.parse` 校验语法。
    """
    if not base_file.is_file():
        raise FileNotFoundError(f"目标文件不存在: {base_file}")
    content = base_file.read_text(encoding="utf-8")

    if FINBERT_CHKHSH in content:
        LOGGER.info("已登记 %s → %s，跳过（幂等）", FINBERT_CHKHSH[:16], FINBERT_PRE_NAME)
        return "already-patched"

    anchor_pos = content.find(_INSERT_ANCHOR)
    if anchor_pos < 0:
        raise ValueError(f"注入锚点未命中（上游 llama.cpp 登记链结构可能已变更，请人工复核后更新本脚本）: {base_file}")

    patched = content.replace(_INSERT_ANCHOR, _INJECT_BLOCK + _INSERT_ANCHOR, 1)
    ast.parse(patched)  # 语法校验：注入块格式错误时在此报错，不落盘
    base_file.write_text(patched, encoding="utf-8")
    LOGGER.info("已注入登记条目 %s → %s（%s）", FINBERT_CHKHSH[:16], FINBERT_PRE_NAME, base_file)
    return "patched"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="注入 FinBERT2-base pre-tokenizer 登记到 llama.cpp conversion/base.py")
    parser.add_argument(
        "--base-file",
        required=True,
        help="llama.cpp/conversion/base.py 路径（须为已 checkout 的 tag v0.3.0 工作树）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """脚本入口：成功（含幂等）返回 0；失败记录 ERROR 并返回 1。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(argv)
    try:
        patch_base_file(Path(args.base_file))
        return 0
    except (ValueError, FileNotFoundError, OSError) as exc:
        LOGGER.error("patch 失败: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
