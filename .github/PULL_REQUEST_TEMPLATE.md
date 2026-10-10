## 🎯 PR 概述
<!-- 一句话说明本次修改目标、解决什么问题/实现什么功能 -->

## 🔗 关联工单/Issue
Closes #XXX
Relates to #XXX
<!-- 关闭工单用Closes，关联参考用Relates -->

## 📌 修改类型（删除无关项）
- [ ] feat：新增业务功能
- [ ] fix：线上/测试Bug修复（P0/P1/P2分级标注）
- [ ] refactor：代码重构、架构调整、逻辑优化（无功能变更）
- [ ] docs：README/CONTRIBUTING/CLAUDE/SECURITY/CHANGELOG文档更新
- [ ] test：单元/集成/E2E测试新增、修复、优化
- [ ] perf：性能、并发、数据库查询优化
- [ ] ci：GitHub Actions、pre-commit、流水线调整
- [ ] db：数据表迁移、字段/索引、约束变更（需附alembic脚本）
- [ ] chore：依赖升级、配置调整、清理死代码

## 📝 详细变更清单
<!-- 逐条列出文件、模块、核心改动，区分产品代码/测试/文档 -->
- 模块：xxx，文件：xxx.py，改动：
- 文档：xxx.md，同步更新内容：
- 数据库：alembic版本：xxx，变更说明：

## 🧪 测试覆盖（按适用范围勾选，不要求全部勾选）
<!-- 填写规则（对齐 CONTRIBUTING.md「变更类型 → 最小验证子集」的按变更范围裁剪原则，适用于本模板全部勾选清单节）：
① 适用且已验证：勾选 [x]，行内注明验证方式（本地命令或 CI job 名）；
② 不适用：保持 [ ]，行内标注「N/A：<原因>」；
③ 适用但未执行：保持 [ ]，行内标注「未执行：<原因>」并附补救计划或风险声明。
禁止将不适用或未执行项勾选为通过；CI 实际触发条件以 .github/workflows/ci_cd.yml 为准。-->
### 自动化测试
- [ ] 单元测试：新增/更新用例，覆盖率达标
- [ ] 集成测试：DAO/Service层链路验证
- [ ] E2E（`tests/e2e/`，Flet + pytest；本地可用 `run_e2e_local.py`）：UI流程验证，无超时/断言失败
- [ ] 静态检查：ruff + pyright 无报错
- [ ] CI 流水线（PR 触发 job：lint-fast / ci-checks / ci-checks-windows / e2e-tests-windows / embedded-tests / requirements-drift 全部通过；installer-smoke 仅 installer.iss 或 scripts/*installer* 变更时执行 ISCC；build-windows 仅在 tag 推送（`v*.*.*`）或手动触发时运行，PR 阶段不存在 build job）

### 手动验证
<!-- 下列各项同样按三态规则填写：与本 PR 变更范围无关的项标「N/A：<原因>」，不删行，便于评审逐条核对 -->
- [ ] 本地完整启动项目验证正向流程
- [ ] 边界场景、异常分支、并发场景验证
- [ ] 数据库迁移回滚验证（如有DDL变更）
- [ ] UI变更附前后截图/GIF（无UI变更标 N/A）

<!-- 四类填写实例（勾选组合与 .github/workflows/ci_cd.yml 实际 job 触发条件一致）：
① 纯文档 PR（仅 docs/**、*.md 等 Markdown 类路径，均不在 pull_request paths 白名单 → 主 CI workflow 不触发，0 个 job 运行）：
   单元测试 N/A（无 Python 变更；最小子集为 scripts/check_docs_consistency.py + tests/unit/test_docs_consistency.py，本地运行）
   集成测试 / E2E / CI 流水线 N/A（主 CI 不触发；E2E 针对 UI 流程）· 静态检查 N/A（ruff/pyright 不覆盖 Markdown）
   手动验证各项 N/A；自检清单中 ruff/pyright 行写「未执行：纯文档改动按最小验证子集不要求」
② DAO PR（data/ DAO/模型，含 alembic 迁移；**.py 与 alembic/** 均在白名单 → 主 CI 全套触发）：
   单元测试 [x]（CI ci-checks）· 集成测试 [x]（CI ci-checks「Run Integration Tests」）· E2E [x]（CI e2e-tests-windows）
   静态检查 [x]（lint-fast + ci-checks Pyright）· CI 流水线 [x]（PR 触发 job 全绿；build 不适用于 PR 阶段）
   手动验证：数据库迁移回滚 [x]（CI「Verify Alembic Migrations」含 downgrade base → upgrade head 回归；本地 alembic upgrade head + alembic check）；本地完整启动/边界场景按影响面；UI 截图 N/A
③ UI PR（ui/ 模块；**.py 在白名单 → 主 CI 全套触发）：
   单元测试 [x]（tests/unit/ui/）· 集成测试 N/A（无 DAO/Service 链路变更；CI ci-checks 中集成测试照常执行并作为门禁，非本 PR 验证目标）
   E2E [x]（CI e2e-tests-windows，UI 流程为其核心验证目标）· 静态检查 [x] · CI 流水线 [x]（build 不适用于 PR 阶段）
   手动验证：本地完整启动 [x]（正向流程）· UI 前后截图 [x] · 迁移回滚 N/A（无 DDL 变更）
④ 普通代码 PR（strategies/services 等 .py；**.py 在白名单 → 主 CI 全套触发）：
   单元测试 [x]（对应策略/服务用例）· 集成测试 N/A（未触及 DAO/Service 持久层；若触及改按②组合填写）· E2E [x]（CI e2e-tests-windows，PR 门禁必跑）
   静态检查 [x] · CI 流水线 [x]（build 不适用于 PR 阶段）
   手动验证：边界场景 [x]（单测覆盖）；本地完整启动/迁移回滚/UI 截图按影响面标 N/A
混合改动（文档 + 代码）按代码侧适用组合填写；任何「适用但未执行」的项写「未执行：<原因>」，不勾选。-->

## ⚠️ 风险与兼容说明
1. 是否存在**破坏性变更**（旧接口/旧数据不兼容）：
2. 是否需要上线前执行数据库迁移脚本：
3. 是否影响定时调度、后台异步任务、自托管Runner：
4. 安全相关变更：密钥/加密/输入校验/LLM权限调整说明：

## 📖 文档同步校验（适配文档检视规范）
- [ ] README.md 同步更新功能/URL/配置/版本
- [ ] CLAUDE.md 架构、单例、策略规范同步对齐
- [ ] CONTRIBUTING.md 流程、安装命令、测试步骤同步
- [ ] SECURITY.md 支持版本、依赖CVE更新
- [ ] docs/flet/ 子文档与 man/ 其他专题按 [docs/flet/README.md](../docs/flet/README.md) 任务路由同步（Owner/复核触发器见文档头部；man/flet-best-practices.md 已改为 stub 指向 docs/flet/）
- [ ] CHANGELOG.md 由release-please自动生成，无手动修改

## ✅ 提交前自检清单（强制全部核对）
<!-- 「核对」= 逐条表态，同「测试覆盖」节三态规则：适用且已达成勾选 [x]；不适用行内标注「N/A：<原因>」；未达成写「未执行：<原因>」；禁止空置不表态 -->
- [ ] 代码符合 [CLAUDE.md 架构红线 R1-R24](../CLAUDE.md#31--绝对禁止)、分层规范、单例约束
- [ ] 无硬编码密钥、数据库密码、Token、隐私信息
- [ ] 无废弃死代码、注释掉的临时调试代码
- [ ] 所有新增分支、边界逻辑配套对应测试用例
- [ ] 国际化文案统一使用I18n，无中文硬编码
- [ ] 并发逻辑增加锁/信号量，无pd.options全局竞态污染
- [ ] 数据库新增字段补充索引、非空、默认约束
- [ ] 已确认避免重复造轮子，优先复用了工程已有代码及成熟开源库
- [ ] 本次修改引入的新问题已全部自查修复
- [ ] Ruff lint + format 已通过（`ruff check .` + `ruff format --check .`）
- [ ] Pyright 类型检查已通过（`pyright`）
- [ ] 测试断言避免弱断言（`assert x` / `assertTrue`），使用精确断言（`assertEqual` / `assert x == y`）
- [ ] 所有 `# type: ignore` 均带 `[error-code]` 注释（R3 红线）
- [ ] 所有 `# NOTE(lazy):` 标记含三要素（简化内容 / ceiling / upgrade）

## 👀 评审重点提示
<!-- 告诉评审人重点检查模块、风险点、容易忽略的链路 -->

## 补充备注
