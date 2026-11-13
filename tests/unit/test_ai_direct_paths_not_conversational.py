"""DD-F34 / HANDOFF §11.1：新闻、新闻风险、FinBERT 与连接诊断保留原直接链。

设计冻结：这些路径复用同一连接/账本，但**不得被强造成会话**（不建 agent 会话、
不调用多轮聊天入口）。本用例以静态依赖扫描固化该边界，防止后续「统一 Agent 化」
把它们静默改成会话链。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: 保持直接推理/专用编码路径、不得会话化的模块（HANDOFF §11.1 原入口清单）。
_DIRECT_PATH_MODULES = (
    "services/ai_service/news_classifier.py",
    "services/ai_service/news_risk.py",
    "services/news_sentiment_scorer.py",
    "services/news_insight_service.py",
    "services/news_sentiment_index_service.py",
    "services/news_subscription_service.py",
)

#: 会话化入口：多轮聊天回合与统一 Agent 服务。
_CONVERSATIONAL_NAMES = frozenset({"execute_chat_turn", "AgentService"})


def _parse(relative: str) -> ast.Module:
    path = _REPO_ROOT / relative
    assert path.is_file(), f"missing module: {relative}"
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("relative", _DIRECT_PATH_MODULES)
def test_direct_path_does_not_import_agent_session_layer(relative: str) -> None:
    imported = _imported_modules(_parse(relative))
    agent_imports = sorted(
        module
        for module in imported
        if module == "services.agent_service" or module.startswith("services.agent_service.")
    )
    assert agent_imports == [], f"{relative} must not import the agent session layer: {agent_imports}"


@pytest.mark.parametrize("relative", _DIRECT_PATH_MODULES)
def test_direct_path_does_not_reference_conversational_entrypoints(relative: str) -> None:
    tree = _parse(relative)
    attribute_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    bare_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    referenced = (_CONVERSATIONAL_NAMES & attribute_names) | (_CONVERSATIONAL_NAMES & bare_names)
    assert referenced == set(), f"{relative} must stay non-conversational, found: {sorted(referenced)}"
