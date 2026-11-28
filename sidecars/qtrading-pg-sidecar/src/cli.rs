//! CLI 定义（pg_plan §6.1 命令列表，clap derive）。
//!
//! Phase 1 范围：run/status/stop/version/doctor/dump/restore/maintenance-shell。
//! `--password-source keyring` 延后至后续 Phase（DoD 未列，YAGNI）。
//! `reset-password` 已落地（§13.7.8，P1-1）。

use clap::{Parser, Subcommand};
use std::path::PathBuf;

/// 默认业务库名（与 ``AppConfig.embedded_pg_database`` 默认值一致）。
///
/// 仅作为单元测试基准使用：维护类子命令（``dump`` / ``restore`` /
/// ``maintenance-shell``）必须显式传 ``--database``，静默回落默认库会让自定义库
/// 部署备份/恢复到错误库（D07-005）。
#[cfg(test)]
pub const DEFAULT_DATABASE: &str = "qtrading";

/// 默认超级用户名（与 ``AppConfig.embedded_pg_username`` 默认值一致）。
///
/// 未显式传 ``--username`` 的旧脚本/旧命令保持原语义（以 postgres 角色连接）；
/// Python 侧仅在配置为非默认用户名时才透传 ``--username``（旧 sidecar 无从识别该参数）。
pub const DEFAULT_USERNAME: &str = "postgres";

#[derive(Parser, Debug)]
#[command(
    name = "qtrading-pg-sidecar",
    version,
    about = "qTrading embedded PostgreSQL sidecar"
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// 常驻 supervisor：setup → initdb → start → ready JSON → 监督 → graceful stop
    Run(RunArgs),
    /// 停止该 PGDATA 上运行中的 PostgreSQL（分级停止：smart 25s → fast 5s → kill）
    Stop(DataDirArgs),
    /// 查询状态（stdout 输出 status JSON）
    Status(DataDirArgs),
    /// 诊断数据目录/版本/锁/上次异常退出（stdout 输出 doctor JSON）
    Doctor(DoctorArgs),
    /// 备份：运行中实例直连 pg_dump，或离线临时实例 pg_dump
    Dump(DumpArgs),
    /// 恢复到新目录，不覆盖原目录（§12.2，需维护锁）
    Restore(RestoreArgs),
    /// 临时维护实例：输出脱敏连接信息与 psql 路径，等待用户结束后关闭（需维护锁）
    MaintenanceShell(MaintenanceShellArgs),
    /// 重置超级用户密码（§13.7.8，需维护锁；临时 trust 实例 + ALTER USER，支持自定义超级用户名 M23）
    ResetPassword(ResetPasswordArgs),
    /// 版本与构建元数据
    Version(VersionArgs),
}

#[derive(clap::Args, Debug, Clone)]
pub struct DataDirArgs {
    /// PGDATA 目录（<app data>/postgres/16/data）
    #[arg(long)]
    pub data_dir: PathBuf,
}

#[derive(clap::Args, Debug, Clone)]
pub struct DoctorArgs {
    /// PGDATA 目录（<app data>/postgres/16/data）
    #[arg(long)]
    pub data_dir: PathBuf,
    /// 业务库名（可选；提供时诊断其是否满足维护命令标识符契约，
    /// 用于存量非标名称集群的兼容迁移提示）
    #[arg(long)]
    pub database: Option<String>,
    /// 超级用户名（可选；同上）
    #[arg(long)]
    pub username: Option<String>,
}

#[derive(clap::Args, Debug, Clone)]
pub struct ResetPasswordArgs {
    #[arg(long)]
    pub data_dir: PathBuf,
    /// 超级用户名（须与 `run --username` / `AppConfig.embedded_pg_username` 一致，
    /// 否则 ALTER USER 作用在不存在的角色上，改密无效；缺省 postgres）
    #[arg(long, default_value = DEFAULT_USERNAME)]
    pub username: String,
}

#[derive(clap::Args, Debug, Clone)]
pub struct RunArgs {
    #[arg(long)]
    pub data_dir: PathBuf,
    /// PostgreSQL binaries 安装目录（默认 <data-dir>/../install）
    #[arg(long)]
    pub install_dir: Option<PathBuf>,
    /// 密码文件路径（默认 <data-dir>/../runtime/password）
    #[arg(long)]
    pub password_file: Option<PathBuf>,
    /// 业务库名
    #[arg(long, default_value = "qtrading")]
    pub database: String,
    /// 超级用户名
    #[arg(long, default_value = "postgres")]
    pub username: String,
    /// 监听地址（仅本机）
    #[arg(long, default_value = "127.0.0.1")]
    pub listen: String,
    /// 端口；0 = 随机分配（每次启动重新随机，不复用 state.json，AI-15）
    #[arg(long, default_value_t = 0)]
    pub port: u16,
    /// sidecar 日志文件（默认 <app data>/postgres-logs/sidecar.log）
    #[arg(long)]
    pub log_file: Option<PathBuf>,
    /// 父进程 PID（Unix 兜底轮询；Windows 主用 parent pipe EOF，PID 复用风险）
    #[arg(long)]
    pub parent_pid: Option<u32>,
}

#[derive(clap::Args, Debug, Clone)]
pub struct DumpArgs {
    #[arg(long)]
    pub data_dir: PathBuf,
    /// 输出备份文件（PostgreSQL custom format；先写 <output>.partial 再原子改名）
    #[arg(long)]
    pub output: PathBuf,
    /// 业务库名（必填；须与 `AppConfig.embedded_pg_database` 一致）
    ///
    /// 不提供默认值：静默回落 `qtrading` 会让自定义库部署备份到错误库（D07-005）。
    #[arg(long, required = true)]
    pub database: String,
    /// 超级用户名（须与 `run --username` / `AppConfig.embedded_pg_username` 一致，
    /// 否则在线/离线维护连接会以错误角色认证失败；缺省 postgres）
    #[arg(long, default_value_t = DEFAULT_USERNAME.to_string())]
    pub username: String,
}

#[derive(clap::Args, Debug, Clone)]
pub struct RestoreArgs {
    #[arg(long)]
    pub data_dir: PathBuf,
    /// 输入备份文件
    #[arg(long)]
    pub input: PathBuf,
    /// 恢复到该目录（缺省：恢复到 --data-dir 的 §12.2 原子切换流程）。
    ///
    /// 独立布局须为 `<data root>/data`（如 `<app data>/postgres/16/data`），恢复成功后
    /// sidecar 会为该布局写入同一密码；与源数据目录同级的目录共享 runtime，沿用源凭据。
    #[arg(long)]
    pub target_data_dir: Option<PathBuf>,
    /// 业务库名（必填；须与 `AppConfig.embedded_pg_database` 一致）
    ///
    /// 不提供默认值：静默回落 `qtrading` 会让自定义库部署恢复到错误库（D07-005）。
    #[arg(long, required = true)]
    pub database: String,
    /// 超级用户名（initdb 新 cluster 的超级用户，须与 `run --username` 一致；
    /// 缺省 postgres）
    #[arg(long, default_value_t = DEFAULT_USERNAME.to_string())]
    pub username: String,
}

#[derive(clap::Args, Debug, Clone)]
pub struct MaintenanceShellArgs {
    #[arg(long)]
    pub data_dir: PathBuf,
    /// 业务库名（必填；用于连接串与 psql 提示，须为 `AppConfig.embedded_pg_database`）
    ///
    /// 不提供默认值：自定义库部署下静默显示 `qtrading` 会误导连接/恢复操作（D07-005）。
    #[arg(long, required = true)]
    pub database: String,
    /// 超级用户名（须与 `run --username` / `AppConfig.embedded_pg_username` 一致；
    /// 缺省 postgres）
    #[arg(long, default_value_t = DEFAULT_USERNAME.to_string())]
    pub username: String,
}

#[derive(clap::Args, Debug, Clone)]
pub struct VersionArgs {
    /// 输出机器可读 JSON（schema qtrading.embedded_postgres.version.v1）
    #[arg(long)]
    pub json: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
        Cli::try_parse_from(args)
    }

    #[test]
    fn run_defaults() {
        let cli = parse(&["qtrading-pg-sidecar", "run", "--data-dir", "/d"]).unwrap();
        let Command::Run(args) = cli.command else {
            panic!("expect run")
        };
        assert_eq!(args.database, "qtrading");
        assert_eq!(args.username, "postgres");
        assert_eq!(args.listen, "127.0.0.1");
        assert_eq!(args.port, 0);
        assert!(args.install_dir.is_none());
        assert!(args.parent_pid.is_none());
    }

    #[test]
    fn run_full_args() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "run",
            "--data-dir",
            "/d",
            "--install-dir",
            "/i",
            "--password-file",
            "/p",
            "--database",
            "mydb",
            "--username",
            "admin",
            "--listen",
            "127.0.0.1",
            "--port",
            "5544",
            "--log-file",
            "/l/sidecar.log",
            "--parent-pid",
            "1234",
        ])
        .unwrap();
        let Command::Run(args) = cli.command else {
            panic!("expect run")
        };
        assert_eq!(
            args.install_dir.as_deref(),
            Some(std::path::Path::new("/i"))
        );
        assert_eq!(
            args.password_file.as_deref(),
            Some(std::path::Path::new("/p"))
        );
        assert_eq!(args.database, "mydb");
        assert_eq!(args.username, "admin");
        assert_eq!(args.port, 5544);
        assert_eq!(args.parent_pid, Some(1234));
    }

    #[test]
    fn run_requires_data_dir() {
        assert!(parse(&["qtrading-pg-sidecar", "run"]).is_err());
    }

    #[test]
    fn simple_commands_parse() {
        for sub in ["stop", "status", "doctor", "reset-password"] {
            let cli = parse(&["qtrading-pg-sidecar", sub, "--data-dir", "/d"]).unwrap();
            assert!(matches!(
                cli.command,
                Command::Stop(_)
                    | Command::Status(_)
                    | Command::Doctor(_)
                    | Command::ResetPassword(_)
            ));
        }
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "maintenance-shell",
            "--data-dir",
            "/d",
            "--database",
            DEFAULT_DATABASE,
        ])
        .unwrap();
        assert!(matches!(cli.command, Command::MaintenanceShell(_)));
    }

    #[test]
    fn reset_password_username_default_and_override() {
        // 旧命令兼容：未传 --username 时仍以 postgres 角色改密
        let cli = parse(&["qtrading-pg-sidecar", "reset-password", "--data-dir", "/d"]).unwrap();
        let Command::ResetPassword(args) = cli.command else {
            panic!("expect reset-password")
        };
        assert_eq!(args.username, DEFAULT_USERNAME);

        // 自定义超级用户名实例（run --username qtrading）须显式透传
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "reset-password",
            "--data-dir",
            "/d",
            "--username",
            "qtrading",
        ])
        .unwrap();
        let Command::ResetPassword(args) = cli.command else {
            panic!("expect reset-password")
        };
        assert_eq!(args.username, "qtrading");
    }

    #[test]
    fn reset_password_help_lists_username() {
        let err = parse(&["qtrading-pg-sidecar", "reset-password", "--help"]).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::DisplayHelp);
        let rendered = err.to_string();
        assert!(rendered.contains("--username"), "help 须列出 --username");
        assert!(
            rendered.contains(DEFAULT_USERNAME),
            "help 须列出默认值 {DEFAULT_USERNAME}"
        );
    }

    #[test]
    fn dump_restore_args() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "dump",
            "--data-dir",
            "/d",
            "--output",
            "/b/x.dump",
            "--database",
            "qtrading",
        ])
        .unwrap();
        let Command::Dump(args) = cli.command else {
            panic!("expect dump")
        };
        assert_eq!(args.output, PathBuf::from("/b/x.dump"));
        assert_eq!(args.database, "qtrading");
        // M21: 未传 --username 时缺省 postgres（旧命令向后兼容）
        assert_eq!(args.username, DEFAULT_USERNAME);

        let cli = parse(&[
            "qtrading-pg-sidecar",
            "restore",
            "--data-dir",
            "/d",
            "--input",
            "/b/x.dump",
            "--target-data-dir",
            "/d2",
            "--database",
            "qtrading",
        ])
        .unwrap();
        let Command::Restore(args) = cli.command else {
            panic!("expect restore")
        };
        assert_eq!(args.input, PathBuf::from("/b/x.dump"));
        assert_eq!(args.target_data_dir, Some(PathBuf::from("/d2")));
        assert_eq!(args.database, "qtrading");
        assert_eq!(args.username, DEFAULT_USERNAME);
    }

    /// D07-005：维护命令不得静默回落默认库，缺 --database 必须解析失败。
    #[test]
    fn maintenance_commands_require_database() {
        for args in [
            vec![
                "qtrading-pg-sidecar",
                "dump",
                "--data-dir",
                "/d",
                "--output",
                "/b/x.dump",
            ],
            vec![
                "qtrading-pg-sidecar",
                "restore",
                "--data-dir",
                "/d",
                "--input",
                "/b/x.dump",
            ],
            vec![
                "qtrading-pg-sidecar",
                "maintenance-shell",
                "--data-dir",
                "/d",
            ],
        ] {
            let err = parse(&args).unwrap_err();
            assert_eq!(err.kind(), clap::error::ErrorKind::MissingRequiredArgument);
            assert!(err.to_string().contains("--database"), "{err}");
        }
    }

    /// 空库名可以通过 argv 进入，但必须由 maint 的标识符白名单拒绝（不让空值静默生效）。
    #[test]
    fn empty_database_parses_but_is_rejected_later() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "dump",
            "--data-dir",
            "/d",
            "--output",
            "/b/x.dump",
            "--database",
            "",
        ])
        .unwrap();
        let Command::Dump(args) = cli.command else {
            panic!("expect dump")
        };
        assert!(args.database.is_empty());
    }

    #[test]
    fn maintenance_shell_username_default_and_override() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "maintenance-shell",
            "--data-dir",
            "/d",
            "--database",
            "qtrading",
        ])
        .unwrap();
        let Command::MaintenanceShell(args) = cli.command else {
            panic!("expect maintenance-shell")
        };
        assert_eq!(args.username, DEFAULT_USERNAME);
        assert_eq!(args.database, "qtrading");

        let cli = parse(&[
            "qtrading-pg-sidecar",
            "maintenance-shell",
            "--data-dir",
            "/d",
            "--database",
            "stock_db",
            "--username",
            "qtrading",
        ])
        .unwrap();
        let Command::MaintenanceShell(args) = cli.command else {
            panic!("expect maintenance-shell")
        };
        assert_eq!(args.username, "qtrading");
        assert_eq!(args.database, "stock_db");
    }

    #[test]
    fn dump_restore_username_override() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "dump",
            "--data-dir",
            "/d",
            "--output",
            "/b/x.dump",
            "--database",
            "qtrading",
            "--username",
            "qtrading",
        ])
        .unwrap();
        let Command::Dump(args) = cli.command else {
            panic!("expect dump")
        };
        assert_eq!(args.username, "qtrading");

        let cli = parse(&[
            "qtrading-pg-sidecar",
            "restore",
            "--data-dir",
            "/d",
            "--input",
            "/b/x.dump",
            "--database",
            "qtrading",
            "--username",
            "qtrading",
        ])
        .unwrap();
        let Command::Restore(args) = cli.command else {
            panic!("expect restore")
        };
        assert_eq!(args.username, "qtrading");
    }

    #[test]
    fn dump_restore_database_override() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "dump",
            "--data-dir",
            "/d",
            "--output",
            "/b/x.dump",
            "--database",
            "stock_db",
        ])
        .unwrap();
        let Command::Dump(args) = cli.command else {
            panic!("expect dump")
        };
        assert_eq!(args.database, "stock_db");

        let cli = parse(&[
            "qtrading-pg-sidecar",
            "restore",
            "--data-dir",
            "/d",
            "--input",
            "/b/x.dump",
            "--database",
            "stock_db",
        ])
        .unwrap();
        let Command::Restore(args) = cli.command else {
            panic!("expect restore")
        };
        assert_eq!(args.database, "stock_db");
    }

    #[test]
    fn restore_target_optional() {
        let cli = parse(&[
            "qtrading-pg-sidecar",
            "restore",
            "--data-dir",
            "/d",
            "--input",
            "/b/x.dump",
            "--database",
            "qtrading",
        ])
        .unwrap();
        let Command::Restore(args) = cli.command else {
            panic!("expect restore")
        };
        assert!(args.target_data_dir.is_none());
    }

    #[test]
    fn version_json_flag() {
        let cli = parse(&["qtrading-pg-sidecar", "version", "--json"]).unwrap();
        let Command::Version(args) = cli.command else {
            panic!("expect version")
        };
        assert!(args.json);
        let cli = parse(&["qtrading-pg-sidecar", "version"]).unwrap();
        let Command::Version(args) = cli.command else {
            panic!("expect version")
        };
        assert!(!args.json);
    }

    #[test]
    fn unknown_subcommand_rejected() {
        assert!(parse(&["qtrading-pg-sidecar", "bogus"]).is_err());
    }
}
