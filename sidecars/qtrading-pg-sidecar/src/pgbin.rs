//! PostgreSQL 二进制调用层（pg_plan §12.1：sidecar 从安装目录解析工具路径）。
//!
//! 统一经 `run_tool` 执行 bundled 工具（initdb/pg_ctl/psql/pg_dump/pg_restore/pg_controldata），
//! 密码只走 `PGPASSWORD` 环境变量，禁止上命令行（R9）；含凭据的 SQL 经 stdin 传递
//! （`psql_stdin`，`-f -`），同样不进 argv。
//! 分级停止（§7.3）：smart 25s → fast 5s → kill fallback，总预算 ≤ 32s。

use crate::process_identity::{self, ClusterIdentity, IdentityError};
use std::path::{Path, PathBuf};
use std::time::Duration;

/// 关键工具清单：setup 完整性校验与 doctor 复用。
pub const REQUIRED_TOOLS: &[&str] = &[
    "initdb",
    "pg_ctl",
    "postgres",
    "psql",
    "pg_dump",
    "pg_restore",
    "pg_controldata",
];

pub fn tool_path(install_dir: &Path, name: &str) -> PathBuf {
    let bin = install_dir.join("bin");
    #[cfg(windows)]
    {
        bin.join(format!("{name}.exe"))
    }
    #[cfg(not(windows))]
    {
        bin.join(name)
    }
}

/// 返回缺失的关键工具名（空 = 完整）。
pub fn missing_tools(install_dir: &Path) -> Vec<&'static str> {
    REQUIRED_TOOLS
        .iter()
        .copied()
        .filter(|name| !tool_path(install_dir, name).exists())
        .collect()
}

/// postmaster.pid 解析结果（行 1 = PID，行 2 = PGDATA，行 3 = 启动时刻，行 4 = port）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PostmasterInfo {
    pub pid: u32,
    /// 行 2 记录的 PGDATA（用于归属校验；旧版/截断文件可能缺失）。
    pub data_dir: Option<PathBuf>,
    /// 行 3 记录的 postmaster 启动时刻（epoch 秒；用于归属校验）。
    pub started_at: Option<i64>,
    pub port: Option<u16>,
}

pub fn read_postmaster_pid(data_dir: &Path) -> Option<PostmasterInfo> {
    let content = std::fs::read_to_string(data_dir.join("postmaster.pid")).ok()?;
    let mut lines = content.lines();
    let pid: u32 = lines.next()?.trim().parse().ok()?;
    let pidfile_data_dir = lines
        .next()
        .map(|l| PathBuf::from(l.trim()))
        .filter(|p| !p.as_os_str().is_empty());
    let started_at = lines.next().and_then(|l| l.trim().parse::<i64>().ok());
    let port = lines.next().and_then(|l| l.trim().parse().ok());
    Some(PostmasterInfo {
        pid,
        data_dir: pidfile_data_dir,
        started_at,
        port,
    })
}

// ---- 进程存活/终止（跨平台） ----

#[cfg(windows)]
pub fn process_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_ACCESS_DENIED, STILL_ACTIVE,
    };
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    // windows_sys 0.61 在 Win32::Foundation 导出 STILL_ACTIVE（NTSTATUS/i32 = 259），
    // GetExitCodeProcess 的退出码为 u32，故按业务语义做一次无符号转换。
    const STILL_ACTIVE_U32: u32 = STILL_ACTIVE as u32;
    // SAFETY: OpenProcess 仅查询句柄；成功立即 CloseHandle。
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        // 无权限不代表不存在（如其他用户的进程）
        return unsafe { GetLastError() } == ERROR_ACCESS_DENIED;
    }
    // OpenProcess 对已退出但仍有句柄持有的进程返回有效句柄（Windows 进程对象生命周期），
    // 需进一步用 GetExitCodeProcess 检查退出码是否为 STILL_ACTIVE。
    let mut exit_code: u32 = 0;
    let alive =
        unsafe { GetExitCodeProcess(handle, &mut exit_code) != 0 && exit_code == STILL_ACTIVE_U32 };
    unsafe {
        let _ = CloseHandle(handle);
    }
    alive
}

#[cfg(unix)]
pub fn process_alive(pid: u32) -> bool {
    // SAFETY: signal 0 仅探测存在性，不投递信号。
    let rc = unsafe { libc::kill(pid as libc::pid_t, 0) };
    if rc == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(unix)]
fn sigquit(pid: u32) {
    // SAFETY: SIGQUIT = PostgreSQL immediate shutdown 语义。
    unsafe {
        libc::kill(pid as libc::pid_t, libc::SIGQUIT);
    }
}

// ---- 工具执行 ----

#[derive(Debug)]
pub struct ToolOutput {
    pub code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
}

impl ToolOutput {
    pub fn success(&self) -> bool {
        self.code == Some(0)
    }
}

#[derive(Debug)]
pub enum ToolError {
    Io(std::io::Error),
    Timeout,
}

impl std::fmt::Display for ToolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "{e}"),
            Self::Timeout => write!(f, "timed out"),
        }
    }
}

/// 执行 bundled 工具：超时强杀子进程；env 注入（如 PGPASSWORD）。
pub async fn run_tool(
    program: &Path,
    args: &[&str],
    envs: &[(&str, &str)],
    timeout: Duration,
) -> Result<ToolOutput, ToolError> {
    let mut cmd = tokio::process::Command::new(program);
    cmd.args(args)
        .envs(envs.iter().map(|(k, v)| (k, v)))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let child = cmd.spawn().map_err(ToolError::Io)?;
    let out = match tokio::time::timeout(timeout, child.wait_with_output()).await {
        Ok(r) => r.map_err(ToolError::Io)?,
        Err(_) => return Err(ToolError::Timeout),
    };
    Ok(ToolOutput {
        code: out.status.code(),
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    })
}

/// 执行 bundled 工具并通过 stdin 传递输入：stdin 内容不进 argv（R9）。
///
/// 用于含凭据的 SQL（如 `ALTER USER ... PASSWORD`）：psql 以 `-f -` 从 stdin 读脚本。
/// 与 `run_tool` 同样保留超时强杀与 `kill_on_drop`；异步 future 被取消时子进程随之终止。
pub async fn run_tool_with_stdin(
    program: &Path,
    args: &[&str],
    envs: &[(&str, &str)],
    stdin_data: &str,
    timeout: Duration,
) -> Result<ToolOutput, ToolError> {
    use tokio::io::AsyncWriteExt;

    let mut cmd = tokio::process::Command::new(program);
    cmd.args(args)
        .envs(envs.iter().map(|(k, v)| (k, v)))
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd.spawn().map_err(ToolError::Io)?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| ToolError::Io(std::io::Error::other("stdin pipe unavailable")))?;

    let (write_result, wait_result) = match tokio::time::timeout(timeout, async move {
        let write_result = stdin.write_all(stdin_data.as_bytes()).await;
        // 必须显式关闭 stdin（EOF），否则读取脚本的工具会一直等待输入。
        drop(stdin);
        (write_result, child.wait_with_output().await)
    })
    .await
    {
        Ok(r) => r,
        Err(_) => return Err(ToolError::Timeout),
    };

    let out = wait_result.map_err(ToolError::Io)?;
    // 子进程可能提前退出并关闭 stdin；非零退出时优先保留其 stderr 供调用方诊断。
    if let Err(e) = write_result {
        if out.status.success() {
            return Err(ToolError::Io(e));
        }
    }
    Ok(ToolOutput {
        code: out.status.code(),
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    })
}

/// 同步版（doctor/status 等短命令；仍在 tokio 上下文时经 spawn_blocking 调用）。
pub fn run_tool_blocking(
    program: &Path,
    args: &[&str],
    envs: &[(&str, &str)],
) -> std::io::Result<ToolOutput> {
    let mut cmd = std::process::Command::new(program);
    cmd.args(args)
        .envs(envs.iter().map(|(k, v)| (k, v)))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let out = cmd.output()?;
    Ok(ToolOutput {
        code: out.status.code(),
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    })
}

/// psql 单条 SQL（`-X -q -A -t`，ON_ERROR_STOP）。密码经 env，永不上 argv。
// 连接参数 6 个 + sql + timeout 均为必需；为单函数引入参数结构体属过度抽象
#[allow(clippy::too_many_arguments)]
pub async fn psql(
    install_dir: &Path,
    host: &str,
    port: u16,
    user: &str,
    database: &str,
    password: &str,
    sql: &str,
    timeout: Duration,
) -> Result<String, ToolError> {
    let port_s = port.to_string();
    let out = run_tool(
        &tool_path(install_dir, "psql"),
        &[
            "-X",
            "-q",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            host,
            "-p",
            &port_s,
            "-U",
            user,
            "-d",
            database,
            "-c",
            sql,
        ],
        &[("PGPASSWORD", password), ("PGCONNECT_TIMEOUT", "5")],
        timeout,
    )
    .await?;
    if !out.success() {
        // stderr 可能含连接串形态信息，交给调用方前已过 logging::sanitize（落盘时）
        return Err(ToolError::Io(std::io::Error::other(
            out.stderr.trim().to_string(),
        )));
    }
    Ok(out.stdout.trim().to_string())
}

/// psql 经 stdin 执行 SQL（`-f -`）：与 `psql` 相同的连接参数/ON_ERROR_STOP/超时，
/// 但 SQL 不进 argv，避免含凭据语句出现在子进程命令行（R9）。
// 连接参数 6 个 + sql + timeout 均为必需；为单函数引入参数结构体属过度抽象
#[allow(clippy::too_many_arguments)]
pub async fn psql_stdin(
    install_dir: &Path,
    host: &str,
    port: u16,
    user: &str,
    database: &str,
    password: &str,
    sql: &str,
    timeout: Duration,
) -> Result<String, ToolError> {
    let port_s = port.to_string();
    let out = run_tool_with_stdin(
        &tool_path(install_dir, "psql"),
        &[
            "-X",
            "-q",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            host,
            "-p",
            &port_s,
            "-U",
            user,
            "-d",
            database,
            "-f",
            "-",
        ],
        &[("PGPASSWORD", password), ("PGCONNECT_TIMEOUT", "5")],
        sql,
        timeout,
    )
    .await?;
    if !out.success() {
        // stderr 可能含连接串形态信息；调用方负责在输出前经 logging::sanitize 脱敏。
        return Err(ToolError::Io(std::io::Error::other(
            out.stderr.trim().to_string(),
        )));
    }
    Ok(out.stdout.trim().to_string())
}

// ---- 分级停止（§7.3 / §13.7.48） ----

pub const SMART_STOP_SECS: u64 = 25;
pub const FAST_STOP_SECS: u64 = 5;
const KILL_GRACE_SECS: u64 = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StopMode {
    Smart,
    Fast,
    KillFallback,
}

impl StopMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Smart => "smart",
            Self::Fast => "fast",
            Self::KillFallback => "kill_fallback",
        }
    }
}

/// 停止失败原因（区分「进程仍活」与「无法证明归属」）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StopFailure {
    /// 调用方未提供已校验的集群身份 → 拒绝任何 PID 信号。
    NoIdentity,
    /// 身份校验失败 → 拒绝任何 PID 信号（fail closed）。
    Identity(IdentityError),
    /// 已校验并发送信号，但进程仍存活。
    StillRunning,
}

impl std::fmt::Display for StopFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoIdentity => write!(f, "缺少可验证的集群身份，拒绝向 PID 发送信号"),
            Self::Identity(e) => write!(f, "无法证明 PID 属于本 PGDATA：{e}"),
            Self::StillRunning => write!(f, "分级停止后 postgres 进程仍存活"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StopOutcome {
    /// 实际生效的停止模式；未发送任何信号（已停止/拒绝）时为 None。
    pub mode: Option<StopMode>,
    pub stopped: bool,
    pub failure: Option<StopFailure>,
}

impl StopOutcome {
    fn stopped_by(mode: StopMode) -> Self {
        Self {
            mode: Some(mode),
            stopped: true,
            failure: None,
        }
    }

    fn already_stopped() -> Self {
        Self {
            mode: None,
            stopped: true,
            failure: None,
        }
    }

    fn refused(failure: StopFailure) -> Self {
        Self {
            mode: None,
            stopped: false,
            failure: Some(failure),
        }
    }

    pub fn mode_str(&self) -> Option<&'static str> {
        self.mode.map(|m| m.as_str())
    }
}

async fn pg_ctl_stop(install_dir: &Path, data_dir: &Path, mode: &str, wait_secs: u64) -> bool {
    let data = data_dir.to_string_lossy().into_owned();
    let t = wait_secs.to_string();
    let out = run_tool(
        &tool_path(install_dir, "pg_ctl"),
        &["stop", "-D", &data, "-m", mode, "-w", "-t", &t],
        &[],
        Duration::from_secs(wait_secs + 10),
    )
    .await;
    match out {
        Ok(o) => o.success(),
        Err(_) => false,
    }
}

async fn wait_process_exit(pid: u32, timeout: Duration) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed() < timeout {
        if !process_alive(pid) {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    !process_alive(pid)
}

/// 每次 `pg_ctl stop` / 信号升级前的复核；返回 `true` 表示目标已停止（幂等）。
///
/// 校验失败但 PID 已不存在时按「已停止」处理（绝不发送信号）；PID 仍存活却
/// 无法证明归属时返回错误 → 调用方 fail closed。
fn revalidate(identity: &ClusterIdentity) -> Result<bool, IdentityError> {
    match process_identity::verify_cluster_identity(identity) {
        Ok(()) => Ok(false),
        // OS 层已判定进程不存在（含僵尸进程）→ 幂等按已停止处理，绝不发送信号。
        Err(e) if e.is_not_running() => Ok(true),
        Err(e) => {
            if !process_alive(identity.pid) {
                Ok(true)
            } else {
                Err(e)
            }
        }
    }
}

/// 分级停止：smart(25s) → fast(5s) → immediate quit → SIGKILL/TerminateProcess 兜底。
///
/// `identity` 必须由 [`process_identity::capture_cluster_identity`] 生成。每次
/// `pg_ctl stop`、SIGQUIT、强杀前都会重新校验 PGDATA / postmaster 启动时刻 /
/// OS 创建时刻 / 可执行文件路径；任何一步无法证明归属即立即停止升级并返回
/// [`StopFailure::Identity`]——不会对原始 PID 强杀。
pub async fn graded_stop(
    install_dir: &Path,
    data_dir: &Path,
    identity: Option<&ClusterIdentity>,
) -> StopOutcome {
    let Some(identity) = identity else {
        tracing::error!(
            "graded_stop refused: no verified cluster identity; raw PID signals disabled"
        );
        return StopOutcome::refused(StopFailure::NoIdentity);
    };

    // 任何信号/工具调用前先复核；PID 已被复用时此处即拒绝。
    match revalidate(identity) {
        Ok(true) => return StopOutcome::already_stopped(),
        Ok(false) => {}
        Err(e) => {
            tracing::error!("graded_stop refused before smart stop: {e}");
            return StopOutcome::refused(StopFailure::Identity(e));
        }
    }
    if pg_ctl_stop(install_dir, data_dir, "smart", SMART_STOP_SECS).await {
        return StopOutcome::stopped_by(StopMode::Smart);
    }
    tracing::warn!("smart stop failed/timeout, escalating to fast stop");
    match revalidate(identity) {
        Ok(true) => return StopOutcome::already_stopped(),
        Ok(false) => {}
        Err(e) => {
            tracing::error!("graded_stop refused before fast stop: {e}");
            return StopOutcome::refused(StopFailure::Identity(e));
        }
    }
    if pg_ctl_stop(install_dir, data_dir, "fast", FAST_STOP_SECS).await {
        return StopOutcome::stopped_by(StopMode::Fast);
    }
    tracing::warn!("fast stop failed/timeout, kill fallback engaged");

    match revalidate(identity) {
        Ok(true) => return StopOutcome::already_stopped(),
        Ok(false) => {}
        Err(e) => {
            tracing::error!("graded_stop refused before immediate quit: {e}");
            return StopOutcome::refused(StopFailure::Identity(e));
        }
    }
    #[cfg(unix)]
    sigquit(identity.pid);
    #[cfg(windows)]
    {
        let pid_s = identity.pid.to_string();
        let _ = run_tool(
            &tool_path(install_dir, "pg_ctl"),
            &["kill", "QUIT", &pid_s],
            &[],
            Duration::from_secs(5),
        )
        .await;
    }
    if wait_process_exit(identity.pid, Duration::from_secs(KILL_GRACE_SECS)).await {
        return StopOutcome::stopped_by(StopMode::KillFallback);
    }

    // Windows 在同一进程句柄上复核创建时刻后 TerminateProcess；Unix 复核后 SIGKILL。
    match process_identity::terminate_verified(identity) {
        Ok(()) => {}
        Err(e) if e.is_not_running() => return StopOutcome::already_stopped(),
        Err(e) => {
            tracing::error!("graded_stop refused before force terminate: {e}");
            return StopOutcome::refused(StopFailure::Identity(e));
        }
    }
    let stopped = wait_process_exit(identity.pid, Duration::from_secs(KILL_GRACE_SECS)).await;
    if stopped {
        StopOutcome::stopped_by(StopMode::KillFallback)
    } else {
        StopOutcome {
            mode: Some(StopMode::KillFallback),
            stopped: false,
            failure: Some(StopFailure::StillRunning),
        }
    }
}

/// start.log 尾部（8KB），只扫尾部避免历史日志误判。
fn start_log_tail(data_dir: &Path) -> String {
    let path = data_dir.join("start.log");
    let Ok(content) = std::fs::read_to_string(&path) else {
        return String::new();
    };
    content
        .chars()
        .rev()
        .take(8192)
        .collect::<String>()
        .chars()
        .rev()
        .collect()
}

fn start_log_contains(data_dir: &Path, patterns: &[&str]) -> bool {
    let lower = start_log_tail(data_dir).to_lowercase();
    patterns.iter().any(|p| lower.contains(p))
}

/// 从 `data_dir/start.log` 尾部识别 crash recovery 失败特征（§7.2 AI-16 → exit 40）。
pub fn start_log_indicates_recovery_failure(data_dir: &Path) -> bool {
    const PATTERNS: &[&str] = &[
        "could not locate a valid checkpoint record",
        "invalid checkpoint record",
        "could not find redo location",
        "panic:", // recovery PANIC（如 WAL 损坏）
        "database files are incompatible with server",
    ];
    start_log_contains(data_dir, PATTERNS)
}

/// 端口占用特征（§7.2 端口重试判定；`pg_ctl -w` 失败原因仅靠日志区分）。
pub fn start_log_indicates_port_conflict(data_dir: &Path) -> bool {
    const PATTERNS: &[&str] = &[
        "could not bind",
        "address already in use",
        "could not create any tcp/ip sockets",
    ];
    start_log_contains(data_dir, PATTERNS)
}

/// pg_controldata 解析结果（doctor 与 run state 共用）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControlData {
    /// "in production" / "shut down" / "in crash recovery" 等原始串
    pub cluster_state: String,
    pub data_checksums: bool,
}

/// 执行 `pg_controldata -D <data_dir>` 并解析关键字段；控制文件损坏/不可读返回 None（§13.7.36）。
pub fn read_control_data(install_dir: &Path, data_dir: &Path) -> Option<ControlData> {
    let data = data_dir.to_string_lossy().into_owned();
    let out = run_tool_blocking(
        &tool_path(install_dir, "pg_controldata"),
        &["-D", &data],
        &[],
    )
    .ok()?;
    if !out.success() {
        return None;
    }
    parse_control_data_output(&out.stdout)
}

/// 解析 `pg_controldata` stdout 为 ControlData；缺失关键字段返回 None。
/// 抽出为独立纯函数便于单测覆盖解析分支（P3-Rust-Coverage-D38）。
fn parse_control_data_output(stdout: &str) -> Option<ControlData> {
    let mut cluster_state = None;
    let mut data_checksums = None;
    for line in stdout.lines() {
        let Some((key, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        // 键名随 PostgreSQL 本地化固定为英文（lc_messages=C 仅影响 server 日志，不影响工具输出键）
        match key.trim() {
            "Database cluster state" => cluster_state = Some(value.to_string()),
            "Data page checksum version" => data_checksums = Some(value != "0"),
            _ => {}
        }
    }
    Some(ControlData {
        cluster_state: cluster_state?,
        data_checksums: data_checksums?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("qts-pgbin-test-{}-{}", std::process::id(), name))
    }

    #[test]
    fn tool_path_has_bin_segment() {
        let p = tool_path(Path::new("/x/install"), "psql");
        assert!(p.starts_with(Path::new("/x/install").join("bin")));
        #[cfg(windows)]
        assert!(p.ends_with("psql.exe"));
        #[cfg(not(windows))]
        assert!(p.ends_with("psql"));
    }

    #[test]
    fn missing_tools_reports_all_when_absent() {
        let dir = unique_tmp("missing");
        let missing = missing_tools(&dir);
        assert_eq!(missing.len(), REQUIRED_TOOLS.len());
        std::fs::create_dir_all(dir.join("bin")).unwrap();
        let first = tool_path(&dir, REQUIRED_TOOLS[0]);
        std::fs::write(&first, b"x").unwrap();
        let missing = missing_tools(&dir);
        assert_eq!(missing.len(), REQUIRED_TOOLS.len() - 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn postmaster_pid_parsing() {
        let dir = unique_tmp("pmpid");
        std::fs::create_dir_all(&dir).unwrap();
        // 真实 postmaster.pid 格式：PID / data dir / start epoch / port / socket / listen
        std::fs::write(
            dir.join("postmaster.pid"),
            "12345\n/x/data\n1784505600\n55432\n/tmp\n127.0.0.1\n",
        )
        .unwrap();
        let info = read_postmaster_pid(&dir).unwrap();
        assert_eq!(info.pid, 12345);
        assert_eq!(info.data_dir.as_deref(), Some(Path::new("/x/data")));
        assert_eq!(info.started_at, Some(1784505600));
        assert_eq!(info.port, Some(55432));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn postmaster_pid_missing_or_garbage() {
        let dir = unique_tmp("pmpid-bad");
        std::fs::create_dir_all(&dir).unwrap();
        assert!(read_postmaster_pid(&dir).is_none());
        std::fs::write(dir.join("postmaster.pid"), "not-a-pid\n").unwrap();
        assert!(read_postmaster_pid(&dir).is_none());
        // 只有 PID 行的截断文件仍可解析 PID，但身份字段缺失（由 process_identity 拒绝）
        std::fs::write(dir.join("postmaster.pid"), "12345\n").unwrap();
        let info = read_postmaster_pid(&dir).unwrap();
        assert_eq!(info.pid, 12345);
        assert!(info.data_dir.is_none());
        assert!(info.started_at.is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn own_process_is_alive_and_dead_pid_not() {
        assert!(process_alive(std::process::id()));
        // 取一个几乎不可能存活的 pid
        #[cfg(unix)]
        let dead = 4_000_000u32;
        #[cfg(windows)]
        let dead = 4_000_000u32;
        assert!(!process_alive(dead));
    }

    #[test]
    fn stop_mode_strings_match_state_values() {
        assert_eq!(StopMode::Smart.as_str(), "smart");
        assert_eq!(StopMode::Fast.as_str(), "fast");
        assert_eq!(StopMode::KillFallback.as_str(), "kill_fallback");
    }

    #[test]
    fn recovery_failure_patterns_detected() {
        let dir = unique_tmp("recovery");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("start.log"),
            "2026-07-20 LOG: starting\nFATAL: could not locate a valid checkpoint record\n",
        )
        .unwrap();
        assert!(start_log_indicates_recovery_failure(&dir));
        std::fs::write(dir.join("start.log"), "LOG: database system is ready\n").unwrap();
        assert!(!start_log_indicates_recovery_failure(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn recovery_failure_missing_log_is_false() {
        let dir = unique_tmp("recovery-none");
        assert!(!start_log_indicates_recovery_failure(&dir));
    }

    /// §7.2 端口冲突特征：start.log 含 "could not bind" / "address already in use" 等被识别。
    #[test]
    fn port_conflict_patterns_detected() {
        let dir = unique_tmp("port-conflict");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("start.log"),
            "2026-07-20 LOG: starting\nFATAL: could not bind IPv4 address\n",
        )
        .unwrap();
        assert!(start_log_indicates_port_conflict(&dir));
        std::fs::write(dir.join("start.log"), "LOG: database system is ready\n").unwrap();
        assert!(!start_log_indicates_port_conflict(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 端口冲突：日志缺失时返回 false（与 recovery_failure_missing_log_is_false 对称）。
    #[test]
    fn port_conflict_missing_log_is_false() {
        let dir = unique_tmp("port-conflict-none");
        assert!(!start_log_indicates_port_conflict(&dir));
    }

    /// start_log_tail 仅取尾部 8KB，避免历史日志误判（§7.2）。
    #[test]
    fn start_log_tail_truncates_to_8kb() {
        let dir = unique_tmp("log-tail");
        std::fs::create_dir_all(&dir).unwrap();
        // 写 16KB 历史日志 + 1KB 尾部特征
        let mut content = String::with_capacity(20_000);
        content.push_str(&"historical line\n".repeat(1000)); // ~15KB
        content.push_str("could not locate a valid checkpoint record\n"); // 尾部特征
        std::fs::write(dir.join("start.log"), &content).unwrap();
        // 尾部特征在 8KB 窗口内，应被识别
        assert!(start_log_indicates_recovery_failure(&dir));
        // 把特征推到 16KB 之前（超出 8KB 窗口）→ 不应被识别
        let mut content_far = String::with_capacity(20_000);
        content_far.push_str("could not locate a valid checkpoint record\n");
        content_far.push_str(&"historical line\n".repeat(1100)); // ~17KB 填充
        std::fs::write(dir.join("start.log"), &content_far).unwrap();
        assert!(
            !start_log_indicates_recovery_failure(&dir),
            "特征超出 8KB 尾部窗口不应被识别"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// start_log_contains 大小写不敏感（PATTERNS 与日志均转 lowercase 比较）。
    #[test]
    fn start_log_contains_is_case_insensitive() {
        let dir = unique_tmp("log-case");
        std::fs::create_dir_all(&dir).unwrap();
        // PostgreSQL 日志可能为 "FATAL: Could Not Bind" 等大小写混合
        std::fs::write(
            dir.join("start.log"),
            "FATAL: Could Not Bind IPv4 Address\n",
        )
        .unwrap();
        assert!(start_log_indicates_port_conflict(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// ToolOutput::success 仅当 code == Some(0)。
    #[test]
    fn tool_output_success_logic() {
        assert!(ToolOutput {
            code: Some(0),
            stdout: String::new(),
            stderr: String::new(),
        }
        .success());
        assert!(!ToolOutput {
            code: Some(1),
            stdout: String::new(),
            stderr: String::new(),
        }
        .success());
        assert!(!ToolOutput {
            code: None,
            stdout: String::new(),
            stderr: String::new(),
        }
        .success());
    }

    /// ToolError::Display：Io 透传底层错误，Timeout 固定 "timed out"。
    #[test]
    fn tool_error_display_formats() {
        let io_err = std::io::Error::other("connection refused");
        let e = ToolError::Io(io_err);
        assert!(format!("{e}").contains("connection refused"));

        let e = ToolError::Timeout;
        assert_eq!(format!("{e}"), "timed out");
    }

    /// parse_control_data_output：完整有效输出 → 提取 cluster_state + data_checksums。
    #[test]
    fn parse_control_data_extracts_fields() {
        let stdout = "\
pg_control version number:            1300\n\
Catalog version number:               202409242\n\
Database system identifier:           7420397430118478233\n\
Database cluster state:               shut down\n\
pg_control last modified:             Mon Jul 21 15:30:00 2026\n\
Latest checkpoint location:           0/16D9C80\n\
Data page checksum version:           1\n\
";
        let cd = parse_control_data_output(stdout).expect("should parse");
        assert_eq!(cd.cluster_state, "shut down");
        assert!(cd.data_checksums, "version=1 → true");
    }

    /// parse_control_data_output：data_checksums=0 → false。
    #[test]
    fn parse_control_data_checksums_zero_is_false() {
        let stdout = "\
Database cluster state:               in production\n\
Data page checksum version:           0\n\
";
        let cd = parse_control_data_output(stdout).expect("should parse");
        assert_eq!(cd.cluster_state, "in production");
        assert!(!cd.data_checksums, "version=0 → false");
    }

    /// parse_control_data_output：缺失 cluster_state → None。
    #[test]
    fn parse_control_data_missing_cluster_state_returns_none() {
        let stdout = "Data page checksum version:           1\n";
        assert!(parse_control_data_output(stdout).is_none());
    }

    /// parse_control_data_output：缺失 data_checksums → None。
    #[test]
    fn parse_control_data_missing_checksums_returns_none() {
        let stdout = "Database cluster state:               shut down\n";
        assert!(parse_control_data_output(stdout).is_none());
    }

    /// parse_control_data_output：无 ':' 分隔符的行被跳过，不影响其他行解析。
    #[test]
    fn parse_control_data_skips_lines_without_colon() {
        let stdout = "\
random garbage line without colon\n\
Database cluster state:               shut down\n\
another no colon line\n\
Data page checksum version:           1\n\
";
        let cd = parse_control_data_output(stdout).expect("should parse");
        assert_eq!(cd.cluster_state, "shut down");
        assert!(cd.data_checksums);
    }

    /// parse_control_data_output：空输出 → None。
    #[test]
    fn parse_control_data_empty_output_returns_none() {
        assert!(parse_control_data_output("").is_none());
    }

    // ---- P3-Rust-Coverage-D38: 补充 pgbin.rs 工具执行/停止路径覆盖 ----

    /// 跨平台获取一个必定存在的简单可执行程序路径（echo 命令）。
    fn echo_program() -> PathBuf {
        #[cfg(windows)]
        {
            // Windows 没有 /bin/echo；用 cmd.exe /c echo 模拟成功工具调用
            PathBuf::from("cmd.exe")
        }
        #[cfg(not(windows))]
        {
            PathBuf::from("/bin/echo")
        }
    }

    fn echo_args(msg: &str) -> Vec<&str> {
        #[cfg(windows)]
        {
            vec!["/c", "echo", msg]
        }
        #[cfg(not(windows))]
        {
            vec![msg]
        }
    }

    /// `run_tool` 成功路径：执行 echo 命令 → ToolOutput.success() == true。
    #[tokio::test]
    async fn run_tool_executes_successfully() {
        let prog = echo_program();
        let args = echo_args("hello-cov");
        let out = run_tool(&prog, &args, &[], Duration::from_secs(5))
            .await
            .expect("echo should succeed");
        assert!(out.success(), "code={:?} stderr={}", out.code, out.stderr);
        assert!(out.stdout.contains("hello-cov"), "stdout={}", out.stdout);
    }

    /// `run_tool` 失败路径：程序不存在 → ToolError::Io。
    #[tokio::test]
    async fn run_tool_nonexistent_program_returns_io_error() {
        let prog = PathBuf::from("/nonexistent/path/that/does/not/exe");
        let err = run_tool(&prog, &[], &[], Duration::from_secs(5))
            .await
            .expect_err("should fail");
        assert!(
            matches!(err, ToolError::Io(_)),
            "expected Io error, got {err}"
        );
    }

    /// `run_tool` 超时路径：sleep 命令超过 timeout → ToolError::Timeout。
    #[tokio::test]
    async fn run_tool_timeout_returns_timeout_error() {
        #[cfg(windows)]
        let (prog, args): (PathBuf, Vec<&'static str>) = (
            PathBuf::from("cmd.exe"),
            vec!["/c", "ping", "-n", "10", "127.0.0.1"],
        );
        #[cfg(not(windows))]
        let (prog, args): (PathBuf, Vec<&'static str>) = (PathBuf::from("/bin/sleep"), vec!["10"]);
        let err = run_tool(&prog, &args, &[], Duration::from_millis(100))
            .await
            .expect_err("should timeout");
        assert!(
            matches!(err, ToolError::Timeout),
            "expected Timeout, got {err}"
        );
    }

    /// `run_tool_blocking` 成功路径：执行 echo 命令 → ToolOutput.success() == true。
    #[test]
    fn run_tool_blocking_executes_successfully() {
        let prog = echo_program();
        let args = echo_args("blocking-cov");
        let out = run_tool_blocking(&prog, &args, &[]).expect("echo should succeed");
        assert!(out.success(), "code={:?} stderr={}", out.code, out.stderr);
        assert!(out.stdout.contains("blocking-cov"), "stdout={}", out.stdout);
    }

    /// `run_tool_blocking` 失败路径：程序不存在 → std::io::Error。
    #[test]
    fn run_tool_blocking_nonexistent_program_returns_error() {
        let prog = PathBuf::from("/nonexistent/path/that/does/not/exe");
        assert!(run_tool_blocking(&prog, &[], &[]).is_err());
    }

    /// `psql` 失败路径：连接到不存在的端口 → ToolError::Io（连接拒绝）。
    #[tokio::test]
    async fn psql_connection_refused_returns_error() {
        let dir = unique_tmp("psql-fail");
        std::fs::create_dir_all(&dir).unwrap();
        // install_dir 不含 psql 二进制 → run_tool 会失败（Io error）
        let err = psql(
            &dir,
            "127.0.0.1",
            1, // 1 号端口几乎不可能有 postgres
            "postgres",
            "postgres",
            "dummy",
            "select 1",
            Duration::from_secs(2),
        )
        .await
        .expect_err("should fail");
        // psql 二进制不存在 → Io error；即使存在也会因连接拒绝返回 Io error
        assert!(
            matches!(err, ToolError::Io(_)),
            "expected Io error, got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `read_control_data` 失败路径：install_dir 不含 pg_controldata → None。
    #[test]
    fn read_control_data_nonexistent_install_dir_returns_none() {
        let dir = unique_tmp("controldata-fail");
        std::fs::create_dir_all(&dir).unwrap();
        // install_dir 不含 bin/pg_controldata → run_tool_blocking 返回 Err → None
        let result = read_control_data(&dir, &dir);
        assert!(
            result.is_none(),
            "expected None for nonexistent pg_controldata"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `pg_ctl_stop` 失败路径：install_dir 不含 pg_ctl → 返回 false。
    #[tokio::test]
    async fn pg_ctl_stop_nonexistent_install_returns_false() {
        let dir = unique_tmp("pgctl-stop-fail");
        std::fs::create_dir_all(&dir).unwrap();
        let stopped = pg_ctl_stop(&dir, &dir, "fast", 1).await;
        assert!(!stopped, "should fail when pg_ctl binary missing");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `wait_process_exit`：对不存在的 PID 应立即返回 true。
    #[tokio::test]
    async fn wait_process_exit_dead_pid_returns_true() {
        let dead_pid = 4_000_000u32;
        let exited = wait_process_exit(dead_pid, Duration::from_millis(100)).await;
        assert!(exited, "dead pid should be detected as exited");
    }

    /// `wait_process_exit`：对自己进程 PID + 极短 timeout → false（仍在运行）。
    #[tokio::test]
    async fn wait_process_exit_alive_pid_timeout_returns_false() {
        let own_pid = std::process::id();
        let exited = wait_process_exit(own_pid, Duration::from_millis(50)).await;
        assert!(
            !exited,
            "alive pid with short timeout should not be detected as exited"
        );
    }

    /// `graded_stop`：未提供已验证身份 → 拒绝发送任何信号（fail closed）。
    #[tokio::test]
    async fn graded_stop_without_identity_refuses() {
        let dir = unique_tmp("graded-stop-noid");
        std::fs::create_dir_all(&dir).unwrap();
        let outcome = graded_stop(&dir, &dir, None).await;
        assert_eq!(outcome.mode, None);
        assert!(!outcome.stopped);
        assert_eq!(outcome.failure, Some(StopFailure::NoIdentity));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `graded_stop`：身份指向已消失的 PID → 幂等返回已停止，且不发送信号。
    #[tokio::test]
    async fn graded_stop_dead_identity_is_idempotent() {
        let dir = unique_tmp("graded-stop-dead-id");
        std::fs::create_dir_all(&dir).unwrap();
        let dead_pid = 4_000_000u32;
        let identity = ClusterIdentity {
            pid: dead_pid,
            data_dir: dir.clone(),
            postmaster_started_at: 123,
            process: process_identity::ProcessIdentity {
                pid: dead_pid,
                created: "dead".to_string(),
                executable: PathBuf::from("/nonexistent/postgres"),
            },
        };
        let outcome = graded_stop(&dir, &dir, Some(&identity)).await;
        assert_eq!(outcome.mode, None);
        assert!(outcome.stopped, "dead pid should be treated as stopped");
        assert_eq!(outcome.failure, None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `graded_stop`：PID 复用（OS 创建时刻变化）时在发送任何信号前拒绝，
    /// 受控辅助进程必须仍存活（D16-F02 核心回归）。
    #[cfg(unix)]
    #[tokio::test]
    async fn graded_stop_refuses_pid_reuse_before_any_signal() {
        let dir = unique_tmp("graded-stop-reuse");
        std::fs::create_dir_all(&dir).unwrap();
        let mut helper = std::process::Command::new("/bin/sleep")
            .arg("60")
            .spawn()
            .expect("spawn sleep helper");
        let helper_pid = helper.id();
        std::fs::write(
            dir.join("postmaster.pid"),
            format!(
                "{helper_pid}\n{}\n123\n55432\n/tmp\n127.0.0.1\n",
                dir.display()
            ),
        )
        .unwrap();

        let mut identity = process_identity::capture_with_expected_exe(
            &dir,
            std::path::Path::new("/bin/sleep"),
            &read_postmaster_pid(&dir).unwrap(),
        )
        .expect("capture helper identity");
        // 模拟 PID 复用：同一 PID 的创建时刻已不同。
        identity.process.created = "foreign-boot:foreign-starttime".to_string();

        let outcome = graded_stop(&dir, &dir, Some(&identity)).await;
        assert_eq!(outcome.mode, None, "must not escalate any signal");
        assert!(!outcome.stopped);
        assert!(
            matches!(
                outcome.failure,
                Some(StopFailure::Identity(IdentityError::ProcessMismatch { .. }))
            ),
            "unexpected failure: {:?}",
            outcome.failure
        );
        assert!(
            process_alive(helper_pid),
            "helper process must survive rejected stop"
        );

        let _ = helper.kill();
        let _ = helper.wait();
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `start_log_tail`：日志文件不存在 → 返回空字符串。
    #[test]
    fn start_log_tail_missing_file_returns_empty() {
        let dir = unique_tmp("logtail-missing");
        let tail = start_log_tail(&dir);
        assert!(
            tail.is_empty(),
            "missing log file should return empty string"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `start_log_tail`：短日志 → 返回完整内容（不截断）。
    #[test]
    fn start_log_tail_short_log_returns_full_content() {
        let dir = unique_tmp("logtail-short");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("start.log"), "short log line\n").unwrap();
        let tail = start_log_tail(&dir);
        assert_eq!(tail, "short log line\n");
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- D16-F07: psql_stdin 不把含凭据 SQL 放入 argv ----

    #[cfg(unix)]
    fn write_fake_executable(path: &Path, content: &str) {
        use std::os::unix::fs::PermissionsExt;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, content).unwrap();
        let mut perms = std::fs::metadata(path).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(path, perms).unwrap();
    }

    /// 合成哨兵密码：捕获实际子进程 argv/env/stdin/stdout/stderr，
    /// argv 与公开输出均不含哨兵，SQL 仅经受控 stdin 承载。
    #[cfg(unix)]
    #[tokio::test]
    async fn psql_stdin_keeps_sql_out_of_argv_and_feeds_it_via_stdin() {
        let dir = unique_tmp("psql-stdin-capture");
        let bin = dir.join("bin");
        let argv_path = dir.join("argv.txt");
        let env_path = dir.join("env.txt");
        let stdin_path = dir.join("stdin.txt");
        let script = format!(
            "#!/bin/sh\n\
             printf '%s\\n' \"$@\" > '{argv}'\n\
             env > '{env}'\n\
             cat > '{stdin}'\n\
             printf 'fake-stderr\\n' >&2\n\
             printf 'fake-ok\\n'\n",
            argv = argv_path.display(),
            env = env_path.display(),
            stdin = stdin_path.display(),
        );
        write_fake_executable(&bin.join("psql"), &script);

        const SENTINEL: &str = "SynthSentinel-Pwd-0_1.~Za";
        let sql = format!("ALTER USER postgres PASSWORD $${SENTINEL}$$;");
        let out = psql_stdin(
            &dir,
            "127.0.0.1",
            5432,
            "postgres",
            "postgres",
            "trust-dummy",
            &sql,
            Duration::from_secs(5),
        )
        .await
        .expect("fake psql should succeed");

        assert_eq!(out, "fake-ok");
        assert!(!out.contains(SENTINEL), "stdout 泄露哨兵");

        let argv = std::fs::read_to_string(&argv_path).unwrap();
        let env = std::fs::read_to_string(&env_path).unwrap();
        let stdin = std::fs::read_to_string(&stdin_path).unwrap();
        assert!(!argv.contains(SENTINEL), "argv 泄露哨兵密码");
        assert!(
            argv.lines().any(|l| l == "-f"),
            "psql 须用 -f 从 stdin 读取"
        );
        assert!(argv.lines().any(|l| l == "-"), "psql 须以 -f - 读取 stdin");
        assert!(!env.contains(SENTINEL), "env 泄露哨兵密码");
        assert!(env.contains("PGPASSWORD=trust-dummy"), "连接密码须仍经 env");
        assert_eq!(stdin, sql, "ALTER USER SQL 必须完整经受控 stdin 传递");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// psql_stdin 超时：子进程未在时限内结束 → ToolError::Timeout（kill_on_drop 终止子进程）。
    #[cfg(unix)]
    #[tokio::test]
    async fn psql_stdin_timeout_returns_timeout_error() {
        let dir = unique_tmp("psql-stdin-timeout");
        write_fake_executable(&dir.join("bin").join("psql"), "#!/bin/sh\nsleep 10\n");
        let err = psql_stdin(
            &dir,
            "127.0.0.1",
            5432,
            "postgres",
            "postgres",
            "trust-dummy",
            "select 1",
            Duration::from_millis(100),
        )
        .await
        .expect_err("should timeout");
        assert!(
            matches!(err, ToolError::Timeout),
            "expected Timeout, got {err}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
