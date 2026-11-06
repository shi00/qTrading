"""Agent 应用事务 DAO（设计正本 §8.2/§8.3）。

本模块只负责应用 public 表的事务与查询边界：原生 Harness 运行、原生
store/文件、模型/MCP 出站都在提交之后，不进入 ``_guarded_begin``。
D01 建立共享基础设施（锁序、upsert、公开事件追加）；受理/控制/历史/
效果回执由 D02—D04 在同一 DAO 上补齐，不另建第二入口。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import UUID

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.agent_types import decode_json_doc, json_doc
from data.persistence.models import AgentEvent, AgentStreamHead

from .base_dao import BaseDao

logger = logging.getLogger(__name__)

# §8.3 固定锁序：同层按 UUID 排序。新增写方法必须沿此顺序取锁，
# 禁止在执行中反向回锁（例如先锁资源再锁 intent）。
LOCK_ORDER: tuple[str, ...] = (
    "submission-identity",
    "admission-head",
    "total-slot",
    "conversation",
    "intent",
    "execution",
    "task",
    "attempt",
    "resource",
    "plan-review",
    "decision",
    "delivery",
    "cleanup",
    "presentation",
    "stream-head",
)

# 公开事件只保存读模型/成果指针：artifact.published 的 payload 仅为
# ArtifactPublication，不得携带 content/markdown 正文副本（§6.7/§10）。
_ARTIFACT_PUBLICATION_KEYS = frozenset({"artifact_id", "revision", "content_digest"})
PUBLIC_STREAM_KEY = "public"


def _digest(doc: str) -> str:
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


class AgentDao(BaseDao):
    """Agent 应用表 DAO（继承 BaseDao，绑定当前可信服务域）。"""

    # ------------------------------------------------------------------
    # 共享写入基础设施（D01）
    # ------------------------------------------------------------------

    async def _upsert_rows(
        self,
        table_name: str,
        columns: list[str],
        pk_columns: list[str],
        rows: list[dict[str, Any]],
        *,
        conn: Any = None,
    ) -> int:
        """批量 upsert，复用 BaseDao._save_upsert 的冲突/类型归一语义。

        空 rows 返回 0；不吞写库异常（调用方事务因此回滚）。
        """
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        return await self._save_upsert(
            df,
            table_name,
            columns,
            pk_columns=pk_columns,
            conn=conn,
        )

    async def append_event(
        self,
        *,
        event_key: str,
        domain: str,
        entity_id: UUID,
        entity_revision: int,
        kind: str,
        payload: str,
        conversation_id: UUID | None = None,
        execution_id: UUID | None = None,
        stream_key: str = PUBLIC_STREAM_KEY,
        conn: Any = None,
    ) -> int:
        """在事务最后追加公开事件并递增 stream tail，返回提交次序 seq。

        事件 payload 必须已是规范 JSON；artifact.published 只接受
        ArtifactPublication 三键指针（正文按准确版本经 read_artifact 读取）。
        ``seq`` 由本事务在锁住 stream head 后递增，不使用 SERIAL 预取号冒充
        提交次序；回滚不推进（与 §8.3 一致）。
        """
        if entity_revision < 1:
            raise ValueError("entity_revision must be >= 1")
        payload_obj = decode_json_doc(payload)
        if kind == "artifact.published":
            if not isinstance(payload_obj, dict):
                raise ValueError("artifact.published payload must be an object")
            keys = frozenset(payload_obj)
            if keys != _ARTIFACT_PUBLICATION_KEYS:
                raise ValueError(
                    "artifact.published payload must contain exactly "
                    f"{sorted(_ARTIFACT_PUBLICATION_KEYS)}, got {sorted(keys)}"
                )

        payload_digest = _digest(payload)

        if conn is None:
            async with self._guarded_begin() as tx:
                return await self._append_event_in_tx(
                    tx,
                    event_key=event_key,
                    domain=domain,
                    entity_id=entity_id,
                    entity_revision=entity_revision,
                    kind=kind,
                    payload_obj=payload_obj,
                    payload_digest=payload_digest,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    stream_key=stream_key,
                )
        return await self._append_event_in_tx(
            conn,
            event_key=event_key,
            domain=domain,
            entity_id=entity_id,
            entity_revision=entity_revision,
            kind=kind,
            payload_obj=payload_obj,
            payload_digest=payload_digest,
            conversation_id=conversation_id,
            execution_id=execution_id,
            stream_key=stream_key,
        )

    async def _append_event_in_tx(
        self,
        conn: Any,
        *,
        event_key: str,
        domain: str,
        entity_id: UUID,
        entity_revision: int,
        kind: str,
        payload_obj: Any,
        payload_digest: str,
        conversation_id: UUID | None,
        execution_id: UUID | None,
        stream_key: str,
    ) -> int:
        await conn.execute(
            pg_insert(AgentStreamHead)
            .values(stream_key=stream_key, tail=0)
            .on_conflict_do_nothing(index_elements=["stream_key"])
        )
        head = await conn.execute(
            sa.select(AgentStreamHead.tail).where(AgentStreamHead.stream_key == stream_key).with_for_update()
        )
        row = head.first()
        if row is None:
            raise RuntimeError(f"agent_stream_head row missing for {stream_key!r}")
        seq = int(row[0]) + 1
        await conn.execute(
            sa.update(AgentStreamHead)
            .where(AgentStreamHead.stream_key == stream_key)
            .values(tail=seq, updated_at=sa.func.now())
        )
        await conn.execute(
            sa.insert(AgentEvent).values(
                seq=seq,
                event_key=event_key,
                domain=domain,
                conversation_id=conversation_id,
                execution_id=execution_id,
                entity_id=entity_id,
                entity_revision=entity_revision,
                kind=kind,
                payload=payload_obj,
                payload_digest=payload_digest,
            )
        )
        return seq

    # ------------------------------------------------------------------
    # 公开事件指针校验辅助（H01 保存成果时复用）
    # ------------------------------------------------------------------

    @staticmethod
    def artifact_publication_payload(artifact_id: UUID, revision: int, content_digest: str) -> str:
        """构造 ArtifactPublication 规范 JSON（只含身份/版本/摘要）。"""
        return json_doc({"artifact_id": artifact_id, "revision": revision, "content_digest": content_digest})
