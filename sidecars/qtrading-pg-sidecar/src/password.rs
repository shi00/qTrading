//! URL-safe 密码生成与 password file 读写（pg_plan §7.4 / §13.7.45）。
//!
//! 字符集仅 RFC 3986 unreserved（`A-Za-z0-9-_.~`），长度 32，从源头避免
//! `postgresql://user:pwd@host/...` 拼接破坏 URL 解析。

use rand::Rng;
use std::path::Path;

pub const PASSWORD_LEN: usize = 32;
const CHARSET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~";
/// F5（检视 06）：Windows DPAPI 加密密码文件的 ASCII 标记前缀（`PGPW2:<hex blob>`）。
/// 无该前缀的文件按明文读（Unix，或 Windows 升级前的旧格式）。
pub const DPAPI_PREFIX: &str = "PGPW2:";

pub fn generate_password() -> String {
    let mut rng = rand::rng();
    (0..PASSWORD_LEN)
        .map(|_| CHARSET[rng.random_range(0..CHARSET.len())] as char)
        .collect()
}

/// F5（检视 06）：hex 编码小写（DPAPI blob 文本化，避免引入额外 cargo 依赖）。
/// 仅在 Windows DPAPI 加密路径（`encode_password_file`）使用，须与之一致地按平台限定。
#[cfg(windows)]
fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0x0f) as usize] as char);
    }
    s
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) {
        return None;
    }
    let mut out = Vec::with_capacity(s.len() / 2);
    for pair in s.as_bytes().chunks_exact(2) {
        let hi = (pair[0] as char).to_digit(16)?;
        let lo = (pair[1] as char).to_digit(16)?;
        out.push(((hi << 4) | lo) as u8);
    }
    Some(out)
}

/// F5（检视 06）：明文密码 → 文件字节序列。Windows 用 DPAPI 加密，Unix 保持明文。
#[cfg(windows)]
fn encode_password_file(password: &str) -> std::io::Result<Vec<u8>> {
    let blob = dpapi::protect(password.as_bytes())?;
    let mut out = DPAPI_PREFIX.as_bytes().to_vec();
    out.extend_from_slice(hex_encode(&blob).as_bytes());
    Ok(out)
}

/// F5（检视 06）：`PGPW2:` 前缀后的 hex → 明文字符串。
fn decode_password_file(hexpart: &str) -> Option<String> {
    let blob = hex_decode(hexpart)?;
    let plain = dpapi_unprotect(&blob).ok()?;
    String::from_utf8(plain).ok()
}

#[cfg(windows)]
fn dpapi_unprotect(blob: &[u8]) -> std::io::Result<Vec<u8>> {
    dpapi::unprotect(blob)
}

#[cfg(not(windows))]
fn dpapi_unprotect(_blob: &[u8]) -> std::io::Result<Vec<u8>> {
    Err(std::io::Error::new(
        std::io::ErrorKind::InvalidInput,
        "DPAPI-encrypted password file on a non-Windows platform",
    ))
}

pub fn read_password_file(path: &Path) -> Option<String> {
    let content = std::fs::read_to_string(path).ok()?;
    let trimmed = content.trim();
    if trimmed.is_empty() {
        return None;
    }
    match trimmed.strip_prefix(DPAPI_PREFIX) {
        // Windows DPAPI 加密格式（F5，检视 06）
        Some(hexpart) => decode_password_file(hexpart),
        // 明文格式：Unix，或 Windows 升级前的旧文件
        None => Some(trimmed.to_string()),
    }
}

/// 写入 password file；Unix 强制 0600（§7.4），Windows 用 DPAPI 加密（F5，检视 06）。
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
    #[cfg(windows)]
    let bytes = encode_password_file(password)?;
    #[cfg(unix)]
    let bytes = password.as_bytes().to_vec();
    file.write_all(&bytes)?;
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

#[cfg(windows)]
/// F5（检视 06）：Windows DPAPI 包裹（`CryptProtectData`/`CryptUnprotectData`）。
/// DPAPI 无夹带 entropy + `CRYPTPROTECT_UI_FORBIDDEN`，blob 绑定当前 Windows 用户+机器，
/// 被复制到其他机器/账号无法解密。输出 blob 由系统 LocalAlloc 分配，须 `LocalFree` 释放。
mod dpapi {
    use std::ptr;

    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    pub fn protect(data: &[u8]) -> std::io::Result<Vec<u8>> {
        let in_blob = CRYPT_INTEGER_BLOB {
            cbData: data.len().try_into().unwrap_or(u32::MAX),
            pbData: data.as_ptr() as *mut u8,
        };
        let mut out_blob = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: ptr::null_mut(),
        };
        let ok = unsafe {
            CryptProtectData(
                &in_blob,
                ptr::null(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut out_blob,
            )
        };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        let out = unsafe { std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize) }
            .to_vec();
        unsafe { LocalFree(out_blob.pbData as _) };
        Ok(out)
    }

    pub fn unprotect(data: &[u8]) -> std::io::Result<Vec<u8>> {
        let in_blob = CRYPT_INTEGER_BLOB {
            cbData: data.len().try_into().unwrap_or(u32::MAX),
            pbData: data.as_ptr() as *mut u8,
        };
        let mut out_blob = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: ptr::null_mut(),
        };
        let ok = unsafe {
            CryptUnprotectData(
                &in_blob,
                ptr::null_mut(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                0,
                &mut out_blob,
            )
        };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        let out = unsafe { std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize) }
            .to_vec();
        unsafe { LocalFree(out_blob.pbData as _) };
        Ok(out)
    }
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

    #[test]
    fn hex_roundtrips() {
        #[cfg(windows)]
        {
            let data = b"\x00\x01\xfe\xffhello\x80";
            assert_eq!(hex_decode(&hex_encode(data)).unwrap(), data);
            assert_eq!(hex_encode(b""), "");
        }
        assert_eq!(hex_decode("").unwrap(), Vec::<u8>::new());
    }

    #[test]
    fn hex_decode_rejects_malformed() {
        assert!(hex_decode("a").is_none()); // 奇数长度
        assert!(hex_decode("az").is_none()); // 非法字符
        assert!(hex_decode("gg").is_none());
    }

    #[test]
    fn plaintext_file_reads_as_is() {
        let dir = unique_tmp("plain");
        let path = dir.join("password");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, "PlainPwd-1_2.3~").unwrap();
        assert_eq!(
            read_password_file(&path).as_deref(),
            Some("PlainPwd-1_2.3~")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn dpapi_protect_unprotect_roundtrip() {
        let secret = b"hunter2-secret-payload";
        let blob = dpapi::protect(secret).unwrap();
        assert_ne!(blob, secret); // 密文不等于明文
        assert_eq!(dpapi::unprotect(&blob).unwrap(), secret);
        // blob 中不应直接出现明文字节
        assert!(!blob.windows(secret.len()).any(|w| w == secret.as_slice()));
    }

    #[cfg(windows)]
    #[test]
    fn write_then_read_uses_dpapi_format_on_windows() {
        let dir = unique_tmp("dpapi");
        let path = dir.join("password");
        let pwd = "DpapiPwd-1_2.3~";
        write_password_file(&path, pwd).unwrap();
        // 文件内容必须是标记前缀 + hex，而非明文
        let raw = std::fs::read_to_string(&path).unwrap();
        assert!(raw.starts_with(DPAPI_PREFIX));
        assert!(raw.len() > DPAPI_PREFIX.len());
        // 读取应解密回原文
        assert_eq!(read_password_file(&path).as_deref(), Some(pwd));
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
