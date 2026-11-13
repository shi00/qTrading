"""E-03-2 Windows 受管只读工作区单元测试（同实际句柄核最终路径后读取）。

覆盖：受管根内读取、最终路径越界（``..``/符号链接）拒绝、绝对路径/盘符拒绝、
单文件字节上限恰等/超一、只读（无写方法/无 shell）、非普通文件拒绝。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.agent_types import AgentValidationError
from services.agent_service.skill_workspace import ReadOnlyWorkspace


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[ReadOnlyWorkspace, Path]:
    root = tmp_path / "skill-v1"
    root.mkdir()
    (root / "SKILL.md").write_bytes("# 技能\n内容".encode())
    return ReadOnlyWorkspace(str(root)), root


class TestReadWithinRoot:
    def test_reads_exact_text_from_managed_root(self, workspace: tuple[ReadOnlyWorkspace, Path]) -> None:
        ws, _root = workspace
        assert ws.read_text("SKILL.md") == "# 技能\n内容"

    def test_bounded_file_bytes_exact_and_plus_one(self, workspace: tuple[ReadOnlyWorkspace, Path]) -> None:
        ws, root = workspace
        size = (root / "SKILL.md").stat().st_size
        assert len(ws.read_bytes("SKILL.md", max_bytes=size)) == size
        with pytest.raises(AgentValidationError) as exc:
            ws.read_bytes("SKILL.md", max_bytes=size - 1)
        assert exc.value.code == "skill_file_bytes_exceeded"

    def test_directory_read_is_rejected(self, workspace: tuple[ReadOnlyWorkspace, Path]) -> None:
        ws, root = workspace
        (root / "sub").mkdir()
        with pytest.raises(AgentValidationError) as exc:
            ws.read_bytes("sub", max_bytes=10)
        assert exc.value.code == "invalid_skill_path"


class TestPathEscapes:
    @pytest.mark.parametrize("relative", ["../outside.txt", "sub/../../outside.txt", "C:\\Windows\\win.ini"])
    def test_traversal_and_absolute_paths_are_rejected(
        self, workspace: tuple[ReadOnlyWorkspace, Path], relative: str
    ) -> None:
        ws, _root = workspace
        with pytest.raises(AgentValidationError) as exc:
            ws.read_bytes(relative, max_bytes=10)
        assert exc.value.code in {"invalid_skill_path", "skill_path_escape"}

    def test_symlink_escape_is_rejected_by_final_path_check(
        self, workspace: tuple[ReadOnlyWorkspace, Path], tmp_path: Path
    ) -> None:
        ws, root = workspace
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = root / "linked.txt"
        try:
            os.symlink(outside, link)
        except OSError as exc:  # Windows 未启用开发者模式/无权限
            pytest.skip(f"symlink unavailable: {exc}")
        with pytest.raises(AgentValidationError) as exc:
            ws.read_bytes("linked.txt", max_bytes=100)
        assert exc.value.code == "skill_path_escape"

    def test_missing_file_is_rejected(self, workspace: tuple[ReadOnlyWorkspace, Path]) -> None:
        ws, _root = workspace
        with pytest.raises(AgentValidationError) as exc:
            ws.read_bytes("nope.md", max_bytes=10)
        assert exc.value.code == "invalid_skill_path"


class TestReadOnlySurface:
    def test_no_write_or_shell_surface_is_exposed(self, workspace: tuple[ReadOnlyWorkspace, Path]) -> None:
        ws, _root = workspace
        public = {name for name in dir(ws) if not name.startswith("_")}
        assert not any(token in name for name in public for token in ("write", "delete", "exec", "shell", "run"))

    def test_root_must_be_absolute_existing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(AgentValidationError) as exc:
            ReadOnlyWorkspace("relative/root")
        assert exc.value.code == "invalid_workspace_root"
        with pytest.raises(AgentValidationError) as exc:
            ReadOnlyWorkspace(str(tmp_path / "absent"))
        assert exc.value.code == "invalid_workspace_root"
