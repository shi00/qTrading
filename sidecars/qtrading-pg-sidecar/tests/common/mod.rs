//! 集成测试共享辅助（pg_plan §17.2/§17.6）。
//!
//! 提供 sidecar binary 路径解析、临时 data_dir 管理、ready JSON 解析、
//! 子进程生命周期清理等通用功能。

use assert_cmd::cargo::cargo_bin;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::time::Duration;

/// sidecar run ready JSON 等待超时（首次需下载+解压 PostgreSQL binaries）。
pub const READY_TIMEOUT: Duration = Duration::from_secs(300);
/// sidecar stop 等待超时。
pub const STOP_TIMEOUT: Duration = Duration::from_secs(60);

/// 获取 sidecar binary 路径。
pub fn sidecar_path() -> PathBuf {
    cargo_bin("qtrading-pg-sidecar")
}

/// 创建唯一临时 data_dir。
/// 返回 (TempDir 守卫, data_dir 路径)。TempDir 守卫保持存活则目录自动清理。
/// sidecar 的 install_dir / password_file 等兄弟目录也在 TempDir 内。
pub fn unique_data_dir(test_name: &str) -> (tempfile::TempDir, PathBuf) {
    let parent = tempfile::Builder::new()
        .prefix(&format!("qts-it-{test_name}-"))
        .tempdir()
        .expect("create tempdir");
    let data_dir = parent.path().join("data");
    std::fs::create_dir_all(&data_dir).expect("create data_dir");
    (parent, data_dir)
}

/// 启动 `run` 命令，返回子进程（stdin/stdout piped）。
/// stdin piped 以便测试通过关闭 stdin 触发 sidecar 优雅停止（stdin EOF → supervise 检测）。
pub fn spawn_run(data_dir: &Path) -> Child {
    std::process::Command::new(sidecar_path())
        .arg("run")
        .arg("--data-dir")
        .arg(data_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn sidecar run")
}

/// 启动 `run` 命令带额外参数。
pub fn spawn_run_with(data_dir: &Path, extra_args: &[&str]) -> Child {
    let mut cmd = std::process::Command::new(sidecar_path());
    cmd.arg("run").arg("--data-dir").arg(data_dir);
    for arg in extra_args {
        cmd.arg(arg);
    }
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn sidecar run")
}

/// 通知 sidecar 优雅停止：关闭 stdin 管道触发 EOF。
/// sidecar supervise 循环检测 stdin EOF 后走 graceful stop 流程（§7.3）。
/// 调用后仍需 `wait_for_exit` 等待 sidecar 实际退出。
#[allow(dead_code)]
pub fn graceful_stop(child: &mut Child) {
    drop(child.stdin.take());
}

/// 从子进程 stdout 读取第一行 ready JSON，超时 panic。
pub fn wait_for_ready(child: &mut Child, timeout: Duration) -> serde_json::Value {
    let stdout = child.stdout.take().expect("stdout piped");
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        if reader.read_line(&mut line).is_ok() {
            let _ = tx.send(line);
        }
    });
    match rx.recv_timeout(timeout) {
        Ok(line) => serde_json::from_str(line.trim()).unwrap_or_else(|e| {
            let _ = child.kill();
            panic!("ready JSON parse failed: {e}; line: {line:?}");
        }),
        Err(_) => {
            let exit_info = if let Ok(status) = child.try_wait() {
                format!("child exited: {status:?}")
            } else {
                "child still running but no ready JSON".to_string()
            };
            let _ = child.kill();
            let _ = child.wait();
            panic!("sidecar did not produce ready JSON within {timeout:?}: {exit_info}");
        }
    }
}

/// 执行 `stop` 命令，返回 exit code。
pub fn stop_sidecar(data_dir: &Path) -> u8 {
    let output = std::process::Command::new(sidecar_path())
        .arg("stop")
        .arg("--data-dir")
        .arg(data_dir)
        .output()
        .expect("failed to run stop");
    output.status.code().unwrap_or(255) as u8
}

/// 执行 `status` 命令，解析 JSON。
pub fn status_json(data_dir: &Path) -> serde_json::Value {
    let output = std::process::Command::new(sidecar_path())
        .arg("status")
        .arg("--data-dir")
        .arg(data_dir)
        .output()
        .expect("failed to run status");
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(stdout.trim()).unwrap_or_else(|e| {
        panic!(
            "status JSON parse failed: {e}; stdout: {stdout:?}; stderr: {}",
            String::from_utf8_lossy(&output.stderr)
        )
    })
}

/// 执行 `doctor` 命令，解析 JSON。
pub fn doctor_json(data_dir: &Path) -> serde_json::Value {
    let output = std::process::Command::new(sidecar_path())
        .arg("doctor")
        .arg("--data-dir")
        .arg(data_dir)
        .output()
        .expect("failed to run doctor");
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(stdout.trim()).unwrap_or_else(|e| {
        panic!(
            "doctor JSON parse failed: {e}; stdout: {stdout:?}; stderr: {}",
            String::from_utf8_lossy(&output.stderr)
        )
    })
}

/// 执行 `dump` 命令，返回 exit code。
#[allow(dead_code)]
pub fn dump_sidecar(data_dir: &Path, output_path: &Path) -> u8 {
    let output = std::process::Command::new(sidecar_path())
        .arg("dump")
        .arg("--data-dir")
        .arg(data_dir)
        .arg("--output")
        .arg(output_path)
        .output()
        .expect("failed to run dump");
    output.status.code().unwrap_or(255) as u8
}

/// 执行 `dump` 命令，返回 (exit_code, stderr)，用于测试诊断。
#[allow(dead_code)]
pub fn dump_sidecar_with_stderr(data_dir: &Path, output_path: &Path) -> (u8, String) {
    let output = std::process::Command::new(sidecar_path())
        .arg("dump")
        .arg("--data-dir")
        .arg(data_dir)
        .arg("--output")
        .arg(output_path)
        .output()
        .expect("failed to run dump");
    (
        output.status.code().unwrap_or(255) as u8,
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

/// 执行 `restore` 命令，返回 exit code。
#[allow(dead_code)]
pub fn restore_sidecar(data_dir: &Path, input_path: &Path) -> u8 {
    let output = std::process::Command::new(sidecar_path())
        .arg("restore")
        .arg("--data-dir")
        .arg(data_dir)
        .arg("--input")
        .arg(input_path)
        .output()
        .expect("failed to run restore");
    output.status.code().unwrap_or(255) as u8
}

/// 执行 `restore` 命令，返回 (exit_code, stderr)，用于测试诊断。
#[allow(dead_code)]
pub fn restore_sidecar_with_stderr(data_dir: &Path, input_path: &Path) -> (u8, String) {
    let output = std::process::Command::new(sidecar_path())
        .arg("restore")
        .arg("--data-dir")
        .arg(data_dir)
        .arg("--input")
        .arg(input_path)
        .output()
        .expect("failed to run restore");
    (
        output.status.code().unwrap_or(255) as u8,
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

/// 执行 `version --json` 命令，解析 JSON。
#[allow(dead_code)]
pub fn version_json() -> serde_json::Value {
    let output = std::process::Command::new(sidecar_path())
        .arg("version")
        .arg("--json")
        .output()
        .expect("failed to run version");
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(stdout.trim()).expect("version JSON parse failed")
}

/// 等待子进程退出，返回 exit code。超时则 kill。
pub fn wait_for_exit(child: &mut Child, timeout: Duration) -> u8 {
    let start = std::time::Instant::now();
    while start.elapsed() < timeout {
        match child.try_wait() {
            Ok(Some(status)) => return status.code().unwrap_or(255) as u8,
            Ok(None) => std::thread::sleep(Duration::from_millis(200)),
            Err(_) => return 255,
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    255
}

/// 清理 sidecar 子进程（kill + wait）并尝试 stop 命令兜底。
pub fn cleanup_sidecar(child: &mut Child, data_dir: &Path) {
    let _ = child.kill();
    let _ = child.wait();
    let _ = stop_sidecar(data_dir);
}

/// 从 ready JSON 提取 postgres pid。
pub fn ready_pid(ready: &serde_json::Value) -> Option<u32> {
    ready.get("pid").and_then(|v| v.as_u64()).map(|n| n as u32)
}

/// 从 ready JSON 提取端口。
#[allow(dead_code)]
pub fn ready_port(ready: &serde_json::Value) -> Option<u16> {
    ready.get("port").and_then(|v| v.as_u64()).map(|n| n as u16)
}
