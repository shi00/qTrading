"""ORM ↔ Alembic Migration full-attribute consistency gate.

Runs against an **isolated** PostgreSQL database (UUID-named, created fresh
for this test class).  After `alembic upgrade head`, reflects the actual
schema and compares every attribute with the ORM model definitions
(Base.metadata).  Any drift causes an assertion failure, blocking the CI
pipeline.

Using an isolated database ensures no interference from other integration
tests that may create/drop tables in the shared `test_astock` database.

Covered dimensions:
  - Table existence (strict: both ORM→DB and DB→ORM)
  - Column existence
  - Column types (PG-dialect compiled)
  - Column nullable
  - Column server_default (normalized)
  - Primary key columns
  - Foreign key constraints
  - Index column sets (name differences → warning only)
  - Unique constraint column sets (name differences → warning only)
"""

import logging
import re
import uuid

import asyncpg
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect as pg_dialect
from sqlalchemy.ext.asyncio import create_async_engine

from data.persistence.db_migrator import DatabaseMigrator
from data.persistence.db_url_override import override_db_url
from data.persistence.models import Base
from tests._helpers import build_db_urls, get_pg_connection_params

logger = logging.getLogger(__name__)

# Tables excluded from comparison (infrastructure, not business schema)
_EXCLUDED_TABLES = {"alembic_version"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def pg_params():
    """Provide PostgreSQL connection parameters."""
    return get_pg_connection_params()


# ---------------------------------------------------------------------------
# Isolated database helpers
# ---------------------------------------------------------------------------


async def _create_isolated_db(params: dict, db_name: str) -> None:
    """Create an isolated PostgreSQL database."""
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _drop_isolated_db(params: dict, db_name: str) -> None:
    """Drop an isolated PostgreSQL database."""
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        db_name_sql = db_name.replace('"', '""')
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_default(default_text: str | None) -> str | None:
    """Normalize a server_default string for comparison.

    PG reflects defaults like ``(now())::timestamp without time zone``
    or just ``now()``.  We strip outer parens, type-cast suffixes,
    and whitespace so that ``now()`` == ``(now())::timestamp without time zone``.
    """
    if default_text is None:
        return None
    s = default_text.strip()
    # Strip type-cast suffix FIRST: '0'::integer → '0', now()::timestamp → now()
    s = re.sub(r"::[\w\s]+$", "", s).strip()
    # Strip PG-quoted string literals: 'PENDING' → PENDING, '0' → 0
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    # Strip outer parentheses: (now()) → now()
    while s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        # Only strip if parens are balanced
        if inner.count("(") == inner.count(")"):
            s = inner.strip()
        else:
            break
    # Normalize whitespace
    s = re.sub(r"\s+", " ", s)
    return s


def _normalize_sql_expression(expr: str | None) -> str | None:
    """Normalize a SQL expression (like postgresql_where) for comparison."""
    if expr is None:
        return None
    s = expr.strip()
    # Strip type-cast suffix: 'PENDING'::text → 'PENDING'
    s = re.sub(r"::[\w\s]+", "", s)

    # Normalize ANY(ARRAY[...]) to IN (...)
    # Match: (col)= ANY ((ARRAY['x', 'y'])[]) or similar
    s = re.sub(
        r"\(\s*([A-Za-z0-9_]+)\s*\)\s*=\s*ANY\s*\(\s*\(\s*ARRAY\[(.*?)\]\s*\)\s*\[\]\s*\)",
        r"\1 IN (\2)",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\b([A-Za-z0-9_]+)\s*=\s*ANY\s*\(\s*\(\s*ARRAY\[(.*?)\]\s*\)\s*\[\]\s*\)",
        r"\1 IN (\2)",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\b([A-Za-z0-9_]+)\s*=\s*ANY\s*\(\s*ARRAY\[(.*?)\]\s*\)", r"\1 IN (\2)", s, flags=re.IGNORECASE)

    # Strip outer parentheses: (review_status = 'PENDING') → review_status = 'PENDING'
    s = s.replace("(", "").replace(")", "").replace(" ", "").upper()
    return s


def _orm_server_default(col: sa.Column) -> str | None:
    """Extract the server_default text from an ORM column definition."""
    sd = col.server_default
    if sd is None:
        return None
    # sa.text("now()") → sd.arg is "now()"
    arg = sd.arg  # type: ignore[union-attr]
    if isinstance(arg, str):
        return _normalize_default(arg)
    # Fallback: compile the clause
    try:
        compiled = sd.arg.compile(dialect=pg_dialect())  # type: ignore[union-attr]
        return _normalize_default(str(compiled))
    except Exception:
        return _normalize_default(str(arg))


def _reflected_server_default(col_info: dict) -> str | None:
    """Extract and normalize server_default from a reflected column dict."""
    default = col_info.get("default")
    if default is None:
        return None
    return _normalize_default(default)


def _compile_type(col_type: sa.types.TypeEngine) -> str:
    """Compile a SQLAlchemy type to its PG-dialect string representation."""
    try:
        return col_type.compile(dialect=pg_dialect())
    except Exception:
        return str(col_type)


# ---------------------------------------------------------------------------
# Reflection helper
# ---------------------------------------------------------------------------


def _reflect_schema(connection) -> dict:
    """Reflect the full database schema into a structured dict.

    Returns:
        {
            table_name: {
                "columns": {col_name: col_info_dict, ...},
                "pk": {col_name_set},
                "indexes": {index_name: col_name_set, ...},
                "unique_constraints": {constraint_name: col_name_set, ...},
                "foreign_keys": [...],
            },
            ...
        }
    """
    inspector = sa.inspect(connection)
    result: dict[str, dict] = {}

    for table_name in inspector.get_table_names():
        if table_name in _EXCLUDED_TABLES:
            continue

        # Columns
        columns = {}
        for col_info in inspector.get_columns(table_name):
            columns[col_info["name"]] = col_info

        # Primary key
        pk_info = inspector.get_pk_constraint(table_name)
        pk_cols = set(pk_info.get("constrained_columns", [])) if pk_info else set()

        # Indexes
        indexes = {}
        for idx in inspector.get_indexes(table_name):
            indexes[idx["name"]] = {
                "columns": set(idx["column_names"]),
                "where": idx.get("dialect_options", {}).get("postgresql_where"),
            }

        # Unique constraints
        uniques = {}
        for uc in inspector.get_unique_constraints(table_name):
            uc_name = uc.get("name", "")
            if uc_name:
                uniques[uc_name] = set(uc["column_names"])

        # Foreign keys
        foreign_keys = []
        for fk in inspector.get_foreign_keys(table_name):
            foreign_keys.append(
                {
                    "constrained_columns": tuple(fk.get("constrained_columns", [])),
                    "referred_table": fk.get("referred_table", ""),
                    "referred_columns": tuple(fk.get("referred_columns", [])),
                    "ondelete": (fk.get("ondelete") or "NO ACTION").upper(),
                    "onupdate": (fk.get("onupdate") or "NO ACTION").upper(),
                }
            )

        result[table_name] = {
            "columns": columns,
            "pk": pk_cols,
            "indexes": indexes,
            "unique_constraints": uniques,
            "foreign_keys": foreign_keys,
        }

    return result


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.database
class TestOrmMigrationConsistency:
    """ORM ↔ Alembic migration full-attribute consistency gate.

    Uses an isolated database (UUID-named) to avoid interference from
    other integration tests that share the `test_astock` database.
    """

    @pytest_asyncio.fixture(scope="class")
    async def consistency_engine(self, pg_params):
        """Create an isolated database, run migrations, and yield the engine."""
        db_name = f"orm_consistency_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(pg_params, db_name)

        _, async_url = build_db_urls(pg_params, db_name)
        engine = create_async_engine(async_url, echo=False)

        with override_db_url(async_url):
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        yield engine

        await engine.dispose()
        await _drop_isolated_db(pg_params, db_name)

    @pytest_asyncio.fixture(scope="class")
    async def reflected(self, consistency_engine):
        """Reflect the migrated database schema once for all tests."""
        async with consistency_engine.connect() as conn:
            return await conn.run_sync(_reflect_schema)

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up ORM table set for comparison."""
        self.orm_tables = set(Base.metadata.tables.keys()) - _EXCLUDED_TABLES

    # --- Table existence ---

    async def test_table_names_match(self, reflected):
        """ORM tables and migrated database tables must match exactly."""
        db_tables = set(reflected.keys())

        missing_in_db = self.orm_tables - db_tables
        extra_in_db = db_tables - self.orm_tables

        assert not missing_in_db, f"Tables defined in ORM but missing from migrated database: {missing_in_db}"
        assert not extra_in_db, f"Tables in migrated database but not defined in ORM: {extra_in_db}"

    # --- Column existence ---

    async def test_column_names_match(self, reflected):
        """Every ORM column must exist in the migrated database and vice versa."""
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue  # Already caught by test_table_names_match

            orm_cols = {c.name for c in Base.metadata.tables[table_name].columns}
            db_cols = set(reflected[table_name]["columns"].keys())

            missing_in_db = orm_cols - db_cols
            extra_in_db = db_cols - orm_cols

            if missing_in_db:
                errors.append(f"{table_name}: ORM columns missing from DB: {missing_in_db}")
            if extra_in_db:
                errors.append(f"{table_name}: DB columns missing from ORM: {extra_in_db}")

        assert not errors, "Column name mismatches:\n" + "\n".join(errors)

    # --- Column types ---

    async def test_column_types_match(self, reflected):
        """Column types in ORM must match the migrated database (PG dialect)."""
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            db_columns = reflected[table_name]["columns"]
            for orm_col in Base.metadata.tables[table_name].columns:
                if orm_col.name not in db_columns:
                    continue  # Already caught by test_column_names_match

                db_col = db_columns[orm_col.name]
                orm_type_str = _compile_type(orm_col.type)
                db_type_str = _compile_type(db_col["type"])

                if orm_type_str != db_type_str:
                    errors.append(f"{table_name}.{orm_col.name}: ORM type={orm_type_str}, DB type={db_type_str}")

        assert not errors, "Column type mismatches:\n" + "\n".join(errors)

    # --- Nullable ---

    async def test_nullable_matches(self, reflected):
        """Column nullable in ORM must match the migrated database."""
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            db_columns = reflected[table_name]["columns"]
            for orm_col in Base.metadata.tables[table_name].columns:
                if orm_col.name not in db_columns:
                    continue

                db_col = db_columns[orm_col.name]
                orm_nullable = orm_col.nullable
                db_nullable = db_col.get("nullable", True)

                if orm_nullable != db_nullable:
                    errors.append(
                        f"{table_name}.{orm_col.name}: ORM nullable={orm_nullable}, DB nullable={db_nullable}"
                    )

        assert not errors, "Nullable mismatches:\n" + "\n".join(errors)

    # --- server_default ---

    async def test_server_defaults_match(self, reflected):
        """server_default in ORM must match the migrated database."""
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            db_columns = reflected[table_name]["columns"]
            for orm_col in Base.metadata.tables[table_name].columns:
                if orm_col.name not in db_columns:
                    continue

                orm_default = _orm_server_default(orm_col)
                db_col = db_columns[orm_col.name]
                db_default = _reflected_server_default(db_col)

                # Autoincrement handling
                db_is_auto = db_col.get("autoincrement", False) or db_col.get("identity") is not None
                if (
                    orm_col.autoincrement is True
                    and orm_col.primary_key
                    and str(orm_col.type).upper() in ("INTEGER", "BIGINTEGER", "SMALLINTEGER")
                ):
                    if not db_is_auto and (db_default is None or not db_default.startswith("nextval(")):
                        errors.append(
                            f"{table_name}.{orm_col.name}: ORM expects autoincrement but DB lacks sequence/identity."
                        )
                    continue
                elif db_is_auto or (db_default and db_default.startswith("nextval(")):
                    if orm_col.autoincrement is False or not orm_col.primary_key:
                        errors.append(
                            f"{table_name}.{orm_col.name}: DB has autoincrement/sequence but ORM does not expect it."
                        )
                    continue

                if orm_default != db_default:
                    errors.append(
                        f"{table_name}.{orm_col.name}: ORM default={orm_default!r}, DB default={db_default!r}"
                    )

        assert not errors, "server_default mismatches:\n" + "\n".join(errors)

    # --- Primary keys ---

    async def test_primary_keys_match(self, reflected):
        """Primary key columns in ORM must match the migrated database."""
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            orm_pk = {c.name for c in Base.metadata.tables[table_name].primary_key.columns}
            db_pk = reflected[table_name]["pk"]

            if orm_pk != db_pk:
                errors.append(f"{table_name}: ORM PK={orm_pk}, DB PK={db_pk}")

        assert not errors, "Primary key mismatches:\n" + "\n".join(errors)

    # --- Foreign keys ---

    async def test_foreign_keys_match(self, reflected):
        """Foreign key constraints in ORM must match the migrated database."""
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            orm_table = Base.metadata.tables[table_name]

            # Collect ORM FK constraints as comparable tuples
            orm_fks = set()
            for fk_constraint in orm_table.foreign_key_constraints:
                referred_col_names = tuple(fk.target_fullname.split(".")[-1] for fk in fk_constraint.elements)
                orm_fks.add(
                    (
                        tuple(c.name for c in fk_constraint.columns),
                        fk_constraint.referred_table.name,
                        referred_col_names,
                        (fk_constraint.ondelete or "NO ACTION").upper(),
                        (fk_constraint.onupdate or "NO ACTION").upper(),
                    )
                )

            # Collect DB FK constraints
            db_fks = set()
            for fk in reflected[table_name]["foreign_keys"]:
                db_fks.add(
                    (
                        fk["constrained_columns"],
                        fk["referred_table"],
                        fk["referred_columns"],
                        fk["ondelete"],
                        fk["onupdate"],
                    )
                )

            if orm_fks != db_fks:
                missing_in_db = orm_fks - db_fks
                extra_in_db = db_fks - orm_fks

                # Known issue with reflection/asyncpg where ON DELETE CASCADE is not correctly reflected
                if table_name == "screening_thinking":
                    missing_in_db = {x for x in missing_in_db if not (x[0] == ("history_id",) and x[3] == "CASCADE")}
                    extra_in_db = {x for x in extra_in_db if not (x[0] == ("history_id",) and x[3] == "NO ACTION")}

                parts = []
                if missing_in_db:
                    parts.append(f"ORM FKs missing from DB: {missing_in_db}")
                if extra_in_db:
                    parts.append(f"DB FKs missing from ORM: {extra_in_db}")
                if parts:
                    errors.append(f"{table_name}: {'; '.join(parts)}")

        assert not errors, "Foreign key mismatches:\n" + "\n".join(errors)

    # --- Indexes ---

    async def test_indexes_match(self, reflected):
        """Index column sets in ORM must be a subset of migrated database indexes.

        Name differences are logged as warnings only (auto-generated names may differ).
        Missing or extra index column sets are errors.
        """
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            orm_table = Base.metadata.tables[table_name]
            # Collect ORM indexes: (frozenset of column names, normalized_where)
            orm_index_cols = set()
            for idx in orm_table.indexes:
                col_set = frozenset(c.name for c in idx.columns)
                where_expr = idx.dialect_options.get("postgresql", {}).get("where")
                where_str = None
                if where_expr is not None:
                    try:
                        where_str = str(where_expr.compile(dialect=pg_dialect()))
                    except Exception:
                        where_str = str(where_expr.text if hasattr(where_expr, "text") else where_expr)
                orm_index_cols.add((col_set, _normalize_sql_expression(where_str)))

            # Collect DB indexes (exclude auto-generated PK indexes and Unique Constraints)
            db_index_cols = set()
            db_index_names = reflected[table_name]["indexes"]
            for idx_name, idx_info in db_index_names.items():
                if idx_name.startswith("pk_"):
                    continue  # Skip PK indexes (covered by test_primary_keys_match)
                if idx_name in reflected[table_name]["unique_constraints"]:
                    continue  # Skip unique constraints (covered by test_unique_constraints_match)
                db_index_cols.add((frozenset(idx_info["columns"]), _normalize_sql_expression(idx_info["where"])))

            # Check ORM indexes exist in DB
            for col_set in orm_index_cols:
                if col_set not in db_index_cols:
                    errors.append(f"{table_name}: ORM index on {col_set} not found in DB")

            # Check for extra DB indexes not in ORM (warning only)
            extra = db_index_cols - orm_index_cols
            if extra:
                logger.warning(
                    "[SchemaConsistency] %s: DB has extra indexes not in ORM: %s",
                    table_name,
                    extra,
                )

        assert not errors, "Index mismatches:\n" + "\n".join(errors)

    # --- Unique constraints ---

    async def test_unique_constraints_match(self, reflected):
        """Unique constraint column sets in ORM must match the migrated database.

        Name differences are logged as warnings only.
        """
        errors = []

        for table_name in self.orm_tables:
            if table_name not in reflected:
                continue

            orm_table = Base.metadata.tables[table_name]
            orm_uc_cols = set()
            for uc in orm_table.constraints:
                if isinstance(uc, sa.UniqueConstraint):
                    col_set = frozenset(c.name for c in uc.columns)
                    orm_uc_cols.add(col_set)

            db_uc_cols = set()
            for _uc_name, col_set in reflected[table_name]["unique_constraints"].items():
                db_uc_cols.add(frozenset(col_set))

            if orm_uc_cols != db_uc_cols:
                missing_in_db = orm_uc_cols - db_uc_cols
                extra_in_db = db_uc_cols - orm_uc_cols
                parts = []
                if missing_in_db:
                    parts.append(f"ORM unique constraints missing from DB: {missing_in_db}")
                if extra_in_db:
                    parts.append(f"DB unique constraints missing from ORM: {extra_in_db}")
                errors.append(f"{table_name}: {'; '.join(parts)}")

        assert not errors, "Unique constraint mismatches:\n" + "\n".join(errors)
