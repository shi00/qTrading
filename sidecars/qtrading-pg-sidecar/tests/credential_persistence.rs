//! D16-T03 集成回归：reset-password「ALTER 成功后再写密码文件」失败后的可恢复性。
//!
//! 覆盖用户要求的真实运行证据：临时实例 + ALTER USER 已成功，但新密码落盘失败
//! （持久 password 路径被非空目录占据，root 也无法 rename 覆盖）时：
//! - 必须返回 `PASSWORD_FAILED`（exit 16），错误信息明确指向密码文件写入而非 ALTER；
//! - 不得残留 `.password.tmp-*` 半截临时文件；
//! - 阻塞移除后再次 reset-password 必须成功，新凭据 0600 且可用于重新 run / 直连认证。
//!
//! 串行执行（--test-threads=1），避免端口/锁/PG 缓存冲突。

mod common;

// 复用生产 password 模块读取逻辑：Windows 上密码文件是 DPAPI 加密的，
// 直接比较字节会把「同一密码」误判为不同。
#[path = "../src/password.rs"]
#[allow(dead_code)]
mod password;

use common::*;
use std::path::{Path, PathBuf};
use std::process::Command;

fn reset_password_sidecar(data_dir: &Path) -> (u8, String) {
    let output = Command::new(sidecar_path())
        .arg("reset-password")
        .arg("--data-dir")
        .arg(data_dir)
        .output()
        .expect("failed to run reset-password");
    (
        output.status.code().unwrap_or(255) as u8,
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

fn runtime_password_path(data_dir: &Path) -> PathBuf {
    data_dir
        .parent()
        .expect("data_dir has parent")
        .join("runtime")
        .join("password")
}

fn tmp_residue(runtime_dir: &Path) -> Vec<String> {
    std::fs::read_dir(runtime_dir)
        .expect("read runtime dir")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|name| name.starts_with(".password.tmp-"))
        .collect()
}

/// reset-password 在 ALTER 成功后写持久密码文件失败：exit 16、无临时残留；
/// 解除阻塞后再次 reset 成功，新凭据 0600、可重新 run 并直连认证。
#[test]
fn test_reset_password_recovers_after_password_file_write_failure() {
    let (_tmp, data_dir) = unique_data_dir("cred-reset-file-fail");
    let password_path = runtime_password_path(&data_dir);
    let runtime_dir = password_path.parent().expect("runtime dir").to_path_buf();

    // 初始集群：run → ready → 优雅停止，得到可用凭据。
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    assert_eq!(
        wait_for_exit(&mut child, STOP_TIMEOUT),
        0,
        "first run should exit 0"
    );
    let old_password = password::read_password_file(&password_path).expect("initial credential");

    // 用非空目录占据持久密码路径：rename 无法覆盖目录，且 root 也不能绕过。
    std::fs::remove_file(&password_path).expect("remove initial password file");
    std::fs::create_dir_all(&password_path).expect("create blocker directory");
    std::fs::write(password_path.join("blocker"), b"keep").expect("write blocker marker");

    // 第一次 reset：ALTER 已成功，但密码文件写入失败 → exit 16。
    let (code, stderr) = reset_password_sidecar(&data_dir);
    assert_eq!(
        code, 16,
        "password file write failure must map to PASSWORD_FAILED; stderr:\n{stderr}"
    );
    assert!(
        stderr.contains("写入新 password file 失败"),
        "stderr must identify credential persistence failure; got:\n{stderr}"
    );
    assert!(
        !stderr.contains("ALTER USER"),
        "ALTER USER itself succeeded; stderr must not report it as failed; got:\n{stderr}"
    );
    assert!(
        tmp_residue(&runtime_dir).is_empty(),
        "failed credential write must not leave temporary files: {:?}",
        tmp_residue(&runtime_dir)
    );
    assert_eq!(
        std::fs::read(password_path.join("blocker")).expect("blocker still readable"),
        b"keep",
        "failed write must not modify the blocking target"
    );

    // 解除阻塞后再次 reset：必须恢复（ALTER + 落盘都成功）。
    std::fs::remove_dir_all(&password_path).expect("remove blocker directory");
    let (code2, stderr2) = reset_password_sidecar(&data_dir);
    assert_eq!(
        code2, 0,
        "reset-password must recover after path is unblocked; stderr:\n{stderr2}"
    );
    let new_password =
        password::read_password_file(&password_path).expect("credential after recovery");
    // R9：用 assert! 避免断言失败时把明文密码写入测试输出。
    assert!(
        new_password != old_password,
        "reset-password must rotate the credential"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&password_path)
            .expect("stat recovered password file")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600, "recovered credential must be 0600");
    }

    // 新凭据可用：重新 run 走密码文件连接，能 ready 即证明文件与 DB 一致。
    let mut child2 = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");
    let port = ready_port(&ready).expect("port in ready JSON");
    let psql_name = if cfg!(windows) { "psql.exe" } else { "psql" };
    let psql = data_dir
        .parent()
        .expect("data_dir has parent")
        .join("install")
        .join("bin")
        .join(psql_name);
    let out = Command::new(&psql)
        .args([
            "-X",
            "-q",
            "-A",
            "-t",
            "-h",
            "127.0.0.1",
            "-p",
            &port.to_string(),
            "-U",
            "postgres",
            "-d",
            "qtrading",
            "-c",
            "select current_user",
        ])
        .env("PGPASSWORD", &new_password)
        .output()
        .expect("run bundled psql");
    assert!(
        out.status.success(),
        "recovered credential must authenticate; stderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "postgres");

    cleanup_sidecar(&mut child2, &data_dir);
}

/// 状态文件落盘为 tmp + rename：正常 run/stop 后 runtime 不得残留 state 临时文件。
#[test]
fn test_state_write_leaves_no_temporary_files_across_lifecycle() {
    let (_tmp, data_dir) = unique_data_dir("cred-state-atomic");
    let runtime_dir = data_dir
        .parent()
        .expect("data_dir has parent")
        .join("runtime");

    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");
    assert!(runtime_dir.join("state.json").exists(), "state must exist");

    graceful_stop(&mut child);
    assert_eq!(
        wait_for_exit(&mut child, STOP_TIMEOUT),
        0,
        "run should stop cleanly"
    );

    let residue: Vec<String> = std::fs::read_dir(&runtime_dir)
        .expect("read runtime dir")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|name| name.starts_with("state.json.tmp-"))
        .collect();
    assert!(
        residue.is_empty(),
        "state writes must not leave temporary files: {residue:?}"
    );
}
