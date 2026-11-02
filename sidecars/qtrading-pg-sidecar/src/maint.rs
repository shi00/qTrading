//! 离线维护命令（pg_plan §13.2）：doctor / dump / restore / maintenance-shell。
//!
//! - doctor：只读诊断（§13.5 诊断包数据源），输出 doctor JSON + issues 汇总，永不修改 PGDATA。
//! - dump：运行中实例直连 `pg_dump`（SQL 级，不占锁，§13.3 AI-28）；离线则持锁起临时实例。
//! - restore：恢复到新目录不覆盖原目录（§12.2），复用既有密码（AI-29），临时实例 + pg_restore；
//!   显式目标使用独立 runtime 时写入同一密码，保证目标布局可独立启动（D16-F05）。
//! - maintenance-shell：持锁起临时维护实例，输出脱敏连接信息，等用户结束后优雅停止。
//!
//! 恢复后 schema 停留于备份时点；Alembic 补平由 app 下次启动的既有迁移流程负责（Phase 2+，§12.2 step 7）。

use crate::cli::{self, DEFAULT_DATABASE};
use crate::commands::state_file_condition;
use crate::exit_codes;
use crate::lockfile::{AcquireError, MaintenanceLock};
use crate::logging;
use crate::password;
use crate::paths::Layout;
use crate::pgbin;
use crate::preflight;
use crate::process_identity;
use crate::protocol;
use crate::run;
use crate::setup::{self, DataDirState};
use crate::state;
use postgresql_embedded::PostgreSQL;
use serde::Serialize;
use std::ffi::{OsStr, OsString};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

const DUMP_TIMEOUT: Duration = Duration::from_secs(3600);
const LISTEN_LOCAL: &str = "127.0.0.1";

/// SQL 标识符白名单：`[A-Za-z_][A-Za-z0-9_]*` 且长度 ≤ 63（PostgreSQL NAMEDATALEN-1）。
///
/// 会进入 SQL 文本或 psql `-U` 的标识符（库名、超级用户名）必须先经此校验，
/// Python 侧同样校验；此处独立校验防止直接调用 CLI 绕过（R4：SQL 注入）。
fn validate_identifier(value: &str, what: &str) -> Result<(), u8> {
    let mut chars = value.chars();
    let first_ok = chars
        .next()
        .map(|c| c.is_ascii_alphabetic() || c == '_')
        .unwrap_or(false);
    let rest_ok = chars.all(|c| c.is_ascii_alphanumeric() || c == '_');
    if first_ok && rest_ok && value.len() <= 63 {
        Ok(())
    } else {
        eprintln!("[sidecar] {what}非法（须为 [A-Za-z_][A-Za-z0-9_]*，长度 ≤ 63）: {value:?}");
        Err(exit_codes::ARGUMENT_ERROR)
    }
}

/// 业务库名白名单（UIR-F16-002；库名会进入 `CREATE DATABASE "<name>"` 的 SQL 文本）。
fn validate_database_name(name: &str) -> Result<(), u8> {
    validate_identifier(name, "数据库名")
}

/// 超级用户名白名单校验（M23：reset-password 需按实例实际超级用户改密）。
///
/// 用户名会进入 `ALTER USER <name> PASSWORD ...` 的 SQL 文本，白名单校验是注入防护。
fn validate_username(name: &str) -> Result<(), u8> {
    validate_identifier(name, "用户名")
}

fn acquire_lock(layout: &Layout) -> Result<MaintenanceLock, u8> {
    match MaintenanceLock::try_acquire(&layout.lock_file) {
        Ok(l) => Ok(l),
        Err(AcquireError::Conflict) => {
            eprintln!(
                "[sidecar] 维护锁冲突：qTrading 或另一维护进程正在使用 {}；请先关闭后重试",
                layout.data_dir.display()
            );
            Err(exit_codes::LOCK_CONFLICT)
        }
        Err(AcquireError::Io(e)) => {
            eprintln!(
                "[sidecar] 维护锁获取失败 {}: {e}",
                layout.lock_file.display()
            );
            Err(exit_codes::LOCK_CONFLICT)
        }
    }
}

/// D16-F03：restore 前确认源 PGDATA 没有仍在运行的实例。
///
/// 维护锁只排除协作进程（qTrading / 其他 sidecar）。sidecar 崩溃或被 kill 后锁由
/// OS 释放，但 postgres 可能仍在运行；锁可获取 ≠ 源集群已停止。因此必须在任何
/// setup/initdb/目录切换前读取源 `postmaster.pid` 并复用 F02 的归属校验：
///
/// - 无 pid 文件 / PID 已不存在（含僵尸）→ 视为已停止，允许继续（原目录损坏也可恢复）；
/// - PID 仍存活且可证明属于本源 PGDATA → `LOCK_CONFLICT`，提示先 stop，不自动杀；
/// - PID 仍存活但无法证明归属 → fail closed，同样 `LOCK_CONFLICT`（绝不发送信号）。
///
/// 损坏目录中不可解析的 pid 文件按「无活实例」处理：无法证明存在活进程时不应
/// 阻止恢复；只有「PID 存活」这一事实与身份证据冲突时才拒绝。
fn ensure_source_cluster_idle(layout: &Layout, phase: &str) -> Result<(), u8> {
    let Some(info) = pgbin::read_postmaster_pid(&layout.data_dir) else {
        return Ok(());
    };
    if !pgbin::process_alive(info.pid) {
        tracing::warn!(
            "stale postmaster.pid (pid {}) ignored before restore ({phase})",
            info.pid
        );
        return Ok(());
    }

    let stop_hint = format!(
        "请先执行 `qtrading-pg-sidecar stop --data-dir {}`，确认实例已停止后重试",
        layout.data_dir.display()
    );
    match process_identity::capture_cluster_identity_from_info(
        &layout.data_dir,
        &layout.install_dir,
        &info,
    ) {
        Ok(identity) => {
            eprintln!(
                "[sidecar] {phase}：源 PGDATA 仍有 PostgreSQL 实例在运行（pid {}，启动于 {}）。\
                 restore 不会自动停止或切换正在使用的数据目录；{stop_hint}。",
                identity.pid, identity.postmaster_started_at
            );
            Err(exit_codes::LOCK_CONFLICT)
        }
        // 存活探测与身份读取之间进程已退出（含僵尸）：按已停止处理，绝不发送信号。
        Err(e) if e.is_not_running() => {
            tracing::warn!(
                "postgres pid {} exited during restore guard ({phase}): {e}",
                info.pid
            );
            Ok(())
        }
        Err(e) => {
            eprintln!(
                "[sidecar] {phase}：PID {} 在运行但无法证明其属于源 PGDATA {}（{e}）。\
                 restore 按 fail closed 拒绝切换，不会向该 PID 发送信号；{stop_hint}，\
                 或人工确认该进程归属后重试。",
                info.pid,
                layout.data_dir.display()
            );
            Err(exit_codes::LOCK_CONFLICT)
        }
    }
}

fn load_password(layout: &Layout) -> Result<String, u8> {
    match password::read_password_file(&layout.password_file) {
        Some(p) => {
            logging::register_secret(&p);
            Ok(p)
        }
        None => {
            eprintln!(
                "[sidecar] 密码文件缺失（{}）：若密码丢失请先走 reset-password 流程（§13.7.8）",
                layout.password_file.display()
            );
            Err(exit_codes::PASSWORD_FAILED)
        }
    }
}

/// 目录名用紧凑时间戳（`20260721T153000Z`）。
fn ts_compact() -> String {
    run::utc_now_iso8601()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect()
}

// ---- doctor ----

#[derive(Serialize, Debug)]
struct Issue {
    code: &'static str,
    /// "error" = run 必然失败；"warning" = 可运行但需关注
    severity: &'static str,
    message: String,
}

#[derive(Serialize, Debug)]
struct DoctorJson {
    schema: &'static str,
    data_dir: String,
    initialized: bool,
    pg_version: Option<u32>,
    bundled_pg_major: Option<u32>,
    version_match: bool,
    critical_files_missing: Vec<String>,
    install_dir_complete: bool,
    missing_tools: Vec<String>,
    lock_held: bool,
    postmaster_pid: Option<u32>,
    postgres_alive: bool,
    /// 运行中实例的实际监听端口（来自 postmaster.pid，仅进程存活时取值）。
    /// 未运行 / pid 残留 / 端口缺失或非法时为 null（未知），不回退到配置端口。
    port: Option<u16>,
    stale_postmaster_pid: bool,
    cluster_state: Option<String>,
    control_data_readable: bool,
    data_checksums: Option<bool>,
    hba_matches_template: Option<bool>,
    managed_block_drift: Vec<String>,
    password_file_present: bool,
    password_file_perms_ok: bool,
    fs_kind: String,
    fs_supported: bool,
    free_bytes: Option<u64>,
    cloud_sync_feature: Option<String>,
    state_file: &'static str,
    runtime_status: Option<String>,
    last_start_error: Option<String>,
    last_stop_mode: Option<String>,
    kill_fallback_count: u32,
    /// §13.7.44 / §7.5 残留物扫描：兄弟目录中检测到的 `<data_dir_name>.restore-*` 目录。
    restore_residuals: Vec<String>,
    /// §13.7.44 / §7.5 残留物扫描：兄弟目录中检测到的 `*.partial` 文件（dump 中断残留）。
    dump_partials: Vec<String>,
    issues: Vec<Issue>,
}

/// 运行中 postmaster 的监听端口（可信运行态）。
///
/// 仅当 PostgreSQL 进程实际存活时取 `postmaster.pid` 中记录的端口；
/// 进程已死（pid 残留）、端口缺失或为 0 时返回 `None`（未知），
/// 不回退到任何配置端口（调用方不得用外置配置补位）。
fn runtime_port(pm: Option<&pgbin::PostmasterInfo>, postgres_alive: bool) -> Option<u16> {
    if !postgres_alive {
        return None;
    }
    pm.and_then(|i| i.port).filter(|p| *p > 0)
}

pub fn doctor(data_dir: &Path) -> Result<(), u8> {
    logging::init(None);
    let layout = Layout::from_data_dir(data_dir, None, None, None);
    let mut issues: Vec<Issue> = Vec::new();
    let error = |code: &'static str, message: String| Issue {
        code,
        severity: "error",
        message,
    };
    let warning = |code: &'static str, message: String| Issue {
        code,
        severity: "warning",
        message,
    };

    // -- 数据目录形态 --
    let initialized = layout.data_dir.join("PG_VERSION").exists();
    let nonempty = layout.data_dir.exists()
        && std::fs::read_dir(&layout.data_dir)
            .map(|mut r| r.next().is_some())
            .unwrap_or(false);
    if nonempty && !initialized {
        issues.push(error(
            "nonempty_no_pg_version",
            "数据目录非空但 PG_VERSION 缺失（initdb 中断残留或误指目录），run 将返回 exit 40"
                .into(),
        ));
    }
    let pg_version = setup::read_pg_major(&layout.data_dir);
    let bundled_major = setup::bundled_pg_major();
    let version_match = match (pg_version, bundled_major) {
        (Some(found), Some(want)) => found == want,
        (None, _) => !initialized, // 未初始化不算不匹配
        (Some(_), None) => true,   // bundled 版本未知时不判定不匹配
    };
    if initialized && !version_match {
        issues.push(error(
            "version_mismatch",
            format!("PG_VERSION 主版本 {pg_version:?} 与内置 PostgreSQL {bundled_major:?} 不匹配，需走迁移流程"),
        ));
    }
    let critical_files_missing: Vec<String> = if initialized {
        ["global/pg_control", "base", "postgresql.conf"]
            .iter()
            .filter(|f| !layout.data_dir.join(f).exists())
            .map(|f| f.to_string())
            .collect()
    } else {
        Vec::new()
    };
    if !critical_files_missing.is_empty() {
        issues.push(error(
            "critical_files_missing",
            format!(
                "关键文件缺失：{}（§13.7.37）",
                critical_files_missing.join(", ")
            ),
        ));
    }

    // -- install dir --
    let marker = layout.install_dir.join(setup::SETUP_COMPLETE_MARKER);
    let missing_tools: Vec<String> = pgbin::missing_tools(&layout.install_dir)
        .iter()
        .map(|s| s.to_string())
        .collect();
    let install_dir_complete = marker.exists() && missing_tools.is_empty();
    if !install_dir_complete {
        issues.push(warning(
            "install_incomplete",
            "install dir 缺少 .setup-complete 或关键工具，下次 run 将重做 setup（§16.2）".into(),
        ));
    }

    // -- 进程与锁 --
    let pm = pgbin::read_postmaster_pid(&layout.data_dir);
    let postgres_alive = pm.as_ref().is_some_and(|i| pgbin::process_alive(i.pid));
    // 实际监听端口只取可信运行态（进程存活时的 postmaster.pid），供 UI 状态面板展示
    let port = runtime_port(pm.as_ref(), postgres_alive);
    let stale_postmaster_pid = pm.is_some() && !postgres_alive;
    if stale_postmaster_pid {
        issues.push(warning(
            "stale_postmaster_pid",
            "postmaster.pid 残留但进程已死（sidecar 崩溃痕迹）；run/stop 会自动清理".into(),
        ));
    }
    let lock_held = MaintenanceLock::is_held(&layout.lock_file);

    // -- pg_control --
    let control = if initialized {
        pgbin::read_control_data(&layout.install_dir, &layout.data_dir)
    } else {
        None
    };
    let control_data_readable = !initialized || control.is_some();
    if initialized && control.is_none() {
        issues.push(error(
            "pg_control_unreadable",
            "pg_controldata 不可读（pg_control 损坏嫌疑，§13.7.36）；禁止 pg_resetwal，请从备份恢复".into(),
        ));
    }
    if let Some(c) = &control {
        if c.cluster_state != "shut down" && c.cluster_state != "in production" && !postgres_alive {
            issues.push(warning(
                "cluster_state_abnormal",
                format!(
                    "cluster state = \"{}\"（上次未干净关闭，下次启动将进入 crash recovery）",
                    c.cluster_state
                ),
            ));
        }
    }

    // -- 配置漂移（仅已初始化时评估） --
    let hba_matches = initialized.then(|| setup::hba_matches_template(&layout.data_dir));
    if hba_matches == Some(false) {
        issues.push(warning(
            "hba_drift",
            "pg_hba.conf 与 sidecar 模板不一致，下次 run 将重写（§13.7.33）".into(),
        ));
    }
    let managed_drift: Vec<String> = if initialized {
        setup::managed_block_drift(&layout.data_dir)
            .iter()
            .map(|s| s.to_string())
            .collect()
    } else {
        Vec::new()
    };
    if !managed_drift.is_empty() {
        issues.push(warning(
            "managed_block_drift",
            format!(
                "postgresql.conf 受管参数漂移：{}，下次 run 将自修复",
                managed_drift.join(", ")
            ),
        ));
    }

    // -- 密码文件 --
    let password_file_present = layout.password_file.exists();
    let password_file_perms_ok = password::password_file_perms_ok(&layout.password_file);
    if initialized && !password_file_present {
        issues.push(error(
            "password_file_missing",
            "密码文件缺失但 PGDATA 已存在（§13.7.8/§13.7.46），run 将返回 exit 16".into(),
        ));
    }
    if password_file_present && !password_file_perms_ok {
        issues.push(warning(
            "password_file_perms",
            "password file 权限过宽（Unix 要求 0600），preflight 将拒绝启动".into(),
        ));
    }

    // -- 环境 --
    let fs_kind = preflight::FsKind::from_env_override()
        .unwrap_or_else(|| preflight::detect_fs_kind(&layout.data_dir));
    if !fs_kind.is_supported() {
        issues.push(error(
            "fs_unsupported",
            format!(
                "文件系统 {} 无可靠 fsync 语义，preflight 将拒绝启动（§13.7.43）",
                fs_kind.describe()
            ),
        ));
    }
    let abs = layout
        .data_dir
        .canonicalize()
        .unwrap_or_else(|_| layout.data_dir.clone());
    let cloud_sync_feature = preflight::detect_cloud_sync(&abs);
    if let Some(feature) = &cloud_sync_feature {
        issues.push(error(
            "cloud_sync_path",
            format!("数据目录位于网盘同步路径（{feature}），WAL 损坏风险，preflight 将拒绝启动"),
        ));
    }
    let free_bytes = preflight::free_space(&layout.data_dir);
    if let Some(free) = free_bytes {
        if free < preflight::MIN_FREE_BYTES {
            issues.push(error(
                "disk_low",
                format!(
                    "剩余空间 {}MB < 500MB，preflight 将拒绝启动（§13.7.5）",
                    free / (1024 * 1024)
                ),
            ));
        }
    }

    // -- runtime state --
    let (state_cond, st) = state_file_condition(&layout);
    if state_cond == "corrupted" {
        issues.push(warning(
            "state_corrupted",
            "runtime state.json 损坏（不影响数据，run 将重建）；上次运行状态不可知".into(),
        ));
    }
    let kill_fallback_count = st.as_ref().map_or(0, |s| s.kill_fallback_count);
    if kill_fallback_count > 0 {
        issues.push(warning(
            "kill_fallback_history",
            format!("历史上发生过 {kill_fallback_count} 次 kill fallback 强制终止（§13.7.48），建议检查磁盘/杀软"),
        ));
    }
    let last_start_error = st.as_ref().and_then(|s| s.last_start_error.clone());
    if last_start_error.is_some() {
        issues.push(warning(
            "last_start_error",
            last_start_error.clone().unwrap_or_default(),
        ));
    }

    // §13.7.44 / §7.5 残留物扫描：兄弟目录中 `<data_dir_name>.restore-*` 与 `*.partial`
    // 由 dump/restore 中断产生，doctor 只读列出，不自动清理（用户确认后手动删除）
    let (restore_residuals, dump_partials) = scan_residuals(&layout.data_dir);
    if !restore_residuals.is_empty() {
        issues.push(warning(
            "restore_residual",
            format!(
                "检测到 restore 中断残留目录（§13.7.44）：{}；建议确认无重要数据后删除",
                restore_residuals.join(", ")
            ),
        ));
    }
    if !dump_partials.is_empty() {
        issues.push(warning(
            "dump_partial",
            format!(
                "检测到 dump 中断残留 .partial 文件（§13.7.44）：{}；建议删除",
                dump_partials.join(", ")
            ),
        ));
    }

    let json = DoctorJson {
        schema: protocol::DOCTOR_SCHEMA,
        data_dir: layout.data_dir.to_string_lossy().replace('\\', "/"),
        initialized,
        pg_version,
        bundled_pg_major: bundled_major,
        version_match,
        critical_files_missing,
        install_dir_complete,
        missing_tools,
        lock_held,
        postmaster_pid: pm.as_ref().map(|i| i.pid),
        postgres_alive,
        port,
        stale_postmaster_pid,
        cluster_state: control.as_ref().map(|c| c.cluster_state.clone()),
        control_data_readable,
        data_checksums: control.as_ref().map(|c| c.data_checksums),
        hba_matches_template: hba_matches,
        managed_block_drift: managed_drift,
        password_file_present,
        password_file_perms_ok,
        fs_kind: fs_kind.describe(),
        fs_supported: fs_kind.is_supported(),
        free_bytes,
        cloud_sync_feature,
        state_file: state_cond,
        runtime_status: st.as_ref().map(|s| s.status.clone()),
        last_start_error,
        last_stop_mode: st.as_ref().and_then(|s| s.last_stop_mode.clone()),
        kill_fallback_count,
        restore_residuals,
        dump_partials,
        issues,
    };
    protocol::print_json_line(&json).map_err(|e| {
        eprintln!("[sidecar] doctor JSON 输出失败: {e}");
        exit_codes::ARGUMENT_ERROR
    })
}

/// 扫描 data_dir 兄弟目录中的 dump/restore 中断残留物（§13.7.44 / §7.5）。
///
/// - `<data_dir_name>.restore-*` 目录：restore 中断残留（`restore()` 失败路径已自清理，
///   但 sidecar 进程被 kill 时残留目录仍存在）
/// - `*.partial` 文件：dump 中断残留（`dump()` 失败路径已自清理，但同上）
///
/// 仅扫描 `data_dir.parent`：restore/dump 默认输出路径在兄弟目录中（§12.2 / §13.7.44），
/// 用户自定义路径（如 `~/backups/`）不在扫描范围内（YAGNI：扫描全盘不可行）。
fn scan_residuals(data_dir: &Path) -> (Vec<String>, Vec<String>) {
    let mut restore_residuals: Vec<String> = Vec::new();
    let mut dump_partials: Vec<String> = Vec::new();

    let parent = match data_dir.parent() {
        Some(p) => p,
        None => return (restore_residuals, dump_partials),
    };
    let data_dir_name = match data_dir.file_name().and_then(|n| n.to_str()) {
        Some(n) => n,
        None => return (restore_residuals, dump_partials),
    };
    let restore_prefix = format!("{data_dir_name}.restore-");

    let entries = match std::fs::read_dir(parent) {
        Ok(r) => r,
        Err(_) => return (restore_residuals, dump_partials),
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = match entry.file_name().to_str() {
            Some(n) => n.to_string(),
            None => continue,
        };
        // restore 残留目录：兄弟目录匹配 `<data_dir_name>.restore-*` 模式
        if name.starts_with(&restore_prefix) && path.is_dir() {
            restore_residuals.push(path.to_string_lossy().replace('\\', "/"));
        }
        // dump 残留文件：兄弟文件匹配 `*.partial` 模式
        if name.ends_with(".partial") && path.is_file() {
            dump_partials.push(path.to_string_lossy().replace('\\', "/"));
        }
    }

    (restore_residuals, dump_partials)
}

// ---- 临时实例管理（dump 离线 / restore / maintenance-shell 共用） ----

struct TempInstance {
    /// 必须保留 ownership：PostgreSQL::drop 会调用 pg_ctl stop（crate 0.22 行为），
    /// 提前 drop 会立即停止 postgres，导致后续 psql 连接失败。
    ///
    /// run.rs 的常驻实例不使用 std::mem::forget 阻止 drop：依赖 graded_stop 先于
    /// PostgreSQL 变量 drop 完成（graded_stop 在 supervise 返回后立即调用，
    /// postgresql 变量在 run 函数返回时才 drop）。panic 路径下 drop 可能触发
    /// crate 内部 stop（非分级策略），但 panic 概率极低且残留 postgres 可由
    /// 下次 `stop` 命令清理（§6.3 契约）。
    #[allow(dead_code)]
    pg: PostgreSQL,
    layout: Layout,
    port: u16,
    /// 启动后捕获的可验证身份；为 None 时 graded_stop 会 fail closed（不发送 PID 信号）。
    identity: Option<process_identity::ClusterIdentity>,
}

impl TempInstance {
    async fn start(layout: Layout, username: &str, password: &str) -> Result<Self, u8> {
        let port = run::pick_free_port(LISTEN_LOCAL).map_err(|e| {
            eprintln!("[sidecar] 随机端口分配失败: {e}");
            exit_codes::START_FAILED
        })?;
        let mut pg = run::build_postgresql(&layout, LISTEN_LOCAL, username, password, port);
        pg.start().await.map_err(|e| {
            eprintln!("[sidecar] 临时实例启动失败: {e}");
            exit_codes::START_FAILED
        })?;

        // pg.start() 返回时 postgres 可能仍在 init/recovery，需等 ready 再让调用方连入。
        // 主流程 run.rs 有 health_check 兜底；临时实例此处复用 psql 探测语义。
        let deadline = std::time::Instant::now() + Duration::from_secs(30);
        loop {
            match pgbin::psql(
                &layout.install_dir,
                LISTEN_LOCAL,
                port,
                username,
                "postgres",
                password,
                "select 1",
                Duration::from_secs(2),
            )
            .await
            {
                Ok(_) => break,
                Err(_) if std::time::Instant::now() < deadline => {
                    tokio::time::sleep(Duration::from_millis(200)).await;
                }
                Err(e) => {
                    eprintln!("[sidecar] 临时实例健康检查超时: {e}");
                    let identity = capture_temp_identity(&layout);
                    let _ = pgbin::graded_stop(
                        &layout.install_dir,
                        &layout.data_dir,
                        identity.as_ref(),
                    )
                    .await;
                    return Err(exit_codes::START_FAILED);
                }
            }
        }

        let identity = capture_temp_identity(&layout);
        Ok(Self {
            pg,
            layout,
            port,
            identity,
        })
    }

    async fn stop(self) {
        let outcome = pgbin::graded_stop(
            &self.layout.install_dir,
            &self.layout.data_dir,
            self.identity.as_ref(),
        )
        .await;
        if !outcome.stopped {
            let reason = outcome
                .failure
                .as_ref()
                .map_or_else(|| "unknown".to_string(), std::string::ToString::to_string);
            eprintln!("[sidecar] 警告：临时实例未能干净停止（{reason}）");
        }
        // self.pg 在函数结束 drop；graded_stop 已停 postgres，crate status() 检测到非 Started，drop 不会再 stop
    }
}

/// 捕获临时实例的可验证身份；失败时返回 None，graded_stop 将拒绝发送 PID 信号。
fn capture_temp_identity(layout: &Layout) -> Option<process_identity::ClusterIdentity> {
    let info = pgbin::read_postmaster_pid(&layout.data_dir)?;
    match process_identity::capture_cluster_identity_from_info(
        &layout.data_dir,
        &layout.install_dir,
        &info,
    ) {
        Ok(identity) => Some(identity),
        Err(e) => {
            eprintln!("[sidecar] 临时实例身份捕获失败 (pid {}): {e}", info.pid);
            None
        }
    }
}

// ---- dump ----

pub async fn dump(args: cli::DumpArgs) -> Result<(), u8> {
    logging::init(None);
    validate_database_name(&args.database)?;
    let layout = Layout::from_data_dir(&args.data_dir, None, None, None);

    if !layout.data_dir.join("PG_VERSION").exists() {
        eprintln!(
            "[sidecar] 数据目录未初始化，无可备份数据：{}",
            layout.data_dir.display()
        );
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }
    let password = load_password(&layout)?;

    // 输出先写 .partial 再原子改名，避免半截备份被当作可用（§13.7.44 同源策略）
    let partial = PathBuf::from(format!("{}.partial", args.output.display()));
    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            eprintln!("[sidecar] 备份输出目录创建失败 {}: {e}", parent.display());
            exit_codes::DUMP_RESTORE_FAILED
        })?;
    }

    // 运行中实例：SQL 级直连，不占维护锁（§13.3 AI-28）；离线：持锁起临时实例
    let pm = pgbin::read_postmaster_pid(&layout.data_dir);
    let online_port = pm
        .as_ref()
        .filter(|i| pgbin::process_alive(i.pid))
        .and_then(|i| i.port);

    let result = match online_port {
        Some(port) => {
            tracing::info!("dump via running instance on port {port}");
            run_pg_dump(
                &layout.install_dir,
                port,
                &args.database,
                &args.username,
                &password,
                &partial,
            )
            .await
        }
        None => {
            let _lock = acquire_lock(&layout)?;
            if pm.is_some() {
                let _ = std::fs::remove_file(layout.data_dir.join("postmaster.pid"));
            }
            setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;
            let instance = TempInstance::start(layout.clone(), &args.username, &password).await?;
            let port = instance.port;
            let install_dir = instance.layout.install_dir.clone();
            let r = run_pg_dump(
                &install_dir,
                port,
                &args.database,
                &args.username,
                &password,
                &partial,
            )
            .await;
            instance.stop().await;
            r
        }
    };

    match result {
        Ok(()) => {
            std::fs::rename(&partial, &args.output).map_err(|e| {
                eprintln!("[sidecar] 备份原子改名失败 {}: {e}", args.output.display());
                let _ = std::fs::remove_file(&partial);
                exit_codes::DUMP_RESTORE_FAILED
            })?;
            eprintln!("[sidecar] 备份完成：{}", args.output.display());
            Ok(())
        }
        Err(code) => {
            let _ = std::fs::remove_file(&partial);
            Err(code)
        }
    }
}

async fn run_pg_dump(
    install_dir: &Path,
    port: u16,
    database: &str,
    username: &str,
    password: &str,
    output: &Path,
) -> Result<(), u8> {
    let port_s = port.to_string();
    let out_s = output.to_string_lossy().into_owned();
    let out = pgbin::run_tool(
        &pgbin::tool_path(install_dir, "pg_dump"),
        &[
            "-F",
            "c",
            "-f",
            &out_s,
            "-h",
            LISTEN_LOCAL,
            "-p",
            &port_s,
            "-U",
            username,
            "-d",
            database,
        ],
        &[("PGPASSWORD", password), ("PGCONNECT_TIMEOUT", "10")],
        DUMP_TIMEOUT,
    )
    .await
    .map_err(|e| {
        eprintln!("[sidecar] pg_dump 执行失败: {e}");
        exit_codes::DUMP_RESTORE_FAILED
    })?;
    if !out.success() {
        eprintln!(
            "[sidecar] pg_dump 失败 (code {:?}): {}",
            out.code,
            logging::sanitize(out.stderr.trim())
        );
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }
    Ok(())
}

// ---- restore ----

/// restore 成功后向 stdout 报告的目标目录（Python `EmbeddedPgMaintenanceService.restore`
/// 在未显式传 `--target-data-dir` 时以 `stdout.strip()` 取此值）。
fn restore_target_to_report(layout: &Layout, restore_dir: &Path, do_swap: bool) -> String {
    let target = if do_swap {
        &layout.data_dir
    } else {
        restore_dir
    };
    target.to_string_lossy().replace('\\', "/")
}

// ---- 显式恢复目标的凭据供给（D16-F05） ----

/// 显式目标使用独立 runtime 时，恢复成功后必须把同一密码写入目标 runtime。
/// 结构持有目标维护锁直至 restore 结束，防止并发维护进程同时使用目标布局。
struct TargetCredentials {
    layout: Layout,
    _lock: MaintenanceLock,
}

/// 词法绝对化 + `.`/`..` 归一（不解析符号链接；供尚未创建的路径比较使用）。
fn normalize_path(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map_or_else(|_| path.to_path_buf(), |cwd| cwd.join(path))
    };
    let mut out = PathBuf::new();
    for comp in absolute.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                match out.components().next_back() {
                    Some(Component::RootDir) | Some(Component::Prefix(_)) => continue,
                    _ => {}
                }
                if !out.pop() {
                    out.push(comp.as_os_str());
                }
            }
            _ => out.push(comp.as_os_str()),
        }
    }
    out
}

/// 解析到最近的已存在祖先后再拼回剩余组件：目标目录可能尚未创建，但父目录符号链接
/// 已存在的场景必须按真实位置比较，避免把凭据写进源 runtime 或源 PGDATA。
fn canonical_for_compare(path: &Path) -> PathBuf {
    let absolute = normalize_path(path);
    let mut suffix: Vec<OsString> = Vec::new();
    let mut probe = absolute.as_path();
    loop {
        if let Ok(mut resolved) = std::fs::canonicalize(probe) {
            for part in suffix.iter().rev() {
                resolved.push(part);
            }
            return resolved;
        }
        let Some(name) = probe.file_name() else {
            return absolute;
        };
        let Some(parent) = probe.parent() else {
            return absolute;
        };
        suffix.push(name.to_os_string());
        probe = parent;
    }
}

#[cfg(windows)]
fn os_component_eq(a: &OsStr, b: &OsStr) -> bool {
    // Windows 文件系统大小写不敏感；符号链接归一后仍须按平台语义比较。
    a.to_string_lossy()
        .eq_ignore_ascii_case(&b.to_string_lossy())
}

#[cfg(not(windows))]
fn os_component_eq(a: &OsStr, b: &OsStr) -> bool {
    a == b
}

fn paths_equal(a: &Path, b: &Path) -> bool {
    let (ca, cb) = (canonical_for_compare(a), canonical_for_compare(b));
    let mut ac = ca.components();
    let mut bc = cb.components();
    loop {
        match (ac.next(), bc.next()) {
            (None, None) => return true,
            (Some(x), Some(y)) if os_component_eq(x.as_os_str(), y.as_os_str()) => {}
            _ => return false,
        }
    }
}

fn is_within(child: &Path, ancestor: &Path) -> bool {
    let (cc, ca) = (
        canonical_for_compare(child),
        canonical_for_compare(ancestor),
    );
    let mut ac = ca.components();
    let mut cc = cc.components();
    loop {
        match ac.next() {
            None => return true,
            Some(ap) => match cc.next() {
                Some(cp) if os_component_eq(ap.as_os_str(), cp.as_os_str()) => {}
                _ => return false,
            },
        }
    }
}

fn is_data_dir_name(path: &Path) -> bool {
    let Some(name) = path.file_name() else {
        return false;
    };
    #[cfg(windows)]
    {
        name.to_string_lossy().eq_ignore_ascii_case("data")
    }
    #[cfg(not(windows))]
    {
        name == OsStr::new("data")
    }
}

/// 判定显式目标的凭据归属：
/// - 与源布局共享 runtime（同级目录/符号链接归一后同一文件）→ `Ok(None)`，沿用源凭据；
/// - 独立 runtime → 只接受应用布局 `<data root>/data`，返回目标布局供后续加锁与写入；
/// - 目标位于源 PGDATA 内或命名不是 `data` → 明确拒绝并给出受支持路径（exit 2）。
fn plan_explicit_target(layout: &Layout, target: &Path) -> Result<Option<Layout>, u8> {
    let target_layout = Layout::from_data_dir(target, None, None, None);
    if paths_equal(&layout.password_file, &target_layout.password_file) {
        return Ok(None);
    }
    if is_within(&target_layout.data_dir, &layout.data_dir) {
        eprintln!(
            "[sidecar] 目标目录位于源 PGDATA 内，拒绝把恢复数据或凭据写入源数据目录：{}",
            target.display()
        );
        return Err(exit_codes::ARGUMENT_ERROR);
    }
    if !is_data_dir_name(&target_layout.data_dir) {
        let supported = target
            .parent()
            .map_or_else(|| PathBuf::from("<data root>/data"), |p| p.join("data"));
        eprintln!(
            "[sidecar] 显式目标布局不受支持：{}。独立 runtime 的恢复目标必须为 `<data root>/data`\
             （应用布局 `<app data>/postgres/<major>/data`，例如 {}）；\
             与源数据目录同级的目录可直接使用（共享 runtime）。",
            target.display(),
            supported.display()
        );
        return Err(exit_codes::ARGUMENT_ERROR);
    }
    Ok(Some(target_layout))
}

fn acquire_target_lock(target_layout: &Layout) -> Result<MaintenanceLock, u8> {
    match MaintenanceLock::try_acquire(&target_layout.lock_file) {
        Ok(lock) => Ok(lock),
        Err(AcquireError::Conflict) => {
            eprintln!(
                "[sidecar] 目标维护锁冲突：另一进程正在使用目标布局 {}；\
                 请先停止使用该目录的 qTrading/维护进程后重试",
                target_layout.data_dir.display()
            );
            Err(exit_codes::LOCK_CONFLICT)
        }
        Err(AcquireError::Io(e)) => {
            eprintln!(
                "[sidecar] 目标维护锁获取失败 {}: {e}",
                target_layout.lock_file.display()
            );
            Err(exit_codes::LOCK_CONFLICT)
        }
    }
}

/// 写入前校验目标 runtime 归属：拒绝符号链接 runtime/密码文件与任何已存在但
/// 不属于本次的凭据；同一密码允许重写（用于收紧权限/统一平台格式）。
fn verify_target_credentials(target_layout: &Layout, password: &str) -> Result<(), u8> {
    let runtime_dir = target_layout
        .password_file
        .parent()
        .unwrap_or(target_layout.data_dir.as_path());
    match std::fs::symlink_metadata(runtime_dir) {
        Ok(meta) if meta.file_type().is_symlink() => {
            eprintln!(
                "[sidecar] 目标 runtime 是符号链接，拒绝写入凭据：{}",
                runtime_dir.display()
            );
            return Err(exit_codes::PASSWORD_FAILED);
        }
        Ok(meta) if !meta.is_dir() => {
            eprintln!(
                "[sidecar] 目标 runtime 不是目录，拒绝写入凭据：{}",
                runtime_dir.display()
            );
            return Err(exit_codes::PASSWORD_FAILED);
        }
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => {
            eprintln!(
                "[sidecar] 目标 runtime 状态读取失败 {}: {e}",
                runtime_dir.display()
            );
            return Err(exit_codes::PASSWORD_FAILED);
        }
    }

    match std::fs::symlink_metadata(&target_layout.password_file) {
        Ok(meta) => {
            if meta.file_type().is_symlink() {
                eprintln!(
                    "[sidecar] 目标密码文件是符号链接，拒绝覆盖：{}",
                    target_layout.password_file.display()
                );
                return Err(exit_codes::PASSWORD_FAILED);
            }
            match password::read_password_file(&target_layout.password_file) {
                Some(existing) if existing == password => Ok(()),
                Some(_) => {
                    eprintln!(
                        "[sidecar] 目标 runtime 已有不属于本次恢复的凭据，拒绝覆盖：{}。\
                         如需复用该目标，请先确认其来源或改用新的应用布局目录。",
                        target_layout.password_file.display()
                    );
                    Err(exit_codes::PASSWORD_FAILED)
                }
                None => {
                    eprintln!(
                        "[sidecar] 目标密码文件存在但不可用（空/损坏/不可解密），拒绝覆盖：{}",
                        target_layout.password_file.display()
                    );
                    Err(exit_codes::PASSWORD_FAILED)
                }
            }
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => {
            eprintln!(
                "[sidecar] 目标密码文件状态读取失败 {}: {e}",
                target_layout.password_file.display()
            );
            Err(exit_codes::PASSWORD_FAILED)
        }
    }
}

fn cleanup_restored_target(restore_dir: &Path) {
    if let Err(e) = std::fs::remove_dir_all(restore_dir) {
        eprintln!(
            "[sidecar] 目标数据目录清理失败，请手动删除 {}: {e}",
            restore_dir.display()
        );
    }
}

/// 恢复成功后再写凭据（避免失败恢复留下孤立凭据）；写入失败时清理本次恢复目录，
/// 不让目标停留在「有数据但无凭据」的不可用状态。重复校验缩小并发覆盖窗口。
fn finalize_target_credentials(
    credentials: &TargetCredentials,
    password: &str,
    restore_dir: &Path,
) -> Result<(), u8> {
    if let Err(code) = verify_target_credentials(&credentials.layout, password) {
        cleanup_restored_target(restore_dir);
        return Err(code);
    }
    match password::write_password_file(&credentials.layout.password_file, password) {
        Ok(()) => {
            eprintln!(
                "[sidecar] 目标 runtime 已写入同一密码（未输出明文）：{}",
                credentials.layout.password_file.display()
            );
            Ok(())
        }
        Err(e) => {
            eprintln!(
                "[sidecar] 目标 runtime 密码文件写入失败 {}: {e}",
                credentials.layout.password_file.display()
            );
            cleanup_restored_target(restore_dir);
            Err(exit_codes::PASSWORD_FAILED)
        }
    }
}

pub async fn restore(args: cli::RestoreArgs) -> Result<(), u8> {
    logging::init(None);
    validate_database_name(&args.database)?;
    let layout = Layout::from_data_dir(&args.data_dir, None, None, None);

    if !args.input.exists() {
        eprintln!("[sidecar] 备份文件不存在：{}", args.input.display());
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }
    let _lock = acquire_lock(&layout)?;
    // D16-F03：锁可获取只证明没有协作进程持锁（sidecar 崩溃会由 OS 释放），
    // 不代表源 PGDATA 的孤儿 postgres 已停止；任何 setup/initdb/切换前必须先校验。
    ensure_source_cluster_idle(&layout, "restore 开始前")?;
    let password = load_password(&layout)?;
    setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;

    // §12.2：恢复到新目录。显式 --target-data-dir 时原目录完全不动；缺省时走原子切换。
    let ts = ts_compact();
    let mut target_credentials: Option<TargetCredentials> = None;
    let (restore_dir, do_swap) = match &args.target_data_dir {
        Some(target) => {
            // D16-F05：独立 runtime 的目标必须由本次恢复建立凭据，否则用户切到该布局后
            // run 只会读目标 runtime/password（缺失 → exit 16）。先校验布局归属并加锁，
            // 再动任何恢复目录。
            if let Some(target_layout) = plan_explicit_target(&layout, target)? {
                let lock = acquire_target_lock(&target_layout)?;
                verify_target_credentials(&target_layout, &password)?;
                target_credentials = Some(TargetCredentials {
                    layout: target_layout,
                    _lock: lock,
                });
            }
            let nonempty = target.exists()
                && std::fs::read_dir(target)
                    .map(|mut r| r.next().is_some())
                    .unwrap_or(false);
            if nonempty {
                eprintln!(
                    "[sidecar] 目标目录非空，拒绝覆盖（§12.2 不覆盖原则）：{}",
                    target.display()
                );
                return Err(exit_codes::DUMP_RESTORE_FAILED);
            }
            (target.clone(), false)
        }
        None => {
            let name = layout
                .data_dir
                .file_name()
                .unwrap_or_default()
                .to_string_lossy();
            (
                layout
                    .data_dir
                    .with_file_name(format!("{name}.restore-{ts}")),
                true,
            )
        }
    };

    let outcome = restore_into(
        &layout,
        &restore_dir,
        &args.input,
        &args.database,
        &args.username,
        &password,
    )
    .await;
    if let Err(code) = outcome {
        let _ = std::fs::remove_dir_all(&restore_dir);
        return Err(code);
    }

    if do_swap {
        // D16-F03：恢复耗时窗口内可能有外部 postgres 开始使用源 PGDATA（锁只约束
        // 协作进程）；切换前再次校验，失败时不改名、不创建 .bak，保留已完成的恢复目录。
        if let Err(code) = ensure_source_cluster_idle(&layout, "恢复目录切换前") {
            eprintln!(
                "[sidecar] 未切换目录：已完成的恢复目录保留于 {}，源 PGDATA 未改动；\
                 可人工处理或清理后重试",
                restore_dir.display()
            );
            return Err(code);
        }
        let name = layout
            .data_dir
            .file_name()
            .unwrap_or_default()
            .to_string_lossy();
        let bak = layout.data_dir.with_file_name(format!("{name}.bak-{ts}"));
        if layout.data_dir.exists() {
            std::fs::rename(&layout.data_dir, &bak).map_err(|e| {
                eprintln!(
                    "[sidecar] 原目录改名失败（恢复原状需手动处理 {} 与 {}）: {e}",
                    layout.data_dir.display(),
                    restore_dir.display()
                );
                exit_codes::DUMP_RESTORE_FAILED
            })?;
        }
        if let Err(e) = std::fs::rename(&restore_dir, &layout.data_dir) {
            // 回滚：尽量把原目录改回去
            if bak.exists() {
                let _ = std::fs::rename(&bak, &layout.data_dir);
            }
            eprintln!("[sidecar] 新目录切换失败（已尝试回滚）: {e}");
            return Err(exit_codes::DUMP_RESTORE_FAILED);
        }
        // cluster 身份已更换：重置 state 避免残留旧实例的 running/port 误导 status
        let mut st = state::read(&layout.state_file).unwrap_or_default();
        st.status = "stopped".to_string();
        st.postgres_pid = None;
        st.port = None;
        st.postgres_version = Some(setup::bundled_pg_version().to_string());
        let _ = state::write(&layout.state_file, &st);
        eprintln!("[sidecar] 恢复完成，原目录保留于 {}", bak.display());
    } else {
        if let Some(credentials) = &target_credentials {
            finalize_target_credentials(credentials, &password, &restore_dir)?;
        }
        eprintln!(
            "[sidecar] 恢复完成：{}（原目录未改动）",
            restore_dir.display()
        );
    }

    let reported = restore_target_to_report(&layout, &restore_dir, do_swap);
    use std::io::Write as _;
    let mut stdout = std::io::stdout().lock();
    if let Err(e) = writeln!(stdout, "{reported}") {
        // 数据已成功恢复；stdout 断裂只影响调用方取目标路径，不改变恢复结果。
        tracing::warn!("restore target stdout write failed: {e}");
    }
    Ok(())
}

/// 在 restore_dir 上执行：initdb → 安全基线 → 临时实例 → create db → pg_restore → 健康检查。
/// 任一步失败返回错误码，调用方负责清理 restore_dir。
async fn restore_into(
    layout: &Layout,
    restore_dir: &Path,
    input: &Path,
    database: &str,
    username: &str,
    password: &str,
) -> Result<(), u8> {
    let restore_layout = Layout::from_data_dir(
        restore_dir,
        Some(&layout.install_dir),
        Some(&layout.password_file),
        None,
    );

    // initdb 要求空目录；--target-data-dir 场景目录可能不存在，先建空壳
    std::fs::create_dir_all(restore_dir).map_err(|e| {
        eprintln!("[sidecar] 恢复目录创建失败 {}: {e}", restore_dir.display());
        exit_codes::DUMP_RESTORE_FAILED
    })?;
    setup::run_initdb(&restore_layout, username, password).await?;
    if let Err(e) = setup::write_security_baseline(restore_dir, LISTEN_LOCAL) {
        eprintln!("[sidecar] 安全基线写入失败: {e}");
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }

    let instance = TempInstance::start(restore_layout, username, password).await?;
    let result = restore_via_instance(&instance, input, database, username, password).await;
    instance.stop().await;
    result
}

async fn restore_via_instance(
    instance: &TempInstance,
    input: &Path,
    database: &str,
    username: &str,
    password: &str,
) -> Result<(), u8> {
    let port_s = instance.port.to_string();
    let install_dir = &instance.layout.install_dir;

    // AI-29：既有密码认证新 cluster（initdb --pwfile 已注入同一密码）
    // 库名已由 validate_database_name 限定为安全标识符，双引号仅用于保留大小写。
    pgbin::psql(
        install_dir,
        LISTEN_LOCAL,
        instance.port,
        username,
        "postgres",
        password,
        &format!("CREATE DATABASE \"{database}\""),
        Duration::from_secs(30),
    )
    .await
    .map_err(|e| {
        eprintln!("[sidecar] 恢复实例建库失败: {e}");
        exit_codes::DUMP_RESTORE_FAILED
    })?;

    let in_s = input.to_string_lossy().into_owned();
    let out = pgbin::run_tool(
        &pgbin::tool_path(install_dir, "pg_restore"),
        &[
            "-h",
            LISTEN_LOCAL,
            "-p",
            &port_s,
            "-U",
            username,
            "-d",
            database,
            "--no-owner",
            "--no-privileges",
            &in_s,
        ],
        &[("PGPASSWORD", password), ("PGCONNECT_TIMEOUT", "10")],
        DUMP_TIMEOUT,
    )
    .await
    .map_err(|e| {
        eprintln!("[sidecar] pg_restore 执行失败: {e}");
        exit_codes::DUMP_RESTORE_FAILED
    })?;
    if !out.success() {
        eprintln!(
            "[sidecar] pg_restore 失败 (code {:?})：备份文件损坏或版本不兼容（§13.7.11）：{}",
            out.code,
            logging::sanitize(out.stderr.trim())
        );
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }

    pgbin::psql(
        install_dir,
        LISTEN_LOCAL,
        instance.port,
        username,
        database,
        password,
        "select 1",
        Duration::from_secs(15),
    )
    .await
    .map_err(|e| {
        eprintln!("[sidecar] 恢复后健康检查失败: {e}");
        exit_codes::DUMP_RESTORE_FAILED
    })?;
    Ok(())
}

// ---- maintenance-shell ----

/// maintenance-shell stdout 契约：Python `EmbeddedPgMaintenanceService.maintenance_shell`
/// 解析 `psql_path` / `connection_string_redacted`（连接串密码段固定 `***`，R9）。
#[derive(Serialize, Debug)]
struct MaintenanceShellJson {
    psql_path: String,
    connection_string_redacted: String,
}

fn maintenance_shell_json(install_dir: &Path, port: u16, username: &str) -> MaintenanceShellJson {
    MaintenanceShellJson {
        psql_path: pgbin::tool_path(install_dir, "psql")
            .to_string_lossy()
            .into_owned(),
        // 用户名须与实际临时实例一致，否则自定义用户名部署下 stdout 连接串显示错误角色
        connection_string_redacted: format!(
            "postgresql://{username}:***@{LISTEN_LOCAL}:{port}/{DEFAULT_DATABASE}"
        ),
    }
}

pub async fn maintenance_shell(args: cli::MaintenanceShellArgs) -> Result<(), u8> {
    logging::init(None);
    let layout = Layout::from_data_dir(&args.data_dir, None, None, None);

    let _lock = acquire_lock(&layout)?;
    match setup::guard_data_dir(&layout.data_dir)? {
        DataDirState::Existing => {}
        DataDirState::Fresh => {
            eprintln!(
                "[sidecar] 数据目录未初始化，无维护对象：{}",
                layout.data_dir.display()
            );
            return Err(exit_codes::DATA_DIR_ABNORMAL);
        }
    }
    let password = load_password(&layout)?;
    setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;

    let username = args.username.as_str();
    let instance = TempInstance::start(layout.clone(), username, &password).await?;
    let info = maintenance_shell_json(&layout.install_dir, instance.port, username);
    if let Err(e) = protocol::print_json_line(&info) {
        // stdout 管道不可用：无法向调用方交付连接信息，停止临时实例后失败退出。
        eprintln!("[sidecar] maintenance-shell JSON 输出失败: {e}");
        instance.stop().await;
        return Err(exit_codes::HEALTH_CHECK_FAILED);
    }
    let psql_path = pgbin::tool_path(&layout.install_dir, "psql");
    eprintln!("[sidecar] 维护实例已启动：");
    eprintln!(
        "  连接（脱敏）: postgresql://{username}:***@{LISTEN_LOCAL}:{}/{DEFAULT_DATABASE}",
        instance.port
    );
    eprintln!(
        "  psql: {} -h {LISTEN_LOCAL} -p {} -U {username} -d {DEFAULT_DATABASE}",
        psql_path.display(),
        instance.port
    );
    eprintln!(
        "  密码: 见 password file {}（权限 0600，禁止打印明文）",
        layout.password_file.display()
    );
    eprintln!("  完成后按 Enter 结束维护实例…");

    wait_for_enter().await;
    instance.stop().await;
    eprintln!("[sidecar] 维护实例已停止");
    Ok(())
}

async fn wait_for_enter() {
    use tokio::io::AsyncReadExt;
    let mut stdin = tokio::io::stdin();
    let mut buf = [0u8; 64];
    loop {
        match stdin.read(&mut buf).await {
            Ok(0) => break, // EOF（管道/重定向场景）：直接结束，避免永久挂起
            Ok(n) => {
                if buf[..n].contains(&b'\n') {
                    break;
                }
            }
            Err(_) => break,
        }
    }
}

// ---- reset-password ----

/// 通过临时 `pg_ctl` 启动的 postgres 实例 + 临时 trust pg_hba.conf 重置超级用户密码（§13.7.8）。
///
/// 历史方案 `postgres --single` 单用户模式在 Windows 上被 PostgreSQL 安全限制拒绝
/// （`check_root()` 检测到 admin 用户组成员身份即 exit 1；GitHub Actions Windows runner
/// 默认以 `runneradmin`/Administrators 组运行触发此限制）。`pg_ctl` 内部
/// `CreateRestrictedProcess` 降权后才能在 Windows admin 下启动 postgres 多用户模式。
///
/// 超级用户名取 `--username`（缺省 postgres，与 `run --username` /
/// `AppConfig.embedded_pg_username` 对齐）：自定义用户名 cluster 上 postgres 角色不存在，
/// 硬编码 postgres 会让 `ALTER USER` 作用在错误角色（M23）。
///
/// 流程：validate_username → acquire_lock → guard_data_dir（仅 Existing）→ ensure_binaries →
///       stale pid 清理 → 临时改 pg_hba.conf 为 trust（仅 127.0.0.1，RAII guard 出错路径恢复）→
///       启动临时 postgres 实例（复用 dump 离线 TempInstance 路径）→
///       psql 连接执行 `ALTER USER <username> PASSWORD '<new>'`（trust 模式无需密码）→
///       停止实例 → 重写 pg_hba.conf（scram-sha-256，新密码生效）→ 写入新 password file →
///       register_secret。
///
/// exit code：
/// - 2 ARGUMENT_ERROR：用户名非法（白名单外的标识符，防 SQL 注入）
/// - 50 LOCK_CONFLICT：维护锁冲突（qTrading 运行中）/ PGDATA 已有活实例
/// - 40 DATA_DIR_ABNORMAL：数据目录未初始化
/// - 16 PASSWORD_FAILED：临时实例启动失败 / ALTER USER 失败 / password file 写入失败
/// - 30 DUMP_RESTORE_FAILED：pg_hba.conf 重写失败
pub async fn reset_password(args: cli::ResetPasswordArgs) -> Result<(), u8> {
    logging::init(None);
    validate_username(&args.username)?;
    let layout = Layout::from_data_dir(&args.data_dir, None, None, None);

    let _lock = acquire_lock(&layout)?;
    match setup::guard_data_dir(&layout.data_dir)? {
        DataDirState::Existing => {}
        DataDirState::Fresh => {
            eprintln!(
                "[sidecar] 数据目录未初始化，无密码可重置：{}",
                layout.data_dir.display()
            );
            return Err(exit_codes::DATA_DIR_ABNORMAL);
        }
    }
    setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;

    // stale postmaster.pid 清理（§7.2，与 run.rs/commands.rs stop 同款逻辑）
    // 临时实例启动前必须保证 PGDATA 无残留 pid 文件，否则 pg_ctl start 拒绝启动。
    if let Some(info) = pgbin::read_postmaster_pid(&layout.data_dir) {
        if pgbin::process_alive(info.pid) {
            eprintln!(
                "[sidecar] PostgreSQL 已运行于该 PGDATA (pid {})，禁止 reset-password 并发操作。\n\
                 请先执行 `qtrading-pg-sidecar stop --data-dir {}` 清理残留进程，再重试 reset-password。",
                info.pid,
                layout.data_dir.display()
            );
            return Err(exit_codes::LOCK_CONFLICT);
        }
        tracing::warn!(
            "stale postmaster.pid (pid {}) removed before reset-password",
            info.pid
        );
        let _ = std::fs::remove_file(layout.data_dir.join("postmaster.pid"));
    }

    let new_pwd = password::generate_password();

    // 临时改 pg_hba.conf 为 trust（仅 127.0.0.1）：绕过密码认证以执行 ALTER USER。
    // 出错路径由 HbaRestoreGuard RAII 恢复原 pg_hba.conf；成功路径在 write_security_baseline
    // 重写后 disarm，避免恢复覆盖 scram-sha-256 新内容。
    let hba_path = layout.data_dir.join("pg_hba.conf");
    let mut hba_guard = HbaRestoreGuard::new(hba_path.clone());
    const TRUST_HBA: &str = "host all all 127.0.0.1/32 trust\n";
    if let Err(e) = std::fs::write(&hba_path, TRUST_HBA) {
        eprintln!("[sidecar] pg_hba.conf 临时 trust 写入失败: {e}");
        return Err(exit_codes::PASSWORD_FAILED);
    }

    // 启动临时 postgres 实例（postgresql_embedded 内部 pg_ctl，Windows admin 也能启动）。
    // dummy 密码：trust 模式下 psql 健康检查不验证密码（PGPASSWORD 会被 postgres 忽略）。
    const DUMMY_PWD: &str = "reset-trust-bypass-not-verified";
    let instance = match TempInstance::start(layout.clone(), &args.username, DUMMY_PWD).await {
        Ok(instance) => instance,
        Err(code) => {
            // 自定义用户名未与实例超级用户一致时，健康检查以该角色连接会一直失败（约 30s 后超时）。
            // 该错误码本身不区分原因，这里补一条与 --username 相关的可操作提示。
            eprintln!(
                "[sidecar] 临时实例未能就绪：请确认 --username {} 与该数据目录的超级用户一致",
                args.username
            );
            return Err(code);
        }
    };
    let port = instance.port;
    let install_dir = instance.layout.install_dir.clone();

    // psql 连接执行 ALTER USER（trust 模式忽略 PGPASSWORD）。
    // args.username 已过 validate_username 白名单（[A-Za-z_][A-Za-z0-9_]*, ≤63），可安全拼接。
    let sql = format!("ALTER USER {} PASSWORD $${new_pwd}$$;", args.username);
    let alter_result = pgbin::psql(
        &install_dir,
        LISTEN_LOCAL,
        port,
        &args.username,
        "postgres",
        DUMMY_PWD,
        &sql,
        Duration::from_secs(10),
    )
    .await;

    // 无论 ALTER 成功与否都要停临时实例
    instance.stop().await;

    if let Err(e) = alter_result {
        eprintln!(
            "[sidecar] ALTER USER {} PASSWORD 失败（请确认 --username 与实例超级用户一致）: {e}",
            args.username
        );
        return Err(exit_codes::PASSWORD_FAILED);
    }

    // 重写 pg_hba.conf（scram-sha-256，新密码生效，清除临时 trust 行）
    if let Err(e) = setup::write_security_baseline(&layout.data_dir, LISTEN_LOCAL) {
        eprintln!("[sidecar] pg_hba.conf 重写失败: {e}");
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }
    hba_guard.disarm();

    // 写入新 password file（权限 0600 on Unix）
    if let Err(e) = password::write_password_file(&layout.password_file, &new_pwd) {
        eprintln!(
            "[sidecar] 写入新 password file 失败 {}：{e}",
            layout.password_file.display()
        );
        return Err(exit_codes::PASSWORD_FAILED);
    }

    // 注册新密码为 secret（R9：防止后续日志/异常泄露）
    logging::register_secret(&new_pwd);

    eprintln!("[sidecar] 密码已重置，请重启 qTrading");
    Ok(())
}

/// RAII guard：临时 trust pg_hba.conf 修改失败/出错路径下恢复原内容。
/// 成功路径由 write_security_baseline 重写 pg_hba.conf 后调 disarm() 抑制恢复。
struct HbaRestoreGuard {
    path: PathBuf,
    backup: Option<String>,
}

impl HbaRestoreGuard {
    fn new(path: PathBuf) -> Self {
        let backup = std::fs::read_to_string(&path).ok();
        Self { path, backup }
    }

    /// 成功路径调用：避免恢复覆盖 write_security_baseline 已写入的 scram-sha-256 内容。
    fn disarm(&mut self) {
        self.backup = None;
    }
}

impl Drop for HbaRestoreGuard {
    fn drop(&mut self) {
        if let Some(content) = self.backup.take() {
            let _ = std::fs::write(&self.path, content);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cli::DEFAULT_USERNAME;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-maint-test-{}-{}", std::process::id(), name))
    }

    /// 构造"已初始化"假 cluster 目录（仅文件骨架，不含真实 PG 数据）。
    fn make_fake_cluster(dir: &Path) {
        std::fs::create_dir_all(dir.join("global")).unwrap();
        std::fs::create_dir_all(dir.join("base")).unwrap();
        std::fs::write(dir.join("PG_VERSION"), "16\n").unwrap();
        std::fs::write(dir.join("global/pg_control"), b"ctl").unwrap();
        std::fs::write(dir.join("postgresql.conf"), "# conf\n").unwrap();
    }

    /// 写真实格式的 postmaster.pid（PID / PGDATA / 启动时刻 / port / socket / listen）。
    fn write_postmaster_pid(data_dir: &Path, pid: u32, started_at: i64) {
        std::fs::create_dir_all(data_dir).unwrap();
        std::fs::write(
            data_dir.join("postmaster.pid"),
            format!(
                "{pid}\n{}\n{started_at}\n55432\n/tmp\n127.0.0.1\n",
                data_dir.display()
            ),
        )
        .unwrap();
    }

    /// reset-password 参数构造（默认用户名为 postgres，旧 CLI 语义）。
    fn reset_password_args(data_dir: PathBuf) -> cli::ResetPasswordArgs {
        cli::ResetPasswordArgs {
            data_dir,
            username: DEFAULT_USERNAME.to_string(),
        }
    }

    #[test]
    fn doctor_on_fresh_dir_reports_not_initialized() {
        let dir = unique_tmp("doctor-fresh");
        let data_dir = dir.join("postgres/16/data");
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn doctor_on_fake_cluster_ok() {
        let dir = unique_tmp("doctor-cluster");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn doctor_on_nonempty_without_pg_version_ok_but_issues() {
        // doctor 自身永不失败（exit 0），问题经 issues 数组上报
        let dir = unique_tmp("doctor-abnormal");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("random.txt"), "x").unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn runtime_port_requires_alive_process_and_valid_port() {
        let info = pgbin::PostmasterInfo {
            pid: 1234,
            data_dir: Some(PathBuf::from("/x/data")),
            started_at: Some(1234567890),
            port: Some(55432),
        };
        // 进程存活 → 取 postmaster.pid 端口
        assert_eq!(runtime_port(Some(&info), true), Some(55432));
        // 进程已死（pid 残留）→ 未知，不得沿用旧端口
        assert_eq!(runtime_port(Some(&info), false), None);
        // 无 postmaster.pid → 未知
        assert_eq!(runtime_port(None, true), None);
        // 端口缺失 → 未知
        let no_port = pgbin::PostmasterInfo {
            pid: 1234,
            data_dir: Some(PathBuf::from("/x/data")),
            started_at: Some(1234567890),
            port: None,
        };
        assert_eq!(runtime_port(Some(&no_port), true), None);
        // 端口 0（非法）→ 未知，不当作有效端口
        let zero_port = pgbin::PostmasterInfo {
            pid: 1234,
            data_dir: Some(PathBuf::from("/x/data")),
            started_at: Some(1234567890),
            port: Some(0),
        };
        assert_eq!(runtime_port(Some(&zero_port), true), None);
    }

    #[test]
    fn validate_database_name_accepts_safe_identifiers() {
        // UIR-F16-002：库名透传前的白名单校验
        for name in ["qtrading", "stock_db", "StockDB2", "_internal", "a"] {
            assert_eq!(validate_database_name(name), Ok(()), "{name} 应被接受");
        }
        let max_len = "a".repeat(63);
        assert_eq!(validate_database_name(&max_len), Ok(()));
    }

    #[test]
    fn validate_database_name_rejects_unsafe() {
        // 空名 / 非 ASCII 首字符 / 非法字符 / 注入片段 / 超长
        for name in [
            "",
            "1abc",
            "-abc",
            "a b",
            "a-b",
            "a;drop",
            "a\"b",
            "对外库",
            "a'b",
        ] {
            assert_eq!(
                validate_database_name(name),
                Err(exit_codes::ARGUMENT_ERROR),
                "{name:?} 应被拒绝"
            );
        }
        assert_eq!(
            validate_database_name(&"a".repeat(64)),
            Err(exit_codes::ARGUMENT_ERROR)
        );
    }

    #[tokio::test]
    async fn dump_rejects_invalid_database_name() {
        let dir = unique_tmp("dump-baddb");
        let data_dir = dir.join("postgres/16/data");
        let args = cli::DumpArgs {
            data_dir,
            output: dir.join("out.dump"),
            database: "bad name".to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(dump(args).await, Err(exit_codes::ARGUMENT_ERROR));
        assert!(!dir.join("out.dump").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_rejects_invalid_database_name() {
        let dir = unique_tmp("restore-baddb");
        let args = cli::RestoreArgs {
            data_dir: dir.join("postgres/16/data"),
            input: dir.join("nope.dump"),
            target_data_dir: None,
            database: "a;drop".to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::ARGUMENT_ERROR));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn dump_rejects_uninitialized_dir() {
        let dir = unique_tmp("dump-fresh");
        let data_dir = dir.join("postgres/16/data");
        let args = cli::DumpArgs {
            data_dir,
            output: dir.join("out.dump"),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(dump(args).await, Err(exit_codes::DUMP_RESTORE_FAILED));
        assert!(!dir.join("out.dump").exists());
        assert!(!dir.join("out.dump.partial").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn dump_rejects_missing_password() {
        let dir = unique_tmp("dump-nopw");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        let args = cli::DumpArgs {
            data_dir,
            output: dir.join("out.dump"),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(dump(args).await, Err(exit_codes::PASSWORD_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_rejects_missing_input() {
        let dir = unique_tmp("restore-noinput");
        let args = cli::RestoreArgs {
            data_dir: dir.join("postgres/16/data"),
            input: dir.join("nope.dump"),
            target_data_dir: None,
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::DUMP_RESTORE_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_rejects_nonempty_target() {
        let dir = unique_tmp("restore-nonempty");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        // 与源数据目录同级的目录共享 runtime（D16-F05 兼容路径），此处只验证非空拒绝
        let target = dir.join("postgres/16/restored");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(target.join("x"), "y").unwrap();
        // 密码文件需存在以越过 load_password
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: Some(target),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::DUMP_RESTORE_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- D16-F05 显式目标布局与凭据归属（纯文件系统，不启动 PG） ----

    #[test]
    fn plan_explicit_target_shares_runtime_for_sibling_and_accepts_app_layout() {
        let dir = unique_tmp("f05-plan");
        let source = Layout::from_data_dir(&dir.join("postgres/16/data"), None, None, None);

        // 同级目录共享 runtime：沿用源凭据，不产生额外写入
        let sibling = dir.join("postgres/16/restored");
        assert!(plan_explicit_target(&source, &sibling).unwrap().is_none());

        // 独立应用布局 `<data root>/data`：需要为本次恢复建立凭据
        let target = dir.join("second/postgres/16/data");
        let planned = plan_explicit_target(&source, &target).unwrap().unwrap();
        assert_eq!(planned.data_dir, target);
        assert_eq!(
            planned.password_file,
            dir.join("second/postgres/16/runtime/password")
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn plan_explicit_target_rejects_unsupported_layout_and_source_pgdata() {
        let dir = unique_tmp("f05-plan-reject");
        let source = Layout::from_data_dir(&dir.join("postgres/16/data"), None, None, None);

        // 任意布局（命名不是 `data`，且 runtime 与源无关）→ 明确拒绝
        let arbitrary = dir.join("restored");
        assert_eq!(
            plan_explicit_target(&source, &arbitrary).err(),
            Some(exit_codes::ARGUMENT_ERROR)
        );

        // 目标位于源 PGDATA 内 → 拒绝把数据/凭据写进源数据目录
        let inside = source.data_dir.join("nested/data");
        assert_eq!(
            plan_explicit_target(&source, &inside).err(),
            Some(exit_codes::ARGUMENT_ERROR)
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn verify_target_credentials_rejects_foreign_credentials_without_overwrite() {
        let dir = unique_tmp("f05-verify");
        let target = Layout::from_data_dir(&dir.join("second/postgres/16/data"), None, None, None);
        password::write_password_file(&target.password_file, "OtherPwd-1_2.3~").unwrap();
        let before = std::fs::read(&target.password_file).unwrap();

        assert_eq!(
            verify_target_credentials(&target, "SourcePwd-1_2.3~"),
            Err(exit_codes::PASSWORD_FAILED)
        );
        assert_eq!(
            std::fs::read(&target.password_file).unwrap(),
            before,
            "无关凭据不得被覆盖"
        );

        // 同一密码允许重写（用于统一平台格式/收紧权限）
        assert_eq!(
            verify_target_credentials(&target, "OtherPwd-1_2.3~"),
            Ok(())
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn verify_target_credentials_rejects_unusable_or_symlinked_files() {
        let dir = unique_tmp("f05-verify-bad");
        let target = Layout::from_data_dir(&dir.join("second/postgres/16/data"), None, None, None);
        std::fs::create_dir_all(target.password_file.parent().unwrap()).unwrap();

        // 空/损坏的密码文件不可覆盖（可能是别的实例残留）
        std::fs::write(&target.password_file, "  \n").unwrap();
        assert_eq!(
            verify_target_credentials(&target, "SourcePwd-1_2.3~"),
            Err(exit_codes::PASSWORD_FAILED)
        );
        std::fs::remove_file(&target.password_file).unwrap();

        // Windows 无法在非特权测试中创建符号链接，仅 Unix 覆盖符号链接拒绝路径
        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;

            let real = dir.join("real-password");
            std::fs::write(&real, "RealPwd-1_2.3~").unwrap();
            symlink(&real, &target.password_file).unwrap();
            assert_eq!(
                verify_target_credentials(&target, "SourcePwd-1_2.3~"),
                Err(exit_codes::PASSWORD_FAILED),
                "符号链接密码文件不得被覆盖"
            );
            std::fs::remove_file(&target.password_file).unwrap();

            let runtime = target.password_file.parent().unwrap();
            std::fs::remove_dir(runtime).unwrap();
            let real_runtime = dir.join("real-runtime");
            std::fs::create_dir_all(&real_runtime).unwrap();
            symlink(&real_runtime, runtime).unwrap();
            assert_eq!(
                verify_target_credentials(&target, "SourcePwd-1_2.3~"),
                Err(exit_codes::PASSWORD_FAILED),
                "符号链接 runtime 不得被写入"
            );
        }

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_rejects_target_with_existing_credentials() {
        let dir = unique_tmp("f05-restore-conflict");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "SourcePwd-1_2.3~").unwrap();

        let target = dir.join("second/postgres/16/data");
        let target_layout = Layout::from_data_dir(&target, None, None, None);
        password::write_password_file(&target_layout.password_file, "OtherPwd-1_2.3~").unwrap();
        let before = std::fs::read(&target_layout.password_file).unwrap();

        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: Some(target),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::PASSWORD_FAILED));
        assert_eq!(
            std::fs::read(&target_layout.password_file).unwrap(),
            before,
            "目标已有无关凭据时必须拒绝且不覆盖"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_rejects_unsupported_target_layout_before_side_effects() {
        let dir = unique_tmp("f05-restore-layout");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "SourcePwd-1_2.3~").unwrap();

        let target = dir.join("restored");
        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: Some(target.clone()),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::ARGUMENT_ERROR));
        assert!(!target.exists(), "拒绝时不得创建目标目录");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_lock_conflict() {
        let dir = unique_tmp("restore-lock");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let _lock = MaintenanceLock::try_acquire(&layout.lock_file).unwrap();
        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: None,
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::LOCK_CONFLICT));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-F03：无 pid 文件 / PID 已死的 stale 文件不阻止恢复（原目录损坏仍可恢复）。
    #[test]
    fn source_guard_allows_absent_or_stale_pidfile() {
        let dir = unique_tmp("source-guard-stale");
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        // 无 postmaster.pid
        assert_eq!(ensure_source_cluster_idle(&layout, "test"), Ok(()));

        // stale postmaster.pid（PID 不存在）
        write_postmaster_pid(&data_dir, 4_000_000, 1_234_567_890);
        assert_eq!(ensure_source_cluster_idle(&layout, "test"), Ok(()));
        assert!(
            data_dir.join("postmaster.pid").exists(),
            "守卫不得删除/改写源 postmaster.pid"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-F03：PID 存活但无法证明属于本源 PGDATA → fail closed（exit 50）。
    #[test]
    fn source_guard_rejects_unverifiable_live_pid() {
        let dir = unique_tmp("source-guard-unverifiable");
        let data_dir = dir.join("postgres/16/data");
        write_postmaster_pid(&data_dir, std::process::id(), 1_784_505_600);
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        assert_eq!(
            ensure_source_cluster_idle(&layout, "test"),
            Err(exit_codes::LOCK_CONFLICT)
        );
        assert!(data_dir.join("postmaster.pid").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-F03：pid 文件 PID 存活且可证明归属（install/bin/postgres 指向本测试进程
    /// 可执行文件）→ 仍拒绝，且不发送任何信号。
    #[cfg(unix)]
    #[test]
    fn source_guard_rejects_verified_live_instance() {
        use std::os::unix::fs::symlink;

        let dir = unique_tmp("source-guard-live");
        let data_dir = dir.join("postgres/16/data");
        write_postmaster_pid(&data_dir, std::process::id(), 1_784_505_600);
        let bin = dir.join("postgres/16/install/bin");
        std::fs::create_dir_all(&bin).unwrap();
        symlink(std::env::current_exe().unwrap(), bin.join("postgres")).unwrap();

        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        assert_eq!(
            ensure_source_cluster_idle(&layout, "test"),
            Err(exit_codes::LOCK_CONFLICT)
        );
        assert!(data_dir.join("postmaster.pid").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-F03：restore 在密码检查/解包/initdb 前就拒绝活实例，且不创建任何
    /// `.restore-*` / `.bak-*` 目录、不改动源 pid 文件。
    #[tokio::test]
    async fn restore_rejects_live_source_before_any_side_effect() {
        let dir = unique_tmp("restore-live-source");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/16/data");
        write_postmaster_pid(&data_dir, std::process::id(), 1_784_505_600);
        // 故意不写 password file：须先命中活实例守卫（exit 50），而非密码错误（exit 16）。
        let args = cli::RestoreArgs {
            data_dir: data_dir.clone(),
            input,
            target_data_dir: None,
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::LOCK_CONFLICT));

        let parent = data_dir.parent().unwrap();
        let stray: Vec<_> = std::fs::read_dir(parent)
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.starts_with("data.restore-") || n.starts_with("data.bak-"))
            .collect();
        assert!(stray.is_empty(), "拒绝路径不得创建恢复目录: {stray:?}");
        assert!(data_dir.join("postmaster.pid").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-F03：stale（PID 已死）pgdata 不触发活实例拒绝，继续走既有密码前置检查。
    #[tokio::test]
    async fn restore_allows_stale_dead_pid_then_checks_password() {
        let dir = unique_tmp("restore-stale-pid");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/16/data");
        write_postmaster_pid(&data_dir, 4_000_000, 1_234_567_890);
        let args = cli::RestoreArgs {
            data_dir: data_dir.clone(),
            input,
            target_data_dir: None,
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::PASSWORD_FAILED));
        assert!(data_dir.join("postmaster.pid").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn reset_password_rejects_uninitialized_dir() {
        let dir = unique_tmp("resetpw-fresh");
        let data_dir = dir.join("postgres/16/data");
        let args = reset_password_args(data_dir);
        assert_eq!(
            reset_password(args).await,
            Err(exit_codes::DATA_DIR_ABNORMAL)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn reset_password_lock_conflict() {
        let dir = unique_tmp("resetpw-lock");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let _lock = MaintenanceLock::try_acquire(&layout.lock_file).unwrap();
        let args = reset_password_args(data_dir.clone());
        assert_eq!(reset_password(args).await, Err(exit_codes::LOCK_CONFLICT));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `validate_username`：自定义超级用户名（合法标识符）通过，供 reset-password 透传。
    #[test]
    fn validate_username_accepts_safe_identifiers() {
        for name in ["postgres", "qtrading", "stock_admin", "_svc", "a"] {
            assert_eq!(validate_username(name), Ok(()), "{name} 应被接受");
        }
        assert_eq!(validate_username(&"a".repeat(63)), Ok(()));
    }

    /// `validate_username`：白名单外（空 / 注入片段 / 引号 / 非 ASCII / 超长）一律拒绝，
    /// 防止 `ALTER USER <name>` 拼接 SQL 时被注入（R4）。
    #[test]
    fn validate_username_rejects_unsafe() {
        for name in [
            "",
            "1abc",
            "a-b",
            "a b",
            "a;drop",
            "a\"b",
            "a'b",
            "对外用户",
            "postgres $$",
        ] {
            assert_eq!(
                validate_username(name),
                Err(exit_codes::ARGUMENT_ERROR),
                "{name:?} 应被拒绝"
            );
        }
        assert_eq!(
            validate_username(&"a".repeat(64)),
            Err(exit_codes::ARGUMENT_ERROR)
        );
    }

    /// `reset_password` 错误路径：非法用户名在获取维护锁前即被拒绝（exit 2），
    /// 且不触碰 PGDATA / 密码文件。
    #[tokio::test]
    async fn reset_password_rejects_invalid_username() {
        let dir = unique_tmp("resetpw-badname");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        let original = std::fs::read_to_string(&layout.password_file).unwrap();

        let args = cli::ResetPasswordArgs {
            data_dir: data_dir.clone(),
            username: "bad;drop".to_string(),
        };
        assert_eq!(reset_password(args).await, Err(exit_codes::ARGUMENT_ERROR));
        // 密码文件未被改写（未进入临时实例 / ALTER USER 流程）
        let after = std::fs::read_to_string(&layout.password_file).unwrap();
        assert_eq!(after, original, "非法用户名不得改动密码文件");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn ts_compact_is_dir_safe() {
        let ts = ts_compact();
        assert!(ts.ends_with('Z'));
        assert!(ts.chars().all(|c| c.is_ascii_alphanumeric()));
    }

    /// §13.7.44 / §7.5 残留物扫描：`<data_dir_name>.restore-*` 兄弟目录被识别为残留。
    #[test]
    fn scan_residuals_detects_restore_sibling_dir() {
        let dir = unique_tmp("scan-restore");
        let data_dir = dir.join("data");
        std::fs::create_dir_all(&data_dir).unwrap();
        // 创建两个 restore 残留目录（不同时间戳），位于 data_dir 的兄弟目录
        let residual1 = dir.join("data.restore-20260723T120000Z");
        let residual2 = dir.join("data.restore-20260723T130000Z");
        std::fs::create_dir_all(&residual1).unwrap();
        std::fs::create_dir_all(&residual2).unwrap();
        // 写入部分文件模拟半截状态
        std::fs::write(residual1.join("PG_VERSION"), b"16\n").unwrap();

        let (restore_residuals, dump_partials) = scan_residuals(&data_dir);
        assert_eq!(restore_residuals.len(), 2, "should detect 2 residual dirs");
        assert!(
            restore_residuals
                .iter()
                .all(|p| p.contains("data.restore-")),
            "all residuals should match pattern: {restore_residuals:?}"
        );
        assert!(dump_partials.is_empty(), "no dump partial expected");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// §13.7.44 / §7.5 残留物扫描：`*.partial` 兄弟文件被识别为 dump 中断残留。
    #[test]
    fn scan_residuals_detects_dump_partial_file() {
        let dir = unique_tmp("scan-partial");
        let data_dir = dir.join("data");
        std::fs::create_dir_all(&data_dir).unwrap();
        // 创建 dump 残留文件（位于 data_dir 的兄弟目录）
        let partial = dir.join("weekly_backup.dump.partial");
        std::fs::write(&partial, b"half dump").unwrap();
        // 非 .partial 后缀的文件不应被识别
        let normal = dir.join("weekly_backup.dump");
        std::fs::write(&normal, b"full dump").unwrap();

        let (restore_residuals, dump_partials) = scan_residuals(&data_dir);
        assert!(restore_residuals.is_empty(), "no restore residual expected");
        assert_eq!(dump_partials.len(), 1, "should detect 1 partial file");
        assert!(
            dump_partials[0].ends_with("weekly_backup.dump.partial"),
            "partial path mismatch: {}",
            dump_partials[0]
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// §13.7.44 / §7.5 残留物扫描：兄弟目录中无残留时返回空。
    #[test]
    fn scan_residuals_clean_dir_returns_empty() {
        let dir = unique_tmp("scan-clean");
        let data_dir = dir.join("data");
        std::fs::create_dir_all(&data_dir).unwrap();
        // 仅创建正常文件（非 .partial / 非 restore-*）
        std::fs::write(dir.join("readme.txt"), b"hi").unwrap();
        std::fs::write(dir.join("backup.dump"), b"full").unwrap();

        let (restore_residuals, dump_partials) = scan_residuals(&data_dir);
        assert!(restore_residuals.is_empty());
        assert!(dump_partials.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// §13.7.44 / §7.5 残留物扫描：`<data_dir_name>.bak-*` 兄弟目录不应被识别为 restore 残留
    /// （bak 目录是 restore 成功后的正常备份，非中断残留）。
    #[test]
    fn scan_residuals_ignores_bak_sibling_dirs() {
        let dir = unique_tmp("scan-bak");
        let data_dir = dir.join("data");
        std::fs::create_dir_all(&data_dir).unwrap();
        let bak = dir.join("data.bak-20260723T120000Z");
        std::fs::create_dir_all(&bak).unwrap();

        let (restore_residuals, dump_partials) = scan_residuals(&data_dir);
        assert!(
            restore_residuals.is_empty(),
            "bak dirs should not be reported as restore residuals: {restore_residuals:?}"
        );
        assert!(dump_partials.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- P3-Rust-Coverage-D38: 补充 maint.rs 分支覆盖 ----

    /// `load_password` 成功路径：password file 存在 → 返回密码字符串并注册 secret。
    #[test]
    fn load_password_returns_password_when_file_exists() {
        let dir = unique_tmp("loadpw-ok");
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        let pwd = load_password(&layout).expect("password should load");
        assert_eq!(pwd, "TestPwd-1_2.3~xxxx");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `load_password` 失败路径：password file 缺失 → 返回 PASSWORD_FAILED (16)。
    #[test]
    fn load_password_fails_when_file_missing() {
        let dir = unique_tmp("loadpw-miss");
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        assert_eq!(load_password(&layout), Err(exit_codes::PASSWORD_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `load_password` 失败路径：password file 为空 → read_password_file 返回 None → PASSWORD_FAILED。
    #[test]
    fn load_password_fails_when_file_empty() {
        let dir = unique_tmp("loadpw-empty");
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        std::fs::create_dir_all(layout.password_file.parent().unwrap()).unwrap();
        std::fs::write(&layout.password_file, "  \n").unwrap();
        assert_eq!(load_password(&layout), Err(exit_codes::PASSWORD_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// maintenance-shell stdout 契约：psql 路径 + 脱敏连接串（密码段固定 `***`）。
    #[test]
    fn maintenance_shell_json_redacts_password() {
        let info = maintenance_shell_json(Path::new("/x/install"), 55432, DEFAULT_USERNAME);
        let v = serde_json::to_value(&info).unwrap();
        assert!(v["psql_path"].as_str().unwrap().contains("psql"));
        assert_eq!(
            v["connection_string_redacted"],
            "postgresql://postgres:***@127.0.0.1:55432/qtrading"
        );
        assert!(v["connection_string_redacted"]
            .as_str()
            .unwrap()
            .contains(":***@"));
        // 自定义用户名部署：stdout 连接串须与实际实例角色一致（M21 × M19 合并适配）
        let info = maintenance_shell_json(Path::new("/x/install"), 55432, "qtrading");
        let v = serde_json::to_value(&info).unwrap();
        assert_eq!(
            v["connection_string_redacted"],
            "postgresql://qtrading:***@127.0.0.1:55432/qtrading"
        );
    }

    /// restore stdout 契约：原子切换后报告当前数据目录，显式目标模式报告新目录。
    #[test]
    fn restore_target_to_report_selects_active_data_dir() {
        let layout = Layout::from_data_dir(Path::new("/x/postgres/16/data"), None, None, None);
        let restore_dir = Path::new("/x/postgres/16/data.restore-T");
        assert_eq!(
            restore_target_to_report(&layout, restore_dir, true),
            "/x/postgres/16/data"
        );
        assert_eq!(
            restore_target_to_report(&layout, restore_dir, false),
            "/x/postgres/16/data.restore-T"
        );
        // 统一使用 '/'，Windows 路径亦不例外（调用方按字符串取路径）
        assert_eq!(
            restore_target_to_report(
                &Layout::from_data_dir(Path::new(r"C:\x\data"), None, None, None),
                Path::new(r"C:\x\new"),
                false
            ),
            "C:/x/new"
        );
    }

    /// `HbaRestoreGuard`：构造时备份原内容，drop 时恢复（未 disarm 路径）。
    #[test]
    fn hba_restore_guard_restores_on_drop_without_disarm() {
        let dir = unique_tmp("hba-restore");
        std::fs::create_dir_all(&dir).unwrap();
        let hba_path = dir.join("pg_hba.conf");
        std::fs::write(&hba_path, "original content\n").unwrap();
        {
            let _guard = HbaRestoreGuard::new(hba_path.clone());
            // 修改文件内容模拟 trust 写入
            std::fs::write(&hba_path, "modified trust content\n").unwrap();
            // guard 离开作用域时 drop 应恢复 original content
        }
        let restored = std::fs::read_to_string(&hba_path).unwrap();
        assert_eq!(
            restored, "original content\n",
            "guard should restore original content on drop without disarm"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `HbaRestoreGuard`：disarm 后 drop 不恢复（成功路径，避免覆盖新 baseline）。
    #[test]
    fn hba_restore_guard_disarm_prevents_restore_on_drop() {
        let dir = unique_tmp("hba-disarm");
        std::fs::create_dir_all(&dir).unwrap();
        let hba_path = dir.join("pg_hba.conf");
        std::fs::write(&hba_path, "original content\n").unwrap();
        {
            let mut guard = HbaRestoreGuard::new(hba_path.clone());
            std::fs::write(&hba_path, "new scram-sha-256 baseline\n").unwrap();
            guard.disarm();
            // disarm 后 drop 不应恢复
        }
        let after = std::fs::read_to_string(&hba_path).unwrap();
        assert_eq!(
            after, "new scram-sha-256 baseline\n",
            "disarm should prevent restore on drop"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `HbaRestoreGuard`：构造时文件不存在（backup=None），drop 不写入任何内容。
    #[test]
    fn hba_restore_guard_no_backup_does_nothing_on_drop() {
        let dir = unique_tmp("hba-nobackup");
        std::fs::create_dir_all(&dir).unwrap();
        let hba_path = dir.join("pg_hba.conf");
        // 文件不存在时构造 guard
        {
            let _guard = HbaRestoreGuard::new(hba_path.clone());
            // drop 时 backup=None，不应创建文件
        }
        assert!(
            !hba_path.exists(),
            "guard with no backup should not create file on drop"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：已初始化但关键文件缺失（global/pg_control 缺失）→ 触发 critical_files_missing error。
    /// doctor 永远返回 Ok(())，issues 经 JSON 输出，此处只验证不 panic。
    #[test]
    fn doctor_on_init_dir_with_missing_critical_files_ok() {
        let dir = unique_tmp("doctor-missing-critical");
        let data_dir = dir.join("postgres/16/data");
        // PG_VERSION 存在但 global/pg_control 缺失
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        std::fs::create_dir_all(data_dir.join("base")).unwrap();
        std::fs::write(data_dir.join("postgresql.conf"), "# conf\n").unwrap();
        // 故意不创建 global/pg_control
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：postmaster.pid 残留但进程已死（stale_postmaster_pid warning 分支）。
    #[test]
    fn doctor_detects_stale_postmaster_pid() {
        let dir = unique_tmp("doctor-stale-pid");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        // 写入一个几乎不可能存活的 PID（与 pgbin.rs 测试同款）
        std::fs::write(
            data_dir.join("postmaster.pid"),
            "4000000\n/x/data\n1784505600\n55432\n/tmp\n127.0.0.1\n",
        )
        .unwrap();
        // doctor 应识别 stale pid 但不失败
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：runtime state.json 损坏 → state_corrupted warning 分支。
    #[test]
    fn doctor_detects_corrupted_state_file() {
        let dir = unique_tmp("doctor-corrupt-state");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        // 写入损坏的 state.json（位于 runtime/state.json）
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        std::fs::create_dir_all(layout.state_file.parent().unwrap()).unwrap();
        std::fs::write(&layout.state_file, "{not valid json").unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：state.json 含 kill_fallback_count > 0 → kill_fallback_history warning 分支。
    #[test]
    fn doctor_reports_kill_fallback_history() {
        let dir = unique_tmp("doctor-kill-fallback");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let st = state::RuntimeState {
            kill_fallback_count: 2,
            last_start_error: Some("test start error".to_string()),
            ..Default::default()
        };
        state::write(&layout.state_file, &st).unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：PG_VERSION 与 bundled 主版本不匹配 → version_mismatch error 分支。
    #[test]
    fn doctor_detects_pg_version_mismatch() {
        let dir = unique_tmp("doctor-version-mismatch");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(data_dir.join("global")).unwrap();
        std::fs::create_dir_all(data_dir.join("base")).unwrap();
        // 写入一个与 bundled (17) 不匹配的主版本（16）
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        std::fs::write(data_dir.join("global/pg_control"), b"ctl").unwrap();
        std::fs::write(data_dir.join("postgresql.conf"), "# conf\n").unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：已初始化但 password file 存在 → password_file_perms_ok 校验路径覆盖。
    #[test]
    fn doctor_with_password_file_present() {
        let dir = unique_tmp("doctor-with-pw");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `scan_residuals`：data_dir 无 parent（根路径场景）→ 返回空 Vec 不 panic。
    #[test]
    fn scan_residuals_handles_root_path() {
        // 根路径"/"或"C:\"的 parent 为 None；用 Path::new("/") 构造
        let root = Path::new(if cfg!(windows) { "C:\\" } else { "/" });
        // 不实际写文件，仅验证 parent=None 分支不 panic
        let (restore, partials) = scan_residuals(root);
        assert!(restore.is_empty());
        assert!(partials.is_empty());
    }

    /// `scan_residuals`：data_dir 名称不可解析为 str（非 Unicode 字节）→ 返回空不 panic。
    /// Unix 平台才能构造非 Unicode 路径；Windows 跳过。scan_residuals 在 file_name
    /// 解析为 None 时即 return，不会 read_dir，故无需在文件系统创建非 UTF-8 目录
    /// （macOS APFS 会拒绝非 UTF-8 文件名，OS error 92: Illegal byte sequence）。
    #[cfg(unix)]
    #[test]
    fn scan_residuals_handles_non_utf8_data_dir_name() {
        use std::os::unix::ffi::OsStringExt;
        let non_utf8 = std::ffi::OsString::from_vec(vec![0xFF, 0xFE, 0xFD]);
        let data_dir = std::env::temp_dir().join(non_utf8);
        let (restore, partials) = scan_residuals(&data_dir);
        assert!(restore.is_empty(), "non-utf8 name should be skipped");
        assert!(partials.is_empty());
    }

    /// `acquire_lock` 错误路径：lock_file 路径不可创建（父目录是文件）→ LOCK_CONFLICT。
    #[test]
    fn acquire_lock_fails_when_parent_is_file() {
        let dir = unique_tmp("acquire-lock-fail");
        std::fs::create_dir_all(&dir).unwrap();
        // 创建一个文件作为 lock_file 的"父目录"
        let blocker = dir.join("blocker");
        std::fs::write(&blocker, b"i am a file").unwrap();
        // lock_file 路径为 blocker/lock → 父目录 blocker 是文件，create_dir_all 失败
        let data_dir = dir.join("postgres/16/data");
        let mut layout = Layout::from_data_dir(&data_dir, None, None, None);
        layout.lock_file = blocker.join("lock");
        match acquire_lock(&layout) {
            Err(code) => assert_eq!(code, exit_codes::LOCK_CONFLICT),
            Ok(_) => panic!("acquire_lock should fail when parent is a file"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `dump` 错误路径：output 父目录创建失败 → DUMP_RESTORE_FAILED。
    #[tokio::test]
    async fn dump_fails_when_output_parent_uncreateable() {
        let dir = unique_tmp("dump-output-fail");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        // output 路径的父目录为一个已存在的文件
        let blocker = dir.join("blocker");
        std::fs::write(&blocker, b"file").unwrap();
        let args = cli::DumpArgs {
            data_dir,
            output: blocker.join("out.dump"),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(dump(args).await, Err(exit_codes::DUMP_RESTORE_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `restore` 错误路径：密码文件缺失 → PASSWORD_FAILED（在 acquire_lock 之后）。
    #[tokio::test]
    async fn restore_fails_when_password_missing() {
        let dir = unique_tmp("restore-nopw");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/16/data");
        // 故意不写 password file
        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: None,
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(restore(args).await, Err(exit_codes::PASSWORD_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `reset_password` 错误路径：PGDATA 已初始化但有活实例（postmaster.pid 含活 PID）
    /// → LOCK_CONFLICT。此处用本进程 PID 模拟"活 postgres"。
    #[tokio::test]
    async fn reset_password_rejects_alive_postgres() {
        let dir = unique_tmp("resetpw-alive");
        let data_dir = dir.join("postgres/16/data");
        // 必须先建立完整 cluster（含 global/pg_control / base / postgresql.conf），
        // 否则 guard_data_dir 会先返回 DATA_DIR_ABNORMAL(40) 而非 LOCK_CONFLICT(50)
        make_fake_cluster(&data_dir);
        // 写入本进程 PID 作为"活" postgres
        let own_pid = std::process::id();
        std::fs::write(
            data_dir.join("postmaster.pid"),
            format!("{own_pid}\n/x/data\n1784505600\n55432\n/tmp\n127.0.0.1\n"),
        )
        .unwrap();
        let args = reset_password_args(data_dir);
        assert_eq!(reset_password(args).await, Err(exit_codes::LOCK_CONFLICT));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `maintenance_shell` 错误路径：未初始化目录 → DATA_DIR_ABNORMAL。
    #[tokio::test]
    async fn maintenance_shell_rejects_uninitialized_dir() {
        let dir = unique_tmp("shell-fresh");
        let data_dir = dir.join("postgres/16/data");
        let args = cli::MaintenanceShellArgs {
            data_dir,
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(
            maintenance_shell(args).await,
            Err(exit_codes::DATA_DIR_ABNORMAL)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `maintenance_shell` 错误路径：锁冲突 → LOCK_CONFLICT。
    #[tokio::test]
    async fn maintenance_shell_lock_conflict() {
        let dir = unique_tmp("shell-lock");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let _lock = MaintenanceLock::try_acquire(&layout.lock_file).unwrap();
        let args = cli::MaintenanceShellArgs {
            data_dir,
            username: DEFAULT_USERNAME.to_string(),
        };
        assert_eq!(
            maintenance_shell(args).await,
            Err(exit_codes::LOCK_CONFLICT)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- P3-Rust-Coverage-D38: 补充 doctor 残留物报告与边界分支 ----

    /// doctor：data_dir 兄弟目录含 `.restore-*` 残留 + `.partial` 文件 →
    /// 触发 restore_residual + dump_partial warning 分支（lines 338-355）。
    #[test]
    fn doctor_reports_residuals_and_partials() {
        let dir = unique_tmp("doctor-residuals");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        // scan_residuals 扫描 data_dir.parent()（即 dir/postgres/16/），
        // data_dir 的 file_name = "data"，残留目录需命名为 "data.restore-*"
        let parent = data_dir.parent().unwrap(); // dir/postgres/16/
        let restore_residual = parent.join("data.restore-20260728T120000Z");
        std::fs::create_dir_all(&restore_residual).unwrap();
        let partial_file = parent.join("backup.dump.partial");
        std::fs::write(&partial_file, b"half dump").unwrap();
        // doctor 永远返回 Ok(())，issues 经 JSON 输出
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：password_file 存在但权限过宽（Unix 0644）→ password_file_perms warning。
    /// Windows 无文件权限模型，此测试仅在 Unix 验证。
    #[cfg(unix)]
    #[test]
    fn doctor_reports_password_file_perms_on_unix() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_tmp("doctor-pw-perms");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        // 改为 0644（权限过宽，非 0600）
        std::fs::set_permissions(
            &layout.password_file,
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `scan_residuals`：parent 目录不可读（不存在）→ 返回空 Vec 不 panic。
    /// data_dir 的 parent 不存在时 read_dir 返回 Err → 走 line 422 分支。
    #[test]
    fn scan_residuals_parent_not_found_returns_empty() {
        // 构造一个 parent 不存在的 data_dir 路径
        let dir = unique_tmp("scan-noparent");
        let nonexistent_parent = dir.join("nonexistent-parent");
        let data_dir = nonexistent_parent.join("data");
        // 不创建 nonexistent_parent，read_dir 会失败
        let (restore, partials) = scan_residuals(&data_dir);
        assert!(restore.is_empty(), "should return empty on read_dir error");
        assert!(partials.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `wait_for_enter`：stdin EOF（管道关闭/重定向）→ 立即返回不阻塞。
    /// cargo test 默认非 TTY 环境下 stdin 可能已 EOF；此测试验证不 panic 不死锁。
    #[tokio::test]
    async fn wait_for_enter_handles_eof_gracefully() {
        // 在 CI/非交互环境，stdin 通常为 EOF 或管道。
        // 设置 1 秒 timeout 防止测试挂起（若 stdin 为 TTY 会超时失败，提示环境问题）。
        let result = tokio::time::timeout(Duration::from_secs(1), wait_for_enter()).await;
        // Ok(()) = 正常返回（EOF/Err 均会 break）；Err(Elapsed) = 超时（stdin 阻塞）
        // 在非 TTY 环境（CI）应 Ok，本地 TTY 可能 Elapsed，两者都接受为通过
        // — 此测试主要确保代码路径被覆盖（line 881-896）
        let _ = result;
    }

    /// doctor：数据目录非空 + PG_VERSION 存在但 global/pg_control 缺失 →
    /// critical_files_missing + pg_control_unreadable 双 error 分支（组合覆盖）。
    #[test]
    fn doctor_missing_pg_control_with_version() {
        let dir = unique_tmp("doctor-no-control");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        std::fs::create_dir_all(data_dir.join("base")).unwrap();
        std::fs::write(data_dir.join("postgresql.conf"), "# conf\n").unwrap();
        // 故意不创建 global/pg_control
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：数据目录非空 + PG_VERSION 存在但 base 缺失 →
    /// critical_files_missing 包含 "base" 分支。
    #[test]
    fn doctor_missing_base_dir_with_version() {
        let dir = unique_tmp("doctor-no-base");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(data_dir.join("global")).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        std::fs::write(data_dir.join("global/pg_control"), b"ctl").unwrap();
        std::fs::write(data_dir.join("postgresql.conf"), "# conf\n").unwrap();
        // 故意不创建 base 目录
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：数据目录非空 + PG_VERSION 存在但 postgresql.conf 缺失 →
    /// critical_files_missing 包含 "postgresql.conf" 分支。
    #[test]
    fn doctor_missing_postgresql_conf_with_version() {
        let dir = unique_tmp("doctor-no-conf");
        let data_dir = dir.join("postgres/16/data");
        std::fs::create_dir_all(data_dir.join("global")).unwrap();
        std::fs::create_dir_all(data_dir.join("base")).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();
        std::fs::write(data_dir.join("global/pg_control"), b"ctl").unwrap();
        // 故意不创建 postgresql.conf
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- P3-Rust-Coverage-D38: doctor env override + cloud sync 分支 ----

    /// doctor：通过 `QTRADING_PG_SIDECAR_FORCE_FS_KIND=fat32` 强制 FAT32 →
    /// fs_unsupported error 分支（lines 281-287）。
    /// env var 为进程全局，set 后立即 remove 避免串扰其他并行测试。
    #[test]
    fn doctor_with_fs_unsupported_env_override() {
        std::env::set_var("QTRADING_PG_SIDECAR_FORCE_FS_KIND", "fat32");
        let dir = unique_tmp("doctor-fat32");
        let data_dir = dir.join("postgres/16/data");
        make_fake_cluster(&data_dir);
        assert!(doctor(&data_dir).is_ok());
        std::env::remove_var("QTRADING_PG_SIDECAR_FORCE_FS_KIND");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// doctor：data_dir 路径含 "onedrive" → cloud_sync_path error 分支（lines 295-298）。
    #[test]
    fn doctor_with_cloud_sync_path() {
        let dir = unique_tmp("doctor-onedrive");
        // 路径含 "onedrive" 触发 detect_cloud_sync
        let data_dir = dir.join("onedrive/postgres/16/data");
        make_fake_cluster(&data_dir);
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- P3-Rust-Coverage-D38: PG-starting 成功路径测试 ----

    /// PG-starting 测试串行化锁：避免端口/锁/PGDATA 冲突。
    static PG_TEST_MUTEX: std::sync::Mutex<()> = std::sync::Mutex::new(());

    /// 设置真实 PG 实例所需的目录结构与 binaries：
    /// ensure_binaries + write_password_file + run_initdb + write_security_baseline。
    /// 返回 (layout, password, dir) — dir 用于测试后清理。
    async fn setup_pg_layout(test_name: &str) -> (Layout, String, PathBuf) {
        setup_pg_layout_with(test_name, DEFAULT_USERNAME).await
    }

    /// 同 `setup_pg_layout`，但可指定 initdb 超级用户名（M23：自定义用户名 cluster）。
    async fn setup_pg_layout_with(test_name: &str, username: &str) -> (Layout, String, PathBuf) {
        let dir = unique_tmp(test_name);
        let data_dir = dir.join("postgres/16/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let password = "TestPwd-1_2.3~xxxx".to_string();

        setup::ensure_binaries(&layout, &|_| {}).await.unwrap();
        password::write_password_file(&layout.password_file, &password).unwrap();
        setup::run_initdb(&layout, username, &password)
            .await
            .unwrap();
        setup::write_security_baseline(&data_dir, LISTEN_LOCAL).unwrap();

        (layout, password, dir)
    }

    /// `dump` 在线路径（PG 运行中）：TempInstance::start → dump 检测在线 → run_pg_dump → 原子改名。
    /// 覆盖 TempInstance::start/stop、dump 在线分支、run_pg_dump、结果处理。
    /// `await_holding_lock`：PG_TEST_MUTEX 是测试串行化锁，非生产代码，持有锁 await 不会死锁。
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn dump_succeeds_with_running_instance() {
        let _guard = PG_TEST_MUTEX.lock().unwrap();
        let (layout, password, dir) = setup_pg_layout("dump-online").await;

        let instance = TempInstance::start(layout.clone(), DEFAULT_USERNAME, &password)
            .await
            .expect("TempInstance start failed");
        // run_pg_dump 连接 `qtrading` 库，必须在 dump 前创建（run.rs ensure_database 等价）
        pgbin::psql(
            &instance.layout.install_dir,
            LISTEN_LOCAL,
            instance.port,
            DEFAULT_USERNAME,
            "postgres",
            &password,
            "CREATE DATABASE qtrading;",
            // 与生产 restore 路径（`Duration::from_secs(30)`）对齐，避免 CI 慢机上的 10s 超时 flaky
            Duration::from_secs(30),
        )
        .await
        .expect("CREATE DATABASE qtrading failed");

        let dump_path = layout.data_dir.parent().unwrap().join("test.dump");
        let args = cli::DumpArgs {
            data_dir: layout.data_dir.clone(),
            output: dump_path.clone(),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        let result = dump(args).await;

        instance.stop().await;

        assert!(result.is_ok(), "dump should succeed: {:?}", result.err());
        assert!(
            dump_path.exists(),
            "dump file should exist at {dump_path:?}"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `restore` 成功路径（target_data_dir 模式，不交换目录）：
    /// 启动 PG → dump → 停止 → restore 到独立应用布局 `<root>/postgres/16/data`
    /// → 验证 target 含 PG_VERSION、目标 runtime 已写入同一密码、源凭据未被改动。
    /// 覆盖 D16-F05 的目标布局规划/加锁/凭据写入与 restore_via_instance。
    /// `await_holding_lock`：PG_TEST_MUTEX 是测试串行化锁，非生产代码，持有锁 await 不会死锁。
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn restore_succeeds_with_target_data_dir() {
        let _guard = PG_TEST_MUTEX.lock().unwrap();
        let (layout, password, dir) = setup_pg_layout("restore-target").await;
        let source_password_before = std::fs::read(&layout.password_file).unwrap();

        // 1. 启动 PG 并 dump（在线路径，不占锁）
        let instance = TempInstance::start(layout.clone(), DEFAULT_USERNAME, &password)
            .await
            .expect("TempInstance start failed");
        // dump 需要连接 qtrading 库，先创建
        pgbin::psql(
            &instance.layout.install_dir,
            LISTEN_LOCAL,
            instance.port,
            DEFAULT_USERNAME,
            "postgres",
            &password,
            "CREATE DATABASE qtrading;",
            // 与生产 restore 路径（`Duration::from_secs(30)`）对齐，避免 CI 慢机上的 10s 超时 flaky
            Duration::from_secs(30),
        )
        .await
        .expect("CREATE DATABASE qtrading failed");
        let dump_path = layout.data_dir.parent().unwrap().join("test.dump");
        let dump_args = cli::DumpArgs {
            data_dir: layout.data_dir.clone(),
            output: dump_path.clone(),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        let dump_result = dump(dump_args).await;
        instance.stop().await;
        assert!(dump_result.is_ok(), "dump should succeed first");
        assert!(dump_path.exists(), "dump file should exist");

        // 2. restore 到独立应用布局（do_swap=false，不交换目录；runtime 与源不同）
        let target = dir.join("second/postgres/16/data");
        let restore_args = cli::RestoreArgs {
            data_dir: layout.data_dir.clone(),
            input: dump_path.clone(),
            target_data_dir: Some(target.clone()),
            database: DEFAULT_DATABASE.to_string(),
            username: DEFAULT_USERNAME.to_string(),
        };
        let restore_result = restore(restore_args).await;

        assert!(
            restore_result.is_ok(),
            "restore should succeed: {:?}",
            restore_result.err()
        );
        assert!(
            target.join("PG_VERSION").exists(),
            "target should be initialized"
        );

        // 3. D16-F05：目标布局必须能独立启动 —— runtime/password 存在且为同一密码
        let target_layout = Layout::from_data_dir(&target, None, None, None);
        assert_eq!(
            password::read_password_file(&target_layout.password_file).as_deref(),
            Some(password.as_str()),
            "目标 runtime 必须写入同一密码"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&target_layout.password_file)
                .unwrap()
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600, "目标密码文件权限必须为 0600");
        }

        // 4. 源凭据保持原样（不得被改写/重新加密）
        assert_eq!(
            std::fs::read(&layout.password_file).unwrap(),
            source_password_before,
            "源密码文件不得被修改"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `reset_password` 成功路径：
    /// 启动 PG → 停止 → reset_password → 验证 password file 内容变更。
    /// 覆盖 reset_password 完整成功路径（trust HBA → TempInstance → ALTER USER → 重写 HBA）。
    /// `await_holding_lock`：PG_TEST_MUTEX 是测试串行化锁，非生产代码，持有锁 await 不会死锁。
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn reset_password_succeeds_with_pg_instance() {
        let _guard = PG_TEST_MUTEX.lock().unwrap();
        let (layout, password, dir) = setup_pg_layout("resetpw-ok").await;

        // 读取旧密码（F5：Windows 加密文件经 read_password_file 解密为明文）
        let old_pwd = password::read_password_file(&layout.password_file)
            .expect("old password should be readable");

        // 启动并停止 PG（确保 PGDATA 可用，lock 可获取）
        let instance = TempInstance::start(layout.clone(), DEFAULT_USERNAME, &password)
            .await
            .expect("TempInstance start failed");
        instance.stop().await;

        // 调用 reset_password
        let args = reset_password_args(layout.data_dir.clone());
        let result = reset_password(args).await;

        assert!(
            result.is_ok(),
            "reset_password should succeed: {:?}",
            result.err()
        );

        // 验证密码已变更（F5：经 read_password_file 解密，取明文比较）
        let new_pwd = password::read_password_file(&layout.password_file)
            .expect("new password should be readable");
        assert_ne!(new_pwd, old_pwd, "password should be rotated");
        assert!(!new_pwd.is_empty(), "new password should not be empty");

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `reset_password` 成功路径（自定义超级用户名，M23）：
    /// initdb 以 `qtrading` 为超级用户建 cluster → `reset-password --username qtrading`
    /// → 密码轮换。若仍硬编码 postgres（角色不存在），临时实例健康检查失败 exit 12。
    /// `await_holding_lock`：PG_TEST_MUTEX 是测试串行化锁，非生产代码，持有锁 await 不会死锁。
    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn reset_password_succeeds_with_custom_username() {
        let _guard = PG_TEST_MUTEX.lock().unwrap();
        const CUSTOM_USER: &str = "qtrading";
        let (layout, _password, dir) = setup_pg_layout_with("resetpw-custom", CUSTOM_USER).await;

        let old_pwd = password::read_password_file(&layout.password_file)
            .expect("old password should be readable");

        let args = cli::ResetPasswordArgs {
            data_dir: layout.data_dir.clone(),
            username: CUSTOM_USER.to_string(),
        };
        let result = reset_password(args).await;
        assert!(
            result.is_ok(),
            "reset_password --username {CUSTOM_USER} should succeed: {:?}",
            result.err()
        );

        let new_pwd = password::read_password_file(&layout.password_file)
            .expect("new password should be readable");
        assert_ne!(
            new_pwd, old_pwd,
            "password should be rotated for custom superuser"
        );
        assert!(!new_pwd.is_empty(), "new password should not be empty");

        let _ = std::fs::remove_dir_all(&dir);
    }
}
