"""D4 生成器机制测试（检视 §5.2 / §6.4「生成」负例与 AC06/AC07 的机制部分）。

覆盖：块协议（缺块/重复/嵌套/孤儿结束/非法 id/结束标记错配）、作者源渲染输入校验
（缺源/缺字段/未知字段/重复身份/非法 section 不得静默取默认值）、路径越界（含符号
链接解析后越界）、封闭目标清单（目标不在清单即拒绝且不被触碰）。全部在临时目录
构造（夹具工厂见 fixtures/generator_fixtures.py），不触碰仓库真实正文。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import docs_generated as dg
from fixtures import generator_fixtures as gf

pytestmark = pytest.mark.docs_pure


# ---------------------------------------------------------------------------
# 块协议：Markdown 成对注释块
# ---------------------------------------------------------------------------


def test_scan_markdown_blocks_single() -> None:
    """正例：单块扫描返回块身份与内容行。"""
    text = "前文\n<!-- generated:a-block -->\n- 内容行\n<!-- /generated -->\n后文\n"
    blocks = dg.scan_blocks(text, "markdown")
    assert set(blocks) == {"a-block"}
    assert blocks["a-block"].content_lines == ("- 内容行",)


def test_scan_markdown_blocks_multiple() -> None:
    """正例：同文档多块按各自身份归位（结束标记不带 id 时按栈配对）。"""
    text = "<!-- generated:first -->\nA\n<!-- /generated -->\n<!-- generated:second -->\nB\n<!-- /generated -->\n"
    blocks = dg.scan_blocks(text, "markdown")
    assert blocks["first"].content_lines == ("A",)
    assert blocks["second"].content_lines == ("B",)


def test_missing_block_rejected() -> None:
    """负例：登记块在文本中缺失 → replace_block 显式拒绝（不静默当空块）。"""
    with pytest.raises(dg.MissingBlockError, match="缺少生成块 'some-block'"):
        dg.replace_block("没有生成块标记的正文", "markdown", "some-block", ["新内容"])


def test_missing_block_via_update_rejected(tmp_path: Path) -> None:
    """负例（端到端）：目标缺块标记 → run_update 拒绝。"""
    root = gf.build_demo_tree(tmp_path, target_text="# 无块目标\n正文\n")
    with pytest.raises(dg.MissingBlockError, match="缺生成块 'demo-block'"):
        dg.run_update(root, gf.make_demo_registry())


def test_invalid_block_id_via_update_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例（端到端，D40）：目标含非法块身份 → run_update 具名拒绝（诊断可定位行号）。"""
    monkeypatch.setattr(dg, "WriteGuard", gf.AtomicReplaceWriteGuard)
    root = gf.build_demo_tree(tmp_path, target_text="<!-- generated:demo.block -->\n<!-- /generated -->\n")
    with pytest.raises(dg.InvalidBlockIdError, match="第 1 行块 id 非法: 'demo.block'"):
        dg.run_update(root, gf.make_demo_registry())


def test_duplicate_block_rejected() -> None:
    """负例：同一块身份出现两次 → 显式拒绝（不静默取第一处）。"""
    text = "<!-- generated:dup -->\nA\n<!-- /generated -->\n<!-- generated:dup -->\nB\n<!-- /generated -->\n"
    with pytest.raises(dg.DuplicateBlockError, match="块身份重复: 'dup'"):
        dg.scan_blocks(text, "markdown")


def test_nested_block_rejected() -> None:
    """负例：块内再出现开始标记（嵌套/重叠）→ 显式拒绝。"""
    text = "<!-- generated:outer -->\n<!-- generated:inner -->\n<!-- /generated -->\n<!-- /generated -->\n"
    with pytest.raises(dg.NestedBlockError, match="块 'outer'（第 1 行起）内嵌套新的块开始标记"):
        dg.scan_blocks(text, "markdown")


def test_orphan_end_rejected() -> None:
    """负例：无开始标记的孤儿结束标记 → 显式拒绝。"""
    text = "正文\n<!-- /generated -->\n正文\n"
    with pytest.raises(dg.OrphanEndError, match="孤儿结束标记"):
        dg.scan_blocks(text, "markdown")


def test_invalid_block_id_rejected() -> None:
    """负例：块 id 含大写/下划线/空形态等非法形式 → 显式拒绝。"""
    for bad_id in ("Bad_Id", "BAD-ID", "_x"):
        text = f"<!-- generated:{bad_id} -->\n<!-- /generated -->\n"
        with pytest.raises(dg.InvalidBlockIdError, match="块 id 非法"):
            dg.scan_blocks(text, "markdown")


def test_end_tag_id_mismatch_rejected() -> None:
    """负例：带 id 的结束标记与开始标记身份不一致 → 显式拒绝。"""
    text = "<!-- generated:alpha -->\n<!-- /generated:beta -->\n"
    with pytest.raises(dg.EndMismatchError, match="结束标记身份不匹配: 'beta'"):
        dg.scan_blocks(text, "markdown")


def test_invalid_block_id_outside_charset_rejected() -> None:
    """负例（D40）：正则字符集外的身份（点号等）不得被当成普通正文静默忽略。"""
    for bad_id in ("bad.id", "bad.id/sub", "bad~id", "v1.0"):
        with pytest.raises(dg.InvalidBlockIdError, match="块 id 非法"):
            dg.scan_blocks(f"<!-- generated:{bad_id} -->\n内容\n<!-- /generated -->\n", "markdown")
        with pytest.raises(dg.InvalidBlockIdError, match="块 id 非法"):
            dg.scan_blocks(f"# generated:{bad_id}\n内容\n# /generated:{bad_id}\n", "yaml")


def test_invalid_end_block_id_rejected() -> None:
    """负例（D40）：结束标记身份非法（点号/空身份）同样具名拒绝，不静默配对。"""
    for text in (
        "<!-- generated:alpha -->\n<!-- /generated:bad.id -->\n",
        "<!-- generated:alpha -->\n<!-- /generated: -->\n",
        "<!-- /generated:bad.id -->\n",
    ):
        with pytest.raises(dg.InvalidBlockIdError, match="id 非法"):
            dg.scan_blocks(text, "markdown")


def test_empty_block_id_rejected() -> None:
    """负例（D40）：空身份开始标记显式拒绝（不是无块普通正文）。"""
    with pytest.raises(dg.InvalidBlockIdError, match="块 id 非法: ''"):
        dg.scan_blocks("<!-- generated: -->\n<!-- /generated -->\n", "markdown")


def test_mixed_valid_and_invalid_block_id_rejected() -> None:
    """负例（D40）：合法 good 之后的非法 bad.id 不得被忽略（不能只返回 good）。"""
    text = "<!-- generated:good -->\nA\n<!-- /generated -->\n<!-- generated:bad.id -->\nB\n<!-- /generated:bad.id -->\n"
    with pytest.raises(dg.InvalidBlockIdError, match="'bad.id'"):
        dg.scan_blocks(text, "markdown")


def test_legal_block_ids_still_recognized() -> None:
    """正例：合法身份（小写字母/数字/连字符）与显式结束身份识别行为不变。"""
    text = "<!-- generated:a1-b2 -->\nA\n<!-- /generated:a1-b2 -->\n"
    blocks = dg.scan_blocks(text, "markdown")
    assert set(blocks) == {"a1-b2"}
    assert blocks["a1-b2"].content_lines == ("A",)


def test_fenced_code_example_not_promoted() -> None:
    """代码示例按角色处理：围栏内标记既不识别为现行块，也不产生非法身份诊断。"""
    text = (
        "```md\n<!-- generated:demo -->\n<!-- /generated -->\n<!-- generated:bad.id -->\n```\n"
        "<!-- generated:real -->\nR\n<!-- /generated -->\n"
    )
    blocks = dg.scan_blocks(text, "markdown")
    assert set(blocks) == {"real"}


def test_inline_code_example_not_promoted() -> None:
    """代码示例按角色处理：行内代码中的标记示例不误升为现行块、不误诊断。"""
    text = "说明：`<!-- generated:bad.id -->` 与 `<!-- generated:demo -->` 仅为示例。\n"
    assert dg.scan_blocks(text, "markdown") == {}


# ---------------------------------------------------------------------------
# 块协议：YAML 成对注释块
# ---------------------------------------------------------------------------


def test_scan_yaml_blocks() -> None:
    """正例：YAML 成对 `# generated:<id>` / `# /generated:<id>` 块扫描。"""
    text = "key: value\n# generated:y-block\n- 行一\n# /generated:y-block\n"
    blocks = dg.scan_blocks(text, "yaml")
    assert blocks["y-block"].content_lines == ("- 行一",)


def test_yaml_block_missing_end_rejected(tmp_path: Path) -> None:
    """负例（端到端）：YAML 目标缺结束标记（块未闭合）→ run_update 显式拒绝。"""
    root = tmp_path
    (root / "sources").mkdir()
    (root / gf.DEMO_SOURCE_REL).write_text(gf.VALID_SOURCE_YAML, encoding="utf-8")
    (root / gf.YAML_TARGET_REL).write_text("# generated:demo-yaml-block\n", encoding="utf-8")
    with pytest.raises(dg.UnclosedBlockError, match="块 'demo-yaml-block'（第 1 行起）缺少结束标记"):
        dg.run_update(root, gf.make_yaml_registry())


# ---------------------------------------------------------------------------
# 作者源渲染输入校验（不得静默取默认值）
# ---------------------------------------------------------------------------


def test_source_missing_rejected(tmp_path: Path) -> None:
    """负例：作者源文件缺失 → 显式拒绝（缺源负例）。"""
    root = tmp_path
    (root / gf.DEMO_TARGET_REL).write_text(gf.DEMO_TARGET, encoding="utf-8")
    with pytest.raises(dg.SourceMissingError, match="作者源缺失: sources/demo.yml"):
        dg.run_update(root, gf.make_demo_registry())


def test_source_required_field_missing_rejected(tmp_path: Path) -> None:
    """负例：作者源条目缺必填字段 → 显式拒绝，渲染函数不得被调用。"""
    broken = gf.VALID_SOURCE_YAML.replace("    title: 演示红线一\n", "")
    root = gf.build_demo_tree(tmp_path, source_text=broken)
    calls: list[int] = []

    def counting_render(validated: dict[str, object], root: Path) -> list[str]:
        calls.append(1)
        return []

    registry = gf.make_demo_registry(render=counting_render)
    with pytest.raises(dg.SourceFieldError, match="缺必填字段 \\['title'\\]"):
        dg.run_update(root, registry)
    assert calls == [], "校验失败时渲染函数被调用=静默取默认值，违规"


def test_source_unknown_field_rejected(tmp_path: Path) -> None:
    """负例：作者源条目含 schema 未登记的未知字段 → 显式拒绝。"""
    broken = gf.VALID_SOURCE_YAML.replace(
        "    rule_type: INVARIANT\n",
        "    rule_type: INVARIANT\n    rogue_field: 值\n",
        1,
    )
    root = gf.build_demo_tree(tmp_path, source_text=broken)
    with pytest.raises(dg.SourceUnknownFieldError, match="未知字段 \\['rogue_field'\\]"):
        dg.run_update(root, gf.make_demo_registry())


def test_source_duplicate_identity_rejected(tmp_path: Path) -> None:
    """负例：作者源条目身份重复（同 id 两条）→ 显式拒绝。"""
    broken = gf.VALID_SOURCE_YAML.replace("  - id: R2", "  - id: R1", 1)
    root = gf.build_demo_tree(tmp_path, source_text=broken)
    with pytest.raises(dg.SourceDuplicateIdentityError, match="身份重复: 'R1'"):
        dg.run_update(root, gf.make_demo_registry())


def test_source_invalid_section_rejected(tmp_path: Path) -> None:
    """负例：section 字段取值不在允许集合 → 显式拒绝。"""
    broken = gf.VALID_SOURCE_YAML.replace("rule_type: WORKFLOW", "rule_type: BOGUS", 1)
    root = gf.build_demo_tree(tmp_path, source_text=broken)
    with pytest.raises(dg.SourceSectionError, match="取值非法: 'BOGUS'"):
        dg.run_update(root, gf.make_demo_registry())


def test_source_yaml_not_dict_rejected(tmp_path: Path) -> None:
    """负例：作者源 YAML 顶层不是映射 → 显式拒绝（不静默当空集合）。"""
    root = gf.build_demo_tree(tmp_path, source_text="- 纯列表\n")
    with pytest.raises(dg.SourceFormatError, match="顶层须为映射"):
        dg.run_update(root, gf.make_demo_registry())


# ---------------------------------------------------------------------------
# D34：必填值形态校验（null/空串/空集合/路径不存在）必须在渲染与写入之前失败
# ---------------------------------------------------------------------------


def _spy_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, source_text: str
) -> tuple[Path, tuple[dg.BlockSpec, ...], list[int], list[Path]]:
    """构造「渲染调用 + 写入调用」双计数夹具（写入阶段被触达即违规）。"""
    root = gf.build_demo_tree(tmp_path, source_text=source_text)
    calls: list[int] = []
    writes: list[Path] = []

    def counting_render(validated: dict[str, object], root: Path) -> list[str]:
        calls.append(1)
        return []

    def recording_write(self: dg.WriteGuard, path: Path, data: bytes) -> None:
        writes.append(Path(path))
        raise AssertionError("非法作者源不得触达写入阶段")

    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    monkeypatch.setattr(gf.HeldFdWriteGuard, "write_bytes", recording_write)
    return root, gf.make_demo_registry(render=counting_render), calls, writes


@pytest.mark.parametrize("bad_title", ["null", "''", "'   '", "123"])
def test_source_invalid_title_rejected_before_render_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_title: str
) -> None:
    """负例：title 为 null/空串/纯空白/非字符串 → 渲染与写入阶段均不得被触达。"""
    broken = gf.VALID_SOURCE_YAML.replace("title: 演示红线一", f"title: {bad_title}")
    root, registry, calls, writes = _spy_update(tmp_path, monkeypatch, source_text=broken)
    with pytest.raises(dg.SourceFieldError, match="title 须为非空字符串"):
        dg.run_update(root, registry)
    assert calls == [], "值形态校验失败时渲染函数被调用=非法值进入了渲染阶段"
    assert writes == [], "值形态校验失败时写入被调用=非法值进入了写入阶段"


@pytest.mark.parametrize("bad_id", ["null", "''", "'   '", "123"])
def test_source_invalid_identity_rejected_before_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_id: str
) -> None:
    """负例：身份字段为 null/空串/纯空白/非字符串 → 渲染前拒绝。"""
    broken = gf.VALID_SOURCE_YAML.replace("  - id: R1", f"  - id: {bad_id}", 1)
    root, registry, calls, writes = _spy_update(tmp_path, monkeypatch, source_text=broken)
    with pytest.raises(dg.SourceFieldError, match="id 须为非空字符串"):
        dg.run_update(root, registry)
    assert calls == []
    assert writes == []


def test_source_empty_item_collection_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：作者源条目集合为空（空 topics/redlines）→ 渲染前显式拒绝。"""
    root, registry, calls, writes = _spy_update(tmp_path, monkeypatch, source_text="redlines: []\n")
    with pytest.raises(dg.SourceFormatError, match="条目集合为空"):
        dg.run_update(root, registry)
    assert calls == []
    assert writes == []


def test_source_null_item_collection_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：作者源条目集合为 null（仅键无值）→ 渲染前显式拒绝。"""
    root, registry, calls, writes = _spy_update(tmp_path, monkeypatch, source_text="redlines:\n")
    with pytest.raises(dg.SourceFormatError, match="缺 'redlines' 列表"):
        dg.run_update(root, registry)
    assert calls == []
    assert writes == []


def _canonical_schema() -> dg.SourceSchema:
    """canonical 主题作者源 schema（路径字段与必填值形态按真实内置 schema 声明）。"""
    return dg.SourceSchema(
        path=gf.DEMO_SOURCE_REL,
        fmt="yaml",
        item_key="topics",
        required_fields=frozenset({"id", "title", "canonical"}),
        known_fields=frozenset({"id", "title", "canonical", "section", "workflow", "note"}),
        non_empty_string_fields=frozenset({"title", "canonical"}),
        path_fields=frozenset({"canonical"}),
        identity_field="id",
    )


def _build_canonical_tree(root: Path, canonical_value: str) -> None:
    """落盘 canonical 主题作者源与目标块（canonical 值由用例注入）。"""
    (root / "sources").mkdir()
    (root / gf.DEMO_SOURCE_REL).write_text(
        f"topics:\n  - id: strategy\n    title: 新增/修改策略\n    canonical: {canonical_value}\n",
        encoding="utf-8",
    )
    (root / gf.DEMO_TARGET_REL).write_text(gf.DEMO_TARGET, encoding="utf-8")


@pytest.mark.parametrize("canonical_value", ["null", "''", "'   '", "docs/nonexistent.md"])
def test_source_canonical_value_shape_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, canonical_value: str
) -> None:
    """负例：canonical 为 null/空串/纯空白/不存在的路径 → 渲染前拒绝。"""
    root = tmp_path
    _build_canonical_tree(root, canonical_value)
    calls: list[int] = []

    def counting_render(validated: dict[str, object], root: Path) -> list[str]:
        calls.append(1)
        return []

    registry = (
        dg.BlockSpec(
            block_id="demo-block",
            target=gf.DEMO_TARGET_REL,
            sources=(_canonical_schema(),),
            render=counting_render,
        ),
    )
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    with pytest.raises(dg.SourceFieldError, match="canonical"):
        dg.run_update(root, registry)
    assert calls == [], "canonical 值形态非法时渲染函数被调用=非法值进入了渲染阶段"


def test_source_canonical_path_exists_accepted(tmp_path: Path) -> None:
    """正例：canonical 指向真实存在的路径 → 校验通过并可供渲染。"""
    root = tmp_path
    (root / "docs" / "patterns").mkdir(parents=True)
    (root / "docs" / "patterns" / "strategy-template.md").write_text("# 策略\n", encoding="utf-8")
    _build_canonical_tree(root, "docs/patterns/strategy-template.md")
    items = dg.load_validated_source(root, _canonical_schema())
    assert isinstance(items, list)
    assert items[0]["canonical"] == "docs/patterns/strategy-template.md"


def test_source_value_shapes_valid_accepted(tmp_path: Path) -> None:
    """正例：合法身份/标题/非空集合正常通过校验并渲染（防误伤）。"""
    root = gf.build_demo_tree(tmp_path)
    items = dg.load_validated_source(root, gf.demo_source_schema())
    assert [item["id"] for item in items if isinstance(item, dict)] == ["R1", "R2"]
    assert gf.demo_render({gf.DEMO_SOURCE_REL: items}, root) == ["- R1：演示红线一", "- R2：演示红线二"]


# ---------------------------------------------------------------------------
# 路径安全：越界与符号链接
# ---------------------------------------------------------------------------


def test_parent_escape_target_rejected(tmp_path: Path) -> None:
    """负例：目标路径 `..` 逃逸出 root → 显式拒绝。"""
    import dataclasses

    spec = dataclasses.replace(gf.make_demo_registry()[0], target="../escaped.md")
    root = gf.build_demo_tree(tmp_path)
    with pytest.raises(dg.PathEscapeError, match="路径解析后越界"):
        dg.run_update(root, (spec,))


def test_absolute_target_rejected(tmp_path: Path) -> None:
    """负例：目标为绝对路径（Path 拼接覆盖语义）→ 显式拒绝。"""
    import dataclasses

    outside = tmp_path.parent / "absolute-outside.md"
    spec = dataclasses.replace(gf.make_demo_registry()[0], target=str(outside))
    root = gf.build_demo_tree(tmp_path)
    with pytest.raises(dg.PathEscapeError, match="路径解析后越界"):
        dg.run_update(root, (spec,))


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    """负例：符号链接解析后指向 root 外 → 显式拒绝。"""
    outside = tmp_path.parent / "symlink-outside.md"
    outside.write_text("外部文件\n", encoding="utf-8")
    root = gf.build_demo_tree(tmp_path)
    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境无符号链接创建权限（Windows 非开发者模式），负例在 POSIX CI 真跑")
    import dataclasses

    spec = dataclasses.replace(gf.make_demo_registry()[0], target="link.md")
    with pytest.raises(dg.PathEscapeError, match="路径解析后越界"):
        dg.run_update(root, (spec,))


# ---------------------------------------------------------------------------
# 封闭目标清单
# ---------------------------------------------------------------------------


def test_only_outside_registry_rejected(tmp_path: Path) -> None:
    """负例：only 请求的目标不在封闭清单 → 显式拒绝。"""
    root = gf.build_demo_tree(tmp_path)
    with pytest.raises(dg.TargetNotRegisteredError, match="目标不在封闭清单: \\['ghost.md'\\]"):
        dg.run_update(root, gf.make_demo_registry(), only=("ghost.md",))


def test_unregistered_target_with_markers_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：未登记目标即使含合法生成块标记，也不被任何路径扫描或写入（封闭清单）。"""
    root = gf.build_demo_tree(tmp_path)
    stray = root / "stray.md"
    stray.write_text("<!-- generated:demo-block -->\n旧内容\n<!-- /generated -->\n", encoding="utf-8")
    before = stray.read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok is False and result.written == (), "原位写被拒绝时不得把目标记为已写"
    assert stray.read_bytes() == before, "未登记目标被触碰=扫描到标记就能写，违反封闭清单"


# ---------------------------------------------------------------------------
# 更新编排正例（端到端）
# ---------------------------------------------------------------------------


def test_update_renders_block_and_preserves_prose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正例：块内容被渲染替换、块外手写正文逐字节保留；原位写被拒绝时文件保持原样。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    original = target.read_text(encoding="utf-8")
    rendered = dg.render_block_lines(root, gf.make_demo_registry()[0])
    text = dg.replace_block(original, "markdown", "demo-block", rendered)
    assert "<!-- generated:demo-block -->" in text
    assert "- R1：演示红线一" in text
    assert "- R2：演示红线二" in text
    assert "块外手写正文必须原样保留。" in text
    assert text.startswith("# 演示目标\n\n<!-- generated:demo-block -->\n")

    before = target.read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok is False and result.written == (), "拒绝原位写时不得记为已写"
    assert target.read_bytes() == before, "拒绝原位写不得改动目标（含块外手写正文）"


def test_update_yaml_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正例：YAML 目标块按 `# generated:<id>` 成对注释渲染替换；原位写被拒绝时保持原样。"""
    root = gf.build_yaml_tree(tmp_path)
    target = root / gf.YAML_TARGET_REL
    original = target.read_text(encoding="utf-8")
    rendered = dg.render_block_lines(root, gf.make_yaml_registry()[0])
    text = dg.replace_block(original, "yaml", "demo-yaml-block", rendered)
    assert "# generated:demo-yaml-block" in text
    assert "# /generated:demo-yaml-block" in text
    assert "- R1：演示红线一" in text

    before = target.read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    result = dg.run_update(root, gf.make_yaml_registry())
    assert result.ok is False and result.written == ()
    assert target.read_bytes() == before


def test_render_output_cannot_write_outside_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """负例：渲染输出只产出行内容，不伪造块边界；原位写被拒绝时目标保持原样。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    original = target.read_text(encoding="utf-8")
    rendered = dg.render_block_lines(root, gf.make_demo_registry()[0])
    text = dg.replace_block(original, "markdown", "demo-block", rendered)
    # 块标记行之外不新增 generated 标记（渲染输出不得伪造块边界）
    assert text.count("<!-- generated:") == 1
    assert text.count("<!-- /generated") == 1

    before = target.read_bytes()
    monkeypatch.setattr(dg, "WriteGuard", gf.HeldFdWriteGuard)
    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok is False and result.written == ()
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# D35：写后回调失败仍保完整提交状态报告
# ---------------------------------------------------------------------------


class _PartiallyFailingAtomicGuard(gf.AtomicReplaceWriteGuard):
    """测试替身：ok.md 原子替换成功，dead.md 写失败（模拟部分目标未写）。"""

    def write_bytes(self, path: Path, data: bytes) -> None:
        if Path(path).name == "dead.md":
            raise OSError("模拟写失败")
        super().write_bytes(path, data)


def _two_target_registry() -> tuple[dg.BlockSpec, ...]:
    return tuple(
        dg.BlockSpec(
            block_id=f"demo-{Path(rel).stem}",
            target=rel,
            sources=(gf.demo_source_schema(),),
            render=gf.demo_render,
        )
        for rel in ("ok.md", "dead.md")
    )


def test_update_post_check_failure_keeps_written_failed_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写后回调抛约定异常：仍返回完整 UpdateResult（已写/写失败/后检查错误）。"""
    root = gf.build_demo_tree(tmp_path)
    for rel in ("ok.md", "dead.md"):
        (root / rel).write_text(
            f"# {rel}\n\n<!-- generated:demo-{Path(rel).stem} -->\n<!-- /generated -->\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(dg, "WriteGuard", _PartiallyFailingAtomicGuard)

    def bad_post_check() -> list[str]:
        raise dg.SourceMissingError("更新后检查坏输入")

    result = dg.run_update(root, _two_target_registry(), post_check=bad_post_check)
    assert result.written == ("ok.md",)
    assert result.failed == ("dead.md",)
    assert len(result.post_check_errors) == 1
    assert "更新后 all 检查执行失败" in result.post_check_errors[0]
    assert "更新后检查坏输入" in result.post_check_errors[0]
    assert result.ok is False


def test_update_post_check_programming_defect_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写后回调的程序缺陷（TypeError）必须上抛，不得伪装成后检查失败。"""
    root = gf.build_demo_tree(tmp_path)
    monkeypatch.setattr(dg, "WriteGuard", gf.AtomicReplaceWriteGuard)

    def defective_post_check() -> list[str]:
        raise TypeError("代码缺陷")

    with pytest.raises(TypeError, match="代码缺陷"):
        dg.run_update(root, gf.make_demo_registry(), post_check=defective_post_check)


# ---------------------------------------------------------------------------
# D41：受控块替换只改块内字节，块外行尾逐字节保真
# ---------------------------------------------------------------------------

_START = "<!-- generated:b -->"
_END = "<!-- /generated -->"


def _block_parts(text: str, block_id: str = "b") -> tuple[str, str]:
    """返回受控块之外的前缀/后缀原文（按各自原始行尾拼接，用于逐字节比较）。"""
    span = dg.scan_blocks(text, "markdown")[block_id]
    lines = text.splitlines(keepends=True)
    return "".join(lines[: span.start_line - 1]), "".join(lines[span.end_line :])


@pytest.mark.parametrize(
    ("case", "head", "tail", "block_nl"),
    [
        ("all-lf", "# 头\n\n", "\n尾\n", "\n"),
        ("all-crlf", "# 头\r\n\r\n", "\r\n尾\r\n", "\r\n"),
        ("head-tail-lf-block-crlf", "# 头\n\n", "\n尾\n", "\r\n"),
        ("head-tail-crlf-block-lf", "# 头\r\n\r\n", "\r\n尾\r\n", "\n"),
    ],
)
def test_replace_block_preserves_outside_line_endings(case: str, head: str, tail: str, block_nl: str) -> None:
    """LF / CRLF / 混合行尾：只替换块内字节，块外原文逐字节不变（D41）。"""
    text = f"{head}{_START}{block_nl}- 旧{block_nl}{_END}{tail}"
    new = ["- 新一", "- 新二"]
    span = dg.scan_blocks(text, "markdown")["b"]
    lines = text.splitlines(keepends=True)
    start_idx, end_idx = span.start_line - 1, span.end_line - 1
    expected = (
        "".join(lines[:start_idx])
        + _START
        + block_nl
        + "".join(f"{line}{block_nl}" for line in new)
        + lines[end_idx]
        + "".join(lines[end_idx + 1 :])
    )

    out = dg.replace_block(text, "markdown", "b", new)

    assert _block_parts(out) == _block_parts(text), f"{case}: 块外字节被改写"
    assert out == expected, f"{case}: 块内内容或行尾不符合预期"
    assert dg.scan_blocks(out, "markdown")["b"].content_lines == tuple(new)
    assert dg.replace_block(out, "markdown", "b", new) == out, f"{case}: 二次替换非零差异"


def test_replace_block_preserves_missing_trailing_newline() -> None:
    """无末尾换行：替换块内容后文件仍不以换行结尾，块前字节不变（D41）。"""
    text = f"# 头\n\n{_START}\n- 旧\n{_END}"
    new = ["- 新"]
    prefix, suffix = _block_parts(text)
    assert suffix == "", "夹具前提：块结束标记即文件末尾"

    out = dg.replace_block(text, "markdown", "b", new)

    assert _block_parts(out) == (prefix, suffix)
    assert out == f"{prefix}{_START}\n- 新\n{_END}"
    assert not out.endswith("\n")
    assert dg.replace_block(out, "markdown", "b", new) == out


def test_replace_block_boundaries() -> None:
    """边界：块在文件首/尾、空块时块外字节仍完全保真，二次替换零差异（D41）。"""
    cases = {
        "file-start": f"{_START}\n- 旧\n{_END}\n尾\n",
        "file-end": f"# 头\n{_START}\n- 旧\n{_END}",
        "empty-block": f"# 头\n{_START}\n{_END}\n尾\n",
    }
    new = ["- 新"]
    for case, text in cases.items():
        prefix, suffix = _block_parts(text)
        out = dg.replace_block(text, "markdown", "b", new)
        assert _block_parts(out) == (prefix, suffix), f"{case}: 块外字节被改写"
        assert dg.scan_blocks(out, "markdown")["b"].content_lines == tuple(new), case
        assert dg.replace_block(out, "markdown", "b", new) == out, f"{case}: 二次替换非零差异"


def test_update_preserves_mixed_line_endings_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：手写头/尾 LF、受控块 CRLF，更新后块外字节不变且二次运行零差异（D41）。"""
    root = gf.build_demo_tree(tmp_path)
    target = root / gf.DEMO_TARGET_REL
    original = "# 演示目标\n\n<!-- generated:demo-block -->\r\n<!-- /generated -->\r\n\n块外手写正文必须原样保留。\n"
    target.write_bytes(original.encode("utf-8"))
    prefix, suffix = _block_parts(original, "demo-block")
    monkeypatch.setattr(dg, "WriteGuard", gf.AtomicReplaceWriteGuard)

    result = dg.run_update(root, gf.make_demo_registry())

    assert result.ok and result.written == (gf.DEMO_TARGET_REL,)
    after = target.read_bytes().decode("utf-8")  # 不经通用换行转换读取，保留原始 CRLF
    assert _block_parts(after, "demo-block") == (prefix, suffix), "块外手写正文的行尾被改写"
    assert "- R1：演示红线一\r\n- R2：演示红线二\r\n" in after, "块内应沿用受控块自身的 CRLF"
    second = dg.run_update(root, gf.make_demo_registry())
    assert second.ok and second.written == () and second.unchanged == (gf.DEMO_TARGET_REL,)


def test_platform_import_guard() -> None:
    """机制自检：docs_generated 在当前平台可导入且声明其保护/原位写后端（不静默伪装）。"""
    assert dg.PLATFORM_LOCK_BACKEND in ("win32-exclusive-open", "posix-advisory-unsupported")
    if sys.platform == "win32":
        assert dg.PLATFORM_LOCK_BACKEND == "win32-exclusive-open"
    # D28：无已验证「保护 + 崩溃原子提交」方案前，所有平台的值都必须是拒绝态
    assert dg.INPLACE_WRITE_BACKEND == "refused-unverified-atomic-commit"


def test_docs_index_candidates_order_is_platform_independent(tmp_path: Path) -> None:
    """文件级目录候选集按 POSIX 相对路径排序：`README.md` 必须排在 `adr/` 之前。

    平台回归：Path 自带排序在 Windows 大小写不敏感（readme 落在 adr 之后）、Linux 按
    ASCII（README 在 adr 之前），同一作者源两平台渲染次序不同 → 生成块跨平台判不一致
    （PR #2196 首轮 Docs Static Checks / Code Quality 即因此失败）。
    """
    docs = tmp_path / "docs"
    (docs / "adr").mkdir(parents=True)
    (docs / "README.md").write_text("# 索引\n", encoding="utf-8")
    (docs / "adr" / "0001-x.md").write_text("# X\n", encoding="utf-8")

    order = [p.relative_to(tmp_path).as_posix() for p in dg._docs_index_candidates(tmp_path)]

    assert order == ["docs/README.md", "docs/adr/0001-x.md"]
