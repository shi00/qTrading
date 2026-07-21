//! preflight 资源预检（pg_plan §7.2 / §13.7.5/39/40/42/43，全部命中返回 exit 15）。
//!
//! 检查项：磁盘空间 ≥500MB、目录可写性（EACCES 指数退避 3 次）、文件系统类型、
//! 网盘同步路径特征、password file 权限（Unix 0600）。

use crate::password;
use crate::paths::Layout;
use std::path::{Path, PathBuf};
use thiserror::Error;

pub const MIN_FREE_BYTES: u64 = 500 * 1024 * 1024;
pub const RUNTIME_WARN_FREE_BYTES: u64 = 100 * 1024 * 1024;

#[derive(Error, Debug)]
pub enum PreflightFailure {
    #[error("磁盘空间不足：{path} 剩余 {free_mb}MB < 500MB，请清理后重试")]
    DiskSpace { path: PathBuf, free_mb: u64 },
    #[error(
        "目录不可写：{path}（已重试 3 次；若被杀软/索引服务锁定，请将 postgres/17/ 加入排除列表）"
    )]
    NotWritable { path: PathBuf },
    #[error("数据库目录必须位于本地日志式文件系统（Windows NTFS/ReFS、macOS APFS/HFS+、Linux ext4/xfs/btrfs），当前：{kind}")]
    UnsupportedFs { kind: String },
    #[error("PGDATA 不得位于网盘同步目录（{feature}）：同步客户端并发写会损坏 WAL，请改用本地非同步路径")]
    CloudSyncPath { feature: String },
    #[error("password file 权限过宽（{path}）：Unix 要求 0600，请 chmod 600 后重试")]
    PasswordFilePerms { path: PathBuf },
}

impl PreflightFailure {
    pub fn check_name(&self) -> &'static str {
        match self {
            Self::DiskSpace { .. } => "disk_space",
            Self::NotWritable { .. } => "dir_writable",
            Self::UnsupportedFs { .. } => "filesystem_type",
            Self::CloudSyncPath { .. } => "cloud_sync_path",
            Self::PasswordFilePerms { .. } => "password_file_perms",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FsKind {
    Ntfs,
    ReFs,
    Apfs,
    HfsPlus,
    ExtFamily,
    Xfs,
    Btrfs,
    Zfs,
    F2fs,
    Overlayfs,
    Tmpfs,
    Fat32,
    ExFat,
    NetworkFs,
    Unknown(String),
}

impl FsKind {
    pub fn describe(&self) -> String {
        match self {
            Self::Ntfs => "NTFS".into(),
            Self::ReFs => "ReFS".into(),
            Self::Apfs => "APFS".into(),
            Self::HfsPlus => "HFS+".into(),
            Self::ExtFamily => "ext2/3/4".into(),
            Self::Xfs => "xfs".into(),
            Self::Btrfs => "btrfs".into(),
            Self::Zfs => "zfs".into(),
            Self::F2fs => "f2fs".into(),
            Self::Overlayfs => "overlayfs".into(),
            Self::Tmpfs => "tmpfs".into(),
            Self::Fat32 => "FAT32/vFAT".into(),
            Self::ExFat => "exFAT".into(),
            Self::NetworkFs => "network fs (SMB/NFS/WebDAV)".into(),
            Self::Unknown(s) => format!("unknown({s})"),
        }
    }

    /// §13.7.43：FAT32/exFAT/网络盘无可靠 fsync 语义，拒绝；未知类型放行但告警（doctor 报告）。
    pub fn is_supported(&self) -> bool {
        !matches!(self, Self::Fat32 | Self::ExFat | Self::NetworkFs)
    }

    /// 失败注入 #27 测试钩子：`QTRADING_PG_SIDECAR_FORCE_FS_KIND=fat32` 强制判定。
    pub fn from_env_override() -> Option<Self> {
        let v = std::env::var("QTRADING_PG_SIDECAR_FORCE_FS_KIND").ok()?;
        Some(match v.to_ascii_lowercase().as_str() {
            "ntfs" => Self::Ntfs,
            "refs" => Self::ReFs,
            "apfs" => Self::Apfs,
            "hfs+" | "hfsplus" => Self::HfsPlus,
            "ext4" | "ext3" | "ext2" => Self::ExtFamily,
            "xfs" => Self::Xfs,
            "btrfs" => Self::Btrfs,
            "zfs" => Self::Zfs,
            "f2fs" => Self::F2fs,
            "overlayfs" | "overlay" => Self::Overlayfs,
            "tmpfs" => Self::Tmpfs,
            "fat32" | "fat" | "vfat" => Self::Fat32,
            "exfat" => Self::ExFat,
            "nfs" | "smb" | "cifs" | "webdav" => Self::NetworkFs,
            other => Self::Unknown(other.to_string()),
        })
    }
}

const CLOUD_SYNC_FEATURES: &[&str] = &[
    "onedrive",
    "dropbox",
    "iclouddrive",
    "baidunetdisk",
    "baidu网盘",
    "百度网盘",
    "googledrive",
    "nutstore",
    "坚果云",
    "pcloud",
    "sync.com",
    "box sync",
];

/// §13.7.39：路径含网盘同步目录特征即拒绝（大小写不敏感、忽略空格）。
pub fn detect_cloud_sync(path: &Path) -> Option<String> {
    let normalized: String = path
        .to_string_lossy()
        .to_lowercase()
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect();
    CLOUD_SYNC_FEATURES
        .iter()
        .map(|f| f.replace(' ', ""))
        .find(|f| normalized.contains(f.as_str()))
}

fn nearest_existing_ancestor(path: &Path) -> PathBuf {
    let mut current = path;
    loop {
        if current.exists() {
            return current.to_path_buf();
        }
        match current.parent() {
            Some(p) => current = p,
            None => return current.to_path_buf(),
        }
    }
}

pub fn free_space(path: &Path) -> Option<u64> {
    fs2::free_space(nearest_existing_ancestor(path)).ok()
}

fn ensure_writable(path: &Path) -> Result<(), PreflightFailure> {
    std::fs::create_dir_all(path).map_err(|_| PreflightFailure::NotWritable {
        path: path.to_path_buf(),
    })?;
    let probe = path.join(format!(".qtrading-write-probe-{}", std::process::id()));
    // §13.7.42：杀软/索引服务瞬时持锁，指数退避 1s/2s/4s 重试 3 次
    let mut delay = std::time::Duration::from_secs(1);
    for attempt in 0..3 {
        let result = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)
            .and_then(|f| f.sync_all());
        match result {
            Ok(_) => {
                let _ = std::fs::remove_file(&probe);
                return Ok(());
            }
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied && attempt < 2 => {
                std::thread::sleep(delay);
                delay *= 2;
            }
            Err(_) => {
                return Err(PreflightFailure::NotWritable {
                    path: path.to_path_buf(),
                })
            }
        }
    }
    Err(PreflightFailure::NotWritable {
        path: path.to_path_buf(),
    })
}

/// 完整 preflight；任一失败即返回首个错误（调用方映射 exit 15 并写 stderr）。
pub fn run(layout: &Layout) -> Result<(), PreflightFailure> {
    // 1. 目录可写性（顺带创建 data_dir，initdb 允许空目录）
    ensure_writable(&layout.data_dir)?;

    // 2. 磁盘空间（data 与 install 所在卷）
    for path in [&layout.data_dir, &layout.install_dir] {
        if let Some(free) = free_space(path) {
            if free < MIN_FREE_BYTES {
                return Err(PreflightFailure::DiskSpace {
                    path: path.clone(),
                    free_mb: free / (1024 * 1024),
                });
            }
        }
    }

    // 3. 网盘同步路径（用规范化绝对路径判定）
    let abs = layout
        .data_dir
        .canonicalize()
        .unwrap_or_else(|_| layout.data_dir.clone());
    if let Some(feature) = detect_cloud_sync(&abs) {
        return Err(PreflightFailure::CloudSyncPath { feature });
    }

    // 4. 文件系统类型
    let fs_kind = FsKind::from_env_override().unwrap_or_else(|| detect_fs_kind(&layout.data_dir));
    if !fs_kind.is_supported() {
        return Err(PreflightFailure::UnsupportedFs {
            kind: fs_kind.describe(),
        });
    }
    if let FsKind::Unknown(raw) = &fs_kind {
        tracing::warn!(
            "unrecognized filesystem type {raw} on {}; proceeding with caution",
            layout.data_dir.display()
        );
    }

    // 5. password file 权限（仅已存在时校验；首次生成由 password 模块保证 0600）
    if layout.password_file.exists() && !password::password_file_perms_ok(&layout.password_file) {
        return Err(PreflightFailure::PasswordFilePerms {
            path: layout.password_file.clone(),
        });
    }

    Ok(())
}

// ---- 平台文件系统类型探测 ----

#[cfg(windows)]
pub fn detect_fs_kind(path: &Path) -> FsKind {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetVolumeInformationW;

    // 取卷根（如 C:\）
    let root: PathBuf = match path.components().next() {
        Some(std::path::Component::Prefix(prefix)) => {
            let s = prefix.as_os_str().to_string_lossy().to_string();
            PathBuf::from(format!("{s}\\"))
        }
        _ => return FsKind::Unknown("no-volume-root".into()),
    };
    let root_wide: Vec<u16> = root
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let mut fs_name = vec![0u16; 64];
    let ok = unsafe {
        GetVolumeInformationW(
            root_wide.as_ptr(),
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            fs_name.as_mut_ptr(),
            fs_name.len() as u32,
        )
    };
    if ok == 0 {
        return FsKind::Unknown(format!(
            "query-failed-{}",
            std::io::Error::last_os_error().raw_os_error().unwrap_or(0)
        ));
    }
    let len = fs_name
        .iter()
        .position(|c| *c == 0)
        .unwrap_or(fs_name.len());
    let name = String::from_utf16_lossy(&fs_name[..len]).to_ascii_uppercase();
    match name.as_str() {
        "NTFS" => FsKind::Ntfs,
        "REFS" => FsKind::ReFs,
        "FAT32" | "FAT" | "VFAT" => FsKind::Fat32,
        "EXFAT" => FsKind::ExFat,
        "CDFS" | "UDF" => FsKind::Unknown(name),
        other if other.contains("SMB") || other.contains("NFS") || other.contains("CSC") => {
            FsKind::NetworkFs
        }
        other => FsKind::Unknown(other.to_string()),
    }
}

#[cfg(target_os = "macos")]
pub fn detect_fs_kind(path: &Path) -> FsKind {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let c_path = match CString::new(path.as_os_str().as_bytes()) {
        Ok(p) => p,
        Err(_) => return FsKind::Unknown("bad-path".into()),
    };
    let mut stat: libc::statfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statfs(c_path.as_ptr(), &mut stat) } != 0 {
        return FsKind::Unknown("statfs-failed".into());
    }
    let name = unsafe { std::ffi::CStr::from_ptr(stat.f_fstypename.as_ptr()) }
        .to_string_lossy()
        .to_ascii_lowercase();
    match name.as_str() {
        "apfs" => FsKind::Apfs,
        "hfs" | "hfs+" => FsKind::HfsPlus,
        "exfat" => FsKind::ExFat,
        "msdos" | "fat32" => FsKind::Fat32,
        "smbfs" | "nfs" | "webdav" | "osxfuse" => FsKind::NetworkFs,
        "tmpfs" => FsKind::Tmpfs,
        other => FsKind::Unknown(other.to_string()),
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
pub fn detect_fs_kind(path: &Path) -> FsKind {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    const EXT_MAGIC: i64 = 0xEF53;
    const XFS_MAGIC: i64 = 0x5846_5342;
    const BTRFS_MAGIC: i64 = 0x9123_683E;
    const ZFS_MAGIC: i64 = 0x2FC1_2FC1;
    const F2FS_MAGIC: i64 = 0xF2F5_2010;
    const OVERLAYFS_MAGIC: i64 = 0x794C_7630;
    const TMPFS_MAGIC: i64 = 0x0102_1994;
    const NFS_MAGIC: i64 = 0x6969;
    const SMB_MAGIC: i64 = 0xFF53_4D42;
    const SMB2_MAGIC: i64 = 0xFE53_4D42;
    const MSDOS_MAGIC: i64 = 0x4D44;
    const EXFAT_MAGIC: i64 = 0x2011_BAB0;

    let c_path = match CString::new(path.as_os_str().as_bytes()) {
        Ok(p) => p,
        Err(_) => return FsKind::Unknown("bad-path".into()),
    };
    let mut stat: libc::statfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statfs(c_path.as_ptr(), &mut stat) } != 0 {
        return FsKind::Unknown("statfs-failed".into());
    }
    match stat.f_type as i64 {
        EXT_MAGIC => FsKind::ExtFamily,
        XFS_MAGIC => FsKind::Xfs,
        BTRFS_MAGIC => FsKind::Btrfs,
        ZFS_MAGIC => FsKind::Zfs,
        F2FS_MAGIC => FsKind::F2fs,
        OVERLAYFS_MAGIC => FsKind::Overlayfs,
        TMPFS_MAGIC => FsKind::Tmpfs,
        NFS_MAGIC | SMB_MAGIC | SMB2_MAGIC => FsKind::NetworkFs,
        MSDOS_MAGIC => FsKind::Fat32,
        EXFAT_MAGIC => FsKind::ExFat,
        other => FsKind::Unknown(format!("magic-{other:#x}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fs_kind_support_matrix() {
        for ok in [
            FsKind::Ntfs,
            FsKind::ReFs,
            FsKind::Apfs,
            FsKind::HfsPlus,
            FsKind::ExtFamily,
            FsKind::Xfs,
            FsKind::Btrfs,
            FsKind::Zfs,
            FsKind::F2fs,
            FsKind::Overlayfs,
            FsKind::Tmpfs,
        ] {
            assert!(ok.is_supported(), "{} 应放行", ok.describe());
        }
        for bad in [FsKind::Fat32, FsKind::ExFat, FsKind::NetworkFs] {
            assert!(!bad.is_supported(), "{} 应拒绝", bad.describe());
        }
        assert!(
            FsKind::Unknown("x".into()).is_supported(),
            "未知类型放行但告警"
        );
    }

    #[test]
    fn env_override_parses_known_values() {
        // 测试内设置/清理 env，避免依赖外部环境
        for (raw, expect) in [
            ("fat32", FsKind::Fat32),
            ("EXFAT", FsKind::ExFat),
            ("ntfs", FsKind::Ntfs),
            ("smb", FsKind::NetworkFs),
            ("ext4", FsKind::ExtFamily),
        ] {
            std::env::set_var("QTRADING_PG_SIDECAR_FORCE_FS_KIND", raw);
            assert_eq!(FsKind::from_env_override(), Some(expect), "raw={raw}");
        }
        std::env::set_var("QTRADING_PG_SIDECAR_FORCE_FS_KIND", "zfs-custom");
        assert_eq!(
            FsKind::from_env_override(),
            Some(FsKind::Unknown("zfs-custom".into()))
        );
        std::env::remove_var("QTRADING_PG_SIDECAR_FORCE_FS_KIND");
        assert_eq!(FsKind::from_env_override(), None);
    }

    #[test]
    fn cloud_sync_detection_windows_paths() {
        assert!(
            detect_cloud_sync(Path::new(r"C:\Users\u\OneDrive\qTrading\postgres\17\data"))
                .is_some()
        );
        assert!(detect_cloud_sync(Path::new(r"C:\Users\u\Dropbox\data")).is_some());
        assert!(detect_cloud_sync(Path::new(r"D:\BaiduNetdiskWorkspace\pg\data")).is_some());
        assert!(detect_cloud_sync(Path::new(r"C:\Users\u\iCloudDrive\data")).is_some());
        assert!(detect_cloud_sync(Path::new(r"C:\Users\u\百度网盘\data")).is_some());
    }

    #[test]
    fn cloud_sync_detection_unix_paths() {
        assert!(detect_cloud_sync(Path::new("/home/u/Dropbox/pg/data")).is_some());
        assert!(detect_cloud_sync(Path::new("/home/u/Google Drive/pg/data")).is_some());
        assert!(detect_cloud_sync(Path::new("/home/u/坚果云/pg/data")).is_some());
        assert!(
            detect_cloud_sync(Path::new("/home/u/.local/share/qTrading/postgres/17/data"))
                .is_none()
        );
        assert!(detect_cloud_sync(Path::new("/mnt/data/postgres/17/data")).is_none());
    }

    #[test]
    fn cloud_sync_ignores_spaces_and_case() {
        // 规范化去空格+小写：Google Drive（真实文件夹含空格）须命中，"ONE DRIVE" 同理命中
        assert!(detect_cloud_sync(Path::new("/home/u/ONE DRIVE/data")).is_some());
        assert!(detect_cloud_sync(Path::new("/home/u/OneDrive - Company/data")).is_some());
    }

    #[test]
    fn writable_probe_roundtrip() {
        let dir = std::env::temp_dir().join(format!("qts-preflight-{}", std::process::id()));
        ensure_writable(&dir).unwrap();
        assert!(dir.exists());
        assert!(
            std::fs::read_dir(&dir).unwrap().next().is_none(),
            "probe 文件应已删除"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn writable_probe_rejects_readonly_dir() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("qts-preflight-ro-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o555)).unwrap();
        let result = ensure_writable(&dir);
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o755)).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(matches!(result, Err(PreflightFailure::NotWritable { .. })));
    }

    #[test]
    fn failure_names_stable() {
        assert_eq!(
            PreflightFailure::DiskSpace {
                path: PathBuf::from("/x"),
                free_mb: 1
            }
            .check_name(),
            "disk_space"
        );
        assert_eq!(
            PreflightFailure::NotWritable {
                path: PathBuf::from("/x")
            }
            .check_name(),
            "dir_writable"
        );
        assert_eq!(
            PreflightFailure::UnsupportedFs {
                kind: "FAT32".into()
            }
            .check_name(),
            "filesystem_type"
        );
        assert_eq!(
            PreflightFailure::CloudSyncPath {
                feature: "onedrive".into()
            }
            .check_name(),
            "cloud_sync_path"
        );
        assert_eq!(
            PreflightFailure::PasswordFilePerms {
                path: PathBuf::from("/x")
            }
            .check_name(),
            "password_file_perms"
        );
    }
}
