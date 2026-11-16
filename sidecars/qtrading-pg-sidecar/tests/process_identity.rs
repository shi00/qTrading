//! D16-F02 集成回归：stop 前必须校验 PID 所属集群身份。
//!
//! 覆盖：
//! - pid 文件指向受控非 PG 辅助进程 → stop 非零，辅助进程仍存活，pid 文件保留；
//! - pid 文件指向另一受控集群 → stop 非零，两集群进程均存活；
//! - D07-001：A 的陈旧 pid 文件指向已被 B 复用的 PID（第 2 行仍是 A 的 PGDATA）→
//!   stop 非零，B 的 postgres 存活；
//! - 真本集群（sidecar 崩溃后残留 postgres）→ stop 成功分级停止；
//! - 真 stale（PID 已死）→ stop 幂等成功并清理 pid 文件；
//! - 检查与升级信号之间身份变化（pid 文件被重写）→ 拒绝且不发送信号。
//!
//! 串行执行（--test-threads=1），避免端口/锁/PG 缓存冲突。

mod common;

use common::*;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

fn postmaster_pid_path(data_dir: &Path) -> PathBuf {
    data_dir.join("postmaster.pid")
}

/// 写真实格式的 postmaster.pid（6 行）。
fn write_pidfile(data_dir: &Path, pid: u32, started_at: i64, port: u16) {
    std::fs::write(
        postmaster_pid_path(data_dir),
        format!(
            "{pid}\n{}\n{started_at}\n{port}\n/tmp\n127.0.0.1\n",
            data_dir.display()
        ),
    )
    .expect("write postmaster.pid");
}

#[cfg(unix)]
fn install_dir_for(data_dir: &Path) -> PathBuf {
    data_dir
        .parent()
        .expect("data_dir has parent")
        .join("install")
}

/// 运行 stop 并捕获 stderr，便于失败诊断。
fn run_stop(data_dir: &Path) -> (u8, String) {
    let output = Command::new(sidecar_path())
        .arg("stop")
        .arg("--data-dir")
        .arg(data_dir)
        .output()
        .expect("failed to run stop");
    (
        output.status.code().unwrap_or(255) as u8,
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

/// sidecar 崩溃后其维护锁随进程释放；短暂重试处理锁释放竞态。
fn stop_with_lock_retry(data_dir: &Path, timeout: Duration) -> (u8, String) {
    let start = Instant::now();
    loop {
        let (code, stderr) = run_stop(data_dir);
        if code != 50 || start.elapsed() >= timeout {
            return (code, stderr);
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

/// 受控非 PG 辅助进程（Unix sleep / Windows ping），用于模拟 PID 复用目标。
fn spawn_controlled_process() -> Child {
    #[cfg(windows)]
    {
        Command::new("ping")
            .args(["-n", "60", "127.0.0.1"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn controlled helper")
    }
    #[cfg(not(windows))]
    {
        Command::new("/bin/sleep")
            .arg("60")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn controlled helper")
    }
}

/// 以 `cwd = data_dir` 启动受控进程，模拟 postmaster 启动后 chdir 到 PGDATA 的真实行为。
#[cfg(unix)]
fn spawn_controlled_process_in(data_dir: &Path) -> Child {
    Command::new("/bin/sleep")
        .arg("60")
        .current_dir(data_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn controlled helper in data_dir")
}

/// 当前 epoch 秒：辅助进程刚创建，pid 文件第 3 行写该值即与 OS 创建时刻相容。
#[cfg(unix)]
fn now_epoch_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock after epoch")
        .as_secs() as i64
}

fn kill_helper(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

/// pid 文件指向受控非 PG 辅助进程：必须拒绝（exit 20），辅助进程存活，pid 文件保留。
#[test]
fn test_stop_refuses_foreign_helper_process() {
    let (_tmp, data_dir) = unique_data_dir("pid_foreign_helper");
    let mut helper = spawn_controlled_process();
    let helper_pid = helper.id();
    write_pidfile(&data_dir, helper_pid, 1_784_505_600, 55432);

    let (code, stderr) = run_stop(&data_dir);
    assert_eq!(
        code, 20,
        "stop must return STOP_FAILED when PID is not this cluster; stderr:\n{stderr}"
    );
    assert!(
        process_alive(helper_pid),
        "foreign helper must survive rejected stop (pid {helper_pid})"
    );
    assert!(
        postmaster_pid_path(&data_dir).exists(),
        "postmaster.pid must be preserved for diagnostics"
    );

    kill_helper(&mut helper);
}

/// pid 文件指向另一受控集群：A 的 stop 必须拒绝，且 B 的 postgres 与维护锁不受影响。
#[test]
fn test_stop_refuses_other_instance_pidfile() {
    let (_tmp_a, data_a) = unique_data_dir("pid_other_a");
    let (_tmp_b, data_b) = unique_data_dir("pid_other_b");

    let mut child_b = spawn_run(&data_b);
    let ready_b = wait_for_ready(&mut child_b, READY_TIMEOUT);
    let pg_pid_b = ready_pid(&ready_b).expect("pid in ready JSON");
    assert_eq!(ready_b["status"], "running");

    // 把 B 的真实 postmaster.pid 复制到 A：A 会读到 B 的 PID 与 PGDATA。
    let b_pidfile = std::fs::read_to_string(postmaster_pid_path(&data_b)).expect("read B pidfile");
    std::fs::write(postmaster_pid_path(&data_a), b_pidfile).expect("write A pidfile");

    let (code, stderr) = run_stop(&data_a);
    assert_eq!(
        code, 20,
        "stop on A must refuse to kill B's postgres; stderr:\n{stderr}"
    );
    assert!(
        process_alive(pg_pid_b),
        "other instance postgres must survive (pid {pg_pid_b})"
    );
    assert!(
        postmaster_pid_path(&data_a).exists(),
        "A's postmaster.pid must be preserved"
    );
    // B 仍在正常运行（其维护锁由 sidecar B 持有，A 的 stop 无权也不应影响它）。
    assert_eq!(status_json(&data_b)["status"], "running");

    cleanup_sidecar(&mut child_b, &data_b);
}

/// D07-001：A 的陈旧 pid 文件指向已被 B 复用的 PID。
///
/// 与「复制 B 整个 pid 文件」不同，这里第 2 行仍是 A 的 PGDATA、期望可执行文件也能解析到
/// B 的 postgres。旧实现只比较 pid 文件 PGDATA + 可执行路径就会放行，并把 B 的当前创建
/// 时刻当作原集群证据，最终向 B 发送停止信号。修复后必须以「pid 文件启动时刻与该 PID
/// 的 OS 创建时刻不相容」为由拒绝，且 B 的 postgres 不受影响。
#[cfg(unix)]
#[test]
fn test_stop_refuses_stale_pidfile_with_reused_pid() {
    let (_tmp_a, data_a) = unique_data_dir("pid_reused_a");
    let (_tmp_b, data_b) = unique_data_dir("pid_reused_b");

    let mut child_b = spawn_run(&data_b);
    let ready_b = wait_for_ready(&mut child_b, READY_TIMEOUT);
    let pg_pid_b = ready_pid(&ready_b).expect("pid in ready JSON");
    assert_eq!(ready_b["status"], "running");

    // 让 A 的期望可执行文件（A_parent/install/bin/postgres）解析到 B 的安装目录，
    // 从而把判定焦点隔离到「启动时刻关联 / 真实 PGDATA」这一新证据上。
    std::os::unix::fs::symlink(install_dir_for(&data_b), install_dir_for(&data_a))
        .expect("symlink install dir for A");

    // A 的旧 pid 文件：PID 已被 B 复用，但记录的是 A 的 PGDATA 与 A 的旧启动时刻。
    write_pidfile(&data_a, pg_pid_b, 1_234_567_890, 55432);

    let (code, stderr) = run_stop(&data_a);
    assert_eq!(
        code, 20,
        "stop on A must refuse a stale pidfile pointing at a reused PID; stderr:\n{stderr}"
    );
    assert!(
        process_alive(pg_pid_b),
        "B's postgres must survive rejected stop (pid {pg_pid_b})"
    );
    assert_eq!(status_json(&data_b)["status"], "running");
    assert!(
        postmaster_pid_path(&data_a).exists(),
        "A's stale postmaster.pid must be preserved for diagnostics"
    );
    assert!(
        postmaster_pid_path(&data_b).exists(),
        "B's postmaster.pid must stay intact"
    );

    cleanup_sidecar(&mut child_b, &data_b);
}

/// 真本集群：sidecar 崩溃后 postgres 残留；stop 应通过身份校验并完成分级停止。
#[test]
fn test_stop_stops_genuine_cluster_after_sidecar_crash() {
    let (_tmp, data_dir) = unique_data_dir("pid_genuine");
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");

    // 模拟 sidecar 崩溃：只杀 sidecar，postgres 仍在运行。
    let _ = child.kill();
    let _ = child.wait();

    let (code, stderr) = stop_with_lock_retry(&data_dir, Duration::from_secs(10));
    assert_eq!(
        code, 0,
        "stop must succeed for a genuine cluster owned by this PGDATA; stderr:\n{stderr}"
    );

    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline && process_alive(pg_pid) {
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(!process_alive(pg_pid), "postgres should be stopped");
    assert_eq!(
        status_json(&data_dir)["status"],
        "stopped",
        "status should report stopped"
    );
    assert!(
        !postmaster_pid_path(&data_dir).exists(),
        "postmaster.pid should be removed after clean stop"
    );
}

/// 真 stale：PID 已死 → 幂等成功（exit 0）并清理 pid 文件。
#[test]
fn test_stop_cleans_dead_pidfile() {
    let (_tmp, data_dir) = unique_data_dir("pid_stale");
    write_pidfile(&data_dir, 4_000_000, 1_234_567_890, 55432);

    let (code, stderr) = run_stop(&data_dir);
    assert_eq!(
        code, 0,
        "dead PID should be idempotent success; stderr:\n{stderr}"
    );
    assert!(
        !postmaster_pid_path(&data_dir).exists(),
        "stale postmaster.pid should be removed"
    );
}

/// 检查与信号升级之间身份变化：stop 先捕获 helper A，smart `pg_ctl` 期间 pid 文件被
/// 重写为 helper B → 升级信号前复核失败，拒绝发送信号，A/B 均存活。
///
/// 用可控假 pg_ctl（sleep 2 后失败）制造“检查通过 → 升级前”窗口，无需真实 PG。
#[cfg(unix)]
#[test]
fn test_stop_refuses_when_pidfile_changes_before_escalation() {
    use std::os::unix::fs::PermissionsExt;

    let (_tmp, data_dir) = unique_data_dir("pid_toctou");
    let install = install_dir_for(&data_dir);
    let bin = install.join("bin");
    std::fs::create_dir_all(&bin).expect("create install bin");

    // expected exe = install/bin/postgres：指向 /bin/sleep，使 helper 的身份校验可通过。
    std::os::unix::fs::symlink("/bin/sleep", bin.join("postgres")).expect("symlink postgres");
    // smart stop 成功前会等待 2s，随后失败并进入升级路径。
    let fake_pg_ctl = bin.join("pg_ctl");
    std::fs::write(&fake_pg_ctl, "#!/bin/sh\nsleep 2\nexit 1\n").expect("write fake pg_ctl");
    std::fs::set_permissions(&fake_pg_ctl, std::fs::Permissions::from_mode(0o755))
        .expect("chmod fake pg_ctl");

    let mut helper_a = spawn_controlled_process_in(&data_dir);
    let mut helper_b = spawn_controlled_process();
    let started_at = now_epoch_secs();
    write_pidfile(&data_dir, helper_a.id(), started_at, 55432);

    // 在 stop 命令执行 smart 阶段时重写 pid 文件，模拟 PID 复用/身份换代。
    let stop_child = Command::new(sidecar_path())
        .arg("stop")
        .arg("--data-dir")
        .arg(&data_dir)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn stop");

    std::thread::sleep(Duration::from_millis(600));
    write_pidfile(&data_dir, helper_b.id(), started_at, 55432);

    let output = stop_child.wait_with_output().expect("wait stop");
    let code = output.status.code().unwrap_or(255) as u8;
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(
        code, 20,
        "stop must refuse when identity changed before escalation; stderr:\n{stderr}"
    );
    assert!(
        process_alive(helper_a.id()),
        "original helper must not be signalled"
    );
    assert!(
        process_alive(helper_b.id()),
        "replacement helper must not be signalled"
    );
    assert!(
        postmaster_pid_path(&data_dir).exists(),
        "postmaster.pid must be preserved"
    );

    kill_helper(&mut helper_a);
    kill_helper(&mut helper_b);
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
