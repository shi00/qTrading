//! CLI 定义（pg_plan §6.1 命令列表，clap derive）。
//!
//! Phase 1 范围：run/status/stop/version/doctor/dump/restore/maintenance-shell。
//! `--password-source keyring` 与 `reset-password` 延后至后续 Phase（DoD 未列，YAGNI）。

use clap::{Parser, Subcommand};
use std::path::PathBuf;

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
    Doctor(DataDirArgs),
    /// 备份：运行中实例直连 pg_dump，或离线临时实例 pg_dump
    Dump(DumpArgs),
    /// 恢复到新目录，不覆盖原目录（§12.2，需维护锁）
    Restore(RestoreArgs),
    /// 临时维护实例：输出脱敏连接信息与 psql 路径，等待用户结束后关闭（需维护锁）
    MaintenanceShell(DataDirArgs),
    /// 版本与构建元数据
    Version(VersionArgs),
}

#[derive(clap::Args, Debug, Clone)]
pub struct DataDirArgs {
    /// PGDATA 目录（<app data>/postgres/17/data）
    #[arg(long)]
    pub data_dir: PathBuf,
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
}

#[derive(clap::Args, Debug, Clone)]
pub struct RestoreArgs {
    #[arg(long)]
    pub data_dir: PathBuf,
    /// 输入备份文件
    #[arg(long)]
    pub input: PathBuf,
    /// 恢复到该目录（缺省：恢复到 --data-dir 的 §12.2 原子切换流程）
    #[arg(long)]
    pub target_data_dir: Option<PathBuf>,
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
        for sub in ["stop", "status", "doctor", "maintenance-shell"] {
            let cli = parse(&["qtrading-pg-sidecar", sub, "--data-dir", "/d"]).unwrap();
            assert!(matches!(
                cli.command,
                Command::Stop(_)
                    | Command::Status(_)
                    | Command::Doctor(_)
                    | Command::MaintenanceShell(_)
            ));
        }
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
        ])
        .unwrap();
        let Command::Dump(args) = cli.command else {
            panic!("expect dump")
        };
        assert_eq!(args.output, PathBuf::from("/b/x.dump"));

        let cli = parse(&[
            "qtrading-pg-sidecar",
            "restore",
            "--data-dir",
            "/d",
            "--input",
            "/b/x.dump",
            "--target-data-dir",
            "/d2",
        ])
        .unwrap();
        let Command::Restore(args) = cli.command else {
            panic!("expect restore")
        };
        assert_eq!(args.input, PathBuf::from("/b/x.dump"));
        assert_eq!(args.target_data_dir, Some(PathBuf::from("/d2")));
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
