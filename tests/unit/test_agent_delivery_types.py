"""E-05 成果交付命令 / 目标保护 / 分段与回执契约（§4.2、§10.5、HANDOFF §10.2）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.agent_types import (
    DELIVERY_CHUNK_MAX_UTF16,
    AgentValidationError,
    ArtifactDeliveryCommand,
    CommandRef,
    DeliveryManifest,
    DeliveryReceipt,
    DeliveryTarget,
    decode_artifact_delivery_command,
    decode_delivery_manifest,
    decode_delivery_receipt,
    decode_delivery_target,
    delivery_input_digest,
    derive_delivery_id,
    encode_artifact_delivery_command,
    encode_delivery_manifest,
    encode_delivery_receipt,
    encode_delivery_target,
    split_utf16_chunks,
    validate_delivery_target_path,
    validate_delivery_transition,
)

_TIMESTAMP = "2026-11-15T08:00:00.000000Z"


def _target(**overrides: object) -> DeliveryTarget:
    base = DeliveryTarget(
        target_path="C:\\Users\\admin\\Desktop\\result.md",
        expected_existing=False,
        expected_existing_digest=None,
        overwrite_confirmed=False,
    )
    return replace(base, **overrides)


def _command(**overrides: object) -> ArtifactDeliveryCommand:
    base = ArtifactDeliveryCommand(
        command=CommandRef(
            user_scope="u1",
            source_module="backtest",
            business_request_id="req-1",
            command_id="cmd-1",
            expected_revision=0,
        ),
        artifact_id="art-1",
        artifact_version=1,
        format="md",
        format_rule_version=1,
        delivery_id="delivery-placeholder",
        target=_target(),
        expected_delivery_revision=0,
    )
    derived = replace(base, delivery_id=derive_delivery_id(base))
    return replace(derived, **overrides)


def _receipt(**overrides: object) -> DeliveryReceipt:
    base = DeliveryReceipt(
        command_record_id="command-1",
        delivery_id="delivery-1",
        artifact_id="art-1",
        artifact_version=1,
        artifact_digest="a" * 64,
        format="md",
        phase="staged",
        publish_effect="not_attempted",
        cancel_effect="not_requested",
        output_digest="b" * 64,
        verified_at=_TIMESTAMP,
        error_code=None,
        next_action=None,
    )
    return replace(base, **overrides)


class TestTargetPathPolicy:
    @pytest.mark.parametrize(
        "value",
        ["C:\\out\\a.md", "d:\\x\\y.csv", "E:\\成果\\报告.xlsx"],
    )
    def test_absolute_drive_paths_accepted(self, value: str) -> None:
        assert validate_delivery_target_path(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "out.md",
            "C:out.md",
            "C:\\",
            "\\\\server\\share\\a.md",
            "\\\\?\\C:\\a.md",
            "\\\\.\\C:\\a.md",
            "https://example.com/a.md",
            "C:\\a\\b:c.md",
            "C:\\a\\CON.txt",
            "C:\\a\\b. ",
            "C:\\a\\..\\b.md",
            "C:/a/b.md",
            "C:\\a\\b?.md",
        ],
    )
    def test_unsafe_or_ambiguous_paths_rejected(self, value: str) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_delivery_target_path(value)
        assert exc.value.code == "invalid_target_path"


class TestDeliveryTarget:
    def test_non_existing_target_roundtrip(self) -> None:
        target = _target()
        wire = encode_delivery_target(target)
        assert wire == {
            "target_path": target.target_path,
            "expected_existing": False,
            "expected_existing_digest": None,
            "overwrite_confirmed": False,
        }
        assert decode_delivery_target(wire) == target

    def test_existing_target_requires_digest_and_confirmation(self) -> None:
        existing = _target(expected_existing=True, expected_existing_digest="c" * 64, overwrite_confirmed=True)
        assert decode_delivery_target(encode_delivery_target(existing)) == existing
        with pytest.raises(AgentValidationError) as missing_digest:
            encode_delivery_target(replace(existing, expected_existing_digest=None))
        assert missing_digest.value.code == "invalid_digest"
        with pytest.raises(AgentValidationError) as unconfirmed:
            encode_delivery_target(replace(existing, overwrite_confirmed=False))
        assert unconfirmed.value.code == "unconfirmed_overwrite"

    def test_non_existing_target_rejects_digest_or_confirmation(self) -> None:
        with pytest.raises(AgentValidationError) as digest:
            encode_delivery_target(_target(expected_existing_digest="c" * 64))
        assert digest.value.code == "invalid_value"
        with pytest.raises(AgentValidationError) as confirmed:
            encode_delivery_target(_target(overwrite_confirmed=True))
        assert confirmed.value.code == "invalid_value"

    def test_decode_rejects_extra_key(self) -> None:
        wire = encode_delivery_target(_target())
        wire["extra"] = 1
        with pytest.raises(AgentValidationError) as exc:
            decode_delivery_target(wire)
        assert exc.value.code == "unknown_field"


class TestArtifactDeliveryCommand:
    def test_roundtrip_is_stable(self) -> None:
        command = _command()
        wire = encode_artifact_delivery_command(command)
        assert decode_artifact_delivery_command(wire) == command
        assert encode_artifact_delivery_command(decode_artifact_delivery_command(wire)) == wire

    def test_delivery_id_is_derived_from_command_and_artifact(self) -> None:
        command = _command()
        assert command.delivery_id == derive_delivery_id(command)
        wire = encode_artifact_delivery_command(command)
        wire["delivery_id"] = "delivery_other"
        with pytest.raises(AgentValidationError) as exc:
            decode_artifact_delivery_command(wire)
        assert exc.value.code == "invalid_value"

    def test_expected_revision_must_match_command(self) -> None:
        command = _command()
        with pytest.raises(AgentValidationError) as exc:
            encode_artifact_delivery_command(replace(command, expected_delivery_revision=1))
        assert exc.value.code == "invalid_revision"

    def test_invalid_format_and_rule_version_rejected(self) -> None:
        command = _command()
        with pytest.raises(AgentValidationError) as fmt:
            encode_artifact_delivery_command(replace(command, format="pdf"))
        assert fmt.value.code == "invalid_delivery_format"
        with pytest.raises(AgentValidationError) as rule:
            encode_artifact_delivery_command(replace(command, format_rule_version=2))
        assert rule.value.code == "unsupported_format_rule_version"

    def test_artifact_version_must_be_positive(self) -> None:
        command = _command()
        with pytest.raises(AgentValidationError) as exc:
            encode_artifact_delivery_command(replace(command, artifact_version=0))
        assert exc.value.code == "invalid_revision"

    def test_input_digest_changes_with_target(self) -> None:
        command = _command()
        first = delivery_input_digest(command)
        second = delivery_input_digest(replace(command, target=_target(target_path="D:\\out\\result.md")))
        assert first == delivery_input_digest(command)
        assert first != second


class TestUtf16Chunking:
    def test_empty_text_keeps_one_empty_chunk(self) -> None:
        assert split_utf16_chunks("") == ("",)

    def test_exact_limit_passes_and_plus_one_splits(self) -> None:
        exact = "a" * DELIVERY_CHUNK_MAX_UTF16
        assert split_utf16_chunks(exact) == (exact,)
        over = "a" * (DELIVERY_CHUNK_MAX_UTF16 + 1)
        chunks = split_utf16_chunks(over)
        assert [len(chunk) for chunk in chunks] == [DELIVERY_CHUNK_MAX_UTF16, 1]
        assert "".join(chunks) == over

    def test_non_bmp_character_counts_two_units_without_split(self) -> None:
        text = "a" * 29_999 + "😀"
        first, second = split_utf16_chunks(text)
        assert len(first) == 29_999
        assert second == "😀"
        text = "a" * DELIVERY_CHUNK_MAX_UTF16 + "😀"
        first, second = split_utf16_chunks(text)
        assert len(first) == DELIVERY_CHUNK_MAX_UTF16
        assert second == "😀"

    def test_invalid_limit_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            split_utf16_chunks("abc", max_units=1)
        assert exc.value.code == "invalid_chunk_limit"


class TestDeliveryReceipt:
    def test_roundtrip_for_each_phase(self) -> None:
        samples = (
            replace(_receipt(), phase="manifest_saved", output_digest=None),
            _receipt(),
            replace(_receipt(), phase="published_confirmed", publish_effect="published"),
            replace(
                _receipt(),
                phase="cancelled_before_publish",
                cancel_effect="cancelled_before_publish",
                output_digest=None,
            ),
            replace(_receipt(), phase="unknown", publish_effect="unknown", error_code="io_unverifiable"),
        )
        for receipt in samples:
            assert decode_delivery_receipt(encode_delivery_receipt(receipt)) == receipt

    def test_published_requires_output_digest(self) -> None:
        receipt = _receipt(phase="published_confirmed", publish_effect="published", output_digest=None)
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_receipt(receipt)
        assert exc.value.code == "missing_field"

    def test_publish_effect_must_match_phase(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_receipt(_receipt(publish_effect="published"))
        assert exc.value.code == "invalid_delivery_phase"

    def test_unknown_requires_error_code(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_receipt(_receipt(phase="unknown", publish_effect="unknown"))
        assert exc.value.code == "missing_field"

    def test_invalid_phase_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_receipt(_receipt(phase="done"))
        assert exc.value.code == "invalid_delivery_phase"


_STAGED_PATH = "C:\\Users\\admin\\Desktop\\.result.md.abc.staged"


def _manifest(**overrides: object) -> DeliveryManifest:
    command = _command()
    base = DeliveryManifest(
        delivery_id=command.delivery_id,
        command=command.command,
        artifact_id=command.artifact_id,
        artifact_version=command.artifact_version,
        artifact_digest="d" * 64,
        format=command.format,
        format_rule_version=command.format_rule_version,
        target=command.target,
        phase="manifest_saved",
        source_state="available",
        output_digest=None,
        output_bytes=None,
        staged_path=None,
        verified_at=_TIMESTAMP,
        error_code=None,
    )
    return replace(base, **overrides)


class TestDeliveryManifest:
    def test_roundtrip_across_phases(self) -> None:
        samples = (
            _manifest(),
            _manifest(phase="staged", output_digest="e" * 64, output_bytes=12, staged_path=_STAGED_PATH),
            _manifest(phase="validated", output_digest="e" * 64, output_bytes=12, staged_path=_STAGED_PATH),
            _manifest(phase="publish_claimed", output_digest="e" * 64, output_bytes=12, staged_path=_STAGED_PATH),
            _manifest(phase="published_confirmed", output_digest="e" * 64, output_bytes=12),
            _manifest(phase="cancelled_before_publish"),
            _manifest(phase="unknown", error_code="delivery_publish_unverified"),
        )
        for manifest in samples:
            assert decode_delivery_manifest(encode_delivery_manifest(manifest)) == manifest

    def test_staged_phase_requires_output_and_staged_path(self) -> None:
        with pytest.raises(AgentValidationError) as missing_output:
            encode_delivery_manifest(_manifest(phase="staged", staged_path=_STAGED_PATH))
        assert missing_output.value.code == "missing_field"
        with pytest.raises(AgentValidationError) as missing_path:
            encode_delivery_manifest(_manifest(phase="staged", output_digest="e" * 64, output_bytes=12))
        assert missing_path.value.code == "missing_field"

    def test_manifest_saved_rejects_output_fields(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_manifest(_manifest(output_digest="e" * 64, output_bytes=12))
        assert exc.value.code == "invalid_delivery_phase"

    def test_published_rejects_staged_path(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_manifest(
                _manifest(
                    phase="published_confirmed",
                    output_digest="e" * 64,
                    output_bytes=12,
                    staged_path=_STAGED_PATH,
                )
            )
        assert exc.value.code == "invalid_delivery_phase"

    def test_unknown_requires_error_code(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_manifest(_manifest(phase="unknown"))
        assert exc.value.code == "missing_field"

    def test_output_digest_and_bytes_must_pair(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_delivery_manifest(_manifest(phase="staged", output_digest="e" * 64, staged_path=_STAGED_PATH))
        assert exc.value.code == "missing_field"

    def test_decode_rejects_unsupported_version(self) -> None:
        wire = encode_delivery_manifest(_manifest())
        wire["schema_version"] = 2
        with pytest.raises(AgentValidationError) as exc:
            decode_delivery_manifest(wire)
        assert exc.value.code == "unsupported_schema_version"

    @pytest.mark.parametrize(
        ("previous", "following"),
        [
            ("manifest_saved", "staged"),
            ("staged", "validated"),
            ("validated", "publish_claimed"),
            ("publish_claimed", "published_confirmed"),
            ("staged", "cancelled_before_publish"),
            ("publish_claimed", "unknown"),
            ("unknown", "unknown"),
        ],
    )
    def test_allowed_transitions(self, previous: str, following: str) -> None:
        validate_delivery_transition(previous, following)

    @pytest.mark.parametrize(
        ("previous", "following"),
        [
            ("manifest_saved", "published_confirmed"),
            ("staged", "publish_claimed"),
            ("published_confirmed", "unknown"),
            ("unknown", "staged"),
            ("cancelled_before_publish", "staged"),
        ],
    )
    def test_illegal_transitions_rejected(self, previous: str, following: str) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_delivery_transition(previous, following)
        assert exc.value.code == "invalid_delivery_transition"
