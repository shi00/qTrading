"""E-03-4a Skill 包 SKILL.md 前台/布局/内容摘要/导入决策单元测试。

覆盖（需求 F12 / D02 / UX DSH-Skill导入）：
- 根目录恰好一个有效 SKILL.md；嵌套或缺失 → 拒绝并提示拆包；
- frontmatter 严格解析（``---`` 包裹、description 必填、布尔仅 YAML 1.2 拼写）；
- 名称按 Harness 合同校验（NFKC、≤64、小写字母数字与单连字符）；
- 同名判定去首尾空白且不区分大小写（NFKC + casefold）；
- 未知/缺失许可拒绝；依赖项必须是合法 Skill 名且去重；
- Harness 行为字段被接受但不实现（只记录，不赋权）；
- 规范内容摘要对文件顺序不敏感、对内容变化敏感；
- 导入决策：新建/未变化/需确认更新/确认更新。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from core.agent_types import AgentValidationError, sha256_hex, stable_id
from services.agent_service.skills import (
    SkillImportStatus,
    content_digest_of_files,
    decide_skill_import,
    inspect_skill_package,
    normalize_skill_name,
    parse_skill_manifest,
    validate_skill_name,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _manifest(**fields: object) -> bytes:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, (tuple, list)):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append("# Instructions")
    return "\n".join(lines).encode("utf-8")


_DEFAULT_MANIFEST = _manifest(name="demo-skill", description="demo description", license="MIT")


def _inspect(entries: dict[str, bytes]):
    return inspect_skill_package(_zip_bytes(entries))


class TestSkillManifestParsing:
    def test_valid_frontmatter_parsed(self) -> None:
        parsed = parse_skill_manifest(_DEFAULT_MANIFEST, directory_name="ignored")
        assert parsed.name == "demo-skill"
        assert parsed.description == "demo description"
        assert parsed.license_id == "MIT"
        assert parsed.dependencies == ()
        assert parsed.model_invocable is True
        assert parsed.user_invocable is True

    def test_name_falls_back_to_directory_name(self) -> None:
        data = _manifest(description="demo", license="Apache-2.0")
        parsed = parse_skill_manifest(data, directory_name="dir-skill")
        assert parsed.name == "dir-skill"

    def test_missing_frontmatter_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(b"# no frontmatter\n", directory_name="demo")
        assert exc.value.code == "invalid_skill_manifest"

    def test_unclosed_frontmatter_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(b"---\nname: demo\ndescription: x\n", directory_name="demo")
        assert exc.value.code == "invalid_skill_manifest"

    def test_missing_description_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(_manifest(name="demo", license="MIT"), directory_name="demo")
        assert exc.value.code == "invalid_skill_manifest"

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(_manifest(name="demo", description="   ", license="MIT"), directory_name="demo")
        assert exc.value.code == "invalid_skill_manifest"

    @pytest.mark.parametrize("license_value", ["WTFPL", "BSD-4-Clause", "MIT OR Apache-2.0"])
    def test_unknown_license_rejected(self, license_value: str) -> None:
        data = _manifest(name="demo", description="demo", license=license_value)
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(data, directory_name="demo")
        assert exc.value.code == "skill_license_unknown"

    def test_missing_license_rejected(self) -> None:
        data = _manifest(name="demo", description="demo")
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(data, directory_name="demo")
        assert exc.value.code == "skill_license_unknown"

    @pytest.mark.parametrize("license_value", ["Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "LGPL-3.0-only"])
    def test_allowed_licenses_accepted(self, license_value: str) -> None:
        data = _manifest(name="demo", description="demo", license=license_value)
        assert parse_skill_manifest(data, directory_name="demo").license_id == license_value

    @pytest.mark.parametrize(
        "name",
        ["Demo", "demo skill", "-demo", "demo-", "de--mo", "a" * 65, "demo_skill"],
    )
    def test_invalid_skill_names_rejected(self, name: str) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_skill_name(name)
        assert exc.value.code == "invalid_skill_name"

    def test_behavioral_fields_are_recorded_not_enforced(self) -> None:
        data = _manifest(
            name="demo",
            description="demo",
            license="MIT",
            **{"allowed-tools": ("Read", "Grep"), "model": "some-model", "dependencies": ("base-skill",)},
        )
        parsed = parse_skill_manifest(data, directory_name="demo")
        assert parsed.ignored_behavioral_fields == ("allowed-tools", "dependencies", "model")
        assert parsed.dependencies == ("base-skill",)

    def test_dependency_must_be_valid_skill_name(self) -> None:
        data = _manifest(name="demo", description="demo", license="MIT", dependencies=("Bad Name",))
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(data, directory_name="demo")
        assert exc.value.code == "invalid_skill_dependency"

    def test_duplicate_dependencies_rejected(self) -> None:
        data = _manifest(name="demo", description="demo", license="MIT", dependencies=("a", "a"))
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(data, directory_name="demo")
        assert exc.value.code == "invalid_skill_dependency"

    @pytest.mark.parametrize("value", ["yes", "1", "on"])
    def test_boolean_fields_only_accept_yaml12_spellings(self, value: str) -> None:
        data = _manifest(
            name="demo",
            description="demo",
            license="MIT",
            **{"disable-model-invocation": value},
        )
        with pytest.raises(AgentValidationError) as exc:
            parse_skill_manifest(data, directory_name="demo")
        assert exc.value.code == "invalid_skill_manifest"

    def test_boolean_fields_are_case_insensitive(self) -> None:
        data = _manifest(
            name="demo",
            description="demo",
            license="MIT",
            **{"disable-model-invocation": "TRUE"},
        )
        assert parse_skill_manifest(data, directory_name="demo").model_invocable is False

    def test_disable_model_invocation_false_accepted(self) -> None:
        data = _manifest(
            name="demo",
            description="demo",
            license="MIT",
            **{"disable-model-invocation": "false"},
        )
        assert parse_skill_manifest(data, directory_name="demo").model_invocable is True


class TestPackageLayout:
    def test_missing_root_manifest_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _inspect({"refs/a.txt": b"x"})
        assert exc.value.code == "skill_manifest_missing"

    def test_only_nested_manifest_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _inspect({"nested/SKILL.md": _DEFAULT_MANIFEST})
        assert exc.value.code == "skill_manifest_not_root"

    def test_multiple_candidates_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _inspect({"SKILL.md": _DEFAULT_MANIFEST, "nested/SKILL.md": _DEFAULT_MANIFEST})
        assert exc.value.code == "skill_multiple_candidates"

    def test_case_insensitive_manifest_name_accepted(self) -> None:
        inspection = _inspect({"skill.md": _DEFAULT_MANIFEST})
        assert inspection.manifest_text.startswith("---\n")

    def test_invalid_utf8_manifest_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _inspect({"SKILL.md": b"---\nname: demo\n\xff\n---"})
        assert exc.value.code == "invalid_skill_manifest"


class TestContentDigest:
    def test_file_order_does_not_change_digest(self) -> None:
        first = _inspect({"SKILL.md": _DEFAULT_MANIFEST, "a.txt": b"a", "b.txt": b"b"})
        second = _inspect({"b.txt": b"b", "SKILL.md": _DEFAULT_MANIFEST, "a.txt": b"a"})
        assert content_digest_of_files(first.files) == content_digest_of_files(second.files)

    def test_content_change_changes_digest(self) -> None:
        first = _inspect({"SKILL.md": _DEFAULT_MANIFEST, "a.txt": b"a"})
        second = _inspect({"SKILL.md": _DEFAULT_MANIFEST, "a.txt": b"b"})
        assert content_digest_of_files(first.files) != content_digest_of_files(second.files)

    def test_digest_is_sha256_hex(self) -> None:
        inspection = _inspect({"SKILL.md": _DEFAULT_MANIFEST})
        digest = content_digest_of_files(inspection.files)
        assert len(digest) == 64 and digest == digest.lower()


class TestNameIdentity:
    def test_name_normalization_trims_and_casefolds(self) -> None:
        assert normalize_skill_name("  Demo-Skill  ") == "demo-skill"
        assert normalize_skill_name("DEMO-SKILL") == normalize_skill_name("demo-skill")
        assert normalize_skill_name("ｄｅｍｏ") == normalize_skill_name("demo")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            normalize_skill_name("   ")
        assert exc.value.code == "invalid_skill_name"

    def test_stable_identity_binds_user_and_name(self) -> None:
        first = stable_id("skill", ["scope", normalize_skill_name("Demo")])
        second = stable_id("skill", ["scope", normalize_skill_name("demo")])
        other_scope = stable_id("skill", ["other", normalize_skill_name("demo")])
        assert first == second
        assert first != other_scope


class TestImportDecision:
    def test_no_existing_is_new_version_one(self) -> None:
        decision = decide_skill_import((), content_digest=sha256_hex(b"a"), confirm_update=False)
        assert decision.status is SkillImportStatus.NEW
        assert decision.next_version == 1
        assert decision.existing_version is None

    def test_same_digest_is_unchanged(self) -> None:
        digest = sha256_hex(b"a")
        decision = decide_skill_import(((1, digest), (2, digest)), content_digest=digest, confirm_update=False)
        assert decision.status is SkillImportStatus.UNCHANGED
        assert decision.next_version == 2
        assert decision.existing_version == 2

    def test_different_digest_requires_confirmation(self) -> None:
        decision = decide_skill_import(((1, sha256_hex(b"a")),), content_digest=sha256_hex(b"b"), confirm_update=False)
        assert decision.status is SkillImportStatus.UPDATE_REQUIRES_CONFIRMATION
        assert decision.next_version == 2
        assert decision.existing_version == 1

    def test_confirmed_update_appends_next_version(self) -> None:
        decision = decide_skill_import(
            ((1, sha256_hex(b"a")), (4, sha256_hex(b"b"))), content_digest=sha256_hex(b"c"), confirm_update=True
        )
        assert decision.status is SkillImportStatus.UPDATE
        assert decision.next_version == 5
        assert decision.existing_version == 4
