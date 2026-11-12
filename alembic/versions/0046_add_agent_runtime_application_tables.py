"""add agent runtime application tables (C-01, Agent 详细设计 §9.2—§9.11)

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-12 00:00:00.000000

新增 Agent 运行时应用表 14 张（§9.1.6「应用 agent_* 表由现有应用 Alembic/
SQLAlchemy/BaseDao 管理」；§9.13「C-01 同时交付 14 张应用表（含
agent_body_payload）的 ORM、TABLE_DEFINITIONS、AgentDao/CacheManager 注册、
全部新字段/复合 FK/不可变写入及 Alembic 迁移」）：

- agent_session / agent_execution / agent_round / agent_message / agent_artifact；
- agent_command / agent_decision / agent_operation；
- agent_asset_version / agent_source_link / agent_body_payload（§9.10.1 首版
  同库不可变正文单块 BYTEA，应用表由原 13 张变为 14 张）；
- agent_resource_slot / agent_read_mark / agent_cleanup_job。

**实现方式（有意偏离 0040 手写列定义惯例，理由如下）**：env.py 以
``data.persistence.models.Base.metadata`` 为 ``target_metadata`` 且
``compare_type=True, compare_server_default=True``——``alembic check`` 与 CI
``downgrade base → upgrade head`` 会把库结构逐列与 ORM 元数据比对。14 张表含
大量复合 FK（全部携 user_scope）、部分唯一索引与 CHECK 值域，手写列定义的
漂移几乎必然被门禁拦截。故本迁移直接从**同一 ORM 元数据**建表（按
``AGENT_RUNTIME_TABLE_ORDER`` 依赖序 ``create_all`` / 逆序 ``drop_all``），
结构性保证 ORM↔迁移零漂移；表集合由 tests/unit/test_agent_persistence_schema.py
断言（14 张、顺序、成员与 TABLE_DEFINITIONS 注册）。

契约要点（§9.1/§9.2）：
- 全部 session/execution/round 引用设 FK RESTRICT；复合 FK 携 user_scope；
- ID 列 TEXT、digest 列 CHAR(64)、状态 TEXT+CHECK 枚举、水位/计数 BIGINT 非负；
- created_at/updated_at TIMESTAMPTZ NOT NULL（新表按设计正本 §9.2，与既有
  ai_* 表 timezone=False 惯例不同）；
- body/owner 环引用（message/artifact/asset 的 body_ref 反向 FK 与
  agent_body_payload 的 owner FK）为 NO ACTION DEFERRABLE（§9.10.1），在
  create_all 中经 use_alter 于全部表建立后统一补建；
- execution.current_round_id / session.draft_transfer_ref 循环 FK 同样延后
  补建（RESTRICT，不级联删）；
- 密钥、连接 URL、Flet 对象、Engine、隐藏推理不落任何 Agent 表（§9.1.4）。
"""

from collections.abc import Sequence

from alembic import op
from data.persistence.models import AGENT_RUNTIME_TABLE_ORDER, Base

# revision identifiers, used by Alembic.
revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the 14 agent runtime application tables from the ORM metadata.

    循环 FK（use_alter=True）由 SQLAlchemy create_all 在全部表建立后经 ALTER
    统一补建；建表顺序见 AGENT_RUNTIME_TABLE_ORDER（无内联 FK 依赖环）。
    """
    bind = op.get_bind()
    Base.metadata.create_all(
        bind,
        tables=[Base.metadata.tables[name] for name in AGENT_RUNTIME_TABLE_ORDER],
    )


def downgrade() -> None:
    """Drop the 14 agent runtime application tables (data loss, non-recoverable).

    SQLAlchemy ``drop_all`` 会对 use_alter 循环 FK 先行 ``ALTER ... DROP
    CONSTRAINT``（create 的镜像语义），随后按逆拓扑序 drop 表；不需要（也不允许）
    手工重复删 FK——重复 drop 会触发 UndefinedObjectError。
    """
    bind = op.get_bind()
    metadata = Base.metadata
    metadata.drop_all(
        bind,
        tables=[metadata.tables[name] for name in reversed(AGENT_RUNTIME_TABLE_ORDER)],
    )
