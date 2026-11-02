//! 集群进程身份校验（D16-F02）。
//!
//! PID 存在性不等于归属：`postmaster.pid` 可能残留，PID 也可能被系统复用。
//! 在向任何 PID 发送信号（含 `pg_ctl stop`/`pg_ctl kill`）前，必须先证明该 PID
//! 仍是本 PGDATA 的 postmaster：
//!
//! - 规范化 PGDATA（`postmaster.pid` 第 2 行 vs 调用方 data_dir）
//! - postmaster 启动时刻（`postmaster.pid` 第 3 行）
//! - OS 进程创建时刻（Linux `/proc/<pid>/stat` starttime + boot_id；
//!   macOS `proc_pidinfo` start_tvsec/usec；Windows `GetProcessTimes` FILETIME）
//! - 可执行文件路径（Linux `/proc/<pid>/exe`；macOS `proc_pidpath`；
//!   Windows `QueryFullProcessImageNameW`）
//!
//! 任一证据不可读或无法比对即 fail closed：拒绝发送信号，由调用方返回
//! `STOP_FAILED` 并保留 `postmaster.pid` 与诊断信息。
//!
//! F03（restore 前校验源集群空闲）与 R01 复用 [`ClusterIdentity`] /
//! [`capture_cluster_identity`] / [`verify_cluster_identity`]。

use crate::pgbin::PostmasterInfo;
use std::path::{Component, Path, PathBuf};

/// OS 层进程身份证据（同一 PID 被复用后 `created` 必然不同）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcessIdentity {
    pub pid: u32,
    /// 创建时刻证据（平台内部格式，仅用于等值比较，不对外解释）。
    ///
    /// - Linux: `<boot_id>:<starttime ticks>`
    /// - macOS: `<pbi_start_tvsec>.<pbi_start_tvusec>`
    /// - Windows: `<creation FILETIME high>:<low>`
    pub created: String,
    /// 进程可执行文件绝对路径。
    pub executable: PathBuf,
}

/// 可验证的集群身份：绑定 PGDATA + postmaster 启动时刻 + 当前 OS 进程。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClusterIdentity {
    pub pid: u32,
    /// 规范化（canonicalize，失败时词法归一）后的 PGDATA。
    pub data_dir: PathBuf,
    /// `postmaster.pid` 第 3 行记录的后台进程启动时刻（epoch 秒）。
    pub postmaster_started_at: i64,
    pub process: ProcessIdentity,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdentityError {
    /// 没有 `postmaster.pid`（通常表示已停止）。
    NoPidFile,
    /// `postmaster.pid` 缺少必需行，无法证明归属。
    IncompletePidFile(&'static str),
    /// 非法 PID（0/1 等系统进程，绝不发送信号）。
    InvalidPid(u32),
    /// 目标进程不存在（幂等停止视为已停止）。
    NotRunning(u32),
    /// `postmaster.pid` 记录的 PGDATA 与请求操作的不是同一目录。
    DataDirMismatch { expected: PathBuf, actual: PathBuf },
    /// 进程可执行文件不是本安装目录的 postgres。
    ExecutableMismatch { expected: PathBuf, actual: PathBuf },
    /// `postmaster.pid` 的 PID 或启动时刻已变化（文件被重写/进程已换代）。
    PidFileChanged {
        expected_pid: u32,
        actual_pid: u32,
        expected_started_at: i64,
        actual_started_at: i64,
    },
    /// OS 创建时刻与已捕获身份不一致（PID 复用）。
    ProcessMismatch {
        pid: u32,
        expected_created: String,
        actual_created: String,
    },
    /// 身份 API 不可用（权限、平台不支持、procfs 缺失等）→ fail closed。
    ProcessUnavailable { pid: u32, reason: String },
    /// 当前平台没有可用的身份证据实现 → fail closed。
    #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
    Unsupported,
}

impl IdentityError {
    /// 目标已不存在：调用方可走幂等停止/真 stale 清理路径。
    pub fn is_not_running(&self) -> bool {
        matches!(self, Self::NotRunning(_))
    }
}

impl std::fmt::Display for IdentityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoPidFile => write!(f, "postmaster.pid 缺失"),
            Self::IncompletePidFile(line) => {
                write!(f, "postmaster.pid 缺少 {line}，无法校验集群归属")
            }
            Self::InvalidPid(pid) => write!(f, "PID {pid} 非法（拒绝向系统进程发送信号）"),
            Self::NotRunning(pid) => write!(f, "PID {pid} 已不存在"),
            Self::DataDirMismatch { expected, actual } => write!(
                f,
                "PGDATA 不匹配（期望 {}，postmaster.pid 记录 {}）",
                expected.display(),
                actual.display()
            ),
            Self::ExecutableMismatch { expected, actual } => write!(
                f,
                "可执行文件不匹配（期望 {}，实际 {}）",
                expected.display(),
                actual.display()
            ),
            Self::PidFileChanged {
                expected_pid,
                actual_pid,
                expected_started_at,
                actual_started_at,
            } => write!(
                f,
                "postmaster.pid 已变化（pid {expected_pid}→{actual_pid}，\
                 started_at {expected_started_at}→{actual_started_at}）"
            ),
            Self::ProcessMismatch {
                pid,
                expected_created,
                actual_created,
            } => write!(
                f,
                "PID {pid} 创建时刻不匹配（期望 {expected_created}，实际 {actual_created}），疑似 PID 复用"
            ),
            Self::ProcessUnavailable { pid, reason } => {
                write!(f, "无法读取 PID {pid} 的进程身份（{reason}）")
            }
            #[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
            Self::Unsupported => write!(f, "当前平台不支持进程身份校验"),
        }
    }
}

impl std::error::Error for IdentityError {}

/// 规范化 PGDATA：优先 canonicalize（解析符号链接），失败时绝对化并消解 `.`/`..`。
pub fn normalize_data_dir(path: &Path) -> PathBuf {
    if let Ok(canonical) = std::fs::canonicalize(path) {
        return canonical;
    }
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    };
    let mut out = PathBuf::new();
    for comp in absolute.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push("..");
                }
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn executable_matches(expected: &Path, actual: &Path) -> bool {
    let expected = normalize_data_dir(expected);
    let actual = normalize_data_dir(actual);
    #[cfg(windows)]
    {
        expected
            .to_string_lossy()
            .eq_ignore_ascii_case(&actual.to_string_lossy())
    }
    #[cfg(not(windows))]
    {
        expected == actual
    }
}

/// 读取 `data_dir/postmaster.pid` 并构造可验证身份（期望可执行文件为
/// `install_dir/bin/postgres`）。
pub fn capture_cluster_identity(
    data_dir: &Path,
    install_dir: &Path,
) -> Result<ClusterIdentity, IdentityError> {
    let info = crate::pgbin::read_postmaster_pid(data_dir).ok_or(IdentityError::NoPidFile)?;
    let expected_exe = crate::pgbin::tool_path(install_dir, "postgres");
    capture_with_expected_exe(data_dir, &expected_exe, &info)
}

/// 复用已读取的 [`PostmasterInfo`] 构造身份（避免重复读 pid 文件）。
pub fn capture_cluster_identity_from_info(
    data_dir: &Path,
    install_dir: &Path,
    info: &PostmasterInfo,
) -> Result<ClusterIdentity, IdentityError> {
    let expected_exe = crate::pgbin::tool_path(install_dir, "postgres");
    capture_with_expected_exe(data_dir, &expected_exe, info)
}

/// 底层构造入口（测试可注入期望可执行文件路径）。
pub fn capture_with_expected_exe(
    expected_data_dir: &Path,
    expected_exe: &Path,
    info: &PostmasterInfo,
) -> Result<ClusterIdentity, IdentityError> {
    if info.pid < 2 {
        return Err(IdentityError::InvalidPid(info.pid));
    }
    let pidfile_data_dir = info
        .data_dir
        .clone()
        .ok_or(IdentityError::IncompletePidFile("第 2 行（PGDATA 路径）"))?;
    let postmaster_started_at = info
        .started_at
        .ok_or(IdentityError::IncompletePidFile("第 3 行（启动时刻）"))?;

    let expected_data_dir = normalize_data_dir(expected_data_dir);
    let actual_data_dir = normalize_data_dir(&pidfile_data_dir);
    if expected_data_dir != actual_data_dir {
        return Err(IdentityError::DataDirMismatch {
            expected: expected_data_dir,
            actual: actual_data_dir,
        });
    }

    let process = process_identity(info.pid)?;
    if !executable_matches(expected_exe, &process.executable) {
        return Err(IdentityError::ExecutableMismatch {
            expected: normalize_data_dir(expected_exe),
            actual: process.executable,
        });
    }
    Ok(ClusterIdentity {
        pid: info.pid,
        data_dir: actual_data_dir,
        postmaster_started_at,
        process,
    })
}

/// 重新读取 `postmaster.pid` 与 OS 进程身份，证明该 PID 仍是被捕获的 postmaster。
pub fn verify_cluster_identity(identity: &ClusterIdentity) -> Result<(), IdentityError> {
    let info =
        crate::pgbin::read_postmaster_pid(&identity.data_dir).ok_or(IdentityError::NoPidFile)?;
    if info.pid != identity.pid {
        return Err(IdentityError::PidFileChanged {
            expected_pid: identity.pid,
            actual_pid: info.pid,
            expected_started_at: identity.postmaster_started_at,
            actual_started_at: info.started_at.unwrap_or_default(),
        });
    }
    let pidfile_data_dir = info
        .data_dir
        .clone()
        .ok_or(IdentityError::IncompletePidFile("第 2 行（PGDATA 路径）"))?;
    let actual_data_dir = normalize_data_dir(&pidfile_data_dir);
    if actual_data_dir != identity.data_dir {
        return Err(IdentityError::DataDirMismatch {
            expected: identity.data_dir.clone(),
            actual: actual_data_dir,
        });
    }
    let actual_started_at = info
        .started_at
        .ok_or(IdentityError::IncompletePidFile("第 3 行（启动时刻）"))?;
    if actual_started_at != identity.postmaster_started_at {
        return Err(IdentityError::PidFileChanged {
            expected_pid: identity.pid,
            actual_pid: info.pid,
            expected_started_at: identity.postmaster_started_at,
            actual_started_at,
        });
    }

    let process = process_identity(info.pid)?;
    if process.created != identity.process.created {
        return Err(IdentityError::ProcessMismatch {
            pid: identity.pid,
            expected_created: identity.process.created.clone(),
            actual_created: process.created,
        });
    }
    if !executable_matches(&identity.process.executable, &process.executable) {
        return Err(IdentityError::ExecutableMismatch {
            expected: identity.process.executable.clone(),
            actual: process.executable,
        });
    }
    Ok(())
}

/// 读取指定 PID 的 OS 身份；进程不存在返回 [`IdentityError::NotRunning`]。
#[cfg(target_os = "linux")]
pub fn process_identity(pid: u32) -> Result<ProcessIdentity, IdentityError> {
    let stat_path = format!("/proc/{pid}/stat");
    let stat = match std::fs::read_to_string(&stat_path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Err(IdentityError::NotRunning(pid))
        }
        Err(e) => {
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("读取 {stat_path} 失败: {e}"),
            })
        }
    };
    // comm 字段可能含空格/括号，取最后一个 ')' 之后的部分：state=第 3 字段，starttime=第 22 字段。
    let rest = stat
        .rfind(')')
        .and_then(|idx| stat.get(idx + 1..))
        .ok_or_else(|| IdentityError::ProcessUnavailable {
            pid,
            reason: format!("{stat_path} 格式不可解析"),
        })?;
    let fields: Vec<&str> = rest.split_whitespace().collect();
    let state = fields.first().copied().unwrap_or("");
    if state == "Z" {
        // 僵尸进程已经退出，只是父进程尚未回收；不得再发送任何信号。
        return Err(IdentityError::NotRunning(pid));
    }
    let starttime = fields
        .get(19)
        .copied()
        .ok_or_else(|| IdentityError::ProcessUnavailable {
            pid,
            reason: format!("{stat_path} 缺少 starttime 字段"),
        })?;
    // boot_id 用于区分跨重启后可能重复的 starttime；不可读即无法证明身份 → fail closed。
    let boot_id = std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map(|s| s.trim().to_string())
        .map_err(|e| IdentityError::ProcessUnavailable {
            pid,
            reason: format!("读取 /proc/sys/kernel/random/boot_id 失败: {e}"),
        })?;
    if boot_id.is_empty() {
        return Err(IdentityError::ProcessUnavailable {
            pid,
            reason: "boot_id 为空".to_string(),
        });
    }
    let exe_path = format!("/proc/{pid}/exe");
    let executable = match std::fs::read_link(&exe_path) {
        Ok(p) => p,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            // 进程在读取期间退出。
            return Err(IdentityError::NotRunning(pid));
        }
        Err(e) => {
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("读取 {exe_path} 失败: {e}"),
            })
        }
    };
    Ok(ProcessIdentity {
        pid,
        created: format!("{boot_id}:{starttime}"),
        executable,
    })
}

#[cfg(target_os = "macos")]
fn process_absent(pid: u32) -> bool {
    // proc_pidinfo/proc_pidpath 失败时 errno 不保证可靠；用 kill(pid, 0) 复核，
    // 避免把「无法读取」误判为「已不存在」而误清理仍存活的进程。
    // SAFETY: signal 0 仅探测存在性，不投递信号。
    let rc = unsafe { libc::kill(pid as libc::pid_t, 0) };
    rc != 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

#[cfg(target_os = "macos")]
pub fn process_identity(pid: u32) -> Result<ProcessIdentity, IdentityError> {
    // SAFETY: proc_pidinfo 只读；缓冲区是本栈上的正确类型与长度。
    let mut info: libc::proc_bsdinfo = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<libc::proc_bsdinfo>() as libc::c_int;
    let rc = unsafe {
        libc::proc_pidinfo(
            pid as libc::c_int,
            libc::PROC_PIDTBSDINFO,
            0,
            &mut info as *mut _ as *mut libc::c_void,
            size,
        )
    };
    if rc <= 0 {
        let err = std::io::Error::last_os_error();
        if process_absent(pid) {
            return Err(IdentityError::NotRunning(pid));
        }
        return Err(IdentityError::ProcessUnavailable {
            pid,
            reason: format!("proc_pidinfo 失败: {err}"),
        });
    }

    let mut buf = vec![0u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
    // SAFETY: proc_pidpath 只读；buf 长度 >= 声明的缓冲区大小。
    let n = unsafe {
        libc::proc_pidpath(
            pid as libc::c_int,
            buf.as_mut_ptr() as *mut libc::c_void,
            buf.len() as u32,
        )
    };
    if n <= 0 {
        let err = std::io::Error::last_os_error();
        if process_absent(pid) {
            return Err(IdentityError::NotRunning(pid));
        }
        return Err(IdentityError::ProcessUnavailable {
            pid,
            reason: format!("proc_pidpath 失败: {err}"),
        });
    }
    let path = String::from_utf8_lossy(&buf[..n as usize]).into_owned();
    Ok(ProcessIdentity {
        pid,
        created: format!("{}.{:06}", info.pbi_start_tvsec, info.pbi_start_tvusec),
        executable: PathBuf::from(path),
    })
}

#[cfg(windows)]
mod win {
    use super::*;
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_INVALID_PARAMETER, FILETIME, STILL_ACTIVE,
    };
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, GetProcessTimes, OpenProcess, QueryFullProcessImageNameW,
        TerminateProcess, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    };

    const STILL_ACTIVE_U32: u32 = STILL_ACTIVE as u32;

    fn filetime_key(t: FILETIME) -> String {
        format!("{:08x}:{:08x}", t.dwHighDateTime, t.dwLowDateTime)
    }

    fn open(
        pid: u32,
        access: u32,
    ) -> Result<windows_sys::Win32::Foundation::HANDLE, IdentityError> {
        // SAFETY: OpenProcess 仅打开句柄；成功由调用方负责关闭。
        let handle = unsafe { OpenProcess(access, 0, pid) };
        if handle.is_null() {
            let err = unsafe { GetLastError() };
            if err == ERROR_INVALID_PARAMETER {
                return Err(IdentityError::NotRunning(pid));
            }
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("OpenProcess 失败 (error {err})"),
            });
        }
        Ok(handle)
    }

    fn identity_from_handle(
        pid: u32,
        handle: windows_sys::Win32::Foundation::HANDLE,
    ) -> Result<ProcessIdentity, IdentityError> {
        let mut creation = FILETIME::default();
        let mut exit = FILETIME::default();
        let mut kernel = FILETIME::default();
        let mut user = FILETIME::default();
        // SAFETY: handle 由 OpenProcess/验证路径保证有效且只读出参。
        let ok =
            unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) };
        if ok == 0 {
            let err = unsafe { GetLastError() };
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("GetProcessTimes 失败 (error {err})"),
            });
        }
        let mut code: u32 = 0;
        // SAFETY: 同上；GetExitCodeProcess 只读。
        let code_ok = unsafe { GetExitCodeProcess(handle, &mut code) };
        if code_ok == 0 {
            // 退出码不可读即无法证明进程状态 → fail closed。
            let err = unsafe { GetLastError() };
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("GetExitCodeProcess 失败 (error {err})"),
            });
        }
        if code != STILL_ACTIVE_U32 {
            return Err(IdentityError::NotRunning(pid));
        }

        let mut buf = [0u16; 32 * 1024];
        let mut size = buf.len() as u32;
        // SAFETY: handle 有效；buf 与 size 同步传入。
        let ok = unsafe {
            QueryFullProcessImageNameW(handle, PROCESS_NAME_WIN32, buf.as_mut_ptr(), &mut size)
        };
        if ok == 0 {
            let err = unsafe { GetLastError() };
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("QueryFullProcessImageNameW 失败 (error {err})"),
            });
        }
        Ok(ProcessIdentity {
            pid,
            created: filetime_key(creation),
            executable: PathBuf::from(String::from_utf16_lossy(&buf[..size as usize])),
        })
    }

    pub fn process_identity(pid: u32) -> Result<ProcessIdentity, IdentityError> {
        let handle = open(pid, PROCESS_QUERY_LIMITED_INFORMATION)?;
        let result = identity_from_handle(pid, handle);
        // SAFETY: 句柄在本函数内创建，必须关闭。
        unsafe {
            let _ = CloseHandle(handle);
        }
        result
    }

    /// 在同一进程句柄上完成「身份复核 + TerminateProcess」，避免 PID 复用竞态。
    pub fn terminate_verified(identity: &ClusterIdentity) -> Result<(), IdentityError> {
        let handle = open(
            identity.pid,
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
        )?;
        let current = identity_from_handle(identity.pid, handle);
        let verified = current.and_then(|current| {
            if current.created != identity.process.created {
                return Err(IdentityError::ProcessMismatch {
                    pid: identity.pid,
                    expected_created: identity.process.created.clone(),
                    actual_created: current.created,
                });
            }
            if !executable_matches(&identity.process.executable, &current.executable) {
                return Err(IdentityError::ExecutableMismatch {
                    expected: identity.process.executable.clone(),
                    actual: current.executable,
                });
            }
            Ok(())
        });
        let result = match verified {
            Ok(()) => {
                // SAFETY: 已验证句柄对应同一创建时刻的进程。
                let ok = unsafe { TerminateProcess(handle, 1) };
                if ok == 0 {
                    let err = unsafe { GetLastError() };
                    Err(IdentityError::ProcessUnavailable {
                        pid: identity.pid,
                        reason: format!("TerminateProcess 失败 (error {err})"),
                    })
                } else {
                    Ok(())
                }
            }
            Err(e) => Err(e),
        };
        // SAFETY: 句柄在本函数内创建，必须关闭。
        unsafe {
            let _ = CloseHandle(handle);
        }
        result
    }
}

#[cfg(windows)]
pub fn process_identity(pid: u32) -> Result<ProcessIdentity, IdentityError> {
    win::process_identity(pid)
}

#[cfg(windows)]
pub fn terminate_verified(identity: &ClusterIdentity) -> Result<(), IdentityError> {
    win::terminate_verified(identity)
}

#[cfg(unix)]
pub fn terminate_verified(identity: &ClusterIdentity) -> Result<(), IdentityError> {
    // SIGKILL 前做最后一次身份复核（Unix 无句柄语义，存在极短 TOCTOU 窗口；
    // 复核已把「PID 复用」的时间窗口压到最小，且 Windows 走句柄路径完全消除该窗口）。
    verify_cluster_identity(identity)?;
    // SAFETY: 已验证该 PID 仍属于本集群 postmaster。
    unsafe {
        libc::kill(identity.pid as libc::pid_t, libc::SIGKILL);
    }
    Ok(())
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
pub fn process_identity(pid: u32) -> Result<ProcessIdentity, IdentityError> {
    let _ = pid;
    Err(IdentityError::Unsupported)
}

#[cfg(not(any(unix, windows)))]
pub fn terminate_verified(identity: &ClusterIdentity) -> Result<(), IdentityError> {
    let _ = identity;
    Err(IdentityError::Unsupported)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-pid-test-{}-{}", std::process::id(), name))
    }

    fn write_pidfile(data_dir: &Path, pid: u32, started_at: i64) {
        std::fs::create_dir_all(data_dir).unwrap();
        std::fs::write(
            data_dir.join("postmaster.pid"),
            format!(
                "{pid}\n{}\n{started_at}\n55432\n/tmp\n127.0.0.1\n",
                data_dir.display()
            ),
        )
        .unwrap();
    }

    fn own_info(data_dir: &Path, started_at: i64) -> PostmasterInfo {
        PostmasterInfo {
            pid: std::process::id(),
            data_dir: Some(data_dir.to_path_buf()),
            started_at: Some(started_at),
            port: Some(55432),
        }
    }

    fn own_exe() -> PathBuf {
        std::env::current_exe().unwrap()
    }

    #[test]
    fn normalize_removes_curdir_and_parentdir() {
        let dir = unique_tmp("normalize");
        std::fs::create_dir_all(&dir).unwrap();
        let nested = dir.join("a/b");
        std::fs::create_dir_all(&nested).unwrap();
        let lexical = dir.join("a/b/../b/./");
        assert_eq!(
            normalize_data_dir(&lexical),
            std::fs::canonicalize(&nested).unwrap()
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn own_process_identity_is_readable() {
        let id = process_identity(std::process::id()).expect("own process identity");
        assert_eq!(id.pid, std::process::id());
        assert!(!id.created.is_empty());
        assert!(
            id.executable.exists(),
            "exe should exist: {:?}",
            id.executable
        );
    }

    #[test]
    fn dead_pid_is_not_running() {
        let dead = 4_000_000u32;
        let err = process_identity(dead).expect_err("dead pid should fail");
        assert!(err.is_not_running(), "expected NotRunning, got {err}");
    }

    #[test]
    fn invalid_pid_rejected() {
        let dir = unique_tmp("invalid-pid");
        std::fs::create_dir_all(&dir).unwrap();
        let info = PostmasterInfo {
            pid: 1,
            data_dir: Some(dir.clone()),
            started_at: Some(123),
            port: None,
        };
        let err = capture_with_expected_exe(&dir, &own_exe(), &info).unwrap_err();
        assert_eq!(err, IdentityError::InvalidPid(1));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn incomplete_pidfile_rejected() {
        let dir = unique_tmp("incomplete");
        std::fs::create_dir_all(&dir).unwrap();
        let info = PostmasterInfo {
            pid: std::process::id(),
            data_dir: None,
            started_at: None,
            port: None,
        };
        let err = capture_with_expected_exe(&dir, &own_exe(), &info).unwrap_err();
        assert!(matches!(err, IdentityError::IncompletePidFile(_)));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn capture_and_verify_own_process_succeeds() {
        let dir = unique_tmp("capture-own");
        write_pidfile(&dir, std::process::id(), 1_784_505_600);
        let info = own_info(&dir, 1_784_505_600);
        let identity =
            capture_with_expected_exe(&dir, &own_exe(), &info).expect("capture own process");
        assert_eq!(identity.pid, std::process::id());
        assert_eq!(identity.postmaster_started_at, 1_784_505_600);
        verify_cluster_identity(&identity).expect("verify own process");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn data_dir_mismatch_rejected() {
        let dir = unique_tmp("dirmismatch");
        let other = unique_tmp("dirmismatch-other");
        write_pidfile(&dir, std::process::id(), 123);
        let info = own_info(&other, 123);
        let err = capture_with_expected_exe(&dir, &own_exe(), &info).unwrap_err();
        assert!(
            matches!(err, IdentityError::DataDirMismatch { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&other);
    }

    #[test]
    fn executable_mismatch_rejected() {
        let dir = unique_tmp("exemismatch");
        write_pidfile(&dir, std::process::id(), 123);
        let info = own_info(&dir, 123);
        let err =
            capture_with_expected_exe(&dir, Path::new("/nonexistent/postgres"), &info).unwrap_err();
        assert!(
            matches!(err, IdentityError::ExecutableMismatch { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn verify_rejects_pidfile_rewrite() {
        let dir = unique_tmp("pidfile-change");
        write_pidfile(&dir, std::process::id(), 123);
        let info = own_info(&dir, 123);
        let identity =
            capture_with_expected_exe(&dir, &own_exe(), &info).expect("capture own process");

        // 模拟检查后 pid 文件被重写（指向另一 PID）→ 升级信号前必须拒绝。
        std::fs::write(
            dir.join("postmaster.pid"),
            format!("999999\n{}\n123\n55432\n/tmp\n127.0.0.1\n", dir.display()),
        )
        .unwrap();
        let err = verify_cluster_identity(&identity).unwrap_err();
        assert!(
            matches!(err, IdentityError::PidFileChanged { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn verify_rejects_creation_change() {
        let dir = unique_tmp("created-change");
        write_pidfile(&dir, std::process::id(), 123);
        let info = own_info(&dir, 123);
        let mut identity =
            capture_with_expected_exe(&dir, &own_exe(), &info).expect("capture own process");
        // 模拟 PID 复用：同一 PID 但 OS 创建时刻不同。
        identity.process.created = "foreign-boot:foreign-starttime".to_string();
        let err = verify_cluster_identity(&identity).unwrap_err();
        assert!(
            matches!(err, IdentityError::ProcessMismatch { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn verify_detects_process_gone() {
        let dir = unique_tmp("gone");
        write_pidfile(&dir, 4_000_000, 123);
        let info = PostmasterInfo {
            pid: 4_000_000,
            data_dir: Some(dir.clone()),
            started_at: Some(123),
            port: None,
        };
        let err = capture_with_expected_exe(&dir, &own_exe(), &info).unwrap_err();
        assert!(err.is_not_running(), "got {err}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn capture_without_pidfile_is_no_pid_file() {
        let dir = unique_tmp("no-pidfile");
        std::fs::create_dir_all(&dir).unwrap();
        let err = capture_cluster_identity(&dir, &dir).unwrap_err();
        assert_eq!(err, IdentityError::NoPidFile);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
