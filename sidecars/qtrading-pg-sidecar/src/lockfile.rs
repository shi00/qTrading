//! 维护锁（pg_plan §13.3）：`postgres/16/runtime/lock`。
//!
//! fs2 OS 级文件锁：进程死亡自动释放，无 stale lock（stale 检测由 doctor 负责报告）。
//! PGDATA 级操作（restore/maintenance-shell/离线 dump/run）必须持锁；冲突返回 exit 50。

use fs2::FileExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// 探测方只以非阻塞共享锁占用极短窗口；维护方在报告冲突前做有限次重试，
/// 避免把瞬时探测误报为「已有维护进程占用」（真实维护持锁时间远大于该窗口）。
const CONFLICT_RETRIES: u32 = 5;
const CONFLICT_RETRY_DELAY: Duration = Duration::from_millis(10);

/// 锁冲突判定：Windows LockFileEx 冲突返回 ERROR_LOCK_VIOLATION(33)，Rust 归为
/// Uncategorized；Unix flock 冲突为 WouldBlock。
fn is_lock_conflict(e: &std::io::Error) -> bool {
    e.kind() == std::io::ErrorKind::WouldBlock || e.raw_os_error() == Some(33)
}

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
    /// 排他获取；非阻塞，但遇冲突时做有限次短重试（吸收探测方的瞬时共享锁），
    /// 真实维护持锁期间仍以冲突返回（调用方决定冲突语义）。
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
        let mut attempt = 0;
        loop {
            match file.try_lock_exclusive() {
                Ok(()) => {
                    return Ok(Self {
                        file,
                        path: path.to_path_buf(),
                    })
                }
                Err(e) if is_lock_conflict(&e) => {
                    if attempt >= CONFLICT_RETRIES {
                        return Err(AcquireError::Conflict);
                    }
                    attempt += 1;
                    std::thread::sleep(CONFLICT_RETRY_DELAY);
                }
                Err(e) => return Err(AcquireError::Io(e)),
            }
        }
    }

    /// 探测锁是否被持有（doctor/status 用）。
    ///
    /// 只读探测，不产生任何写盘副作用：锁文件缺失即视为未持有（不创建文件或父目录）；
    /// 文件存在时以只读句柄尝试非阻塞共享锁并立即释放——共享锁与维护方的排他锁互斥，
    /// 因此不会把「另一进程正在维护」误报为无锁；探测占锁窗口极短且不写盘，不阻断真实维护。
    ///
    /// 注意：Windows 字节范围锁为句柄级语义，同进程内自持的锁探测不到；doctor/status
    /// 与维护命令分属不同进程，故不影响使用场景。
    pub fn is_held(path: &Path) -> bool {
        let file = match std::fs::File::open(path) {
            Ok(f) => f,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return false,
            Err(_) => return false,
        };
        // 显式走 fs2 trait：std 1.89+ 也有同名 File::try_lock_shared，返回类型不同，
        // 指示符写法可避免解析到 std 并获得 io::Error（与现有 fs2 排他锁实现同源）。
        match fs2::FileExt::try_lock_shared(&file) {
            Ok(()) => {
                let _ = file.unlock();
                false
            }
            Err(e) if is_lock_conflict(&e) => true,
            // 无法判定（权限/文件系统不支持锁等）时按未持有处理：探测只服务状态展示，
            // 不得因此阻断维护方（维护方仍会自行做排他判定）。
            Err(_) => false,
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

    /// D07-004：探测（doctor/status）不得在缺失时创建锁文件或父目录。
    #[test]
    fn probe_missing_file_creates_nothing() {
        let dir = unique_tmp("probe-missing");
        let path = dir.join("nested/lock");
        assert!(!MaintenanceLock::is_held(&path));
        assert!(!path.exists(), "探测不得创建锁文件");
        assert!(!dir.exists(), "探测不得创建父目录");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D07-004：文件存在但无人持锁 → 未持有；探测不得截断或改写锁文件。
    #[test]
    fn probe_existing_unlocked_file_is_read_only() {
        let dir = unique_tmp("probe-unlocked");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("lock");
        std::fs::write(&path, b"sentinel").unwrap();
        assert!(!MaintenanceLock::is_held(&path));
        assert_eq!(
            std::fs::read(&path).unwrap(),
            b"sentinel",
            "探测不得截断/改写锁文件"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D07-004：只读权限的锁文件仍可探测（探测只开只读句柄，不需要写权限）。
    #[cfg(unix)]
    #[test]
    fn probe_works_on_read_only_file() {
        use std::os::unix::fs::PermissionsExt;
        let dir = unique_tmp("probe-readonly");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("lock");
        std::fs::write(&path, b"").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o444)).unwrap();
        assert!(!MaintenanceLock::is_held(&path));
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D07-004：维护方持排他锁时探测必须报「已持有」（Unix flock 按打开文件描述计锁，
    /// 同进程另开句柄同样冲突；Windows 句柄级锁由下方跨进程用例覆盖）。
    #[cfg(unix)]
    #[test]
    fn probe_reports_held_while_exclusive_lock_held() {
        let dir = unique_tmp("probe-held");
        let path = dir.join("lock");
        let lock = MaintenanceLock::try_acquire(&path).unwrap();
        assert!(MaintenanceLock::is_held(&path));
        drop(lock);
        assert!(!MaintenanceLock::is_held(&path), "维护退出后应立即视为无锁");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// D07-004：并发探测（共享锁）不得让维护方取得排他锁时误报冲突。
    #[test]
    fn concurrent_probes_do_not_block_maintenance() {
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;

        let dir = unique_tmp("probe-concurrent");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("lock");
        std::fs::write(&path, b"").unwrap();

        let stop = Arc::new(AtomicBool::new(false));
        let probes: Vec<_> = (0..3)
            .map(|_| {
                let path = path.clone();
                let stop = Arc::clone(&stop);
                std::thread::spawn(move || {
                    while !stop.load(Ordering::Relaxed) {
                        let _ = MaintenanceLock::is_held(&path);
                    }
                })
            })
            .collect();

        let acquired = (0..20).find_map(|_| MaintenanceLock::try_acquire(&path).ok());
        stop.store(true, Ordering::Relaxed);
        for probe in probes {
            probe.join().unwrap();
        }
        assert!(
            acquired.is_some(),
            "并发探测不得阻断维护方取得排他锁（否则 doctor/status 会造成假 exit 50）"
        );
        drop(acquired);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
