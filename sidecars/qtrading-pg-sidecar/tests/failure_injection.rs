//! Rust sidecar 集成测试 — §17.6 失败注入场景。
//!
//! 12 场景 + 1 对称补充：#1/#3/#4/#6/#8/#9/#23/#24/#26/#27/#28/#28b/#31。
//! #28b 是 #28 的对称测试（dump .partial 残留 vs restore 目录残留）。
//! #1/#6 通过 env override 钩子注入（与 #27 FORCE_FS_KIND 同款语义）。
//! 串行执行（--test-threads=1），避免端口/锁/PG 缓存冲突。
//! 首个测试会下载+解压 PostgreSQL binaries（约 30MB），后续测试复用缓存。

mod common;

use common::*;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

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
/// D16-T01：不再手工创建残留目录，而是通过 `QTRADING_PG_SIDECAR_INJECT_PAUSE_AT` 同步点
/// 在恢复后健康检查完成、临时实例仍存活时强杀 sidecar，制造真实 kill-mid-restore。
/// 断言：源 PGDATA 未被改名/未创建 .bak、残留目录含完整 PGDATA、doctor 报告残留；
/// 残留目录里的孤儿 postgres 尚未停止，但源目录仍可独立启动（维护安全）。
#[test]
fn test_inject_28_restore_interruption_residual() {
    let (_tmp, data_dir) = unique_data_dir("fi_28");
    let base = data_dir.parent().unwrap().to_path_buf();

    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let dump_path = base.join("backup.dump");
    let (dump_code, dump_stderr) = dump_sidecar_with_stderr(&data_dir, &dump_path);
    assert_eq!(dump_code, 0, "dump failed: {dump_stderr}");
    graceful_stop(&mut child);
    assert_eq!(wait_for_exit(&mut child, STOP_TIMEOUT), 0);

    // 恢复完成健康检查后暂停：临时 postgres 仍在运行，强杀后即留下孤儿实例 + 残留目录。
    let marker = base.join("restore-health-check.marker");
    let mut restore = std::process::Command::new(sidecar_path())
        .arg("restore")
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--input")
        .arg(&dump_path)
        .env(
            "QTRADING_PG_SIDECAR_INJECT_PAUSE_AT",
            "restore_after_health_check",
        )
        .env("QTRADING_PG_SIDECAR_INJECT_PAUSE_MARKER", &marker)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn restore with pause injection");
    wait_for_pause_marker(
        &marker,
        Duration::from_secs(60),
        "restore_after_health_check",
    );

    let residual = sibling_dirs(&base, "data.restore-")
        .into_iter()
        .next()
        .expect("restore 中间目录应存在");
    assert!(residual.join("PG_VERSION").exists());
    let orphan_pid = postmaster_pid(&residual);
    assert!(
        orphan_pid.is_some(),
        "健康检查同步点临时 postgres 应仍在运行"
    );
    assert!(
        orphan_pid.is_some_and(process_alive),
        "postmaster.pid 记录的实际进程应存活（而非 stale pid）"
    );

    let _ = restore.kill();
    let exit = wait_for_exit(&mut restore, Duration::from_secs(30));
    assert_ne!(exit, 0, "被强杀的 restore 不应报告成功退出");

    // 源 PGDATA 未改动，且没有创建 .bak（尚未进入原子切换）。
    assert!(
        data_dir.join("PG_VERSION").exists(),
        "中断恢复不得改动源 PGDATA"
    );
    assert!(
        sibling_dirs(&base, "data.bak-").is_empty(),
        "中断恢复不得创建 .bak"
    );

    // doctor 应列出残留目录与 warning。
    let doc = doctor_json(&data_dir);
    let residuals = doc["restore_residuals"]
        .as_array()
        .expect("restore_residuals array");
    assert!(
        residuals
            .iter()
            .any(|r| r.as_str().unwrap_or_default().contains("data.restore-")),
        "doctor 应列出 restore 残留: {doc}"
    );
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues
            .iter()
            .any(|i| i["code"] == "restore_residual" && i["severity"] == "warning"),
        "doctor 应报告 restore_residual warning: {doc}"
    );

    // 清理孤儿 postgres（属于残留目录，不属于源 PGDATA）后再删除残留目录。
    let _guard = RemovePathOnDrop(residual.clone());
    if let Some(pid) = orphan_pid {
        force_kill_process(pid);
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(10) {
            if !process_alive(pid) {
                break;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        assert!(!process_alive(pid), "孤儿 postgres 应被清理");
    }

    // 源目录仍可维护启动（孤儿实例仅占用残留目录，不阻塞源 PGDATA）。
    let mut child2 = spawn_run(&data_dir);
    let ready2 = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(ready2["status"], "running");
    cleanup_sidecar(&mut child2, &data_dir);
}

/// #28b dump 中断残留 → doctor 列出唯一 `*.partial` 残留文件（§17.6 #28 对称 / §13.7.44 / §7.5）。
///
/// D16-T01：不再手工创建残留文件，而是在 pg_dump 完成、原子 rename 之前的同步点强杀
/// sidecar，制造真实 kill-mid-dump。断言：最终输出不存在、唯一临时文件保留且非空、
/// 输出级 `.lock` 已释放（重试同一路径成功）、doctor 报告 `dump_partial` warning。
#[test]
fn test_inject_28b_dump_partial_residual() {
    let (_tmp, data_dir) = unique_data_dir("fi_28b");
    let base = data_dir.parent().unwrap().to_path_buf();

    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);

    let output = base.join("backup.dump");
    let marker = base.join("dump-before-rename.marker");
    let mut dump = std::process::Command::new(sidecar_path())
        .arg("dump")
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--output")
        .arg(&output)
        .env("QTRADING_PG_SIDECAR_INJECT_PAUSE_AT", "dump_before_rename")
        .env("QTRADING_PG_SIDECAR_INJECT_PAUSE_MARKER", &marker)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn dump with pause injection");
    wait_for_pause_marker(&marker, Duration::from_secs(60), "dump_before_rename");

    // 中断点：最终文件尚未出现，唯一 .partial 已写出内容。
    assert!(
        !output.exists(),
        "中断时最终备份文件不得存在（否则半截备份会被当作可用）"
    );
    let partials = dump_partials(&base, "backup.dump.");
    assert_eq!(partials.len(), 1, "应存在且仅存在一个唯一 .partial");
    assert!(
        std::fs::metadata(&partials[0]).expect("stat partial").len() > 0,
        "中断前 pg_dump 应已写出内容"
    );
    // F06 输出级锁（不占 PGDATA 维护锁）：进程存活时存在，强杀后 OS 释放锁。
    let lock_path = PathBuf::from(format!("{}.lock", output.display()));
    assert!(lock_path.exists(), "F06 输出级 .lock 应已创建");

    let _ = dump.kill();
    let exit = wait_for_exit(&mut dump, Duration::from_secs(30));
    assert_ne!(exit, 0, "被强杀的 dump 不应报告成功退出");
    assert!(!output.exists(), "强杀后最终备份仍不得出现");

    let doc = doctor_json(&data_dir);
    let listed = doc["dump_partials"]
        .as_array()
        .expect("dump_partials array");
    assert!(
        listed
            .iter()
            .any(|p| p.as_str().unwrap_or_default().contains("backup.dump.")),
        "doctor 应列出 dump 残留: {doc}"
    );
    let issues = doc["issues"].as_array().expect("issues array");
    assert!(
        issues
            .iter()
            .any(|i| i["code"] == "dump_partial" && i["severity"] == "warning"),
        "doctor 应报告 dump_partial warning: {doc}"
    );

    // 输出锁随进程死亡释放：同一路径重试应成功（且使用新的唯一临时文件）。
    let _partial_guard = RemovePathOnDrop(partials[0].clone());
    let (retry_code, retry_stderr) = dump_sidecar_with_stderr(&data_dir, &output);
    assert_eq!(
        retry_code, 0,
        "重试 dump 应成功（输出锁已释放）: {retry_stderr}"
    );
    assert!(output.exists(), "重试后最终备份应存在");
    let _output_guard = RemovePathOnDrop(output.clone());

    cleanup_sidecar(&mut child, &data_dir);
}

/// #28c restore 在 initdb 阶段失败（复用现有 INJECT_INITDB_FAIL）→ exit 11，源目录不变。
///
/// 断言：准确退出码 11、源 PGDATA 完好、无 .bak、无 restore 残留；失败清理后源库仍可启动。
#[test]
fn test_inject_28c_restore_initdb_failure_keeps_source() {
    let (_tmp, data_dir) = unique_data_dir("fi_28c");
    let base = data_dir.parent().unwrap().to_path_buf();

    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    let dump_path = base.join("backup.dump");
    let (dump_code, dump_stderr) = dump_sidecar_with_stderr(&data_dir, &dump_path);
    assert_eq!(dump_code, 0, "dump failed: {dump_stderr}");
    graceful_stop(&mut child);
    assert_eq!(wait_for_exit(&mut child, STOP_TIMEOUT), 0);

    let restore = std::process::Command::new(sidecar_path())
        .arg("restore")
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--input")
        .arg(&dump_path)
        .env("QTRADING_PG_SIDECAR_INJECT_INITDB_FAIL", "1")
        .output()
        .expect("run restore with initdb failure injection");
    assert_eq!(
        restore.status.code(),
        Some(11),
        "initdb 失败应映射 exit 11; stderr: {}",
        String::from_utf8_lossy(&restore.stderr)
    );

    assert!(data_dir.join("PG_VERSION").exists(), "源 PGDATA 应保持不变");
    assert!(
        sibling_dirs(&base, "data.bak-").is_empty(),
        "失败不得创建 .bak"
    );
    assert!(
        sibling_dirs(&base, "data.restore-").is_empty(),
        "失败路径应清理 restore 中间目录"
    );

    let mut child2 = spawn_run(&data_dir);
    let ready2 = wait_for_ready(&mut child2, READY_TIMEOUT);
    assert_eq!(ready2["status"], "running");
    cleanup_sidecar(&mut child2, &data_dir);
}

/// #28d restore 在 pg_restore 阶段失败（损坏备份）→ exit 30，源目录不变且无残留。
///
/// 断言：准确退出码 30、源 PG_VERSION 完好、无 .bak、restore 中间目录已清理。
#[test]
fn test_inject_28d_restore_pg_restore_failure_keeps_source() {
    let (_tmp, data_dir) = unique_data_dir("fi_28d");
    let base = data_dir.parent().unwrap().to_path_buf();

    let mut child = spawn_run(&data_dir);
    let _ready = wait_for_ready(&mut child, READY_TIMEOUT);
    graceful_stop(&mut child);
    assert_eq!(wait_for_exit(&mut child, STOP_TIMEOUT), 0);

    let corrupt = base.join("corrupt.dump");
    std::fs::write(&corrupt, b"not a postgres custom-format dump").expect("write corrupt dump");

    let restore = std::process::Command::new(sidecar_path())
        .arg("restore")
        .arg("--data-dir")
        .arg(&data_dir)
        .arg("--input")
        .arg(&corrupt)
        .output()
        .expect("run restore with corrupt dump");
    assert_eq!(
        restore.status.code(),
        Some(30),
        "pg_restore 失败应映射 exit 30; stderr: {}",
        String::from_utf8_lossy(&restore.stderr)
    );

    assert!(data_dir.join("PG_VERSION").exists(), "源 PGDATA 应保持不变");
    assert!(
        sibling_dirs(&base, "data.bak-").is_empty(),
        "失败不得创建 .bak"
    );
    assert!(
        sibling_dirs(&base, "data.restore-").is_empty(),
        "失败路径应清理 restore 中间目录"
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

// ---- 辅助函数（与 roundtrip.rs 同款，YAGNI 不抽到 common） ----

/// 列出兄弟目录中名称以 `prefix` 开头的目录（.bak-* / data.restore-*）。
fn sibling_dirs(base: &Path, prefix: &str) -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(base)
        .unwrap_or_else(|e| panic!("read_dir {}: {e}", base.display()))
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_string_lossy().starts_with(prefix) && e.path().is_dir())
        .map(|e| e.path())
        .collect();
    dirs.sort();
    dirs
}

/// 列出兄弟文件中以 `prefix` 开头且以 `.partial` 结尾的 dump 临时文件。
fn dump_partials(base: &Path, prefix: &str) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(base)
        .unwrap_or_else(|e| panic!("read_dir {}: {e}", base.display()))
        .filter_map(|e| e.ok())
        .filter(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            name.starts_with(prefix) && name.ends_with(".partial") && e.path().is_file()
        })
        .map(|e| e.path())
        .collect();
    files.sort();
    files
}

/// 从 PGDATA/postmaster.pid 读取 postmaster PID（首行）。
fn postmaster_pid(data_dir: &Path) -> Option<u32> {
    std::fs::read_to_string(data_dir.join("postmaster.pid"))
        .ok()?
        .lines()
        .next()?
        .trim()
        .parse()
        .ok()
}

/// 等待注入同步点写出的 marker 文件；超时 panic 并指出阶段，避免静默挂起。
fn wait_for_pause_marker(path: &Path, timeout: Duration, phase: &str) {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(content) = std::fs::read_to_string(path) {
            assert_eq!(
                content.trim(),
                phase,
                "marker should record the injected phase"
            );
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    panic!(
        "injection marker not observed within {timeout:?}: {} (phase {phase})",
        path.display()
    );
}

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
