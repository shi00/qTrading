//! stderr 人类日志信道（pg_plan §6.2）+ R9 脱敏 + §13.7.10 轮转（单文件 10MB × 5）。
//!
//! 脱敏双保险：
//! 1. 调用方不得把密码/明文 URL 写入日志（代码约定，review 兜底）；
//! 2. `sanitize` 在落盘/输出前按行处理：注册过的 secret 全部替换为 `***`，
//!    并对 `scheme://user:password@host` 形态的 URL 做通用密码段掩码。

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, RwLock};
use std::time::{Duration, Instant};

const MAX_FILE_BYTES: u64 = 10 * 1024 * 1024;
const KEEP_ROTATED: u32 = 5;
/// 轮转被阻塞后的重试退避，避免每写一行都重复顺移归档。
const ROTATE_RETRY_BACKOFF: Duration = Duration::from_secs(30);
const MASK: &str = "***";

static SECRETS: OnceLock<RwLock<Vec<String>>> = OnceLock::new();

fn secrets() -> &'static RwLock<Vec<String>> {
    SECRETS.get_or_init(|| RwLock::new(Vec::new()))
}

/// 注册需要脱敏的敏感串（密码、含密码 URL）。过短串忽略，避免误伤正常日志。
pub fn register_secret(secret: &str) {
    if secret.len() < 4 {
        return;
    }
    if let Ok(mut guard) = secrets().write() {
        if !guard.iter().any(|s| s == secret) {
            guard.push(secret.to_string());
        }
    }
}

/// 对单行日志脱敏：注册 secret 全量替换 + URL 密码段通用掩码。
pub fn sanitize(line: &str) -> String {
    let mut out = line.to_string();
    if let Ok(guard) = secrets().read() {
        for secret in guard.iter() {
            if out.contains(secret.as_str()) {
                out = out.replace(secret.as_str(), MASK);
            }
        }
    }
    mask_url_passwords(&out)
}

/// `scheme://user:password@host` → `scheme://user:***@host`（无正则，逐段扫描，覆盖一行内多个 URL）。
fn mask_url_passwords(input: &str) -> String {
    let mut result = String::with_capacity(input.len());
    let mut rest = input;
    while let Some(scheme_pos) = rest.find("://") {
        let after_scheme = &rest[scheme_pos + 3..];
        // authority 段到第一个 '/' 或字符串结尾
        let authority_end = after_scheme.find('/').unwrap_or(after_scheme.len());
        let authority = &after_scheme[..authority_end];
        let masked = authority.rfind('@').and_then(|at| {
            authority[..at]
                .rfind(':')
                .map(|colon| format!("{}{}{}", &authority[..colon + 1], MASK, &authority[at..]))
        });
        match masked {
            Some(m) => {
                result.push_str(&rest[..scheme_pos + 3]);
                result.push_str(&m);
                rest = &after_scheme[authority_end..];
            }
            None => {
                // 无密码段：原样保留到 authority 结束，继续向后扫描
                let keep = scheme_pos + 3 + authority_end;
                result.push_str(&rest[..keep]);
                rest = &rest[keep..];
            }
        }
    }
    result.push_str(rest);
    result
}

struct RotatingFile {
    path: PathBuf,
    file: std::fs::File,
    size: u64,
    /// 轮转退避截止时刻；`None` 表示无待退避的失败。
    next_rotate_attempt: Option<Instant>,
}

impl RotatingFile {
    fn open(path: &Path) -> std::io::Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        let rf = Self {
            path: path.to_path_buf(),
            file,
            size,
            next_rotate_attempt: None,
        };
        // 进程若在「摘除主文件 → 归档落地」之间崩溃，暂存块会留在磁盘上；
        // 启动时先尝试归位（失败则原样保留，下次轮转再试），避免掉电丢日志。
        let staging = rf.staging_path();
        if staging.exists() {
            rf.land_staged(&staging);
        }
        Ok(rf)
    }

    /// 轮转：先把主文件原子改名为暂存文件并立即新建空主文件接管写入，
    /// 暂存成功后才顺移 `.4→.5 … .1→.2`，最后 `sidecar.log.rotating → sidecar.log.1`。
    ///
    /// 第一步失败（主文件被外部进程以不带 `FILE_SHARE_DELETE` 的句柄打开）时
    /// 立即放弃本次轮转：不触碰 `.1~.5`、不丢当前日志行，仅设置退避后继续追加。
    fn rotate(&mut self) {
        let staging = self.staging_path();
        // 上次落地失败的暂存块必须先归位，否则 Windows rename 会覆盖它而丢失旧日志。
        if staging.exists() && !self.land_staged(&staging) {
            self.defer_rotation();
            return;
        }
        if let Err(e) = std::fs::rename(&self.path, &staging) {
            eprintln!(
                "[sidecar] log rotation deferred (live file locked): {}: {e}",
                self.path.display()
            );
            self.defer_rotation();
            return;
        }
        match std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            Ok(file) => {
                self.file = file;
                self.size = 0;
            }
            Err(e) => {
                // 无法接管原路径：回滚改名，继续用旧句柄写当前日志。
                eprintln!(
                    "[sidecar] log rotation aborted (cannot reopen live file): {}: {e}",
                    self.path.display()
                );
                if let Err(rollback) = std::fs::rename(&staging, &self.path) {
                    eprintln!(
                        "[sidecar] log rotation rollback failed: {} -> {}: {rollback}",
                        staging.display(),
                        self.path.display()
                    );
                }
                self.defer_rotation();
                return;
            }
        }
        // 单步失败只记录不中断：暂存块保留在磁盘上，由下次轮转/open 重试落地。
        self.land_staged(&staging);
        self.next_rotate_attempt = None;
    }

    /// 顺移归档链（目标位被占用时跳过而非覆盖），再把暂存块落到 `.1`。
    /// 返回暂存块是否已落地；失败时暂存块原样保留。
    fn land_staged(&self, staging: &Path) -> bool {
        let mut first_error: Option<String> = None;
        for dst_index in (2..=KEEP_ROTATED).rev() {
            let src = rotated_path(&self.path, dst_index - 1);
            let dst = rotated_path(&self.path, dst_index);
            if !src.exists() {
                continue;
            }
            if dst_index == KEEP_ROTATED {
                let _ = std::fs::remove_file(&dst);
            }
            if dst.exists() {
                first_error.get_or_insert_with(|| {
                    format!(
                        "{} -> {} skipped (destination occupied)",
                        src.display(),
                        dst.display()
                    )
                });
                continue;
            }
            if let Err(e) = std::fs::rename(&src, &dst) {
                first_error
                    .get_or_insert_with(|| format!("{} -> {}: {e}", src.display(), dst.display()));
            }
        }
        let target = rotated_path(&self.path, 1);
        let landed = if target.exists() {
            first_error.get_or_insert_with(|| {
                format!(
                    "{} -> {} deferred (destination occupied)",
                    staging.display(),
                    target.display()
                )
            });
            false
        } else {
            match std::fs::rename(staging, &target) {
                Ok(()) => true,
                Err(e) => {
                    first_error.get_or_insert_with(|| {
                        format!("{} -> {}: {e}", staging.display(), target.display())
                    });
                    false
                }
            }
        };
        if let Some(message) = first_error {
            eprintln!("[sidecar] log archive shift incomplete: {message}");
        }
        landed
    }

    fn defer_rotation(&mut self) {
        self.next_rotate_attempt = Some(Instant::now() + ROTATE_RETRY_BACKOFF);
    }

    fn staging_path(&self) -> PathBuf {
        staging_path(&self.path)
    }

    fn write_line(&mut self, line: &[u8]) -> std::io::Result<()> {
        let over_limit = self.size > 0 && self.size + line.len() as u64 > MAX_FILE_BYTES;
        if over_limit
            && self
                .next_rotate_attempt
                .is_none_or(|at| Instant::now() >= at)
        {
            self.rotate();
        }
        self.file.write_all(line)?;
        self.size += line.len() as u64;
        Ok(())
    }
}

fn rotated_path(base: &Path, index: u32) -> PathBuf {
    if index == 0 {
        base.to_path_buf()
    } else {
        let mut name = base.file_name().unwrap_or_default().to_os_string();
        name.push(format!(".{index}"));
        base.with_file_name(name)
    }
}

/// 轮转暂存路径：`sidecar.log` → `sidecar.log.rotating`。
fn staging_path(base: &Path) -> PathBuf {
    let mut name = base.file_name().unwrap_or_default().to_os_string();
    name.push(".rotating");
    base.with_file_name(name)
}

struct SinkInner {
    file: Option<RotatingFile>,
    line_buf: Vec<u8>,
}

impl SinkInner {
    fn write_bytes(&mut self, buf: &[u8]) -> std::io::Result<()> {
        self.line_buf.extend_from_slice(buf);
        while let Some(pos) = self.line_buf.iter().position(|b| *b == b'\n') {
            let mut line: Vec<u8> = self.line_buf.drain(..=pos).collect();
            let sanitized = sanitize(String::from_utf8_lossy(&line).trim_end());
            line = format!("{sanitized}\n").into_bytes();
            let stderr = std::io::stderr();
            let mut lock = stderr.lock();
            let _ = lock.write_all(&line);
            let _ = lock.flush();
            if let Some(file) = self.file.as_mut() {
                let _ = file.write_line(&line);
            }
        }
        Ok(())
    }
}

/// tracing `MakeWriter`：完成行脱敏后同时写 stderr 与轮转文件。
#[derive(Clone)]
pub struct LogSink {
    inner: Arc<Mutex<SinkInner>>,
}

pub struct LogSinkWriter {
    inner: Arc<Mutex<SinkInner>>,
}

impl Write for LogSinkWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.write_bytes(buf)?;
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::writer::MakeWriter<'a> for LogSink {
    type Writer = LogSinkWriter;

    fn make_writer(&'a self) -> Self::Writer {
        LogSinkWriter {
            inner: Arc::clone(&self.inner),
        }
    }
}

/// 初始化全局日志：stderr + 可选轮转文件。重复调用安全（测试场景）。
pub fn init(log_file: Option<&Path>) {
    let file = log_file.and_then(|p| match RotatingFile::open(p) {
        Ok(f) => Some(f),
        Err(e) => {
            eprintln!("[sidecar] log file open failed {}: {e}", p.display());
            None
        }
    });
    let sink = LogSink {
        inner: Arc::new(Mutex::new(SinkInner {
            file,
            line_buf: Vec::new(),
        })),
    };
    let subscriber = tracing_subscriber::fmt()
        .with_writer(sink)
        .with_ansi(false)
        .with_max_level(tracing::Level::INFO)
        .finish();
    let _ = tracing::subscriber::set_global_default(subscriber);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-log-test-{}-{}", std::process::id(), name))
    }

    #[test]
    fn sanitize_replaces_registered_secret() {
        register_secret("Sup3rSecretPwd");
        assert_eq!(
            sanitize("connecting with Sup3rSecretPwd ok"),
            "connecting with *** ok"
        );
        assert_eq!(sanitize("no secret here"), "no secret here");
    }

    #[test]
    fn sanitize_ignores_short_secrets() {
        register_secret("abc");
        assert_eq!(sanitize("abc stays"), "abc stays");
    }

    #[test]
    fn mask_url_password_variants() {
        assert_eq!(
            mask_url_passwords("postgresql://postgres:hunter2@127.0.0.1:5432/qtrading"),
            "postgresql://postgres:***@127.0.0.1:5432/qtrading"
        );
        // 无密码段不变
        assert_eq!(
            mask_url_passwords("postgresql://postgres@127.0.0.1/db"),
            "postgresql://postgres@127.0.0.1/db"
        );
        // 已脱敏不重复处理
        assert_eq!(
            mask_url_passwords("postgresql://postgres:***@127.0.0.1/db"),
            "postgresql://postgres:***@127.0.0.1/db"
        );
        // 非 URL 文本不变
        assert_eq!(mask_url_passwords("plain text"), "plain text");
    }

    #[test]
    fn rotating_file_rotates_at_size_limit() {
        let dir = unique_tmp("rotate");
        let path = dir.join("sidecar.log");
        let mut rf = RotatingFile::open(&path).unwrap();
        let big = vec![b'x'; (MAX_FILE_BYTES / 2) as usize];
        rf.write_line(&big).unwrap();
        rf.write_line(&big).unwrap();
        rf.write_line(&big).unwrap(); // 触发 rotate
        assert!(rotated_path(&path, 1).exists());
        assert!(path.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotated_path_naming() {
        let base = Path::new("/tmp/sidecar.log");
        assert_eq!(rotated_path(base, 0), PathBuf::from("/tmp/sidecar.log"));
        assert_eq!(rotated_path(base, 3), PathBuf::from("/tmp/sidecar.log.3"));
    }

    fn write_file(path: &Path, bytes: &[u8]) {
        std::fs::write(path, bytes).unwrap();
    }

    /// 以不带 FILE_SHARE_DELETE 的句柄打开文件：外部进程占用时 rename 会被拒绝。
    #[cfg(windows)]
    fn lock_without_delete_share(path: &Path) -> std::fs::File {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ_OR_WRITE: u32 = 0x1 | 0x2;
        std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .share_mode(FILE_SHARE_READ_OR_WRITE)
            .open(path)
            .unwrap()
    }

    #[test]
    fn rotation_shifts_archive_chain_in_order() {
        let dir = unique_tmp("chain");
        let path = dir.join("sidecar.log");
        std::fs::create_dir_all(&dir).unwrap();
        write_file(&path, b"live");
        for i in 1..=KEEP_ROTATED {
            write_file(&rotated_path(&path, i), format!("archive-{i}").as_bytes());
        }
        let mut rf = RotatingFile::open(&path).unwrap();
        let big = vec![b'x'; MAX_FILE_BYTES as usize];
        rf.write_line(&big).unwrap();

        assert_eq!(std::fs::read(rotated_path(&path, 1)).unwrap(), b"live");
        assert_eq!(std::fs::read(rotated_path(&path, 2)).unwrap(), b"archive-1");
        assert_eq!(std::fs::read(rotated_path(&path, 3)).unwrap(), b"archive-2");
        assert_eq!(std::fs::read(rotated_path(&path, 4)).unwrap(), b"archive-3");
        assert_eq!(std::fs::read(rotated_path(&path, 5)).unwrap(), b"archive-4");
        assert_eq!(std::fs::read(&path).unwrap(), big);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn staged_chunk_is_landed_on_open() {
        let dir = unique_tmp("staged-recover");
        let path = dir.join("sidecar.log");
        std::fs::create_dir_all(&dir).unwrap();
        write_file(&path, b"live\n");
        write_file(&staging_path(&path), b"staged-old\n");
        write_file(&rotated_path(&path, 1), b"archive-1\n");

        let _rf = RotatingFile::open(&path).unwrap();

        assert_eq!(
            std::fs::read(rotated_path(&path, 1)).unwrap(),
            b"staged-old\n"
        );
        assert_eq!(
            std::fs::read(rotated_path(&path, 2)).unwrap(),
            b"archive-1\n"
        );
        assert!(!staging_path(&path).exists());
        assert_eq!(std::fs::read(&path).unwrap(), b"live\n");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn rotation_defers_when_live_file_locked_by_external_handle() {
        let dir = unique_tmp("locked-live");
        let path = dir.join("sidecar.log");
        std::fs::create_dir_all(&dir).unwrap();
        write_file(&path, b"boot\n");
        for i in 1..=KEEP_ROTATED {
            write_file(&rotated_path(&path, i), format!("archive-{i}").as_bytes());
        }
        let mut rf = RotatingFile::open(&path).unwrap();
        let lock = lock_without_delete_share(&path);

        let big = vec![b'y'; MAX_FILE_BYTES as usize];
        let _ = rf.write_line(&big);
        // 当前日志行不得丢弃。
        assert_eq!(
            std::fs::metadata(&path).unwrap().len(),
            5 + big.len() as u64
        );
        // 归档链未被顺移或覆盖。
        for i in 1..=KEEP_ROTATED {
            assert_eq!(
                std::fs::read(rotated_path(&path, i)).unwrap(),
                format!("archive-{i}").into_bytes()
            );
        }
        // 不留下暂存文件。
        assert!(!staging_path(&path).exists());

        // 退避期内继续写：仍不触碰归档、仍不丢日志。
        let _ = rf.write_line(b"next\n");
        assert_eq!(
            std::fs::metadata(&path).unwrap().len(),
            5 + big.len() as u64 + 5
        );
        for i in 1..=KEEP_ROTATED {
            assert_eq!(
                std::fs::read(rotated_path(&path, i)).unwrap(),
                format!("archive-{i}").into_bytes()
            );
        }

        drop(lock);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn rotation_retries_after_backoff_expires() {
        let dir = unique_tmp("retry-after-backoff");
        let path = dir.join("sidecar.log");
        std::fs::create_dir_all(&dir).unwrap();
        write_file(&path, b"boot\n");
        for i in 1..=KEEP_ROTATED {
            write_file(&rotated_path(&path, i), format!("archive-{i}").as_bytes());
        }
        let mut rf = RotatingFile::open(&path).unwrap();
        let lock = lock_without_delete_share(&path);
        let big = vec![b'y'; MAX_FILE_BYTES as usize];
        let _ = rf.write_line(&big);
        drop(lock);

        // 模拟退避期已过（真实等待 30s 不现实）：清空退避截止时刻。
        rf.next_rotate_attempt = None;
        let _ = rf.write_line(b"after\n");

        // 旧主文件（boot + big）落到 .1，归档链顺移；新主文件只含触发行。
        assert_eq!(
            std::fs::metadata(rotated_path(&path, 1)).unwrap().len(),
            5 + big.len() as u64
        );
        assert_eq!(std::fs::read(rotated_path(&path, 2)).unwrap(), b"archive-1");
        assert_eq!(std::fs::read(&path).unwrap(), b"after\n");
        assert!(!staging_path(&path).exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn partial_archive_shift_keeps_staging_and_live_logging() {
        let dir = unique_tmp("partial-shift");
        let path = dir.join("sidecar.log");
        std::fs::create_dir_all(&dir).unwrap();
        write_file(&path, b"live");
        for i in 1..=KEEP_ROTATED {
            write_file(&rotated_path(&path, i), format!("archive-{i}").as_bytes());
        }
        let mut rf = RotatingFile::open(&path).unwrap();
        let lock = lock_without_delete_share(&rotated_path(&path, 2));

        let big = vec![b'x'; MAX_FILE_BYTES as usize];
        rf.write_line(&big).unwrap();

        // .4→.5、.3→.4 成功；.2 被锁导致该步跳过，.1→.2 与落地均因目标被占用而跳过。
        assert_eq!(std::fs::read(rotated_path(&path, 4)).unwrap(), b"archive-3");
        assert_eq!(std::fs::read(rotated_path(&path, 5)).unwrap(), b"archive-4");
        assert_eq!(std::fs::read(rotated_path(&path, 1)).unwrap(), b"archive-1");
        assert_eq!(std::fs::read(rotated_path(&path, 2)).unwrap(), b"archive-2");
        // 旧主文件保留在暂存块中不丢失，新主文件已接管写入。
        assert_eq!(std::fs::read(staging_path(&path)).unwrap(), b"live");
        assert_eq!(std::fs::read(&path).unwrap(), big);

        drop(lock);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
