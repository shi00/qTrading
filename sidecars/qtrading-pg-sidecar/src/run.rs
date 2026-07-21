//! `run` supervisor（pg_plan §7.2/§7.3）：常驻进程，stdout 第 1 行 ready JSON（§6.2）。
//!
//! 流程：维护锁 → preflight → binaries setup → initdb 守卫 → 密码 → initdb(Fresh) →
//! 安全基线 → stale pid 清理 → 启动（端口重试 ≤3）→ 健康检查 → create database →
//! runtime state(running) → ready JSON → 监督（父进程/postgres/磁盘）→ 分级停止。
//!
//! stdout 协议：ready JSON 前禁止任何 stdout 写入；运行期仅 warning/exit 事件 JSON-lines。

use crate::exit_codes;
use crate::lockfile::{AcquireError, MaintenanceLock};
use crate::password;
use crate::paths::Layout;
use crate::pgbin::{self, StopMode};
use crate::preflight;
use crate::protocol::{self, EventJson, ReadyJson};
use crate::setup::{self, DataDirState};
use crate::state::{self};
use crate::{cli, logging};
use postgresql_embedded::{PostgreSQL, SettingsBuilder};
use std::net::TcpListener;
use std::time::Duration;

const MAX_PORT_ATTEMPTS: u8 = 3;

pub(crate) fn pick_free_port(listen: &str) -> std::io::Result<u16> {
    let listener = TcpListener::bind((listen, 0))?;
    listener.local_addr().map(|a| a.port())
}

/// crate 实例构造（run 与维护命令共用）：fsync 兜底（AI-21）+ trust_installation_dir
/// （install dir 已由 ensure_binaries 原子就绪；禁止 crate 再拼 `<dir>/<version>` 子目录）。
pub(crate) fn build_postgresql(
    layout: &Layout,
    listen: &str,
    username: &str,
    password: &str,
    port: u16,
) -> PostgreSQL {
    let mut configuration = std::collections::HashMap::new();
    configuration.insert("fsync".to_string(), "on".to_string());
    let settings = SettingsBuilder::new()
        .installation_dir(layout.install_dir.clone())
        .data_dir(layout.data_dir.clone())
        .password_file(layout.password_file.clone())
        .host(listen.to_string())
        .port(port)
        .username(username.to_string())
        .password(password.to_string())
        .configuration(configuration)
        .timeout(Some(Duration::from_secs(300))) // Spike 实测：crate 默认超时在 Windows 首次启动不足
        .trust_installation_dir(true)
        .temporary(false)
        .build();
    PostgreSQL::new(settings)
}

/// sidecar 运行期 UTC 时间戳（marker/state 用）。Howard Hinnant civil-from-days，与 build.rs 同款。
pub fn utc_now_iso8601() -> String {
    let epoch_secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (epoch_secs / 86_400) as i64;
    let secs_of_day = epoch_secs % 86_400;
    let (h, m, s) = (
        secs_of_day / 3600,
        (secs_of_day % 3600) / 60,
        secs_of_day % 60,
    );
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let yr = if mo <= 2 { y + 1 } else { y };
    format!("{yr:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

pub async fn run(args: cli::RunArgs) -> Result<(), u8> {
    let layout = Layout::from_data_dir(
        &args.data_dir,
        args.install_dir.as_deref(),
        args.password_file.as_deref(),
        args.log_file.as_deref(),
    );
    logging::init(Some(&layout.sidecar_log));
    tracing::info!(
        "sidecar run starting, data_dir={}",
        layout.data_dir.display()
    );

    // 1. 维护锁（§13.3/§13.7.1：冲突 = 已有实例/维护进程占用）
    let _lock = match MaintenanceLock::try_acquire(&layout.lock_file) {
        Ok(l) => l,
        Err(AcquireError::Conflict) => {
            eprintln!(
                "[sidecar] 维护锁冲突：qTrading 或另一维护进程正在使用 {}；请勿重复启动",
                layout.data_dir.display()
            );
            return Err(exit_codes::LOCK_CONFLICT);
        }
        Err(AcquireError::Io(e)) => {
            eprintln!(
                "[sidecar] 维护锁获取失败 {}: {e}",
                layout.lock_file.display()
            );
            return Err(exit_codes::LOCK_CONFLICT);
        }
    };

    // 2. preflight 资源预检（§7.2，exit 15）
    if let Err(f) = preflight::run(&layout) {
        eprintln!("[sidecar] preflight [{}] {f}", f.check_name());
        return Err(exit_codes::PREFLIGHT_FAILED);
    }

    // 3. bundled binaries 原子 setup（§16.2，exit 10）
    setup::ensure_binaries(&layout, &|msg| tracing::info!("{msg}")).await?;

    // 4. initdb 红线守卫（§7.2 AI-16，exit 40）
    let dir_state = setup::guard_data_dir(&layout.data_dir)?;

    // 5. 密码（§7.4：Fresh 生成/复用；Existing 缺失 → exit 16，§13.7.8/§13.7.46）
    let password = match password::read_password_file(&layout.password_file) {
        Some(p) => p,
        None => match dir_state {
            DataDirState::Fresh => {
                let p = password::generate_password();
                password::write_password_file(&layout.password_file, &p).map_err(|e| {
                    eprintln!(
                        "[sidecar] password file 写入失败 {}: {e}",
                        layout.password_file.display()
                    );
                    exit_codes::PASSWORD_FAILED
                })?;
                p
            }
            DataDirState::Existing => {
                eprintln!(
                    "[sidecar] 密码文件缺失但 PGDATA 已存在（{}）：无法认证。\
                     若密码丢失请先走 reset-password 流程，禁止自动重置（§13.7.8）。",
                    layout.data_dir.display()
                );
                return Err(exit_codes::PASSWORD_FAILED);
            }
        },
    };
    logging::register_secret(&password);

    // 6. Fresh → initdb（exit 11）；安全基线幂等写入（Fresh/Existing 均执行，§13.7.33 漂移自修复）
    if dir_state == DataDirState::Fresh {
        tracing::info!("initdb --data-checksums initializing cluster");
        setup::run_initdb(&layout, &args.username).await?;
    }
    if let Err(e) = setup::write_security_baseline(&layout.data_dir, &args.listen) {
        eprintln!("[sidecar] 安全基线写入失败: {e}");
        return Err(exit_codes::START_FAILED);
    }

    // 7. stale postmaster.pid 处理（§7.2）；活实例占用 PGDATA → exit 50
    if let Some(info) = pgbin::read_postmaster_pid(&layout.data_dir) {
        if pgbin::process_alive(info.pid) {
            eprintln!(
                "[sidecar] PostgreSQL 已运行于该 PGDATA (pid {})，禁止双实例启动。\n\
                 这通常是上次 sidecar 异常退出（崩溃/被 kill）后残留的 postgres 进程。\n\
                 请先执行 `qtrading-pg-sidecar stop --data-dir {}` 清理残留进程，再重试 run。",
                info.pid,
                layout.data_dir.display()
            );
            return Err(exit_codes::LOCK_CONFLICT);
        }
        tracing::warn!("stale postmaster.pid (pid {}) removed", info.pid);
        let _ = std::fs::remove_file(layout.data_dir.join("postmaster.pid"));
    }

    // 8. 启动（端口冲突重试 ≤3；crash recovery → exit 40；其余 exit 12）
    let (postgresql, port) = start_with_retry(&layout, &args, &password).await?;
    let pg_pid = pgbin::read_postmaster_pid(&layout.data_dir).map(|i| i.pid);

    // 9. 健康检查（§7.5，exit 13；28P01 认证失败 → exit 16）。失败须清理已启动的 postgres。
    if let Err(code) = health_check(&layout, &args, &password, port).await {
        stop_after_failed_start(&layout, pg_pid, "health check failed").await;
        return Err(code);
    }

    // 9.5 kill fallback 后额外系统目录完整性检查（§7.3 / MAJ-1）
    // 上次 stop_mode=kill_fallback 时，crash recovery 可能掩盖数据页损坏；
    // 追加 pg_catalog 系统目录查询，任一失败或返回 0 → exit 13。
    {
        let prev_stop_mode = state::read(&layout.state_file)
            .and_then(|s| s.last_stop_mode)
            .unwrap_or_default();
        if prev_stop_mode == "kill_fallback" {
            tracing::warn!("上次停止模式为 kill_fallback，执行系统目录完整性检查（§7.3 / MAJ-1）");
            if let Err(code) = post_kill_fallback_check(&layout, &args, &password, port).await {
                stop_after_failed_start(&layout, pg_pid, "post kill_fallback check failed").await;
                return Err(code);
            }
            tracing::info!("系统目录完整性检查通过");
        }
    }

    // 10. create database（exit 14/16）
    if let Err(code) = ensure_database(&postgresql, &args.database).await {
        stop_after_failed_start(&layout, pg_pid, "create database failed").await;
        return Err(code);
    }

    // 11. runtime state → running（继承 kill_fallback_count 历史，§13.7.48）
    {
        let mut st = state::read(&layout.state_file).unwrap_or_default();
        st.status = "running".to_string();
        st.postgres_version = Some(setup::bundled_pg_version().to_string());
        st.port = Some(port);
        st.postgres_pid = pg_pid;
        st.sidecar_pid = Some(std::process::id());
        st.started_at_utc = Some(utc_now_iso8601());
        st.last_start_error = None;
        st.data_checksums = pgbin::read_control_data(&layout.install_dir, &layout.data_dir)
            .map(|c| c.data_checksums);
        if let Err(e) = state::write(&layout.state_file, &st) {
            tracing::warn!("runtime state write failed: {e}");
        }
    }

    // 12. ready JSON：stdout 第一行且此前无任何 stdout 写入（§6.2）
    let ready = ReadyJson::new(
        setup::bundled_pg_version().to_string(),
        args.listen.clone(),
        port,
        args.database.clone(),
        args.username.clone(),
        layout.data_dir.to_string_lossy().replace('\\', "/"),
        pg_pid,
    );
    if protocol::print_json_line(&ready).is_err() {
        // stdout 管道已坏（父进程死亡）→ 无监督对象，停止 postgres 后退出
        stop_after_failed_start(&layout, pg_pid, "stdout pipe broken before ready").await;
        return Err(exit_codes::HEALTH_CHECK_FAILED);
    }
    tracing::info!(
        "ready on {}:{} (postgres pid {:?})",
        args.listen,
        port,
        pg_pid
    );

    // 13. 监督循环 → 结束原因
    let end = supervise(&layout, &args, pg_pid).await;

    // 14. 分级停止（§7.3）+ state 收尾 + exit 事件
    let stop_outcome = pgbin::graded_stop(&layout.install_dir, &layout.data_dir, pg_pid).await;
    {
        let mut st = state::read(&layout.state_file).unwrap_or_default();
        st.status = match &end {
            SuperviseEnd::PostgresDied => "failed".to_string(),
            SuperviseEnd::Shutdown(_) => if stop_outcome.stopped {
                "stopped"
            } else {
                "failed"
            }
            .to_string(),
        };
        if matches!(end, SuperviseEnd::PostgresDied) {
            st.last_start_error =
                Some("postgres process died unexpectedly during supervision".to_string());
        }
        st.last_stop_mode = Some(stop_outcome.mode.as_str().to_string());
        if stop_outcome.mode == StopMode::KillFallback {
            st.kill_fallback_count += 1; // §13.7.48 异常终止累计
        } else if stop_outcome.stopped {
            st.kill_fallback_count = 0;
        }
        st.postgres_pid = None;
        if let Err(e) = state::write(&layout.state_file, &st) {
            tracing::warn!("runtime state write failed: {e}");
        }
    }

    match end {
        SuperviseEnd::PostgresDied => {
            emit_event(EventJson::exit("postgres_died", exit_codes::POSTGRES_DIED));
            Err(exit_codes::POSTGRES_DIED)
        }
        SuperviseEnd::Shutdown(reason) => {
            if !stop_outcome.stopped {
                eprintln!("[sidecar] postgres 未能停止（kill fallback 失败）");
                emit_event(EventJson::exit("stop_failed", exit_codes::STOP_FAILED));
                return Err(exit_codes::STOP_FAILED);
            }
            emit_event(EventJson::exit(reason, 0));
            Ok(())
        }
    }
}

enum SuperviseEnd {
    /// 父进程消失/信号 → graceful stop，exit 0
    Shutdown(&'static str),
    /// postgres 意外退出（不自动重启，§6.3）→ exit 60
    PostgresDied,
}

/// 监督循环：父进程检测（stdin EOF 主用，--parent-pid 轮询辅助）+ postgres 存活 + 磁盘低水位告警。
async fn supervise(layout: &Layout, args: &cli::RunArgs, pg_pid: Option<u32>) -> SuperviseEnd {
    use tokio::io::AsyncReadExt;

    // 父进程死亡 → stdin pipe 关闭 → 读到 EOF。读到数据忽略（协议未定输入）。
    let stdin_eof = async {
        let mut stdin = tokio::io::stdin();
        let mut buf = [0u8; 256];
        loop {
            match stdin.read(&mut buf).await {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
        }
    };
    tokio::pin!(stdin_eof);

    let sigterm = async {
        #[cfg(unix)]
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut s) => {
                s.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
        #[cfg(not(unix))]
        std::future::pending::<()>().await
    };
    tokio::pin!(sigterm);

    let mut parent_tick = tokio::time::interval(Duration::from_millis(500));
    let mut pg_tick = tokio::time::interval(Duration::from_secs(1));
    let mut disk_tick = tokio::time::interval(Duration::from_secs(30));

    loop {
        tokio::select! {
            _ = &mut stdin_eof => {
                tracing::info!("parent pipe EOF (parent gone); graceful stop");
                return SuperviseEnd::Shutdown("parent_gone");
            }
            _ = tokio::signal::ctrl_c() => {
                tracing::info!("ctrl-c received; graceful stop");
                return SuperviseEnd::Shutdown("signal");
            }
            _ = &mut sigterm => {
                tracing::info!("SIGTERM received; graceful stop");
                return SuperviseEnd::Shutdown("signal");
            }
            _ = parent_tick.tick(), if args.parent_pid.is_some() => {
                if let Some(ppid) = args.parent_pid {
                    if !pgbin::process_alive(ppid) {
                        tracing::info!("parent pid {ppid} gone; graceful stop");
                        return SuperviseEnd::Shutdown("parent_gone");
                    }
                }
            }
            _ = pg_tick.tick() => {
                if let Some(pid) = pg_pid {
                    if !pgbin::process_alive(pid) {
                        tracing::error!("postgres pid {pid} died unexpectedly");
                        return SuperviseEnd::PostgresDied;
                    }
                }
            }
            _ = disk_tick.tick() => {
                if let Some(free) = preflight::free_space(&layout.data_dir) {
                    if free < preflight::RUNTIME_WARN_FREE_BYTES {
                        emit_event(EventJson::warning(
                            "disk_low",
                            format!("数据卷剩余空间 {}MB < 100MB，请尽快清理或备份（§13.7.5）", free / (1024 * 1024)),
                        ));
                    }
                }
            }
        }
    }
}

fn emit_event(mut event: EventJson) {
    event.message = logging::sanitize(&event.message);
    if let Err(e) = protocol::print_json_line(&event) {
        tracing::warn!("stdout event write failed: {e}");
    }
}

async fn start_with_retry(
    layout: &Layout,
    args: &cli::RunArgs,
    password: &str,
) -> Result<(PostgreSQL, u16), u8> {
    let mut attempt = 0u8;
    loop {
        attempt += 1;
        let port = if args.port == 0 {
            pick_free_port(&args.listen).map_err(|e| {
                eprintln!("[sidecar] 随机端口分配失败: {e}");
                exit_codes::START_FAILED
            })?
        } else {
            args.port
        };
        // 清理历史 start.log，避免旧的端口冲突/recovery 特征干扰本次判定
        let _ = std::fs::remove_file(layout.data_dir.join("start.log"));

        let mut postgresql = build_postgresql(layout, &args.listen, &args.username, password, port);
        match postgresql.start().await {
            Ok(()) => {
                tracing::info!("postgres started on {}:{}", args.listen, port);
                return Ok((postgresql, port));
            }
            Err(e) => {
                if pgbin::start_log_indicates_recovery_failure(&layout.data_dir) {
                    eprintln!(
                        "[sidecar] crash recovery 失败（pg_control/WAL 损坏嫌疑，§13.7.36）：\
                         禁止自动 pg_resetwal；请运行 doctor 诊断并走恢复流程"
                    );
                    record_start_failure(layout, "crash recovery failed");
                    return Err(exit_codes::DATA_DIR_ABNORMAL);
                }
                if pgbin::start_log_indicates_port_conflict(&layout.data_dir)
                    && args.port == 0
                    && attempt < MAX_PORT_ATTEMPTS
                {
                    tracing::warn!(
                        "port {port} 冲突，重新随机重试 ({attempt}/{MAX_PORT_ATTEMPTS})"
                    );
                    continue;
                }
                eprintln!("[sidecar] postgres start failed: {e}");
                record_start_failure(layout, &format!("start failed: {e}"));
                return Err(exit_codes::START_FAILED);
            }
        }
    }
}

/// 健康检查（§7.5）：select version() / current_database() / select 1。
async fn health_check(
    layout: &Layout,
    args: &cli::RunArgs,
    password: &str,
    port: u16,
) -> Result<(), u8> {
    for sql in ["select version()", "select current_database()", "select 1"] {
        pgbin::psql(
            &layout.install_dir,
            &args.listen,
            port,
            &args.username,
            "postgres",
            password,
            sql,
            Duration::from_secs(10),
        )
        .await
        .map_err(|e| {
            let msg = e.to_string();
            if msg.contains("28P01") || msg.contains("password authentication failed") {
                eprintln!(
                    "[sidecar] 认证失败（密码与 cluster 不匹配，§13.7.46）：\
                     改回原 data_dir、走 reset-password、或改用外置模式，三选一"
                );
                exit_codes::PASSWORD_FAILED
            } else {
                eprintln!("[sidecar] health check failed ({sql}): {msg}");
                exit_codes::HEALTH_CHECK_FAILED
            }
        })?;
    }
    Ok(())
}

/// kill fallback 后的系统目录完整性检查（§7.3 / MAJ-1）。
///
/// kill fallback (SIGQUIT) 导致 PostgreSQL 立即退出，crash recovery 重放 WAL
/// 可能让集群进入"in production"状态，但系统目录页损坏仍可能导致后续 SQL 失败。
/// 追加 pg_catalog 三张核心系统表存在性 + 非空检查：
/// - pg_database：数据库列表（至少含 postgres/template1）
/// - pg_namespace：命名空间（至少含 pg_catalog）
/// - pg_class：所有表/索引/视图元数据（至少含系统表本身）
///
/// 任一查询失败或返回 0 → exit 13（HEALTH_CHECK_FAILED）。
async fn post_kill_fallback_check(
    layout: &Layout,
    args: &cli::RunArgs,
    password: &str,
    port: u16,
) -> Result<(), u8> {
    // 使用 count(*) 而非存在性检查：count 返回数值，psql 输出可解析；
    // 0 行表示系统目录为空（极端损坏），同样视为失败。
    let checks: [(&str, &str); 3] = [
        ("pg_database", "select count(*) from pg_catalog.pg_database"),
        (
            "pg_namespace",
            "select count(*) from pg_catalog.pg_namespace",
        ),
        ("pg_class", "select count(*) from pg_catalog.pg_class"),
    ];
    for (name, sql) in checks {
        let out = pgbin::psql(
            &layout.install_dir,
            &args.listen,
            port,
            &args.username,
            "postgres",
            password,
            sql,
            Duration::from_secs(10),
        )
        .await
        .map_err(|e| {
            eprintln!("[sidecar] kill fallback 后系统目录检查失败 ({name}): {e}（§7.3 / MAJ-1）");
            exit_codes::HEALTH_CHECK_FAILED
        })?;
        // psql 输出形如 " count \n-------\n     N\n(1 row)"
        // 取输出中的数字部分（trim 后第一行非空数字）
        let count: i64 = out
            .lines()
            .map(|l| l.trim())
            .find(|l| !l.is_empty() && l.chars().all(|c| c.is_ascii_digit()))
            .and_then(|l| l.parse().ok())
            .unwrap_or(0);
        if count <= 0 {
            eprintln!(
                "[sidecar] kill fallback 后系统目录 {name} 为空或损坏 (count={count})，\
                 禁止继续启动以免扩大损坏（§7.3 / MAJ-1）"
            );
            return Err(exit_codes::HEALTH_CHECK_FAILED);
        }
        tracing::info!("系统目录 {name} count={count} OK");
    }
    Ok(())
}

async fn ensure_database(postgresql: &PostgreSQL, database: &str) -> Result<(), u8> {
    let classify = |e: postgresql_embedded::Error| -> u8 {
        let msg = e.to_string();
        if msg.contains("28P01") || msg.contains("password authentication failed") {
            eprintln!("[sidecar] 认证失败（密码与 cluster 不匹配，§13.7.46）");
            exit_codes::PASSWORD_FAILED
        } else {
            eprintln!("[sidecar] create database failed: {msg}");
            exit_codes::CREATE_DATABASE_FAILED
        }
    };
    match postgresql.database_exists(database).await {
        Ok(true) => Ok(()),
        Ok(false) => postgresql.create_database(database).await.map_err(classify),
        Err(e) => Err(classify(e)),
    }
}

/// 启动后阶段失败（健康检查/建库/stdout 断裂）的清理：尽力停止，避免留下无主 postgres。
/// 同时记录 start failure 到 state.json，供 doctor/用户排查（OBS-1）。
async fn stop_after_failed_start(layout: &Layout, pg_pid: Option<u32>, err: &str) {
    let _ = pgbin::graded_stop(&layout.install_dir, &layout.data_dir, pg_pid).await;
    record_start_failure(layout, err);
}

fn record_start_failure(layout: &Layout, err: &str) {
    let mut st = state::read(&layout.state_file).unwrap_or_default();
    st.status = "failed".to_string();
    st.last_start_error = Some(err.chars().take(500).collect());
    let _ = state::write(&layout.state_file, &st);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_now_format_is_iso8601_z() {
        let ts = utc_now_iso8601();
        assert_eq!(ts.len(), 20);
        assert!(ts.ends_with('Z'));
        assert_eq!(&ts[4..5], "-");
        assert_eq!(&ts[10..11], "T");
    }

    #[test]
    fn free_port_is_bindable_and_nonzero() {
        let port = pick_free_port("127.0.0.1").unwrap();
        assert!(port > 0);
        // 返回的端口刚刚释放，通常可立即绑定（TOCTOU 可接受，真实冲突由启动重试兜底）
        assert!(TcpListener::bind(("127.0.0.1", port)).is_ok());
    }
}
