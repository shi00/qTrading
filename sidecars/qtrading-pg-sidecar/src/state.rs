//! runtime state 持久化（pg_plan §11 `postgres/16/runtime/state.json`）。
//! 原子写入（tmp + rename），损坏时容错为 None（doctor 负责报告）。

use serde::{Deserialize, Serialize};
use std::path::Path;

pub const STATE_SCHEMA: &str = "qtrading.embedded_postgres.runtime_state.v1";

/// restore 切换前的集群实例诊断归档（历史累计，不参与当前实例态统计）。
///
/// 旧集群的 `kill_fallback_count` / `last_stop_mode` / `last_start_error` 描述的是
/// 已被换下的那套数据目录，不能丢给新集群冒充（也不能悄悄清零），故整体迁入历史。
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct ArchivedCluster {
    /// 旧集群实例代际；旧格式 state 无该字段时为 `None`。
    #[serde(default)]
    pub cluster_instance_id: Option<String>,
    #[serde(default)]
    pub kill_fallback_count: u32,
    #[serde(default)]
    pub last_stop_mode: Option<String>,
    #[serde(default)]
    pub last_start_error: Option<String>,
    /// 归档时刻（UTC ISO8601）。
    #[serde(default)]
    pub archived_at_utc: Option<String>,
}

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
    /// 当前集群实例代际（restore 切换后重新生成；旧格式 state 无该字段）。
    #[serde(default)]
    pub cluster_instance_id: Option<String>,
    /// 历史集群实例诊断归档（restore 切换时迁入）。
    #[serde(default)]
    pub archived_clusters: Vec<ArchivedCluster>,
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
            cluster_instance_id: None,
            archived_clusters: Vec::new(),
        }
    }
}

impl RuntimeState {
    /// restore 原子切换后把 state 重置为新集群实例。
    ///
    /// - 旧实例的当前态诊断（`kill_fallback_count` / `last_stop_mode` /
    ///   `last_start_error`）整体归档到 `archived_clusters`，避免被新集群继承或被
    ///   悄悄丢弃；
    /// - 新实例获得新的 `cluster_instance_id`，运行态字段复位。
    ///
    /// `kill_fallback_count` 归零是**新实例的累计起点**，旧值已随实例归档。
    pub fn reset_for_restored_cluster(&mut self, now_utc: &str, postgres_version: &str) {
        let has_history = self.kill_fallback_count > 0
            || self.last_stop_mode.is_some()
            || self.last_start_error.is_some()
            || self.cluster_instance_id.is_some();
        if has_history {
            self.archived_clusters.push(ArchivedCluster {
                cluster_instance_id: self.cluster_instance_id.clone(),
                kill_fallback_count: self.kill_fallback_count,
                last_stop_mode: self.last_stop_mode.clone(),
                last_start_error: self.last_start_error.clone(),
                archived_at_utc: Some(now_utc.to_string()),
            });
        }
        self.cluster_instance_id = Some(new_cluster_instance_id(now_utc));
        self.status = "stopped".to_string();
        self.postgres_version = Some(postgres_version.to_string());
        self.port = None;
        self.postgres_pid = None;
        self.sidecar_pid = None;
        self.started_at_utc = None;
        self.last_stop_mode = None;
        self.last_start_error = None;
        self.kill_fallback_count = 0;
    }
}

/// 生成集群实例代际标识（时刻 + 纳秒 + 进程号，进程内/跨进程均可区分）。
fn new_cluster_instance_id(now_utc: &str) -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{now_utc}-{nanos:x}-{:x}", std::process::id())
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
        assert!(s.cluster_instance_id.is_none());
        assert!(s.archived_clusters.is_empty());
    }

    /// restore 切换：旧实例诊断归档而不是伪造清零，新实例获得新代际。
    #[test]
    fn reset_for_restored_cluster_archives_old_instance_diagnostics() {
        let mut s = RuntimeState {
            status: "failed".into(),
            cluster_instance_id: Some("old-generation".into()),
            kill_fallback_count: 3,
            last_stop_mode: Some("kill_fallback".into()),
            last_start_error: Some("postgres died".into()),
            postgres_pid: Some(4321),
            port: Some(55432),
            sidecar_pid: Some(999),
            started_at_utc: Some("2026-01-01T00:00:00Z".into()),
            ..Default::default()
        };
        s.reset_for_restored_cluster("2026-02-02T00:00:00Z", "16.9");

        assert_eq!(s.archived_clusters.len(), 1);
        let archived = &s.archived_clusters[0];
        assert_eq!(
            archived.cluster_instance_id.as_deref(),
            Some("old-generation")
        );
        assert_eq!(archived.kill_fallback_count, 3);
        assert_eq!(archived.last_stop_mode.as_deref(), Some("kill_fallback"));
        assert_eq!(archived.last_start_error.as_deref(), Some("postgres died"));
        assert_eq!(
            archived.archived_at_utc.as_deref(),
            Some("2026-02-02T00:00:00Z")
        );

        // 新实例：运行态与旧实例证据全部复位，旧计数只存在于归档。
        assert_eq!(s.status, "stopped");
        assert_eq!(s.postgres_version.as_deref(), Some("16.9"));
        assert!(s.port.is_none());
        assert!(s.postgres_pid.is_none());
        assert!(s.sidecar_pid.is_none());
        assert!(s.started_at_utc.is_none());
        assert!(s.last_stop_mode.is_none());
        assert!(s.last_start_error.is_none());
        assert_eq!(s.kill_fallback_count, 0);
        assert_ne!(s.cluster_instance_id.as_deref(), Some("old-generation"));
        assert!(
            s.cluster_instance_id
                .as_deref()
                .is_some_and(|id| !id.is_empty()),
            "新实例必须生成非空代际标识"
        );
    }

    /// 每次 restore 都生成新的 cluster 代际（同秒内也不复用）。
    #[test]
    fn reset_for_restored_cluster_assigns_distinct_instance_ids() {
        let mut first = RuntimeState::default();
        first.reset_for_restored_cluster("2026-02-02T00:00:00Z", "16.9");
        let mut second = RuntimeState::default();
        second.reset_for_restored_cluster("2026-02-02T00:00:00Z", "16.9");
        assert_ne!(first.cluster_instance_id, second.cluster_instance_id);

        // 无历史可归档时不产生空归档记录。
        assert!(first.archived_clusters.is_empty());
    }

    /// 旧格式 state（无新增字段）必须可读，不得因缺字段改写为「未知/损坏」。
    #[test]
    fn legacy_state_without_new_fields_deserializes() {
        let legacy = r#"{
          "schema": "qtrading.embedded_postgres.runtime_state.v1",
          "status": "stopped",
          "postgres_version": "16.9",
          "port": null,
          "postgres_pid": null,
          "sidecar_pid": null,
          "started_at_utc": null,
          "last_stop_mode": "smart",
          "kill_fallback_count": 2,
          "last_start_error": null,
          "data_checksums": true,
          "last_migration_failed": false
        }"#;
        let s: RuntimeState = serde_json::from_str(legacy).expect("legacy state must deserialize");
        assert_eq!(s.kill_fallback_count, 2);
        assert_eq!(s.last_stop_mode.as_deref(), Some("smart"));
        assert!(s.cluster_instance_id.is_none());
        assert!(s.archived_clusters.is_empty());
    }

    /// 归档记录随 state.json 落盘并可读回（迁移后旧诊断不会消失）。
    #[test]
    fn archived_clusters_roundtrip_through_state_file() {
        let dir = unique_tmp("archive-roundtrip");
        let path = dir.join("state.json");
        let mut s = RuntimeState {
            kill_fallback_count: 1,
            last_stop_mode: Some("kill_fallback".into()),
            ..Default::default()
        };
        s.reset_for_restored_cluster("2026-02-02T00:00:00Z", "16.9");
        write(&path, &s).unwrap();

        let loaded = read(&path).expect("state must be readable");
        assert_eq!(loaded.archived_clusters, s.archived_clusters);
        assert_eq!(loaded.cluster_instance_id, s.cluster_instance_id);
        assert_eq!(loaded.kill_fallback_count, 0);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
