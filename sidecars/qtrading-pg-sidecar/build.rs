//! 构建元数据采集（pg_plan §6.1 `version --json` / §15.7 可校验信息）。
//!
//! 通过 `cargo:rustc-env` 注入，代码侧用 `env!`/`option_env!` 消费：
//! - SIDECAR_TARGET / SIDECAR_PROFILE / SIDECAR_BUILD_TIME_UNIX / SIDECAR_BUILD_TIME_UTC
//! - SIDECAR_GIT_SHA（CI 经 SIDECAR_GIT_SHA 注入；本地回退 `git rev-parse`，失败为 "unknown"）
//! - SIDECAR_POSTGRES_VERSION（.cargo/config.toml [env] POSTGRESQL_VERSION，精确 pin `=17.2.0`）
//! - SIDECAR_CRATE_VERSION（postgresql_embedded 版本，解析 Cargo.lock）
//! - SIDECAR_RUSTC_VERSION

use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=SIDECAR_GIT_SHA");
    println!("cargo:rerun-if-env-changed=POSTGRESQL_VERSION");

    let target = std::env::var("TARGET").unwrap_or_else(|_| "unknown".into());
    let profile = std::env::var("PROFILE").unwrap_or_else(|_| "unknown".into());
    println!("cargo:rustc-env=SIDECAR_TARGET={target}");
    println!("cargo:rustc-env=SIDECAR_PROFILE={profile}");

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    println!("cargo:rustc-env=SIDECAR_BUILD_TIME_UNIX={now}");
    println!(
        "cargo:rustc-env=SIDECAR_BUILD_TIME_UTC={}",
        iso8601_utc(now)
    );

    let git_sha = std::env::var("SIDECAR_GIT_SHA")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(git_rev_parse)
        .unwrap_or_else(|| "unknown".into());
    println!("cargo:rustc-env=SIDECAR_GIT_SHA={git_sha}");

    // .cargo/config.toml [env] POSTGRESQL_VERSION 会传入 build script 环境
    let pg_version = std::env::var("POSTGRESQL_VERSION").unwrap_or_else(|_| "unknown".into());
    println!(
        "cargo:rustc-env=SIDECAR_POSTGRES_VERSION={}",
        pg_version.trim_start_matches('=')
    );

    println!(
        "cargo:rustc-env=SIDECAR_CRATE_VERSION={}",
        crate_version_from_lock()
    );
    println!("cargo:rustc-env=SIDECAR_RUSTC_VERSION={}", rustc_version());
}

fn git_rev_parse() -> Option<String> {
    let out = Command::new("git")
        .args(["rev-parse", "--short=12", "HEAD"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let sha = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if sha.is_empty() {
        None
    } else {
        Some(sha)
    }
}

fn crate_version_from_lock() -> String {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default();
    let lock = match std::fs::read_to_string(format!("{manifest_dir}/Cargo.lock")) {
        Ok(c) => c,
        Err(_) => return "unknown".into(),
    };
    // Cargo.lock 结构：[[package]] name = "postgresql_embedded" 下一行 version = "x.y.z"
    let mut found = false;
    for line in lock.lines() {
        let line = line.trim();
        if line == r#"name = "postgresql_embedded""# {
            found = true;
            continue;
        }
        if found {
            if let Some(v) = line
                .strip_prefix("version = \"")
                .and_then(|s| s.strip_suffix('"'))
            {
                return v.to_string();
            }
            if line.starts_with('[') {
                break;
            }
        }
    }
    "unknown".into()
}

fn rustc_version() -> String {
    let rustc = std::env::var("RUSTC").unwrap_or_else(|_| "rustc".into());
    Command::new(rustc)
        .arg("--version")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".into())
}

/// unix epoch 秒 → ISO 8601 UTC（Howard Hinnant civil-from-days 算法，避免引入 chrono 依赖）。
fn iso8601_utc(epoch_secs: u64) -> String {
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

#[cfg(test)]
mod tests {
    use super::iso8601_utc;

    #[test]
    fn epoch_zero_is_1970() {
        assert_eq!(iso8601_utc(0), "1970-01-01T00:00:00Z");
    }

    #[test]
    fn known_timestamp() {
        // 2026-07-20T00:00:00Z = 1784505600
        assert_eq!(&iso8601_utc(1_784_505_600)[..10], "2026-07-20");
    }

    #[test]
    fn end_of_day_boundary() {
        assert_eq!(iso8601_utc(86_399), "1970-01-01T23:59:59Z");
        assert_eq!(iso8601_utc(86_400), "1970-01-02T00:00:00Z");
    }
}
