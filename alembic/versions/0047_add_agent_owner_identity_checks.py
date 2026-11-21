"""add agent body owner identity and derived asset pair checks (R2-I32)

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-18 00:00:00.000000

补建两条结构性 CHECK（0046 为冻结历史快照，模型变更走后续迁移）：

1. ``agent_body_payload``：``owner_kind`` / ``owner_id`` / ``owner_version`` 三元组
   必须与恰被选中的 owner 外键列成对一致——
   message 版本固定 ``'1'``；artifact 版本按 ``BIGINT`` 的规范十进制字符串；
   asset 版本逐字相等。原 ``owner_exclusive`` 只约束「哪一列非空」，允许
   ``owner_id`` 与 ``message_id`` / ``artifact_id`` / ``asset_id`` 指向不同主体，
   造成正文归属可被文字三元组伪装。
2. ``agent_source_link``：``derived_asset_id`` / ``derived_asset_version`` 必须成对
   声明。原 ``consumer_null_combination`` 只在 ``consumer_kind = 'asset'`` 分支要求
   两者非空，其余分支仅要求 id 为 NULL，导致「只给 version 的孤立派生边」可通过。

存量数据策略（禁止直接上线失败）：
- 两条约束均以 ``NOT VALID`` 补建：升级不扫描历史行，历史不一致不会阻断部署；
  新写入/更新立即受检（PostgreSQL 对未验证 CHECK 仍强制执行）；
- 迁移**不回填/不改写**历史行：owner 三元组与 FK 列谁是权威不可在迁移内可靠
  判定，静默改写会把「疑似损坏」伪装成「已修复」（R21）；
- 读取侧 ``AgentDao._read_body_text`` 做同语义校验，历史不一致行按
  ``PERSISTENCE_UNAVAILABLE`` 失败关闭，不返回可疑正文；确认历史数据已清理后
  可人工执行 ``ALTER TABLE ... VALIDATE CONSTRAINT`` 转为已验证状态。

``downgrade`` 仅删除这两条约束（不改数据，可逆）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0047"
down_revision: str | Sequence[str] | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add owner identity / derived asset pair checks without scanning legacy rows."""

    op.execute(
        "ALTER TABLE agent_body_payload ADD CONSTRAINT ck_agent_body_payload_owner_identity "
        "CHECK ((owner_kind = 'message' AND message_id = owner_id AND owner_version = '1') "
        "OR (owner_kind = 'artifact' AND artifact_id = owner_id "
        "AND artifact_version IS NOT NULL AND owner_version = artifact_version::text) "
        "OR (owner_kind = 'asset' AND asset_id = owner_id AND asset_version = owner_version)) NOT VALID"
    )
    op.execute(
        "ALTER TABLE agent_source_link ADD CONSTRAINT ck_agent_source_link_derived_asset_pair "
        "CHECK ((derived_asset_id IS NULL) = (derived_asset_version IS NULL)) NOT VALID"
    )


def downgrade() -> None:
    """Drop the two checks (no data rewritten on upgrade, so downgrade is lossless)."""

    op.execute("ALTER TABLE agent_source_link DROP CONSTRAINT ck_agent_source_link_derived_asset_pair")
    op.execute("ALTER TABLE agent_body_payload DROP CONSTRAINT ck_agent_body_payload_owner_identity")
