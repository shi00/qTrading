//! qtrading-pg-sidecar — Phase 1 MVP CLI 入口（pg_plan §6.1）。
//!
//! 命令：run/status/stop/doctor/dump/restore/maintenance-shell/version。
//! stdout = 机器协议信道（JSON），stderr = 人类日志信道（§6.2）。

mod cli;
mod commands;
mod exit_codes;
mod lockfile;
mod logging;
mod maint;
mod password;
mod paths;
mod pgbin;
mod preflight;
mod protocol;
mod run;
mod setup;
mod state;

use clap::Parser;
use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    setup_panic_hook();
    let parsed = cli::Cli::parse();
    let result: Result<(), u8> = match parsed.command {
        cli::Command::Run(args) => run::run(args).await,
        cli::Command::Status(args) => commands::status(&args.data_dir),
        cli::Command::Stop(args) => commands::stop(&args.data_dir).await,
        cli::Command::Doctor(args) => maint::doctor(&args.data_dir),
        cli::Command::Dump(args) => maint::dump(args).await,
        cli::Command::Restore(args) => maint::restore(args).await,
        cli::Command::MaintenanceShell(args) => maint::maintenance_shell(args).await,
        cli::Command::ResetPassword(args) => maint::reset_password(args).await,
        cli::Command::Version(args) => commands::version(args.json),
    };
    let code = match result {
        Ok(()) => exit_codes::SUCCESS,
        Err(code) => code,
    };
    // 绕过 tokio runtime 优雅 shutdown：run 命令的 supervise 中 tokio::io::stdin()
    // 会 spawn blocking thread 阻塞读 stdin；parent_pid 路径下 stdin 未关闭，
    // runtime drop 会等待该 thread 导致进程无法退出（test 7 hang 根因）。
    // 所有清理（graded_stop/state/emit_event）已在命令中完成，进程退出时 OS 回收资源。
    std::process::exit(code as i32);
}

/// 注册 panic hook：panic 时将信息写入 stderr（人类日志信道，§6.2），
/// 弥补 `std::process::exit()` 跳过 tokio runtime Drop 导致 tracing 日志可能丢失的问题（§13.7.30 崩溃报告）。
///
/// 不依赖 tracing（panic 时 tracing 可能未初始化或已损坏），eprintln 是兜底信道。
/// 调用默认 hook 保留默认 panic 行为（打印 location + 可选 backtrace）。
fn setup_panic_hook() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        eprintln!("[sidecar] panic: {info}");
        default_hook(info);
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setup_panic_hook_is_idempotent_and_safe() {
        // 多次调用安全：每次 take_hook 拿到上一次 set 的 hook，再包装一层。
        // 不 panic 即视为注册成功。
        setup_panic_hook();
        setup_panic_hook();
    }
}
