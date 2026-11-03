"""scripts/compute_training_fingerprint.py 单测（gh 输出解析的 fail-closed 边界）。

覆盖 ``list_release_tags``：``gh release list`` 输出不可解析 / 结构非法 / 缺 ``tagName`` /
达到查询上限时必须抛 ``FingerprintError``（main 翻译为 exit 2）——不得让裸异常以 exit 1
逃逸，否则与「输入无变化」业务阻断码（同 exit 1）混淆（ADR-0015 退出码契约）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import compute_training_fingerprint as ctf  # noqa: E402 - sys.path 注入后导入


def _payload(tags: list[str]) -> str:
    return json.dumps([{"tagName": tag} for tag in tags])


def test_list_release_tags_returns_parsed_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法 JSON 数组逐个取 ``tagName``（含非模型 Release，由调用方过滤）。"""
    monkeypatch.setattr(ctf, "_run_gh", lambda args: _payload(["corpus-v1", "finbert-gguf-v0.1.0"]))
    assert ctf.list_release_tags("owner/repo") == ["corpus-v1", "finbert-gguf-v0.1.0"]


def test_list_release_tags_excludes_drafts_and_prereleases(monkeypatch: pytest.MonkeyPatch) -> None:
    """查询必须排除 draft / pre-release（D20-F09）：未发布件不得作为去重锚。"""
    calls: list[list[str]] = []

    def fake_gh(args):
        calls.append(list(args))
        return _payload(["finbert-gguf-v0.1.0"])

    monkeypatch.setattr(ctf, "_run_gh", fake_gh)
    ctf.list_release_tags("owner/repo")
    assert calls == [
        [
            "release",
            "list",
            "--repo",
            "owner/repo",
            "--limit",
            str(ctf.RELEASE_LIST_LIMIT),
            "--exclude-drafts",
            "--exclude-pre-releases",
            "--json",
            "tagName",
        ]
    ]


def test_list_release_tags_propagates_gh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """gh 查询失败不得降级为空列表（否则会被误判为首版发布）。"""

    def _boom(args):
        raise ctf.FingerprintError("gh 调用失败（exit 1）: gh release list: connection refused")

    monkeypatch.setattr(ctf, "_run_gh", _boom)
    with pytest.raises(ctf.FingerprintError, match="gh 调用失败"):
        ctf.list_release_tags("owner/repo")


def test_list_release_tags_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 JSON 输出必须 fail-closed（FingerprintError → exit 2，而非裸 JSONDecodeError）。"""
    monkeypatch.setattr(ctf, "_run_gh", lambda args: "not json")
    with pytest.raises(ctf.FingerprintError, match="不是合法 JSON"):
        ctf.list_release_tags("owner/repo")


def test_list_release_tags_rejects_non_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """顶层不是数组（如对象）时拒绝，不按未知语义静默解析。"""
    monkeypatch.setattr(ctf, "_run_gh", lambda args: json.dumps({"tagName": "finbert-gguf-v0.1.0"}))
    with pytest.raises(ctf.FingerprintError, match="结构非法"):
        ctf.list_release_tags("owner/repo")


def test_list_release_tags_rejects_missing_tagname(monkeypatch: pytest.MonkeyPatch) -> None:
    """条目缺 ``tagName`` 字符串字段时拒绝（不得静默生成空 tag）。"""
    monkeypatch.setattr(ctf, "_run_gh", lambda args: json.dumps([{"name": "x"}]))
    with pytest.raises(ctf.FingerprintError, match="tagName"):
        ctf.list_release_tags("owner/repo")


def test_list_release_tags_fails_closed_at_query_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中查询上限时无法保证取到 semver 最新 → fail-closed。"""
    monkeypatch.setattr(
        ctf, "_run_gh", lambda args: _payload([f"finbert-gguf-v0.{index}.0" for index in range(ctf.RELEASE_LIST_LIMIT)])
    )
    with pytest.raises(ctf.FingerprintError, match="查询上限"):
        ctf.list_release_tags("owner/repo")
