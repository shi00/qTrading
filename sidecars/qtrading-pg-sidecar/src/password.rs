//! URL-safe 密码生成与 password file 读写（pg_plan §7.4 / §13.7.45）。
//!
//! 字符集仅 RFC 3986 unreserved（`A-Za-z0-9-_.~`），长度 32，从源头避免
//! `postgresql://user:pwd@host/...` 拼接破坏 URL 解析。

use rand::Rng;
use std::path::Path;

pub const PASSWORD_LEN: usize = 32;
const CHARSET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~";

pub fn generate_password() -> String {
    let mut rng = rand::rng();
    (0..PASSWORD_LEN)
        .map(|_| CHARSET[rng.random_range(0..CHARSET.len())] as char)
        .collect()
}

pub fn read_password_file(path: &Path) -> Option<String> {
    let content = std::fs::read_to_string(path).ok()?;
    let trimmed = content.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// 写入 password file；Unix 强制 0600（§7.4），Windows 依赖 NTFS 用户私有 ACL。
pub fn write_password_file(path: &Path, password: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    use std::io::Write;
    file.write_all(password.as_bytes())?;
    file.sync_all()
}

/// 校验既有 password file 权限（仅 Unix；Windows 跳过，exit 15 由调用方决定）。
#[cfg(unix)]
pub fn password_file_perms_ok(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.permissions().mode() & 0o077 == 0)
        .unwrap_or(true) // 文件不存在：首次运行场景，由生成流程负责权限
}

#[cfg(windows)]
pub fn password_file_perms_ok(_path: &Path) -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("qts-pw-test-{}-{}", std::process::id(), name))
    }

    #[test]
    fn generated_password_is_url_safe_unreserved() {
        for _ in 0..100 {
            let pwd = generate_password();
            assert_eq!(pwd.len(), PASSWORD_LEN);
            assert!(
                pwd.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"-_.~".contains(&b)),
                "密码含 URL 保留字符: {pwd}"
            );
        }
    }

    #[test]
    fn generated_passwords_are_random() {
        assert_ne!(generate_password(), generate_password());
    }

    #[test]
    fn password_round_trips_through_url_encoding() {
        // §17.3 #56：URL 拼接后经 quote 可被无损解析
        let pwd = generate_password();
        let url = format!(
            "postgresql://postgres:{}@127.0.0.1:5432/qtrading",
            urlencoding_like(&pwd)
        );
        let parsed = parse_userinfo(&url).unwrap();
        assert_eq!(parsed, format!("postgres:{pwd}"));
    }

    fn urlencoding_like(s: &str) -> String {
        // unreserved 字符集无需编码，quote(safe="") 原样返回
        s.to_string()
    }

    fn parse_userinfo(url: &str) -> Option<String> {
        let after = url.strip_prefix("postgresql://")?;
        let end = after.find('@')?;
        Some(after[..end].to_string())
    }

    #[test]
    fn write_then_read_roundtrip() {
        let dir = unique_tmp("roundtrip");
        let path = dir.join("password");
        write_password_file(&path, "TestPwd-1_2.3~").unwrap();
        assert_eq!(read_password_file(&path).as_deref(), Some("TestPwd-1_2.3~"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn read_missing_or_empty_returns_none() {
        let dir = unique_tmp("missing");
        assert_eq!(read_password_file(&dir.join("nope")), None);
        let path = dir.join("empty");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, "  \n").unwrap();
        assert_eq!(read_password_file(&path), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn written_file_has_0600() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_tmp("perms");
        let path = dir.join("password");
        write_password_file(&path, "x".repeat(32).as_str()).unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert!(password_file_perms_ok(&path));
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(!password_file_perms_ok(&path));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
