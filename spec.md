# spec.md — AStockScreener Product Contract

> **版本**: 0.9.0 | **最后更新**: 2026-07-24 | **维护者**: Harness (harness-plan)
>
> 本文件是项目产品契约（product contract），定义"什么是正确的"。
> Plans.md 是任务台账（task contract），定义"要做什么"。
> 两者冲突时以 spec.md 为准。precedence: `spec.md > sub-spec > Plans.md`。

---

## 1. 嵌入式 PostgreSQL 凭证管理契约

### 1.1 模式判定

- **判定入口**: `QTRADING_DATABASE_MODE` 环境变量（`embedded` | `external`，默认 `external`）+ `AppConfig.embedded_pg_enabled`
- **单一判定函数**: `ConfigHandler.is_embedded_mode()`（`utils/config_handler.py`）
- 两者均为 True 时才为 embedded 模式；否则 external 模式

### 1.2 凭证生成与所有权

| 凭证 | 生成方 | 存储位置 | 生命周期 |
|------|--------|----------|----------|
| 用户名 | AppConfig 默认值 `embedded_pg_username`（默认 `"postgres"`） | `user_settings.json` | 持久化，用户可改 |
| 密码 | **Rust sidecar** `password::generate_password()`（32 字符 URL-safe） | `<data_root>/runtime/password` 文件（Unix 0600，Windows NTFS ACL） | 持久化至 data_dir 删除；sidecar 首次启动生成，后续复用 |
| 连接 URL | **Python** `EmbeddedPostgresService._start_sync_impl` 拼接 | **仅内存**（`ConnectionInfo.url` + `config.DB_URL` 运行时变量） | **不持久化到 `user_settings.json`**（D15 决策：避免明文密码写 config） |

### 1.3 密码文件行为契约

- **Fresh data dir**（PGDATA 不存在）: sidecar 生成新密码 → 写 password 文件 → initdb 用此密码
- **Existing data dir + password 文件存在**: sidecar 复用既有密码（保证重启后密码一致）
- **Existing data dir + password 文件缺失**: sidecar **拒绝启动**（exit 16），需走 `reset-password` 流程，**禁止自动重置**（§13.7.8）
- **权限**: Unix 强制 0600；Windows 依赖 NTFS 用户私有 ACL
- **清理**: app 正常退出时**不删除** password 文件（保证下次启动可复用）；仅 `reset-password` / data dir 删除时清理

### 1.4 连接 URL 运行时获取契约（**本次修复核心**）

**问题**: 原实现用 `override_db_url` 上下文管理器临时覆盖 URL 源，`with` 块退出后 URL 失效，导致：
1. `ConfigHandler.get_db_url()` 在 `with` 块外返回空值
2. `check_onboarding_needed(db_url="")` 每次重启都返回 True → 强制 onboarding
3. 日志记录 "DB_URL configured: False"（误导诊断）
4. 任何 `with` 块外调用 `get_db_url()` 的代码路径拿不到 URL

**修复契约**:
- embedded 模式下，`prepare_database_runtime()` 返回 URL 后，**永久设置** `config.DB_URL = embedded_url`（运行时变量，非持久化到 config 文件，不设置 `DATABASE_URL` 环境变量以避免污染子进程）
- `ConfigHandler.get_db_url()` 通过 Priority 3（`config.DB_URL` 兜底）返回 embedded URL（Priority 1 `DATABASE_URL` 未设置、Priority 2 rebuild 因 `db_host` 为空跳过）
- **main.py 不再使用** `override_db_url` 上下文管理器；`db_url_override.py` 文件保留供测试 fixture 使用（测试需要临时覆盖 URL，产品代码不再引用）
- `CacheManager.__init__` 调用 `ConfigHandler.get_db_url()` 正常获取 embedded URL
- Alembic 迁移通过 `db_migrator._get_sync_database_url(engine)` 从 engine 获取 URL（已有逻辑，不受影响）

### 1.5 Secret 注册契约

- `EmbeddedPostgresService._start_sync_impl` 必须注册以下值为 secret（`DataSanitizer.register_secret`）:
  1. **完整 URL**（含密码）— 已有
  2. **密码本身**（单独注册）— **本次新增**，防止密码单独出现在错误消息中不被脱敏
- `DataSanitizer._PATTERN_URL_CREDENTIALS` 正则提供 URL 凭证模式兜底脱敏
- `DataSanitizer._known_secrets` 精确替换提供裸密码脱敏

### 1.6 Onboarding 行为契约

- **external 模式**: `check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete)` — db_url 为空时需要 onboarding
- **embedded 模式**: DB 自动准备，但 Token/AI Key 仍需用户配置
  - 首次启动（`onboarding_complete=False`）: 进入 onboarding wizard，DB step 显示"本地数据库已自动准备"
  - **后续重启**（`onboarding_complete=True`）: **必须跳过 onboarding**，直接进入主应用
  - 判定逻辑: embedded 模式下 `check_onboarding_needed` 不应因 `db_url` 为空而返回 True（因为 embedded URL 已通过 `config.DB_URL` 提供）

### 1.7 不变量

- embedded URL **永不写入** `user_settings.json`（D15 决策不变）
- embedded URL **永不写入** `DATABASE_URL` 环境变量（避免污染子进程；仅设置 `config.DB_URL` 运行时变量）
- password 文件 **永不删除**（除 reset-password / data dir 删除）
- `config.DB_URL` 在 embedded 模式启动后 **永不被还原**（app 生命周期内持久）
- `EmbeddedPostgresService` 单例在 app 生命周期内 **永驻**（stop_sync 仅在 shutdown 时调用）
- `db_url_override.py` 保留供测试使用（产品代码不再引用）

---

## 2. 变更历史

| 日期 | 版本 | 变更 | 作者 |
|------|------|------|------|
| 2026-07-24 | 0.9.0 | 初始创建：嵌入式 PG 凭证管理契约（harness-plan create） | Harness |
| 2026-07-24 | 0.9.1 | §1.4/§1.7 修正：`db_url_override.py` 保留供测试使用；明确不设置 `DATABASE_URL` 环境变量 | Harness |
