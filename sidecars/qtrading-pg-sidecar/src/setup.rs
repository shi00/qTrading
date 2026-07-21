//! setup 原子性（pg_plan §16.2 AI-39）+ initdb 红线守卫（§7.2 AI-16）+ 安全基线（§7.4/§7.6）。
//!
//! - 解压走 `<install-dir>.tmp-<pid>/`，关键工具校验通过后原子 rename，写 `.setup-complete`；
//!   中断残留（tmp 目录 / 无 marker 的 install dir）下次启动清理重做，禁止半截继续。
//! - initdb 由 sidecar 显式执行（`--data-checksums` + scram），不委托 crate 默认初始化
//!   （postgresql_embedded 0.21 `initialize()` 不带 `--data-checksums`，§7.4 AI-18 不满足）。
//! - `pg_hba.conf` 整文件覆盖、`postgresql.conf` 受管块幂等写入（§13.7.33 漂移自修复）。

use crate::exit_codes;
use crate::paths::Layout;
use crate::pgbin;
use postgresql_embedded::{PostgreSQL, SettingsBuilder};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::time::Duration;

pub const SETUP_COMPLETE_MARKER: &str = ".setup-complete";
/// 解压期防 initdb 哨兵文件内容（crate `is_initialized()` 只看 postgresql.conf 存在性，
/// 见 postgresql_embedded 0.21 postgresql.rs `fn is_initialized`）。
const EXTRACTION_GUARD: &str = "# qtrading-pg-sidecar extraction guard\n";

pub const PG_HBA_TEMPLATE: &str = "\
# qtrading-pg-sidecar managed - do not edit manually
host all postgres 127.0.0.1/32 scram-sha-256
host all postgres ::1/128      scram-sha-256
";

/// §7.6 受管参数（值与 doctor 校验共用此单一来源）。
pub const MANAGED_PARAMS: &[(&str, &str)] = &[
    ("max_connections", "100"),
    ("shared_buffers", "128MB"),
    ("fsync", "on"),
    ("synchronous_commit", "on"),
    ("full_page_writes", "on"),
    ("password_encryption", "scram-sha-256"),
    ("autovacuum", "on"),
    ("log_min_duration_statement", "1000"),
    ("work_mem", "4MB"),
    ("maintenance_work_mem", "64MB"),
    ("lc_messages", "C"),
    ("lc_monetary", "C"),
];

const BLOCK_BEGIN: &str =
    "# >>> qtrading-pg-sidecar managed block (do not edit between markers) >>>";
const BLOCK_END: &str = "# <<< qtrading-pg-sidecar managed block <<<";

/// bundled PostgreSQL 主版本（build.rs 注入，如 "17.2.0" → 17）。
pub fn bundled_pg_major() -> Option<u32> {
    env!("SIDECAR_POSTGRES_VERSION")
        .split('.')
        .next()?
        .parse()
        .ok()
}

pub fn bundled_pg_version() -> &'static str {
    env!("SIDECAR_POSTGRES_VERSION")
}

/// PG_VERSION 文件内容（"17\n"）→ 主版本号。
pub fn read_pg_major(data_dir: &Path) -> Option<u32> {
    std::fs::read_to_string(data_dir.join("PG_VERSION"))
        .ok()?
        .trim()
        .parse()
        .ok()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DataDirState {
    /// 不存在/为空 → 允许 initdb
    Fresh,
    /// 已初始化且版本匹配 → 禁止 initdb
    Existing,
}

/// §7.2 AI-16 守卫：非空无 PG_VERSION / 版本不匹配 / 关键文件缺失 → exit 40。
pub fn guard_data_dir(data_dir: &Path) -> Result<DataDirState, u8> {
    if !data_dir.exists() {
        return Ok(DataDirState::Fresh);
    }
    let mut entries: Vec<PathBuf> = std::fs::read_dir(data_dir)
        .map_err(|e| {
            eprintln!("[sidecar] data dir unreadable {}: {e}", data_dir.display());
            exit_codes::DATA_DIR_ABNORMAL
        })?
        .filter_map(|e| e.ok().map(|x| x.path()))
        .collect();

    // 解压哨兵独占目录：上次 setup 解压期崩溃残留，安全清理（非用户数据）
    if entries.len() == 1
        && entries[0]
            .file_name()
            .is_some_and(|n| n == "postgresql.conf")
        && std::fs::read_to_string(&entries[0]).is_ok_and(|c| c == EXTRACTION_GUARD)
    {
        let _ = std::fs::remove_file(&entries[0]);
        entries.clear();
    }

    if entries.is_empty() {
        return Ok(DataDirState::Fresh);
    }

    if !data_dir.join("PG_VERSION").exists() {
        eprintln!(
            "[sidecar] data dir {} 非空但 PG_VERSION 缺失（疑似 initdb 中断残留或误指目录）；\
             禁止对非空目录再跑 initdb。请运行 doctor 诊断，确认内容后手动清理或从备份恢复。",
            data_dir.display()
        );
        return Err(exit_codes::DATA_DIR_ABNORMAL);
    }

    match (read_pg_major(data_dir), bundled_pg_major()) {
        (Some(found), Some(want)) if found != want => {
            eprintln!(
                "[sidecar] PG_VERSION 主版本 {found} 与内置 PostgreSQL {want} 不匹配；\
                 大版本升级需走迁移流程，禁止直接启动。"
            );
            return Err(exit_codes::DATA_DIR_ABNORMAL);
        }
        (None, _) => {
            eprintln!("[sidecar] PG_VERSION 不可解析：{}", data_dir.display());
            return Err(exit_codes::DATA_DIR_ABNORMAL);
        }
        _ => {}
    }

    // §13.7.37 关键文件缺失
    for required in ["global/pg_control", "base", "postgresql.conf"] {
        if !data_dir.join(required).exists() {
            eprintln!(
                "[sidecar] 关键文件缺失 {required}（{}）：数据目录不完整，请运行 doctor 诊断并走恢复流程。",
                data_dir.display()
            );
            return Err(exit_codes::DATA_DIR_ABNORMAL);
        }
    }
    Ok(DataDirState::Existing)
}

/// 计算 7 个关键工具的 sha256（AI-39 完整性校验）。返回 (工具名, sha256) 列表。
fn compute_tools_sha256(install_dir: &Path) -> Vec<(String, String)> {
    pgbin::REQUIRED_TOOLS
        .iter()
        .filter_map(|name| {
            let path = pgbin::tool_path(install_dir, name);
            std::fs::read(&path).ok().map(|bytes| {
                let mut hasher = Sha256::new();
                hasher.update(&bytes);
                (name.to_string(), format!("{:x}", hasher.finalize()))
            })
        })
        .collect()
}

/// marker 中记录的版本 + sha256 与当前 bundled 版本 + 实际文件一致才算 setup 完成
/// （§13.7.23 小版本升级后重做 + AI-39 完整性校验）。
fn marker_valid(marker: &Path, install_dir: &Path) -> bool {
    let Ok(content) = std::fs::read_to_string(marker) else {
        return false;
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(&content) else {
        return false;
    };
    let Some(pg_ver) = v.get("postgres_version").and_then(|x| x.as_str()) else {
        return false;
    };
    if pg_ver != bundled_pg_version() {
        return false;
    }
    let Some(recorded) = v.get("tools_sha256").and_then(|x| x.as_array()) else {
        return false; // 旧 marker 无 sha256 → 重做
    };
    let actual = compute_tools_sha256(install_dir);
    if actual.len() != pgbin::REQUIRED_TOOLS.len() {
        return false; // 工具缺失
    }
    actual.iter().all(|(name, hash)| {
        recorded.iter().any(|entry| {
            entry.get("name").and_then(|n| n.as_str()) == Some(name.as_str())
                && entry.get("sha256").and_then(|h| h.as_str()) == Some(hash.as_str())
        })
    })
}

/// 确保 bundled binaries 可用（原子解压 + marker）。失败映射 exit 10。
pub async fn ensure_binaries(layout: &Layout, progress: &dyn Fn(&str)) -> Result<(), u8> {
    let marker = layout.install_dir.join(SETUP_COMPLETE_MARKER);
    if marker.exists()
        && marker_valid(&marker, &layout.install_dir)
        && pgbin::missing_tools(&layout.install_dir).is_empty()
    {
        return Ok(());
    }

    // 清理历史 tmp 目录（含其他 pid 残留）
    let install_name = layout
        .install_dir
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned();
    if let Some(parent) = layout.install_dir.parent() {
        if let Ok(read) = std::fs::read_dir(parent) {
            for entry in read.filter_map(|e| e.ok()) {
                let name = entry.file_name().to_string_lossy().into_owned();
                if name.starts_with(&format!("{install_name}.tmp-")) {
                    let _ = std::fs::remove_dir_all(entry.path());
                }
            }
        }
    }

    // 无 marker / 工具不全的 install dir = 半截状态，禁止继续，整体重做
    if layout.install_dir.exists() {
        tracing::warn!(
            "install dir 缺少 {} 或关键工具，重做 setup: {}",
            SETUP_COMPLETE_MARKER,
            layout.install_dir.display()
        );
        std::fs::remove_dir_all(&layout.install_dir).map_err(|e| {
            eprintln!("[sidecar] half install dir cleanup failed: {e}");
            exit_codes::SETUP_FAILED
        })?;
    }

    let tmp = layout
        .install_dir
        .with_file_name(format!("{install_name}.tmp-{}", std::process::id()));
    progress("extracting postgresql binaries");

    // crate 只负责解压；用哨兵 postgresql.conf 让其 is_initialized() 短路，跳过其 initdb
    let data_dir = layout.data_dir.clone();
    let guard_path = data_dir.join("postgresql.conf");
    let planted_guard = !guard_path.exists();
    if planted_guard {
        std::fs::create_dir_all(&data_dir).map_err(|e| {
            eprintln!("[sidecar] data dir create failed: {e}");
            exit_codes::SETUP_FAILED
        })?;
        std::fs::write(&guard_path, EXTRACTION_GUARD).map_err(|e| {
            eprintln!("[sidecar] extraction guard write failed: {e}");
            exit_codes::SETUP_FAILED
        })?;
    }

    let settings = SettingsBuilder::new()
        .installation_dir(tmp.clone())
        .data_dir(data_dir.clone())
        .password_file(layout.password_file.clone())
        .timeout(Some(Duration::from_secs(300)))
        .temporary(false)
        .build();
    let mut postgresql = PostgreSQL::new(settings);
    let setup_result = postgresql.setup().await;

    if planted_guard {
        let _ = std::fs::remove_file(&guard_path);
    }
    setup_result.map_err(|e| {
        eprintln!("[sidecar] bundled archive extraction failed: {e}");
        exit_codes::SETUP_FAILED
    })?;

    // crate 在 bundled exact version 下会把解压目标拼为 <tmp>/<version>（PostgreSQL::new），
    // 实际位置以 settings 回读为准；trust_installation_dir 不可用（会跳过 install）
    let extracted = postgresql.settings().installation_dir.clone();
    let missing = pgbin::missing_tools(&extracted);
    if !missing.is_empty() {
        eprintln!(
            "[sidecar] extraction incomplete, missing tools: {}",
            missing.join(", ")
        );
        let _ = std::fs::remove_dir_all(&tmp);
        return Err(exit_codes::SETUP_FAILED);
    }

    std::fs::rename(&extracted, &layout.install_dir).map_err(|e| {
        eprintln!("[sidecar] install dir atomic rename failed: {e}");
        exit_codes::SETUP_FAILED
    })?;
    // tmp 空壳（extracted 已迁出）；失败不致命，下次启动清理
    let _ = std::fs::remove_dir_all(&tmp);

    let tools_sha256: Vec<String> = compute_tools_sha256(&layout.install_dir)
        .into_iter()
        .map(|(name, sha256)| format!("{{\"name\":\"{name}\",\"sha256\":\"{sha256}\"}}"))
        .collect();
    let marker_content = format!(
        "{{\"postgres_version\":\"{}\",\"completed_at_utc\":\"{}\",\"tools_sha256\":[{}]}}\n",
        bundled_pg_version(),
        crate::run::utc_now_iso8601(),
        tools_sha256.join(",")
    );
    if let Err(e) = std::fs::write(&marker, marker_content) {
        // marker 写失败：下次启动按半截状态整体重做，不致命
        tracing::warn!("setup-complete marker write failed (will redo next run): {e}");
    }
    progress("postgresql binaries ready");
    Ok(())
}

/// 显式 initdb（§7.4）：--data-checksums + scram-sha-256 + UTF8。失败映射 exit 11。
pub async fn run_initdb(layout: &Layout, username: &str) -> Result<(), u8> {
    let data = layout.data_dir.to_string_lossy().into_owned();
    let pwfile = layout.password_file.to_string_lossy().into_owned();
    let out = pgbin::run_tool(
        &pgbin::tool_path(&layout.install_dir, "initdb"),
        &[
            "-D",
            &data,
            "-U",
            username,
            "--auth=scram-sha-256",
            "--auth-local=scram-sha-256",
            &format!("--pwfile={pwfile}"),
            "--encoding=UTF8",
            "--data-checksums",
            "--no-instructions",
        ],
        &[],
        Duration::from_secs(180),
    )
    .await
    .map_err(|e| {
        eprintln!("[sidecar] initdb exec failed: {e}");
        exit_codes::INITDB_FAILED
    })?;
    if !out.success() {
        eprintln!(
            "[sidecar] initdb failed (code {:?}): {}",
            out.code,
            out.stderr.trim()
        );
        return Err(exit_codes::INITDB_FAILED);
    }
    Ok(())
}

/// 安全基线幂等写入（§7.4/§7.6）：pg_hba 整文件覆盖 + postgresql.conf 受管块替换。
pub fn write_security_baseline(data_dir: &Path, listen: &str) -> std::io::Result<()> {
    std::fs::write(data_dir.join("pg_hba.conf"), PG_HBA_TEMPLATE)?;

    let conf_path = data_dir.join("postgresql.conf");
    let existing = std::fs::read_to_string(&conf_path).unwrap_or_default();
    std::fs::write(
        &conf_path,
        format!(
            "{}\n{}",
            strip_managed_block(&existing),
            managed_block(listen)
        ),
    )
}

fn managed_block(listen: &str) -> String {
    let mut block = String::from(BLOCK_BEGIN);
    block.push('\n');
    block.push_str(&format!("listen_addresses = '{listen}'\n"));
    for (k, v) in MANAGED_PARAMS {
        block.push_str(&format!("{k} = '{v}'\n"));
    }
    block.push_str(BLOCK_END);
    block.push('\n');
    block
}

fn strip_managed_block(content: &str) -> String {
    let mut out = String::with_capacity(content.len());
    let mut inside = false;
    for line in content.lines() {
        if line.trim() == BLOCK_BEGIN {
            inside = true;
            continue;
        }
        if line.trim() == BLOCK_END {
            inside = false;
            continue;
        }
        if !inside {
            out.push_str(line);
            out.push('\n');
        }
    }
    out.trim_end().to_string()
}

/// doctor 用：pg_hba 与模板逐字节一致（允许尾部空白差异）。
pub fn hba_matches_template(data_dir: &Path) -> bool {
    std::fs::read_to_string(data_dir.join("pg_hba.conf"))
        .map(|c| c.trim_end() == PG_HBA_TEMPLATE.trim_end())
        .unwrap_or(false)
}

/// doctor 用：返回受管块中缺失/漂移的参数名（空 = 无漂移）。
pub fn managed_block_drift(data_dir: &Path) -> Vec<&'static str> {
    let Ok(content) = std::fs::read_to_string(data_dir.join("postgresql.conf")) else {
        return MANAGED_PARAMS.iter().map(|(k, _)| *k).collect();
    };
    let mut drift = Vec::new();
    for (k, v) in MANAGED_PARAMS {
        let expect = format!("{k} = '{v}'");
        if !content.lines().any(|l| l.trim() == expect) {
            drift.push(*k);
        }
    }
    drift
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-setup-test-{}-{}", std::process::id(), name))
    }

    fn make_existing_cluster(dir: &Path) {
        std::fs::create_dir_all(dir.join("global")).unwrap();
        std::fs::create_dir_all(dir.join("base")).unwrap();
        std::fs::write(dir.join("PG_VERSION"), "17\n").unwrap();
        std::fs::write(dir.join("global/pg_control"), b"ctl").unwrap();
        std::fs::write(dir.join("postgresql.conf"), "# conf\n").unwrap();
    }

    #[test]
    fn guard_fresh_when_missing_or_empty() {
        let dir = unique_tmp("fresh");
        assert_eq!(guard_data_dir(&dir).unwrap(), DataDirState::Fresh);
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(guard_data_dir(&dir).unwrap(), DataDirState::Fresh);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn guard_existing_cluster_ok() {
        let dir = unique_tmp("existing");
        make_existing_cluster(&dir);
        assert_eq!(guard_data_dir(&dir).unwrap(), DataDirState::Existing);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn guard_rejects_nonempty_without_pg_version() {
        let dir = unique_tmp("no-pgver");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("random.txt"), "x").unwrap();
        assert_eq!(guard_data_dir(&dir), Err(exit_codes::DATA_DIR_ABNORMAL));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn guard_rejects_missing_critical_files() {
        let dir = unique_tmp("missing-critical");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("PG_VERSION"), "17\n").unwrap();
        // 无 global/pg_control
        assert_eq!(guard_data_dir(&dir), Err(exit_codes::DATA_DIR_ABNORMAL));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn guard_cleans_extraction_guard_remnant() {
        let dir = unique_tmp("guard-remnant");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("postgresql.conf"), EXTRACTION_GUARD).unwrap();
        assert_eq!(guard_data_dir(&dir).unwrap(), DataDirState::Fresh);
        assert!(!dir.join("postgresql.conf").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn managed_block_roundtrip_and_idempotent() {
        let dir = unique_tmp("baseline");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("postgresql.conf"), "# default conf\nport = 5432\n").unwrap();
        write_security_baseline(&dir, "127.0.0.1").unwrap();
        let first = std::fs::read_to_string(dir.join("postgresql.conf")).unwrap();
        assert!(first.contains("fsync = 'on'"));
        assert!(first.contains("password_encryption = 'scram-sha-256'"));
        assert!(first.contains("listen_addresses = '127.0.0.1'"));
        assert!(first.contains("port = 5432"));
        // 幂等：第二次写入不产生重复块
        write_security_baseline(&dir, "127.0.0.1").unwrap();
        let second = std::fs::read_to_string(dir.join("postgresql.conf")).unwrap();
        assert_eq!(first, second);
        assert_eq!(second.matches(BLOCK_BEGIN).count(), 1);
        assert!(managed_block_drift(&dir).is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn managed_block_drift_detects_tampering() {
        let dir = unique_tmp("drift");
        std::fs::create_dir_all(&dir).unwrap();
        write_security_baseline(&dir, "127.0.0.1").unwrap();
        let conf = std::fs::read_to_string(dir.join("postgresql.conf")).unwrap();
        std::fs::write(
            dir.join("postgresql.conf"),
            conf.replace("fsync = 'on'", "fsync = 'off'"),
        )
        .unwrap();
        let drift = managed_block_drift(&dir);
        assert!(drift.contains(&"fsync"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn hba_template_written_and_verified() {
        let dir = unique_tmp("hba");
        std::fs::create_dir_all(&dir).unwrap();
        write_security_baseline(&dir, "127.0.0.1").unwrap();
        assert!(hba_matches_template(&dir));
        let hba = std::fs::read_to_string(dir.join("pg_hba.conf")).unwrap();
        assert!(hba.contains("host all postgres 127.0.0.1/32 scram-sha-256"));
        assert!(!hba.contains("trust"));
        assert!(!hba.contains("0.0.0.0"));
        std::fs::write(dir.join("pg_hba.conf"), "host all all 0.0.0.0/0 trust\n").unwrap();
        assert!(!hba_matches_template(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn bundled_major_parses() {
        // build.rs 注入 "17.2.0"（.cargo/config.toml POSTGRESQL_VERSION="=17.2.0"）
        assert_eq!(bundled_pg_major(), Some(17));
        assert_eq!(bundled_pg_version(), "17.2.0");
    }

    #[test]
    fn marker_valid_checks_version_and_sha256() {
        let dir = unique_tmp("marker-valid");
        std::fs::create_dir_all(dir.join("bin")).unwrap();
        for name in pgbin::REQUIRED_TOOLS {
            std::fs::write(pgbin::tool_path(&dir, name), b"fake binary content").unwrap();
        }
        let marker = dir.join(SETUP_COMPLETE_MARKER);

        // 正确 marker：版本 + sha256 匹配
        let tools_sha256: Vec<String> = compute_tools_sha256(&dir)
            .into_iter()
            .map(|(name, sha256)| format!("{{\"name\":\"{name}\",\"sha256\":\"{sha256}\"}}"))
            .collect();
        let content = format!(
            "{{\"postgres_version\":\"{}\",\"completed_at_utc\":\"2026-01-01T00:00:00Z\",\"tools_sha256\":[{}]}}\n",
            bundled_pg_version(),
            tools_sha256.join(",")
        );
        std::fs::write(&marker, &content).unwrap();
        assert!(marker_valid(&marker, &dir));

        // 篡改工具文件 → sha256 不匹配
        std::fs::write(pgbin::tool_path(&dir, "initdb"), b"tampered").unwrap();
        assert!(!marker_valid(&marker, &dir));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn marker_valid_rejects_old_marker_without_sha256() {
        let dir = unique_tmp("marker-old");
        std::fs::create_dir_all(dir.join("bin")).unwrap();
        for name in pgbin::REQUIRED_TOOLS {
            std::fs::write(pgbin::tool_path(&dir, name), b"fake").unwrap();
        }
        let marker = dir.join(SETUP_COMPLETE_MARKER);
        // 旧格式 marker（无 tools_sha256 字段）→ 重做
        let content = format!(
            "{{\"postgres_version\":\"{}\",\"completed_at_utc\":\"2026-01-01T00:00:00Z\"}}\n",
            bundled_pg_version()
        );
        std::fs::write(&marker, &content).unwrap();
        assert!(!marker_valid(&marker, &dir));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
