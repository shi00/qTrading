"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    # 幂等编写提示（DAT-19）：结构性变更（add/drop column/index/table）默认用内省判断再执行，
    # 确保重复运行不报错；或优先用 op.add_column/create_index 的 if_not_exists / drop_* 的 if_exists。
    # 内省判断可复用本模板同目录 alembic/_shared_helpers.py 的 column_exists()：
    #   from _shared_helpers import column_exists
    #   if not column_exists(op.get_bind(), "<table>", "col"): op.add_column(...)
    # 注意：alembic/ 目录由 alembic/env.py 注册到 sys.path，故用顶层导入而非
    #   "from alembic._shared_helpers import ..."（后者会命中已装的同名 alembic 包）。
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    # 幂等编写提示（DAT-19）：downgrade 中的 drop 操作同样先用内省判断再执行，保证可重复安全回滚。
    ${downgrades if downgrades else "pass"}
