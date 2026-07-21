//! stdout 机器协议（pg_plan §6.2）：第 1 行 ready JSON，其后 JSON-lines 事件。
//! schema 字段全部固定常量，主版本号供 Python 端启动时校验（§13.7.19 / AI-40）。

use serde::Serialize;
use std::io::Write;

pub const PROTOCOL_VERSION: &str = "v1";

pub const READY_SCHEMA: &str = "qtrading.embedded_postgres.run.ready.v1";
pub const EVENT_WARNING_SCHEMA: &str = "qtrading.embedded_postgres.event.warning.v1";
pub const EVENT_EXIT_SCHEMA: &str = "qtrading.embedded_postgres.event.exit.v1";
pub const STATUS_SCHEMA: &str = "qtrading.embedded_postgres.status.v1";
pub const DOCTOR_SCHEMA: &str = "qtrading.embedded_postgres.doctor.v1";
pub const VERSION_SCHEMA: &str = "qtrading.embedded_postgres.version.v1";

/// §6.2 ready JSON（`run` 启动成功后的 stdout 第一行）。
#[derive(Serialize, Debug)]
pub struct ReadyJson {
    pub schema: &'static str,
    pub status: &'static str,
    pub postgres_version: String,
    pub host: String,
    pub port: u16,
    pub database: String,
    pub username: String,
    pub password_source: &'static str,
    /// R9：URL 中密码段固定脱敏为 `***`（明文只经 password file 传递，不上 stdout）。
    pub url: String,
    pub data_dir: String,
    pub sidecar_pid: u32,
    pub pid: Option<u32>,
}

impl ReadyJson {
    pub fn new(
        postgres_version: String,
        host: String,
        port: u16,
        database: String,
        username: String,
        data_dir: String,
        postgres_pid: Option<u32>,
    ) -> Self {
        Self {
            schema: READY_SCHEMA,
            status: "running",
            url: format!("postgresql://{username}:***@{host}:{port}/{database}"),
            postgres_version,
            host,
            port,
            database,
            username,
            password_source: "password_file",
            data_dir,
            sidecar_pid: std::process::id(),
            pid: postgres_pid,
        }
    }
}

/// 第 2 行起的 JSON-lines 状态事件。`message` 必须在构造前脱敏（logging::sanitize）。
#[derive(Serialize, Debug)]
pub struct EventJson {
    pub schema: &'static str,
    pub event: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phase: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

impl EventJson {
    pub fn warning(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            schema: EVENT_WARNING_SCHEMA,
            event: "warning",
            phase: None,
            code: Some(code.into()),
            message: message.into(),
            data: None,
        }
    }

    pub fn exit(reason: &'static str, code: u8) -> Self {
        Self {
            schema: EVENT_EXIT_SCHEMA,
            event: "exit",
            phase: None,
            code: Some(code.to_string()),
            message: reason.to_string(),
            data: None,
        }
    }
}

/// 单行写入 stdout（父子进程私有管道；调用方负责 message 脱敏）。
pub fn print_json_line<T: Serialize>(value: &T) -> std::io::Result<()> {
    let line = serde_json::to_string(value)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let stdout = std::io::stdout();
    let mut lock = stdout.lock();
    writeln!(lock, "{line}")?;
    lock.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_json_matches_plan_schema() {
        let ready = ReadyJson::new(
            "17.2.0".into(),
            "127.0.0.1".into(),
            54832,
            "qtrading".into(),
            "postgres".into(),
            "C:/Users/u/AppData/Local/qTrading/postgres/17/data".into(),
            Some(12345),
        );
        let v = serde_json::to_value(&ready).unwrap();
        assert_eq!(v["schema"], "qtrading.embedded_postgres.run.ready.v1");
        assert_eq!(v["status"], "running");
        assert_eq!(v["postgres_version"], "17.2.0");
        assert_eq!(v["host"], "127.0.0.1");
        assert_eq!(v["port"], 54832);
        assert_eq!(v["database"], "qtrading");
        assert_eq!(v["username"], "postgres");
        assert_eq!(v["password_source"], "password_file");
        // R9：密码段必须脱敏
        assert_eq!(
            v["url"],
            "postgresql://postgres:***@127.0.0.1:54832/qtrading"
        );
        assert!(!v["url"].as_str().unwrap().contains(":17."));
        assert_eq!(v["pid"], 12345);
        assert!(v["sidecar_pid"].as_u64().unwrap() > 0);
    }

    #[test]
    fn ready_json_pid_optional() {
        let ready = ReadyJson::new(
            "17.2.0".into(),
            "127.0.0.1".into(),
            5432,
            "qtrading".into(),
            "postgres".into(),
            "/tmp/d".into(),
            None,
        );
        let v = serde_json::to_value(&ready).unwrap();
        assert!(v["pid"].is_null());
    }

    #[test]
    fn event_schemas_fixed() {
        let w = EventJson::warning("disk_low", "free space < 100MB");
        let v = serde_json::to_value(&w).unwrap();
        assert_eq!(v["schema"], EVENT_WARNING_SCHEMA);
        assert_eq!(v["code"], "disk_low");

        let e = EventJson::exit("parent_gone", 0);
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["schema"], EVENT_EXIT_SCHEMA);
        assert_eq!(v["code"], "0");
        assert_eq!(v["message"], "parent_gone");
    }

    #[test]
    fn protocol_version_is_v1() {
        assert_eq!(PROTOCOL_VERSION, "v1");
        for schema in [
            READY_SCHEMA,
            EVENT_WARNING_SCHEMA,
            EVENT_EXIT_SCHEMA,
            STATUS_SCHEMA,
            DOCTOR_SCHEMA,
            VERSION_SCHEMA,
        ] {
            assert!(schema.ends_with(".v1"), "schema 主版本漂移: {schema}");
        }
    }
}
