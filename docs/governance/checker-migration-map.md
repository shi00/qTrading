# 42 项文档检查迁移映射表（D5）

> **正本声明**：本表为检视 §6.3「42 项原检查以 docs-full-result-checker.json 的函数身份为核对底稿」的落地文档。
> **底稿 JSON（`reviews/docs-full-result-checker.json`）缺失未恢复，本表逐项人工核对现行代码填写（口径：`scripts/check_docs_consistency.py` 的 `_SCOPE_CHECK_NAMES` 计划表 + 各检查函数体 + 测试实引用行号核对），不依赖底稿统计。**
>
> - 「旧函数名」在兼容期全部保留原名与原签名（检视 §5.1「不拆成几十个小检查文件」）；删除须经后续工作包显式登记。
> - 「处置」值域：
>   - **保留** —— 检查逻辑与解析路径保留（含仅修 F17 必需输入显式失败、受检集 git 跟踪基线的修正，不含解析路径变更）；
>   - **共享解析替代** —— 解析路径已改走 `scripts/docs_markdown.py` 统一视图（标题/锚点/链接/HTML ID/代码块边界），不再手写行级正则再造 Markdown；
>   - **保留 + 迁移出口** —— 语义已由 D4 受控生成协议（`scripts/docs_generated.py` BUILTIN_REGISTRY 激活条目）承载双保险，D6/T6 切换注册表后旧表达退役（退役前不得删）。
> - 「执行入口」格式 `scope（all 含并集）/ job`；job 现状为 pre-commit `docs-consistency` hook + CI `Docs Consistency Check`（docs-ci.yml，required check；D9/T9 迁移 CI 结构时本列同步更新）。
> - F17 必需输入缺失负例逐项见 `tests/docs/test_required_inputs.py`（数据驱动 43 项，承接表完整性由 `test_all_scope_checks_have_required_input_negative` 守护）——下表「对应测试」不再逐行重复该文件。

## document scope（正式文档结构 / 路径锚点 / 目录索引 / 生成输出）

| # | 检查函数 | 处置 | 输入（必需源） | 对应测试 | 执行入口 |
|---|---------|------|---------------|---------|---------|
| 1 | `check_anchor_dead_links` | 共享解析替代 | `CHECKED_DOCS` 受检集（git ls-files 基线） | `tests/unit/test_docs_consistency.py::TestTrackedDocsLinksResolve`、`TestDocsConsistencyScript`；`tests/docs/test_parse_migration.py` | document / docs-consistency hook + docs-ci |
| 2 | `check_relative_dead_links` | 共享解析替代 | `CHECKED_DOCS` 受检集（git ls-files 基线） | `TestTrackedDocsLinksResolve`；`tests/docs/test_link_checks_migration.py` | document / docs-consistency hook + docs-ci |
| 3 | `check_flet_hub_completeness` | 共享解析替代 | `docs/flet/README.md`（hub）+ `docs/flet/**` | `TestFletHubCompleteness` | document / docs-consistency hook + docs-ci |
| 4 | `check_agents_md_sync` | 保留 + 迁移出口 | `AGENTS.md`（redlines-invariant 块）+ `docs/governance/redlines.yml` | `TestAgentsMdSync`；注册条目一致性见 `check_docs_generated`（#43） | document / docs-consistency hook + docs-ci |
| 5 | `check_agents_md_min_verify_commands` | 保留 + 迁移出口 | `AGENTS.md`（min-verify-commands 块）+ `CONTRIBUTING.md`（命令作者块） | `TestAgentsMinVerifyCommands`（含 F20 修法反例 + 块外正文通过对照） | document / docs-consistency hook + docs-ci |
| 6 | `check_claude_executive_sync` | 保留 + 迁移出口 | `CLAUDE.md`（claude-executive 块）+ `docs/governance/redlines.yml` | `TestClaudeExecutiveSync`（含 F20 修法反例 + 块外正文通过对照） | document / docs-consistency hook + docs-ci |
| 7 | `check_claude_self_check_sync` | 保留 + 迁移出口 | `CLAUDE.md`（redlines-self-check 块）+ `docs/governance/redlines.yml` | `TestClaudeSelfCheckSync` | document / docs-consistency hook + docs-ci |
| 8 | `check_canonical_completion_criteria` | 保留 | `docs/governance/canonical-topics.yml` | `TestCanonicalCompletionCriteria` | document / docs-consistency hook + docs-ci |
| 9 | `check_canonical_docs_are_gated` | 保留 | `docs/governance/canonical-topics.yml` | `TestCanonicalDocsAreGated` | document / docs-consistency hook + docs-ci |
| 10 | `check_docs_index_completeness` | 共享解析替代 | `docs/README.md` + `CONTRIBUTING.md` 索引 + `docs/**`（候选集 git ls-files，开放问题 4 修正） | `TestDocsIndexCompleteness`；gitignored 不误报见 `tests/docs/test_index_gitignore.py` | document / docs-consistency hook + docs-ci |
| 11 | `check_reviews_index_completeness` | 共享解析替代 | `reviews/README.md` + `reviews/**`（候选集 git ls-files） | `TestReviewsIndexCompleteness`；`tests/docs/test_index_gitignore.py` | document / docs-consistency hook + docs-ci |
| 12 | `check_adr_index_completeness` | 共享解析替代 | `CONTRIBUTING.md` 索引 + `docs/adr/**` | `TestAdrIndexCompleteness` | document / docs-consistency hook + docs-ci |
| 13 | `check_scripts_index_completeness` | 共享解析替代 | `docs/guides/ci-cd.md` 工程脚本清单 + `scripts/*.py` | `TestScriptsIndexCompleteness` | document / docs-consistency hook + docs-ci |
| 14 | `check_guillemet_references` | 共享解析替代 | `CHECKED_DOCS` 受检集（代码块边界经统一视图剔除） | `TestGuillemetReferences` | document / docs-consistency hook + docs-ci |
| 15 | `check_adr_supersede_chain` | 保留（F17 显式失败） | `docs/adr/**`（目录缺失显式失败） | `TestAdrSupersedeChain` | document / docs-consistency hook + docs-ci |
| 16 | `check_evals_case_index_consistency` | 保留 | evals README 索引 | `TestEvalsCaseIndexConsistency` | document / docs-consistency hook + docs-ci |

## governance scope（作者源 / 例外 / 路由 / 配置映射）

| # | 检查函数 | 处置 | 输入（必需源） | 对应测试 | 执行入口 |
|---|---------|------|---------------|---------|---------|
| 17 | `check_precommit_hook_count` | 保留 | `.pre-commit-config.yaml` | `TestPrecommitHookAndWorkflowEnum` | governance / docs-consistency hook + docs-ci |
| 18 | `check_precommit_hook_names` | 保留 | `.pre-commit-config.yaml` + `docs/guides/ci-cd.md` hook 清单 | `TestPrecommitHookAndWorkflowEnum` | governance / docs-consistency hook + docs-ci |
| 19 | `check_workflow_enum` | 保留（F21 rename 新旧路径并集扩展） | `docs/guides/ci-cd.md` + `.github/workflows/**` | `TestPrecommitHookAndWorkflowEnum`；rename 幽灵负例见 `tests/docs/test_enforcement_triggers.py::TestWorkflowEnumRenameGhost` | governance / docs-consistency hook + docs-ci |
| 20 | `check_flet_version_drift` | 共享解析替代 | `FLET_VERSION_DOCS`（正文 + 代码块行经统一视图） | `TestDocsConsistencyScriptExtensions`（GDR-06 漂移检查） | governance / docs-consistency hook + docs-ci |
| 21 | `check_redlines_yaml_consistency` | 保留 | `docs/governance/redlines.yml` | `TestRedlinesYamlConsistency` | governance / docs-consistency hook + docs-ci |
| 22 | `check_redline_range_consistency` | 保留 | `docs/governance/redlines.yml` | `TestRedlinesYamlConsistency` | governance / docs-consistency hook + docs-ci |
| 23 | `check_contract_count_prose` | 保留 | `pyproject.toml`（`[project].version` 唯一正本）+ `CONTRIBUTING.md` | `TestContractCountProse` | governance / docs-consistency hook + docs-ci |
| 24 | `check_enforcement_mapping` | 保留 | `docs/governance/redlines.yml`（enforcement 字段） | `TestEnforcementMapping` | governance / docs-consistency hook + docs-ci |
| 25 | `check_enforcement_reverse_coverage` | 保留（F21 触发器感知扩展） | `docs/governance/redlines.yml` + workflows + pre-commit + `check_redlines.py` 存在性 | `TestEnforcementReverseCoverage`；触发器负例见 `tests/docs/test_enforcement_triggers.py::TestN5TriggerAwareness` / `TestRunBlockTriggers` | governance / docs-consistency hook + docs-ci |
| 26 | `check_exceptions_yaml_consistency` | 保留（F17 显式失败） | `docs/governance/exceptions.yml` | `TestExceptionsYamlConsistency` | governance / docs-consistency hook + docs-ci |
| 27 | `check_exceptions_reverse_coverage` | 保留 | `docs/governance/exceptions.yml` | `TestExceptionsYamlConsistency` | governance / docs-consistency hook + docs-ci |
| 28 | `check_canonical_topics_consistency` | 保留（F17 显式失败） | `docs/governance/canonical-topics.yml` + `CLAUDE.md` 路由表 | `TestCanonicalTopicsYamlConsistency` | governance / docs-consistency hook + docs-ci |
| 29 | `check_ruleset_metadata_consistency` | 保留 | `CLAUDE.md` / `AGENTS.md` 规则集元数据 | `TestRulesetMetadataConsistency` | governance / docs-consistency hook + docs-ci |
| 30 | `check_ruleset_changelog_version` | 保留 | `docs/governance/ruleset-changelog.md` | `TestRulesetChangelogVersion` | governance / docs-consistency hook + docs-ci |
| 31 | `check_decision_tree_mapping` | 保留 | `CLAUDE.md` §1.8 路由表 + canonical-topics.yml | `TestDecisionTreeMapping` | governance / docs-consistency hook + docs-ci |
| 32 | `check_canonical_routing` | 保留 | `CLAUDE.md` route 锚 + canonical-topics.yml | `TestCanonicalRouting` | governance / docs-consistency hook + docs-ci |
| 33 | `check_governance_id_references` | 保留 | `docs/governance/exceptions.yml` + `docs/**` | `TestGovernanceIdReferences` | governance / docs-consistency hook + docs-ci |
| 34 | `check_governance_id_glossary`（经 `_glossary_check_with_warnings` 包装入计划） | 共享解析替代（标题集经统一视图）+ 保留 warning 分级 | `docs/governance/governance-ids.md` + `docs/**` | `TestGovernanceIdGlossary`、`TestGovernanceIdGenericForm`；warning 分层见 `tests/docs/test_cli_layering.py`（F19） | governance / docs-consistency hook + docs-ci |
| 35 | `check_governance_id_dual_meaning` | 保留（F17 显式失败） | `docs/governance/governance-ids.md` | `TestGovernanceIdGenericForm` | governance / docs-consistency hook + docs-ci |
| 36 | `check_flet_badge_version` | 保留 | `README.md` | `TestProseMetaGuards` | governance / docs-consistency hook + docs-ci |
| 37 | `check_exception_count_prose` | 保留（F17 显式失败） | `docs/governance/exceptions.yml` + `docs/governance/redlines.yml` | `TestProseMetaGuards` | governance / docs-consistency hook + docs-ci |

## source scope（NOTE / 模块 / 单例 / 动态文案源码契约）

| # | 检查函数 | 处置 | 输入（必需源） | 对应测试 | 执行入口 |
|---|---------|------|---------------|---------|---------|
| 38 | `check_note_lazy_format` | 保留 | `scripts/`、`core/`…全仓 `*.py` 的 `NOTE(lazy):` 标记 | `TestNoteLazyFormatDetection`、`TestNoteLazyFormatRootLayout` | source / docs-consistency hook + docs-ci |
| 39 | `check_core_modules_completeness` | 保留 | `core/**` + `docs/architecture/overview.md` §4.2（D6 起；原 `CLAUDE.md` §4.2，架构正文正本迁移） | `TestCoreModulesCompleteness` | source / docs-consistency hook + docs-ci |
| 40 | `check_strategy_desc_dynamic_consistency` | 保留（F17 locale 必需资产夹具修正） | `ui/locales/**` 双语 strings.json + 策略源码 | `TestStrategyDescDynamicConsistency` | source / docs-consistency hook + docs-ci |
| 41 | `check_singleton_count_prose` | 保留 | `docs/architecture/singleton-lifecycle.md` | `TestSingletonCountProse` | source / docs-consistency hook + docs-ci |
| 42 | `check_non_registered_singleton_doc` | 保留 | `docs/architecture/singleton-lifecycle.md` | `TestNonRegisteredSingletonDoc` | source / docs-consistency hook + docs-ci |

## D4 新增（不在 42 项原清单内，同一执行入口）

| # | 检查函数 | 处置 | 输入（必需源） | 对应测试 | 执行入口 |
|---|---------|------|---------------|---------|---------|
| 43 | `check_docs_generated` | 受控生成协议承载（D4） | `scripts/docs_generated.py` BUILTIN_REGISTRY 激活条目（AGENTS.md × 2、CLAUDE.md × 2 生成块 + 作者源） | `tests/docs/test_generate.py`、`test_concurrency.py`；必需输入负例 `test_required_inputs.py` | document / docs-consistency hook + docs-ci |

## 迁移出口备注（退役前置条件）

- **#4–#7（AGENTS/CLAUDE 同步四项）**：与 `check_docs_generated`（#43）构成「旧表达检查 + 注册表一致性」双保险。退役旧表达须同时满足：① D6/T6 已切作者源并激活对应注册条目；② 旧检查的既有反例（F20 修法负例）在注册表路径上有等价失败样例（检视 §2 T5「任一旧检查退役前，正常样例、原失败样例和迁移后的等价失败样例都必须运行」）。退役动作本身属后续工作包，不在 D5 内执行。
- **#10/#11/#12（索引类）**：T6 生成 `docs/README.md` 目录后，候选集与覆盖判定可转由 `docs-readme-file-index` 注册槽位（现为非激活占位）承载；切换前本表现状为准。
- 本表为 D5 时点快照；后续工作包调整处置时须同步更新本表并保持「逐项人工核对」口径（不得以函数计数代理责任完整）。
