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
    "services/ai_service/gguf_sentiment_adapter.py",
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
            # ``from services import agent_service`` 的 node.module 只到父包，
            # 不拼回被导入子模块就会漏检该形态（绕过守卫）。
            modules.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
    return modules


def _conversational_references(tree: ast.Module) -> set[str]:
    attribute_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    bare_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return set(_CONVERSATIONAL_NAMES & attribute_names) | set(_CONVERSATIONAL_NAMES & bare_names)


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
    referenced = _conversational_references(_parse(relative))
    assert referenced == set(), f"{relative} must stay non-conversational, found: {sorted(referenced)}"


def test_import_scanner_detects_from_package_import_submodule() -> None:
    """守守卫：``from services import agent_service`` 形态必须被识别，否则守卫可被绕过。"""

    tree = ast.parse("from services import agent_service\n")
    assert "services.agent_service" in _imported_modules(tree)


def test_import_scanner_detects_plain_import() -> None:
    tree = ast.parse("import services.agent_service\n")
    assert "services.agent_service" in _imported_modules(tree)


@pytest.mark.parametrize(
    "source",
    [
        "import services.agent_service\nservices.agent_service.AgentService\n",
        "from services.agent_service import service\nexecute_chat_turn()\n",
        "def f(AgentService):\n    return AgentService\n",
    ],
)
def test_conversational_detection_flags_session_entrypoints(source: str) -> None:
    """守守卫：守卫自身必须能认出会话化入口（import / 属性 / 裸名三种形态）。"""

    tree = ast.parse(source)
    agent_imports = [
        module
        for module in _imported_modules(tree)
        if module == "services.agent_service" or module.startswith("services.agent_service.")
    ]
    assert agent_imports or _conversational_references(tree), f"guard failed to detect: {source!r}"
