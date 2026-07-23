//! 离线维护命令（pg_plan §13.2）：doctor / dump / restore / maintenance-shell。
//!
//! - doctor：只读诊断（§13.5 诊断包数据源），输出 doctor JSON + issues 汇总，永不修改 PGDATA。
//! - dump：运行中实例直连 `pg_dump`（SQL 级，不占锁，§13.3 AI-28）；离线则持锁起临时实例。
//! - restore：恢复到新目录不覆盖原目录（§12.2），复用既有密码（AI-29），临时实例 + pg_restore。
//! - maintenance-shell：持锁起临时维护实例，输出脱敏连接信息，等用户结束后优雅停止。
//!
//! 恢复后 schema 停留于备份时点；Alembic 补平由 app 下次启动的既有迁移流程负责（Phase 2+，§12.2 step 7）。

use crate::commands::state_file_condition;
use crate::exit_codes;
use crate::lockfile::{AcquireError, MaintenanceLock};
use crate::password;
use crate::paths::Layout;
use crate::pgbin;
use crate::preflight;
use crate::protocol;
use crate::run;
use crate::setup::{self, DataDirState};
use crate::state;
use crate::{cli, logging};
use postgresql_embedded::PostgreSQL;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::time::Duration;

const DUMP_TIMEOUT: Duration = Duration::from_secs(3600);
const DEFAULT_DATABASE: &str = "qtrading";
const DEFAULT_USERNAME: &str = "postgres";
const LISTEN_LOCAL: &str = "127.0.0.1";

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
    /// 必须保留 ownership：PostgreSQL::drop 会调用 pg_ctl stop（crate 0.21 行为），
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
    pid: Option<u32>,
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
                    let pid = pgbin::read_postmaster_pid(&layout.data_dir).map(|i| i.pid);
                    let _ = pgbin::graded_stop(&layout.install_dir, &layout.data_dir, pid).await;
                    return Err(exit_codes::START_FAILED);
                }
            }
        }

        let pid = pgbin::read_postmaster_pid(&layout.data_dir).map(|i| i.pid);
        Ok(Self {
            pg,
            layout,
            port,
            pid,
        })
    }

    async fn stop(self) {
        let outcome =
            pgbin::graded_stop(&self.layout.install_dir, &self.layout.data_dir, self.pid).await;
        if !outcome.stopped {
            eprintln!(
                "[sidecar] 警告：临时实例未能干净停止 (mode={})",
                outcome.mode.as_str()
            );
        }
        // self.pg 在函数结束 drop；graded_stop 已停 postgres，crate status() 检测到非 Started，drop 不会再 stop
    }
}

// ---- dump ----

pub async fn dump(args: cli::DumpArgs) -> Result<(), u8> {
    logging::init(None);
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
            run_pg_dump(&layout.install_dir, port, &password, &partial).await
        }
        None => {
            let _lock = acquire_lock(&layout)?;
            if pm.is_some() {
                let _ = std::fs::remove_file(layout.data_dir.join("postmaster.pid"));
            }
            setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;
            let instance = TempInstance::start(layout.clone(), DEFAULT_USERNAME, &password).await?;
            let port = instance.port;
            let install_dir = instance.layout.install_dir.clone();
            let r = run_pg_dump(&install_dir, port, &password, &partial).await;
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
            DEFAULT_USERNAME,
            "-d",
            DEFAULT_DATABASE,
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

pub async fn restore(args: cli::RestoreArgs) -> Result<(), u8> {
    logging::init(None);
    let layout = Layout::from_data_dir(&args.data_dir, None, None, None);

    if !args.input.exists() {
        eprintln!("[sidecar] 备份文件不存在：{}", args.input.display());
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }
    let _lock = acquire_lock(&layout)?;
    let password = load_password(&layout)?;
    setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;

    // §12.2：恢复到新目录。显式 --target-data-dir 时原目录完全不动；缺省时走原子切换。
    let ts = ts_compact();
    let (restore_dir, do_swap) = match &args.target_data_dir {
        Some(target) => {
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

    let outcome = restore_into(&layout, &restore_dir, &args.input, &password).await;
    if let Err(code) = outcome {
        let _ = std::fs::remove_dir_all(&restore_dir);
        return Err(code);
    }

    if do_swap {
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
        eprintln!(
            "[sidecar] 恢复完成：{}（原目录未改动）",
            restore_dir.display()
        );
    }
    Ok(())
}

/// 在 restore_dir 上执行：initdb → 安全基线 → 临时实例 → create db → pg_restore → 健康检查。
/// 任一步失败返回错误码，调用方负责清理 restore_dir。
async fn restore_into(
    layout: &Layout,
    restore_dir: &Path,
    input: &Path,
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
    setup::run_initdb(&restore_layout, DEFAULT_USERNAME).await?;
    if let Err(e) = setup::write_security_baseline(restore_dir, LISTEN_LOCAL) {
        eprintln!("[sidecar] 安全基线写入失败: {e}");
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }

    let instance = TempInstance::start(restore_layout, DEFAULT_USERNAME, password).await?;
    let result = restore_via_instance(&instance, input, password).await;
    instance.stop().await;
    result
}

async fn restore_via_instance(
    instance: &TempInstance,
    input: &Path,
    password: &str,
) -> Result<(), u8> {
    let port_s = instance.port.to_string();
    let install_dir = &instance.layout.install_dir;

    // AI-29：既有密码认证新 cluster（initdb --pwfile 已注入同一密码）
    pgbin::psql(
        install_dir,
        LISTEN_LOCAL,
        instance.port,
        DEFAULT_USERNAME,
        "postgres",
        password,
        &format!("CREATE DATABASE {DEFAULT_DATABASE}"),
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
            DEFAULT_USERNAME,
            "-d",
            DEFAULT_DATABASE,
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
        DEFAULT_USERNAME,
        DEFAULT_DATABASE,
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

pub async fn maintenance_shell(args: cli::DataDirArgs) -> Result<(), u8> {
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

    let instance = TempInstance::start(layout.clone(), DEFAULT_USERNAME, &password).await?;
    let psql_path = pgbin::tool_path(&layout.install_dir, "psql");
    eprintln!("[sidecar] 维护实例已启动：");
    eprintln!(
        "  连接（脱敏）: postgresql://{DEFAULT_USERNAME}:***@{LISTEN_LOCAL}:{}/{DEFAULT_DATABASE}",
        instance.port
    );
    eprintln!(
        "  psql: {} -h {LISTEN_LOCAL} -p {} -U {DEFAULT_USERNAME} -d {DEFAULT_DATABASE}",
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

/// 通过 `postgres --single` 单用户模式重置 postgres 用户密码（§13.7.8）。
///
/// 单用户模式不需要密码认证（PostgreSQL 内部直接执行 SQL），适用于密码丢失场景。
/// 流程：acquire_lock → guard_data_dir（仅 Existing）→ ensure_binaries →
///       生成新密码 → `postgres --single -D <dir> postgres` 通过 stdin 注入
///       `ALTER USER postgres PASSWORD '<new>'` → 写入新 password file →
///       重写 pg_hba.conf（scram-sha-256，确保新密码生效）→ register_secret。
///
/// exit code：
/// - 50 LOCK_CONFLICT：维护锁冲突（qTrading 运行中）
/// - 40 DATA_DIR_ABNORMAL：数据目录未初始化
/// - 16 PASSWORD_FAILED：postgres --single 失败 / password file 写入失败
/// - 30 DUMP_RESTORE_FAILED：pg_hba.conf 重写失败
pub async fn reset_password(args: cli::DataDirArgs) -> Result<(), u8> {
    logging::init(None);
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
    // Windows 上 graded_stop 可能走 kill fallback 残留 postmaster.pid，导致
    // `postgres --single` 拒绝启动（exit 16 PASSWORD_FAILED）。这里在 spawn 前清理。
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
    let postgres_path = pgbin::tool_path(&layout.install_dir, "postgres");
    let data_dir_s = layout.data_dir.to_string_lossy().into_owned();
    // SQL 字符串字面量需用美元引号 $$ 避免 SQL 注入（new_pwd 是 URL-safe 字符集，理论上安全）
    let sql = format!("ALTER USER postgres PASSWORD $${new_pwd}$$;\n");

    let mut cmd = tokio::process::Command::new(&postgres_path);
    cmd.arg("--single")
        .args(["-D", &data_dir_s])
        // 单用户模式不监听 TCP，仅本地 IPC
        .args(["-c", "listen_addresses="])
        .arg("postgres")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        // tokio::process::Command 自带 creation_flags 方法（不需 CommandExt trait import）
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd.spawn().map_err(|e| {
        eprintln!(
            "[sidecar] postgres --single 启动失败 {}：{e}",
            postgres_path.display()
        );
        exit_codes::PASSWORD_FAILED
    })?;

    // 写入 SQL 到 stdin 触发执行，drop stdin 触发 EOF 让 postgres 退出
    if let Some(mut stdin) = child.stdin.take() {
        use tokio::io::AsyncWriteExt;
        if let Err(e) = stdin.write_all(sql.as_bytes()).await {
            eprintln!("[sidecar] 写入 SQL 到 postgres --single stdin 失败：{e}");
            let _ = child.kill().await;
            return Err(exit_codes::PASSWORD_FAILED);
        }
        let _ = stdin.shutdown().await;
    }

    let out = tokio::time::timeout(Duration::from_secs(30), child.wait_with_output()).await;
    let output = match out {
        Ok(Ok(o)) => o,
        Ok(Err(e)) => {
            eprintln!("[sidecar] postgres --single wait 失败：{e}");
            return Err(exit_codes::PASSWORD_FAILED);
        }
        Err(_) => {
            eprintln!("[sidecar] postgres --single 超时 30s");
            return Err(exit_codes::PASSWORD_FAILED);
        }
    };
    if !output.status.success() {
        eprintln!(
            "[sidecar] postgres --single 失败 (code {:?})：{}",
            output.status.code(),
            logging::sanitize(String::from_utf8_lossy(&output.stderr).trim())
        );
        return Err(exit_codes::PASSWORD_FAILED);
    }

    // 写入新 password file（权限 0600 on Unix）
    if let Err(e) = password::write_password_file(&layout.password_file, &new_pwd) {
        eprintln!(
            "[sidecar] 写入新 password file 失败 {}：{e}",
            layout.password_file.display()
        );
        return Err(exit_codes::PASSWORD_FAILED);
    }

    // 重写 pg_hba.conf（保持 scram-sha-256 认证，新密码生效）
    if let Err(e) = setup::write_security_baseline(&layout.data_dir, LISTEN_LOCAL) {
        eprintln!("[sidecar] pg_hba.conf 重写失败：{e}");
        return Err(exit_codes::DUMP_RESTORE_FAILED);
    }

    // 注册新密码为 secret（R9：防止后续日志/异常泄露）
    logging::register_secret(&new_pwd);

    eprintln!("[sidecar] 密码已重置，请重启 qTrading");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-maint-test-{}-{}", std::process::id(), name))
    }

    /// 构造"已初始化"假 cluster 目录（仅文件骨架，不含真实 PG 数据）。
    fn make_fake_cluster(dir: &Path) {
        std::fs::create_dir_all(dir.join("global")).unwrap();
        std::fs::create_dir_all(dir.join("base")).unwrap();
        std::fs::write(dir.join("PG_VERSION"), "17\n").unwrap();
        std::fs::write(dir.join("global/pg_control"), b"ctl").unwrap();
        std::fs::write(dir.join("postgresql.conf"), "# conf\n").unwrap();
    }

    #[test]
    fn doctor_on_fresh_dir_reports_not_initialized() {
        let dir = unique_tmp("doctor-fresh");
        let data_dir = dir.join("postgres/17/data");
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn doctor_on_fake_cluster_ok() {
        let dir = unique_tmp("doctor-cluster");
        let data_dir = dir.join("postgres/17/data");
        make_fake_cluster(&data_dir);
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn doctor_on_nonempty_without_pg_version_ok_but_issues() {
        // doctor 自身永不失败（exit 0），问题经 issues 数组上报
        let dir = unique_tmp("doctor-abnormal");
        let data_dir = dir.join("postgres/17/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("random.txt"), "x").unwrap();
        assert!(doctor(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn dump_rejects_uninitialized_dir() {
        let dir = unique_tmp("dump-fresh");
        let data_dir = dir.join("postgres/17/data");
        let args = cli::DumpArgs {
            data_dir,
            output: dir.join("out.dump"),
        };
        assert_eq!(dump(args).await, Err(exit_codes::DUMP_RESTORE_FAILED));
        assert!(!dir.join("out.dump").exists());
        assert!(!dir.join("out.dump.partial").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn dump_rejects_missing_password() {
        let dir = unique_tmp("dump-nopw");
        let data_dir = dir.join("postgres/17/data");
        make_fake_cluster(&data_dir);
        let args = cli::DumpArgs {
            data_dir,
            output: dir.join("out.dump"),
        };
        assert_eq!(dump(args).await, Err(exit_codes::PASSWORD_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_rejects_missing_input() {
        let dir = unique_tmp("restore-noinput");
        let args = cli::RestoreArgs {
            data_dir: dir.join("postgres/17/data"),
            input: dir.join("nope.dump"),
            target_data_dir: None,
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
        let target = dir.join("target");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(target.join("x"), "y").unwrap();
        // 密码文件需存在以越过 load_password
        let data_dir = dir.join("postgres/17/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        password::write_password_file(&layout.password_file, "TestPwd-1_2.3~xxxx").unwrap();
        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: Some(target),
        };
        assert_eq!(restore(args).await, Err(exit_codes::DUMP_RESTORE_FAILED));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn restore_lock_conflict() {
        let dir = unique_tmp("restore-lock");
        let input = dir.join("in.dump");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&input, b"dump").unwrap();
        let data_dir = dir.join("postgres/17/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let _lock = MaintenanceLock::try_acquire(&layout.lock_file).unwrap();
        let args = cli::RestoreArgs {
            data_dir,
            input,
            target_data_dir: None,
        };
        assert_eq!(restore(args).await, Err(exit_codes::LOCK_CONFLICT));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn reset_password_rejects_uninitialized_dir() {
        let dir = unique_tmp("resetpw-fresh");
        let data_dir = dir.join("postgres/17/data");
        let args = cli::DataDirArgs { data_dir };
        assert_eq!(
            reset_password(args).await,
            Err(exit_codes::DATA_DIR_ABNORMAL)
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn reset_password_lock_conflict() {
        let dir = unique_tmp("resetpw-lock");
        let data_dir = dir.join("postgres/17/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        std::fs::write(data_dir.join("PG_VERSION"), "17\n").unwrap();
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let _lock = MaintenanceLock::try_acquire(&layout.lock_file).unwrap();
        let args = cli::DataDirArgs { data_dir };
        assert_eq!(reset_password(args).await, Err(exit_codes::LOCK_CONFLICT));
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
        std::fs::write(residual1.join("PG_VERSION"), b"17\n").unwrap();

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
}
