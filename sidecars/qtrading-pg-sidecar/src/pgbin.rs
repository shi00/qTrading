//! PostgreSQL 二进制调用层（pg_plan §12.1：sidecar 从安装目录解析工具路径）。
//!
//! 统一经 `run_tool` 执行 bundled 工具（initdb/pg_ctl/psql/pg_dump/pg_restore/pg_controldata），
//! 密码只走 `PGPASSWORD` 环境变量，禁止上命令行（R9）。
//! 分级停止（§7.3）：smart 25s → fast 5s → kill fallback，总预算 ≤ 32s。

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

/// postmaster.pid 解析结果（行 1 = PID，行 4 = port）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PostmasterInfo {
    pub pid: u32,
    pub port: Option<u16>,
}

pub fn read_postmaster_pid(data_dir: &Path) -> Option<PostmasterInfo> {
    let content = std::fs::read_to_string(data_dir.join("postmaster.pid")).ok()?;
    let mut lines = content.lines();
    let pid: u32 = lines.next()?.trim().parse().ok()?;
    let port = lines.nth(2).and_then(|l| l.trim().parse().ok());
    Some(PostmasterInfo { pid, port })
}

// ---- 进程存活/终止（跨平台） ----

#[cfg(windows)]
pub fn process_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ACCESS_DENIED};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    // STILL_ACTIVE = 259（Windows SDK 约定）；windows_sys 0.59 未直接导出该常量，用字面量。
    const STILL_ACTIVE: u32 = 259;
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
        unsafe { GetExitCodeProcess(handle, &mut exit_code) != 0 && exit_code == STILL_ACTIVE };
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

#[cfg(windows)]
fn force_terminate(pid: u32) {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};
    // SAFETY: 句柄非空才 TerminateProcess，之后总是 CloseHandle。
    let handle = unsafe { OpenProcess(PROCESS_TERMINATE, 0, pid) };
    if !handle.is_null() {
        unsafe {
            let _ = TerminateProcess(handle, 1);
            let _ = CloseHandle(handle);
        }
    }
}

#[cfg(unix)]
fn force_terminate(pid: u32) {
    // SAFETY: SIGKILL 兜底；目标不存在时错误忽略。
    unsafe {
        libc::kill(pid as libc::pid_t, libc::SIGKILL);
    }
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

#[derive(Debug)]
pub struct StopOutcome {
    pub mode: StopMode,
    pub stopped: bool,
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

/// 分级停止：smart(25s) → fast(5s) → SIGQUIT/TerminateProcess 兜底。
/// `pid` 用于兜底与存活确认；None 时仅凭 pg_ctl 结果判断。
pub async fn graded_stop(install_dir: &Path, data_dir: &Path, pid: Option<u32>) -> StopOutcome {
    if pg_ctl_stop(install_dir, data_dir, "smart", SMART_STOP_SECS).await {
        return StopOutcome {
            mode: StopMode::Smart,
            stopped: true,
        };
    }
    tracing::warn!("smart stop failed/timeout, escalating to fast stop");
    if pg_ctl_stop(install_dir, data_dir, "fast", FAST_STOP_SECS).await {
        return StopOutcome {
            mode: StopMode::Fast,
            stopped: true,
        };
    }
    tracing::warn!("fast stop failed/timeout, kill fallback engaged");
    if let Some(pid) = pid {
        #[cfg(unix)]
        sigquit(pid);
        #[cfg(windows)]
        {
            let pid_s = pid.to_string();
            let _ = run_tool(
                &tool_path(install_dir, "pg_ctl"),
                &["kill", "QUIT", &pid_s],
                &[],
                Duration::from_secs(5),
            )
            .await;
        }
        if wait_process_exit(pid, Duration::from_secs(KILL_GRACE_SECS)).await {
            return StopOutcome {
                mode: StopMode::KillFallback,
                stopped: true,
            };
        }
        force_terminate(pid);
        let stopped = wait_process_exit(pid, Duration::from_secs(KILL_GRACE_SECS)).await;
        return StopOutcome {
            mode: StopMode::KillFallback,
            stopped,
        };
    }
    StopOutcome {
        mode: StopMode::KillFallback,
        stopped: false,
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

    /// `graded_stop`：install_dir 不含 pg_ctl → smart/fast 均失败 →
    /// pid=Some(dead) 时 kill fallback 路径 → stopped=true（进程已死）。
    #[tokio::test]
    async fn graded_stop_nonexistent_pg_ctl_with_dead_pid() {
        let dir = unique_tmp("graded-stop-dead");
        std::fs::create_dir_all(&dir).unwrap();
        let dead_pid = 4_000_000u32;
        let outcome = graded_stop(&dir, &dir, Some(dead_pid)).await;
        assert_eq!(outcome.mode, StopMode::KillFallback);
        assert!(outcome.stopped, "dead pid should be detected as stopped");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// `graded_stop`：pid=None 且 pg_ctl 不可用 → KillFallback + stopped=false。
    #[tokio::test]
    async fn graded_stop_no_pid_returns_not_stopped() {
        let dir = unique_tmp("graded-stop-nopid");
        std::fs::create_dir_all(&dir).unwrap();
        let outcome = graded_stop(&dir, &dir, None).await;
        assert_eq!(outcome.mode, StopMode::KillFallback);
        assert!(!outcome.stopped, "no pid + no pg_ctl → not stopped");
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
}
