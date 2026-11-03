//! D16-F03 集成回归：restore 不得切换仍被使用的源 PGDATA。
//!
//! 覆盖：
//! - sidecar 崩溃/被 kill 但 postgres 仍活（维护锁已被 OS 释放）→ restore exit 50，
//!   源 PGDATA 路径/pid 文件/版本文件不变，不产生 `.restore-*` / `.bak-*`；
//! - 显式 `stop` 确认实例停止后，restore 成功完成原子切换；
//! - sidecar 与 postgres 均死亡（真双死、stale pid 残留）→ restore 仍可恢复；
//! - PID 存活但无法证明归属 → restore exit 50 且无目录副作用（无需真实 PG）。
//!
//! 串行执行（--test-threads=1），避免端口/锁/PG 缓存冲突。

mod common;

// 复用生产 password 模块读取逻辑：Windows 上密码文件是 DPAPI 加密的，
// 直接比较字节会把「同一密码」误判为不同（DPAPI blob 每次加密随机）。
#[path = "../src/password.rs"]
#[allow(dead_code)]
mod password;

use common::*;
use std::path::{Path, PathBuf};
use std::process::Command;
#[cfg(windows)]
use std::process::Stdio;
use std::time::{Duration, Instant};

const LOCK_RELEASE_TIMEOUT: Duration = Duration::from_secs(10);
const PROCESS_EXIT_TIMEOUT: Duration = Duration::from_secs(10);

fn postmaster_pid_path(data_dir: &Path) -> PathBuf {
    data_dir.join("postmaster.pid")
}

/// 写真实格式的 postmaster.pid（6 行）。
fn write_pidfile(data_dir: &Path, pid: u32, started_at: i64) {
    std::fs::create_dir_all(data_dir).expect("create data dir");
    std::fs::write(
        postmaster_pid_path(data_dir),
        format!(
            "{pid}\n{}\n{started_at}\n55432\n/tmp\n127.0.0.1\n",
            data_dir.display()
        ),
    )
    .expect("write postmaster.pid");
}

/// sidecar 崩溃后维护锁由 OS 释放，restore 可能短暂仍读到锁冲突；只对锁冲突重试。
fn restore_after_lock_release(
    data_dir: &Path,
    input: &Path,
    timeout: Duration,
) -> (u8, String, String) {
    let start = Instant::now();
    loop {
        let out = restore_sidecar_with_output(data_dir, input);
        if out.0 == 50 && out.2.contains("维护锁冲突") && start.elapsed() < timeout {
            std::thread::sleep(Duration::from_millis(200));
            continue;
        }
        return out;
    }
}

fn stop_after_lock_release(data_dir: &Path, timeout: Duration) -> (u8, String) {
    let start = Instant::now();
    loop {
        let output = Command::new(sidecar_path())
            .arg("stop")
            .arg("--data-dir")
            .arg(data_dir)
            .output()
            .expect("failed to run stop");
        let code = output.status.code().unwrap_or(255) as u8;
        let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
        if code == 50 && start.elapsed() < timeout {
            std::thread::sleep(Duration::from_millis(200));
            continue;
        }
        return (code, stderr);
    }
}

fn sibling_dirs(data_dir: &Path) -> Vec<String> {
    let parent = data_dir.parent().expect("data_dir has parent");
    std::fs::read_dir(parent)
        .expect("read parent")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with("data.restore-") || n.starts_with("data.bak-"))
        .collect()
}

fn wait_until_dead(pid: u32) {
    let deadline = Instant::now() + PROCESS_EXIT_TIMEOUT;
    while Instant::now() < deadline && process_alive(pid) {
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(!process_alive(pid), "process {pid} should be dead");
}

/// 强杀 PostgreSQL（模拟无优雅停机的真双死场景）。
fn kill_hard(pid: u32) {
    #[cfg(unix)]
    {
        // SAFETY: SIGKILL 仅发送到调用方指定的、已由 ready JSON 确认的 postgres PID。
        unsafe {
            libc::kill(pid as libc::pid_t, libc::SIGKILL);
        }
    }
    #[cfg(windows)]
    {
        let status = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .expect("run taskkill");
        assert!(status.success(), "taskkill failed for pid {pid}");
    }
}

/// 跨平台进程存活检查（集成测试不依赖 sidecar 内部 API）。
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

/// PID 存活但无法证明属于源 PGDATA：restore 必须 fail closed，不创建目录、不改 pid 文件。
#[test]
fn test_restore_rejects_unverifiable_live_pid() {
    let (_tmp, data_dir) = unique_data_dir("rg_unverifiable");
    let dump_path = data_dir.parent().unwrap().join("input.dump");
    std::fs::write(&dump_path, b"not-a-real-dump").unwrap();

    write_pidfile(&data_dir, std::process::id(), 1_784_505_600);
    let before = std::fs::read(postmaster_pid_path(&data_dir)).unwrap();

    let (code, _stdout, stderr) = restore_sidecar_with_output(&data_dir, &dump_path);
    assert_eq!(
        code, 50,
        "restore must fail closed when a live PID cannot be proven to own PGDATA; stderr:\n{stderr}"
    );
    assert!(
        stderr.contains("无法证明"),
        "stderr should explain the identity failure; got:\n{stderr}"
    );
    assert_eq!(
        std::fs::read(postmaster_pid_path(&data_dir)).unwrap(),
        before,
        "postmaster.pid must not be modified"
    );
    assert!(
        sibling_dirs(&data_dir).is_empty(),
        "rejected restore must not create restore/bak dirs: {:?}",
        sibling_dirs(&data_dir)
    );
}

/// sidecar 崩溃但 postgres 仍活 → restore exit 50 且无切换/无新目录；
/// 显式 stop 确认后再次 restore → 成功切换并保留 .bak。
#[test]
fn test_restore_rejects_orphan_postgres_then_succeeds_after_stop() {
    let (_tmp, data_dir) = unique_data_dir("rg_orphan");
    let dump_path = data_dir.parent().unwrap().join("backup.dump");

    // 启动真实集群并在在线路径 dump（SQL 级，不占锁）。
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");
    let (dump_code, dump_stderr) = dump_sidecar_with_stderr(&data_dir, &dump_path);
    assert_eq!(
        dump_code, 0,
        "dump should succeed while instance is running; stderr:\n{dump_stderr}"
    );

    let pidfile_before = std::fs::read(postmaster_pid_path(&data_dir)).expect("read pidfile");
    let pg_version_before = std::fs::read(data_dir.join("PG_VERSION")).expect("read PG_VERSION");
    let source_path_before = std::fs::canonicalize(&data_dir).expect("canonicalize source");
    let pgdata_in_pidfile = String::from_utf8_lossy(&pidfile_before)
        .lines()
        .nth(1)
        .expect("pidfile line 2")
        .to_string();

    // 仅杀 sidecar，不杀 postgres：维护锁随进程释放，但源集群仍活。
    let _ = child.kill();
    let _ = child.wait();
    assert!(
        process_alive(pg_pid),
        "postgres {pg_pid} should survive sidecar crash"
    );

    let (code, _stdout, stderr) =
        restore_after_lock_release(&data_dir, &dump_path, LOCK_RELEASE_TIMEOUT);
    assert_eq!(
        code, 50,
        "restore must reject a still-running source cluster; stderr:\n{stderr}"
    );
    assert!(
        stderr.contains("stop"),
        "stderr should point the user to stop; got:\n{stderr}"
    );
    assert!(
        process_alive(pg_pid),
        "restore must not kill the orphan postgres"
    );
    assert_eq!(
        std::fs::read(postmaster_pid_path(&data_dir)).unwrap(),
        pidfile_before,
        "source postmaster.pid must be unchanged"
    );
    assert_eq!(
        std::fs::read(data_dir.join("PG_VERSION")).unwrap(),
        pg_version_before,
        "source PG_VERSION must be unchanged"
    );
    assert!(
        sibling_dirs(&data_dir).is_empty(),
        "rejected restore must not create restore/bak dirs: {:?}",
        sibling_dirs(&data_dir)
    );
    assert_eq!(
        std::fs::canonicalize(&data_dir).expect("canonicalize source after reject"),
        source_path_before,
        "source PGDATA path must stay unchanged"
    );
    assert_eq!(
        String::from_utf8_lossy(&std::fs::read(postmaster_pid_path(&data_dir)).unwrap())
            .lines()
            .nth(1)
            .expect("pidfile line 2"),
        pgdata_in_pidfile,
        "source PGDATA path recorded in postmaster.pid must stay unchanged"
    );

    // 显式 stop 确认实例已停止。
    let (stop_code, stop_stderr) = stop_after_lock_release(&data_dir, LOCK_RELEASE_TIMEOUT);
    assert_eq!(
        stop_code, 0,
        "explicit stop should stop the orphan postgres; stderr:\n{stop_stderr}"
    );
    wait_until_dead(pg_pid);

    // 再次 restore：源目录无活实例 → 允许原子切换。
    let (restore_code, restore_stdout, restore_stderr) =
        restore_after_lock_release(&data_dir, &dump_path, LOCK_RELEASE_TIMEOUT);
    assert_eq!(
        restore_code, 0,
        "restore should succeed after explicit stop; stderr:\n{restore_stderr}"
    );
    let reported = restore_stdout.trim();
    assert_eq!(
        std::fs::canonicalize(reported).unwrap(),
        std::fs::canonicalize(&data_dir).unwrap(),
        "default restore should report the swapped-in data dir"
    );
    assert!(
        data_dir.join("PG_VERSION").exists(),
        "swapped-in data dir should be initialized"
    );
    let baks: Vec<_> = std::fs::read_dir(data_dir.parent().unwrap())
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_string_lossy().starts_with("data.bak-"))
        .collect();
    assert!(!baks.is_empty(), "original data dir should be kept as .bak");
    assert!(
        baks[0].path().join("PG_VERSION").exists(),
        "the .bak copy must preserve the original cluster"
    );
}

/// sidecar 与 postgres 均死亡（真双死、stale pid 残留）→ 无活实例，restore 仍可恢复。
#[test]
fn test_restore_recovers_when_sidecar_and_postgres_are_both_dead() {
    let (_tmp, data_dir) = unique_data_dir("rg_double_dead");
    let dump_path = data_dir.parent().unwrap().join("backup.dump");

    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");
    let (dump_code, dump_stderr) = dump_sidecar_with_stderr(&data_dir, &dump_path);
    assert_eq!(dump_code, 0, "dump should succeed; stderr:\n{dump_stderr}");

    // 真双死：先硬杀 sidecar，再硬杀 postgres（不经过 pg_ctl，postmaster.pid 残留）。
    let _ = child.kill();
    let _ = child.wait();
    kill_hard(pg_pid);
    wait_until_dead(pg_pid);
    assert!(
        postmaster_pid_path(&data_dir).exists(),
        "hard-killed postgres should leave a stale postmaster.pid"
    );

    let (code, _stdout, stderr) =
        restore_after_lock_release(&data_dir, &dump_path, LOCK_RELEASE_TIMEOUT);
    assert_eq!(
        code, 0,
        "stale pid with no live process must not block restore; stderr:\n{stderr}"
    );
    assert!(
        data_dir.join("PG_VERSION").exists(),
        "restored data dir should be initialized"
    );
    let baks: Vec<_> = std::fs::read_dir(data_dir.parent().unwrap())
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_string_lossy().starts_with("data.bak-"))
        .collect();
    assert!(
        !baks.is_empty(),
        "original damaged/dead dir should be kept as .bak"
    );
}

/// D16-F05：显式恢复到独立应用布局后，目标布局必须自包含（含 runtime 密码），
/// 不依赖源 runtime 即可启动并通过 SELECT 1；源密码文件保持不变。
#[test]
fn test_restore_explicit_target_layout_is_self_contained() {
    let (_tmp, data_dir) = unique_data_dir("rg_f05_self_contained");
    // 独立应用布局根：与源 data_dir 不同父目录 → runtime 独立（name 必须为 `data`）
    let target_root = tempfile::Builder::new()
        .prefix("qts-it-rg_f05_target-")
        .tempdir()
        .expect("create target tempdir");
    let target_data_dir = target_root.path().join("data");
    let dump_path = data_dir.parent().unwrap().join("backup.dump");

    // 源运行 → dump → 优雅停止（释放维护锁）
    let mut source_child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut source_child, READY_TIMEOUT);
    let (dump_code, dump_stderr) = dump_sidecar_with_stderr(&data_dir, &dump_path);
    assert_eq!(
        dump_code, 0,
        "dump should succeed while source is running; stderr:\n{dump_stderr}"
    );
    graceful_stop(&mut source_child);
    assert_eq!(
        wait_for_exit(&mut source_child, STOP_TIMEOUT),
        0,
        "source run should stop cleanly"
    );

    let source_password_path = data_dir.parent().unwrap().join("runtime").join("password");
    let source_password_bytes = std::fs::read(&source_password_path).expect("read source password");
    let source_password_plain =
        password::read_password_file(&source_password_path).expect("decode source password");

    // 显式恢复到独立布局：sidecar 必须为目标 runtime 写入同一密码
    let output = Command::new(sidecar_path())
        .arg("restore")
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--input")
        .arg(&dump_path)
        .arg("--target-data-dir")
        .arg(&target_data_dir)
        .output()
        .expect("run restore to explicit target");
    assert_eq!(
        output.status.code(),
        Some(0),
        "restore to independent layout should succeed; stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let restore_stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    assert_eq!(
        restore_stdout,
        target_data_dir.to_string_lossy().replace('\\', "/"),
        "restore stdout must report the explicit target data dir"
    );

    let target_password_path = target_root.path().join("runtime").join("password");
    assert!(
        target_password_path.exists(),
        "target runtime password file must be written: {}",
        target_password_path.display()
    );
    // R9：用 assert! 避免断言失败时把明文密码写入测试输出。
    assert!(
        password::read_password_file(&target_password_path).as_deref()
            == Some(source_password_plain.as_str()),
        "target runtime must carry the same password"
    );
    // D16-T03：显式异地恢复写入的 Unix 凭据必须为 0600；Windows DPAPI 实机未在本环境验证。
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&target_password_path)
            .expect("stat target password file")
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600, "target runtime password file must be 0600");
    }
    assert_eq!(
        std::fs::read(&source_password_path).expect("re-read source password"),
        source_password_bytes,
        "source password file must stay unchanged"
    );

    // 用目标布局启动（不传任何源密码/源目录参数）：ready 后 SELECT 1
    let mut target_child = spawn_run(&target_data_dir);
    let target_ready = wait_for_ready(&mut target_child, READY_TIMEOUT);
    assert_eq!(target_ready["status"], "running");
    let port = ready_port(&target_ready).expect("port in target ready JSON");

    let psql_name = if cfg!(windows) { "psql.exe" } else { "psql" };
    let psql = target_root
        .path()
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
            "select 1",
        ])
        .env("PGPASSWORD", &source_password_plain)
        .output()
        .expect("run bundled psql against restored target");
    assert!(
        out.status.success(),
        "restored target must accept login with target runtime password; stderr:\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&out.stdout).trim(),
        "1",
        "SELECT 1 must succeed on the restored target"
    );

    cleanup_sidecar(&mut target_child, &target_data_dir);
}
