//! Rust sidecar 集成测试 — 9 大 roundtrip 场景（pg_plan §17.2）。
//!
//! 所有测试串行执行（--test-threads=1），避免端口/锁冲突。
//! 首个测试会下载+解压 PostgreSQL binaries（约 30MB），后续测试复用缓存。

mod common;

use common::*;
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;

/// 1. setup/run/status/stop 完整生命周期 roundtrip（§17.2 场景 1）。
///
/// sidecar 运行时 stop 命令返回 50（§13.3 锁冲突），所以通过关闭 stdin 触发优雅停止。
#[test]
fn test_setup_run_status_stop_roundtrip() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_1");

    // run → ready JSON
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");
    assert_eq!(ready["database"], "qtrading");
    assert!(ready["port"].as_u64().unwrap_or(0) > 0);

    // status → running
    let st = status_json(&data_dir);
    assert_eq!(st["status"], "running");

    // 关闭 stdin 触发 sidecar 优雅停止
    graceful_stop(&mut child);
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    assert_eq!(exit, 0);

    // status → stopped
    let st = status_json(&data_dir);
    assert_eq!(st["status"], "stopped");
}

/// 2. 随机端口启动（§17.2 场景 2）。
#[test]
fn test_random_port_start() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_2");

    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let port = ready_port(&ready).expect("port in ready JSON");
    assert!(port > 0, "random port should be non-zero");

    // status 端口一致
    let st = status_json(&data_dir);
    assert_eq!(st["port"], ready["port"]);

    cleanup_sidecar(&mut child, &data_dir);
}

/// 3. 已运行实例重复 run → exit 50（§17.2 场景 3）。
#[test]
fn test_duplicate_run_rejected() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_3");

    let mut child1 = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child1, READY_TIMEOUT);

    // 第二次 run → exit 50 (LOCK_CONFLICT)
    let mut child2 = spawn_run(&data_dir);
    let exit = wait_for_exit(&mut child2, Duration::from_secs(30));
    assert_eq!(exit, 50);

    cleanup_sidecar(&mut child1, &data_dir);
}

/// 4. stale pid recovery（§17.2 场景 4）。
#[test]
fn test_stale_pid_recovery() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_4");

    // 首次 run → ready → 优雅停止
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    let _ = wait_for_exit(&mut child, STOP_TIMEOUT);

    // 写入 stale postmaster.pid（指向不存在的 pid）
    let stale_pid = format!(
        "999999\n{}\n{}\n55432\n/tmp\n127.0.0.1\n",
        data_dir.display(),
        1234567890u64
    );
    std::fs::write(data_dir.join("postmaster.pid"), stale_pid).unwrap();

    // 再次 run → 应清理 stale pid → 启动成功
    let mut child2 = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");

    cleanup_sidecar(&mut child2, &data_dir);
}

/// 5. create database idempotent（§17.2 场景 5）。
#[test]
fn test_create_database_idempotent() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_5");

    // 首次 run → ready → graceful stop（外部 stop 在 sidecar 运行时返回 50 锁冲突，§13.3）
    let mut child1 = spawn_run(&data_dir);
    let ready1 = wait_for_ready(&mut child1, READY_TIMEOUT);
    assert_eq!(ready1["database"], "qtrading");
    graceful_stop(&mut child1);
    let _ = wait_for_exit(&mut child1, STOP_TIMEOUT);

    // 再次 run → database 仍存在
    let mut child2 = spawn_run(&data_dir);
    let ready2 = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(ready2["database"], "qtrading");

    cleanup_sidecar(&mut child2, &data_dir);
}

/// 6. dump/restore roundtrip（§17.2 场景 6）。
#[test]
fn test_dump_restore_roundtrip() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_6");

    // run → ready
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);

    // dump（运行中实例直连）
    let dump_path = data_dir.parent().unwrap().join("backup.dump");
    let (dump_code, dump_stderr) = dump_sidecar_with_stderr(&data_dir, &dump_path);
    if dump_code != 0 {
        panic!(
            "dump failed with code {dump_code}; stderr:\n=== BEGIN ===\n{dump_stderr}\n=== END ==="
        );
    }
    assert!(dump_path.exists(), "dump file should exist");

    // 优雅停止 sidecar（外部 stop 命令在 sidecar 运行时返回 50 锁冲突，§13.3）
    graceful_stop(&mut child);
    let _ = wait_for_exit(&mut child, STOP_TIMEOUT);

    // restore → 原目录保留为 .bak-*
    let (restore_code, restore_stderr) = restore_sidecar_with_stderr(&data_dir, &dump_path);
    if restore_code != 0 {
        panic!(
            "restore failed with code {restore_code}; stderr:\n=== BEGIN ===\n{restore_stderr}\n=== END ==="
        );
    }

    // 验证 .bak-* 目录存在
    let parent = data_dir.parent().unwrap();
    let bak_dirs: Vec<_> = std::fs::read_dir(parent)
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_string_lossy().starts_with("data.bak-"))
        .collect();
    assert!(
        !bak_dirs.is_empty(),
        "should have .bak-* directory after restore"
    );

    // 清理 restore 产生的 .bak 目录
    for entry in bak_dirs {
        let _ = std::fs::remove_dir_all(entry.path());
    }
}

/// 7. parent_pid 消失后 sidecar 停止 PostgreSQL（§17.2 场景 7 / §17.6 #9）。
#[test]
fn test_parent_pid_disappear_triggers_stop() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_7");

    // 启动辅助进程作为 "父进程"
    let mut helper = spawn_helper();
    let helper_pid_u32 = helper.id();
    let helper_pid = helper_pid_u32.to_string();

    // 验证 helper 启动后确实存活
    assert!(
        process_alive(helper_pid_u32),
        "helper should be alive after spawn"
    );

    // 用辅助进程 pid 启动 sidecar
    let mut child = spawn_run_with(&data_dir, &["--parent-pid", &helper_pid]);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);

    // 异步捕获 sidecar stderr 用于诊断
    let stderr = child.stderr.take().expect("stderr piped");
    let (stderr_tx, stderr_rx) = std::sync::mpsc::channel::<String>();
    std::thread::spawn(move || {
        use std::io::Read;
        let mut buf = String::new();
        let mut reader = stderr;
        let _ = reader.read_to_string(&mut buf);
        let _ = stderr_tx.send(buf);
    });

    // 显式 kill helper 模拟父进程崩溃（Rust Child::drop 不发送终止信号）
    let _ = helper.kill();
    let _ = helper.wait();

    // 验证 helper 进程确实消失（OpenProcess 返回 null）
    let start = std::time::Instant::now();
    while start.elapsed() < Duration::from_secs(5) {
        if !process_alive(helper_pid_u32) {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(
        !process_alive(helper_pid_u32),
        "helper pid {helper_pid_u32} should be gone after kill+wait"
    );

    // 等待 sidecar 检测到父进程消失 → 优雅停止 → exit 0
    // parent_tick 间隔 500ms；graded_stop 总预算 ≤32s（smart 25s + fast 5s + kill），用 STOP_TIMEOUT 60s
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    if exit != 0 {
        let stderr_content = stderr_rx
            .recv_timeout(Duration::from_secs(2))
            .unwrap_or_default();
        panic!(
            "sidecar should stop gracefully when parent pid disappears; got exit {exit}\n\
             === SIDECAR STDERR ===\n{stderr_content}\n=== END STDERR ==="
        );
    }

    // 验证 postgres 已停止
    let st = status_json(&data_dir);
    assert_eq!(st["status"], "stopped");
}

/// 8. 优雅退出（§17.2 场景 8）。
///
/// sidecar 运行时外部 stop 命令返回 50（§13.3 锁冲突），改走 stdin EOF 触发 sidecar 自身 graceful stop（§7.3 supervise）。
#[test]
fn test_graceful_exit_via_stdin_eof() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_8");

    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");

    // 关闭 stdin 触发 sidecar 优雅停止
    graceful_stop(&mut child);

    // sidecar 进程退出
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    assert_eq!(exit, 0);

    // postgres 进程已消失
    // graded_stop 已停 postgres 主进程；Windows 下子进程退出可能有延迟，轮询等待 10s
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    while std::time::Instant::now() < deadline {
        if !process_alive(pg_pid) {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    assert!(!process_alive(pg_pid), "postgres process should be gone");

    // status → stopped
    let st = status_json(&data_dir);
    assert_eq!(st["status"], "stopped");
}

/// 9. doctor 独立运行（qTrading 未运行时）（§17.2 场景 9）。
#[test]
fn test_doctor_independent_run() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_9");

    // 在未初始化的 data_dir 上运行 doctor
    let doc = doctor_json(&data_dir);
    assert_eq!(doc["initialized"], false);
    assert!(doc["issues"].is_array());
}

/// 10. 维护命令锁拒绝（qTrading 运行时）（§17.2 场景 10/§17.6 #5）。
///
/// 按 AI-28：dump 是 SQL 级操作不占锁（运行中实例直连 pg_dump）→ exit 0；
/// restore 是 PGDATA 级必持锁，sidecar 运行时 → exit 50。
#[test]
fn test_maintenance_command_lock_rejection() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_10");

    // run → ready（sidecar 持有维护锁）
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);

    // dump（SQL 级，不占锁）→ exit 0，运行中实例直连 pg_dump
    let dump_path = data_dir.parent().unwrap().join("backup.dump");
    let dump_code = dump_sidecar(&data_dir, &dump_path);
    assert_eq!(
        dump_code, 0,
        "dump is SQL-level (AI-28), should succeed while sidecar is running"
    );
    assert!(dump_path.exists(), "dump file should exist");

    // restore（PGDATA 级，必持锁）→ exit 50
    let restore_code = restore_sidecar(&data_dir, &dump_path);
    assert_eq!(
        restore_code, 50,
        "restore is PGDATA-level, must be rejected while sidecar is running"
    );

    // 清理 dump 文件
    let _ = std::fs::remove_file(&dump_path);

    cleanup_sidecar(&mut child, &data_dir);
}

/// 11. kill_fallback 历史后重启执行系统目录完整性检查（§7.3 / MAJ-1）。
///
/// 验证：上次 last_stop_mode=kill_fallback 时，下次 run 追加 pg_catalog 系统目录
/// 检查（pg_database/pg_namespace/pg_class count > 0）。数据干净时应正常通过。
///
/// 测试策略：正常启动 + graceful_stop → 篡改 state.json 模拟 kill_fallback 历史
/// → 再次 run → 验证 ready JSON 正常出现（说明 post_kill_fallback_check 通过）。
#[test]
fn test_kill_fallback_history_post_check() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_11");

    // 第一次 run → ready → graceful_stop（正常停止，last_stop_mode=smart）
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    assert_eq!(exit, 0, "first run should exit 0");

    // 篡改 state.json 模拟 kill_fallback 历史
    // Layout::from_data_dir 用 data_dir.parent() 作为 base17，runtime 在 base17 下
    let state_path = data_dir
        .parent()
        .unwrap()
        .join("runtime")
        .join("state.json");
    assert!(
        state_path.exists(),
        "state.json should exist after stop: {}",
        state_path.display()
    );
    let state_content = std::fs::read_to_string(&state_path).unwrap();
    let mut state_json: serde_json::Value = serde_json::from_str(&state_content).unwrap();
    state_json["last_stop_mode"] = serde_json::Value::String("kill_fallback".to_string());
    state_json["kill_fallback_count"] = serde_json::Value::Number(1.into());
    std::fs::write(
        &state_path,
        serde_json::to_string_pretty(&state_json).unwrap(),
    )
    .unwrap();

    // 第二次 run → 验证 post_kill_fallback_check 通过（数据干净，系统目录非空）
    let mut child2 = spawn_run(&data_dir);
    let ready2 = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(
        ready2["status"], "running",
        "should ready after kill_fallback post-check passes"
    );

    cleanup_sidecar(&mut child2, &data_dir);
}

/// 12. reset-password 成功 roundtrip（§13.7.8）。
///
/// 验证：sidecar 正常停止后调 reset-password → exit 0 → password file 已更新 →
/// 再次 run 验证新密码可连接（ready JSON status=running）。
#[test]
fn test_reset_password_succeeds() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_12");

    // 1. run → ready → graceful_stop（sidecar 退出，释放维护锁）
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    assert_eq!(exit, 0, "first run should exit 0");

    // 2. 读取旧 password file 内容（paths.rs：data_dir.parent()/runtime/password）
    let password_file = data_dir.parent().unwrap().join("runtime").join("password");
    assert!(
        password_file.exists(),
        "password file should exist after run"
    );
    let old_pwd = std::fs::read_to_string(&password_file).expect("read old password");

    // 3. 调 reset-password → exit 0（sidecar 已退出，锁可用）
    let (reset_code, reset_stderr) = reset_password_sidecar(&data_dir);
    assert_eq!(
        reset_code, 0,
        "reset-password should succeed when sidecar is stopped\n\
         === SIDECAR STDERR ===\n{reset_stderr}\n=== END STDERR ==="
    );

    // 4. 读取新 password file 内容，验证已变更
    let new_pwd = std::fs::read_to_string(&password_file).expect("read new password");
    assert_ne!(new_pwd, old_pwd, "password should be rotated");
    assert!(!new_pwd.is_empty(), "new password should not be empty");

    // 5. 再次 run → ready（验证新密码可连接，pg_hba.conf 已重写生效）
    let mut child2 = spawn_run(&data_dir);
    let ready2 = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(
        ready2["status"], "running",
        "sidecar should ready with new password"
    );

    cleanup_sidecar(&mut child2, &data_dir);
}

/// 13. reset-password 锁冲突（§13.7.8 + §13.3）。
///
/// 验证：sidecar 运行中（持锁）调 reset-password → exit 50（LOCK_CONFLICT）。
#[test]
fn test_reset_password_lock_conflict() {
    let (_tmp, data_dir) = unique_data_dir("roundtrip_13");

    // run → ready（sidecar 持锁运行中）
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);

    // 调 reset-password → exit 50（LOCK_CONFLICT）
    let (reset_code, _reset_stderr) = reset_password_sidecar(&data_dir);
    assert_eq!(
        reset_code, 50,
        "reset-password must be rejected while sidecar is running"
    );

    cleanup_sidecar(&mut child, &data_dir);
}

// ---- 辅助函数 ----

/// 执行 `reset-password` 命令，返回 (exit code, stderr)。
/// 失败时调用方应将 stderr 纳入 assert 诊断信息，便于 CI 排查（Windows 上 postgres --single
/// 启动失败的具体原因只能从 stderr 获取）。
fn reset_password_sidecar(data_dir: &Path) -> (u8, String) {
    let output = std::process::Command::new(sidecar_path())
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

/// 启动一个短命辅助进程（用于 parent_pid 测试）。
///
/// Windows 上避免 `cmd /c ping` 包装：kill cmd 后，cmd 进程对象被 ping 子进程继承句柄，
/// OpenProcess 仍返回有效句柄导致 sidecar 误判父进程仍存活。直接启动 ping 避免 cmd 层。
fn spawn_helper() -> std::process::Child {
    #[cfg(windows)]
    {
        std::process::Command::new("ping")
            .args(["-n", "60", "127.0.0.1"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn helper process")
    }
    #[cfg(not(windows))]
    {
        std::process::Command::new("sleep")
            .arg("60")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn helper process")
    }
}

/// 跨平台进程存活检查（集成测试用，不依赖 sidecar 内部 API）。
///
/// Windows 上 OpenProcess 对已退出但仍有句柄持有的进程返回有效句柄（Windows 进程对象生命周期），
/// 需进一步用 GetExitCodeProcess 检查退出码是否为 STILL_ACTIVE (259)。
fn process_alive(pid: u32) -> bool {
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{
            GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        };
        const STILL_ACTIVE: u32 = 259;
        let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if handle.is_null() {
            return false;
        }
        let mut exit_code: u32 = 0;
        let alive =
            unsafe { GetExitCodeProcess(handle, &mut exit_code) != 0 && exit_code == STILL_ACTIVE };
        unsafe {
            let _ = CloseHandle(handle);
        }
        alive
    }
    #[cfg(not(windows))]
    {
        // SAFETY: signal 0 仅探测存在性
        unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
    }
}
