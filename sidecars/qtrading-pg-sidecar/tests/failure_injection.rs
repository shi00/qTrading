//! Rust sidecar 集成测试 — §17.6 失败注入场景。
//!
//! 12 场景 + 1 对称补充：#1/#3/#4/#6/#8/#9/#23/#24/#26/#27/#28/#28b/#31。
//! #28b 是 #28 的对称测试（dump .partial 残留 vs restore 目录残留）。
//! #1/#6 通过 env override 钩子注入（与 #27 FORCE_FS_KIND 同款语义）。
//! 串行执行（--test-threads=1），避免端口/锁/PG 缓存冲突。
//! 首个测试会下载+解压 PostgreSQL binaries（约 30MB），后续测试复用缓存。

mod common;

use common::*;
use std::path::Path;
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
    // Windows 进程树终止返回时内核可能仍在回收子进程句柄；立即重启会竞争
    // PGDATA 文件/监听句柄，导致新 postmaster 启动超时（CI 偶发 #3 失败）。
    #[cfg(windows)]
    std::thread::sleep(Duration::from_millis(1000));

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
    // 运行时外部 stop 命令会因维护锁返回 50（§13.3），必须走 stdin EOF 让 sidecar 自行
    // 优雅停止，否则强杀 sidecar 会遗留孤儿 postgres 进程占用 PGDATA/临时目录。
    graceful_stop(&mut child);
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    assert_eq!(exit, 0, "sidecar should stop gracefully before tampering");

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

/// D16-R03：无法识别的文件系统仍放行（避免误拒绝），但 doctor 必须给出可见 warning。
#[test]
fn test_unknown_filesystem_reported_by_doctor() {
    let (_tmp, data_dir) = unique_data_dir("fi_unknown_fs");

    let output = std::process::Command::new(sidecar_path())
        .arg("doctor")
        .arg("--data-dir")
        .arg(&data_dir)
        .env("QTRADING_PG_SIDECAR_FORCE_FS_KIND", "custom-unknown")
        .output()
        .expect("run doctor");
    let doc: serde_json::Value =
        serde_json::from_str(String::from_utf8_lossy(&output.stdout).trim()).expect("doctor json");

    assert_eq!(doc["fs_supported"], true, "Unknown 不应被拒绝: {doc}");
    assert_eq!(doc["fs_kind"], "unknown(custom-unknown)");
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues
            .iter()
            .any(|i| i["code"] == "fs_unverified" && i["severity"] == "warning"),
        "doctor should report fs_unverified warning: {doc}"
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
    std::fs::write(residual_dir.join("PG_VERSION"), b"16\n").unwrap();
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

    // 模拟 setup 中断残留：创建带所有权标记的半安装 install dir + tmp 目录
    // data_dir = <tmp>/data, base17 = <tmp>, install_dir = <tmp>/install
    // D16-F01：无所有权标记的非空目录现在会被拒绝清理，因此必须模拟本程序
    // 上次中断留下的 `.setup-owned` 标记（比旧测试更贴近真实崩溃现场）。
    let install_dir = data_dir.parent().unwrap().join("install");
    std::fs::create_dir_all(install_dir.join("bin")).unwrap();
    std::fs::write(install_dir.join(".setup-owned"), b"owned\n").unwrap();
    // 写入部分假 binary（无 .setup-complete marker，半截状态）
    std::fs::write(install_dir.join("bin").join("initdb.exe"), b"partial").ok();
    // 创建 tmp 残留目录（其他 pid 的解压残留，crate 解压到 <tmp>/<version>/）
    let tmp_residual = data_dir
        .parent()
        .unwrap()
        .join(format!("install.tmp-{}", 999999));
    let tmp_version = tmp_residual.join("16.15.0");
    std::fs::create_dir_all(&tmp_version).unwrap();
    std::fs::write(tmp_version.join(".setup-owned"), b"owned\n").unwrap();
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

/// #1 initdb failure (§17.6 #1)：env inject → exit 11 + stderr 含 "initdb failed"。
///
/// 通过 `QTRADING_PG_SIDECAR_INJECT_INITDB_FAIL=1` env var 在 `setup::run_initdb()` 入口
/// 短路返回 `INITDB_FAILED` (11)，避免实际破坏 PostgreSQL binaries。
/// 与 #27 `QTRADING_PG_SIDECAR_FORCE_FS_KIND` 同款 env override 模式（非 cfg(test)）。
#[test]
fn test_inject_01_initdb_failure() {
    let (_tmp, data_dir) = unique_data_dir("fi_01");

    let mut child = std::process::Command::new(sidecar_path())
        .arg("run")
        .arg("--data-dir")
        .arg(&data_dir)
        .env("QTRADING_PG_SIDECAR_INJECT_INITDB_FAIL", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sidecar");

    // initdb 在 setup::ensure_binaries 之后触发；首次需下载 binaries，给 READY_TIMEOUT
    let exit = wait_for_exit(&mut child, READY_TIMEOUT);
    assert_eq!(exit, 11, "sidecar should exit 11 when initdb fails");

    // 读 stderr 验证含 "initdb failed" 关键字（与 Python service.py _format_sidecar_exit_error 对齐）
    let stderr = read_stderr(&mut child);
    assert!(
        stderr.contains("initdb failed"),
        "stderr should contain 'initdb failed', got: {stderr:?}"
    );
}

/// #6 disk full (§17.6 #6)：env inject → exit 15 + stderr 含 "disk full"。
///
/// 通过 `QTRADING_PG_SIDECAR_INJECT_DISK_FULL=1` env var 在 `preflight::run()` 入口
/// 短路返回 `PreflightFailure::DiskSpace`，映射 exit 15。
/// 与 #27 `QTRADING_PG_SIDECAR_FORCE_FS_KIND` 同款 env override 模式（非 cfg(test)）。
#[test]
fn test_inject_06_disk_full() {
    let (_tmp, data_dir) = unique_data_dir("fi_06");

    let mut child = std::process::Command::new(sidecar_path())
        .arg("run")
        .arg("--data-dir")
        .arg(&data_dir)
        .env("QTRADING_PG_SIDECAR_INJECT_DISK_FULL", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn sidecar");

    // preflight 在 setup 之前触发，无需等待 binaries 下载；但保留 READY_TIMEOUT 余量
    let exit = wait_for_exit(&mut child, READY_TIMEOUT);
    assert_eq!(exit, 15, "sidecar should exit 15 when disk full");

    // 读 stderr 验证含 "disk full" 关键字（与 Python service.py _format_sidecar_exit_error 对齐）
    let stderr = read_stderr(&mut child);
    assert!(
        stderr.contains("disk full"),
        "stderr should contain 'disk full', got: {stderr:?}"
    );
}

/// #32 reset-password 非法超级用户名 → exit 2（ARGUMENT_ERROR），且不改动密码文件。
///
/// M23：用户名会进入 `ALTER USER <name> PASSWORD ...` 的 SQL 文本，白名单外的值必须在
/// 进入维护流程前被拒绝（R4 注入防护），且在获取维护锁前失败（不阻塞他人）。
#[test]
fn test_inject_32_reset_password_rejects_invalid_username() {
    let (_tmp, data_dir) = unique_data_dir("fi_32");

    // 建立最小"已初始化"骨架：确保失败原因是用户名非法而非目录问题
    std::fs::create_dir_all(&data_dir).unwrap();
    std::fs::write(data_dir.join("PG_VERSION"), "16\n").unwrap();

    let password_file = data_dir.parent().unwrap().join("runtime").join("password");
    assert!(
        !password_file.exists(),
        "precondition: no password file yet"
    );

    let output = std::process::Command::new(sidecar_path())
        .arg("reset-password")
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--username")
        .arg("bad;drop")
        .output()
        .expect("failed to run reset-password");

    assert_eq!(
        output.status.code(),
        Some(2),
        "invalid username should exit 2 (ARGUMENT_ERROR); stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("用户名非法"),
        "stderr should explain invalid username, got: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    // 未进入改密流程：不创建密码文件，也不改动已有凭据
    assert!(
        !password_file.exists(),
        "password file must not be created when username is rejected"
    );
}

/// 从 child.stderr 读取全部内容为 String。wait_for_exit 后调用（child 已退出，pipe EOF）。
fn read_stderr(child: &mut std::process::Child) -> String {
    use std::io::Read;
    let mut stderr = String::new();
    if let Some(mut child_stderr) = child.stderr.take() {
        let _ = child_stderr.read_to_string(&mut stderr);
    }
    stderr
}

// ---- D16-R01：临时实例停止失败必须硬失败（故障注入） ----

/// 停止阶段故障注入开关（smart / fast / kill，逗号分隔或 all）。
const INJECT_STOP_FAIL: &str = "QTRADING_PG_SIDECAR_INJECT_STOP_FAIL";

/// 启动 cluster 后 kill sidecar：维护锁由 OS 释放，postgres 保持运行，
/// 供 `stop` 命令走真实分层停止路径（不受 Drop 补偿影响）。
fn start_running_cluster_then_crash(
    test_name: &str,
) -> (tempfile::TempDir, std::path::PathBuf, u32) {
    let (tmp, data_dir) = unique_data_dir(test_name);
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");
    let pg_pid = ready_pid(&ready).expect("pid in ready JSON");
    let _ = child.kill();
    let _ = child.wait();
    std::thread::sleep(Duration::from_millis(300));
    (tmp, data_dir, pg_pid)
}

/// 启动 → 优雅停止，得到已初始化且无运行实例的数据目录。
fn init_cluster_and_stop(test_name: &str) -> (tempfile::TempDir, std::path::PathBuf) {
    let (tmp, data_dir) = unique_data_dir(test_name);
    let mut child = spawn_run(&data_dir);
    let ready = wait_for_ready(&mut child, READY_TIMEOUT);
    assert_eq!(ready["status"], "running");
    graceful_stop(&mut child);
    let exit = wait_for_exit(&mut child, STOP_TIMEOUT);
    assert_eq!(exit, 0, "graceful stop should exit 0");
    (tmp, data_dir)
}

/// 读取 postmaster.pid 的 PID（不存在/损坏 → None）。
fn pid_from_file(data_dir: &Path) -> Option<u32> {
    std::fs::read_to_string(data_dir.join("postmaster.pid"))
        .ok()?
        .lines()
        .next()?
        .trim()
        .parse()
        .ok()
}

/// 测试收尾：先走真实 stop，再对仍存活的残留 PID 强杀，避免临时目录/进程累积。
fn cleanup_maybe_running_pg(data_dir: &Path) {
    let _ = stop_sidecar(data_dir);
    if let Some(pid) = pid_from_file(data_dir) {
        if process_alive(pid) {
            force_kill_process(pid);
        }
    }
}

/// D16-R01 #1：仅 smart 阶段失败 → 升级 fast 成功，行为与正常路径一致（exit 0）。
#[test]
fn test_inject_r01_stop_smart_failure_escalates_to_fast() {
    let (_tmp, data_dir, pg_pid) = start_running_cluster_then_crash("fi_r01_smart");
    let data = data_dir.to_str().unwrap();
    let (exit, _stdout, stderr) = run_sidecar_env(
        &["stop", "--data-dir", data],
        &[(INJECT_STOP_FAIL, "smart")],
    );
    assert_eq!(exit, 0, "smart 注入失败应升级 fast 成功: {stderr}");
    assert!(!process_alive(pg_pid), "postgres should be stopped");
    let st = status_json(&data_dir);
    assert_eq!(st["last_stop_mode"], "fast", "state: {st}");
}

/// D16-R01 #2：smart+fast 失败 → kill fallback 成功，记录 kill_fallback。
#[test]
fn test_inject_r01_stop_fast_failure_falls_back_to_kill() {
    let (_tmp, data_dir, pg_pid) = start_running_cluster_then_crash("fi_r01_fast");
    let data = data_dir.to_str().unwrap();
    let (exit, _stdout, stderr) = run_sidecar_env(
        &["stop", "--data-dir", data],
        &[(INJECT_STOP_FAIL, "smart,fast")],
    );
    assert_eq!(exit, 0, "kill fallback 应停止 postgres: {stderr}");
    assert!(!process_alive(pg_pid), "postgres should be stopped");
    let st = status_json(&data_dir);
    assert_eq!(st["last_stop_mode"], "kill_fallback", "state: {st}");
    assert!(st["kill_fallback_count"].as_u64().unwrap_or(0) >= 1);
}

/// D16-R01 #3：smart+fast+kill 全部失败 → exit 20，真实 postgres 仍存活，
/// postmaster.pid 保留供诊断（不发送任何信号、不回退到 Drop 补偿）。
#[test]
fn test_inject_r01_stop_all_failures_exit_20_preserves_pidfile() {
    let (_tmp, data_dir, pg_pid) = start_running_cluster_then_crash("fi_r01_kill");
    let data = data_dir.to_str().unwrap();
    let (exit, _stdout, stderr) = run_sidecar_env(
        &["stop", "--data-dir", data],
        &[(INJECT_STOP_FAIL, "smart,fast,kill")],
    );
    assert_eq!(exit, 20, "stop 失败必须 exit 20: {stderr}");
    assert!(
        process_alive(pg_pid),
        "kill 阶段注入失败时 postgres 不应被杀死"
    );
    assert!(
        data_dir.join("postmaster.pid").exists(),
        "失败时应保留 postmaster.pid 供诊断"
    );
    assert!(
        stderr.contains("stop 失败"),
        "stderr 应报告停止失败，got: {stderr}"
    );

    cleanup_maybe_running_pg(&data_dir);
}

/// D16-R01 #4：dump 成功后临时实例停止失败 → exit 20，但已完成的备份必须保留。
#[test]
fn test_inject_r01_dump_stop_failure_exit_20_preserves_backup() {
    let (_tmp, data_dir) = init_cluster_and_stop("fi_r01_dump");
    let backup = data_dir.parent().unwrap().join("r01.dump");
    let data = data_dir.to_str().unwrap();
    let backup_s = backup.to_str().unwrap();
    let (exit, _stdout, stderr) = run_sidecar_env(
        &["dump", "--data-dir", data, "--output", backup_s],
        &[(INJECT_STOP_FAIL, "all")],
    );
    assert_eq!(exit, 20, "dump 停止失败必须 exit 20: {stderr}");
    assert!(backup.exists(), "已完成的备份必须保留: {backup:?}");
    assert!(
        !backup.with_extension("dump.partial").exists(),
        "备份应已原子改名，不残留 .partial"
    );
    assert!(
        stderr.contains("临时实例停止失败"),
        "stderr 应记录真实停止失败原因: {stderr}"
    );

    cleanup_maybe_running_pg(&data_dir);
}

/// D16-R01 #5：restore 完成后临时实例停止失败 → exit 20，禁止目录切换，
/// 保留 `.restore-*` 目录（唯一恢复成果不得删除）。
#[test]
fn test_inject_r01_restore_stop_failure_blocks_swap_and_preserves_dir() {
    let (_tmp, data_dir) = init_cluster_and_stop("fi_r01_restore");
    let backup = data_dir.parent().unwrap().join("r01-restore.dump");
    let code = dump_sidecar(&data_dir, &backup);
    assert_eq!(code, 0, "baseline dump should succeed");

    let data = data_dir.to_str().unwrap();
    let backup_s = backup.to_str().unwrap();
    let (exit, stdout, stderr) = run_sidecar_env(
        &["restore", "--data-dir", data, "--input", backup_s],
        &[(INJECT_STOP_FAIL, "all")],
    );
    assert_eq!(exit, 20, "restore 停止失败必须 exit 20: {stderr}");
    assert!(
        stdout.trim().is_empty(),
        "失败时不得向 stdout 报告恢复目标: {stdout:?}"
    );

    let name = data_dir.file_name().unwrap().to_str().unwrap().to_string();
    let parent = data_dir.parent().unwrap();
    let restore_dirs: Vec<_> = std::fs::read_dir(parent)
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with(&format!("{name}.restore-")))
        .collect();
    assert_eq!(
        restore_dirs.len(),
        1,
        "必须保留且仅保留一个 .restore-* 目录: {restore_dirs:?}"
    );
    let backup_dirs: Vec<_> = std::fs::read_dir(parent)
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with(&format!("{name}.bak-")))
        .collect();
    assert!(backup_dirs.is_empty(), "禁止切换目录: {backup_dirs:?}");
    assert!(
        data_dir.join("PG_VERSION").exists(),
        "源 PGDATA 必须保持不变"
    );

    // 清理：restore 目录内可能仍有未被 Drop 停掉的 postgres
    let restore_dir = parent.join(&restore_dirs[0]);
    cleanup_maybe_running_pg(&restore_dir);
    cleanup_maybe_running_pg(&data_dir);
}

/// D16-R01 #6：maintenance-shell 结束阶段停止失败 → exit 20，不打印「已停止」成功语义。
#[test]
fn test_inject_r01_maintenance_shell_stop_failure_exit_20() {
    let (_tmp, data_dir) = init_cluster_and_stop("fi_r01_maint");
    let data = data_dir.to_str().unwrap();
    let (exit, stdout, stderr) = run_sidecar_env(
        &["maintenance-shell", "--data-dir", data],
        &[(INJECT_STOP_FAIL, "all")],
    );
    assert_eq!(exit, 20, "maintenance-shell 停止失败必须 exit 20: {stderr}");
    assert!(stdout.contains("psql_path"), "连接信息应已输出: {stdout:?}");
    assert!(
        !stderr.contains("维护实例已停止"),
        "不得打印成功语义: {stderr}"
    );
    assert!(
        stderr.contains("不报告维护成功"),
        "stderr 应说明未报告成功: {stderr}"
    );

    cleanup_maybe_running_pg(&data_dir);
}

// ---- 辅助函数（与 roundtrip.rs 同款，YAGNI 不抽到 common） ----

/// RAII guard: 作用域退出时（含 panic）删除目标路径（目录或文件）。
/// 用于 #28/#28b 测试确保残留物被清理，避免 CI 临时目录累积（P1-1/P1-2）。
/// 先尝试 remove_dir_all（目录），失败再尝试 remove_file（文件），兼容两者。
struct RemovePathOnDrop(std::path::PathBuf);

impl Drop for RemovePathOnDrop {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0).or_else(|_| std::fs::remove_file(&self.0));
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

/// 跨平台强制 kill 进程（SIGKILL on Unix, 进程树 Terminate on Windows）。
///
/// Windows 上仅 TerminateProcess postmaster 会留下仍持有 PGDATA 文件/监听句柄的
/// postgres 子进程；紧接着重启（#3）时新 postmaster 可能启动超时。故优先
/// `taskkill /T /F` 终止整棵进程树，失败再回退到单进程 TerminateProcess。
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
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let tree_killed = std::process::Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .map(|status| status.success())
            .unwrap_or(false);
        if tree_killed {
            return;
        }

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
