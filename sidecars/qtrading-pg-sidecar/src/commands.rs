//! status / stop / version 命令（pg_plan §6.1）。
//!
//! - status：只读诊断（state.json + postmaster.pid 活性 + 锁探测），输出 status JSON。
//! - stop：PGDATA 级操作，须持有维护锁（sidecar 运行中 → exit 50，提示先关闭 qTrading）。
//! - version：构建元数据 + 自 sha256（§15.3/§15.7，`--json` 输出 version JSON）。

use crate::exit_codes;
use crate::lockfile::{AcquireError, MaintenanceLock};
use crate::logging;
use crate::paths::Layout;
use crate::pgbin::{self, StopMode};
use crate::protocol;
use crate::state;
use serde::Serialize;
use sha2::Digest;
use std::path::Path;

// ---- status ----

#[derive(Serialize, Debug)]
struct StatusJson {
    schema: &'static str,
    data_dir: String,
    /// "running" | "stopped" | "failed" | "not_initialized"
    status: String,
    postgres_version: Option<String>,
    port: Option<u16>,
    postgres_pid: Option<u32>,
    postgres_alive: bool,
    sidecar_pid: Option<u32>,
    started_at_utc: Option<String>,
    last_stop_mode: Option<String>,
    kill_fallback_count: u32,
    last_start_error: Option<String>,
    data_checksums: Option<bool>,
    /// 维护锁被持有 = sidecar 或维护进程活跃中
    lock_held: bool,
    /// "ok" | "missing" | "corrupted"
    state_file: &'static str,
}

/// state.json 三态判定：缺失与损坏对调用方语义不同（doctor 报告依据）。
pub(crate) fn state_file_condition(layout: &Layout) -> (&'static str, Option<state::RuntimeState>) {
    if !layout.state_file.exists() {
        return ("missing", None);
    }
    match state::read(&layout.state_file) {
        Some(st) => ("ok", Some(st)),
        None => ("corrupted", None),
    }
}

pub fn status(data_dir: &Path) -> Result<(), u8> {
    let layout = Layout::from_data_dir(data_dir, None, None, None);
    let (state_cond, st) = state_file_condition(&layout);
    let pm = pgbin::read_postmaster_pid(&layout.data_dir);
    let alive = pm.as_ref().is_some_and(|i| pgbin::process_alive(i.pid));
    let initialized = layout.data_dir.join("PG_VERSION").exists();

    let status_str = if alive {
        "running"
    } else if !initialized {
        "not_initialized"
    } else {
        st.as_ref().map_or("stopped", |s| s.status.as_str())
    };

    // 活实例以 postmaster.pid 为准（sidecar 崩溃后 state 可能滞后）；否则回退 state.json
    let (port, pid) = if alive {
        (
            pm.as_ref()
                .and_then(|i| i.port)
                .or(st.as_ref().and_then(|s| s.port)),
            pm.as_ref().map(|i| i.pid),
        )
    } else {
        (st.as_ref().and_then(|s| s.port), None)
    };

    let json = StatusJson {
        schema: protocol::STATUS_SCHEMA,
        data_dir: layout.data_dir.to_string_lossy().replace('\\', "/"),
        status: status_str.to_string(),
        postgres_version: st.as_ref().and_then(|s| s.postgres_version.clone()),
        port,
        postgres_pid: pid,
        postgres_alive: alive,
        sidecar_pid: st.as_ref().and_then(|s| s.sidecar_pid),
        started_at_utc: st.as_ref().and_then(|s| s.started_at_utc.clone()),
        last_stop_mode: st.as_ref().and_then(|s| s.last_stop_mode.clone()),
        kill_fallback_count: st.as_ref().map_or(0, |s| s.kill_fallback_count),
        last_start_error: st.as_ref().and_then(|s| s.last_start_error.clone()),
        data_checksums: st.as_ref().and_then(|s| s.data_checksums),
        lock_held: MaintenanceLock::is_held(&layout.lock_file),
        state_file: state_cond,
    };
    protocol::print_json_line(&json).map_err(|e| {
        eprintln!("[sidecar] status JSON 输出失败: {e}");
        exit_codes::ARGUMENT_ERROR
    })
}

// ---- stop ----

pub async fn stop(data_dir: &Path) -> Result<(), u8> {
    let layout = Layout::from_data_dir(data_dir, None, None, None);
    logging::init(Some(&layout.sidecar_log));

    // PGDATA 级操作：sidecar（qTrading 运行中）持锁 → 拒绝越权停止（§13.3）
    let _lock = match MaintenanceLock::try_acquire(&layout.lock_file) {
        Ok(l) => l,
        Err(AcquireError::Conflict) => {
            eprintln!(
                "[sidecar] qTrading 或维护进程正在使用 {}；请先正常关闭 qTrading（其 sidecar 会负责停止 PostgreSQL）",
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

    let pm = pgbin::read_postmaster_pid(&layout.data_dir);
    match pm {
        Some(info) if pgbin::process_alive(info.pid) => {
            let outcome =
                pgbin::graded_stop(&layout.install_dir, &layout.data_dir, Some(info.pid)).await;
            let mut st = state::read(&layout.state_file).unwrap_or_default();
            st.last_stop_mode = Some(outcome.mode.as_str().to_string());
            if outcome.mode == StopMode::KillFallback {
                st.kill_fallback_count += 1;
            } else if outcome.stopped {
                st.kill_fallback_count = 0;
            }
            if outcome.stopped {
                st.status = "stopped".to_string();
                st.postgres_pid = None;
                if let Err(e) = state::write(&layout.state_file, &st) {
                    tracing::warn!("runtime state write failed: {e}");
                }
                eprintln!(
                    "[sidecar] PostgreSQL 已停止 (mode={}, pid={})",
                    outcome.mode.as_str(),
                    info.pid
                );
                Ok(())
            } else {
                st.status = "failed".to_string();
                let _ = state::write(&layout.state_file, &st);
                eprintln!(
                    "[sidecar] stop 失败：kill fallback 未能终止 postgres (pid {})",
                    info.pid
                );
                Err(exit_codes::STOP_FAILED)
            }
        }
        _ => {
            // 未运行：清理 stale postmaster.pid（sidecar 崩溃残留），保证下次可启动
            if pm.is_some() {
                tracing::warn!(
                    "stale postmaster.pid removed (pid {})",
                    pm.as_ref().map_or(0, |i| i.pid)
                );
                let _ = std::fs::remove_file(layout.data_dir.join("postmaster.pid"));
            }
            // state 停留在 running 是崩溃残留，修正为 stopped
            if let Some(mut st) = state::read(&layout.state_file) {
                if st.status == "running" {
                    st.status = "stopped".to_string();
                    st.postgres_pid = None;
                    let _ = state::write(&layout.state_file, &st);
                }
            }
            eprintln!("[sidecar] PostgreSQL 未在运行");
            Ok(())
        }
    }
}

// ---- version ----

#[derive(Serialize, Debug)]
struct VersionJson {
    schema: &'static str,
    sidecar_version: &'static str,
    protocol_version: &'static str,
    target: &'static str,
    profile: &'static str,
    git_sha: &'static str,
    rustc_version: &'static str,
    postgresql_embedded_version: &'static str,
    postgres_version: &'static str,
    postgres_binary_source: &'static str,
    license: &'static str,
    build_time_utc: &'static str,
    build_time_unix: u64,
    self_sha256: Option<String>,
}

fn build_version_json() -> VersionJson {
    VersionJson {
        schema: protocol::VERSION_SCHEMA,
        sidecar_version: env!("CARGO_PKG_VERSION"),
        protocol_version: protocol::PROTOCOL_VERSION,
        target: env!("SIDECAR_TARGET"),
        profile: env!("SIDECAR_PROFILE"),
        git_sha: env!("SIDECAR_GIT_SHA"),
        rustc_version: env!("SIDECAR_RUSTC_VERSION"),
        postgresql_embedded_version: env!("SIDECAR_CRATE_VERSION"),
        postgres_version: env!("SIDECAR_POSTGRES_VERSION"),
        postgres_binary_source: "theseus-bundled",
        license: "PostgreSQL",
        build_time_utc: env!("SIDECAR_BUILD_TIME_UTC"),
        build_time_unix: env!("SIDECAR_BUILD_TIME_UNIX").parse().unwrap_or(0),
        self_sha256: self_sha256(),
    }
}

/// 运行时自哈希：供 §15.3 manifest 校验与 §7.2 Python 侧启动前校验复用。
fn self_sha256() -> Option<String> {
    let exe = std::env::current_exe().ok()?;
    let bytes = std::fs::read(exe).ok()?;
    let digest = sha2::Sha256::digest(&bytes);
    let mut hex = String::with_capacity(digest.len() * 2);
    for b in digest {
        hex.push_str(&format!("{b:02x}"));
    }
    Some(hex)
}

pub fn version(json: bool) -> Result<(), u8> {
    let v = build_version_json();
    if json {
        return protocol::print_json_line(&v).map_err(|e| {
            eprintln!("[sidecar] version JSON 输出失败: {e}");
            exit_codes::ARGUMENT_ERROR
        });
    }
    println!("qtrading-pg-sidecar {}", v.sidecar_version);
    println!("protocol: {}", v.protocol_version);
    println!(
        "postgres: {} ({})",
        v.postgres_version, v.postgres_binary_source
    );
    println!("target: {} ({})", v.target, v.profile);
    println!("git: {}", v.git_sha);
    println!("rustc: {}", v.rustc_version);
    println!("postgresql_embedded: {}", v.postgresql_embedded_version);
    println!("built: {}", v.build_time_utc);
    if let Some(sha) = &v.self_sha256 {
        println!("sha256: {sha}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("qts-cmd-test-{}-{}", std::process::id(), name))
    }

    #[test]
    fn state_file_condition_tri_state() {
        let dir = unique_tmp("tri");
        let layout = Layout::from_data_dir(&dir.join("postgres/17/data"), None, None, None);
        assert_eq!(state_file_condition(&layout).0, "missing");
        std::fs::create_dir_all(layout.state_file.parent().unwrap()).unwrap();
        std::fs::write(&layout.state_file, "{bad json").unwrap();
        assert_eq!(state_file_condition(&layout).0, "corrupted");
        state::write(&layout.state_file, &state::RuntimeState::default()).unwrap();
        let (cond, st) = state_file_condition(&layout);
        assert_eq!(cond, "ok");
        assert_eq!(st.unwrap().status, "stopped");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn status_json_schema_on_fresh_dir() {
        // fresh 目录：not_initialized，无 state，无锁
        let dir = unique_tmp("status-fresh");
        let data_dir = dir.join("postgres/17/data");
        // status 写 stdout 不便直接断言；改为验证其不报错返回
        assert!(status(&data_dir).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn stop_on_fresh_dir_is_noop_ok() {
        let dir = unique_tmp("stop-noop");
        let data_dir = dir.join("postgres/17/data");
        assert!(stop(&data_dir).await.is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn stop_conflict_when_lock_held() {
        let dir = unique_tmp("stop-conflict");
        let data_dir = dir.join("postgres/17/data");
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        let _lock = MaintenanceLock::try_acquire(&layout.lock_file).unwrap();
        assert_eq!(stop(&data_dir).await, Err(exit_codes::LOCK_CONFLICT));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn version_json_contains_build_metadata() {
        let v = build_version_json();
        let json = serde_json::to_value(&v).unwrap();
        assert_eq!(json["schema"], protocol::VERSION_SCHEMA);
        assert_eq!(json["protocol_version"], "v1");
        assert_eq!(json["postgres_version"], "17.2.0");
        assert_eq!(json["license"], "PostgreSQL");
        assert_eq!(json["postgres_binary_source"], "theseus-bundled");
        assert!(json["build_time_unix"].as_u64().unwrap() > 0);
        assert!(!json["git_sha"].as_str().unwrap().is_empty());
        assert!(!json["target"].as_str().unwrap().is_empty());
        // 自 sha256：测试进程可读取自身 exe
        let sha = json["self_sha256"].as_str().unwrap();
        assert_eq!(sha.len(), 64);
        assert!(sha.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn version_plain_and_json_both_ok() {
        assert!(version(false).is_ok());
        assert!(version(true).is_ok());
    }

    #[test]
    fn self_sha256_stable_within_process() {
        assert_eq!(self_sha256(), self_sha256());
    }
}
