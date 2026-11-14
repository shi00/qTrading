"""D4 生成器测试夹具工厂（检视 §5.2 / §5.2.1 / §6.4 生成负例）。

在临时目录内构造「作者源 + 目标」最小文档树与自定义注册表，验证 docs_generated 的
封闭清单 / 块协议 / 作者源校验 / 更新编排机制。不触碰仓库真实正文（tests/docs 夹具
原则）；仓库真实块的真实渲染等价性由 --check --scope document 在真实根上另行验证。
"""

from __future__ import annotations

import os
from pathlib import Path

import docs_generated as dg

# ---------------------------------------------------------------------------
# 合成作者源与目标模板（全部在临时目录落盘，不指向仓库真实文件）
# ---------------------------------------------------------------------------

VALID_SOURCE_YAML = """\
redlines:
  - id: R1
    title: 演示红线一
    rule_type: INVARIANT
    description: 演示说明一
  - id: R2
    title: 演示红线二
    rule_type: WORKFLOW
    description: 演示说明二
"""

# 目标模板：含空的生成块 + 块外手写正文（正文保留断言的对照素材）
DEMO_TARGET = "# 演示目标\n\n<!-- generated:demo-block -->\n<!-- /generated -->\n\n块外手写正文必须原样保留。\n"

YAML_TARGET = "# 演示 YAML 目标\n# generated:demo-yaml-block\n# /generated:demo-yaml-block\n"

DEMO_SOURCE_REL = "sources/demo.yml"
DEMO_TARGET_REL = "demo.md"
YAML_TARGET_REL = "demo-data.yml"


def demo_render(validated: dict[str, object], root: Path) -> list[str]:
    """演示渲染：按已校验作者源顺序输出 `- {id}：{title}` 行（不含包裹标记）。"""
    entries = validated[DEMO_SOURCE_REL]
    assert isinstance(entries, list), "机制层必须传入已校验的条目列表"
    return [f"- {entry['id']}：{entry['title']}" for entry in entries]


def demo_source_schema(**overrides: object) -> dg.SourceSchema:
    """演示作者源 schema；关键字参数可覆盖任意字段以构造非法 schema 负例。"""
    defaults: dict[str, object] = dict(
        path=DEMO_SOURCE_REL,
        fmt="yaml",
        item_key="redlines",
        required_fields=frozenset({"id", "title", "rule_type"}),
        known_fields=frozenset({"id", "title", "rule_type", "description"}),
        non_empty_string_fields=frozenset({"title"}),
        identity_field="id",
        section_field="rule_type",
        allowed_sections=frozenset({"INVARIANT", "WORKFLOW"}),
    )
    merged = {**defaults, **overrides}
    return dg.SourceSchema(**merged)  # type: ignore[arg-type]  # 测试负例允许故意传非法组合


def make_demo_registry(**spec_overrides: object) -> tuple[dg.BlockSpec, ...]:
    """构造单条目自定义注册表；关键字参数经 dataclasses.replace 覆盖 BlockSpec 字段。"""
    spec = dg.BlockSpec(
        block_id="demo-block",
        target=DEMO_TARGET_REL,
        sources=(demo_source_schema(),),
        render=demo_render,
        block_format="markdown",
        active=True,
    )
    if spec_overrides:
        spec = spec.__class__(
            **{
                **{
                    "block_id": spec.block_id,
                    "target": spec.target,
                    "sources": spec.sources,
                    "render": spec.render,
                    "block_format": spec.block_format,
                    "active": spec.active,
                },
                **spec_overrides,
            }
        )  # type: ignore[arg-type]  # 测试负例允许故意传非法组合
    return (spec,)


def make_yaml_registry() -> tuple[dg.BlockSpec, ...]:
    """YAML 目标形态的自定义注册表（块标记为 `# generated:<id>` 成对注释）。"""
    spec = dg.BlockSpec(
        block_id="demo-yaml-block",
        target=YAML_TARGET_REL,
        sources=(demo_source_schema(),),
        render=demo_render,
        block_format="yaml",
        active=True,
    )
    return (spec,)


def build_demo_tree(tmp_path: Path, *, source_text: str = VALID_SOURCE_YAML, target_text: str = DEMO_TARGET) -> Path:
    """在临时目录落盘「作者源 + 目标」最小文档树，返回 root。"""
    (tmp_path / "sources").mkdir()
    (tmp_path / DEMO_SOURCE_REL).write_text(source_text, encoding="utf-8")
    (tmp_path / DEMO_TARGET_REL).write_text(target_text, encoding="utf-8")
    return tmp_path


def build_yaml_tree(tmp_path: Path) -> Path:
    """落盘 YAML 目标形态的文档树。"""
    (tmp_path / "sources").mkdir()
    (tmp_path / DEMO_SOURCE_REL).write_text(VALID_SOURCE_YAML, encoding="utf-8")
    (tmp_path / YAML_TARGET_REL).write_text(YAML_TARGET, encoding="utf-8")
    return tmp_path


_O_BINARY = getattr(os, "O_BINARY", 0)  # 与生产 WriteGuard 一致：Windows 上禁用 CRT 文本模式翻译


class HeldFdWriteGuard(dg.WriteGuard):
    """测试替身：模拟「写入保护已取得」的持有句柄状态（不提供真实平台独占保护）。

    仅用于在无 Windows 独占打开能力的平台上验证取得保护之后的写入合同（拒绝原位写）；
    真实独占语义仍由 Windows 专用用例覆盖，不得把本替身当作生产保护能力的等价证明。
    """

    def __enter__(self) -> HeldFdWriteGuard:
        self._fds = {path: os.open(str(path), os.O_RDWR | _O_BINARY) for path in self._paths}
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._release()


class AtomicReplaceWriteGuard(dg.WriteGuard):
    """测试替身：模拟「同时满足写入保护与崩溃原子提交」的写入后端（当前生产未提供）。

    生产 ``dg.WriteGuard.write_bytes`` 已按 D28 在任何字节变更前拒绝原位写；本替身仅
    用于在夹具中验证生成器自身逻辑（单源传播、块外保真、幂等），实现为同目录临时
    文件 + ``fsync`` + ``os.replace``（崩溃原子提交），不声称提供真实平台独占保护。
    生产拒绝行为由 test_concurrency 的 D28 负例单独覆盖；不得把本替身当作生产写入
    能力已恢复的证据。
    """

    def __enter__(self) -> AtomicReplaceWriteGuard:
        self._fds = {path: os.open(str(path), os.O_RDWR | _O_BINARY) for path in self._paths}
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._release()

    def write_bytes(self, path: Path, data: bytes) -> None:
        path = Path(path)
        held = self._fds.pop(path, None)
        if held is not None:
            os.close(held)
        tmp = path.with_name(f".{path.name}.d28-test.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o644)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._fds[path] = os.open(str(path), os.O_RDWR)
