"""E-01 附件链路实证：默认 worker 子进程（真实进程边界）。

验证的是本仓库合同在**真实子进程**下的可证事实（不以内存内 ``main()`` 调用替代）：

1. 上传链路：``SubprocessAttachmentWorker`` 默认命令
   （``python -m services.agent_service.attachment_worker``）可被父进程启动，
   请求头 framing + 原始字节抵达 worker，解析结果回到父进程并通过严格复核；
2. 摘要绑定：``source_digest`` 不一致时 worker 拒绝，父进程映射为稳定错误码；
3. 收尾：正常完成后无在途子进程、无残留管道线程。

下载链路：提取正文 → 交付渲染 → 落盘 → 按格式读回（正文与摘要一致）；DB 侧的
交付发布/回执由 ``tests/integration/test_agent_delivery_service.py`` 覆盖（依赖
sidecar PG 二进制，本地缺失时 skip）。

残留（不得据本文件宣称）：附件结果尚未接入研究来源持久化（F-01 接线前）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.agent_types import AttachmentKind, DeliveryField, ReadState, Selection
from services.agent_service.attachments import (
    AttachmentExtractionRequest,
    AttachmentWorkerError,
    SubprocessAttachmentWorker,
)
from services.agent_service.delivery_render import read_delivery_fields, render_delivery

pytestmark = pytest.mark.no_db


def _request(
    data: bytes,
    *,
    kind: AttachmentKind = AttachmentKind.TXT,
    name: str = "note.txt",
    digest: str | None = None,
) -> AttachmentExtractionRequest:
    return AttachmentExtractionRequest(
        user_scope="scope-1",
        attachment_id="att-1",
        file_name=name,
        source_id="src-1",
        source_version="1",
        source_digest=digest or hashlib.sha256(data).hexdigest(),
        kind=kind,
        data=data,
        selection=Selection(kind="all"),
    )


class TestAttachmentUploadChain:
    @pytest.mark.asyncio
    async def test_default_worker_extracts_txt_over_real_process(self) -> None:
        data = "第一行\n第二行\n".encode()
        worker = SubprocessAttachmentWorker(timeout_s=20.0)

        result = await worker.extract(_request(data))

        assert result.body_text == "第一行\n第二行\n"
        assert result.source.source_digest == hashlib.sha256(data).hexdigest()
        assert result.source.source_kind.value == "attachment"
        assert result.read_state is ReadState.READY
        assert result.worker_pid > 0
        assert result.worker_exited is True
        assert result.selected_range.selection.kind == "all"
        assert worker.active_pids() == ()
        assert worker.residual_threads() == ()

    @pytest.mark.asyncio
    async def test_extracted_body_round_trips_through_downloadable_artifact(self, tmp_path: Path) -> None:
        """上传提取的正文 → 渲染交付文件 → 落盘 → 按格式读回，内容与摘要一致。"""

        data = "第一段\n第二段\n".encode()
        worker = SubprocessAttachmentWorker(timeout_s=20.0)
        extracted = await worker.extract(_request(data))
        body = extracted.body_text
        assert body is not None
        fields = (
            DeliveryField("metadata", "artifact_id", "string", "art-1"),
            DeliveryField("body", "", "string", body),
            DeliveryField("payload", "/n", "int", "1"),
        )

        rendered = render_delivery(fields, "md")
        target = tmp_path / "成果.md"
        target.write_bytes(rendered.content)

        assert rendered.digest == hashlib.sha256(target.read_bytes()).hexdigest()
        index = {field.path: field for field in read_delivery_fields(target.read_bytes(), "md")}
        assert index["body:"].value_text == body
        assert index["metadata:artifact_id"].value_text == "art-1"

    @pytest.mark.asyncio
    async def test_digest_mismatch_is_rejected_by_real_worker(self) -> None:
        data = b"payload"
        worker = SubprocessAttachmentWorker(timeout_s=20.0)

        with pytest.raises(AttachmentWorkerError) as exc:
            await worker.extract(_request(data, digest="0" * 64))

        assert exc.value.code == "attachment_digest_mismatch"
        assert worker.active_pids() == ()
