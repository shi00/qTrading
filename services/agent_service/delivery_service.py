"""成果交付编排（E-05-4b / DD SQ-09 / 详细设计 §10.5 / HANDOFF §10.2）。

可靠顺序冻结（每步事实未保存即不进入下一阶段）：
读全成果 → 保存 ``manifest_saved`` → 渲染 → 专属同卷暂存（核回读摘要）→
保存 ``staged`` → 逐字段回读 → 保存 ``validated`` → 新鲜核原来源状态 →
保存 ``publish_claimed`` → 原子替换并核回目标身份/摘要 → 保存 ``published_confirmed``；
发布效果不可确证 → ``unknown``（禁止自动重试）。

禁用：重跑研究/工具、把暂存或未确认替换当成功、取消先胜后仍发布、
``unknown``/终态后再写入、发布前失败删除或覆盖旧目标、在事件循环线程直接做
渲染与文件工作（R16，须经 ``run_blocking``）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from core.agent_types import (
    DELIVERY_SOURCE_STATES,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ArtifactDeliveryCommand,
    ArtifactRead,
    ArtifactReadQuery,
    CommandRef,
    DeliveryManifest,
    DeliveryPhaseReceipt,
    DeliveryReceipt,
    DeliveryTarget,
    build_delivery_fields,
    validate_artifact_delivery_command,
)
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    PublishResult,
    StagedFile,
)
from services.agent_service.delivery_render import (
    DeliveryRenderError,
    RenderedDelivery,
    render_delivery,
    verify_delivery_readback,
)

#: 发布认领后的不可确证错误码（回执与 manifest 共用，UI 据此显示「可能已保存」）。
_UNVERIFIED_CODE = "delivery_publish_unverified"
#: 发布成功但成功回执保存失败时的错误码（文件与数据库不能一起提交）。
_RECEIPT_SAVE_FAILED = "delivery_receipt_save_failed"
#: 发布前新鲜核原来源状态失败（成果已不可读）时的错误码前缀。
_SOURCE_RECHECK_FAILED = "delivery_source_recheck_failed"

#: 发布认领后禁止重试的下一步说明（可核原交付后再决定）。
_UNKNOWN_NEXT_ACTION = "查原交付回执与目标文件身份；确认前不得再次导出"


def _manifest_source_state(value: object) -> str:
    """原来源当前状态 → manifest 词表；库内其他取值按 ``unknown`` 记录，不臆测。

    ``agent_artifact.source_available_state`` 是自由文本列（当前核定），manifest
    只接受 ``DELIVERY_SOURCE_STATES``；非标准取值不得改写原核验事实，也不得
    伪装成 ``available``。
    """

    if isinstance(value, str) and value in DELIVERY_SOURCE_STATES:
        return value
    return "unknown"


class DeliveryArtifactPort(Protocol):
    """成果读取端口（生产接 ``AgentDao.read_artifact``）。"""

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead: ...


class DeliveryPhasePort(Protocol):
    """交付阶段持久化端口（生产接 ``AgentDao.save_delivery_phase``）。"""

    async def save_delivery_phase(
        self,
        *,
        user_scope: str,
        operation_id: str,
        expected_revision: int,
        manifest: DeliveryManifest,
    ) -> DeliveryPhaseReceipt: ...


class DeliveryFilePort(Protocol):
    """本机文件交付端口（生产接 ``WindowsManagedFileStore``）。"""

    def stage(
        self,
        *,
        target: DeliveryTarget,
        content: bytes,
        expected_digest: str,
        name_hint: str | None = None,
    ) -> StagedFile: ...

    def publish(self, *, target: DeliveryTarget, staged: StagedFile) -> PublishResult: ...

    def cancel(self, staged: StagedFile) -> bool: ...


class DeliveryBlockingPort(Protocol):
    """同步工作提交端口（生产接 ``ThreadPoolManager.run_async``）。"""

    async def __call__[T](self, func: Callable[..., T], *args: object, **kwargs: object) -> T: ...


async def _default_run_blocking[T](func: Callable[..., T], *args: object, **kwargs: object) -> T:
    """默认经受管 IO 线程池提交渲染与文件工作（R16）。"""

    from utils.thread_pool import TaskType, ThreadPoolManager

    return await ThreadPoolManager().run_async(TaskType.IO, func, *args, **kwargs)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class DeliveryContext:
    """交付命令在持久记录中的定位事实（operation 行 + 成果所属执行/轮次）。"""

    operation_id: str
    command_record_id: str
    execution_id: str
    round_id: str


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """交付结果：回执（wire 可编码）+ 本交付暂存是否已清理（残留需如实说明）。"""

    receipt: DeliveryReceipt
    staged_cleaned: bool


@dataclass(frozen=True, slots=True)
class _ManifestBase:
    """manifest 的不变部分（成果身份、格式、目标）；阶段字段由调用点补齐。"""

    delivery_id: str
    command: CommandRef
    artifact_id: str
    artifact_version: int
    artifact_digest: str
    format: str
    format_rule_version: int
    target: DeliveryTarget

    def build(
        self,
        *,
        phase: str,
        source_state: str,
        verified_at: str,
        output_digest: str | None = None,
        output_bytes: int | None = None,
        staged_path: str | None = None,
        error_code: str | None = None,
    ) -> DeliveryManifest:
        return DeliveryManifest(
            delivery_id=self.delivery_id,
            command=self.command,
            artifact_id=self.artifact_id,
            artifact_version=self.artifact_version,
            artifact_digest=self.artifact_digest,
            format=self.format,
            format_rule_version=self.format_rule_version,
            target=self.target,
            phase=phase,
            source_state=source_state,
            output_digest=output_digest,
            output_bytes=output_bytes,
            staged_path=staged_path,
            verified_at=verified_at,
            error_code=error_code,
        )


class DeliveryCommandService:
    """显式成果导出编排；只交付已保存成果版本，不重新研究。"""

    def __init__(
        self,
        *,
        artifacts: DeliveryArtifactPort,
        dao: DeliveryPhasePort,
        store: DeliveryFilePort,
        run_blocking: DeliveryBlockingPort | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._dao = dao
        self._store = store
        self._run_blocking = run_blocking or _default_run_blocking
        self._clock = clock or _utc_now

    async def deliver(
        self,
        command: ArtifactDeliveryCommand,
        context: DeliveryContext,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> DeliveryOutcome:
        """按冻结顺序导出并返回回执；发布认领后不可确证即 ``unknown``，不重试。"""

        identity = f"{command.command.user_scope}/{context.operation_id}"
        self._validate(command, context, identity)
        user_scope = command.command.user_scope

        read, body_text = await self._read_complete(command, context, user_scope=user_scope, identity=identity)
        base = _ManifestBase(
            delivery_id=command.delivery_id,
            command=command.command,
            artifact_id=read.artifact_id,
            artifact_version=read.artifact_version,
            artifact_digest=read.result_digest,
            format=command.format,
            format_rule_version=command.format_rule_version,
            target=command.target,
        )
        state = _manifest_source_state(read.source_available_state)
        revision = command.expected_delivery_revision

        saved = await self._save(
            base.build(phase="manifest_saved", source_state=state, verified_at=self._clock()),
            user_scope=user_scope,
            context=context,
            expected_revision=revision,
        )
        revision = saved.revision

        try:
            fields = await self._run_blocking(build_delivery_fields, read, body_text=body_text)
            rendered = await self._run_blocking(render_delivery, fields, command.format)
        except (AgentValidationError, DeliveryRenderError) as exc:
            return self._failed(base, context, rendered=None, phase="manifest_saved", error_code=exc.code)
        digest = rendered.digest
        size = len(rendered.content)

        try:
            staged = await self._run_blocking(
                self._store.stage,
                target=command.target,
                content=rendered.content,
                expected_digest=digest,
                name_hint=command.delivery_id,
            )
        except DeliveryFileError as exc:
            # 暂存未落库：回执与已存 manifest 一致（manifest_saved 无输出摘要）。
            return self._failed(base, context, rendered=None, phase="manifest_saved", error_code=exc.code)
        if staged.digest != digest:
            cleaned = await self._discard(staged)
            return self._failed(
                base,
                context,
                rendered=None,
                phase="manifest_saved",
                error_code="delivery_stage_readback_failed",
                staged_cleaned=cleaned,
            )

        try:
            staged_saved = await self._save(
                base.build(
                    phase="staged",
                    source_state=state,
                    verified_at=self._clock(),
                    output_digest=digest,
                    output_bytes=size,
                    staged_path=str(staged.path),
                ),
                user_scope=user_scope,
                context=context,
                expected_revision=revision,
            )
        except AgentServiceError:
            # 暂存事实未落库：本交付暂存无引用，清理后原样上抛，不进入校验。
            await self._discard(staged)
            raise
        revision = staged_saved.revision

        try:
            await self._run_blocking(verify_delivery_readback, fields, rendered.content, command.format)
        except DeliveryRenderError as exc:
            return self._failed(base, context, rendered=rendered, phase="staged", error_code=exc.code)

        validated = await self._save(
            base.build(
                phase="validated",
                source_state=state,
                verified_at=self._clock(),
                output_digest=digest,
                output_bytes=size,
                staged_path=str(staged.path),
            ),
            user_scope=user_scope,
            context=context,
            expected_revision=revision,
        )
        revision = validated.revision

        try:
            fresh = await self._artifacts.read_artifact(
                ArtifactReadQuery(
                    artifact_id=read.artifact_id,
                    artifact_version=read.artifact_version,
                    execution_id=context.execution_id,
                    round_id=context.round_id,
                    limit=1,
                ),
                user_scope=user_scope,
            )
        except AgentServiceError as exc:
            return self._failed(
                base,
                context,
                rendered=rendered,
                phase="validated",
                error_code=f"{_SOURCE_RECHECK_FAILED}:{exc.code}",
            )
        state = _manifest_source_state(fresh.source_available_state)

        if cancel_requested is not None and cancel_requested():
            cancelled = await self._save(
                base.build(
                    phase="cancelled_before_publish",
                    source_state=state,
                    verified_at=self._clock(),
                    output_digest=digest,
                    output_bytes=size,
                ),
                user_scope=user_scope,
                context=context,
                expected_revision=revision,
            )
            cleaned = await self._discard(staged)
            return DeliveryOutcome(
                receipt=self._receipt(
                    base,
                    context,
                    phase=cancelled.phase,
                    publish_effect="not_attempted",
                    cancel_effect="cancelled_before_publish",
                    output_digest=digest,
                    error_code=None,
                ),
                staged_cleaned=cleaned,
            )

        try:
            claimed = await self._save(
                base.build(
                    phase="publish_claimed",
                    source_state=state,
                    verified_at=self._clock(),
                    output_digest=digest,
                    output_bytes=size,
                    staged_path=str(staged.path),
                ),
                user_scope=user_scope,
                context=context,
                expected_revision=revision,
            )
        except AgentServiceError:
            # 取消并发先胜（revision CAS 失败）时同样不得发布。
            await self._discard(staged)
            raise
        revision = claimed.revision

        try:
            published = await self._run_blocking(self._store.publish, target=command.target, staged=staged)
        except DeliveryUnknownError as exc:
            return await self._unknown(
                base, context, rendered=rendered, state=state, expected_revision=revision, error_code=exc.code
            )
        except DeliveryFileError as exc:
            return self._failed(
                base,
                context,
                rendered=rendered,
                phase="publish_claimed",
                error_code=exc.code,
            )
        if published.snapshot.digest != digest:
            return await self._unknown(
                base, context, rendered=rendered, state=state, expected_revision=revision, error_code=_UNVERIFIED_CODE
            )

        try:
            confirmed = await self._save(
                base.build(
                    phase="published_confirmed",
                    source_state=state,
                    verified_at=self._clock(),
                    output_digest=digest,
                    output_bytes=size,
                ),
                user_scope=user_scope,
                context=context,
                expected_revision=revision,
            )
        except AgentServiceError:
            return await self._unknown(
                base,
                context,
                rendered=rendered,
                state=state,
                expected_revision=revision,
                error_code=_RECEIPT_SAVE_FAILED,
            )

        return DeliveryOutcome(
            receipt=self._receipt(
                base,
                context,
                phase=confirmed.phase,
                publish_effect="published",
                cancel_effect="not_requested",
                output_digest=digest,
                error_code=None,
            ),
            staged_cleaned=False,
        )

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    def _validate(self, command: object, context: object, identity: str) -> None:
        if not isinstance(context, DeliveryContext):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "delivery context must be DeliveryContext", identity)
        for field in ("operation_id", "command_record_id", "execution_id", "round_id"):
            value = getattr(context, field)
            if not isinstance(value, str) or not value:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{field} must be non-empty text", identity)
        if not isinstance(command, ArtifactDeliveryCommand):
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "delivery command must be ArtifactDeliveryCommand", identity
            )
        try:
            validate_artifact_delivery_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

    async def _read_complete(
        self,
        command: ArtifactDeliveryCommand,
        context: DeliveryContext,
        *,
        user_scope: str,
        identity: str,
    ) -> tuple[ArtifactRead, str | None]:
        """跨游标取完整正文；分页未取全不导出（不静默截断）。"""

        cursor: str | None = None
        parts: list[str] = []
        first: ArtifactRead | None = None
        while True:
            read = await self._artifacts.read_artifact(
                ArtifactReadQuery(
                    artifact_id=command.artifact_id,
                    artifact_version=command.artifact_version,
                    execution_id=context.execution_id,
                    round_id=context.round_id,
                    body_cursor=cursor,
                ),
                user_scope=user_scope,
            )
            if first is None:
                first = read
                if read.artifact_id != command.artifact_id or read.artifact_version != command.artifact_version:
                    raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "artifact identity mismatch", identity)
                if read.execution_id != context.execution_id or read.round_id != context.round_id:
                    raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "artifact execution/round mismatch", identity)
            elif (read.artifact_id, read.artifact_version) != (first.artifact_id, first.artifact_version):
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN, "artifact identity changed between pages", identity
                )
            if read.body_text is not None:
                parts.append(read.body_text)
            if read.next_cursor is None:
                break
            if read.next_cursor == cursor:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN, "artifact body cursor did not advance", identity
                )
            cursor = read.next_cursor
        body_text = "".join(parts) if first.body_available and parts else None
        return first, body_text

    async def _save(
        self,
        manifest: DeliveryManifest,
        *,
        user_scope: str,
        context: DeliveryContext,
        expected_revision: int,
    ) -> DeliveryPhaseReceipt:
        return await self._dao.save_delivery_phase(
            user_scope=user_scope,
            operation_id=context.operation_id,
            expected_revision=expected_revision,
            manifest=manifest,
        )

    async def _discard(self, staged: StagedFile) -> bool:
        """只清本交付可确认的暂存文件；清理失败不掩盖原结果。"""

        try:
            return await self._run_blocking(self._store.cancel, staged)
        except OSError:
            return False

    async def _unknown(
        self,
        base: _ManifestBase,
        context: DeliveryContext,
        *,
        rendered: RenderedDelivery,
        state: str,
        expected_revision: int,
        error_code: str,
    ) -> DeliveryOutcome:
        """发布效果不可确证：落 ``unknown`` 并禁止重试，不用失败/取消伪装。"""

        await self._save(
            base.build(
                phase="unknown",
                source_state=state,
                verified_at=self._clock(),
                output_digest=rendered.digest,
                output_bytes=len(rendered.content),
                error_code=error_code,
            ),
            user_scope=base.command.user_scope,
            context=context,
            expected_revision=expected_revision,
        )
        return DeliveryOutcome(
            receipt=self._receipt(
                base,
                context,
                phase="unknown",
                publish_effect="unknown",
                cancel_effect="not_requested",
                output_digest=rendered.digest,
                error_code=error_code,
                next_action=_UNKNOWN_NEXT_ACTION,
            ),
            staged_cleaned=False,
        )

    def _failed(
        self,
        base: _ManifestBase,
        context: DeliveryContext,
        *,
        rendered: RenderedDelivery | None,
        phase: str,
        error_code: str,
        staged_cleaned: bool = False,
    ) -> DeliveryOutcome:
        """发布前可确证失败：保留上次可靠阶段，不发布、不改旧目标。"""

        return DeliveryOutcome(
            receipt=self._receipt(
                base,
                context,
                phase=phase,
                publish_effect="not_attempted",
                cancel_effect="not_requested",
                output_digest=None if rendered is None else rendered.digest,
                error_code=error_code,
            ),
            staged_cleaned=staged_cleaned,
        )

    def _receipt(
        self,
        base: _ManifestBase,
        context: DeliveryContext,
        *,
        phase: str,
        publish_effect: str,
        cancel_effect: str,
        output_digest: str | None,
        error_code: str | None,
        next_action: str | None = None,
    ) -> DeliveryReceipt:
        return DeliveryReceipt(
            command_record_id=context.command_record_id,
            delivery_id=base.delivery_id,
            artifact_id=base.artifact_id,
            artifact_version=base.artifact_version,
            artifact_digest=base.artifact_digest,
            format=base.format,
            phase=phase,
            publish_effect=publish_effect,
            cancel_effect=cancel_effect,
            output_digest=output_digest,
            verified_at=self._clock(),
            error_code=error_code,
            next_action=next_action,
        )
