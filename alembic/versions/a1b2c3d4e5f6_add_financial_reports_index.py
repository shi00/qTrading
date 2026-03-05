"""add composite index on financial_reports(ts_code, ann_date)

Revision ID: a1b2c3d4e5f6
Revises: 367c382dbf28
Create Date: 2026-03-05

Performance fix: get_screening_data JOIN subquery
  SELECT ts_code, MAX(ann_date) FROM financial_reports WHERE ann_date <= ? GROUP BY ts_code
requires (ts_code, ann_date) composite index to avoid full table scan on ~5M rows.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '367c382dbf28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('financial_reports', schema=None) as batch_op:
        batch_op.create_index(
            'ix_financial_reports_ts_code_ann_date',
            ['ts_code', 'ann_date'],
            unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('financial_reports', schema=None) as batch_op:
        batch_op.drop_index('ix_financial_reports_ts_code_ann_date')
