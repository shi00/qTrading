"""E-03-3 Skill 包四阈值预检、脚本 0 执行与失败保旧版单元测试。

覆盖：D02 四阈值（归档 16MiB / 展开 64MiB / 1024 对象 / 10 秒）在解压前执行、
包内路径逃逸拒绝、脚本仅作数据不执行、版本不可覆盖、失败保持旧版本可读。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from core.agent_types import (
    SKILL_MAX_ARCHIVE_BYTES,
    SKILL_MAX_OBJECTS,
    AgentValidationError,
    SkillVersionRef,
)
from services.agent_service.skills import (
    ManagedSkillStore,
    inspect_skill_package,
    install_skill_version,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class _Clock:
    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class TestPreflightThresholds:
    def test_valid_package_reports_files_and_digests(self) -> None:
        data = _zip_bytes({"SKILL.md": b"# skill", "refs/a.txt": b"abc"})
        inspection = inspect_skill_package(data)
        assert [entry.name for entry in inspection.files] == ["SKILL.md", "refs/a.txt"]
        assert inspection.total_expanded_bytes == 7 + 3

    def test_archive_bytes_checked_before_opening_zip(self) -> None:
        oversized = b"PK\x03\x04" + b"\x00" * (SKILL_MAX_ARCHIVE_BYTES + 1)
        with pytest.raises(AgentValidationError) as exc:
            inspect_skill_package(oversized)
        assert exc.value.code == "skill_archive_bytes_exceeded"

    def test_declared_expanded_bytes_rejected_without_extraction(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("big.bin", b"\x00" * (64 * 1024 * 1024 + 1))
        with pytest.raises(AgentValidationError) as exc:
            inspect_skill_package(buffer.getvalue())
        assert exc.value.code == "skill_expanded_bytes_exceeded"

    def test_object_count_exact_and_plus_one(self) -> None:
        entries = {f"f{i}.txt": b"" for i in range(SKILL_MAX_OBJECTS - 1)}
        entries["SKILL.md"] = b"# ok"
        inspect_skill_package(_zip_bytes(entries))
        entries["extra.txt"] = b"x"
        with pytest.raises(AgentValidationError) as exc:
            inspect_skill_package(_zip_bytes(entries))
        assert exc.value.code == "skill_objects_exceeded"

    def test_elapsed_over_ten_seconds_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            inspect_skill_package(_zip_bytes({"SKILL.md": b"# ok"}), clock=_Clock(step=6.0))
        assert exc.value.code == "skill_time_exceeded"

    @pytest.mark.parametrize("name", ["../evil.txt", "C:\\evil.txt", "/abs/evil.txt"])
    def test_escaping_entry_names_are_rejected(self, name: str) -> None:
        with pytest.raises(AgentValidationError) as exc:
            inspect_skill_package(_zip_bytes({name: b"x"}))
        assert exc.value.code == "skill_path_escape"

    def test_corrupt_archive_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            inspect_skill_package(b"PK\x03\x04not-a-zip")
        assert exc.value.code == "invalid_skill_package"


class TestScriptsAreNeverExecuted:
    def test_install_does_not_execute_scripts(self, tmp_path: Path) -> None:
        marker = tmp_path / "executed.txt"
        script = f"open({str(marker)!r}, 'w').write('boom')\n".encode()
        data = _zip_bytes({"SKILL.md": b"# ok", "scripts/boom.py": script})
        store = ManagedSkillStore(str(tmp_path / "skills"))
        ref = SkillVersionRef(skill_id="demo", version=1)

        install_skill_version(store, ref, data)

        assert not marker.exists()
        assert store.read_file(ref, "scripts/boom.py") == script


class TestVersionPublish:
    def test_publish_then_read_and_current_version(self, tmp_path: Path) -> None:
        store = ManagedSkillStore(str(tmp_path / "skills"))
        install_skill_version(store, SkillVersionRef("demo", 1), _zip_bytes({"SKILL.md": b"# v1"}))
        assert store.current_version("demo") == 1
        assert store.read_file(SkillVersionRef("demo", 1), "SKILL.md") == b"# v1"

    def test_existing_version_is_immutable(self, tmp_path: Path) -> None:
        store = ManagedSkillStore(str(tmp_path / "skills"))
        ref = SkillVersionRef("demo", 1)
        install_skill_version(store, ref, _zip_bytes({"SKILL.md": b"# v1"}))
        with pytest.raises(AgentValidationError) as exc:
            install_skill_version(store, ref, _zip_bytes({"SKILL.md": b"# replaced"}))
        assert exc.value.code == "skill_version_exists"
        assert store.read_file(ref, "SKILL.md") == b"# v1"

    def test_failed_install_keeps_previous_version(self, tmp_path: Path) -> None:
        store = ManagedSkillStore(str(tmp_path / "skills"))
        install_skill_version(store, SkillVersionRef("demo", 1), _zip_bytes({"SKILL.md": b"# v1"}))

        bad = b"PK\x03\x04broken"
        with pytest.raises(AgentValidationError) as exc:
            install_skill_version(store, SkillVersionRef("demo", 2), bad)
        assert exc.value.code == "invalid_skill_package"
        assert store.current_version("demo") == 1
        assert store.read_file(SkillVersionRef("demo", 1), "SKILL.md") == b"# v1"
        assert not (tmp_path / "skills" / "demo" / "2").exists()
