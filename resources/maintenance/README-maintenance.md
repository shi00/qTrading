# qTrading 数据库离线维护工具

本目录提供 qTrading 主程序无法启动时的数据库诊断/备份/恢复入口，直接调用 embedded PostgreSQL sidecar binary，不依赖主程序运行。

## 适用场景

- qTrading 主程序启动失败，怀疑 embedded PostgreSQL 数据目录损坏
- 需要在主程序停止状态下备份数据库（一致性快照）
- 需要从备份文件恢复数据库到新目录
- 诊断 sidecar / PostgreSQL 状态（postmaster.pid 残留、锁文件、版本不匹配等）

## 前置条件

- 已通过 **embedded variant** 安装包安装 qTrading（standard variant 不含 sidecar binary）
- sidecar binary 位于 `<安装目录>\_internal\sidecars\qtrading-pg-sidecar.exe`（Windows）或 `_internal/sidecars/qtrading-pg-sidecar`（Linux/macOS）

## 使用方法

### Windows

```cmd
cd "<安装目录>\resources\maintenance"
qtrading-db-maintenance.bat <command> [args]
```

### Linux / macOS

```bash
cd "<安装目录>/resources/maintenance"
./qtrading-db-maintenance.sh <command> [args]
```

## 命令列表

| 命令 | 说明 | 参数 |
|------|------|------|
| `status` | 查询 embedded PostgreSQL 状态（state.json + postmaster.pid 活性 + 锁探测） | 无 |
| `doctor` | 诊断数据目录/版本/锁/上次异常退出，输出 doctor JSON | 无 |
| `dump <file>` | 备份数据库到文件（PostgreSQL custom format） | 输出文件路径 |
| `restore <file>` | 从备份文件恢复到新目录（不覆盖原目录，§12.2 原子切换流程） | 输入文件路径 |
| `stop` | 停止运行中的 PostgreSQL（分级停止：smart 25s → fast 5s → kill） | 无 |
| `maintenance-shell` | 启动临时维护实例，输出脱敏连接信息与 psql 路径 | 无 |
| `version` | 显示 sidecar 版本与构建元数据 | 无 |
| `help` | 显示帮助 | 无 |

## 默认数据目录

脚本自动使用 platformdirs 默认路径作为 `--data-dir`：

- **Windows**: `%LOCALAPPDATA%\qTrading\postgres\17\data`
- **Linux**: `~/.local/share/qTrading/postgres/17/data`（或 `$XDG_DATA_HOME/qTrading/postgres/17/data`）
- **macOS**: `~/Library/Application Support/qTrading/postgres/17/data`

如需操作其他数据目录，直接调用 sidecar binary 并显式传 `--data-dir`：

```cmd
"<安装目录>\_internal\sidecars\qtrading-pg-sidecar.exe" status --data-dir "D:\custom\pgdata"
```

## 安全注意事项

1. **备份前先 stop**：`dump` 命令在运行中实例上使用 `pg_dump` 直连，但为获得一致性快照建议先 `stop` 再 `dump`（离线临时实例模式）
2. **restore 不覆盖原目录**：恢复到 `<data-dir>-restored-<timestamp>` 新目录，需手动确认数据无误后切换
3. **maintenance-shell 需维护锁**：sidecar 运行中会拒绝（exit 50），需先 `stop` 主程序
4. **操作前备份**：任何破坏性操作前先 `dump` 备份当前数据目录
5. **日志位置**：sidecar 日志在 `<app data>/postgres-logs/sidecar.log`，service 日志在 `embedded-pg-service.log`

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| `sidecar binary not found` | 使用了 standard variant 安装包 | 改用 embedded variant 安装包，或联系分发方 |
| `status` 显示 `not_initialized` | 数据目录未初始化（首次启动前） | 正常现象，启动 qTrading 会自动 initdb |
| `doctor` 报告 `state_file: corrupted` | state.json 损坏（异常退出/磁盘错误） | 按 doctor 输出指引修复，必要时从备份恢复 |
| `stop` 返回 exit 50 | sidecar 运行中（维护锁被持有） | 先关闭 qTrading 主程序再试 |
| `dump` 报 `password_file not found` | runtime/password 缺失 | 数据目录可能损坏，从备份恢复 |

## 相关文档

- 主计划：`reviews/pg_plan.md`
- Phase 4 计划：`reviews/pg_phase4_plan.md`
- sidecar 命令详细语义：`sidecars/qtrading-pg-sidecar/src/cli.rs`
