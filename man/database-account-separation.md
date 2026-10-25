# Database Account Separation Guide

> STATUS: Design Guide（设计性文档，仓库未提供自动化账号分离脚本）

This document describes how to separate database accounts for production deployments, following the principle of least privilege. All credential values below are placeholders (`CHANGE_ME_*`); never commit real passwords. Related runtime configuration: [README](../README.md) (external database mode).

## Prerequisites

- PostgreSQL 16.14.0+ with an existing database `astock`, plus a superuser session for role/privilege setup.
- Every application process must run with `QTRADING_DATABASE_MODE=external`. The default is `embedded`; in that mode the application uses its internal PostgreSQL and removes `DATABASE_URL` from the environment (`app/application.py`, `utils/config/db.py`), so the runtime account below would never be used.
- Run `pg_dump` before changing roles or migrating. `GRANT` statements take brief locks on existing tables; schedule them for low traffic.

## Overview

In production environments, the application should use separate database accounts:

| Account | Purpose | Privileges |
|---------|---------|------------|
| `astock_admin` | Schema migrations, DDL operations | CONNECT/CREATE on database; USAGE/CREATE on schema; owns migration-created tables |
| `astock_app` | Daily CRUD operations | SELECT, INSERT, UPDATE, DELETE on application tables |

## Security Rationale

Using a single account for both migrations and application operations violates the principle of least privilege:

1. **SQL Injection Risk**: If an SQL injection vulnerability exists, the attacker could execute DDL commands (DROP TABLE, TRUNCATE)
2. **Accidental Data Loss**: Application bugs could accidentally modify schema
3. **Audit Trail**: Separate accounts provide clear audit trails for schema changes vs data changes

## PostgreSQL Setup

### 1. Create Admin Account

Connect as a superuser (for example `psql -U postgres`), then run:

```sql
-- Migration account. LOGIN is granted by default.
-- CREATEDB is intentionally not granted: migrations create schema objects, not databases.
CREATE USER astock_admin WITH PASSWORD 'CHANGE_ME_ADMIN_PASSWORD';

-- Database-level ALL PRIVILEGES = CONNECT, CREATE, TEMPORARY.
-- Schema-level ALL PRIVILEGES = USAGE, CREATE (needed for tables/indexes/sequences).
GRANT ALL PRIVILEGES ON DATABASE astock TO astock_admin;
GRANT ALL PRIVILEGES ON SCHEMA public TO astock_admin;

-- DML on existing objects only; DDL on pre-existing tables still requires ownership (see below).
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO astock_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO astock_admin;
```

If this role must also create the database itself, grant `CREATEDB` separately; do not keep it as a default privilege.

When adopting account separation on an **existing** database whose objects are owned by another role (for example `postgres`), table privileges are not enough: `ALTER TABLE`/`DROP TABLE` require ownership, so `alembic upgrade head` fails with `must be owner of table`. Either keep that owner as the migration account, or transfer ownership before migrating (review the scope and take a backup first):

```sql
-- Connected to the astock database, as superuser
REASSIGN OWNED BY CHANGE_ME_OLD_OWNER TO astock_admin;
```

### 2. Create Application Account

Run as a superuser so the `ALTER DEFAULT PRIVILEGES FOR ROLE astock_admin` statements are allowed:

```sql
-- Create application account (no DDL, no CREATEDB)
CREATE USER astock_app WITH PASSWORD 'CHANGE_ME_APP_PASSWORD';

-- Grant connect/usage only
GRANT CONNECT ON DATABASE astock TO astock_app;
GRANT USAGE ON SCHEMA public TO astock_app;

-- Existing tables/sequences only (a fresh database has none yet; §3 covers them after migrations)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO astock_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO astock_app;

-- Automatic grants for objects created by the migration role.
-- FOR ROLE is required: without it the defaults apply to the role running this
-- statement (e.g. postgres) and would not cover tables created by astock_admin.
ALTER DEFAULT PRIVILEGES FOR ROLE astock_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO astock_app;
ALTER DEFAULT PRIVILEGES FOR ROLE astock_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO astock_app;
```

### 3. Post-Migration Grant Script

After each Alembic migration, run this script to grant privileges on new tables:

```sql
-- Run as astock_admin after migrations
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I TO astock_app', tbl);
    END LOOP;
END $$;

-- Grant sequence privileges
DO $$
DECLARE
    seq text;
BEGIN
    FOR seq IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'
    LOOP
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I TO astock_app', seq);
    END LOOP;
END $$;
```

Run this after every migration; it is idempotent. It also (re)grants access to `alembic_version`, which the application reads at startup to detect pending migrations.

## Configuration

### Development Environment

In development, a single account is acceptable for convenience:

```env
# .env (development only) — loaded by config.py (python-dotenv) and gitignored
QTRADING_DATABASE_MODE=external
DATABASE_URL=postgresql+asyncpg://CHANGE_ME_USER:CHANGE_ME_PASSWORD@localhost:5432/astock
```

### Production Environment

The application runtime uses the application account only:

```env
# .env (production)
QTRADING_DATABASE_MODE=external
DATABASE_URL=postgresql+asyncpg://astock_app:CHANGE_ME_APP_PASSWORD@db.example.com:5432/astock
```

Admin credentials belong only in the migration shell (§ Migration Workflow). Do not put them in the application runtime environment or persist them in the app config.

Runtime URL resolution (`utils/config/db.py::get_db_url`) is: `DATABASE_URL` environment variable → stored components (`db_host`/`db_port`/`db_user`/`db_name`, password from OS keyring or `DB_PASSWORD`; the environment variable wins) → `config.DB_URL`. `DB_HOST`/`DB_USER` environment variables are **not** read; use `DATABASE_URL` or the onboarding/config file.

### Alembic Configuration

`alembic/env.py::get_database_url()` resolves the URL in this order:

1. `alembic_config.attributes["database_url"]` (injected by application code, highest priority)
2. `alembic_config.get_main_option("sqlalchemy.url")` (the default placeholder `driver://user:pass@localhost/dbname` is ignored)
3. `ConfigHandler.get_db_url()` — inside it `DATABASE_URL` outranks stored components and `config.DB_URL`
4. `config.DB_URL` (initialized from `DATABASE_URL` at import time)
5. `os.environ.get("DATABASE_URL")` (fallback)

Setting `DATABASE_URL` in the migration shell is sufficient; `ALEMBIC_DATABASE_URL` is not read by this repository. Do **not** put real credentials in `alembic.ini` — it is version-controlled, and its `sqlalchemy.url` is a placeholder that `env.py` ignores.

> If `DATABASE_URL` is not set, Alembic silently falls back to the application's stored configuration and may attempt DDL as `astock_app`, which fails with `permission denied`. Always export it for the migration shell and confirm the account: `psql -h host -U astock_admin -d astock -c "SELECT current_user;"` must return `astock_admin`.

## Migration Workflow

> **Startup caveat**: the application checks the schema revision at startup, and its in-app "Upgrade" action runs Alembic with the **runtime** credentials (`data/persistence/db_migrator.py`, `app/startup_controller.py`). `astock_app` has no DDL rights, so: migrate as admin before starting the app, keep `AUTO_MIGRATE` unset/false, and never use the in-app upgrade dialog while the runtime account is `astock_app` (it fails with `permission denied`). For a fresh database, step 1 is mandatory — the app account cannot create the schema.

1. **Run migrations as admin** (repository root; admin credentials only in this shell):
   ```bash
   export DATABASE_URL="postgresql+asyncpg://astock_admin:CHANGE_ME_ADMIN_PASSWORD@host:5432/astock"
   # Windows PowerShell: $env:DATABASE_URL = "postgresql+asyncpg://..."

   psql -h host -U astock_admin -d astock -c "SELECT current_user;"   # must print astock_admin
   alembic upgrade head

   # Grant privileges to the app account with the Post-Migration Grant Script (§3)
   ```

2. **Run the application as the app user**:
   ```bash
   export QTRADING_DATABASE_MODE=external
   export DATABASE_URL="postgresql+asyncpg://astock_app:CHANGE_ME_APP_PASSWORD@host:5432/astock"
   # Windows PowerShell: $env:QTRADING_DATABASE_MODE = "external"; $env:DATABASE_URL = "..."

   python main.py
   ```

3. **Verify isolation**:
   ```bash
   psql -h host -U astock_app -d astock -c "SELECT count(*) FROM alembic_version;"  # succeeds
   psql -h host -U astock_app -d astock -c "CREATE TABLE _priv_check(id int);"       # must fail: permission denied
   ```

> `psql` does not accept the `+asyncpg` driver suffix used by Alembic. Pass the connection fields with `-h/-U/-d` as above, or use a `postgresql://` URL.

## Privilege Summary

### astock_admin (Migration Account)

| Privilege | Purpose |
|-----------|---------|
| CONNECT, CREATE on database (`ALL PRIVILEGES`) | Connect and create schema objects |
| USAGE, CREATE on schema public | Create tables, indexes, sequences |
| Ownership of tables/sequences created by migrations | ALTER/DROP/TRUNCATE and index creation |
| ALL PRIVILEGES on pre-existing tables/sequences | DML on objects created before this role existed (ALTER/DROP still require ownership) |

`CREATEDB` is not part of this role; grant it only if the role must create the database itself.

### astock_app (Application Account)

| Privilege | Purpose |
|-----------|---------|
| CONNECT | Connect to database |
| USAGE on schema | Access public schema |
| SELECT | Read data |
| INSERT | Create new records |
| UPDATE | Modify existing records |
| DELETE | Remove records |
| USAGE, SELECT on sequences | Use auto-increment columns |

**NOT granted**: CREATE TABLE, DROP TABLE, ALTER TABLE, TRUNCATE, CREATEDB

## Rollback

Follow this order to avoid dangling sessions and lockouts.

1. **Stop the application** so the app account holds no connections.
2. **Revert the runtime account**: restore the previous `DATABASE_URL` (or temporarily use `astock_admin`), start the app, and confirm it connects.
3. **Revoke the application privileges** (run as the object owner or a superuser):
   ```sql
   REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM astock_app;
   REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM astock_app;
   REVOKE ALL PRIVILEGES ON SCHEMA public FROM astock_app;
   REVOKE CONNECT ON DATABASE astock FROM astock_app;
   ALTER DEFAULT PRIVILEGES FOR ROLE astock_admin IN SCHEMA public
       REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM astock_app;
   ALTER DEFAULT PRIVILEGES FOR ROLE astock_admin IN SCHEMA public
       REVOKE USAGE, SELECT ON SEQUENCES FROM astock_app;
   ```
4. **Remove the application role** only after confirming it has no sessions and owns no objects:
   ```sql
   SELECT pid, usename FROM pg_stat_activity WHERE usename = 'astock_app';
   DROP ROLE astock_app;  -- run only after the query returns no sessions; close them normally, do not terminate backends blindly
   ```
   Note: `CONNECT` is granted to `PUBLIC` by default on a new database, so revoking the role-level grant alone does not block connections; dropping the role does. Revoke from `PUBLIC` only if you intend to change access for every role.

5. **Roll back a schema migration** (optional, potential data loss): restore the `pg_dump` backup, or run `alembic downgrade -1` (or a specific revision) as `astock_admin`. `alembic downgrade base` drops the whole schema and is for CI/test only. Re-run the Post-Migration Grant Script (§3) if the app account remains.
6. **Remove the migration role** (optional): it owns the objects it created. `DROP OWNED BY astock_admin` also drops those objects, so take a backup and never use it as a shortcut on production.

## Security Checklist

- [ ] Passwords are generated and stored in a secret manager or OS keyring (`DB_PASSWORD` for the runtime); no real credential is committed to `.env`, `alembic.ini`, or the repository
- [ ] Admin credentials are present only in the migration shell, never in the application runtime environment
- [ ] Runtime `DATABASE_URL` points to `astock_app`, and `QTRADING_DATABASE_MODE=external` is set for every app process
- [ ] `AUTO_MIGRATE` is unset/false; migrations are run as admin before app start
- [ ] Post-migration grant script is run after each schema change
- [ ] DDL is denied for the app account (verified in § Migration Workflow step 3)
- [ ] Database audit logging is enabled for DDL operations
- [ ] Regular security audit of granted privileges (`\dp`, `information_schema.role_table_grants`)

