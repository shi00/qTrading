"""Q14：治理 YAML 重复 mapping 键拒绝（共用安全 loader）。

覆盖 docs_generated 作者源加载与 check_docs_consistency 治理检查两条入口：
顶层 / 条目 / 嵌套重复键均显式拒绝并报告字段路径与位置；合法 YAML（含列表重复值、
merge 键、别名）不被误拒；重复身份校验仍由列表身份去重承担。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import check_docs_consistency as cdc
import docs_generated as dg

pytestmark = pytest.mark.docs_pure

_TOP_LEVEL_DUP = "redlines: []\nredlines: []\n"
_ITEM_DUP = "redlines:\n  - id: R1\n    title: 同名标题\n    title: 同名标题\n    rule_type: INVARIANT\n"
_NESTED_DUP = (
    "redlines:\n  - id: R1\n    title: T\n    rule_type: INVARIANT\n    checks:\n      - name: a\n        name: b\n"
)
_LEGAL_YAML = (
    "topics:\n"
    "  - id: a\n"
    "    title: 相同标题\n"
    "    aliases: [x, x]\n"
    "  - id: b\n"
    "    title: 相同标题\n"
    "    aliases: [x, x]\n"
    "merged:\n"
    "  base: &base\n"
    "    k: 1\n"
    "  child:\n"
    "    <<: *base\n"
    "    k: 2\n"
)
_REDLINES_SCHEMA = dg.SourceSchema(
    path="docs/governance/redlines.yml",
    fmt="yaml",
    item_key="redlines",
    required_fields=frozenset({"id", "title", "rule_type"}),
    known_fields=frozenset({"id", "title", "rule_type"}),
    non_empty_string_fields=frozenset({"id", "title", "rule_type"}),
    identity_field="id",
)


@pytest.mark.parametrize(
    ("text", "key", "path", "line", "column"),
    [
        (_TOP_LEVEL_DUP, "redlines", "redlines", 2, 1),
        (_ITEM_DUP, "title", "redlines[0].title", 4, 5),
        (_NESTED_DUP, "name", "redlines[0].checks[0].name", 7, 9),
    ],
)
def test_duplicate_mapping_key_rejected_with_path_and_position(
    text: str, key: str, path: str, line: int, column: int
) -> None:
    # 旧行为证据：yaml.safe_load 接受重复键并静默取最后值，不抛错。
    assert yaml.safe_load(text) is not None
    with pytest.raises(dg.DuplicateMappingKeyError) as excinfo:
        dg.load_yaml(text, source="docs/governance/redlines.yml")
    error = excinfo.value
    assert error.key == key
    assert error.path == path
    assert (error.line, error.column) == (line, column)
    message = str(error)
    assert "重复 mapping 键" in message
    assert path in message
    assert f"第 {line} 行第 {column} 列" in message


def test_identical_duplicate_values_still_rejected() -> None:
    """重复键取值完全相同、折叠后生成结果相等，也不能代替源唯一性校验。"""
    duplicate = "redlines:\n  - id: R1\n    title: 同\n    title: 同\n    rule_type: INVARIANT\n"
    single = "redlines:\n  - id: R1\n    title: 同\n    rule_type: INVARIANT\n"
    assert yaml.safe_load(duplicate) == yaml.safe_load(single)
    with pytest.raises(dg.DuplicateMappingKeyError) as excinfo:
        dg.load_yaml(duplicate, source="docs/governance/redlines.yml")
    error = excinfo.value
    assert error.key == "title"
    assert error.path == "redlines[0].title"
    assert (error.line, error.column) == (4, 5)


def test_legal_yaml_passes_unchanged() -> None:
    data = dg.load_yaml(_LEGAL_YAML, source="docs/governance/canonical-topics.yml")
    assert data == yaml.safe_load(_LEGAL_YAML)
    assert data["topics"][0]["aliases"] == ["x", "x"]  # 列表重复值不是 mapping 重复键
    assert data["merged"]["child"] == {"k": 2}  # 显式键覆盖 merge 键属合法语义


def test_recursive_alias_does_not_loop() -> None:
    data = dg.load_yaml("a: &a\n  self: *a\n", source="recursive.yml")
    assert data["a"]["self"] is data["a"]


def test_generated_source_loader_rejects_duplicate_title(tmp_path: Path) -> None:
    with pytest.raises(dg.SourceFormatError) as excinfo:
        dg.load_validated_source(tmp_path, _REDLINES_SCHEMA, text=_ITEM_DUP)
    message = str(excinfo.value)
    assert "重复 mapping 键 'title'" in message
    assert "redlines[0].title" in message


def test_generated_source_loader_allows_repeated_values(tmp_path: Path) -> None:
    text = (
        "redlines:\n"
        "  - id: R1\n    title: T\n    rule_type: INVARIANT\n"
        "  - id: R2\n    title: T\n    rule_type: INVARIANT\n"
    )
    items = dg.load_validated_source(tmp_path, _REDLINES_SCHEMA, text=text)
    assert isinstance(items, list)
    assert [item["id"] for item in items] == ["R1", "R2"]


def test_generated_source_loader_keeps_identity_dedup(tmp_path: Path) -> None:
    text = (
        "redlines:\n"
        "  - id: R1\n    title: T\n    rule_type: INVARIANT\n"
        "  - id: R1\n    title: T2\n    rule_type: INVARIANT\n"
    )
    with pytest.raises(dg.SourceDuplicateIdentityError) as excinfo:
        dg.load_validated_source(tmp_path, _REDLINES_SCHEMA, text=text)
    assert "身份重复: 'R1'" in str(excinfo.value)


def test_governance_check_rejects_duplicate_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "redlines.yml"
    path.write_text(_ITEM_DUP, encoding="utf-8")
    monkeypatch.setattr(cdc, "REDLINES_YAML_PATH", path)
    errors = cdc.check_redlines_yaml_consistency()
    joined = "\n".join(errors)
    assert "重复 mapping 键 'title'" in joined
    assert "redlines[0].title" in joined
    assert "第 4 行第 5 列" in joined


@pytest.mark.parametrize("check_name", ["check_agents_md_sync", "check_claude_executive_sync"])
def test_sync_checks_report_duplicate_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check_name: str
) -> None:
    path = tmp_path / "redlines.yml"
    path.write_text(_TOP_LEVEL_DUP, encoding="utf-8")
    monkeypatch.setattr(cdc, "REDLINES_YAML_PATH", path)
    errors = getattr(cdc, check_name)()
    assert any("重复 mapping 键" in error for error in errors)
