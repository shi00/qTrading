# qTrading 数据库离线维护工具

本目录提供 qTrading 主程序无法启动时的数据库诊断/备份/恢复入口，直接调用 embedded PostgreSQL sidecar binary，不依赖主程序运行。

> **危险操作提示**：`restore`（默认模式）会替换当前数据目录（原目录保留为 `.bak-<时间戳>`），`reset-password` 会覆盖密码文件。执行前必须完全退出 qTrading；执行 `restore` 前确认备份文件可用；在确认新数据可用前保留 `.bak-*` / `.restore-*` 目录（它们是唯一回滚点）。

## 适用场景

- qTrading 主程序启动失败，怀疑 embedded PostgreSQL 数据目录损坏
- 需要备份数据库（一致性快照，建议在主程序停止状态下执行）
- 需要从备份文件恢复数据库（成功后自动切换为当前数据目录，详见下文 restore 说明）
- 需要重置 postgres 密码（密码文件缺失、不可解密或与数据库不匹配）
- 诊断 sidecar / PostgreSQL 状态（postmaster.pid 残留、锁文件、版本不匹配等）

## 前置条件

- 已通过 **embedded variant** 安装包安装 qTrading（standard variant 不含 sidecar binary）
- sidecar binary 位于 `<安装目录>\_internal\sidecars\qtrading-pg-sidecar.exe`（Windows）或 `_internal/sidecars/qtrading-pg-sidecar`（Linux/macOS）
- 维护类命令（`restore` / `stop` / `maintenance-shell` / `reset-password`，以及离线模式 `dump`）需持有维护锁：qTrading 与 sidecar 必须完全退出，否则返回 exit 50
- 下文的默认数据目录与日志路径，仅在未在应用配置中自定义 `embedded_pg_data_root` / `embedded_pg_log_dir` 时成立（见「默认数据目录」）

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
| `dump <file>` | 备份数据库到文件（PostgreSQL custom format；运行中实例直连，离线时启动临时实例） | 输出文件路径 |
| `restore <file>` | 从备份文件恢复；默认采用原子切换（成功后替换当前数据目录，原目录改名为 `.bak-<时间戳>` 保留），详见「安全注意事项」第 2 条 | 输入文件路径 |
| `stop` | 停止运行中的 PostgreSQL（分级停止：smart 25s → fast 5s → kill） | 无 |
| `maintenance-shell` | 启动临时维护实例，输出脱敏连接信息与 psql 路径（提示走 stderr，stdout 无输出） | 无 |
| `version` | 显示 sidecar 版本与构建元数据 | 无 |
| `help` | 显示帮助 | 无 |

> 维护脚本未路由 `reset-password`（重置 postgres 密码）命令。密码文件缺失/不可解密/密码不匹配时，在 qTrading 完全退出且数据目录已初始化（`PG_VERSION` 存在）的前提下，直接调用 sidecar binary：`"<安装目录>\_internal\sidecars\qtrading-pg-sidecar.exe" reset-password --data-dir "<数据目录>"`（Linux/macOS 用对应 `_internal/sidecars/qtrading-pg-sidecar`）。成功后重启 qTrading，并重新 `dump` 建立新备份；完整分流见 `docs/guides/how-to.md` §9.9（源码仓库路径，安装包内不含该文档）。

## 默认数据目录

脚本自动使用 platformdirs 默认路径作为 `--data-dir`：

- **Windows**: `%LOCALAPPDATA%\qTrading\postgres\16\data`
- **Linux**: `~/.local/share/qTrading/postgres/16/data`（或 `$XDG_DATA_HOME/qTrading/postgres/16/data`）
- **macOS**: `~/Library/Application Support/qTrading/postgres/16/data`

如需操作其他数据目录，直接调用 sidecar binary 并显式传 `--data-dir`：

```cmd
"<安装目录>\_internal\sidecars\qtrading-pg-sidecar.exe" status --data-dir "D:\custom\pgdata"
```

> 若曾在应用配置中自定义 `embedded_pg_data_root`（数据目录）或 `embedded_pg_log_dir`（日志目录），脚本的上述默认路径不再适用：`status` 会指向空目录并显示 `not_initialized`，`doctor` 会报 `initialized: false`（`state_file: missing`），`restore` 也会作用在错误目录。此时一律直接调用 sidecar binary 并显式传 `--data-dir`。内置 PostgreSQL 主版本以安装包随附的 sidecar 为准（当前默认 `16`），升级主版本后路径中的版本号需相应调整。

## 安全注意事项

1. **dump 的在线/离线模式**：主程序运行时 `dump` 直连运行中的实例执行 `pg_dump`（其输出为事务级一致性快照）；若希望备份与当前应用状态严格对应，或应用已无法正常响应，建议先 `stop`，由 sidecar 启动离线临时实例再备份（离线模式需持有维护锁）
2. **restore 采用原子切换**：先恢复到数据目录同级的 `<数据目录名>.restore-<时间戳>` 临时目录，全部成功后**自动切换为当前数据目录**，旧目录改名为 `<数据目录名>.bak-<时间戳>` **保留为备份（原数据不被销毁）**，确认恢复无误后可自行清理；恢复中途失败（含健康检查失败）会自动清理临时目录，原数据目录不受影响。**切换阶段例外**：旧目录改名失败时，原目录与 `.restore-*` 临时目录均原地保留；旧目录改名成功但新目录切换失败时，sidecar 会尝试回滚，若回滚也失败，原数据仅存于 `.bak-<时间戳>`，需手动改名恢复——确认新库可正常启动并使用前，不得删除任何 `.bak-*` / `.restore-*` 目录。如需恢复到指定目录而不切换当前目录，直接调用 sidecar binary 并传 `--target-data-dir <新目录>`（目标目录须为空，不存在亦可；成功后需自行让应用指向该目录）
3. **维护锁前置**：`restore` / `stop` / `maintenance-shell` / `reset-password` 及离线 `dump` 均需维护锁，qTrading 或另一维护进程运行中会拒绝（exit 50）；先正常关闭 qTrading，存在活实例时再 `stop`
4. **操作前备份**：任何破坏性操作前先 `dump` 备份当前数据目录（凭证已丢失时 `dump` 不可用，见「故障排查」exit 16 行；数据目录损坏时 `dump` 也可能失败）
5. **备份文件安全**：`dump` 输出为未加密的 PostgreSQL custom format 文件，请勿放入云同步目录（OneDrive / iCloud / 网盘）或共享盘，也不要通过不受控渠道传输
6. **日志位置**：默认 sidecar 日志在 `<app data>/postgres-logs/sidecar.log`，service 日志在同目录 `embedded-pg-service.log`

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| `sidecar binary not found` | 使用了 standard variant 安装包 | 改用 embedded variant 安装包，或联系分发方 |
| `status` 显示 `not_initialized` | 数据目录未初始化（首次启动前），或脚本默认路径与实际配置的数据目录不一致 | 首次启动前属正常现象，启动 qTrading 会自动 initdb；若应用此前可正常使用，按「默认数据目录」核对自定义 `embedded_pg_data_root` 并显式传 `--data-dir` |
| `doctor` 报告 `state_file: corrupted` | state.json 损坏（异常退出/磁盘错误） | 按 doctor 输出指引修复，必要时从备份恢复 |
| `stop` / `restore` / `maintenance-shell` / `reset-password` 返回 exit 50 | 维护锁被持有：qTrading 或另一维护进程正在使用该 PGDATA | 先正常关闭 qTrading（必要时先 `stop`）再试；若为残留的维护进程/维护实例，结束该进程后重试 |
| `dump`/`restore` 等返回 exit 16，报「密码文件缺失…走 reset-password 流程」 | `runtime/password` 缺失、为空或不可解密（跨机器/账号复制后 DPAPI 无法解密）；数据目录本身未必损坏 | 先 `doctor` 确认 `issues` 无数据损坏项，再按 `docs/guides/how-to.md` §9.9 走 reset-password 分流（注意：此时 `dump` 补做备份也不可用，恢复只能依赖既有备份文件） |
| `dump` / `restore` 返回 exit 30 | 备份/恢复执行失败：备份文件不存在或损坏、版本不兼容、数据目录未初始化（`dump` 无可备份数据）、`--target-data-dir` 目标非空、`pg_dump`/`pg_restore` 失败、恢复目录切换失败 | 核对输入文件路径与完整性；除切换阶段外，失败时 `.partial` 与恢复临时目录会自动清理；切换阶段失败的保留物处理见「安全注意事项」第 2 条 |
| 应用启动时 sidecar `run` 返回 exit 40 / `doctor` `issues` 报 pg_control/WAL 损坏、关键文件缺失 | 数据目录损坏 | 按 `docs/guides/how-to.md` §9.9.2 恢复路径处理：先确认备份文件与 `.bak-*`/`.restore-*` 目录等恢复点，再执行 restore；若密码文件同时不可用且无法 reset-password，则无自动恢复路径——移走损坏目录后由应用全新初始化，库内数据不可恢复 |

## 相关文档

- sidecar 命令详细语义（源码仓库）：`sidecars/qtrading-pg-sidecar/src/cli.rs`
- 离线维护完整流程与故障分流（源码仓库）：`docs/guides/how-to.md` §9

> 以上为源码仓库内路径；安装包的 `resources/maintenance` 目录仅包含维护脚本与本 README，不含 `docs/` 与 `sidecars/` 源码。
