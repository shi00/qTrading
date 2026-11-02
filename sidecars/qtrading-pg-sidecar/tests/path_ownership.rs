//! D16-F01 回归：install dir 与 PGDATA 重叠、或非空目录无所有权标记时，
//! sidecar 必须在任何清理前失败且不改动既有文件。
//!
//! 这些用例都在 `ensure_binaries` 的解压/下载之前退出，不依赖 PostgreSQL binaries，
//! 可在离线环境串行执行。

// 复用 common 中仅部分用例使用的生命周期辅助。
#![allow(dead_code)]

mod common;

use common::*;
use std::path::Path;
use std::process::Command;

fn run_with_install(data_dir: &Path, install_dir: Option<&Path>) -> (i32, String, String) {
    let mut cmd = Command::new(sidecar_path());
    cmd.arg("run").arg("--data-dir").arg(data_dir);
    if let Some(install_dir) = install_dir {
        cmd.arg("--install-dir").arg(install_dir);
    }
    let output = cmd.output().expect("failed to run sidecar");
    (
        output.status.code().unwrap_or(255),
        String::from_utf8_lossy(&output.stdout).into_owned(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

/// install dir = PGDATA → exit 2，既有 PGDATA 文件内容不变。
#[test]
fn install_dir_equal_to_data_dir_rejected() {
    let (_tmp, data_dir) = unique_data_dir("po_equal");
    std::fs::write(data_dir.join("user-data.txt"), b"keep me").unwrap();
    let before = std::fs::read(data_dir.join("user-data.txt")).unwrap();

    let (code, _stdout, stderr) = run_with_install(&data_dir, Some(&data_dir));

    assert_eq!(
        code, 2,
        "install==PGDATA 应以 ARGUMENT_ERROR 失败；stderr: {stderr}"
    );
    assert!(
        stderr.contains("路径配置冲突"),
        "stderr 应说明路径冲突，得到: {stderr}"
    );
    assert_eq!(
        std::fs::read(data_dir.join("user-data.txt")).unwrap(),
        before,
        "PGDATA 内容不得被改动"
    );
    assert!(!data_dir.join(".setup-complete").exists());
    assert!(!data_dir.join(".setup-owned").exists());
}

/// install dir 是 PGDATA 的祖先或后代 → exit 2，且不创建/删除任何文件。
#[test]
fn install_dir_ancestor_or_descendant_of_data_dir_rejected() {
    let (_tmp, data_dir) = unique_data_dir("po_nested");
    std::fs::write(data_dir.join("user-data.txt"), b"keep me").unwrap();
    let before = std::fs::read(data_dir.join("user-data.txt")).unwrap();
    let base = data_dir.parent().unwrap().to_path_buf();

    // install = data_dir 的祖先（清理会递归删除 PGDATA 所在子树）
    let (code, _stdout, stderr) = run_with_install(&data_dir, Some(&base));
    assert_eq!(code, 2, "install 为 PGDATA 祖先应 exit 2；stderr: {stderr}");
    assert_eq!(
        std::fs::read(data_dir.join("user-data.txt")).unwrap(),
        before
    );

    // install = data_dir 的后代
    let inside = data_dir.join("install");
    let (code, _stdout, stderr) = run_with_install(&data_dir, Some(&inside));
    assert_eq!(code, 2, "install 为 PGDATA 后代应 exit 2；stderr: {stderr}");
    assert_eq!(
        std::fs::read(data_dir.join("user-data.txt")).unwrap(),
        before
    );
    assert!(!inside.exists(), "被拒绝的 install dir 不得被创建");
}

/// 非空、无本程序所有权标记的 install dir → exit 10，全部文件保持原样。
#[test]
fn foreign_nonempty_install_dir_is_not_deleted() {
    let (_tmp, data_dir) = unique_data_dir("po_foreign");
    let install_dir = data_dir.parent().unwrap().join("install");
    std::fs::create_dir_all(install_dir.join("bin")).unwrap();
    std::fs::write(install_dir.join("keep.txt"), b"user data").unwrap();
    std::fs::write(install_dir.join("bin").join("initdb.exe"), b"partial").unwrap();
    let keep_before = std::fs::read(install_dir.join("keep.txt")).unwrap();
    let tool_before = std::fs::read(install_dir.join("bin").join("initdb.exe")).unwrap();

    // 使用默认 install 路径（与 PGDATA 同级，路径关系合法）
    let (code, _stdout, stderr) = run_with_install(&data_dir, None);

    assert_eq!(
        code, 10,
        "无所有权标记的非空 install dir 应 exit 10；stderr: {stderr}"
    );
    assert!(
        stderr.contains("所有权标记"),
        "stderr 应说明缺少所有权标记，得到: {stderr}"
    );
    assert_eq!(
        std::fs::read(install_dir.join("keep.txt")).unwrap(),
        keep_before,
        "非 owned 目录内容不得被删除"
    );
    assert_eq!(
        std::fs::read(install_dir.join("bin").join("initdb.exe")).unwrap(),
        tool_before,
        "非 owned 目录中的文件不得被改动"
    );
    assert!(!install_dir.join(".setup-complete").exists());
    assert!(!install_dir.join(".setup-owned").exists());
}

/// Unix：install dir 为指向 PGDATA 的符号链接 → exit 2，目标内容不变。
#[cfg(unix)]
#[test]
fn symlinked_install_dir_alias_rejected() {
    use std::os::unix::fs::symlink;

    let (_tmp, data_dir) = unique_data_dir("po_symlink");
    std::fs::write(data_dir.join("user-data.txt"), b"keep me").unwrap();
    let link = data_dir.parent().unwrap().join("install-link");
    symlink(&data_dir, &link).unwrap();

    let (code, _stdout, stderr) = run_with_install(&data_dir, Some(&link));

    assert_eq!(code, 2, "符号链接别名应以 exit 2 拒绝；stderr: {stderr}");
    assert!(
        data_dir.join("user-data.txt").exists(),
        "符号链接目标内容不得被删除"
    );
    assert!(
        std::fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink(),
        "链接本身不应被清理"
    );
}
