# D16 · Rust sidecar、内置 PostgreSQL 与恢复维护

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责31文件：31完整语义阅读、0完成有限来源核对、0待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共10项；其中本次新增接受0项缺陷、0项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**发现确定问题，任务保持 blocked；不能声称恢复可靠。** 31/31 个主责文件逐段读至 EOF，共 10,788 行；完整 31、部分 0、未读 0、读取失败 0，31 个 SHA-256 均匹配 scope 冻结值。源文件全读来自实际正文阅读，不来自目录枚举、协议字段提取或 AST 统计。

| 类别 | 数量 | ID |
| --- | --- | --- |
| defect | 10 | D16-F01–F10 |
| scenario_gap | 0 | — |
| test_gap | 3 | D16-T01–T03 |
| question | 1 | D16-Q01 |
| risk | 3 | D16-R01–R03 |
| out_of_scope | 0 | — |
| suggestion | 1 | D16-S01 |

确定缺陷分级：P1 × 1、P2 × 7、P3 × 2。最高风险为 F01：安装目录与数据目录重叠时，setup 在 PGDATA 守卫前删除数据。其余包括 PID 归属、活孤儿实例恢复、用户名协议、异地恢复凭据、并发备份、密码通道与 Windows 编码。

**动态验证 0**：未导入/执行项目代码、pytest、Cargo、sidecar、PG、迁移或维护脚本；未安装、联网、读凭据值、读真实数据/日志，未操作任何进程。标准库哈希、源码字段提取及自写字符串编码反例仅属静态取证，不算项目测试或故障注入。

门禁分别表述：按 review-policy，F01 的未豁免 P1 使**发布评审 fail**；公共 G 的运行证据与独立复核仍不足，**G 无法判定，task_status=blocked**。不是已完成、不是批准修复/发布。

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D16-evidence.json](D16-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

冻结清单：`scope.csv` 中 `primary_dimension=D16`，快照 inventory SHA-256：
`be4d0bbd515585b3cdb45f9a452e34b05230aa5adc8c2de60ee163bff312ac98`。无 Git 历史、**无版本控制兜底**；只能认定快照既存问题，不能归因于某提交。全部主责路径、实际行数、完整 `[1, EOF]` 行段、哈希和关键符号见 evidence 的 `file_reads`；本节按原路径列出主责，非目录猜测。

| 主责路径 | 行数 |
| --- | --- |
| `data\persistence\embedded_postgres\__init__.py` | 6 |
| `data\persistence\embedded_postgres\protocol.py` | 34 |
| `data\persistence\embedded_postgres\service.py` | 854 |
| `data\persistence\embedded_postgres\version.py` | 93 |
| `resources\maintenance\qtrading-db-maintenance.bat` | 110 |
| `resources\maintenance\qtrading-db-maintenance.sh` | 136 |
| `resources\maintenance\README-maintenance.md` | 114 |
| `scripts\check_doctor_schema.py` | 158 |
| `scripts\verify_embedded_pg_connect.py` | 272 |
| `services\embedded_pg_maintenance_service.py` | 520 |
| `sidecars\qtrading-pg-sidecar\.cargo\config.toml` | 8 |
| `sidecars\qtrading-pg-sidecar\.gitignore` | 1 |
| `sidecars\qtrading-pg-sidecar\build.rs` | 152 |
| `sidecars\qtrading-pg-sidecar\src\cli.rs` | 316 |
| `sidecars\qtrading-pg-sidecar\src\commands.rs` | 377 |
| `sidecars\qtrading-pg-sidecar\src\exit_codes.rs` | 44 |
| `sidecars\qtrading-pg-sidecar\src\lockfile.rs` | 141 |
| `sidecars\qtrading-pg-sidecar\src\logging.rs` | 285 |
| `sidecars\qtrading-pg-sidecar\src\main.rs` | 74 |
| `sidecars\qtrading-pg-sidecar\src\maint.rs` | 2161 |
| `sidecars\qtrading-pg-sidecar\src\password.rs` | 365 |
| `sidecars\qtrading-pg-sidecar\src\paths.rs` | 122 |
| `sidecars\qtrading-pg-sidecar\src\pgbin.rs` | 913 |
| `sidecars\qtrading-pg-sidecar\src\preflight.rs` | 523 |
| `sidecars\qtrading-pg-sidecar\src\protocol.rs` | 186 |
| `sidecars\qtrading-pg-sidecar\src\run.rs` | 618 |
| `sidecars\qtrading-pg-sidecar\src\setup.rs` | 647 |
| `sidecars\qtrading-pg-sidecar\src\state.rs` | 124 |
| `sidecars\qtrading-pg-sidecar\tests\common\mod.rs` | 268 |
| `sidecars\qtrading-pg-sidecar\tests\failure_injection.rs` | 588 |
| `sidecars\qtrading-pg-sidecar\tests\roundtrip.rs` | 578 |

其中 28 个 Python/Rust/shell 可执行源码文件，另 3 个为 README、Cargo 环境配置、ignore；不把后者或 JSON 解析计作额外源码覆盖。Cargo manifest/lock 主责 D19，本维度不重复检视依赖供应链。

契约依据：`all-run-brief.md`、Plans 的 D16/G；`docs\guides\how-to.md` §9/§12；`docs\reviews\ai-review.md`、project/cli-iac Profile、review-policy；技术债内 Rust/CON04、备份及 header 条目；EX-0011/EX-0012。跨层仅补读实际 caller 所需的 `app\bootstrap.py`、`utils\config_models.py`、备份/状态 VM 与相关单测行段，详见 evidence，不冒充这些外维度文件全读。

非目标：实施补丁、真实恢复、验证远端 CI、验证 theseus/crate 内部实现、读取真实 ACL/凭据/数据库。未确认第三方 crate Drop、OS 文件共享及持久化语义时明确降为风险，不引用注释冒充 API 事实。

## 3. 模型与场景

### 3.1 实际调用、状态与信任边界

- 应用：`prepare_database_runtime` → `EmbeddedPostgresService.from_config/start` → `Popen(run, stdin/stdout/stderr PIPE, --parent-pid)` → 首行 ready → 从文件解密密码、构造仅内存 URL → 交给上层建引擎。version 解析有成功缓存；启动二进制另有 frozen 严格 SHA 校验。
- Rust run：维护锁 → preflight → `ensure_binaries` → `guard_data_dir` → 密码 → Fresh initdb → 基线 → PID 判断 → 随机端口重试 → SQL 健康检查/建库 → state → ready → supervise → graded_stop/state/exit。
- 监督：stdin EOF 主用，500ms parent PID 辅助，1s PG PID，30s 磁盘告警。Unix SIGTERM 与全平台 ctrl-c 分支明确存在；Windows 非 Unix SIGTERM 分支为 pending，不臆称 Windows Job Object 已实现。`failure_injection.rs` 的 Job Object 注释不是实现证据。
- 在线 dump：读取 PGDATA PID/port，存活则不占维护锁，连接 `127.0.0.1` 以默认 `postgres` 执行 `pg_dump -Fc -f <output>.partial`，成功 rename。离线 dump 持锁、起临时实例再备份。
- restore：输入存在 → 源维护锁 → 旧密码/工具 → 新目录 initdb/临时实例/建库/pg_restore/select 1 → stop → 默认双 rename 保留 `.bak-*`；显式 target 不切换。UI `confirm_restore` **不直接执行恢复**，只给退出应用后的指引。
- 凭据：Windows `PGPW2:` + DPAPI；Unix 明文但新建 0600、重写后收紧权限。`run_initdb` 额外生成明文 `.pwinitdb`，正常/工具错误路径删除；kill/断电不保证清理。persistent 文件长期保留属设计，不是泄露的充分证据。

### 3.2 stdout/stderr/退出码逐项核对

| 命令/事件 | Rust 实际输出 | Python 消费与边界 |
| --- | --- | --- |
| run ready | 单行 `run.ready.v1`，URL 密码掩码，host/port/PID/path | 校验 schema/status、数值字段；密码另取文件。合法非 object JSON 的 `.get` 防护不足属健壮性边界，不推成攻击 |
| warning/exit | `event.warning.v1`/`event.exit.v1` JSON-lines | `_start_stdout_reader_thread` 仅原样归档，未转结构化 UI 事件；未承诺已实时通知用户 |
| doctor | `doctor.v1`，诊断问题在 issues；成功 exit 0，不等于健康 | 服务校验 schema 与两个必填字段，port 严格 1..65535；DTO 为 Rust 子集，Q01 |
| status | `status.v1`，活性优先于旧 state | 未发现本维度应用客户端解析 status；维护脚本可直读。旧 state port 不等于活实例端口 |
| dump | stdout 空，提示 stderr；错误非零 | 服务以 exit 判成功，取文件大小；并发 partial F06、编码 F08 |
| restore | stdout 一行路径，**不是 JSON** | `stdout.strip()`，与当前 Rust 一致；输入相对路径时不能据文档声称输出必为绝对路径 |
| maintenance-shell | ready 后单行无 schema 的连接 JSON；提示 stderr；Enter/EOF 后 stop | `communicate()` 等退出才返回信息，不是可保持存活的机器会话；how-to 已明确此限制 |
| version --json | `version.v1`、协议/PG/构建/自 SHA | version.py 校验 schema、取主版本；没有执行该 binary |
| exit | 0/2/10/11/12/13/14/15/16/20/30/40/50/60 | 2=参数；10=setup；15=多种 preflight；20=stop 失败，不是“未运行”；60=监督 PG 死亡。Python错误映射 F10 |

`restore_into` 可传播 initdb 的 11、TempInstance 的 12；不能把 how-to 中“执行失败统一30”理解为每条实际路径均返回30。`reset_password` 临时启动同样可能传播12，而非注释宣称一律16。建议按实际退出码整理文档，不在本轮修改编号或强制合并错误。

### 3.3 成功、异常与平台矩阵

所有“测试”栏均指**静态审读断言，不是本轮运行成功**。

| 场景 | 实现/已有断言 | 结论/缺口 |
| --- | --- | --- |
| Fresh/Existing/非空无 PG_VERSION/主版本不同 | guard、initdb checksums；roundtrip 生命周期；fi24 | 基本守卫存在；先 setup 后 guard 的重叠路径 F01 |
| 半安装、marker hash 失配、解压残留 | marker_valid、临时解压/rename；fi31 手工布置残留 | 不是实际 kill-mid-extract/断电恢复证明 |
| 同 PGDATA 重复 run/正常维护冲突 | fs2 排他锁；roundtrip duplicate/maintenance rejection | 已有保护；不覆盖锁已释放但 PG 仍活的孤儿，F03 |
| 父退出/PG退出/sidecar崩溃重启 | EOF/PID、exit60；fi03/04/08/09 | fi03 还主动杀 PG，**不是只杀 sidecar 的孤儿恢复验证** |
| 端口占用 | run 随机选择、日志识别、≤3 次重试 | 未观察到真正抢端口后成功重试的本轮证据；临时实例无同款重试 |
| 磁盘/文件系统/杀软持锁 | preflight；fi06/27 env短路，fi26仅Unix chmod | env/chmod 不等于真实 ENOSPC、Windows sharing violation |
| stdin EOF/ctrl-c/SIGTERM/stop升级 | run supervise、graded_stop；roundtrip EOF；helper-parent | 无 Job Object 实证；PID 归属 F02；时间/停机失败 R01 |
| 正常 dump/restore/坏归档 | roundtrip6 验存在/PG_VERSION/bak，restore检查 pg_restore rc | 无业务行/约束/版本完整恢复证明；T01 |
| magicheader/空/截断 | VM读8字节；单测拒绝空、5字节与错误magic | 仅格式预检，不是归档完整性；CLI靠pg_restore错误码保全旧目录 |
| dump/restore中断 | fi28/28b 手工创建残留后断言 doctor | 不是真实中断、取消或断电；CON04去重 |
| reset-password | trust HBA guard、ALTER、停机、基线、写新文件；roundtrip12再启动 | 密码 argv F07；文件写失败/崩溃后认证一致性 T03 |
| 自定义账号/目录 | 配置项、CLI/脚本/target | F04/F05，默认postgres/同级target不能反证异地问题 |
| Windows | Win32 PID活性/TerminateProcess、DPAPI、GetVolumeInformationW | 不以 unsafe 报漏洞；F08/F09；ACL/共享/卷分类 R03/T03 |
| macOS | cfg(macos) statfs名字分类；Unix权限/信号 | 不套Linux magic；SDK既有债务未复报；没有macOS运行证据 |
| Linux及其余Unix分支 | cfg(all(unix,not(macos))) statfs magic；kill(0)/SIGQUIT/SIGKILL | 不把该cfg自动等同所有Unix均可编译；实际目标须隔离核验 |

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

以下均为快照既存相关（`preexisting_related`），引入时间 unknown；high 表示 EVID-01 的完整静态可达链，不表示运行复现。

### D16-F01 · setup 在 PGDATA 守卫前递归删除未证明归属的安装目录

- 类别：defect；严重度：P1；置信度：high。
- 位置：`setup.rs::ensure_binaries`、`run.rs::run`、`service.py::EmbeddedPostgresService.from_config`。
- 契约：不得销毁用户既有数据；initdb守卫应保护非空/已有PGDATA。自定义 install 路径不是删除任意目录的授权。
- 链路：`AppConfig.embedded_pg_install_root` 为无路径关系约束的 str → from_config直接取Path → run先ensure_binaries、后guard_data_dir → marker缺失/失配时对整个install_dir调用remove_dir_all。令install_dir=现有data_dir（或其祖先），删除发生在识别PG_VERSION前；普通无marker、含其他文件的自定义目录也被当半安装删除。
- 影响：单个错误配置即可删除已有集群或无关文件；不是仅“启动失败”。本轮没有执行删除。
- 反证：preflight只查可写/空间/FS/云路径/密码权限，没有目录相等或包含校验；维护锁位于runtime，不能保护被误指定为install的其他路径；marker hash保护完整性，不证明无marker目录属于sidecar。
- 修复：在Rust进入任何清理前规范化并校验data/install/runtime/password/log路径关系，拒绝install等于或包含PGDATA/runtime及不安全重解析别名；ensure_binaries遇到非空无所有权标记目录须拒绝，不全删；仅清理本程序可证明拥有的安装/临时目录。Python可提前提示，但Rust必须独立守卫。
- 保留：合法独立install、owned半安装自动重做、关键工具hash、非空PGDATA拒绝initdb保持。
- 回归：合成cluster令install=data、install为data祖先、普通非owned目录含sentinel → 非零且全文件hash不变；路径大小写/符号链接/Windows junction别名同样拒绝；合法owned半安装仍能重建。
- 验证：先加不启动PG的路径/所有权单测，再在隔离VM跑fi31与原有生命周期；本轮均未运行。
- 回退：保留原目录及配置，不提供自动删除“修复”；新检查误拒绝时返回明确路径冲突错误，不能回退至无条件remove_dir_all。

### D16-F02 · stop 的强杀目标仅依据 PID 数字，没有核对进程所属集群

- 类别：defect；严重度：P2；置信度：high。
- 位置：`pgbin.rs::read_postmaster_pid/process_alive/graded_stop/force_terminate`、`commands.rs::stop`。
- 契约：维护仅能停止本PGDATA拥有的实例，不应终止其他实例/程序；PID存在性不等于身份。
- 链路：维护脚本stop/CLI stop → 从postmaster.pid仅取PID/port（忽略目录/启动时刻） → process_alive为真 → graded_stop；pg_ctl smart/fast失败后，Unix直接对该数字SIGQUIT/SIGKILL，Windows经pg_ctl kill后直接OpenProcess(PROCESS_TERMINATE)/TerminateProcess。历史PID已复用为其他同用户进程时仍进入此路径。
- 影响：可能误杀不属于该集群的进程；status/doctor也可把复用PID误报成活PG。不是因为unsafe关键字，而是缺少身份比对的完整调用路径。
- 反证：fs2锁只排除sidecar/维护并发；旧PG已死时锁可释放，不能证明PID身份；pg_ctl失败恰会触发不再核验的兜底。Windows访问被拒时可能不杀成功，不能证明所有可访问同用户进程安全。
- 修复：为读出的PID构造可验证的集群身份（规范化PGDATA、postmaster启动时刻、OS进程创建时刻/可执行路径）；每次信号升级前再验证。无法证明归属则禁止原始PID强杀，返回STOP_FAILED并提示人工确认。
- 保留：确属本实例的smart→fast→kill、幂等未运行成功、真stale文件清理、其他实例维护锁不变。
- 回归：隔离VM中让pid文件指向受控非PG辅助进程及另一受控集群 → stop非零、两个进程均活；真实本集群可正常分级停止；验证检查与kill间身份变化时拒绝。
- 验证：Windows/Unix分别使用进程句柄/创建时刻证据，不能只mock process_alive或用固定“死PID”；本轮未启动辅助进程。
- 回退：身份API不可用时fail closed，保留PID文件和诊断；不退回不带归属验证的kill。

### D16-F03 · restore 把已释放的维护锁误当成原集群已经停止

- 类别：defect；严重度：P2；置信度：high。
- 位置：`maint.rs::restore/restore_into`，对照`run.rs::run`、`maint.rs::reset_password`。
- 契约：how-to §9.1/9.9要求完全退出；活实例必须先stop，恢复不能切换仍被使用的原数据目录。
- 链路：sidecar崩溃/被kill但postgres仍活（run自身明示该可达状态） → OS释放sidecar维护锁 → 离线restore成功acquire_lock → 没有读取或拒绝原PGDATA活PID → 在新目录恢复后进入原目录rename与切换。锁成功不代表孤儿PG停止。
- 影响：至少会对仍被使用的数据目录尝试切换；允许rename的平台可造成旧进程继续服务旧cluster、路径却指向新cluster，随后应用启动另一实例。Windows若文件共享阻止rename可能报错，不能据此宣称跨平台安全。
- 反证：run/reset_password有显式活PID拒绝，restore没有；roundtrip的维护拒绝只覆盖sidecar仍持锁，fi03同时杀PG，均未覆盖该分支。UI离线指引无法停止已经孤儿化的PG。
- 修复：restore获取锁后、任何setup/initdb/切换前验证源集群无活实例；有活实例返回LOCK_CONFLICT并指引stop，不自动杀；切换前再次核验。复用F02的归属校验，无法判定身份也拒绝切换。
- 保留：原目录损坏但无活实例仍可从dump恢复；不要求原cluster先成功启动；保留.bak及切换失败回滚。
- 回归：仅杀受控sidecar、不杀PG，再restore → exit50、原PGDATA/hash/path不变、无新restore目录；显式stop确认后恢复成功。另跑正常持锁拒绝及真正双死恢复。
- 验证：须强隔离VM/可销毁cluster，保留源/目标PID与路径归属证据；本轮未运行，不声称已发生数据损坏。
- 回退：拒绝路径仅诊断，不自动改PID或目录；保全已有.bak/.restore和恢复输入。

### D16-F04 · 维护脚本承诺的自定义用户名没有 Rust CLI 参数可接收

- 类别：defect；严重度：P2；置信度：high。
- 位置：`cli.rs::DumpArgs/RestoreArgs/DataDirArgs`、`maint.rs::DEFAULT_USERNAME/dump/restore_into/reset_password`、两份维护脚本、`EmbeddedPgMaintenanceService` argv构造。
- 契约：README“超级用户名”明确支持QTRADING_PG_USERNAME/前置--username；AppConfig和run均允许embedded_pg_username非postgres。
- 链路：用户按README用非postgres账号 → .bat/.sh将--username加到dump/restore/maintenance-shell →对应clap Args根本没有username → 参数解析退出2，未执行维护。绕过脚本的Python服务也不传用户名，Rust连接/initdb/reset固定postgres，自定义bootstrap用户的cluster无法按运行配置备份/重置。
- 影响：支持的自定义账号场景失去备份/恢复维护入口；默认postgres正常不反证该场景。
- 反证：RunArgs确有username，但其他Args没有继承；库名白名单和--database修复只处理数据库名，不能补用户名；未发现配置validator把该字段锁定为postgres。
- 修复：为维护Args增加保持缺省postgres的username，Python按同一配置快照传递；贯穿临时实例、initdb、dump/restore连接、maintenance信息及reset目标角色；角色名安全引用/校验，不能简单拼接ALTER角色标识符。修正脚本参数构造的word-splitting。
- 保留：旧命令不带username继续postgres；database白名单、锁、stdout协议和备份格式不变。
- 回归：自定义合法账号app_admin初始化 → .sh前置参数/.bat环境变量/服务dump均使用该账号；restore后同账号再次run；reset能重启；非法用户名不产生SQL/参数注入；默认账号旧测试保持。
- 验证：先CLI解析与argv单测，再Windows/Linux/macOS隔离维护往返；本轮未运行clap或shell。
- 回退：不改旧cluster用户名，不自动建第二个postgres角色来掩盖问题；失败前保留原凭据。

### D16-F05 · 显式异地恢复成功后没有目标布局的密码文件

- 类别：defect；严重度：P2；置信度：high。
- 位置：`maint.rs::restore/restore_into`、`paths.rs::Layout::from_data_dir`、`run.rs::run`、`EmbeddedPostgresService.from_config`。
- 契约：how-to §9.4与README称指定target成功后可将应用指向该目录。
- 链路：源A下data、目标B下data且父目录不同 → restore_layout显式继续使用A的password_file/install_dir → initdb/恢复成功但不写B/runtime/password → 按文档把embedded_pg_data_root改到B → from_config/run只寻找B/runtime/password → guard识别Existing且密码缺失 → exit16。
- 影响：已报告成功的异地恢复结果不能按所述方式直接启动；数据未被证明丢失，但用户被迫reset或手工迁凭据。相同父目录的target因共享runtime可避开，不能反证异地路径。
- 反证：run_initdb只在源密码旁写临时.pwinitdb并删除；恢复成功后既无密码迁移又无指向源password的持久化契约；现有restore_succeeds_with_target_data_dir只验PG_VERSION，未重启目标。
- 修复：明确目标应用布局；显式target若使用独立runtime，应先验证目标归属/获取目标锁，再用password::write_password_file按平台安全格式向新目标runtime原子写入同一密码，成功后再报告可用；已有无关凭据不得覆盖。若不支持任意布局，入口明确拒绝并给受支持路径，而非只报成功。
- 保留：不修改源凭据、不把密码输出stdout、不变默认原子切换/.bak策略；同级目标兼容。
- 回归：A/postgres/16/data恢复至B/postgres/16/data → B配置无源A依赖即可ready/SELECT1；Windows仍DPAPI、Unix0600；源A文件hash不变；目标已有不属本次凭据时拒绝。
- 验证：隔离实例执行显式target恢复后重新构造应用配置启动，不能仅断言PG_VERSION存在；本轮未运行。
- 回退：目标凭据交付失败不得切换/报告成功；保留源和可诊断的目标目录，不自动清除唯一恢复成果。

### D16-F06 · 在线并发 dump 共用一个 partial，Python 并发守卫又晚于 Popen

- 类别：defect；严重度：P2；置信度：high。
- 位置：`maint.rs::dump/run_pg_dump`、`EmbeddedPgMaintenanceService._run_sidecar`。
- 契约：partial→最终文件应保证成功交付的是完整备份，不被另一个未完成任务继续写入。
- 链路：两个CLI在线dump使用同一output →均不占维护锁 →均传相同<output>.partial给pg_dump →无输出级排他/唯一临时名 →一个rename成功时另一个仍可能持有同一文件写句柄。Unix路径下成功报告后的最终文件仍可被另一个writer改写，另一次rename/失败清理也相互干扰。Python本地并发拒绝在Popen之后才检查_active_proc，不能保证被拒子进程从未碰文件。
- 影响：备份制品的原子完成/归属保证失效，可能损坏或迟后变化；不把所有平台共享文件语义描述为相同。
- 反证：单个BackupRestoreViewModel有同步is_backing_up及exists守卫，已排除“同一VM双击必然损坏”的说法；它不约束CLI、多个进程/VM。已有service测试明确要求先Popen再kill第二进程，mock不能证明无副作用。
- 修复：先在Python锁内预留单任务状态，再Popen并在失败时释放；Rust为输出目标使用独立排他锁和唯一create_new临时路径，清理仅本任务临时文件；明确拒绝已存在最终备份，不无提示覆盖。在线dump仍不持PGDATA维护锁。
- 保留：在线一致性备份、正常取消CON04边界、单任务partial→最终rename和旧输出文件。
- 回归：两个受控在线dump同目标并发 →一方明确拒绝或序列化，成功文件hash在返回后不再变化；另一任务失败/取消不删对方partial；服务第二调用在Popen前拒绝；不同输出可按政策并行。
- 验证：先用可控writer/barrier测锁与清理，再隔离PG产生可核验行的备份；Windows共享失败应可诊断且不覆盖旧文件。本轮未运行。
- 回退：发生冲突保留已完成备份；不以删除共同.partial作为恢复措施。此根因为输出所有权，不重复计CON04的信号传播/孤儿清理。

### D16-F07 · reset-password 将新明文密码写进 psql 的进程参数

- 类别：defect；严重度：P2；置信度：high。
- 位置：`maint.rs::reset_password`、`pgbin.rs::psql/run_tool`。
- 契约：pgbin模块约定密码只走受控通道、不进argv；日志脱敏不能保护OS进程命令行。
- 链路：reset生成new_pwd →拼为ALTER USER postgres PASSWORD的SQL →pgbin::psql将整条SQL放入-c参数 →run_tool.cmd.args创建psql。新密码以明文出现在子进程argv；register_secret(new_pwd)还在命令完成/写文件之后。
- 影响：能采集该进程命令行的观察者/诊断制品可得到新凭据；不声称跨用户越权或已发生外泄，不依赖unsafe。
- 反证：PGPASSWORD此时为dummy，只用于trust连接，不承载新值；DPAPI仅保护持久化文件；tracing/日志sanitize无法移除已经进入argv的SQL。
- 修复：为密码更新增加仅经stdin传SQL的psql路径（-f -、piped stdin、同样ON_ERROR_STOP/超时/kill_on_drop），不把SQL内容进argv；先注册新secret，错误输出显式sanitize。普通健康检查SQL调用保持现状。
- 保留：无需旧密码、临时仅loopback trust、HbaRestoreGuard、成功恢复scram及密码格式保持；默认仍不打印新值。
- 回归：用合成哨兵密码截获实际子进程argv/env/stdout/stderr/日志 →argv及公开输出均无哨兵，受控stdin承载更新；成功后旧密码失效/新密码可连；失败仍恢复HBA。
- 验证：仅在强隔离环境捕获合成凭据，不收集真实命令行/密码；本轮只审读构造链。
- 回退：凭据更新中途失败按reset一致性恢复流程处理，不能恢复到把密码放argv的实现；保全原cluster及HBA。

### D16-F08 · Python 维护客户端未指定 UTF-8，非 UTF-8 locale 下误解码 Rust 输出

- 类别：defect；严重度：P2；置信度：high。
- 位置：`EmbeddedPgMaintenanceService._run_sidecar`，对照`EmbeddedPostgresService._start_sync_impl`和`version.py::_parse_pg_major_version`。
- 契约：Rust serde/eprintln输出UTF-8；机器协议客户端必须按同一编码读取，不由系统locale决定。
- 链路：UI备份/状态或服务维护调用 →Popen(text=True)却无encoding/errors →communicate按locale文本解码UTF-8 stdout/stderr →非UTF-8 Windows环境中中文错误/成功提示可能UnicodeDecodeError，或中文路径变乱码 →正常exit0无法形成正确结果，VM报告失败。
- 影响：常见本地化环境下doctor/dump/restore结果或目标路径不可用；dump已产出文件时UI仍失败。UTF-8模式/纯ASCII信息不触发，不能据其通过排除受支持的非UTF8环境。
- 反证：run客户端和version客户端均显式encoding=utf-8/errors=replace，维护服务没有；自写字符串“备份完成”的UTF-8字节用cp1252解码在第11字节报UnicodeDecodeError，cp936产生乱码。不是实跑项目/OS。
- 修复：维护Popen统一encoding="utf-8"、明确errors策略（诊断replace；机器JSON损坏仍拒绝），保留原始退出码的分类路径；脚本验证入口同类Popen一并核对。
- 保留：ThreadPoolManager offload、communicate超时回收、取消句柄注册和stdout/stderr分流不变。
- 回归：合成UTF-8中文路径及“备份完成”stderr，在cp1252/cp936 locale且禁用UTF8模式的隔离Python子进程消费 →无解码异常、路径逐字一致；非法UTF8明确处理而非错连目录。
- 验证：先独立subprocess假sidecar字节协议测试，再隔离Windows真实dump/doctor；本轮仅运行标准库字符串反例。
- 回退：诊断不可解码时明确失败并保留已有备份，不能删除或把乱码路径作为恢复成功目录。

### D16-F09 · 四客户端验证脚本把 Windows DPAPI 文件内容直接当数据库密码

- 类别：defect；严重度：P3；置信度：high。
- 位置：`scripts\verify_embedded_pg_connect.py::main`、`password.rs::write_password_file`。
- 契约：该脚本宣称验证四客户端、Alembic及dump/restore；必须消费当前Windows密码格式，不能把验证工具不兼容误当PG不可用。
- 链路：脚本启动Rust run并指定password_file →Windows写PGPW2密文 →脚本read_text/strip后直接拼URL →所有客户端使用密文串而非真实密码认证，后续migration/roundtrip亦失败。
- 影响：Windows该验证入口产生系统性假失败，无法作为恢复可信度证据；不推断其他集成测试也有同样错误。
- 反证：Rust与生产service均已有DPAPI解密路径；脚本没有前缀判断/解密。明文Unix场景正常不反证Windows分支。
- 修复：main复用现有data层_read_embedded_password解码逻辑，解密后再注册secret/构造URL，不新增另一套DPAPI实现；同时按F08指定Popen编码。不得把密文文件改成明文规避。
- 保留：显式work-dir、四驱动验证、Alembic attributes目标注入、失败脱敏与退出码维持。
- 回归：mock DPAPI读取交付合成密码 →四驱动URL使用解密结果且输出无哨兵；隔离Windows实跑脚本完成四SELECT1/迁移/roundtrip；Unix明文分支继续可用。
- 验证：先补现有scripts单测，再在经审核的可销毁工作目录运行CLI；本轮未运行，不能引用脚本历史成功证明当前Windows有效。
- 回退：不能解密则在连接前明确报格式/账号环境错误，保留证据不修改密码文件。

### D16-F10 · Python 维护错误映射与实际 Rust 退出码语义不一致

- 类别：defect；严重度：P3；置信度：high。
- 位置：`EmbeddedPgMaintenanceService._EXIT_CODE_MAP/_raise_for_exit_code/doctor`、`EmbeddedPostgresService._format_sidecar_exit_error`、`exit_codes.rs`。
- 契约：how-to §9.6与exit_codes.rs为退出码正本；错误类型/用户行动不得把setup失败说成参数错误、把所有preflight说成磁盘满。
- 链路：维护dump/restore的ensure_binaries返回10 →Python映射sidecar_arg_error/“维护命令参数错误”；run遇权限/文件系统/密码权限preflight返回15 →_format_sidecar_exit_error硬写disk full/<500MB。20在Rust是STOP_FAILED，服务却标pg_not_running并允许doctor视为成功。
- 影响：可达错误给出错误处置方向、掩盖真正故障分类；16/30等未映射则只有unknown_error。原始exit及脱敏stderr仍在，限制了影响范围。
- 反证：当前doctor正常仅0/2，因此“20容忍”不是当前必触发健康误判；本项确定影响主要为可达10/15错误提示，未把兼容分支夸大成恢复数据损坏。
- 修复：按常量逐项修正2/10/13/14/15/16/20/30/40/50/60映射；15使用资源预检失败通用提示并引用脱敏原因，不编造磁盘量；移除未有版本契约支持的doctor20成功容忍，同步错误码说明。
- 保留：非零实际失败仍抛业务异常，raw exit/脱敏stderr保留，doctor exit0仍需解析issues，stop未运行仍0。
- 回归：参数化各常量输入→类型/提示对齐；10不出现参数错误、权限导致15不声称磁盘满、20不作“未运行成功”；0+issues.error仍是诊断结果非健康证书。
- 验证：扩展现有维护service和failure-injection单测，再隔离故障真实返回码核对；本轮只静态比对常量与字典。
- 回退：未知码保持非零失败，不用容忍未知/20或丢stderr来制造通过。

### 续审独立接受的发现（当前）

本维度没有新增接受项；不以问题配额制造缺陷。

## 5. 待确认待验证及测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D16-T01 · 恢复回归没有证明业务内容与真实中断后的可恢复性

- 类别：test_gap；严重度：P2；置信度：high。
- 位置：`tests\roundtrip.rs::test_dump_restore_roundtrip`、`tests\failure_injection.rs::test_inject_28_restore_interruption_residual/test_inject_28b_dump_partial_residual`。
- 证据：roundtrip6验exit/目标PG_VERSION/bak存在，未重新启动恢复库验证行/约束/迁移版本；28/28b明确手工创建残留，不执行kill-mid-restore/dump。fi03额外杀PG，不能证明孤儿维护安全。
- 建议：往返前写合成带NULL/Unicode/大值/外键的数据与版本标识；恢复后读回逐项比对，.bak可独立回退；用受控同步点覆盖initdb/restore/健康检查/两次rename之间故障，断言源/目标状态与准确返回码。
- 验证：由3.2后续隔离验证承接；动态0，现有通过历史不明。checksum场景25已有债务，**不在这里另计新缺陷**。

### D16-T02 · 关键 doctor 单测只断言不报错，不能验证问题诊断

- 类别：test_gap；严重度：P2；置信度：high。
- 位置：`maint.rs::tests`、`commands.rs::tests`、`scripts\check_doctor_schema.py`。
- 证据：多项doctor missing/corrupt/state/perms测试只assert doctor.is_ok；`wait_for_enter_handles_eof_gracefully`丢弃timeout结果，超时也被接受。集成测试确有部分issues断言，不能据此说所有doctor均无测试。
- 建议：抽出DoctorJson组装或捕获stdout，断言code/severity/unknown值及无错误健康结论；EOF测试显式关闭独立stdin并要求时限内完成。对Q01明确DTO契约后，用真实源码字段集合加回归，不只使用合成schema。
- 验证：现有Cargo --bins和Python测试入口需强隔离；本轮仅审读断言，不执行项目schema脚本。

### D16-T03 · 凭据、备份隐私及 OS 持久化边界没有当前运行证据

- 类别：test_gap；严重度：P2；置信度：high。
- 位置：`password.rs::write_password_file`、`setup.rs::run_initdb`、`maint.rs::reset_password/dump/restore`、`state.rs::write`。
- 证据：Unix新建mode0600及重写后chmod、WindowsDPAPI路径存在；`.pwinitdb`明文文件正常删除，但未覆盖kill/断电及chmod/write失败；重置先ALTER再写密码文件，文件写失败时能否再次恢复须实测。备份由外部pg_dump写，实际文件ACL/继承权限未观察，不能仅凭PGDMP格式认定加密/私密。
- 建议：合成凭据在隔离Windows/Unix检查创建时及失败后权限/残留，仅记录模式/ACL摘要不记录值；各写入/rename失败与断电重启后验证原凭据、新凭据及恢复流程。对未加密备份复用既有F19，不重复报新缺陷。
- 验证：在批准的可销毁VM与独立账号执行，凭据值不入报告；当前不得声称Windows ACL安全、断电原子或恢复可靠。

### D16-R01 · 临时实例 stop 失败后仍可能报告维护成功，crate Drop 补偿未知

- 类别：risk；严重度：P2；置信度：medium。
- 位置：`maint.rs::TempInstance::stop/restore_into/maintenance_shell`、`pgbin.rs::graded_stop`。
- 证据：stop只打印warning、返回()；调用方继续成功/切换。代码注释称PostgreSQL Drop会补偿停止，但本轮未验证锁定crate源码及其失败语义，故不认定“必然切换活临时库”。smart/fast工具timeout包含额外10s，不能仅凭注释承诺总预算≤32s。
- 建议：故障注入强制smart/fast/kill失败，记录真实PG存活/句柄/退出预算；若Drop不能保证停止，将StopOutcome传播为硬失败并禁止切换，保留目录供恢复。
- 验证：3.2隔离测试；本轮不运行或信赖crate注释补偿。

### D16-R02 · start 取消与仍运行的 to_thread 工作线程之间存在待验证时序

- 类别：risk；严重度：P2；置信度：medium。
- 位置：`EmbeddedPostgresService.start/_start_sync_impl/_cleanup_failed_start`、`app\bootstrap.py::prepare_database_runtime`。
- 证据：取消时同步_cleanup_failed_start未与工作线程握手；若取消先于线程赋_process，cleanup看到None，线程随后可能继续Popen，parent仍存活不会触发parent_gone；atexit/后续shutdown有补偿机会。应用是否必走清理与时序结果未实证，不升级确定泄漏。
- 建议：以barrier覆盖“已调度未Popen/已Popen未ready/已ready”取消，断言取消返回后无残留_owned_进程/reader/锁；必要时引入每次start的取消令牌与完成清理握手，不在事件循环上同步等待长清理。
- 验证：补充实际工作线程竞态测试，而不只mock to_thread立即抛CancelledError。与CON04长期维护命令取消不同，本项仅列启动时序风险。

### D16-R03 · Windows 文件系统名不足以确认网络卷/重解析路径受支持

- 类别：risk；严重度：P2；置信度：medium。
- 位置：`preflight.rs::detect_fs_kind` Windows cfg分支。
- 证据：GetVolumeInformationW返回名被映射NTFS/ReFS或包含SMB/NFS/CSC，未见独立卷类型/重解析归属核验；UNC/映射盘具体返回、失败时Unknown放行的实际效果未知。没有调用WinAPI验证，不能断言所有网络盘必然绕过或出现WAL损坏。
- 建议：在隔离Windows用受控UNC/映射卷/junction/普通NTFS做输入矩阵，记录最终规范化路径/卷类型/分类结果；确认后再决定补卷类型检查及Unknown的拒绝/告警策略。
- 验证：需要独立平台环境及授权，本轮不联网或挂载卷。

### D16-Q01 · doctor DTO 子集与严格字段一致性脚本的预期冲突待裁决

- 类别：question；严重度：null；置信度：high。
- 位置：`maint.rs::DoctorJson`、`EmbeddedPgMaintenanceService.DoctorResult`、`scripts\check_doctor_schema.py::check_doctor_schema_consistency`。
- 证据：自写标准库静态提取实际字段为Rust33/Python18，Rust-only15：cloud_sync_feature、cluster_state、control_data_readable、data_checksums、free_bytes、fs_kind、fs_supported、hba_matches_template、kill_fallback_count、last_stop_mode、managed_block_drift、password_file_perms_ok、password_file_present、postmaster_pid、stale_postmaster_pid。脚本要求字段集合相等，DTO注释允许缺省子集；未确认当前CI接入该脚本，不宣称CI已红。
- 建议：明确DTO是否完整镜像；若完整则补字段/验证/消费单测，若有意子集则脚本改成显式必需字段+允许忽略表，并确保issues继续承载故障，不默默忽略新增关键字段。
- 验证：经裁决后在隔离环境运行C-G1及真实源码集成断言；本轮字段提取不算项目脚本执行。

### 续审暂缓/驳回/重复裁决（不计确定问题）

## 6. 改进建议及方案

### D16-S01 · 给运行期故障输出增加有界、可消费的诊断通道

- 类别：suggestion；严重度：null；置信度：high。
- 位置：`EmbeddedPostgresService._start_stdout_reader_thread/_setup_service_logger`、`logging.rs::SinkInner::write_bytes`。
- 建议：将已存在disk_low/exit JSON解码为有界事件，保留未知schema原文诊断但避免静默丢关键退出原因；sidecar.stdout/stderr/service三份append日志采用容量/轮转策略，文件写失败保留可见的替代诊断。当前Rust sidecar.log已有10MB×5轮转，不应重复造一套或声称全部日志无上限。
- 收益/代价：故障及时可见、避免长期磁盘压力；需要明确事件到VM的线程桥接与背压。前提是产品批准告警消费，不能把stdout事件存在视为UI已告警。
- 验证：合成warning/exit/未知schema及不可写日志目标→不阻塞sidecar、事件顺序可追踪、脱敏正确；不重复既有checksum监控技术债。

修复组织建议（非实施授权）：先F01/F02/F03路径和进程归属，再F06备份产物隔离；F04/F05维护配置与目标凭据；F07/F08密码/编码通道；最后F09/F10验证与诊断。每步保留原功能矩阵并独立回归，不合成大重构。涉及危险目录/进程操作的修复不得直接在当前无Git共享环境验证。

## 7. 反证与去重记录

| 候选/历史事项 | 裁决与实际证据 |
| --- | --- |
| “维护命令无锁、重复run可双启动” | 驳回泛化：fs2锁贯穿run/离线维护；存在跨进程锁冲突测试。F03仅指锁所有者死亡后PG仍活 |
| “doctor exit0表示健康” | 驳回：Rust always JSON issues，how-to清楚区分；T02只说部分断言未验证issues |
| “维护stdout被日志污染/restore应是JSON” | 驳回：日志/交互走stderr；restore有意单行路径，Python消费一致；version纯文本是显式非JSON模式 |
| “maintenance_shell服务返回后能连接活实例” | 驳回承诺：how-to已明确communicate后的实例已停；不是本轮新缺陷 |
| “UI恢复直接在运行中换PGDATA” | 驳回：confirm_restore仅离线指引，不调用svc.restore；F03的caller是离线CLI/API |
| “magicheader缺失已全修/仍完全没有” | 两种泛化均驳回：UI已有8字节预检/元信息/拒绝单测；CLI只存在性检查但pg_restore失败不会正常切换。引用P3-REV06-Restore-HeaderValidation，债务状态需维护者按UI/CLI范围澄清，不重复记缺陷 |
| “任意数据库名SQL注入” | 驳回：Python/Rust各有ASCII安全标识符白名单，restore CREATE DATABASE双引号；F07是密码传输通道，非库名注入 |
| “Windows密码一直明文/Unix重写不收权限” | 驳回：PGPW2 DPAPI；Unix既有文件重写后chmod0600。初始写入/崩溃窗口需T03，不据字面明文代码判断已外泄 |
| “unsafe意味着可利用漏洞” | 驳回：查看了具体handle关闭、buffer所有权及cfg；F02只基于错误PID归属路径 |
| P3-CON04-Subprocess-Cancel | 不新增：terminate仅wrapper、孤儿pg_dump、残留partial、restore拒绝取消、ThreadPool不可中断均既有。F06限定另一个根因：并发输出同名partial及spawn前未预留 |
| P2-Fi25-Rust-Injection-Missing | 不新增：checksum/页损坏日志监测与UI链未落地，沿用债务；不能把fi23控制文件损坏当成页checksum覆盖 |
| P3-Rust-Coverage-D38 | 不复报历史覆盖率；本轮未运行llvm-cov，不沿用旧百分比作为当前测量 |
| P2-REV06-Backup-Encryption | 不新增：pg_dump custom format未加密；文档有勿放云盘/共享位置提示，不宣称全部UI提示已核查 |
| 版本缓存、macOS SDK、脚本sys.path | 已登记P3-PR513-VersionCache-No-Prod-Invalidation、P3-Sidecar-macOS26-SDK-Constraint、P3-PR513-Script-SysPath-Inject，未重复计缺陷 |
| EX-0011/EX-0012 | shutdown懒导入架构例外，不据其报反向依赖；不是目录删除/PID/恢复安全豁免 |
| D03-F01–F05 | 宏观limit、URL表单编码、日期转换、学习样本口径、复盘重放根因均不同；本报告不复制D03发现 |

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已进行：仅静态、标准库、无项目执行

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| CSV筛选D16、读取允许的31个源文件计算SHA-256、splitlines | 31路径、10,788行；31/31冻结hash一致；命令exit0 | 内容hash不等于语义阅读；正文另已逐段读EOF |
| 冻结snapshot元数据对照 | inventory标识与brief一致，protected条目不读取 | 没有重新读取全库/受保护内容重建总快照 |
| 自写AST/regex字段对照 | Rust33、Python18、差15，exit0 | 没有导入或运行check_doctor_schema.py；不是源码全读证明 |
| 自写UTF-8字符串编码反例 | cp1252 UnicodeDecodeError[11:12]；cp936乱码，exit0 | 不是当前locale测量、不是Windows/PG动态验证 |
| 产物闭合核对 | 交付前检查两文件、九章/ID/主责集合/hash/计数/行段一致 | 仅检查自己报告；最终结果登记于evidence |

执行环境仅使用本机PowerShell承载标准库Python文本/哈希处理；未探测/激活项目环境。项目用例数0，项目skip/xfail数“不适用，未启动runner”；项目检查无退出码，不写“全部通过”。

### 未运行与隔离验证入口

- C-R1–R5均未运行：`cargo fmt --check`、clippy、`cargo test --target x86_64-pc-windows-msvc --bins`、`--test roundtrip -- --test-threads=1`、`--test failure_injection -- --test-threads=1`。来源validation-matrix；Cargo可能执行build.rs/联网/启动PG，当前无许可和强隔离。
- C-G1/C-G2未运行：`python scripts\check_doctor_schema.py`；`python scripts\verify_embedded_pg_connect.py --sidecar <批准的隔离二进制> --work-dir <隔离副本内新目录>`。F09修复前不能把Windows脚本失败当真实PG结论。
- 未来最小Python选择器（路径均存在）：`python -m pytest -n 0 tests\unit\test_embedded_postgres_service.py tests\unit\test_embedded_postgres_service_failure_injection.py tests\unit\test_embedded_postgres_version.py tests\unit\services\test_embedded_pg_maintenance_service.py tests\unit\scripts\test_verify_embedded_pg_connect.py tests\unit\ui\viewmodels\test_backup_restore_view_model.py`。本轮没有执行；conftest会导入服务，不因mock/collect-only而获准。
- Linux/macOS使用匹配且经审核的工具链/归档，在隔离副本运行现存roundtrip/failure_injection目标；不复制Windows target参数冒充跨平台验证，具体target由制品基线确定。
- 后续隔离必须包含：无宿主用户目录挂载、空配置/独立账号、可销毁DB目录/备份目标、最小权限、禁止外发真实数据；只记录受控PID/端口/路径相对名/ACL摘要及制品hash。故障注入后核对进程树、所有权与残留；清理只限本轮创造且已证明归属的对象。回退依赖原cluster/.bak与受控凭据，不能只重跑“成功”覆盖失败痕迹。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

源码静态阅读闭合，**动态、故障注入、真实PG、三平台与独立复核全部未完成**。不能证明页/约束/迁移状态完整、断电rename持久化、Windows文件共享/ACL、DPAPI跨账号行为或crate Drop补偿。G没有达到，task仍blocked；P1发布阻断与G证据不足同时成立。

下一步由Lead核验本报告与清单/hash、独立反证F01–F10并整合索引；本执行者不改Plans/scope/index、不派生子代理。若决定修复，另行明确路径/生命周期行为契约和实施授权，先失败回归，再最小补丁，再批准的隔离矩阵，最后重新判门禁。

CHECK-01目标/基线明确；02以冻结清单替代不可用Git差异、未伪造历史；03安全边界执行；04调用/状态/权限模型已建；05覆盖→深挖→反证完成静态轮次；06成功/异常/并发/取消/恢复均建模、动态空缺明列；07测试/运维遗漏T/R/Q已列；08 project/cli-iac Profile已读；09跨层caller按行段追踪；10每缺陷有路径/触发/影响；11分类/分级/变化关系分开；12技术债/反证去重；13运行0与未运行清楚；14残余风险与G blocked明确；15无越权副作用。独立review仍pending，不把自查当独立批准。

逐文件交付改动清单：仅 `reviews\20261101-full-audit\D16-sidecar-pg.md` 与 `reviews\20261101-full-audit\D16-evidence.json`。