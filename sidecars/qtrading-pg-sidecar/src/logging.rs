//! stderr 人类日志信道（pg_plan §6.2）+ R9 脱敏 + §13.7.10 轮转（单文件 10MB × 5）。
//!
//! 脱敏双保险：
//! 1. 调用方不得把密码/明文 URL 写入日志（代码约定，review 兜底）；
//! 2. `sanitize` 在落盘/输出前按行处理：注册过的 secret 全部替换为 `***`，
//!    并对 `scheme://user:password@host` 形态的 URL 做通用密码段掩码。

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock, RwLock};

const MAX_FILE_BYTES: u64 = 10 * 1024 * 1024;
const KEEP_ROTATED: u32 = 5;
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
        Ok(Self {
            path: path.to_path_buf(),
            file,
            size,
        })
    }

    fn rotate(&mut self) -> std::io::Result<()> {
        // sidecar.log.5 删除，其余顺移，当前文件改名为 .1
        for i in (1..=KEEP_ROTATED).rev() {
            let src = rotated_path(&self.path, i - 1);
            let dst = rotated_path(&self.path, i);
            if src.exists() {
                if i == KEEP_ROTATED {
                    let _ = std::fs::remove_file(&dst);
                }
                std::fs::rename(&src, &dst)?;
            }
        }
        self.file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        self.size = 0;
        Ok(())
    }

    fn write_line(&mut self, line: &[u8]) -> std::io::Result<()> {
        if self.size + line.len() as u64 > MAX_FILE_BYTES && self.size > 0 {
            self.rotate()?;
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
}
