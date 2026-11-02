//! 用户数据目录布局解析（pg_plan §11）+ 安装目录重叠守卫（D16-F01）。
//!
//! ```text
//! <app data>/postgres/16/{data,install,runtime}
//! <app data>/postgres-logs/sidecar.log
//! <app data>/backups/
//! ```
//!
//! `Layout::validate` 在进入任何清理前规范化全部路径（绝对化、词法收敛、解析已存在祖先的
//! 重解析点），拒绝 install dir 与 PGDATA/runtime/password/log 相同或相互包含。规范化后
//! 比较可捕获符号链接、Windows junction 与大小写别名；无法规范化时 fail closed。

use std::fmt;
use std::path::{Path, PathBuf};

/// 安装目录与其他布局路径重叠（规范化后相同或相互包含）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathConflict {
    /// 路径无法安全规范化（符号链接循环、权限不足等）；fail closed。
    Unresolvable {
        label: &'static str,
        path: PathBuf,
        reason: String,
    },
    /// install dir 与受保护路径相同或相互包含（会被清理逻辑波及）。
    Overlap {
        install_dir: PathBuf,
        other_label: &'static str,
        other: PathBuf,
    },
}

impl fmt::Display for PathConflict {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unresolvable {
                label,
                path,
                reason,
            } => write!(
                f,
                "{label} 路径无法安全规范化：{}（{reason}）；\
                 请检查路径是否存在符号链接循环、悬空链接或权限问题",
                path.display()
            ),
            Self::Overlap {
                install_dir,
                other_label,
                other,
            } => write!(
                f,
                "install dir {} 与 {other_label} {} 相同或相互包含（规范化后）；\
                 清理安装目录会波及该路径，请改用独立 --install-dir",
                install_dir.display(),
                other.display()
            ),
        }
    }
}

/// 规范化路径用于比较：绝对化（词法收敛 `.`/`..`）→ 解析最深已存在祖先的
/// 符号链接/junction → 拼回不存在的尾部组件。
fn normalize_for_compare(label: &'static str, path: &Path) -> Result<PathBuf, PathConflict> {
    let absolute = std::path::absolute(path).map_err(|e| PathConflict::Unresolvable {
        label,
        path: path.to_path_buf(),
        reason: e.to_string(),
    })?;
    canonicalize_existing_prefix(&absolute).map_err(|e| PathConflict::Unresolvable {
        label,
        path: path.to_path_buf(),
        reason: e.to_string(),
    })
}

fn canonicalize_existing_prefix(path: &Path) -> std::io::Result<PathBuf> {
    let mut existing = path.to_path_buf();
    let mut missing: Vec<std::ffi::OsString> = Vec::new();
    loop {
        match std::fs::symlink_metadata(&existing) {
            Ok(_) => break,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let Some(name) = existing.file_name() else {
                    break;
                };
                missing.push(name.to_os_string());
                if !existing.pop() {
                    break;
                }
            }
            Err(e) => return Err(e),
        }
    }
    let mut resolved = std::fs::canonicalize(&existing)?;
    for name in missing.iter().rev() {
        resolved.push(name);
    }
    Ok(resolved)
}

/// 比较键：Windows/macOS 默认大小写不敏感，路径组件统一小写后比较，
/// 防止尚不存在的尾部组件以不同大小写指向同一位置。
fn comparison_components(path: &Path) -> Vec<String> {
    path.components()
        .map(|component| {
            let raw = component.as_os_str().to_string_lossy();
            if cfg!(any(windows, target_os = "macos")) {
                raw.to_lowercase()
            } else {
                raw.into_owned()
            }
        })
        .collect()
}

fn is_ancestor_or_equal(outer: &[String], inner: &[String]) -> bool {
    inner.starts_with(outer)
}

fn check_disjoint(
    install_raw: &Path,
    install_normalized: &Path,
    other_label: &'static str,
    other_raw: &Path,
) -> Result<(), PathConflict> {
    let other = normalize_for_compare(other_label, other_raw)?;
    let install_components = comparison_components(install_normalized);
    let other_components = comparison_components(&other);
    if is_ancestor_or_equal(&install_components, &other_components)
        || is_ancestor_or_equal(&other_components, &install_components)
    {
        return Err(PathConflict::Overlap {
            install_dir: install_raw.to_path_buf(),
            other_label,
            other: other_raw.to_path_buf(),
        });
    }
    Ok(())
}

#[derive(Debug, Clone)]
pub struct Layout {
    pub data_dir: PathBuf,
    pub install_dir: PathBuf,
    pub state_file: PathBuf,
    pub password_file: PathBuf,
    pub lock_file: PathBuf,
    pub sidecar_log: PathBuf,
}

impl Layout {
    /// 从 `--data-dir` 推导完整布局；显式参数优先于派生默认值。
    pub fn from_data_dir(
        data_dir: &Path,
        install_dir: Option<&Path>,
        password_file: Option<&Path>,
        log_file: Option<&Path>,
    ) -> Self {
        let base17 = data_dir
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| data_dir.to_path_buf());
        let root = base17
            .parent()
            .and_then(Path::parent)
            .map(Path::to_path_buf)
            .unwrap_or_else(|| base17.clone());
        let runtime_dir = base17.join("runtime");
        let logs_dir = root.join("postgres-logs");
        Self {
            data_dir: data_dir.to_path_buf(),
            install_dir: install_dir
                .map(Path::to_path_buf)
                .unwrap_or_else(|| base17.join("install")),
            state_file: runtime_dir.join("state.json"),
            password_file: password_file
                .map(Path::to_path_buf)
                .unwrap_or_else(|| runtime_dir.join("password")),
            lock_file: runtime_dir.join("lock"),
            sidecar_log: log_file
                .map(Path::to_path_buf)
                .unwrap_or_else(|| logs_dir.join("sidecar.log")),
        }
    }

    /// D16-F01：清理前校验 install dir 与 PGDATA/runtime/password/log 不重叠。
    ///
    /// 规范化后比较可识别符号链接 / Windows junction / 大小写别名；任一路径无法规范化
    /// （权限、循环、悬空链接）即 fail closed，绝不进入清理。
    pub fn validate(&self) -> Result<(), PathConflict> {
        let install = normalize_for_compare("install dir", &self.install_dir)?;
        let runtime_dir = self
            .state_file
            .parent()
            .unwrap_or(self.state_file.as_path())
            .to_path_buf();
        let protected: [(&'static str, &Path); 4] = [
            ("PGDATA（data dir）", self.data_dir.as_path()),
            ("runtime 目录", runtime_dir.as_path()),
            ("password 文件", self.password_file.as_path()),
            ("日志文件", self.sidecar_log.as_path()),
        ];
        for (label, path) in protected {
            check_disjoint(&self.install_dir, &install, label, path)?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derives_plan_layout_from_data_dir() {
        let layout = Layout::from_data_dir(
            Path::new("/home/u/.local/share/qTrading/postgres/16/data"),
            None,
            None,
            None,
        );
        assert_eq!(
            layout.install_dir,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/16/install")
        );
        assert_eq!(
            layout.state_file,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/16/runtime/state.json")
        );
        assert_eq!(
            layout.password_file,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/16/runtime/password")
        );
        assert_eq!(
            layout.lock_file,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/16/runtime/lock")
        );
        assert_eq!(
            layout.sidecar_log,
            PathBuf::from("/home/u/.local/share/qTrading/postgres-logs/sidecar.log")
        );
    }

    #[test]
    #[cfg(windows)]
    fn derives_windows_layout() {
        let layout = Layout::from_data_dir(
            Path::new(r"C:\Users\u\AppData\Local\qTrading\postgres\16\data"),
            None,
            None,
            None,
        );
        assert_eq!(
            layout.install_dir,
            PathBuf::from(r"C:\Users\u\AppData\Local\qTrading\postgres\16\install")
        );
        assert_eq!(
            layout.sidecar_log,
            PathBuf::from(r"C:\Users\u\AppData\Local\qTrading\postgres-logs\sidecar.log")
        );
    }

    fn temp_root(name: &str) -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("qts-paths-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn default_layout_validates_without_overlap() {
        let root = temp_root("default");
        let data_dir = root.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        let layout = Layout::from_data_dir(&data_dir, None, None, None);
        assert_eq!(layout.validate(), Ok(()));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn install_equal_to_data_dir_rejected() {
        let root = temp_root("equal-data");
        let data_dir = root.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        let layout = Layout::from_data_dir(&data_dir, Some(&data_dir), None, None);
        assert!(matches!(
            layout.validate(),
            Err(PathConflict::Overlap { .. })
        ));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn install_ancestor_or_descendant_of_data_dir_rejected() {
        let root = temp_root("data-nesting");
        let data_dir = root.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();

        // install = PGDATA 的祖先（清理会删除整个 data 子树）
        let ancestor = root.join("postgres/16");
        let layout = Layout::from_data_dir(&data_dir, Some(&ancestor), None, None);
        assert!(matches!(
            layout.validate(),
            Err(PathConflict::Overlap { .. })
        ));

        // install = PGDATA 的后代（清理发生在识别 PG_VERSION 之前）
        let descendant = data_dir.join("install");
        let layout = Layout::from_data_dir(&data_dir, Some(&descendant), None, None);
        assert!(matches!(
            layout.validate(),
            Err(PathConflict::Overlap { .. })
        ));

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn install_overlaps_runtime_password_or_log_rejected() {
        let root = temp_root("runtime-overlap");
        let data_dir = root.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();
        let runtime_dir = root.join("postgres/16/runtime");
        let password_file = runtime_dir.join("password");
        let log_file = root.join("postgres-logs/sidecar.log");

        for layout in [
            Layout::from_data_dir(&data_dir, Some(&runtime_dir), None, None),
            Layout::from_data_dir(&data_dir, Some(&password_file), Some(&password_file), None),
            Layout::from_data_dir(&data_dir, Some(&log_file), None, Some(&log_file)),
        ] {
            assert!(matches!(
                layout.validate(),
                Err(PathConflict::Overlap { .. })
            ));
        }

        let _ = std::fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn symlink_aliases_are_rejected() {
        use std::os::unix::fs::symlink;

        let root = temp_root("symlink");
        let data_dir = root.join("postgres/16/data");
        std::fs::create_dir_all(&data_dir).unwrap();

        // 符号链接指向 PGDATA：规范化后与 data_dir 相同
        let link = root.join("install-link");
        symlink(&data_dir, &link).unwrap();
        let layout = Layout::from_data_dir(&data_dir, Some(&link), None, None);
        assert!(matches!(
            layout.validate(),
            Err(PathConflict::Overlap { .. })
        ));

        // 悬空符号链接无法证明目标，fail closed
        let dangling = root.join("dangling-link");
        symlink(root.join("missing-target"), &dangling).unwrap();
        let layout = Layout::from_data_dir(&data_dir, Some(&dangling), None, None);
        assert!(matches!(
            layout.validate(),
            Err(PathConflict::Unresolvable { .. })
        ));

        let _ = std::fs::remove_dir_all(root);
    }

    #[cfg(windows)]
    #[test]
    fn case_aliases_are_rejected() {
        let root = temp_root("case");
        let data_dir = root.join("PGDATA");
        std::fs::create_dir_all(&data_dir).unwrap();
        // Windows 大小写不敏感：不同大小写指向同一目录
        let install = root.join("pgdata");
        let layout = Layout::from_data_dir(&data_dir, Some(&install), None, None);
        assert!(matches!(
            layout.validate(),
            Err(PathConflict::Overlap { .. })
        ));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn explicit_overrides_win() {
        let layout = Layout::from_data_dir(
            Path::new("/x/postgres/16/data"),
            Some(Path::new("/opt/pg")),
            Some(Path::new("/secret/pw")),
            Some(Path::new("/var/log/s.log")),
        );
        assert_eq!(layout.install_dir, PathBuf::from("/opt/pg"));
        assert_eq!(layout.password_file, PathBuf::from("/secret/pw"));
        assert_eq!(layout.sidecar_log, PathBuf::from("/var/log/s.log"));
    }
}
