//! Rust sidecar 集成测试 — §17.6 失败注入场景。
//!
//! 10 场景 + 1 对称补充：#3/#4/#8/#9/#23/#24/#26/#27/#28/#28b/#31。
//! #28b 是 #28 的对称测试（dump .partial 残留 vs restore 目录残留）。
//! 串行执行（--test-threads=1），避免端口/锁/PG 缓存冲突。
//! 首个测试会下载+解压 PostgreSQL binaries（约 30MB），后续测试复用缓存。

mod common;

use common::*;
use std::process::Stdio;
use std::time::Duration;

/// #3 sidecar 崩溃后重启：doctor 检测 stale pid → 再次 run 成功（§17.6 #3）。
#[test]
fn test_inject_03_sidecar_crash_restart() {
    let (_tmp, data_dir) = unique_data_dir("fi_03");
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");

    // 模拟 sidecar 崩溃：kill sidecar child（postgres 仍在运行）
    let _ = child.kill();
    let _ = child.wait();

    // 强制清理 postgres 进程（模拟 supervisor 消失后 postgres 也崩溃）
    force_kill_process(pg_pid);
    let start = std::time::Instant::now();
    while start.elapsed() < Duration::from_secs(10) {
        if !process_alive(pg_pid) {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }

    // doctor 应检测 stale postmaster.pid（postgres 已死，pid 残留）
    let doc = doctor_json(&data_dir);
    assert_eq!(
        doc["stale_postmaster_pid"], true,
        "doctor should detect stale pid: {doc}"
    );

    // 再次 run → 应清理 stale pid 后成功启动
    let mut child2 = spawn_run(&data_dir);
    let ready2 = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(ready2["status"], "running");

    cleanup_sidecar(&mut child2, &data_dir);
}

/// #4 PostgreSQL 崩溃后 sidecar 检测 postgres 退出 → exit 60（§17.6 #4）。
#[test]
fn test_inject_04_postgres_crash_exit_60() {
    let (_tmp, data_dir) = unique_data_dir("fi_04");
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");

    // kill postgres 进程
    force_kill_process(pg_pid);

    // sidecar 应检测 postgres 退出 → exit 60
    let exit = wait_for_exit(&mut child, Duration::from_secs(15));
    assert_eq!(exit, 60, "sidecar should exit 60 when postgres dies");

    // status → stopped/failed
    let st = status_json(&data_dir);
    let status = st["status"].as_str().unwrap_or("");
    assert!(
        status == "stopped" || status == "failed",
        "status should reflect stopped/failed state, got: {st}"
    );
}

/// #8 Windows 父进程崩溃（Job Object 等价模拟，§17.6 #8）。
/// Job Object 实际由 qTrading Python 父进程创建（§6.3），sidecar 代码无 Job Object 逻辑；
/// 此处以"父进程消失后 sidecar + postgres 退出"等价模拟。
#[cfg(windows)]
#[test]
fn test_inject_08_windows_parent_crash() {
    let (_tmp, data_dir) = unique_data_dir("fi_08");
    let mut helper = spawn_helper();
    let helper_pid = helper.id().to_string();
    let mut child = spawn_run_with(&data_dir, &["--parent-pid", &helper_pid]);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");

    // 显式 kill helper 模拟父进程崩溃
    let _ = helper.kill();
    let _ = helper.wait();

    // sidecar 检测父进程消失 → exit 0
    let exit = wait_for_exit(&mut child, Duration::from_secs(15));
    assert_eq!(exit, 0, "sidecar should exit 0 when parent crashes");

    // postgres 也应已停止
    let start = std::time::Instant::now();
    while start.elapsed() < Duration::from_secs(10) {
        if !process_alive(pg_pid) {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(
        !process_alive(pg_pid),
        "postgres should be gone after parent crash"
    );
}

/// #9 Unix 父进程崩溃（parent pid 轮询，§17.6 #9）。
#[cfg(unix)]
#[test]
fn test_inject_09_unix_parent_crash() {
    let (_tmp, data_dir) = unique_data_dir("fi_09");
    let mut helper = spawn_helper();
    let helper_pid = helper.id().to_string();
    let mut child = spawn_run_with(&data_dir, &["--parent-pid", &helper_pid]);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");

    // 显式 kill helper
    let _ = helper.kill();
    let _ = helper.wait();

    let exit = wait_for_exit(&mut child, Duration::from_secs(15));
    assert_eq!(exit, 0, "sidecar should exit 0 when parent crashes");

    let start = std::time::Instant::now();
    while start.elapsed() < Duration::from_secs(10) {
        if !process_alive(pg_pid) {
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(
        !process_alive(pg_pid),
        "postgres should be gone after parent crash"
    );
}

/// #23 pg_control 损坏 → run 返回 exit 40，不自动修复/不 initdb（§17.6 #23 / §13.7.36）。
#[test]
fn test_inject_23_pg_control_corruption() {
    let (_tmp, data_dir) = unique_data_dir("fi_23");
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    let _ = wait_for_exit(&mut child, STOP_TIMEOUT);

    // 篡改 pg_control
    let pg_control = data_dir.join("global").join("pg_control");
    assert!(pg_control.exists(), "pg_control should exist after initdb");
    let original = std::fs::read(&pg_control).unwrap();
    let mut tampered = original.clone();
    // 翻转全部字节制造损坏（pg_control 完全不可读）
    // 注：仅翻转前 16 字节在 Linux 上 pg_controldata 仍能读取（magic 容错），
    // 翻转全部字节确保 pg_controldata 解析失败 → 触发 pg_control_unreadable
    for byte in tampered.iter_mut() {
        *byte = !*byte;
    }
    std::fs::write(&pg_control, &tampered).unwrap();

    // 再次 run → exit 40
    let mut child2 = spawn_run(&data_dir);
    let exit = wait_for_exit(&mut child2, Duration::from_secs(30));
    assert_eq!(exit, 40, "sidecar should exit 40 when pg_control corrupted");

    // doctor 应报告 pg_control 损坏或启动失败
    // 跨平台兼容：Linux pg_controldata 对部分篡改容错，可能仅触发 last_start_error
    let doc = doctor_json(&data_dir);
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues.iter().any(|i| {
            let code = i["code"].as_str().unwrap_or("");
            code == "pg_control_unreadable"
                || code == "critical_files_missing"
                || code == "last_start_error"
        }),
        "doctor should report pg_control issue: {doc}"
    );

    // 恢复文件以便 TempDir cleanup
    let _ = std::fs::write(&pg_control, &original);
}

/// #24 PG_VERSION 缺失 → run 返回 exit 40，禁止对非空目录 initdb（§17.6 #24 / §13.7.37 / §7.2）。
#[test]
fn test_inject_24_pg_version_missing() {
    let (_tmp, data_dir) = unique_data_dir("fi_24");
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let _ = stop_sidecar(&data_dir);
    let _ = wait_for_exit(&mut child, STOP_TIMEOUT);

    // 删除 PG_VERSION 但保留 base/
    let pg_version = data_dir.join("PG_VERSION");
    assert!(pg_version.exists());
    let _ = std::fs::remove_file(&pg_version);

    // run → exit 40
    let mut child2 = spawn_run(&data_dir);
    let exit = wait_for_exit(&mut child2, Duration::from_secs(30));
    assert_eq!(exit, 40, "sidecar should exit 40 when PG_VERSION missing");

    // doctor 报告 nonempty_no_pg_version
    let doc = doctor_json(&data_dir);
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues.iter().any(|i| i["code"] == "nonempty_no_pg_version"),
        "doctor should report nonempty_no_pg_version: {doc}"
    );
}

/// #26 杀软/索引服务持锁 → preflight 拒绝（§17.6 #26 / §13.7.42）。
/// 仅 Unix 可靠模拟（chmod 0500 拒绝写）；Windows 无可靠等价模拟，跳过。
#[cfg(unix)]
#[test]
fn test_inject_26_antivirus_file_lock() {
    use std::os::unix::fs::PermissionsExt;
    let (_tmp, data_dir) = unique_data_dir("fi_26");

    // 创建 data_dir 并设为只读，模拟写失败（杀软锁等价）
    std::fs::create_dir_all(&data_dir).unwrap();
    std::fs::set_permissions(&data_dir, std::fs::Permissions::from_mode(0o555)).unwrap();

    let mut child = spawn_run(&data_dir);
    let exit = wait_for_exit(&mut child, Duration::from_secs(30));

    // 恢复权限以便 TempDir cleanup
    let _ = std::fs::set_permissions(&data_dir, std::fs::Permissions::from_mode(0o755));

    assert_eq!(
        exit, 15,
        "preflight should reject read-only data_dir with exit 15"
    );
}

/// #27 FAT32 数据目录 → preflight 拒绝（§17.6 #27 / §13.7.43）。
/// 通过 QTRADING_PG_SIDECAR_FORCE_FS_KIND=fat32 env var 覆盖检测。
#[test]
fn test_inject_27_fat32_filesystem_rejected() {
    let (_tmp, data_dir) = unique_data_dir("fi_27");

    // 通过 env var 覆盖 fs_kind 检测为 fat32
    let mut child = std::process::Command::new(sidecar_path())
        .arg("run")
        .arg("--data-dir")
        .arg(&data_dir)
        .env("QTRADING_PG_SIDECAR_FORCE_FS_KIND", "fat32")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sidecar");

    let exit = wait_for_exit(&mut child, Duration::from_secs(30));
    assert_eq!(exit, 15, "preflight should reject FAT32 with exit 15");

    // doctor 也会报告 fs_unsupported
    let doc: serde_json::Value = {
        let output = std::process::Command::new(sidecar_path())
            .arg("doctor")
            .arg("--data-dir")
            .arg(&data_dir)
            .env("QTRADING_PG_SIDECAR_FORCE_FS_KIND", "fat32")
            .output()
            .expect("run doctor");
        let stdout = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str(stdout.trim()).expect("doctor json")
    };
    assert_eq!(doc["fs_kind"], "FAT32/vFAT");
    assert_eq!(doc["fs_supported"], false);
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues.iter().any(|i| i["code"] == "fs_unsupported"),
        "doctor should report fs_unsupported: {doc}"
    );
}

/// #28 restore 中断残留 → doctor 列出 `data.restore-*` 残留目录（§17.6 #28 / §13.7.44 / §7.5）。
///
/// 模拟方式：手动创建 `data.restore-<ts>` 兄弟目录（与 sidecar `restore()` 失败路径生成的
/// 残留目录同构）。真实 kill-mid-restore 测试因 pg_restore 时序不可控而不可靠，故用
/// 手动创建残留目录的方式验证 doctor 扫描逻辑（与 #31 手动创建 tmp 残留同款策略）。
///
/// 清理策略：用 RAII guard（RemoveDirOnDrop）确保残留目录在 panic 时也被清理，
/// 避免测试失败导致 CI 临时目录累积残留（P1-1）。
#[test]
fn test_inject_28_restore_interruption_residual() {
    let (_tmp, data_dir) = unique_data_dir("fi_28");
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    let _ = wait_for_exit(&mut child, STOP_TIMEOUT);

    // 模拟 restore 中断残留：创建 data.restore-<ts> 兄弟目录
    let residual_dir = data_dir
        .parent()
        .unwrap()
        .join("data.restore-20260723T120000Z");
    std::fs::create_dir_all(&residual_dir).unwrap();
    // 写入半截状态文件模拟中断
    std::fs::write(residual_dir.join("PG_VERSION"), b"17\n").unwrap();
    // RAII guard: panic 安全清理残留目录（P1-1）
    let _guard = RemovePathOnDrop(residual_dir.clone());

    // doctor 应列出残留目录
    let doc = doctor_json(&data_dir);
    let residuals = doc["restore_residuals"]
        .as_array()
        .expect("restore_residuals array");
    assert!(
        !residuals.is_empty(),
        "doctor should list restore residual: {doc}"
    );
    assert!(
        residuals.iter().any(|r| r
            .as_str()
            .unwrap_or_default()
            .contains("data.restore-20260723T120000Z")),
        "residual path should match: {residuals:?}"
    );
    // issues 应含 restore_residual 警告
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues
            .iter()
            .any(|i| i["code"] == "restore_residual" && i["severity"] == "warning"),
        "doctor should emit restore_residual warning issue: {doc}"
    );
}

/// #28b dump 中断残留 → doctor 列出 `*.partial` 残留文件（§17.6 #28 对称 / §13.7.44 / §7.5）。
///
/// 与 #28 对称：#28 验证 `data.restore-*` 目录残留扫描，本测试验证 `*.partial` 文件残留扫描。
/// 模拟方式：手动创建 `backup.dump.partial` 兄弟文件（与 sidecar `dump()` 失败路径生成的
/// 半截备份文件同构）。真实 kill-mid-dump 测试因 pg_dump 时序不可控而不可靠，故用
/// 手动创建残留文件的方式验证 doctor 扫描逻辑。
#[test]
fn test_inject_28b_dump_partial_residual() {
    let (_tmp, data_dir) = unique_data_dir("fi_28b");
    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    let _ = wait_for_exit(&mut child, STOP_TIMEOUT);

    // 模拟 dump 中断残留：创建 backup.dump.partial 兄弟文件
    let partial_file = data_dir
        .parent()
        .unwrap()
        .join("backup-20260723T130000Z.dump.partial");
    std::fs::write(&partial_file, b"partial dump content").unwrap();
    // RAII guard: panic 安全清理残留文件（P1-2）
    let _guard = RemovePathOnDrop(partial_file.clone());

    // doctor 应列出残留文件
    let doc = doctor_json(&data_dir);
    let partials = doc["dump_partials"]
        .as_array()
        .expect("dump_partials array");
    assert!(
        !partials.is_empty(),
        "doctor should list dump partial: {doc}"
    );
    assert!(
        partials.iter().any(|p| p
            .as_str()
            .unwrap_or_default()
            .contains("backup-20260723T130000Z.dump.partial")),
        "partial path should match: {partials:?}"
    );
    // issues 应含 dump_partial 警告
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues
            .iter()
            .any(|i| i["code"] == "dump_partial" && i["severity"] == "warning"),
        "doctor should emit dump_partial warning issue: {doc}"
    );
}

/// #31 setup 解压中断残留 → 下次 run 清理 tmp 并重做 setup（§17.6 #31 / §16.2 AI-39）。
#[test]
fn test_inject_31_setup_extraction_interruption() {
    let (_tmp, data_dir) = unique_data_dir("fi_31");

    // 模拟 setup 中断残留：创建无 marker 的 install dir + tmp 目录
    // data_dir = <tmp>/data, base17 = <tmp>, install_dir = <tmp>/install
    let install_dir = data_dir.parent().unwrap().join("install");
    std::fs::create_dir_all(install_dir.join("bin")).unwrap();
    // 写入部分假 binary（无 .setup-complete marker，半截状态）
    std::fs::write(install_dir.join("bin").join("initdb.exe"), b"partial").ok();
    // 创建 tmp 残留目录（其他 pid 的解压残留）
    let tmp_residual = data_dir
        .parent()
        .unwrap()
        .join(format!("install.tmp-{}", 999999));
    std::fs::create_dir_all(&tmp_residual).unwrap();
    std::fs::write(tmp_residual.join("junk"), b"x").unwrap();

    // run → 应清理 tmp 残留 + 删除半截 install dir + 重做 setup → ready
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");

    // 验证 tmp 残留已清理
    assert!(!tmp_residual.exists(), "tmp residual should be cleaned");

    // 验证 .setup-complete marker 已写入
    assert!(
        install_dir.join(".setup-complete").exists(),
        "marker should exist after redo"
    );

    cleanup_sidecar(&mut child, &data_dir);
}

// ---- 辅助函数（与 roundtrip.rs 同款，YAGNI 不抽到 common） ----

/// RAII guard: 作用域退出时（含 panic）删除目标路径（目录或文件）。
/// 用于 #28/#28b 测试确保残留物被清理，避免 CI 临时目录累积（P1-1/P1-2）。
/// 先尝试 remove_dir_all（目录），失败再尝试 remove_file（文件），兼容两者。
struct RemovePathOnDrop(std::path::PathBuf);

impl Drop for RemovePathOnDrop {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0)
            .or_else(|_| std::fs::remove_file(&self.0));
    }
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

/// 跨平台强制 kill 进程（SIGKILL on Unix, TerminateProcess on Windows）。
fn force_kill_process(pid: u32) {
    #[cfg(unix)]
    {
        // SAFETY: SIGKILL 兜底；目标不存在时错误忽略。
        unsafe {
            libc::kill(pid as libc::pid_t, libc::SIGKILL);
        }
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{
            OpenProcess, TerminateProcess, PROCESS_TERMINATE,
        };
        // SAFETY: 句柄非空才 TerminateProcess，之后总是 CloseHandle。
        let handle = unsafe { OpenProcess(PROCESS_TERMINATE, 0, pid) };
        if !handle.is_null() {
            unsafe {
                let _ = TerminateProcess(handle, 1);
                let _ = CloseHandle(handle);
            }
        }
    }
}
