//! 集群进程身份校验（D16-F02 / D07-001）。
//!
//! PID 存在性不等于归属：`postmaster.pid` 可能残留，PID 也可能被系统复用。
//! 在向任何 PID 发送信号（含 `pg_ctl stop`/`pg_ctl kill`）前，必须先证明该 PID
//! 仍是本 PGDATA 的 postmaster：
//!
//! - 规范化 PGDATA（`postmaster.pid` 第 2 行 vs 调用方 data_dir）
//! - **pid 文件启动时刻与 OS 创建时刻的关联**（`postmaster.pid` 第 3 行 vs
//!   [`ProcessIdentity::started_at`]，允许 [`MAX_START_SKEW_SECS`] 内的真实启动延迟）：
//!   仅各自记录而不比较会让「陈旧 pid 文件 + 已被复用的 PID」冒充原集群
//! - **进程真实 PGDATA**（`ProcessIdentity::working_dir`；postmaster 启动后 chdir
//!   到 PGDATA。Linux `/proc/<pid>/cwd`、macOS `proc_pidinfo(PROC_PIDVNODEPATHINFO)`；
//!   平台不提供该证据时为 `None`，由前两项 + 可执行路径承担）
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

/// `postmaster.pid` 第 3 行与 OS 进程创建时刻的允许偏差（秒）。
///
/// postmaster 在 exec 之后才创建锁文件并写入第 3 行，因此两者天然相差一段真实
/// 启动延迟；Linux `btime` 仅有秒级精度，系统时钟也可能被 NTP 小幅校正。
/// 超出该偏差即视为「pid 文件与当前进程不属于同一次启动」（PID 复用或陈旧
/// pid 文件，D07-001），不得据此发送信号。
pub const MAX_START_SKEW_SECS: i64 = 30;

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
    /// OS 记录的进程创建时刻（epoch 秒），用于与 pid 文件第 3 行建立关联。
    pub started_at: i64,
    /// 进程真实工作目录（PostgreSQL postmaster 启动后 chdir 到 PGDATA，
    /// 故其值应等于 PGDATA）。平台无法读取该证据时为 `None`。
    pub working_dir: Option<PathBuf>,
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
    /// pid 文件启动时刻与该 PID 的 OS 创建时刻相差过大（陈旧 pid 文件 / PID 复用）。
    StartTimeMismatch {
        pid: u32,
        pidfile_started_at: i64,
        process_started_at: i64,
    },
    /// 进程真实 PGDATA（工作目录）与请求操作的不是同一目录。
    ProcessDataDirMismatch { expected: PathBuf, actual: PathBuf },
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
            Self::StartTimeMismatch {
                pid,
                pidfile_started_at,
                process_started_at,
            } => write!(
                f,
                "PID {pid} 的 pid 文件启动时刻 {pidfile_started_at} 与该进程 OS 创建时刻 \
                 {process_started_at} 相差超过 {MAX_START_SKEW_SECS}s，pid 文件与当前进程不属于同一次启动"
            ),
            Self::ProcessDataDirMismatch { expected, actual } => write!(
                f,
                "进程真实 PGDATA 不匹配（期望 {}，实际 {}）",
                expected.display(),
                actual.display()
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
    paths_refer_to_same_object(expected, actual)
}

/// 文件系统对象身份（同一对象的不同路径写法应比较相等）。
///
/// Unix 为 `(st_dev, st_ino)`；Windows 为 `(VolumeSerialNumber, FileIndex)`。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileObjectId {
    volume: u64,
    index: u64,
}

/// 读取路径对应的文件系统对象身份；路径不存在/无权限/平台不支持时返回 `None`。
fn file_object_id(path: &Path) -> Option<FileObjectId> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let meta = std::fs::metadata(path).ok()?;
        Some(FileObjectId {
            volume: meta.dev(),
            index: meta.ino(),
        })
    }
    #[cfg(windows)]
    {
        win::file_object_id(path)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = path;
        None
    }
}

/// 去掉 Windows verbatim/设备前缀：`\\?\C:\...` → `C:\...`，
/// `\\?\UNC\server\share` → `\\server\share`，`\\.\C:\...` → `C:\...`。
fn strip_verbatim_prefix(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        let text = path.as_os_str().to_string_lossy();
        if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{rest}"));
        }
        if let Some(rest) = text.strip_prefix(r"\\?\") {
            return PathBuf::from(rest);
        }
        if let Some(rest) = text.strip_prefix(r"\\.\") {
            return PathBuf::from(rest);
        }
    }
    path.to_path_buf()
}

/// 词法规范化：canonicalize 优先（解析符号链接），失败时绝对化并消解 `.`/`..`，
/// 最后剥离 Windows verbatim 前缀，避免同一目录因 `\\?\` 前缀被判定为不同。
fn lexical_normalize(path: &Path) -> PathBuf {
    strip_verbatim_prefix(&normalize_data_dir(path))
}

/// Windows 路径文本相等判定；`case_sensitive=false` 时按 Unicode 小写比较。
#[cfg(windows)]
fn compare_path_text(a: &Path, b: &Path, case_sensitive: bool) -> bool {
    if case_sensitive {
        a == b
    } else {
        a.to_string_lossy().to_lowercase() == b.to_string_lossy().to_lowercase()
    }
}

/// 词法回退比较（身份不可读时使用）。
///
/// Windows 上按目标所在卷/目录的大小写敏感属性比较；无法确认该属性时只接受字节级
/// 相等（fail closed：宁可拒绝停止，也不向可能不属于本集群的 PID 发送信号）。
fn lexical_paths_equal(a: &Path, b: &Path) -> bool {
    let (a, b) = (lexical_normalize(a), lexical_normalize(b));
    #[cfg(windows)]
    {
        match (
            win::dir_is_case_sensitive(&a),
            win::dir_is_case_sensitive(&b),
        ) {
            (Some(a_sensitive), Some(b_sensitive)) => {
                compare_path_text(&a, &b, a_sensitive || b_sensitive)
            }
            _ => a == b,
        }
    }
    #[cfg(not(windows))]
    {
        a == b
    }
}

/// 判定两个路径是否指向同一文件系统对象。
///
/// 优先用文件身份比对：`canonicalize` 一侧返回 `\\?\` verbatim 前缀、另一侧回退词法
/// 路径，或大小写不敏感卷上的大小写差异，都会让 `PathBuf !=` 误判为「不是同一目录/
/// 可执行文件」而拒绝停止合法集群。
///
/// 身份不可读、或身份不同（例如运行期间安装目录被重新解包替换了 postgres 二进制，
/// 同一路径换了 inode）时退回词法比较：路径规范化后相等即视为同一对象——集群归属
/// 仍由 pid 文件 PGDATA + 启动时刻 + 进程 start-time 共同证明，二进制换代不构成
/// 「外来进程」。
fn paths_refer_to_same_object(a: &Path, b: &Path) -> bool {
    if let (Some(a_id), Some(b_id)) = (file_object_id(a), file_object_id(b)) {
        if a_id == b_id {
            return true;
        }
    }
    lexical_paths_equal(a, b)
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
    if !paths_refer_to_same_object(&expected_data_dir, &actual_data_dir) {
        return Err(IdentityError::DataDirMismatch {
            expected: expected_data_dir,
            actual: actual_data_dir,
        });
    }

    let process = process_identity(info.pid)?;
    capture_from_process(
        &actual_data_dir,
        expected_exe,
        postmaster_started_at,
        process,
    )
}

/// 用已读取的 OS 进程证据构造身份（测试可注入合成证据，避免依赖真实 cwd）。
///
/// D07-001：`postmaster.pid` 与 OS 进程证据必须**互相关联**，而不是各自记录。
fn capture_from_process(
    data_dir: &Path,
    expected_exe: &Path,
    postmaster_started_at: i64,
    process: ProcessIdentity,
) -> Result<ClusterIdentity, IdentityError> {
    if !executable_matches(expected_exe, &process.executable) {
        return Err(IdentityError::ExecutableMismatch {
            expected: normalize_data_dir(expected_exe),
            actual: process.executable,
        });
    }
    let data_dir = normalize_data_dir(data_dir);
    validate_process_evidence(&data_dir, postmaster_started_at, &process)?;
    Ok(ClusterIdentity {
        pid: process.pid,
        data_dir,
        postmaster_started_at,
        process,
    })
}

/// 校验 pid 文件与 OS 进程证据的关联：启动时刻一致（容忍真实启动延迟）且真实 PGDATA 相符。
fn validate_process_evidence(
    data_dir: &Path,
    postmaster_started_at: i64,
    process: &ProcessIdentity,
) -> Result<(), IdentityError> {
    let skew = postmaster_started_at.saturating_sub(process.started_at);
    if skew.unsigned_abs() > MAX_START_SKEW_SECS.unsigned_abs() {
        return Err(IdentityError::StartTimeMismatch {
            pid: process.pid,
            pidfile_started_at: postmaster_started_at,
            process_started_at: process.started_at,
        });
    }
    if let Some(working_dir) = &process.working_dir {
        let actual = normalize_data_dir(working_dir);
        if !paths_refer_to_same_object(&actual, data_dir) {
            return Err(IdentityError::ProcessDataDirMismatch {
                expected: data_dir.to_path_buf(),
                actual,
            });
        }
    }
    Ok(())
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
    if !paths_refer_to_same_object(&actual_data_dir, &identity.data_dir) {
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
    // 复核时同样要求 pid 文件与 OS 证据互相关联，避免捕获后 pid 文件被换成别的世代。
    validate_process_evidence(&identity.data_dir, identity.postmaster_started_at, &process)?;
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
    let starttime_ticks: u64 =
        starttime
            .parse()
            .map_err(|_| IdentityError::ProcessUnavailable {
                pid,
                reason: format!("{stat_path} 的 starttime 不可解析: {starttime}"),
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
    // 与 pid 文件第 3 行比较需要 epoch 秒：btime（开机墙钟）+ starttime / USER_HZ。
    let started_at = process_start_epoch(pid, starttime_ticks)?;
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
    // postmaster 启动后 chdir 到 PGDATA：cwd 即进程真实 PGDATA（D07-001）。
    let cwd_path = format!("/proc/{pid}/cwd");
    let working_dir = match std::fs::read_link(&cwd_path) {
        Ok(p) => Some(p),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Err(IdentityError::NotRunning(pid))
        }
        Err(e) => {
            return Err(IdentityError::ProcessUnavailable {
                pid,
                reason: format!("读取 {cwd_path} 失败: {e}"),
            })
        }
    };
    Ok(ProcessIdentity {
        pid,
        created: format!("{boot_id}:{starttime}"),
        started_at,
        working_dir,
        executable,
    })
}

/// 由 `/proc/stat` 内容解析 `btime`（开机时刻 epoch 秒）。
#[cfg(any(target_os = "linux", test))]
fn parse_btime(proc_stat: &str) -> Option<i64> {
    proc_stat.lines().find_map(|line| {
        let mut parts = line.split_whitespace();
        if parts.next()? != "btime" {
            return None;
        }
        parts.next()?.parse().ok()
    })
}

/// 由 btime + starttime 换算进程创建时刻；时钟频率非法时返回 None（fail closed）。
#[cfg(any(target_os = "linux", test))]
fn start_epoch(btime: i64, starttime_ticks: u64, clk_tck: i64) -> Option<i64> {
    let clk_tck = u64::try_from(clk_tck).ok().filter(|t| *t > 0)?;
    let elapsed = i64::try_from(starttime_ticks / clk_tck).ok()?;
    btime.checked_add(elapsed)
}

#[cfg(target_os = "linux")]
fn process_start_epoch(pid: u32, starttime_ticks: u64) -> Result<i64, IdentityError> {
    // SAFETY: sysconf 只读取系统常量，无副作用。
    let clk_tck = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    let proc_stat =
        std::fs::read_to_string("/proc/stat").map_err(|e| IdentityError::ProcessUnavailable {
            pid,
            reason: format!("读取 /proc/stat 失败: {e}"),
        })?;
    let btime = parse_btime(&proc_stat).ok_or_else(|| IdentityError::ProcessUnavailable {
        pid,
        reason: "/proc/stat 缺少 btime，无法关联 pid 文件启动时刻".to_string(),
    })?;
    start_epoch(btime, starttime_ticks, clk_tck).ok_or(IdentityError::ProcessUnavailable {
        pid,
        reason: format!("无法由 btime={btime} 与 starttime={starttime_ticks} 换算进程创建时刻"),
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
        started_at: info.pbi_start_tvsec as i64,
        working_dir: process_working_dir(pid),
        executable: PathBuf::from(path),
    })
}

/// 读取进程真实工作目录（postmaster 启动后 chdir 到 PGDATA）。
///
/// 失败时返回 `None`：该证据不可得不应把 live 进程误判为已退出；启动时刻关联
/// 与可执行路径仍构成 fail closed 判据。
#[cfg(target_os = "macos")]
fn process_working_dir(pid: u32) -> Option<PathBuf> {
    use std::ffi::CStr;
    // SAFETY: proc_pidinfo 只读；缓冲区是本栈上的正确类型与长度。
    let mut vn: libc::proc_vnodepathinfo = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<libc::proc_vnodepathinfo>() as libc::c_int;
    let rc = unsafe {
        libc::proc_pidinfo(
            pid as libc::c_int,
            libc::PROC_PIDVNODEPATHINFO,
            0,
            &mut vn as *mut _ as *mut libc::c_void,
            size,
        )
    };
    if rc <= 0 {
        return None;
    }
    // SAFETY: vip_path 是 NUL 结尾的 C 字符串（MAXPATHLEN 字节），缓冲区由内核填充且在本栈上。
    let cwd = unsafe { CStr::from_ptr(vn.pvi_cdir.vip_path.as_ptr() as *const libc::c_char) };
    let cwd = cwd.to_string_lossy();
    if cwd.is_empty() {
        None
    } else {
        Some(PathBuf::from(cwd.as_ref()))
    }
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

    /// FILETIME（1601-01-01 起的 100ns 计数）→ epoch 秒，用于与 pid 文件第 3 行关联。
    fn filetime_epoch_secs(t: FILETIME) -> i64 {
        const EPOCH_DIFF_SECS: i64 = 11_644_473_600;
        let ticks = ((t.dwHighDateTime as u64) << 32) | t.dwLowDateTime as u64;
        (ticks / 10_000_000) as i64 - EPOCH_DIFF_SECS
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
            started_at: filetime_epoch_secs(creation),
            // Windows 无同权限级别的进程工作目录查询；该证据为 None，
            // 由启动时刻关联 + 可执行路径 + pid 文件承担归属判定。
            working_dir: None,
            executable: PathBuf::from(String::from_utf16_lossy(&buf[..size as usize])),
        })
    }

    /// 以只读元数据方式打开文件/目录句柄（目录需要 `FILE_FLAG_BACKUP_SEMANTICS`）。
    fn open_for_info(path: &Path) -> Option<windows_sys::Win32::Foundation::HANDLE> {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE;
        use windows_sys::Win32::Storage::FileSystem::{
            CreateFileW, FILE_FLAG_BACKUP_SEMANTICS, FILE_SHARE_DELETE, FILE_SHARE_READ,
            FILE_SHARE_WRITE, OPEN_EXISTING,
        };
        let wide: Vec<u16> = path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        // SAFETY: wide 以 NUL 结尾且在本函数内有效；desired access=0 只读取元数据；
        // 共享全部访问权限以免干扰正在运行的进程。
        let handle = unsafe {
            CreateFileW(
                wide.as_ptr(),
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                std::ptr::null(),
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                std::ptr::null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            None
        } else {
            Some(handle)
        }
    }

    /// 文件系统对象身份：`(VolumeSerialNumber, FileIndex)`，同一对象的不同路径写法一致。
    pub fn file_object_id(path: &Path) -> Option<FileObjectId> {
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
        };
        let handle = open_for_info(path)?;
        // SAFETY: 结构体为 POD，全零初始化合法。
        let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
        // SAFETY: handle 有效且只读出参；info 是本栈正确类型。
        let ok = unsafe { GetFileInformationByHandle(handle, &mut info) };
        // SAFETY: 句柄在本函数内创建，必须关闭。
        unsafe {
            let _ = CloseHandle(handle);
        }
        if ok == 0 {
            return None;
        }
        Some(FileObjectId {
            volume: u64::from(info.dwVolumeSerialNumber),
            index: (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
        })
    }

    /// 目录树是否启用大小写敏感（per-directory 标志）。
    ///
    /// 查询失败（旧系统 / 网络共享 / 权限不足）返回 `None`，由调用方 fail closed。
    pub fn dir_is_case_sensitive(path: &Path) -> Option<bool> {
        use windows_sys::Win32::Storage::FileSystem::{
            FileCaseSensitiveInfo, GetFileInformationByHandleEx, FILE_CASE_SENSITIVE_INFO,
        };
        use windows_sys::Win32::System::SystemServices::FILE_CS_FLAG_CASE_SENSITIVE_DIR;

        let target = if path.is_dir() {
            path.to_path_buf()
        } else {
            path.parent()?.to_path_buf()
        };
        let handle = open_for_info(&target)?;
        let mut info = FILE_CASE_SENSITIVE_INFO { Flags: 0 };
        let size = std::mem::size_of::<FILE_CASE_SENSITIVE_INFO>() as u32;
        // SAFETY: handle 有效；info 是本栈正确类型与长度。
        let ok = unsafe {
            GetFileInformationByHandleEx(
                handle,
                FileCaseSensitiveInfo,
                &mut info as *mut _ as *mut std::ffi::c_void,
                size,
            )
        };
        // SAFETY: 句柄在本函数内创建，必须关闭。
        unsafe {
            let _ = CloseHandle(handle);
        }
        if ok == 0 {
            None
        } else {
            Some(info.Flags & FILE_CS_FLAG_CASE_SENSITIVE_DIR != 0)
        }
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

    fn now_secs() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64
    }

    /// 合成 OS 进程证据：可精确控制创建时刻与真实 PGDATA（cwd）证据。
    fn synthetic_process(pid: u32, working_dir: Option<&Path>, started_at: i64) -> ProcessIdentity {
        ProcessIdentity {
            pid,
            created: "synthetic-boot:1".to_string(),
            started_at,
            working_dir: working_dir.map(Path::to_path_buf),
            executable: own_exe(),
        }
    }

    /// 启动一个 cwd = data_dir 的受控进程，模拟「真实孤儿 postmaster」。
    #[cfg(unix)]
    fn spawn_postmaster_like(data_dir: &Path) -> std::process::Child {
        use std::process::Stdio;
        std::process::Command::new("/bin/sleep")
            .arg("60")
            .current_dir(data_dir)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn postmaster-like helper")
    }

    #[cfg(unix)]
    fn kill_child(child: &mut std::process::Child) {
        let _ = child.kill();
        let _ = child.wait();
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
            id.started_at > 1_600_000_000,
            "started_at should be a plausible epoch, got {}",
            id.started_at
        );
        assert!(
            id.executable.exists(),
            "exe should exist: {:?}",
            id.executable
        );
        #[cfg(target_os = "linux")]
        {
            let cwd = std::fs::canonicalize(std::env::current_dir().unwrap()).unwrap();
            assert_eq!(
                id.working_dir.as_deref(),
                Some(cwd.as_path()),
                "Linux 应能读到进程真实工作目录（cwd = 进程真实 PGDATA）"
            );
            // 本测试进程刚启动：btime + starttime/USER_HZ 换算应接近当前时刻（D07-001 关联基础）。
            let now = now_secs();
            assert!(
                (now - id.started_at).unsigned_abs() <= 300,
                "started_at={} 应接近当前时刻 {now}",
                id.started_at
            );
        }
        #[cfg(windows)]
        assert!(id.working_dir.is_none(), "Windows 不提供 cwd 证据");
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

    /// 坏 pid 行：第 3 行不可解析（启动时刻不可读）→ 证据不足，拒绝捕获。
    #[test]
    fn pidfile_with_unparsable_start_time_is_rejected() {
        let dir = unique_tmp("bad-start-line");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("postmaster.pid"),
            format!(
                "{}\n{}\nnot-an-epoch\n55432\n/tmp\n127.0.0.1\n",
                std::process::id(),
                dir.display()
            ),
        )
        .unwrap();
        let info = crate::pgbin::read_postmaster_pid(&dir).expect("pid line 1 still parses");
        assert_eq!(info.started_at, None);
        let err = capture_with_expected_exe(&dir, &own_exe(), &info).unwrap_err();
        assert!(
            matches!(err, IdentityError::IncompletePidFile("第 3 行（启动时刻）")),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 对齐证据（真实孤儿 postmaster 的等价形态）：pid 文件与 OS 证据一致 → 捕获 + 复核通过。
    #[cfg(unix)]
    #[test]
    fn capture_and_verify_genuine_orphan_succeeds() {
        let dir = unique_tmp("capture-orphan");
        std::fs::create_dir_all(&dir).unwrap();
        let mut child = spawn_postmaster_like(&dir);
        let pid = child.id();
        let real = process_identity(pid).expect("helper identity");
        write_pidfile(&dir, pid, real.started_at);
        let info = crate::pgbin::read_postmaster_pid(&dir).expect("read pidfile");

        let identity = capture_with_expected_exe(&dir, Path::new("/bin/sleep"), &info)
            .expect("genuine orphan must be capturable");
        assert_eq!(identity.pid, pid);
        assert_eq!(identity.postmaster_started_at, real.started_at);
        verify_cluster_identity(&identity).expect("genuine orphan must verify");
        assert!(
            child.try_wait().expect("try_wait").is_none(),
            "no signal must be sent during capture/verify"
        );

        kill_child(&mut child);
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

    /// 复用 PID：pid 文件记录的启动时刻远早于该 PID 当前进程的创建时刻 → 拒绝捕获。
    #[test]
    fn capture_rejects_reused_pid_start_time_skew() {
        let dir = unique_tmp("reused-pid");
        std::fs::create_dir_all(&dir).unwrap();
        let now = now_secs();
        let process = synthetic_process(4242, Some(&dir), now);
        let err = capture_from_process(&dir, &own_exe(), now - 3600, process).unwrap_err();
        match err {
            IdentityError::StartTimeMismatch {
                pid,
                pidfile_started_at,
                process_started_at,
            } => {
                assert_eq!(pid, 4242);
                assert_eq!(pidfile_started_at, now - 3600);
                assert_eq!(process_started_at, now);
            }
            other => panic!("expected StartTimeMismatch, got {other}"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 容忍真实启动延迟：pid 文件在进程创建后数秒写入仍应通过；超出窗口则拒绝。
    #[test]
    fn capture_tolerates_real_startup_delay() {
        let dir = unique_tmp("startup-delay");
        std::fs::create_dir_all(&dir).unwrap();
        let now = now_secs();
        let within = synthetic_process(4243, Some(&dir), now);
        capture_from_process(&dir, &own_exe(), now + 10, within)
            .expect("10s startup delay must be tolerated");

        let beyond = synthetic_process(4244, Some(&dir), now);
        let err = capture_from_process(&dir, &own_exe(), now + MAX_START_SKEW_SECS + 1, beyond)
            .unwrap_err();
        assert!(
            matches!(err, IdentityError::StartTimeMismatch { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 进程真实 PGDATA（cwd）不是期望目录 → 拒绝（陈旧 pid 文件指向其它集群的同名进程）。
    #[test]
    fn capture_rejects_foreign_real_pgdata() {
        let dir = unique_tmp("foreign-pgdata");
        let other = unique_tmp("foreign-pgdata-other");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::create_dir_all(&other).unwrap();
        let now = now_secs();
        let process = synthetic_process(4245, Some(&other), now);
        let err = capture_from_process(&dir, &own_exe(), now, process).unwrap_err();
        assert!(
            matches!(err, IdentityError::ProcessDataDirMismatch { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&other);
    }

    /// 平台不提供 cwd 证据时（Windows 语义）仍以启动时刻关联 + 可执行路径为准。
    #[test]
    fn capture_without_working_dir_evidence_uses_start_time() {
        let dir = unique_tmp("no-cwd-evidence");
        std::fs::create_dir_all(&dir).unwrap();
        let now = now_secs();
        let aligned = synthetic_process(4246, None, now);
        capture_from_process(&dir, &own_exe(), now + 3, aligned)
            .expect("aligned evidence must pass without cwd");

        let skewed = synthetic_process(4247, None, now);
        let err = capture_from_process(&dir, &own_exe(), now - 3600, skewed).unwrap_err();
        assert!(
            matches!(err, IdentityError::StartTimeMismatch { .. }),
            "got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn verify_rejects_pidfile_rewrite() {
        let dir = unique_tmp("pidfile-change");
        std::fs::create_dir_all(&dir).unwrap();
        let mut child = spawn_postmaster_like(&dir);
        let pid = child.id();
        let real = process_identity(pid).expect("helper identity");
        write_pidfile(&dir, pid, real.started_at);
        let info = crate::pgbin::read_postmaster_pid(&dir).expect("read pidfile");
        let identity = capture_with_expected_exe(&dir, Path::new("/bin/sleep"), &info)
            .expect("capture genuine orphan");

        // 模拟检查后 pid 文件被重写（指向另一 PID）→ 升级信号前必须拒绝。
        std::fs::write(
            dir.join("postmaster.pid"),
            format!(
                "999999\n{}\n{}\n55432\n/tmp\n127.0.0.1\n",
                dir.display(),
                real.started_at
            ),
        )
        .unwrap();
        let err = verify_cluster_identity(&identity).unwrap_err();
        assert!(
            matches!(err, IdentityError::PidFileChanged { .. }),
            "got {err}"
        );

        kill_child(&mut child);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn verify_rejects_creation_change() {
        let dir = unique_tmp("created-change");
        std::fs::create_dir_all(&dir).unwrap();
        let mut child = spawn_postmaster_like(&dir);
        let pid = child.id();
        let real = process_identity(pid).expect("helper identity");
        write_pidfile(&dir, pid, real.started_at);
        let info = crate::pgbin::read_postmaster_pid(&dir).expect("read pidfile");
        let mut identity = capture_with_expected_exe(&dir, Path::new("/bin/sleep"), &info)
            .expect("capture genuine orphan");
        // 模拟 PID 复用：同一 PID 但 OS 创建时刻不同。
        identity.process.created = "foreign-boot:foreign-starttime".to_string();
        let err = verify_cluster_identity(&identity).unwrap_err();
        assert!(
            matches!(err, IdentityError::ProcessMismatch { .. }),
            "got {err}"
        );

        kill_child(&mut child);
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

    /// `/proc/stat` 解析：取 btime；缺失/非法返回 None（不可读启动时刻 → fail closed）。
    #[test]
    fn parse_btime_extracts_or_rejects() {
        let stat = "cpu  1 2 3\nbtime 1784505600\nprocesses 42\n";
        assert_eq!(parse_btime(stat), Some(1_784_505_600));
        assert_eq!(parse_btime("btime   1784505600  \n"), Some(1_784_505_600));
        assert_eq!(parse_btime("cpu 1 2 3\nprocesses 42\n"), None);
        assert_eq!(parse_btime("btime not-a-number\n"), None);
    }

    /// btime + starttime 换算：USER_HZ 为 100 时按秒取整；非法时钟频率 → None。
    #[test]
    fn start_epoch_converts_ticks_and_rejects_invalid_hz() {
        assert_eq!(start_epoch(1_700_000_000, 12_345, 100), Some(1_700_000_123));
        assert_eq!(start_epoch(1_700_000_000, 12_345, 0), None);
        assert_eq!(start_epoch(1_700_000_000, 12_345, -1), None);
    }

    /// 文件身份优先：硬链接（不同路径、同一对象）必须判定为同一文件。
    #[test]
    fn paths_refer_to_same_object_matches_hardlink() {
        let dir = unique_tmp("same-object-hardlink");
        std::fs::create_dir_all(&dir).unwrap();
        let original = dir.join("original");
        let alias = dir.join("alias");
        std::fs::write(&original, b"payload").unwrap();
        std::fs::hard_link(&original, &alias).expect("create hardlink");

        assert!(
            paths_refer_to_same_object(&original, &alias),
            "硬链接必须按文件身份判定为同一对象"
        );
        assert!(
            !lexical_paths_equal(&original, &alias),
            "词法路径不同，身份判定才是此处的正确依据"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 不同对象必须判定为不同（不能因为都有身份就放行）。
    #[test]
    fn paths_refer_to_same_object_rejects_distinct_paths() {
        let dir = unique_tmp("same-object-distinct");
        let (a, b) = (dir.join("a"), dir.join("b"));
        std::fs::create_dir_all(&a).unwrap();
        std::fs::create_dir_all(&b).unwrap();
        assert!(!paths_refer_to_same_object(&a, &b));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Windows verbatim 前缀（canonicalize 产物）不得导致同一目录被判为不同。
    #[cfg(windows)]
    #[test]
    fn paths_refer_to_same_object_ignores_verbatim_prefix() {
        let dir = unique_tmp("same-object-verbatim");
        std::fs::create_dir_all(&dir).unwrap();
        let plain = dir.clone();
        let verbatim = PathBuf::from(format!(r"\\?\{}", dir.display()));
        assert!(
            paths_refer_to_same_object(&plain, &verbatim),
            "`\\\\?\\` 前缀只是写法差异，不得误判为不同目录"
        );
        assert!(lexical_paths_equal(&plain, &verbatim));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 词法回退：剥离 verbatim 前缀；大小写比较按卷属性，无法确认时 fail closed。
    #[cfg(windows)]
    #[test]
    fn lexical_paths_equal_strips_verbatim_prefix_and_respects_case_sensitivity() {
        assert!(lexical_paths_equal(
            Path::new(r"C:\qts\data"),
            Path::new(r"\\?\C:\qts\data")
        ));
        assert!(lexical_paths_equal(
            Path::new(r"\\server\share\data"),
            Path::new(r"\\?\UNC\server\share\data")
        ));
        assert!(!lexical_paths_equal(
            Path::new(r"C:\qts\data"),
            Path::new(r"C:\qts\other")
        ));

        assert!(compare_path_text(
            Path::new(r"C:\qts\Data"),
            Path::new(r"C:\qts\data"),
            false
        ));
        assert!(!compare_path_text(
            Path::new(r"C:\qts\Data"),
            Path::new(r"C:\qts\data"),
            true
        ));

        // 大小写敏感目录：`sub` 与 `Sub` 是不同的目录 → 必须不等（fail closed 同理）。
        let dir = unique_tmp("case-sensitivity");
        std::fs::create_dir_all(dir.join("Sub")).unwrap();
        let cased = dir.join("Sub");
        let folded = dir.join("sub");
        if win::dir_is_case_sensitive(&cased) != Some(false) {
            assert!(
                !lexical_paths_equal(&cased, &folded),
                "大小写敏感（或无法确认）时不得把不同大小写的路径判为同一目录"
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}
