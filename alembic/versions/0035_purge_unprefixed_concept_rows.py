"""purge legacy unprefixed concept_id rows in stock_concepts

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-02 00:00:00.000000

review09-24 MAJOR-06：``StockDao.overwrite_concepts``（Tushare 概念同步）原先把接口
裸 ``id`` 直接写入 ``stock_concepts.concept_id``（无前缀），与东财 ``EM_``、AI 打标
``AI_LLM_``、涨停 ``LIMIT_`` 三类前缀行混存。修复后 Tushare 行改用 ``TS_`` 前缀，
``overwrite_concepts`` 的删除谓词也收窄为 ``TS_`` 自有行；历史遗留的**无前缀裸 id 行**
因此成为孤儿——既不在任何前缀的清理范围内，也不应再被任何数据源写入，须一次性清除。

本迁移为**数据迁移**（不改 schema）：删除所有 ``concept_id`` 不在已知四类前缀
（``EM_`` / ``AI_LLM_`` / ``LIMIT_`` / ``TS_``）之下的行。``LIKE`` 中的下划线以
``\\_`` 转义并配合 ``ESCAPE '\\'`` 作字面量匹配，避免 ``_`` 被当作单字符通配符放大
匹配范围。``concept_id`` 为 NULL 的行（数据异常，正常不可能出现）经 ``NOT LIKE``
结果为 NULL 而被保留，不在本迁移处理范围内。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete stock_concepts rows whose concept_id carries none of the known prefixes."""
    op.execute(
        "DELETE FROM stock_concepts "
        "WHERE concept_id NOT LIKE 'EM\\_%' ESCAPE '\\' "
        "AND concept_id NOT LIKE 'AI\\_LLM\\_%' ESCAPE '\\' "
        "AND concept_id NOT LIKE 'LIMIT\\_%' ESCAPE '\\' "
        "AND concept_id NOT LIKE 'TS\\_%' ESCAPE '\\'"
    )


def downgrade() -> None:
    """No-op: 被删除的无前缀裸 id 行无法重建（且不应再出现），对齐 0030/0032 先例。

    CI 的 downgrade base → upgrade head 为重建式，不受影响。
    """
    pass
