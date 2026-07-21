//! 用户数据目录布局解析（pg_plan §11）。
//!
//! ```text
//! <app data>/postgres/17/{data,install,runtime}
//! <app data>/postgres-logs/sidecar.log
//! <app data>/backups/
//! ```

use std::path::{Path, PathBuf};

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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derives_plan_layout_from_data_dir() {
        let layout = Layout::from_data_dir(
            Path::new("/home/u/.local/share/qTrading/postgres/17/data"),
            None,
            None,
            None,
        );
        assert_eq!(
            layout.install_dir,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/17/install")
        );
        assert_eq!(
            layout.state_file,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/17/runtime/state.json")
        );
        assert_eq!(
            layout.password_file,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/17/runtime/password")
        );
        assert_eq!(
            layout.lock_file,
            PathBuf::from("/home/u/.local/share/qTrading/postgres/17/runtime/lock")
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
            Path::new(r"C:\Users\u\AppData\Local\qTrading\postgres\17\data"),
            None,
            None,
            None,
        );
        assert_eq!(
            layout.install_dir,
            PathBuf::from(r"C:\Users\u\AppData\Local\qTrading\postgres\17\install")
        );
        assert_eq!(
            layout.sidecar_log,
            PathBuf::from(r"C:\Users\u\AppData\Local\qTrading\postgres-logs\sidecar.log")
        );
    }

    #[test]
    fn explicit_overrides_win() {
        let layout = Layout::from_data_dir(
            Path::new("/x/postgres/17/data"),
            Some(Path::new("/opt/pg")),
            Some(Path::new("/secret/pw")),
            Some(Path::new("/var/log/s.log")),
        );
        assert_eq!(layout.install_dir, PathBuf::from("/opt/pg"));
        assert_eq!(layout.password_file, PathBuf::from("/secret/pw"));
        assert_eq!(layout.sidecar_log, PathBuf::from("/var/log/s.log"));
    }
}
