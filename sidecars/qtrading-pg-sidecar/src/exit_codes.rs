//! exit code 约定（pg_plan §6.4，编号 append-only，禁止复用）。

pub const SUCCESS: u8 = 0;
pub const ARGUMENT_ERROR: u8 = 2;
pub const SETUP_FAILED: u8 = 10;
pub const INITDB_FAILED: u8 = 11;
pub const START_FAILED: u8 = 12;
pub const HEALTH_CHECK_FAILED: u8 = 13;
pub const CREATE_DATABASE_FAILED: u8 = 14;
/// 资源预检失败：磁盘空间不足 / 目录权限不足 / 文件系统不支持 / 网盘同步路径 / password file 权限过宽
pub const PREFLIGHT_FAILED: u8 = 15;
/// 密码/keyring 读取失败（密码条目缺失且 PGDATA 已存在、认证 28P01 不匹配）
pub const PASSWORD_FAILED: u8 = 16;
pub const STOP_FAILED: u8 = 20;
pub const DUMP_RESTORE_FAILED: u8 = 30;
/// 数据目录状态异常（非空无 PG_VERSION / 版本不匹配 / pg_control·WAL 损坏 / 关键文件缺失）
pub const DATA_DIR_ABNORMAL: u8 = 40;
/// 维护锁冲突：qTrading 或另一个维护进程正在使用该 PGDATA
pub const LOCK_CONFLICT: u8 = 50;
/// 监督期间 PostgreSQL 意外退出（sidecar 不自动重启）
pub const POSTGRES_DIED: u8 = 60;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_match_plan_section_6_4() {
        assert_eq!(SUCCESS, 0);
        assert_eq!(ARGUMENT_ERROR, 2);
        assert_eq!(SETUP_FAILED, 10);
        assert_eq!(INITDB_FAILED, 11);
        assert_eq!(START_FAILED, 12);
        assert_eq!(HEALTH_CHECK_FAILED, 13);
        assert_eq!(CREATE_DATABASE_FAILED, 14);
        assert_eq!(PREFLIGHT_FAILED, 15);
        assert_eq!(PASSWORD_FAILED, 16);
        assert_eq!(STOP_FAILED, 20);
        assert_eq!(DUMP_RESTORE_FAILED, 30);
        assert_eq!(DATA_DIR_ABNORMAL, 40);
        assert_eq!(LOCK_CONFLICT, 50);
        assert_eq!(POSTGRES_DIED, 60);
    }
}
