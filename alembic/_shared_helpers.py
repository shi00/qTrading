"""Shared idempotency helpers for Alembic migrations.

DAT-19: 提取幂等判断的公共 helper，替代各迁移内重复实现的私有方法
（原 ``alembic/versions/0003_add_macro_publish_date.py`` 的 ``_column_exists``）。

所有检查基于 SQLAlchemy ``inspect()`` 内省而非手工拼接 SQL，
从而规避 R4（SQL 注入）与跨方言引号/大小写差异问题。
"""

import sqlalchemy as sa
from sqlalchemy import inspect


def column_exists(conn: sa.Connection, table_name: str, column_name: str) -> bool:
    """Check if ``column_name`` exists on ``table_name`` in the bound connection.

    Idempotency guard for add/drop column operations. Uses SQLAlchemy schema
    inspection which handles dialect-specific quoting and case automatically.

    Args:
        conn: The database connection (e.g. ``op.get_bind()`` in a migration).
        table_name: Name of the table to inspect.
        column_name: Name of the column to look up.

    Returns:
        True if the column is present on the table.
    """
    columns = {c["name"] for c in inspect(conn).get_columns(table_name)}
    return column_name in columns
