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


def test_unregistered_target_with_markers_untouched(tmp_path: Path) -> None:
    """负例：未登记目标即使含合法生成块标记，也不被任何路径扫描写入（封闭清单）。"""
    root = gf.build_demo_tree(tmp_path)
    stray = root / "stray.md"
    stray.write_text("<!-- generated:demo-block -->\n旧内容\n<!-- /generated -->\n", encoding="utf-8")
    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok, f"登记目标应正常更新: {result}"
    assert stray.read_text(encoding="utf-8").startswith("<!-- generated:demo-block -->\n旧内容"), (
        "未登记目标被触碰=扫描到标记就能写，违反封闭清单"
    )


# ---------------------------------------------------------------------------
# 更新编排正例（端到端）
# ---------------------------------------------------------------------------


def test_update_renders_block_and_preserves_prose(tmp_path: Path) -> None:
    """正例：块内容被渲染替换；块外手写正文逐字节保留。"""
    root = gf.build_demo_tree(tmp_path)
    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok, f"更新应成功: {result}"
    assert result.written == (gf.DEMO_TARGET_REL,)
    text = (root / gf.DEMO_TARGET_REL).read_text(encoding="utf-8")
    assert "<!-- generated:demo-block -->" in text
    assert "- R1：演示红线一" in text
    assert "- R2：演示红线二" in text
    assert "块外手写正文必须原样保留。" in text
    assert text.startswith("# 演示目标\n\n<!-- generated:demo-block -->\n")


def test_update_yaml_target(tmp_path: Path) -> None:
    """正例：YAML 目标块按 `# generated:<id>` 成对注释渲染替换。"""
    root = gf.build_yaml_tree(tmp_path)
    result = dg.run_update(root, gf.make_yaml_registry())
    assert result.ok, f"更新应成功: {result}"
    text = (root / gf.YAML_TARGET_REL).read_text(encoding="utf-8")
    assert "# generated:demo-yaml-block" in text
    assert "# /generated:demo-yaml-block" in text
    assert "- R1：演示红线一" in text


def test_render_output_cannot_write_outside_block(tmp_path: Path) -> None:
    """负例：渲染输出携带越界路径不成立——渲染只产出行内容，写入受封闭清单与路径校验约束。"""
    root = gf.build_demo_tree(tmp_path)
    result = dg.run_update(root, gf.make_demo_registry())
    assert result.ok
    # 二次确认：块标记行之外不新增 generated 标记（渲染输出不得伪造块边界）
    text = (root / gf.DEMO_TARGET_REL).read_text(encoding="utf-8")
    assert text.count("<!-- generated:") == 1
    assert text.count("<!-- /generated") == 1


def test_platform_import_guard() -> None:
    """机制自检：docs_generated 在当前平台可导入且声明其保护后端（不静默伪装）。"""
    assert dg.PLATFORM_LOCK_BACKEND in ("win32-exclusive-open", "posix-advisory-unsupported")
    if sys.platform == "win32":
        assert dg.PLATFORM_LOCK_BACKEND == "win32-exclusive-open"


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
