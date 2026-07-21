//! runtime state 持久化（pg_plan §11 `postgres/17/runtime/state.json`）。
//! 原子写入（tmp + rename），损坏时容错为 None（doctor 负责报告）。

use serde::{Deserialize, Serialize};
use std::path::Path;

pub const STATE_SCHEMA: &str = "qtrading.embedded_postgres.runtime_state.v1";

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct RuntimeState {
    pub schema: String,
    /// "running" | "stopped" | "failed"
    pub status: String,
    pub postgres_version: Option<String>,
    pub port: Option<u16>,
    pub postgres_pid: Option<u32>,
    pub sidecar_pid: Option<u32>,
    pub started_at_utc: Option<String>,
    /// "smart" | "fast" | "kill_fallback"（§7.3 / §13.7.48）
    pub last_stop_mode: Option<String>,
    pub kill_fallback_count: u32,
    pub last_start_error: Option<String>,
    pub data_checksums: Option<bool>,
    pub last_migration_failed: bool,
}

impl Default for RuntimeState {
    fn default() -> Self {
        Self {
            schema: STATE_SCHEMA.to_string(),
            status: "stopped".to_string(),
            postgres_version: None,
            port: None,
            postgres_pid: None,
            sidecar_pid: None,
            started_at_utc: None,
            last_stop_mode: None,
            kill_fallback_count: 0,
            last_start_error: None,
            data_checksums: None,
            last_migration_failed: false,
        }
    }
}

pub fn read(path: &Path) -> Option<RuntimeState> {
    let content = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

/// tmp + rename 原子写入，避免中途断电留下半截 state（§13.7.44 同源策略）。
pub fn write(path: &Path, state: &RuntimeState) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension(format!("json.tmp-{}", std::process::id()));
    let content = serde_json::to_string_pretty(state)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(&tmp, content)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("qts-state-test-{}-{}", std::process::id(), name))
    }

    #[test]
    fn write_read_roundtrip() {
        let dir = unique_tmp("roundtrip");
        let path = dir.join("state.json");
        let state = RuntimeState {
            status: "running".into(),
            port: Some(55432),
            postgres_pid: Some(4321),
            last_stop_mode: Some("kill_fallback".into()),
            kill_fallback_count: 2,
            data_checksums: Some(true),
            ..Default::default()
        };
        write(&path, &state).unwrap();
        let loaded = read(&path).unwrap();
        assert_eq!(loaded.schema, STATE_SCHEMA);
        assert_eq!(loaded.status, "running");
        assert_eq!(loaded.port, Some(55432));
        assert_eq!(loaded.postgres_pid, Some(4321));
        assert_eq!(loaded.kill_fallback_count, 2);
        assert_eq!(loaded.data_checksums, Some(true));
        // 原子写不残留 tmp
        assert!(!dir
            .join(format!("state.json.tmp-{}", std::process::id()))
            .exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn read_missing_returns_none() {
        let dir = unique_tmp("missing");
        assert!(read(&dir.join("nope.json")).is_none());
    }

    #[test]
    fn read_corrupted_returns_none() {
        let dir = unique_tmp("corrupt");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("state.json");
        std::fs::write(&path, "{not json").unwrap();
        assert!(read(&path).is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn default_values_match_plan() {
        let s = RuntimeState::default();
        assert_eq!(s.status, "stopped");
        assert_eq!(s.kill_fallback_count, 0);
        assert!(!s.last_migration_failed);
        assert!(s.last_stop_mode.is_none());
    }
}
