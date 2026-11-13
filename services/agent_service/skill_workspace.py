"""受管只读工作区（E-03 / 详细设计 §10.3）。

合同：
- 只暴露受管 Skill 版本目录内的读取；不提供写/删除/执行/shell 方法；
- 打开文件后以**同一个实际句柄**取最终路径（Windows ``GetFinalPathNameByHandleW``，
  POSIX ``/proc/self/fd``）再读取，符号链接/junction 越界与上跳路径拒绝；
- 单文件读取有显式字节上限，恰等通过、超一拒绝；
- 不给项目根、不展开通配符、不跟随 ``..``。

本模块是被动读取器，不是任意恶意文件的 OS 级沙箱。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePath
from typing import NoReturn

from core.agent_types import AgentValidationError

_IS_WINDOWS = os.name == "nt"


def _fail(code: str, path: str) -> NoReturn:
    raise AgentValidationError(code, path)


def _final_path(fd: int) -> str:
    """同一句柄的最终路径（不按调用方传入路径推断）。"""

    if _IS_WINDOWS:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(fd)
        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(  # type: ignore[attr-defined]  # Windows-only API
            wintypes.HANDLE(handle), buffer, len(buffer), 0
        )
        if length == 0 or length >= len(buffer):
            _fail("invalid_skill_path", "$.skill.path")
        return buffer.value
    link = f"/proc/self/fd/{fd}"
    if os.path.exists(link):
        return os.readlink(link)
    _fail("unsupported_platform", "$.skill.path")
    raise AssertionError("unreachable")


def _comparable(path: str) -> str:
    """去除 Windows 扩展前缀并统一大小写语义（不改变实际读取目标）。"""

    text = path
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[len("\\\\?\\UNC\\") :]
    elif text.startswith("\\\\?\\"):
        text = text[len("\\\\?\\") :]
    return os.path.normcase(os.path.normpath(text))


class ReadOnlyWorkspace:
    """受管只读目录：所有读取都核最终路径仍在受管根内。"""

    def __init__(self, root: str) -> None:
        if not isinstance(root, str) or not root or not Path(root).is_absolute():
            _fail("invalid_workspace_root", "$.skill.root")
        if not Path(root).is_dir():
            _fail("invalid_workspace_root", "$.skill.root")
        self._root = os.path.realpath(root)
        self._root_cmp = _comparable(self._root) + os.sep

    @property
    def root(self) -> str:
        return self._root

    def read_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        """读取受管根内单个普通文件；越界/非普通文件/超限显式拒绝。"""

        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
            _fail("invalid_type", "$.skill.max_bytes")
        target = self._checked_join(relative_path)
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError:
            _fail("invalid_skill_path", f"$.skill.path[{relative_path}]")
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                _fail("invalid_skill_path", f"$.skill.path[{relative_path}]")
            final = _final_path(fd)
            if not _comparable(final).startswith(self._root_cmp):
                _fail("skill_path_escape", f"$.skill.path[{relative_path}]")
            data = os.read(fd, max_bytes + 1)
        finally:
            os.close(fd)
        if len(data) > max_bytes:
            _fail("skill_file_bytes_exceeded", f"$.skill.path[{relative_path}]")
        return data

    def read_text(self, relative_path: str, *, max_bytes: int = 1_048_576) -> str:
        try:
            return self.read_bytes(relative_path, max_bytes=max_bytes).decode("utf-8-sig")
        except UnicodeDecodeError:
            _fail("invalid_encoding", f"$.skill.path[{relative_path}]")

    def _checked_join(self, relative_path: str) -> str:
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            _fail("invalid_skill_path", "$.skill.path")
        pure = PurePath(relative_path)
        if pure.is_absolute() or pure.drive or pure.root:
            _fail("invalid_skill_path", "$.skill.path")
        if any(part in {"..", ""} for part in pure.parts):
            _fail("invalid_skill_path", "$.skill.path")
        return os.path.join(self._root, *pure.parts)
