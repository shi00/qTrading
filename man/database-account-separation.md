# Database Account Separation Guide

This document describes how to separate database accounts for production deployments, following the principle of least privilege.

## Overview

In production environments, the application should use separate database accounts:

| Account | Purpose | Privileges |
|---------|---------|------------|
| `astock_admin` | Schema migrations, DDL operations | CREATEDB, CREATE TABLE, DROP TABLE, ALTER TABLE |
| `astock_app` | Daily CRUD operations | SELECT, INSERT, UPDATE, DELETE on existing tables |

## Security Rationale

Using a single account for both migrations and application operations violates the principle of least privilege:

1. **SQL Injection Risk**: If an SQL injection vulnerability exists, the attacker could execute DDL commands (DROP TABLE, TRUNCATE)
2. **Accidental Data Loss**: Application bugs could accidentally modify schema
3. **Audit Trail**: Separate accounts provide clear audit trails for schema changes vs data changes

## PostgreSQL Setup

### 1. Create Admin Account

```sql
-- Connect as superuser
psql -U postgres

-- Create admin account for migrations
CREATE USER astock_admin WITH PASSWORD 'secure_admin_password' CREATEDB;

-- Grant schema creation privileges
GRANT ALL PRIVILEGES ON DATABASE astock TO astock_admin;
GRANT ALL PRIVILEGES ON SCHEMA public TO astock_admin;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO astock_admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO astock_admin;

-- Allow future table grants
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO astock_admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO astock_admin;
```

### 2. Create Application Account

```sql
-- Create application account (minimal privileges)
CREATE USER astock_app WITH PASSWORD 'secure_app_password';

-- Grant connect privilege
GRANT CONNECT ON DATABASE astock TO astock_app;
GRANT USAGE ON SCHEMA public TO astock_app;

-- Grant CRUD on existing tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO astock_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO astock_app;

-- Allow future table grants (run after each migration)
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO astock_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO astock_app;
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

## Configuration

### Development Environment

In development, you can use a single account for convenience:

```env
# .env (development only)
DATABASE_URL=postgresql+asyncpg://astock_dev:password@localhost:5432/astock
```

### Production Environment

In production, configure separate accounts:

```env
# .env (production)
# Admin account for migrations (set DATABASE_URL before running alembic commands)
DATABASE_URL=postgresql+asyncpg://astock_admin:admin_password@db.example.com:5432/astock

# Application account for daily operations (override DATABASE_URL after migration)
# DATABASE_URL=postgresql+asyncpg://astock_app:app_password@db.example.com:5432/astock
```

### Alembic Configuration

Update `alembic.ini` to use the admin account:

```ini
[alembic]
sqlalchemy.url = postgresql+asyncpg://astock_admin:admin_password@db.example.com:5432/astock
```

Or use environment variable:

`alembic/env.py` 的 `get_database_url()` 按以下优先级链解析数据库 URL（源码为准）：

1. `alembic_config.attributes["database_url"]`（应用代码注入，最高优先级）
2. `alembic_config.get_main_option("sqlalchemy.url")`（alembic.ini 配置，排除默认占位符）
3. `ConfigHandler.get_db_url()`（项目 ConfigHandler）
4. `config.DB_URL`（config 模块）
5. `os.environ.get("DATABASE_URL")`（环境变量兜底）

因此设置 `DATABASE_URL` 环境变量即可被 Alembic 识别，无需 `ALEMBIC_DATABASE_URL`。

## Migration Workflow

1. **Run migrations as admin**:
   ```bash
   # Set admin credentials
   export DATABASE_URL="postgresql+asyncpg://astock_admin:admin_password@host:5432/astock"
   
   # Run migration
   alembic upgrade head
   
   # Grant privileges to app account (参见下方 Privilege Summary)
   psql -U astock_admin -d astock -c "GRANT CONNECT ON DATABASE astock TO astock_app; GRANT USAGE ON SCHEMA public TO astock_app; GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO astock_app; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO astock_app;"
   ```

2. **Run application as app user**:
   ```bash
   # Set app credentials
   export DATABASE_URL="postgresql+asyncpg://astock_app:app_password@host:5432/astock"
   
   # Start application
   python main.py
   ```

## Privilege Summary

### astock_admin (Migration Account)

| Privilege | Purpose |
|-----------|---------|
| CREATEDB | Create new databases |
| CREATE TABLE | Create new tables |
| DROP TABLE | Remove tables (migration rollback) |
| ALTER TABLE | Modify table structure |
| CREATE INDEX | Create indexes |
| ALL PRIVILEGES on sequences | Manage auto-increment sequences |

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

## Security Checklist

- [ ] Admin account password is strong and stored securely (Keyring/vault)
- [ ] App account password is strong and stored securely
- [ ] Admin credentials are only accessible to deployment/migration processes
- [ ] App credentials are used by the application runtime
- [ ] Post-migration grant script is run after each schema change
- [ ] Database audit logging is enabled for DDL operations
- [ ] Regular security audit of granted privileges

