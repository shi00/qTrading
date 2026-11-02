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

/// D16-T03：仅测试构建启用的故障注入（不进入发布二进制）。
///
/// 用于覆盖 root 无法模拟的 chmod/write 失败路径；线程本地状态避免并行测试串扰。
#[cfg(all(test, unix))]
mod fault_injection {
    use std::cell::Cell;

    #[derive(Clone, Copy, PartialEq, Eq)]
    pub enum Mode {
        None,
        Write,
        Chmod,
    }

    thread_local! {
        static MODE: Cell<Mode> = const { Cell::new(Mode::None) };
    }

    pub fn set(mode: Mode) {
        MODE.with(|m| m.set(mode));
    }

    pub fn current() -> Mode {
        MODE.with(Cell::get)
    }
}

/// 写入 password file；Unix 强制 0600（§7.4），Windows 用 DPAPI 加密（F5，检视 06）。
///
/// D16-F05：同目录临时文件写满并 fsync 后原子 rename，避免写入中断留下半截密码
/// （恢复目标 runtime 的凭据写入必须要么不存在、要么完整可用）；rename 失败时
/// 原文件保持不变，临时文件被清理。
pub fn write_password_file(path: &Path, password: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    #[cfg(windows)]
    let bytes = encode_password_file(password)?;
    #[cfg(unix)]
    let bytes = password.as_bytes().to_vec();

    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    let file_name = path.file_name().map_or_else(
        || "password".to_string(),
        |n| n.to_string_lossy().into_owned(),
    );
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let tmp = parent.join(format!(".{file_name}.tmp-{}-{nanos}", std::process::id()));

    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let write_result = (|| -> std::io::Result<()> {
        use std::io::Write;
        let mut file = options.open(&tmp)?;
        #[cfg(all(test, unix))]
        if fault_injection::current() == fault_injection::Mode::Write {
            return Err(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "injected password write failure (test-only)",
            ));
        }
        file.write_all(&bytes)?;
        file.sync_all()?;
        Ok(())
    })();
    if let Err(e) = write_result {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }

    if let Err(e) = std::fs::rename(&tmp, path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(e);
    }
    // 临时文件已按 0600 创建，rename 保留权限；显式收紧仍用于防御平台差异。
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        #[cfg(all(test, unix))]
        if fault_injection::current() == fault_injection::Mode::Chmod {
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "injected password chmod failure (test-only)",
            ));
        }
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
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

    /// D16-F05：原子写入（临时文件 + rename）不残留临时文件，重写覆盖旧凭据。
    #[test]
    fn atomic_write_leaves_no_temporary_files() {
        let dir = unique_tmp("atomic");
        let path = dir.join("password");
        write_password_file(&path, "FirstPwd-1_2.3~").unwrap();
        write_password_file(&path, "SecondPwd-1_2.3~").unwrap();
        let entries: Vec<std::ffi::OsString> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(
            entries,
            vec![std::ffi::OsString::from("password")],
            "原子写入不得残留临时文件"
        );
        assert_eq!(
            read_password_file(&path).as_deref(),
            Some("SecondPwd-1_2.3~")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-T03：测试构建注入故障；Drop 时复位，避免同线程后续用例串扰。
    #[cfg(unix)]
    struct FaultGuard;

    #[cfg(unix)]
    impl FaultGuard {
        fn set(mode: fault_injection::Mode) -> Self {
            fault_injection::set(mode);
            Self
        }
    }

    #[cfg(unix)]
    impl Drop for FaultGuard {
        fn drop(&mut self) {
            fault_injection::set(fault_injection::Mode::None);
        }
    }

    /// D16-T03：写失败（open/write 阶段）时原凭据必须保持不变，且不残留临时文件。
    #[cfg(unix)]
    #[test]
    fn injected_write_failure_preserves_existing_credential() {
        let dir = unique_tmp("inject-write");
        let path = dir.join("password");
        let first = generate_password();
        write_password_file(&path, &first).unwrap();

        let _guard = FaultGuard::set(fault_injection::Mode::Write);
        let err = write_password_file(&path, &generate_password()).unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::WriteZero);
        assert!(
            read_password_file(&path).as_deref() == Some(first.as_str()),
            "写失败后原凭据必须保持不变"
        );
        assert!(
            std::fs::read_dir(&dir).unwrap().all(|e| !e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".password.tmp-")),
            "写失败后不得残留临时文件"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-T03：rename 失败（目标不可替换）时临时文件被清理，既有目标不被破坏。
    #[test]
    fn rename_failure_cleans_tmp_and_leaves_target_untouched() {
        let dir = unique_tmp("rename-fail");
        let target = dir.join("password");
        std::fs::create_dir_all(target.join("blocker")).unwrap();
        std::fs::write(target.join("blocker").join("marker"), b"keep").unwrap();

        write_password_file(&target, &generate_password()).unwrap_err();

        assert_eq!(
            std::fs::read(target.join("blocker").join("marker")).unwrap(),
            b"keep",
            "rename 失败不得改动既有目标"
        );
        assert!(
            std::fs::read_dir(&dir).unwrap().all(|e| !e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".password.tmp-")),
            "rename 失败后临时文件必须被清理"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-T03：父目录无法创建（父路径是普通文件）时返回错误且不产生任何残留。
    #[test]
    fn parent_create_failure_returns_error_without_residue() {
        let dir = unique_tmp("parent-fail");
        std::fs::create_dir_all(&dir).unwrap();
        let blocker = dir.join("not-a-directory");
        std::fs::write(&blocker, b"keep").unwrap();

        let err = write_password_file(&blocker.join("password"), &generate_password()).unwrap_err();
        assert!(!err.to_string().is_empty());
        assert_eq!(std::fs::read(&blocker).unwrap(), b"keep");
        assert!(
            std::fs::read_dir(&dir).unwrap().all(|e| !e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".password.tmp-")),
            "父目录创建失败不得产生临时文件"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-T03：chmod 失败路径已被显式返回。rename 已完成，新凭据仍因临时文件
    /// 以 0600 创建而保持 owner-only（不因 chmod 报错退化为可读凭据）。
    #[cfg(unix)]
    #[test]
    fn injected_chmod_failure_keeps_new_credential_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_tmp("inject-chmod");
        let path = dir.join("password");
        write_password_file(&path, &generate_password()).unwrap();

        let second = generate_password();
        let _guard = FaultGuard::set(fault_injection::Mode::Chmod);
        let err = write_password_file(&path, &second).unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        assert!(
            read_password_file(&path).as_deref() == Some(second.as_str()),
            "rename 已完成后 chmod 报错不应丢失新凭据"
        );
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "chmod 失败时仍须保持 0600");
        assert!(
            std::fs::read_dir(&dir).unwrap().all(|e| !e
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".password.tmp-")),
            "chmod 失败后不得残留临时文件"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-T03：非 root 下 runtime 目录不可写时 write 失败且旧凭据保持。
    /// root 会绕过目录权限位（本测试在 root 下跳过；CI/非 root 本地用户覆盖）。
    #[cfg(unix)]
    #[test]
    fn unwritable_runtime_dir_preserves_existing_credential() {
        use std::os::unix::fs::PermissionsExt;

        struct DirModeGuard(std::path::PathBuf);
        impl Drop for DirModeGuard {
            fn drop(&mut self) {
                let _ = std::fs::set_permissions(&self.0, std::fs::Permissions::from_mode(0o700));
            }
        }

        // SAFETY: geteuid 仅读取当前进程凭证，无副作用。
        if unsafe { libc::geteuid() } == 0 {
            eprintln!("[skip] root 会绕过 0500 权限位，权限矩阵由非 root/CI 覆盖");
            return;
        }

        let dir = unique_tmp("unwritable-dir");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).unwrap();
        let path = dir.join("password");
        let first = generate_password();
        write_password_file(&path, &first).unwrap();

        let _mode_guard = DirModeGuard(dir.clone());
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o500)).unwrap();
        let err = write_password_file(&path, &generate_password()).unwrap_err();
        assert_eq!(err.kind(), std::io::ErrorKind::PermissionDenied);
        assert!(
            read_password_file(&path).as_deref() == Some(first.as_str()),
            "目录不可写时旧凭据必须保持不变"
        );

        drop(_mode_guard);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D16-T03：非 Windows 平台拒绝 `PGPW2:` DPAPI 前缀，绝不把密文当明文返回。
    #[cfg(not(windows))]
    #[test]
    fn dpapi_prefixed_file_is_rejected_off_windows() {
        let dir = unique_tmp("dpapi-off-win");
        let path = dir.join("password");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, format!("{DPAPI_PREFIX}00ff")).unwrap();
        assert_eq!(read_password_file(&path), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Unix 写入保持明文格式（无 DPAPI 前缀），供 bundled psql 直接读取。
    #[cfg(unix)]
    #[test]
    fn unix_write_has_no_dpapi_prefix() {
        let dir = unique_tmp("unix-plain");
        let path = dir.join("password");
        let pwd = generate_password();
        write_password_file(&path, &pwd).unwrap();
        let raw = std::fs::read_to_string(&path).unwrap();
        assert!(!raw.starts_with(DPAPI_PREFIX));
        assert!(raw.trim() == pwd, "Unix password 文件应为明文");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 已存在的 0644 文件被重写时必须收紧为 0600（OpenOptions::mode 仅创建时生效）。
    #[cfg(unix)]
    #[test]
    fn rewrite_tightens_existing_permissive_file_to_0600() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_tmp("perms-tighten");
        let path = dir.join("password");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(&path, "old-content").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(!password_file_perms_ok(&path));

        write_password_file(&path, "TestPwd-1_2.3~").unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600, "重写后权限应收紧");
        assert_eq!(read_password_file(&path).as_deref(), Some("TestPwd-1_2.3~"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
