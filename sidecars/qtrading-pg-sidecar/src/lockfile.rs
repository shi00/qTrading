//! 维护锁（pg_plan §13.3）：`postgres/17/runtime/lock`。
//!
//! fs2 OS 级文件锁：进程死亡自动释放，无 stale lock（stale 检测由 doctor 负责报告）。
//! PGDATA 级操作（restore/maintenance-shell/离线 dump/run）必须持锁；冲突返回 exit 50。

use fs2::FileExt;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum AcquireError {
    /// 另一进程（qTrading sidecar 或维护命令）持有锁
    Conflict,
    Io(std::io::Error),
}

pub struct MaintenanceLock {
    file: std::fs::File,
    #[allow(dead_code)]
    path: PathBuf,
}

impl MaintenanceLock {
    /// 排他获取；立即返回不阻塞（调用方决定冲突语义）。
    pub fn try_acquire(path: &Path) -> Result<Self, AcquireError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(AcquireError::Io)?;
        }
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .map_err(AcquireError::Io)?;
        file.try_lock_exclusive().map_err(|e| {
            // Windows LockFileEx 冲突返回 ERROR_LOCK_VIOLATION(33)，Rust 归为 Uncategorized；
            // Unix flock 冲突为 WouldBlock
            if e.kind() == std::io::ErrorKind::WouldBlock || e.raw_os_error() == Some(33) {
                AcquireError::Conflict
            } else {
                AcquireError::Io(e)
            }
        })?;
        Ok(Self {
            file,
            path: path.to_path_buf(),
        })
    }

    /// 探测锁是否被持有（doctor 用；不实际持有——拿到即放）。
    pub fn is_held(path: &Path) -> bool {
        match Self::try_acquire(path) {
            Ok(lock) => {
                drop(lock);
                false
            }
            Err(AcquireError::Conflict) => true,
            Err(AcquireError::Io(_)) => false,
        }
    }
}

impl Drop for MaintenanceLock {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-lock-test-{}-{}", std::process::id(), name))
    }

    #[test]
    fn release_then_reacquire() {
        let dir = unique_tmp("reacquire");
        let path = dir.join("lock");
        let lock = MaintenanceLock::try_acquire(&path).unwrap();
        drop(lock);
        assert!(!MaintenanceLock::is_held(&path));
        let _again = MaintenanceLock::try_acquire(&path).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 生产冲突场景是跨进程（qTrading sidecar vs 维护命令）。
    /// Windows 字节范围锁为句柄级语义，同进程第二句柄可加锁，故必须用子进程验证。
    #[test]
    fn conflict_cross_process() {
        if let Ok(p) = std::env::var("QTS_LOCK_CHILD_PROBE") {
            // 子进程：父进程已持锁 → 冲突 + is_held 为真
            let path = PathBuf::from(p);
            let result = MaintenanceLock::try_acquire(&path);
            let desc = match &result {
                Ok(_) => "Ok(acquired)".to_string(),
                Err(AcquireError::Conflict) => "Err(Conflict)".to_string(),
                Err(AcquireError::Io(e)) => {
                    format!("Err(Io kind={:?} raw={:?})", e.kind(), e.raw_os_error())
                }
            };
            assert!(
                matches!(result, Err(AcquireError::Conflict)),
                "子进程应观察到锁冲突，实际: {desc}"
            );
            assert!(MaintenanceLock::is_held(&path));
            return;
        }
        let dir = unique_tmp("conflict");
        let path = dir.join("lock");
        let _lock = MaintenanceLock::try_acquire(&path).unwrap();
        let exe = std::env::current_exe().unwrap();
        let out = std::process::Command::new(exe)
            .args([
                "lockfile::tests::conflict_cross_process",
                "--exact",
                "--nocapture",
            ])
            .env("QTS_LOCK_CHILD_PROBE", &path)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "子进程冲突探测失败 (code {:?}):\nstdout: {}\nstderr: {}",
            out.status.code(),
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr),
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn creates_parent_dirs() {
        let dir = unique_tmp("nested").join("a/b/c");
        let path = dir.join("lock");
        let _lock = MaintenanceLock::try_acquire(&path).unwrap();
        assert!(path.exists());
        let _ = std::fs::remove_dir_all(unique_tmp("nested"));
    }
}
