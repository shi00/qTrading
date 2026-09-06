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
    # 内省判断可参考本模板同目录 alembic/_shared_helpers.py 的 column_exists() 模式：
    #   例：if "col" not in _columns(op.get_bind(), "<table>"): op.add_column(...)
    #   实际导入路径依运行环境解析（alembic 命名空间包与已装 alembic 包同名，慎用顶层
    #   "from alembic._shared_helpers import ..."，需按脚本目录相对导入或路径注册）。
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    # 幂等编写提示（DAT-19）：downgrade 中的 drop 操作同样先用内省判断再执行，保证可重复安全回滚。
    ${downgrades if downgrades else "pass"}
