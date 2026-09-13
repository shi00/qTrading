# Changelog

## Unreleased

## [0.10.0](https://github.com/shi00/qTrading/compare/v0.9.0...v0.10.0) (2026-09-13)


### Features

* **ci:** review07-G14 分层覆盖率测量 --report 模式（advisory 观测） ([#630](https://github.com/shi00/qTrading/issues/630)) ([19c7017](https://github.com/shi00/qTrading/commit/19c701767c4b38b8722b456ba6c65f0720bd9e73))
* **ci:** review07-G15 新增 docs-ci 独立 workflow 覆盖纯文档 PR 一致性检查 ([#633](https://github.com/shi00/qTrading/issues/633)) ([183ec76](https://github.com/shi00/qTrading/commit/183ec761aa9f09d07232349fbe5c4ace28f2321e))
* **ci:** review07-G3 weak-assertion baseline 增加下降 KPI 机制 ([#624](https://github.com/shi00/qTrading/issues/624)) ([e64f4a0](https://github.com/shi00/qTrading/commit/e64f4a00abded022a6e9016d582c512750265fef))
* **ci:** review07-G5 type-ignore reason 务实版（tests attr-defined 豁免 + 渐进 human reason） ([#625](https://github.com/shi00/qTrading/issues/625)) ([37d9d8d](https://github.com/shi00/qTrading/commit/37d9d8d753d2cc8f31708ffb634b2c819e455bd9))
* **docs:** 补齐发布与打包任务入口（GDR-08） ([#785](https://github.com/shi00/qTrading/issues/785)) ([267420a](https://github.com/shi00/qTrading/commit/267420aab969f69a8b75c86e5c2c8848e79c097f))
* **docs:** 需求正本纳入任务决策树与治理正本映射（GDR-04） ([#784](https://github.com/shi00/qTrading/issues/784)) ([169d986](https://github.com/shi00/qTrading/commit/169d9867fb4cd27f0620b5ce1343b4f0feb9008a))
* **e2e:** 增加未接线死锚点静态门禁与保留标记机制 (UIX-18) ([#757](https://github.com/shi00/qTrading/issues/757)) ([f41ecdc](https://github.com/shi00/qTrading/commit/f41ecdce610540ea12ad2b67f09934cb388dc02b))
* **error-classifier:** review05-E1 fallback debug 日志 ([#671](https://github.com/shi00/qTrading/issues/671)) ([87d5f6e](https://github.com/shi00/qTrading/commit/87d5f6e0b8206fd522eeedb968c7c15f4cec8db6))
* **error-hierarchy:** add core.errors AppError base + classify first branch (review05-E3) ([#670](https://github.com/shi00/qTrading/issues/670)) ([1afed6a](https://github.com/shi00/qTrading/commit/1afed6a96026eec7d0d8882ea0dd068aac481636))
* **infra:** review01-A1 import-linter 补契约 5/6 — utils/ui 分层方向 pre-commit 守护 ([#596](https://github.com/shi00/qTrading/issues/596)) ([55c0ab8](https://github.com/shi00/qTrading/commit/55c0ab8f256eb821ed81d7199633c90f9ff3966f))
* **metrics:** add in-process MetricsRegistry + diagnostics export (review05-E19) ([#672](https://github.com/shi00/qTrading/issues/672)) ([6c76966](https://github.com/shi00/qTrading/commit/6c769664314b4e207233451cb472611e965b7656))
* **quality:** add optional continuous-window integrity check to quality gate (D2-9) ([#826](https://github.com/shi00/qTrading/issues/826)) ([3da9974](https://github.com/shi00/qTrading/commit/3da99741d1c7c57eaa175122c036949571a78620))
* **redlines:** review07-G18 R4 检查补强 — 扩目录+字面量检测+f-string warning ([#617](https://github.com/shi00/qTrading/issues/617)) ([83fecb8](https://github.com/shi00/qTrading/commit/83fecb8f33dbd6013e8782ec90c0bad08ba81b08))
* **redlines:** review07-G19 R15 识别条件扩展 + R13 描述同步 ([#618](https://github.com/shi00/qTrading/issues/618)) ([cd5101c](https://github.com/shi00/qTrading/commit/cd5101c2a70f1edb36aa29a8e7cec452abec1492))
* **redlines:** review07-G20 实现 R16 最小可行检查（VM 构造单例检测） ([#616](https://github.com/shi00/qTrading/issues/616)) ([6665a1b](https://github.com/shi00/qTrading/commit/6665a1bd26d0b8c9dc0eb0efdedc01acc2d8a3cf))
* **screener:** AI 三分区呈现 + HISTORY 单表回归 (D7-3) ([58b02a1](https://github.com/shi00/qTrading/commit/58b02a15a3a42bc180eda2561c31f04e0606abf8))
* **screener:** ScreenerView AI 三分区呈现 + HISTORY 单表回归 (D7-3) ([144b7a1](https://github.com/shi00/qTrading/commit/144b7a145f3e98f860ef2badde5098c52c822461))
* **scripts:** P1-01 三条业务红线 AST 可行性原型 ([8d2363e](https://github.com/shi00/qTrading/commit/8d2363e3a4f68e66bc9f3ee77b134ea91439b277))
* **scripts:** P1-01 三条业务红线 AST 可行性原型 ([6691be6](https://github.com/shi00/qTrading/commit/6691be6cbbd036b755bfeae74d2a7af1236658bc))
* **ui:** AppColors 类级状态单例化，根治跨测试状态泄漏 ([#634](https://github.com/shi00/qTrading/issues/634)) ([d658a8a](https://github.com/shi00/qTrading/commit/d658a8ac80bfe6da8b24a7bfbd025b2604ce3b40))
* **ui:** SQL 控制台输入框替换为 flet-code-editor 支持语法高亮 ([#530](https://github.com/shi00/qTrading/issues/530)) ([4a60714](https://github.com/shi00/qTrading/commit/4a60714194956247822b30b197f61f487f22ae5e))
* **ui:** UX-10 字号基准提升 - AppStyles 11/12/13 升至 12/13/14 ([#644](https://github.com/shi00/qTrading/issues/644)) ([d3274c2](https://github.com/shi00/qTrading/commit/d3274c2dd86e97803397c9874f5f0da14792086b))
* **ui:** UX-12 回测图表语境增强 - 日期横轴/轴标题/图例/基准对比/可复制摘要 + IC 日期透传 ([#656](https://github.com/shi00/qTrading/issues/656)) ([fd6f8cc](https://github.com/shi00/qTrading/commit/fd6f8cc6d9d7964171b1fcc6cc2f4d5f2022c9ea))
* **ui:** 关注列表添加关注入口 — 搜索选股+备注+确认对话框 ([#433](https://github.com/shi00/qTrading/issues/433)) ([#595](https://github.com/shi00/qTrading/issues/595)) ([96a164a](https://github.com/shi00/qTrading/commit/96a164ad0bddd3992091928a79b8e7d7742cebcc))
* **ui:** 响应式断点模型 + 零尺寸护栏 + SafeWrapRow + 1280×720 E2E (批次3-C5 UIX-13) ([#750](https://github.com/shi00/qTrading/issues/750)) ([5b64efa](https://github.com/shi00/qTrading/commit/5b64efa8b3b64d5dd7084acfd819c4fa7db043fa))
* **ui:** 回测输入显式校验 — 非法参数显示错误并禁用运行，删除静默兜底（UX-05） ([#592](https://github.com/shi00/qTrading/issues/592)) ([30b67dc](https://github.com/shi00/qTrading/commit/30b67dc5dffd963893370d4ed32823f6c41233e2))
* **ui:** 导航深链协议 TOPIC_NAVIGATE 支持 "&lt;tab&gt;:&lt;subtab&gt;" 直达子页（UX-01） ([#546](https://github.com/shi00/qTrading/issues/546)) ([46d3bde](https://github.com/shi00/qTrading/commit/46d3bde8676471a59f69b26c78b382263817b90d))
* **ui:** 数据筛选清除路径 — 清除按钮 + 空态一键恢复 CTA（UX-07） ([#626](https://github.com/shi00/qTrading/issues/626)) ([0a4ed89](https://github.com/shi00/qTrading/commit/0a4ed89ec1d37e86a19146f011ad41d9b2a0784a))
* **ui:** 新增 LoadingState 共享加载态组件并消除 task_center 空态手工复制 (UIX-14) ([#753](https://github.com/shi00/qTrading/issues/753)) ([7bd607e](https://github.com/shi00/qTrading/commit/7bd607e4398eb35aa12850e4ec6ca657bbc51735))
* **ui:** 股票上下文最小方案 — 新闻/关注列表深链带股票代码进选股页过滤（UX-04） ([#591](https://github.com/shi00/qTrading/issues/591)) ([1ae3ada](https://github.com/shi00/qTrading/commit/1ae3adacfd4e35cb32e88805dc8a2b41a158c3ee))
* **ui:** 表单 Enter 提交绑定主操作 — screener 过滤框 + backtest 参数输入（review04 D19） ([#609](https://github.com/shi00/qTrading/issues/609)) ([d8df7b5](https://github.com/shi00/qTrading/commit/d8df7b5e7bad24558c357f8eb5780147345458c4))
* **ui:** 选股质量门失败态渲染「前往同步」深链按钮（UX-02） ([#549](https://github.com/shi00/qTrading/issues/549)) ([2813b3d](https://github.com/shi00/qTrading/commit/2813b3d53d28b4af61d68183bc6a61b23d01b3c7))


### Bug Fixes

* **agents:** 校正 AGENTS.md 最小安全集声明句并加门禁断言 ([#727](https://github.com/shi00/qTrading/issues/727)) ([118518a](https://github.com/shi00/qTrading/commit/118518a4f5d4a88a29e161d68a5bdf969a88e1b0))
* **ai:** accurately log missing key on cross-provider failover (D5-9) ([#802](https://github.com/shi00/qTrading/issues/802)) ([19b2b9f](https://github.com/shi00/qTrading/commit/19b2b9f93f16e68561a220f164f4adb40e47e970))
* **ai:** align prompt structure with declaration and isolate untrusted external content (D5-2) ([#795](https://github.com/shi00/qTrading/issues/795)) ([d74e30d](https://github.com/shi00/qTrading/commit/d74e30d864c9f1b89ad2d6ca9c9d91a5d4f0981c))
* **ai:** bg_fetch_news 返回 (news, ok) 并透传结果行提示新闻缺失 (D5-7) ([8acb7a9](https://github.com/shi00/qTrading/commit/8acb7a9a730621f5633b240bddaf98980fd1cb12))
* **ai:** bg_fetch_news 返回 (news, ok) 并透传结果行提示新闻缺失 (D5-7) ([a36b316](https://github.com/shi00/qTrading/commit/a36b316e347254bf083ebde7f7cbcd2a009b5afa))
* **ai:** compute context budget based on active model when failover not configured (D5-3) ([#796](https://github.com/shi00/qTrading/issues/796)) ([3454f68](https://github.com/shi00/qTrading/commit/3454f6861db775e20882ff2bda02d0b98e4071fc))
* **ai:** converge news fetch exceptions to prevent stock drop (D5-7) ([#800](https://github.com/shi00/qTrading/issues/800)) ([1168256](https://github.com/shi00/qTrading/commit/1168256f515c78384ee698cf3e0504a923c5eda0))
* **ai:** dynamically deduct system messages and fixed blocks from token budget (D5-4) ([#797](https://github.com/shi00/qTrading/issues/797)) ([ae09eb2](https://github.com/shi00/qTrading/commit/ae09eb2e64b00a37d41ec122239bd3f2c37061c6))
* **ai:** enforce external data policy check before initiating any external requests (D5-1) ([#794](https://github.com/shi00/qTrading/issues/794)) ([72e983e](https://github.com/shi00/qTrading/commit/72e983edd5513728c4c1508a94937377aaab41df))
* **ai:** enhance concurrency streaming feedback and realtime progress (D5-6) ([#799](https://github.com/shi00/qTrading/issues/799)) ([cfee892](https://github.com/shi00/qTrading/commit/cfee892219c0f6d6fbe0d95c2732703c4cdd23c6))
* **ai:** keep AI rejected/failed rows with ai_status, guard review write (D3-6, D3-7) ([17e8f3b](https://github.com/shi00/qTrading/commit/17e8f3b700fc3384195a593e0c4fd4eafac03209))
* **ai:** keep AI rejected/failed rows with ai_status, guard review write (D3-6, D3-7) ([a1ee3ab](https://github.com/shi00/qTrading/commit/a1ee3abe5f836bd4583257d9e36a7264915308e2))
* **ai:** limit _history_cache by memory bytes instead of item count (D5-5) ([#798](https://github.com/shi00/qTrading/issues/798)) ([1379202](https://github.com/shi00/qTrading/commit/137920233106eb51619978e515662bf9cdfbcfc6))
* **ai:** link prompt context budget to model context window ([#70](https://github.com/shi00/qTrading/issues/70)) ([#487](https://github.com/shi00/qTrading/issues/487)) ([ab09a6b](https://github.com/shi00/qTrading/commit/ab09a6b9c94eaae90edbdd10e99cfd9182cac246))
* **ai:** refine fallback token estimator for CJK and non-CJK text (D5-8) ([#801](https://github.com/shi00/qTrading/issues/801)) ([c050877](https://github.com/shi00/qTrading/commit/c050877836a474d9e90f2c10bf10906dfe22acd5))
* **ai:** 政策未确认时返回带 ai_status 标记的结果行 (D5-1) ([e9d6b51](https://github.com/shi00/qTrading/commit/e9d6b512d8719d32ff31f55be97b065dbaa9ba72))
* **ai:** 政策未确认时返回带 ai_status 标记的结果行 (D5-1) ([e8da029](https://github.com/shi00/qTrading/commit/e8da029baa5ce58d608deb647b213ab5f024e0af))
* **ai:** 预算超窗时显式抛 AIBudgetError 提供可操作提示 (D5-4) ([e95e589](https://github.com/shi00/qTrading/commit/e95e5895e21b47990d1b710947f9493aef353307))
* **ai:** 预算超窗时显式抛 AIBudgetError 提供可操作提示 (D5-4) ([c8cbdf4](https://github.com/shi00/qTrading/commit/c8cbdf47cd1bc7d4b5dc59b7e8aff8de2c692c1b))
* **backtest:** BT-01 IC 语义显式化 has_real_score 并在 UI 区分排序 IC ([#885](https://github.com/shi00/qTrading/issues/885)) ([b8928f8](https://github.com/shi00/qTrading/commit/b8928f84aa5d7dd483dd6668a78dda7947ed1f5a))
* **backtest:** implement differential rebalancing instead of full sell-buy (D4-4) ([815ff10](https://github.com/shi00/qTrading/commit/815ff10928a5b585e2c208ce9f764e8f423f17fe))
* **backtest:** implement differential rebalancing instead of full sell-buy (D4-4) ([38dd361](https://github.com/shi00/qTrading/commit/38dd361f28e8ec40914198dc42a7df7c151768ef))
* **backtest:** profit factor 无亏损交易时返回 None 而非 inf (D4-5) ([4658edf](https://github.com/shi00/qTrading/commit/4658edfbd4d9f3899e91c2be42421fb6a18cc7b9))
* **backtest:** profit factor 无亏损交易时返回 None 而非 inf (D4-5) ([44f2997](https://github.com/shi00/qTrading/commit/44f2997ca6dd5f7a39cfa19344f7105aedbc93bb))
* **backtest:** unify backtest trade dates on TradeCalendarService with offline fallback (D4-8) ([9397316](https://github.com/shi00/qTrading/commit/9397316a739fd0b76c718f9a4f869f54728baaad))
* **backtest:** unify backtest trade dates on TradeCalendarService with offline fallback (D4-8) ([301d9ee](https://github.com/shi00/qTrading/commit/301d9eea5bd02a9f47c319fc8314cb69b8e06ba6))
* **backtest:** unify QFQ to point-in-time base for reproducible backtests (D2-2) ([#810](https://github.com/shi00/qTrading/issues/810)) ([800d765](https://github.com/shi00/qTrading/commit/800d7657a8199cf78d24195ec69cd10159b94128))
* **backtest:** 停牌/涨跌停数据缺失不再静默降级，改为显式告警 ([3f0a15e](https://github.com/shi00/qTrading/commit/3f0a15e4069cd8dc034e7c07253a57dedacacf59))
* **backtest:** 停牌/涨跌停数据缺失不再静默降级，改为显式告警 ([1efd795](https://github.com/shi00/qTrading/commit/1efd7953c4cf0e287f1fc0a21613db8bf1e8d923))
* **backtest:** 删除无生产调用点的 _buy_signals 死代码（§3 检视报告 item3） ([#895](https://github.com/shi00/qTrading/issues/895)) ([8b4327b](https://github.com/shi00/qTrading/commit/8b4327b4fd73668d8fee2b2ede5d9a1693c437f5))
* **backtest:** 成交量单位「手」统一换算「股」，修正滑点参与率高估（BT-06） ([#890](https://github.com/shi00/qTrading/issues/890)) ([db8faf2](https://github.com/shi00/qTrading/commit/db8faf208237bb48df42b68afe4b23a03362d324))
* **backtest:** 新增仓位运用效率可见性与 renormalize 满仓开关（BT-03） ([#888](https://github.com/shi00/qTrading/issues/888)) ([e904e63](https://github.com/shi00/qTrading/commit/e904e63827ea53678cf155fd887cf04acceee6ff))
* **backtest:** 显式登记回测质量门控硬编码 GOLD 的技术债（BT-05 第一步） ([#891](https://github.com/shi00/qTrading/issues/891)) ([02327fe](https://github.com/shi00/qTrading/commit/02327fe11a7c6c7638af74576bdf2beee0584fd0))
* **backtest:** 消除买单资金缩放随机性，预算受真实现金钳制并按ts_code稳定排序（§3 检视报告 item1） ([#894](https://github.com/shi00/qTrading/issues/894)) ([90bc119](https://github.com/shi00/qTrading/commit/90bc119e9004fd9094a76f523c3452731637897b))
* **backtest:** 统一净值 qfq 计价口径消除除权日虚假收益 (D4-1) ([8c3ea52](https://github.com/shi00/qTrading/commit/8c3ea523871760b470845c74c238d31161d46832))
* **backtest:** 统一净值 qfq 计价口径消除除权日虚假收益 (D4-1) ([79e6e4b](https://github.com/shi00/qTrading/commit/79e6e4b2da4d754437d12fe9635eeec3cf04cc6c))
* **backtest:** 胜率排除退市强平样本，按 exit_reason 区分主动决策 (D4-6) ([25de021](https://github.com/shi00/qTrading/commit/25de021f17bdf45f0c3bc44ed248bdacafc06267))
* **backtest:** 胜率排除退市强平样本，按 exit_reason 区分主动决策 (D4-6) ([55729f8](https://github.com/shi00/qTrading/commit/55729f84dba1ad320131e68612cee8b324e39e13))
* **backtest:** 退市清算按可配置回收率变现并计成本，新增分项统计（BT-02） ([9d00a6f](https://github.com/shi00/qTrading/commit/9d00a6f226f01dd480dfdaac6d0219e51e194d4f))
* **backtest:** 退市清算按可配置回收率变现并计成本，新增分项统计（BT-02） ([025b7f6](https://github.com/shi00/qTrading/commit/025b7f6a3ab133888f359a5d7608b640c6977459))
* **bootstrap:** 服务启动部分失败时逆序停止已启动服务防泄漏 ([#538](https://github.com/shi00/qTrading/issues/538)) ([b0e8908](https://github.com/shi00/qTrading/commit/b0e89086c39d78cd58e2f1b9300a4d2217c3546b))
* **calendar:** return None on insufficient calendar instead of rough estimate (D2-1) ([#812](https://github.com/shi00/qTrading/issues/812)) ([ae51a1a](https://github.com/shi00/qTrading/commit/ae51a1a2bc0f056698cd12a241d143d997a48afb))
* **ci:** skip secrets-dependent jobs on Dependabot PRs ([d842d25](https://github.com/shi00/qTrading/commit/d842d2540e4378fae6d06b6deed11e50197215c9))
* **ci:** 修复 0020/0021 Alembic 迁移索引幂等与 alembic check 一致性 ([ff00050](https://github.com/shi00/qTrading/commit/ff000503d56e516e71e1530ae1f12b1da2a81e4f))
* **ci:** 修复浅克隆下 diff-coverage 三点 diff 退化为两点误报 (PR [#722](https://github.com/shi00/qTrading/issues/722) 流水线问题) ([#724](https://github.com/shi00/qTrading/issues/724)) ([0204229](https://github.com/shi00/qTrading/commit/02042293de1a6d3bfdb7248c2b112a51f8caf755))
* **concurrency:** capture asyncio task early and calibrate running state in TaskManager (CON-04) ([#773](https://github.com/shi00/qTrading/issues/773)) ([b7d0f31](https://github.com/shi00/qTrading/commit/b7d0f31caf1e19fbee14147089337ae72e92f633))
* **concurrency:** defer semaphore reload when tasks are running in TaskManager (CON-05) ([#765](https://github.com/shi00/qTrading/issues/765)) ([c2e4e34](https://github.com/shi00/qTrading/commit/c2e4e3434cce2ec02ddf3b8d4734108c2b1499ed))
* **concurrency:** prevent persist counter leak on coro drop in TaskManager (CON-06) ([#764](https://github.com/shi00/qTrading/issues/764)) ([b952f0e](https://github.com/shi00/qTrading/commit/b952f0ebc24e3a877085cad0f9809186a8c01f23))
* **concurrency:** reload_config 停机幂等防泄漏 + 在途任务语义如实化 (CON-05) ([#711](https://github.com/shi00/qTrading/issues/711)) ([1255dbf](https://github.com/shi00/qTrading/commit/1255dbfbf11fc762829d6ec62bcb9e23a9541849))
* **concurrency:** remediate CON-04 review findings and calibrate task lifecycle semantics ([#776](https://github.com/shi00/qTrading/issues/776)) ([5fed180](https://github.com/shi00/qTrading/commit/5fed180decc54e876fc5c3568a2f08c6f01233ec))
* **concurrency:** test_task_manager fake_sched 适配 _schedule_coro 的 on_drop 参数 ([511eaef](https://github.com/shi00/qTrading/commit/511eaefa73c06ace7957c1b2116ef5faff30090b))
* **concurrency:** test_task_manager fake_sched 适配 _schedule_coro 的 on_drop 参数 ([836cb91](https://github.com/shi00/qTrading/commit/836cb91f709eb1a898c283dac7c348819e98c7c3))
* **concurrency:** 单例双重初始化竞态改为 double-checked locking (CON-01) ([#690](https://github.com/shi00/qTrading/issues/690)) ([329f32e](https://github.com/shi00/qTrading/commit/329f32ef553717f35047efb8d4abafd43c45a4ec))
* **config:** keyring 可用性探测改 TTL 限时缓存（D8-6） ([aac2ae4](https://github.com/shi00/qTrading/commit/aac2ae47c8da4052498ddb1d48d81153a3ae0894))
* **config:** keyring 可用性探测改 TTL 限时缓存（D8-6） ([a5ea964](https://github.com/shi00/qTrading/commit/a5ea9648f8620141744e17d211e588c92af58538))
* **config:** unify credential save outcome and secure-store fallback chain (D8-2/D8-3/D8-5) ([64cc13c](https://github.com/shi00/qTrading/commit/64cc13cbd8b252b02cc5bf5267a3e4c16a08a986))
* **config:** unify credential save outcome and secure-store fallback chain (D8-2/D8-3/D8-5) ([1003e4f](https://github.com/shi00/qTrading/commit/1003e4f2d9b4e9ca6062dc3dba88e9695a9a8cd0))
* **d7-5:** add AST guard against hardcoded user-facing text ([52d47d6](https://github.com/shi00/qTrading/commit/52d47d6e9cecb69d753314e7b5984c761a89a794))
* **d7-5:** align AppTask default-value tests with Message defaults ([41abb74](https://github.com/shi00/qTrading/commit/41abb74f4fc5bd1095e9584f7d8ff0136cc63337))
* **d7-5:** 新增 AST 守护断言非 UI 层硬编码用户文案 ([59a444a](https://github.com/shi00/qTrading/commit/59a444a7623ae0161e6a7e6a8782326e2ae9c7a6))
* **dao:** 破除 _check_engine 对全局 disposed 标志的耦合 ([#664](https://github.com/shi00/qTrading/issues/664)) ([632b94b](https://github.com/shi00/qTrading/commit/632b94b6b93e2ddc0233cdb55a0825fddd6a9040))
* **data:** attribute missing trading dates in quality-gate errors (D2-9) ([#820](https://github.com/shi00/qTrading/issues/820)) ([2d518bf](https://github.com/shi00/qTrading/commit/2d518bf5c74be9fbe1cc067f7ba205fe330c0632))
* **data:** clarify delay_multiplier usage intent in seasonal adjustments (D1-7) ([#811](https://github.com/shi00/qTrading/issues/811)) ([e60f7d4](https://github.com/shi00/qTrading/commit/e60f7d4fee9284ed07f003981fd1ca2ffb965527))
* **data:** DAO 日期参数边界显式 date 归一化（DAT-26） ([#734](https://github.com/shi00/qTrading/issues/734)) ([5bd5175](https://github.com/shi00/qTrading/commit/5bd5175f706d693a69ae36ccbe842ee78b75b752))
* **data:** Data Explorer 增加会话级 statement_timeout（review03-C6） ([#553](https://github.com/shi00/qTrading/issues/553)) ([d4f1be5](https://github.com/shi00/qTrading/commit/d4f1be55e8d25dda5cc852572adc066719c305cb))
* **data:** Decimal 读取归一化 float64 + 区间预载 max_rows 护栏（DAT-10/11） ([#726](https://github.com/shi00/qTrading/issues/726)) ([79382dd](https://github.com/shi00/qTrading/commit/79382dde07ab96a310578119cf50f2faedb55866))
* **data:** embedded pg start/stop 状态变迁统一由 cls._lock 串行化（review03-C18） ([#557](https://github.com/shi00/qTrading/issues/557)) ([ba72400](https://github.com/shi00/qTrading/commit/ba7240073e587fb635316c767d1ffe4a09c25d17))
* DataExplorer embedded 模式连接错误端口(5432) 根因修复 ([#535](https://github.com/shi00/qTrading/issues/535)) ([18227e6](https://github.com/shi00/qTrading/commit/18227e6f90d3d6c8a8af0a3dbc8ece180a912b90))
* **data:** macro 交易日回退链委托共享 (review03-C14) ([#665](https://github.com/shi00/qTrading/issues/665)) ([66e7f84](https://github.com/shi00/qTrading/commit/66e7f846d962cefa8911fd9b86904b978f9d0b19))
* **data:** moneyflow_hsgt sync_config strategy 对齐 actual historical driver ([#94](https://github.com/shi00/qTrading/issues/94)) ([#731](https://github.com/shi00/qTrading/issues/731)) ([f858fa0](https://github.com/shi00/qTrading/commit/f858fa0c8b66b8944e09c93667576fc97bfc620b))
* **data:** narrow quality-score report aggregation to touched date range (D1-6) ([#808](https://github.com/shi00/qTrading/issues/808)) ([705ccc1](https://github.com/shi00/qTrading/commit/705ccc1c78dd49ad94a51f322b1a6c660f2bae9c))
* **data:** quality gate strict mode hardening review03-C15 ([#560](https://github.com/shi00/qTrading/issues/560)) ([86064ea](https://github.com/shi00/qTrading/commit/86064ea5860385e3ec51db765af72677edf78d39))
* **data:** R5 引擎检查统一委托 _check_engine（review03-C3） ([#552](https://github.com/shi00/qTrading/issues/552)) ([47c02e1](https://github.com/shi00/qTrading/commit/47c02e188e85162857e452e234bc56b3cdc61434))
* **data:** replace consecutive-failure circuit breaker with sliding-window failure rate (D1-3) ([#803](https://github.com/shi00/qTrading/issues/803)) ([4b25db5](https://github.com/shi00/qTrading/commit/4b25db50971a64398f4e158577e066609fb3287b))
* **data:** report skip-cached dates via progress callback in historical sync (D1-8) ([#813](https://github.com/shi00/qTrading/issues/813)) ([ce6c062](https://github.com/shi00/qTrading/commit/ce6c06262d2fb7262de2c54c11cedbc96692f15d))
* **data:** review03 DAT-02 消除 adj_factor 静默降级 1.0 兜底 + null_protected + 单调性检查 ([#696](https://github.com/shi00/qTrading/issues/696)) ([b540390](https://github.com/shi00/qTrading/commit/b5403908c95fc8ad8ac86f6294bd30f0235f6d99))
* **data:** review03 DAT-03/04/05 脏日期 coerce 质量门控 + 向量化 + import 上移 ([#694](https://github.com/shi00/qTrading/issues/694)) ([ad80b01](https://github.com/shi00/qTrading/commit/ad80b01c38b50acfb1b96c1c18cea4fe70367f13))
* **data:** review03 DAT-04 财报同步股票池覆盖退市股，消除基本面生存者偏差 ([#706](https://github.com/shi00/qTrading/issues/706)) ([90a9e60](https://github.com/shi00/qTrading/commit/90a9e604d003b8b98bbe916eb4d33ffb8ad2d586))
* **data:** review03 DAT-05 质押 PIT 查询加 45 天披露滞后，消除回测未来函数 ([#705](https://github.com/shi00/qTrading/issues/705)) ([a13ac59](https://github.com/shi00/qTrading/commit/a13ac592e2bc700b7e239af8041e658a82e81f63))
* **data:** review03 DAT-06 PIT 查询显式排除 ann_date IS NULL 行，消除回测/实盘口径差 ([#698](https://github.com/shi00/qTrading/issues/698)) ([aaaa5a2](https://github.com/shi00/qTrading/commit/aaaa5a29d0390192e7e92123cb0d9181d3525c45))
* **data:** review03 DAT-08 消除 text(f"") f-string SQL 注入风险 + R4 红线守卫 ([#703](https://github.com/shi00/qTrading/issues/703)) ([6cfc4a0](https://github.com/shi00/qTrading/commit/6cfc4a041037e2f933111b9d4a24b5faf974cb8f))
* **data:** review03 DAT-08 行业 LATERAL 子查询加 ORDER BY index_code 消除 LIMIT 1 不确定归属 ([c8d16aa](https://github.com/shi00/qTrading/commit/c8d16aacb8c070d781f44d760bf1123a5dbfbdbf))
* **data:** review03 DAT-08 行业 LATERAL 子查询加 ORDER BY index_code 消除 LIMIT 1 不确定归属 ([ef45b2f](https://github.com/shi00/qTrading/commit/ef45b2ff9f8412ae71bcef186f7742a57a5bc866))
* **data:** review03 DAT-12/13 接入跨表一致性校验与维度表质量监控 ([#708](https://github.com/shi00/qTrading/issues/708)) ([ceda42b](https://github.com/shi00/qTrading/commit/ceda42b2eef348ff214414e15fc958efdfdd1d93))
* **data:** sync_concepts 阻塞期间取消响应缩入 2s 红线 ([#537](https://github.com/shi00/qTrading/issues/537)) ([1998b04](https://github.com/shi00/qTrading/commit/1998b04443640114959a6ea80501bcb297b4035a))
* **data:** tiered breakpoint resume by dense tables with attempted-upto watermark (D1-1) ([#804](https://github.com/shi00/qTrading/issues/804)) ([167d0ae](https://github.com/shi00/qTrading/commit/167d0aec2f974512b19fbeec8982e7d0f3aec110))
* **data:** write_db 门面默认停止吞异常（review03-C12） ([#580](https://github.com/shi00/qTrading/issues/580)) ([7d33637](https://github.com/shi00/qTrading/commit/7d336374639258e59eee5b7a93aa4478188da7cb))
* **data:** 交易日获取回退链修正，不再回退到本地日历日（review03-C14） ([#559](https://github.com/shi00/qTrading/issues/559)) ([6950b75](https://github.com/shi00/qTrading/commit/6950b75a74cb3e329de89365040ba80aadaa2ec5))
* **data:** 修复 DAT-14/15/16/23 schema 索引与约束（0018 迁移） ([#735](https://github.com/shi00/qTrading/issues/735)) ([feadc67](https://github.com/shi00/qTrading/commit/feadc673b32f825c9ac603a5f0b7c5bd94f2b862))
* **data:** 四张明细表主键容纳明细，消除 UPSERT 静默丢行（DAT-09） ([#730](https://github.com/shi00/qTrading/issues/730)) ([9bad780](https://github.com/shi00/qTrading/commit/9bad780ffad82e67c45658fdffa411d211fb8028))
* **data:** 拆分申万/Tushare 行业为独立列，移除写时覆写（DAT-08③） ([#721](https://github.com/shi00/qTrading/issues/721)) ([f9804fd](https://github.com/shi00/qTrading/commit/f9804fd5ccd78d80904f36e9834ab17377bd198b))
* **data:** 消除 DATA-04 行业分类/股票名称快照前视偏差 ([3f76f0d](https://github.com/shi00/qTrading/commit/3f76f0deb9010f7b01b63c526c98eb2c64581f7b))
* **data:** 消除 DATA-04 行业分类/股票名称快照前视偏差 ([1aecaa6](https://github.com/shi00/qTrading/commit/1aecaa6d59d02ee4067de6bb5dcba52709d68779))
* **data:** 消除 R5 引擎守卫 TOCTOU 并修复已释放引擎误判 (DAT-01/DAT-02) ([#693](https://github.com/shi00/qTrading/issues/693)) ([7dc47a1](https://github.com/shi00/qTrading/commit/7dc47a19a3120318e456ccb1e350485e3902acb7))
* **data:** 统一「最新一期财报」排序口径为 end_date DESC, ann_date DESC (DAT-03) ([#697](https://github.com/shi00/qTrading/issues/697)) ([cd4b63b](https://github.com/shi00/qTrading/commit/cd4b63b0f67405a180b6721fdb1362aa642f2e3e))
* **data:** 行业 LATERAL 子查询按 as-of 时点过滤，消除回测行业前视偏差 (DATA-04 L2) ([#896](https://github.com/shi00/qTrading/issues/896)) ([ae2d4c0](https://github.com/shi00/qTrading/commit/ae2d4c0daa298ffdd0205a9c148fc7a7c7840af9))
* **data:** 补齐 A 股印花税历史档位并支持双边征收（DATA-06） ([4176bf8](https://github.com/shi00/qTrading/commit/4176bf81721871594416d3ffe2c691f08c56a93f))
* **data:** 补齐 A 股印花税历史档位并支持双边征收（DATA-06） ([346a143](https://github.com/shi00/qTrading/commit/346a143786b145f2f590be199c61be5edceabe12))
* **data:** 读路径 chunk 失败 fail-fast 显式化（review03-C1） ([#565](https://github.com/shi00/qTrading/issues/565)) ([ff06b41](https://github.com/shi00/qTrading/commit/ff06b41f6b8f5a612015c1e1537eae07113c633f))
* **data:** 读路径行数上限安全阀（review03-C4） ([#577](https://github.com/shi00/qTrading/issues/577)) ([256b09a](https://github.com/shi00/qTrading/commit/256b09a0b4f8a01b27cec5023595f8505bd9c2d6))
* **data:** 财报表主键加入 ann_date 版本维度，PIT 还原财报更正不泄露 (DATA-05) ([4b7b0a2](https://github.com/shi00/qTrading/commit/4b7b0a2f3824fa323b135444b4fdd7e997cabd93))
* **data:** 财报表主键加入 ann_date 版本维度，PIT 还原财报更正不泄露 (DATA-05) ([83f5bed](https://github.com/shi00/qTrading/commit/83f5bed6f48d571bce9a08472843179261b1d2ae))
* **data:** 超大 UPSERT 拆每块独立事务（review03-C2） -C2 ([#573](https://github.com/shi00/qTrading/issues/573)) ([8e48dbe](https://github.com/shi00/qTrading/commit/8e48dbe5559f5da59e1c5b23dc8877f952044a6d))
* **data:** 选股主路径消除生存者偏差，存活判定抽为唯一正本 (DAT-01) ([#695](https://github.com/shi00/qTrading/issues/695)) ([004ce77](https://github.com/shi00/qTrading/commit/004ce77b9700057d3f147705770af277ed240152))
* de-hardcode embedded PostgreSQL version via sidecar version --json ([#512](https://github.com/shi00/qTrading/issues/512)) ([613127d](https://github.com/shi00/qTrading/commit/613127d01c4352a1f6fc292a9286955a19fa6888))
* **deps:** 升级 sidecar chacha20 0.10.1→0.10.2，消除 yanked 使 cargo audit 通过 ([#649](https://github.com/shi00/qTrading/issues/649)) ([857d48f](https://github.com/shi00/qTrading/commit/857d48fed55bc1cba89c3ec44dacf5d0b07117b5))
* **docs:** canonical 路由升级为真链接断言，纯文本/代码块提及不再算路由 (F6, DOC-05) ([#720](https://github.com/shi00/qTrading/issues/720)) ([ae721f1](https://github.com/shi00/qTrading/commit/ae721f1d4210266bff3921f11dc0a0de82ded23f))
* **docs:** check_decision_tree_mapping 逐主题绑定 canonical 归属 (F2) ([80a004b](https://github.com/shi00/qTrading/commit/80a004b56c1b734b94052aa21d7a1b4825e134d8))
* **docs:** check_decision_tree_mapping 逐主题绑定 canonical 归属 (F2) ([9c587c9](https://github.com/shi00/qTrading/commit/9c587c96eceb35ce81d895978c3357836650f5a4))
* **docs:** exceptions 引用语料收窄，归档/记录/评测目录豁免 (F3) ([#716](https://github.com/shi00/qTrading/issues/716)) ([9781fc1](https://github.com/shi00/qTrading/commit/9781fc1785f5113bf76622ac0941f5fec3901ae4))
* **docs:** F2 评审收口——topic.id 缺字段 KeyError 健壮性回归修复 (实施检视 MAJOR) ([5aeb2c0](https://github.com/shi00/qTrading/commit/5aeb2c0486779ead6164212a3e4a18d522a9fef5))
* **docs:** 修复core层与技术栈事实漂移并新增core模块守护门禁 (GDR-11) ([#789](https://github.com/shi00/qTrading/issues/789)) ([dd9cc59](https://github.com/shi00/qTrading/commit/dd9cc59fec831957752aa78bc4c3e4788f3c3f38))
* **docs:** 修复文档体系检视批 1（GDR-01/GDR-02/GDR-06） ([#783](https://github.com/shi00/qTrading/issues/783)) ([2af3989](https://github.com/shi00/qTrading/commit/2af3989adb34134f27ddbfe5ee495ff10d2796b9))
* **docs:** 修复文档体系检视问题 — 红线 schema 增强 + AI 交互规则修正 ([#522](https://github.com/shi00/qTrading/issues/522)) ([93b4293](https://github.com/shi00/qTrading/commit/93b4293c5a310c12634765dcbc95da3ee766245a))
* **docs:** 建立治理ID对照表并新增登记门禁 (GDR-09) ([#792](https://github.com/shi00/qTrading/issues/792)) ([930a210](https://github.com/shi00/qTrading/commit/930a2109633f09facd0381c2b6294cfdcbb094de))
* **docs:** 拆分产物目录常量区分真实gitignored与EX语料豁免 (GDR-07) ([#788](https://github.com/shi00/qTrading/issues/788)) ([046ce4e](https://github.com/shi00/qTrading/commit/046ce4e93ab57520c8ed79f2bb9b04eb144af345))
* **docs:** 文档索引全覆盖扫描豁免本地产物目录 (DOC-11 corner case) ([#722](https://github.com/shi00/qTrading/issues/722)) ([e748bc5](https://github.com/shi00/qTrading/commit/e748bc5d6e266123c335bc99d572791e51cbf3d9))
* **e2e:** AnchorPage.select_option 下拉选项匹配根因修复（E2E 稳定性） ([#593](https://github.com/shi00/qTrading/issues/593)) ([4fab4cd](https://github.com/shi00/qTrading/commit/4fab4cdb44a40eb66f7855ef0c25d6159418a864))
* **embedded-pg:** dump 子进程可取消 + 停机终止在途备份 (CON-04) ([#709](https://github.com/shi00/qTrading/issues/709)) ([8c70d82](https://github.com/shi00/qTrading/commit/8c70d82f0970e08f0bfe8b4205c0bd742f328197))
* **financial:** drop unreachable rn_version/rn_period cleanup to restore diff-coverage ([a97c532](https://github.com/shi00/qTrading/commit/a97c532be6314d8caf4982766c47b8717f438918))
* **governance:** R18 决策树增加非 Git 环境降级路径（AI 可执行性检视 F-02） ([c54ca54](https://github.com/shi00/qTrading/commit/c54ca545835ad7a4e52fb2dd7eaa4f633cd9efd5))
* **governance:** R5 由 INVARIANT 降为 EXCEPTIONABLE 并登记既有偏离 (P1-04) ([e84aec1](https://github.com/shi00/qTrading/commit/e84aec16aaeb320569460f812022095b705ab862))
* **governance:** R5 由 INVARIANT 降为 EXCEPTIONABLE 并登记既有偏离(P1-04) ([9cff63c](https://github.com/shi00/qTrading/commit/9cff63c7521030c6ed7bde41191478b337b93951))
* **governance:** 治理 ID 门禁扩展扫描范围并补登记未登记 ID（F-04） ([a280cae](https://github.com/shi00/qTrading/commit/a280cae5320ce6dd30bed4a6169e51de7858d79a))
* **governance:** 治理 ID 门禁扩展扫描范围并补登记未登记 ID（F-04） ([dfe4810](https://github.com/shi00/qTrading/commit/dfe481006866391c756066e0d371ac1e7da6af59))
* **governance:** 澄清删除边界并补充 AGENTS.md 任务路由（AI 可执行性检视 F-03/F-06/F-07/F-08） ([d015269](https://github.com/shi00/qTrading/commit/d0152697bc0c7814c7606bd8f083b35b5f6941b2))
* **i18n:** task_manager 硬编码 Waiting/Starting 改 Message (D7-1) ([e7081bc](https://github.com/shi00/qTrading/commit/e7081bc1e7df34b471a96c749531293cb006ce8d))
* **i18n:** task_manager 硬编码 Waiting/Starting 改 Message (D7-1) ([36c4de6](https://github.com/shi00/qTrading/commit/36c4de65a3ba80c2336ca5251a596c1d3a5f6d23))
* **i18n:** 补齐 D5-4 缺失 ai_prompt_too_long 键(D5-4) ([a50cb81](https://github.com/shi00/qTrading/commit/a50cb8116823898f27cb3e4f7aefe08e18dc0a96))
* **i18n:** 补齐 D5-4 缺失的 ai_prompt_too_long 键(D5-4) ([09761ab](https://github.com/shi00/qTrading/commit/09761ab18c8aa54eb194b53b9dc9fd053adddaf9))
* **i18n:** 调度器调度期 I18n.get 改 Message (D7-2) ([e3c322a](https://github.com/shi00/qTrading/commit/e3c322a580151889277ac3b485fc306a18a3ffaa))
* **i18n:** 调度器调度期 I18n.get 改 Message (D7-2) ([b9497e1](https://github.com/shi00/qTrading/commit/b9497e1a66a7e6739f21ba0f1c0a0863221ce182))
* **lifecycle:** configure explicit cleanup timeouts in startup rollback (CON-11) ([#761](https://github.com/shi00/qTrading/issues/761)) ([4744a15](https://github.com/shi00/qTrading/commit/4744a15da0938ea7b0080a80bbfaac1870dc0770))
* **lifecycle:** protect service rollback with shield and wait on startup failure (CON-09) ([#762](https://github.com/shi00/qTrading/issues/762)) ([58df011](https://github.com/shi00/qTrading/commit/58df0117a5081161ed211af0f57194b507ffad2a))
* **log:** 修复初始化时序、listener TypeError 与 i18n 缺失翻译 ([#511](https://github.com/shi00/qTrading/issues/511)) ([da50544](https://github.com/shi00/qTrading/commit/da505446cfe2a96a8f007ebd8842b01051657c1d))
* **loop-local:** 消除 store 创建竞态与 fallback 跨循环迁移 (review 02 CON-02/CON-11) ([#691](https://github.com/shi00/qTrading/issues/691)) ([57adfe9](https://github.com/shi00/qTrading/commit/57adfe9d8685639ac82fe231cbd238cb370a6d1c))
* **migration:** 消除 0023 撞号，screening_history 迁移改为 0024 串接 ([#893](https://github.com/shi00/qTrading/issues/893)) ([b47bd95](https://github.com/shi00/qTrading/commit/b47bd9524b73003e0ae8dba16a25c21242b5f6f8))
* **news:** 修复新闻告警跨线程 UI 变更引起的 asyncio TypeError ([#513](https://github.com/shi00/qTrading/issues/513)) ([5b51fc2](https://github.com/shi00/qTrading/commit/5b51fc2b71158c714ab97b61964c3c52e6da05b8))
* **perf:** 启动导入面门禁 + 写库定基 + 阈值校准 (PRF-07) ([#778](https://github.com/shi00/qTrading/issues/778)) ([f18cd3e](https://github.com/shi00/qTrading/commit/f18cd3e7dfdc91c6bb06808d7049d9d230386c10))
* **r06-f4:** keyring 静默降级改为显式告警并提升日志级别 ([#684](https://github.com/shi00/qTrading/issues/684)) ([a874919](https://github.com/shi00/qTrading/commit/a8749190f1fbdb34b6673138f26041fbc718617e))
* **rate-limiter:** TokenBucket 原子预留避免余额转负击穿限流 (D1-5) ([#807](https://github.com/shi00/qTrading/issues/807)) ([9a59cb6](https://github.com/shi00/qTrading/commit/9a59cb6a2a6e0ca3260049d682c00383ca750fea))
* **redlines:** 统一 check_redlines 覆盖范围描述与 hook 名称（GDR-05） ([#787](https://github.com/shi00/qTrading/issues/787)) ([363ef01](https://github.com/shi00/qTrading/commit/363ef0120f942b4a5e77fc631b1d69e4934d1708))
* **review05-e18:** consolidate DAO slow-query thresholds into PerfThreshold ([#673](https://github.com/shi00/qTrading/issues/673)) ([a23cc6e](https://github.com/shi00/qTrading/commit/a23cc6ef1547e9abf93afe5dd45a457f744f2fef))
* **review05-e4:** move InitSyncError out of UI layer to data/sync ([#675](https://github.com/shi00/qTrading/issues/675)) ([e7734cf](https://github.com/shi00/qTrading/commit/e7734cf6ff12b458e5043ef58d5e5f6b022b442f))
* **review05-e7:** demote home_view render-time info logs to debug ([#676](https://github.com/shi00/qTrading/issues/676)) ([8f96970](https://github.com/shi00/qTrading/commit/8f969705ec21e884c0aa31977d26d6479e49dfaa))
* **review05-e9:** sanitize file paths in logged tracebacks ([#677](https://github.com/shi00/qTrading/issues/677)) ([71c140c](https://github.com/shi00/qTrading/commit/71c140c632f8aedc73dc46abf10e9e1d507c35ab))
* **review:** backfill stale T+5 horizon returns (D2-4) ([#822](https://github.com/shi00/qTrading/issues/822)) ([b9cf0f4](https://github.com/shi00/qTrading/commit/b9cf0f40d3aadb49b7c823cdefec7c17649b0bc7))
* **review:** offline trading calendar trusted interval to stop future-date guessing (D2-7) ([#817](https://github.com/shi00/qTrading/issues/817)) ([3bdaa14](https://github.com/shi00/qTrading/commit/3bdaa145b319cca6298d1110aa8958e99e4f43c7))
* **review:** report critical data source failure in UI daily sync (D1-2) ([#819](https://github.com/shi00/qTrading/issues/819)) ([4a02811](https://github.com/shi00/qTrading/commit/4a02811dc1c384a2b6fbc682ab08d545704cf08e))
* **review:** unify benchmark index single-source to CSI 000985 with persistence (D2-5) ([#816](https://github.com/shi00/qTrading/issues/816)) ([59037eb](https://github.com/shi00/qTrading/commit/59037eb5aaa4009f4781424a1a1532f59fd01c78))
* **review:** unify quality-gate entry via require_quality(from_attr) on PolarsBaseStrategy (D2-8) ([#818](https://github.com/shi00/qTrading/issues/818)) ([59b0aac](https://github.com/shi00/qTrading/commit/59b0aac8047490e27509cbdf1e5c534f1db94b9d))
* **review:** use adjusted price and real trading-day T+N for review returns (D2-3) ([#814](https://github.com/shi00/qTrading/issues/814)) ([076aa82](https://github.com/shi00/qTrading/commit/076aa829a698311f84badf445a1be8655f83736f))
* **sanitize:** register authenticated proxy credentials with DataSanitizer (review06-F11) ([#682](https://github.com/shi00/qTrading/issues/682)) ([2750276](https://github.com/shi00/qTrading/commit/275027664042f5af8d46c8e37f3e4a76ee4e745a))
* **sanitizers:** add bare token fallback regex for review05-E8 ([#669](https://github.com/shi00/qTrading/issues/669)) ([28cb692](https://github.com/shi00/qTrading/commit/28cb692ae73f67e453065eff6f5221492502e3c8))
* **scheduler:** add misfire catch-up mechanism and offline calendar fallback (D6-1, D6-2) ([d297381](https://github.com/shi00/qTrading/commit/d29738166814c9f6490f510cc48de92672d74246))
* **scheduler:** add misfire catch-up mechanism and offline calendar fallback (D6-1, D6-2) ([2f147c2](https://github.com/shi00/qTrading/commit/2f147c213a7778f6599212b38bf2161e295e2c1c))
* **scheduler:** check submit_task return value and warn when task not submitted (D6-5) ([763d724](https://github.com/shi00/qTrading/commit/763d724dfe2cf0524daaadaf8a10d9646e89a858))
* **scheduler:** check submit_task return value and warn when task not submitted (D6-5) ([cdbeb81](https://github.com/shi00/qTrading/commit/cdbeb816687c04e01d39f17ee646388f2b9d7632))
* **scheduler:** enforce required job registration at startup and fail fast (D6-6) ([2c285b9](https://github.com/shi00/qTrading/commit/2c285b9dafffad13084b2af5c7df05045fd90dc7))
* **scheduler:** enforce required job registration at startup and fail fast (D6-6) ([4b2f27d](https://github.com/shi00/qTrading/commit/4b2f27d09ddc91025051014452467c5cf9069d81))
* **screener:** 三分区空卡不参与 expand, 恢复结果行可见/可点 (D7-3 e2e) ([fd4e862](https://github.com/shi00/qTrading/commit/fd4e862864323b2ffea3611cefc321d60952f235))
* **screener:** 仅渲染有数据分区恢复 1280x720 结果行可见 (D7-3 e2e) ([cf785df](https://github.com/shi00/qTrading/commit/cf785df9931e267724336ddc4596c2b6a501eaf8))
* **screening:** 历史记录主键改为覆盖语义，避免复盘重复计入 (LIFE-03) ([#887](https://github.com/shi00/qTrading/issues/887)) ([7772e95](https://github.com/shi00/qTrading/commit/7772e954f115367df5da1bcdfb381e95809e715b))
* **security:** DPAPI-encrypt embedded PostgreSQL password file on Windows (review 06 F5) ([#685](https://github.com/shi00/qTrading/issues/685)) ([8ff25f3](https://github.com/shi00/qTrading/commit/8ff25f399d9ea842cd08a0b5e670521dc18f90e2))
* **security:** F14 本地模型路径规范化 + GGUF 魔数校验 ([#688](https://github.com/shi00/qTrading/issues/688)) ([585d945](https://github.com/shi00/qTrading/commit/585d9451feb3d85df41792a4548d0f6c6f88d8a6))
* **security:** purge legacy plaintext key files after credential migration (review 06 F3) ([#683](https://github.com/shi00/qTrading/issues/683)) ([0430a53](https://github.com/shi00/qTrading/commit/0430a53c22cd7e03049dc2f8227cac4ae2e52c43))
* **security:** 用户数据目录与程序资源目录分离（D8-4） ([9244353](https://github.com/shi00/qTrading/commit/92443538604596b566b5636e7963d30d4359c7f6))
* **security:** 用户数据目录与程序资源目录分离（D8-4） ([55437ab](https://github.com/shi00/qTrading/commit/55437abaa094badbb0d3b851ded77a9018bb39b9))
* **security:** 禁止跨供应商 failover 复用全局 API key（D8-1） ([6e42be7](https://github.com/shi00/qTrading/commit/6e42be7a909bec2df5d101e52918ddb1f387c178))
* **security:** 禁止跨供应商 failover 复用全局 API key（D8-1） ([4d48051](https://github.com/shi00/qTrading/commit/4d4805118b76cc5fbfd1f56f32f29ff6cc5e9862))
* **services:** news_subscription_service 接入 classify_error 统一错误处理（P3-M9） ([fff3bce](https://github.com/shi00/qTrading/commit/fff3bceca3ff41cd2a9aada1f98459214b9858ec))
* **services:** NewsSubscriptionService.processing_queue 改为动态 loop-local property (CON-07) ([#760](https://github.com/shi00/qTrading/issues/760)) ([13c0d6f](https://github.com/shi00/qTrading/commit/13c0d6fe58af19aa36a507a4d1e4d58b47e34537))
* **services:** review03 DAT-07 hashlib.md5 标注 usedforsecurity=False 消除弱哈希告警噪声 ([#700](https://github.com/shi00/qTrading/issues/700)) ([d6de443](https://github.com/shi00/qTrading/commit/d6de4431ea214e32843fccdb40b280698a06ffbe))
* **services:** sidecar 调用 TimeoutExpired 包装为业务异常（review03-C19） ([#558](https://github.com/shi00/qTrading/issues/558)) ([8a6f778](https://github.com/shi00/qTrading/commit/8a6f778ae364e27fd27234348d39bb4c43d84e55))
* **shutdown:** calibrate Step 0 budget and bound persist timeout in TaskManager (CON-03) ([#774](https://github.com/shi00/qTrading/issues/774)) ([cc46b08](https://github.com/shi00/qTrading/commit/cc46b08b012aaf188d321b36e8ca075a9970bc69))
* **shutdown:** calibrate Step 0 join budget and align do_cleanup defaults (CON-03) ([#777](https://github.com/shi00/qTrading/issues/777)) ([180e323](https://github.com/shi00/qTrading/commit/180e323db0d057cc07602f85f0354daafebdbcc0))
* **shutdown:** translate internal flush timeout to runtime error with diagnostics (CON-10) ([#763](https://github.com/shi00/qTrading/issues/763)) ([453b773](https://github.com/shi00/qTrading/commit/453b77352c847f6b56f38e92ef12ddf75f04da25))
* **sidecar:** support dynamic username in pg_hba template and update postgresql pin ([#495](https://github.com/shi00/qTrading/issues/495)) ([781a2a8](https://github.com/shi00/qTrading/commit/781a2a82393f19abee12ce421ef3aab90fbf0a5f))
* **strategies:** _reset_singleton 恢复策略注册表真实快照防测试污染（P3-M10） ([8243b1e](https://github.com/shi00/qTrading/commit/8243b1e9cad42da5e5e2d89773a1938272205464))
* **strategies:** add BRONZE tier warning for PolarsBaseStrategy (issue [#93](https://github.com/shi00/qTrading/issues/93)) ([#490](https://github.com/shi00/qTrading/issues/490)) ([72e77c3](https://github.com/shi00/qTrading/commit/72e77c34e1d29185d3ad0b63f45243e9fc0658ac))
* **strategies:** 孤儿新闻任务清理改用 gather_for_shutdown_cleanup（review02-B1） ([#568](https://github.com/shi00/qTrading/issues/568)) ([137556e](https://github.com/shi00/qTrading/commit/137556e13304a082d11bfc2d888e27268bf12b6e))
* **strategies:** 快照捕获直读内部注册表绕过可 mock 入口防空快照污染（M10 回归） ([#544](https://github.com/shi00/qTrading/issues/544)) ([7636361](https://github.com/shi00/qTrading/commit/763636187ec4e41dec27d4eafb7f7b7ed4844cae))
* **strategies:** 统一金额阈值单位换算修复 DATA-01/02 与 total_mv_min ([82b5cd0](https://github.com/shi00/qTrading/commit/82b5cd073beab13cb84b578f4421a2f5271a9c46))
* **strategies:** 统一金额阈值单位换算修复 DATA-01/02 与 total_mv_min ([7c32849](https://github.com/shi00/qTrading/commit/7c328493191b983414f359be8a4ec92eec043d36))
* **strategy:** correct RSI direction and boundary masking (D3-1) ([#821](https://github.com/shi00/qTrading/issues/821)) ([5c3dd37](https://github.com/shi00/qTrading/commit/5c3dd37c91c22bff01f712c41752b8b5e879325e))
* **strategy:** reject contradictory range params instead of silent empty results (D3-3) ([#824](https://github.com/shi00/qTrading/issues/824)) ([1c0c73c](https://github.com/shi00/qTrading/commit/1c0c73cceab11cde7d32ab63fed14cf84efd83be))
* **strategy:** surface VolumeBreakout param auto-adjust warning to user (D3-4) ([#825](https://github.com/shi00/qTrading/issues/825)) ([2bebaff](https://github.com/shi00/qTrading/commit/2bebaffb3a6c137ffad5c0b00a351868c58d10ba))
* **sync:** propagate SyncResult completeness to scheduler idempotency (D1-2) ([#805](https://github.com/shi00/qTrading/issues/805)) ([0679966](https://github.com/shi00/qTrading/commit/06799660d3f2b13e12afad76911ce9f5daecb901))
* **sync:** split SyncResult days_processed and rows_written semantics (D1-4) ([#806](https://github.com/shi00/qTrading/issues/806)) ([2eca5b2](https://github.com/shi00/qTrading/commit/2eca5b20a48f0ee5231b55c87dce1f7bd9b8e88c))
* **task-manager:** allow retrying INTERRUPTED tasks to resume sync (D6-3) ([dde1f0e](https://github.com/shi00/qTrading/commit/dde1f0e39fbbe03e23fbd2ec4959a0dfb1d2d823))
* **task-manager:** allow retrying INTERRUPTED tasks to resume sync (D6-3) ([887348e](https://github.com/shi00/qTrading/commit/887348ea2e6d22b244781a9e6f87c92fd7781ceb))
* **task-manager:** hide retry button for tasks without coroutine factory (D6-7) ([dbee032](https://github.com/shi00/qTrading/commit/dbee032d7092cc1b97208e1385087a623b882db1))
* **task-manager:** LIFE-01 崩溃后重试中断任务的 factory 注册表机制 ([#889](https://github.com/shi00/qTrading/issues/889)) ([83ec9f9](https://github.com/shi00/qTrading/commit/83ec9f9f5ab9ded2f32173906f7a3de6cfc9daef))
* **task-manager:** use dirty-flag deferred flush to prevent progress notification starvation (D6-4) ([1aff832](https://github.com/shi00/qTrading/commit/1aff8327d75e5a5dbf4eefca479f42118e53f6da))
* **task-manager:** use dirty-flag deferred flush to prevent progress notification starvation (D6-4) ([4e4d6c1](https://github.com/shi00/qTrading/commit/4e4d6c1f6170acd4f6468151823fa7356fdc1bec))
* **task-manager:** 历史任务无 factory 时隐藏重试按钮，避免无效入口 (D6-7) ([d7f29ff](https://github.com/shi00/qTrading/commit/d7f29ffcecc3a99dfa3b5cdcde898f920981187f))
* **task-manager:** 持久化单调序号防止乱序快照覆盖终态 (LIFE-02) ([#892](https://github.com/shi00/qTrading/issues/892)) ([72e04f6](https://github.com/shi00/qTrading/commit/72e04f69175cdd8f734e34e550e3cf543788bfc2))
* **task-manager:** 清理 _finished_order 陈旧键，避免虚占淘汰名额 (LIFE-04) ([b680934](https://github.com/shi00/qTrading/commit/b680934aeeb7a9ae535ca25cfb9a84f8ca2b4454))
* **task-manager:** 清理 _finished_order 陈旧键，避免虚占淘汰名额(LIFE-04) ([4514ea6](https://github.com/shi00/qTrading/commit/4514ea6dd7933cea3d803eb003e9a7adf6cc17b7))
* **task:** 淘汰完成时任务移入 _history 保留在界面 (LIFE-05) ([#886](https://github.com/shi00/qTrading/issues/886)) ([f6e0189](https://github.com/shi00/qTrading/commit/f6e018980f853a186eddc52ca30c58f4d52d543c))
* **test:** DAT-06 ann_date NULL 测试类加 xdist_group(serial) 消除并行竞态 ([1703186](https://github.com/shi00/qTrading/commit/1703186f480d6e443bf6d2eacad7c23e67710214))
* **test:** DAT-06 ann_date NULL 测试类加 xdist_group(serial) 消除并行竞态 ([005d7ed](https://github.com/shi00/qTrading/commit/005d7edf3eb318a2a7858dbbd78efc94c2db7337))
* **test:** force UTF-8 IO in firstframe min-import subprocess (PRF-02) ([9cb3dfe](https://github.com/shi00/qTrading/commit/9cb3dfe1c1ab6e267f01f4a99302bc35cb1fd2de))
* **test:** force UTF-8 IO in firstframe min-import subprocess (PRF-02) ([4300f52](https://github.com/shi00/qTrading/commit/4300f52a794fcd945a935e02a8116ddfe49e4e59))
* **test:** mock NewsFetcher.get_hot_concepts 避免 CI Linux worker 真实网调崩溃 ([#525](https://github.com/shi00/qTrading/issues/525)) ([5301506](https://github.com/shi00/qTrading/commit/53015063720693ba469725f544f6bf5c1f162ea0))
* **test:** reset OfflineCalendar class cache after each test (R7) ([064f304](https://github.com/shi00/qTrading/commit/064f304f250872524ae1dbf9ec0a4a2ff769b9f7))
* **test:** test_repair_financial_data 清理 repair 写入残留，消除 DAT-06 集成测试 flaky ([60c6394](https://github.com/shi00/qTrading/commit/60c639460872500ea1b5820d180b9dfc4b5690bc))
* **test:** test_repair_financial_data 清理 repair 写入残留，消除 DAT-06 集成测试 flaky ([3708596](https://github.com/shi00/qTrading/commit/37085968c8314befdabd69cb11bfe0438226f9ad))
* **test:** 隔离 security_utils 测试文件系统 IO，消除 CI 环境相关 flaky ([217b5c8](https://github.com/shi00/qTrading/commit/217b5c85e0fc6af247011137ee10f07a09888310))
* **theme:** CARD_BG 纳入 CUSTOM_COLOR_PRESETS，消除 Layer 2 游离随主题切换 ([#635](https://github.com/shi00/qTrading/issues/635)) ([f67d5ca](https://github.com/shi00/qTrading/commit/f67d5ca592f5abd5709c047c1d265fa5ee0990fd))
* tushare token 验证改为事件循环线程 await set_token_async ([#496](https://github.com/shi00/qTrading/issues/496)) ([03ad606](https://github.com/shi00/qTrading/commit/03ad6065c9c78d1d3da75bbd56b8996a569c0133))
* **ui:** 4 个 ViewModel 懒异步构造 DataProcessor 避免阻塞主线程 (R16) ([#556](https://github.com/shi00/qTrading/issues/556)) ([7dabdca](https://github.com/shi00/qTrading/commit/7dabdca1f90805c36e36812d9847ffbb93c25d80))
* **ui:** 4 个 ViewModel 懒异步构造 DataProcessor 避免阻塞主线程 (R16) ([#567](https://github.com/shi00/qTrading/issues/567)) ([edbe4b6](https://github.com/shi00/qTrading/commit/edbe4b61041ee55d67923eb62014e8271e14ac51))
* **ui:** anchor tushare verify button for stable E2E click ([#681](https://github.com/shi00/qTrading/issues/681)) ([3d26231](https://github.com/shi00/qTrading/commit/3d26231492706b729bfc538bfa11801267f1d12e))
* **ui:** app_layout Stack 显式 fit=StackFit.EXPAND 防止内容区塌缩 ([#524](https://github.com/shi00/qTrading/issues/524)) ([4bdcd4a](https://github.com/shi00/qTrading/commit/4bdcd4abc2f1530378f017dee082ddd291c10f3d))
* **ui:** baseline §2.3 error/Toast API 修正 + Toast duration 下限 10s（UIX-11） ([#743](https://github.com/shi00/qTrading/issues/743)) ([ed044f4](https://github.com/shi00/qTrading/commit/ed044f481db722044029f2de1b7c557751b99969))
* **ui:** cache_cleared 从 pubsub 迁移到 Observable 信号源，消除同 topic 退订误伤（UIX-01） ([#740](https://github.com/shi00/qTrading/issues/740)) ([625a8ea](https://github.com/shi00/qTrading/commit/625a8eab865a62ee25789b0ddec921db2cdd48c3))
* **ui:** CON-03 跨线程通知单调序号丢弃过期快照 ([#692](https://github.com/shi00/qTrading/issues/692)) ([12a628f](https://github.com/shi00/qTrading/commit/12a628fa0f6333a341ce4118c44f624654acee39))
* **ui:** conftest autouse 重置 ToastManager 模块态 + mvvm 状态归属决策表 (UIX-05 批次3-C1) ([#747](https://github.com/shi00/qTrading/issues/747)) ([68a4b33](https://github.com/shi00/qTrading/commit/68a4b333213cb4db270c80f72734a216e20e2e93))
* **ui:** D17 语言保存失败触发 locale 重渲染回滚（harness-review MINOR） ([#611](https://github.com/shi00/qTrading/issues/611)) ([b53dd23](https://github.com/shi00/qTrading/commit/b53dd23976e100ed4028c007e82f38f92dd8cdda))
* **ui:** database_status VM 同步读配置 offload 到 IO 线程池（review02-B13） ([#571](https://github.com/shi00/qTrading/issues/571)) ([d03ca9a](https://github.com/shi00/qTrading/commit/d03ca9a1265a0fc6d788624dd8298e4d1e111f21))
* **ui:** HomeView 失活后经 effect cleanup 退订 service listener，修复失活后反复重渲染 ([#523](https://github.com/shi00/qTrading/issues/523)) ([dd6205d](https://github.com/shi00/qTrading/commit/dd6205d49f0193d39676f08412517a632df02249))
* **ui:** LLM 配置面板外发警告去重并修复 checkbox 标签截断 ([#508](https://github.com/shi00/qTrading/issues/508)) ([5147dd3](https://github.com/shi00/qTrading/commit/5147dd39bf5c962b0ac025a7e0f4d96b150a7fcc))
* **ui:** make Dropdown width self-adaptive based on option text content ([#503](https://github.com/shi00/qTrading/issues/503)) ([676d296](https://github.com/shi00/qTrading/commit/676d296abe6394c7eebb4ebcdda94bcff4d38a31))
* **ui:** migrate bare ft.Colors usages to AppColors token abstraction (UIX-15) ([#780](https://github.com/shi00/qTrading/issues/780)) ([8fd70e5](https://github.com/shi00/qTrading/commit/8fd70e54941bd06fd90b6fa5637956f179845156))
* **ui:** OnboardingViewModel step_validated 改只读方法防可变 dict 暴露 ([#539](https://github.com/shi00/qTrading/issues/539)) ([f1c4178](https://github.com/shi00/qTrading/commit/f1c4178c1a60adfdf4b7a05c260b2847b7dd1d3a))
* **ui:** PaginatedTable 恢复水平滚动+sticky header+列宽拖拽+hover 局部化 (方案 D-v3) ([#529](https://github.com/shi00/qTrading/issues/529)) ([cea77fa](https://github.com/shi00/qTrading/commit/cea77fa418dbc785efefec295c00e567f6df50df))
* **ui:** screener AI prompt 校验失败改为 inline 错误 (D19) ([#667](https://github.com/shi00/qTrading/issues/667)) ([05b2d3f](https://github.com/shi00/qTrading/commit/05b2d3ff45a289804a0ebac2b772e59244eecd21))
* **ui:** Screener state 不可变加固 Mapping/_realtime_snapshot frozen (批次3-C2c UIX-06) ([#748](https://github.com/shi00/qTrading/issues/748)) ([898d107](https://github.com/shi00/qTrading/commit/898d1073f820bcbc240d4371d053ed9b8ed38b23))
* **ui:** SectionHeader 冻结组件不可改 visible，改为条件渲染 ([#507](https://github.com/shi00/qTrading/issues/507)) ([de06dc1](https://github.com/shi00/qTrading/commit/de06dc1c5cb475041b85a60ccac88b54b6d2641b))
* **ui:** SQL 控制台输入框内层 Column 添加 STRETCH 对齐修复宽度不成比例 ([fa1e54f](https://github.com/shi00/qTrading/commit/fa1e54f2dd6863198159c878d8bb33b52680f721))
* **ui:** surface data-view errors to user + explicit toast (review05-E20) ([#680](https://github.com/shi00/qTrading/issues/680)) ([0c1f25d](https://github.com/shi00/qTrading/commit/0c1f25debfe820ed327a1d8f391fc7bc472887f0))
* **ui:** ToastCard cleanup 将 concurrent.futures.Future 桥接为 asyncio.Future 修复 CRITICAL 异常 (P0-1) ([#540](https://github.com/shi00/qTrading/issues/540)) ([7c37af8](https://github.com/shi00/qTrading/commit/7c37af8cd49d49347d37690df9a4d42cc91dfc13))
* **ui:** Tushare token 验证增加 wait_for 超时保护并复用超时倍率常量 ([#506](https://github.com/shi00/qTrading/issues/506)) ([467a46f](https://github.com/shi00/qTrading/commit/467a46f13f391ccc180d0a62fa4c728499280c00))
* **ui:** use_viewmodel deps 改为 resolved_vm 对象身份，外部实例变化自动重订阅（UIX-04） ([#739](https://github.com/shi00/qTrading/issues/739)) ([385963f](https://github.com/shi00/qTrading/commit/385963f1d3c2c75f8fde8b0d6a29bfa764c353f8))
* **ui:** use_viewmodel 订阅后补首帧补偿同步，消除订阅前变更丢失（UIX-03） ([#738](https://github.com/shi00/qTrading/issues/738)) ([ae14c9f](https://github.com/shi00/qTrading/commit/ae14c9f84499af6a8e5af30a9b3f399121955177))
* **ui:** UX 检视修复核心 - SliderInput + 单股重试 + AI 长度配置 ([#493](https://github.com/shi00/qTrading/issues/493)) ([f82ef4f](https://github.com/shi00/qTrading/commit/f82ef4fc97d4d42730c82f3e1e0c0b4e8630d8df))
* **ui:** UX-08 指数无数据空态 — 占位渲染替代固定空卡 ([#641](https://github.com/shi00/qTrading/issues/641)) ([43ae076](https://github.com/shi00/qTrading/commit/43ae076d56c1df0979e1e3f76efb44d1612b520e))
* **ui:** UX-11 表格键盘与读屏语义 - 排序表头 Semantics 三态标注 + 行高 32 ([#655](https://github.com/shi00/qTrading/issues/655)) ([054f0aa](https://github.com/shi00/qTrading/commit/054f0aac25cea37750d079ecc8e1548a3af30cb2))
* **ui:** 主表单 Enter 提交 — 单行表单 on_submit 绑定主动作（UX-09） ([#643](https://github.com/shi00/qTrading/issues/643)) ([93bb9cd](https://github.com/shi00/qTrading/commit/93bb9cdd45e645555c48f4db6dc3d1b145c9f4a4))
* **ui:** 修复 data_source_tab 对话框条件调用 use_dialog 导致取消按钮失效 ([#532](https://github.com/shi00/qTrading/issues/532)) ([909c6ea](https://github.com/shi00/qTrading/commit/909c6eaed3e5dcefc4fe01f6bb497515a4a740b2))
* **ui:** 修复 Tushare Token 验证提示未接入 i18n (R18 worktree 隔离) ([#531](https://github.com/shi00/qTrading/issues/531)) ([977b456](https://github.com/shi00/qTrading/commit/977b45667376179254ec1c3c98d07677bc7e86aa))
* **ui:** 向导步骤标题去除「步骤 N」前缀，并移除重复的 AI 外发说明 ([#510](https://github.com/shi00/qTrading/issues/510)) ([8d39f76](https://github.com/shi00/qTrading/commit/8d39f7647470ed573e126a9838698ad5fcb74743))
* **ui:** 回测报告摘要标注回测数据口径 tradeoff 并登记 accepted tradeoff（DAT-07① + DAT-08②） ([#717](https://github.com/shi00/qTrading/issues/717)) ([31209eb](https://github.com/shi00/qTrading/commit/31209eb27cbcb8ce96bd8ac9da34b7419a2c9d8b))
* **ui:** 将表单字段错误关联至 TextField.error 原生插槽 (UIX-12) ([#754](https://github.com/shi00/qTrading/issues/754)) ([b2330fc](https://github.com/shi00/qTrading/commit/b2330fcf218f90f7d142dfa5055dc6ab87633b71))
* **ui:** 并发模式状态消息翻译注入同级参数填充占位符 (D5-6) ([d77c393](https://github.com/shi00/qTrading/commit/d77c3930fd9ebbace57cdb29045cdd77d840f0a0))
* **ui:** 并发模式状态消息翻译注入同级参数填充占位符 (D5-6) ([7896842](https://github.com/shi00/qTrading/commit/7896842754952ebcb71040aad53c294c5de1bd6a))
* **ui:** 智能向导底部导航条底色 SURFACE-&gt;BACKGROUND 消除配色断层 ([#545](https://github.com/shi00/qTrading/issues/545)) ([2556c92](https://github.com/shi00/qTrading/commit/2556c921087031cc41928230a2bff19b14020368))
* **ui:** 消除 Flet 组件渲染期顶层日志并新增静态红线门禁 (UIX-10) ([#752](https://github.com/shi00/qTrading/issues/752)) ([6e062a2](https://github.com/shi00/qTrading/commit/6e062a2de2366cedee9b150e8b98e301a7f4b633))
* **ui:** 消除 VM 层残留中文与中文异常匹配并规范化 i18n (UIX-17) ([#755](https://github.com/shi00/qTrading/issues/755)) ([7fe0e26](https://github.com/shi00/qTrading/commit/7fe0e26e53432489ec2dcc2b4bdf6b50954d2fd5))
* **ui:** 清理 ScreenerViewModel clear_filters 死代码与悬空测试及 i18n key (UIX-19) ([#751](https://github.com/shi00/qTrading/issues/751)) ([1c7bb17](https://github.com/shi00/qTrading/commit/1c7bb17cd526a9f3849eef7069b77c4ec6ae1d82))
* **ui:** 窗口启动最大化自适应 + 引导欢迎页布局修复 ([#501](https://github.com/shi00/qTrading/issues/501)) ([293a0ca](https://github.com/shi00/qTrading/commit/293a0ca25a003e10107257a63711a10978a41730))
* **ui:** 统一页面标题字号为 FONT_SIZE_XL ([#445](https://github.com/shi00/qTrading/issues/445)) ([#488](https://github.com/shi00/qTrading/issues/488)) ([47a8412](https://github.com/shi00/qTrading/commit/47a84123370918101c8fb0dde203589b52dd06b5))
* **ui:** 首页实时市场快讯改为整页统一滚动，修复新闻区被压缩仅显示一条 ([#509](https://github.com/shi00/qTrading/issues/509)) ([7c87396](https://github.com/shi00/qTrading/commit/7c87396e69def9fe92b0b21e68b4022907bbbb1c))
* **utils:** atexit 兜底与优雅停机主从关系显式化（review02-B2+B10） ([#569](https://github.com/shi00/qTrading/issues/569)) ([002b0f0](https://github.com/shi00/qTrading/commit/002b0f020a6221d14dd7feb15c340b78a590912e))
* **utils:** reload_config 旧池 drain 竞态改用 wait=False 务实方案（review02-B5） ([#575](https://github.com/shi00/qTrading/issues/575)) ([d991cde](https://github.com/shi00/qTrading/commit/d991cde3a04d15b1a1a4bde5e5ee9a3eec492f2a))
* **utils:** reset_all_singletons 复位 atexit 模块级标志防止跨测试泄漏 (R7) ([#590](https://github.com/shi00/qTrading/issues/590)) ([7ceb98e](https://github.com/shi00/qTrading/commit/7ceb98e2462980e11fc5d8d1bb6ec3d11ce05f40))
* **utils:** run_async 去除 unittest.mock 生产热路径检测（CON-06） ([#733](https://github.com/shi00/qTrading/issues/733)) ([5ff1686](https://github.com/shi00/qTrading/commit/5ff168622628e552bacb3f5e806efef3445fc25f))
* **utils:** Step 8 停止 PG 超时日志可观测性增强（review02-B9） ([#578](https://github.com/shi00/qTrading/issues/578)) ([999098b](https://github.com/shi00/qTrading/commit/999098b7dbaab661d90fc8d38b6677538c5ca33c))
* **utils:** ThreadPoolManager worker 数存实例字段，去除私有 _max_workers 访问（CON-10） ([#732](https://github.com/shi00/qTrading/issues/732)) ([6ec3845](https://github.com/shi00/qTrading/commit/6ec38458cc42d717fea52ed1011fb10fe4a488be))
* **utils:** 移除 ThreadPoolManager._reset_singleton 无效类属性死代码 (CON-13) ([#759](https://github.com/shi00/qTrading/issues/759)) ([b1caeb9](https://github.com/shi00/qTrading/commit/b1caeb9ffce8e0f8e8c1e7542a6309a56e9f32b3))
* 关闭确认对话框改为普通工厂修复 No current renderer 错误 ([#497](https://github.com/shi00/qTrading/issues/497)) ([94d10e9](https://github.com/shi00/qTrading/commit/94d10e905e9ea02fc4d757ec3b866e04dc4d32ef))


### Miscellaneous

* **ci:** bump h2 to 0.4.16 to fix cargo audit RUSTSEC-2026-0258 ([#534](https://github.com/shi00/qTrading/issues/534)) ([b31df06](https://github.com/shi00/qTrading/commit/b31df068991a1ddf5c1c09f232be5bea45290430))
* **deps:** bump the actions group across 1 directory with 3 updates ([#528](https://github.com/shi00/qTrading/issues/528)) ([b4cfb19](https://github.com/shi00/qTrading/commit/b4cfb194dd5067fb44e2bec2f2d9f89a3507c183))
* **deps:** bump the actions group with 5 updates ([#533](https://github.com/shi00/qTrading/issues/533)) ([c985ea5](https://github.com/shi00/qTrading/commit/c985ea509700a8c8ce1d15c614a96db0e81cd40e))
* **deps:** UP-1 batch low-risk patch upgrades ([#514](https://github.com/shi00/qTrading/issues/514)) ([a56f17d](https://github.com/shi00/qTrading/commit/a56f17d3a3c855212390a733fb8758e627874347))
* **deps:** UP-2 akshare 1.18.81 -&gt; 1.18.84 ([#515](https://github.com/shi00/qTrading/issues/515)) ([26d7780](https://github.com/shi00/qTrading/commit/26d7780bf04fb0ddfb13f3e073c01cff065e1853))
* **deps:** UP-3 flet suite 0.86.3 -&gt; 0.86.5 ([#516](https://github.com/shi00/qTrading/issues/516)) ([7197051](https://github.com/shi00/qTrading/commit/7197051c4eefacd423a33d0f00411dd4468dd777))
* **deps:** UP-4.1 polars 1.42.1 -&gt; 1.43.2 评估无 deprecation 泄漏后升级 ([#517](https://github.com/shi00/qTrading/issues/517)) ([49b6fcc](https://github.com/shi00/qTrading/commit/49b6fcc6a07a4f6e6998fdf1b4b2c4f34eb13f29))
* **deps:** UP-4.2 pyarrow 24.0.0 -&gt; 25.0.1 确认无 feather 硬依赖后升级 ([#518](https://github.com/shi00/qTrading/issues/518)) ([90c1346](https://github.com/shi00/qTrading/commit/90c13463d1f4cadaa521b07264d3bf4b50b3afce))
* **deps:** UP-4.3 ruff 0.16 评估技术债 — format 变更面过大暂缓 ([d730566](https://github.com/shi00/qTrading/commit/d7305669c5c9b96e3e77b6c337ad238db02301c3))
* **deps:** UP-4.3 ruff 0.16.2 全库格式化升级 ([#520](https://github.com/shi00/qTrading/issues/520)) ([c4734cd](https://github.com/shi00/qTrading/commit/c4734cd18e01b3f6a18fc6039fb2dec6520d70aa))
* **deps:** UP-4.4 playwright 1.61.0 -&gt; 1.62.0 ([#519](https://github.com/shi00/qTrading/issues/519)) ([393cbeb](https://github.com/shi00/qTrading/commit/393cbebe95ff630a5eb0965266367823cf2182c7))
* **docs:** 移除误合入的根目录方案工作稿并补齐忽略规则 ([#668](https://github.com/shi00/qTrading/issues/668)) ([e3a0a46](https://github.com/shi00/qTrading/commit/e3a0a46fa00b5a387259fcc0b0e1548a38c9a9d7))
* **gitignore:** 忽略 Embedded PostgreSQL 只读数据目录运行产物 ([#526](https://github.com/shi00/qTrading/issues/526)) ([56cc1c1](https://github.com/shi00/qTrading/commit/56cc1c11edbb080b243beb8a4f39fca9718cca28))
* **gitignore:** 忽略 sidecars 本地构建产物 *.exe/*.exe.sha256 ([#631](https://github.com/shi00/qTrading/issues/631)) ([91f3cfb](https://github.com/shi00/qTrading/commit/91f3cfb12a7f0a95c38608d0cf0d4d597ded7d71))
* **security:** renegotiate CVE-2025-69872 allowlist expiry to 2026-11-23 ([#536](https://github.com/shi00/qTrading/issues/536)) ([2b8ce55](https://github.com/shi00/qTrading/commit/2b8ce553722f5879c6d45356e77e4d76ea4214e6))
* 阻止 E2E 临时产物(dll/exe)误提交 ([#504](https://github.com/shi00/qTrading/issues/504)) ([c0497d1](https://github.com/shi00/qTrading/commit/c0497d1edbcb2256353d1ea285d12b3ff3680614))


### Documentation

* **adr:** 修正 ADR-0006 R1/R16 排除论证的内部矛盾（F-05） ([d8d7c50](https://github.com/shi00/qTrading/commit/d8d7c509ebd7a6713ddc85a2d10d2a5c796cc675))
* **adr:** 修正 ADR-0006 R1/R16 排除论证的内部矛盾（F-05） ([5fc2e78](https://github.com/shi00/qTrading/commit/5fc2e787085ad6c7dc6f8a66856fd2e88802db5d))
* **adr:** 澄清检视报告落盘正本与门禁提示（GDR-03） ([#786](https://github.com/shi00/qTrading/issues/786)) ([046af4d](https://github.com/shi00/qTrading/commit/046af4daa8d0dc3775faad211558ffa30ca964f9))
* **adr:** 补充 AGENTS.md 最小安全集 R1/R16 例外说明 (GDR-10) ([#791](https://github.com/shi00/qTrading/issues/791)) ([069ca7d](https://github.com/shi00/qTrading/commit/069ca7d3656672dea507f92708afc7b7203488f6))
* AGENTS.md 从纯指针改为「最小安全集+指针」，新增 ADR-0006 与同步门禁 (DOC-08/13) ([#704](https://github.com/shi00/qTrading/issues/704)) ([6cd814e](https://github.com/shi00/qTrading/commit/6cd814eeb7d38bbac4e0dbc2f22aa1afbe252cdf))
* **agents:** 补录 AGENTS.md 最小安全集入选规则治理说明 ([#729](https://github.com/shi00/qTrading/issues/729)) ([12699ff](https://github.com/shi00/qTrading/commit/12699ffbc24ff8e5a1be938b833c3afb636bea6d))
* **backtest:** clarify cash_reserve_pct semantics and lock reserve floor (D4-10) ([7668a2a](https://github.com/shi00/qTrading/commit/7668a2a99f00e4b032ea1ce993084caeaa460714))
* **backtest:** clarify cash_reserve_pct semantics and lock reserve floor (D4-10) ([809cfe0](https://github.com/shi00/qTrading/commit/809cfe06e90c1bb4f4736e0c6b87032453b2759d))
* **CLAUDE:** §1 前置最高优先级红线摘要区块 ([c6fe283](https://github.com/shi00/qTrading/commit/c6fe283f22309b8de42ecdddc0e43e599405fcd1))
* **CLAUDE:** §1 前置最高优先级红线摘要区块 ([ef6f715](https://github.com/shi00/qTrading/commit/ef6f7157e25011cfc8869fb95499fcb87853a3d6))
* **contributing:** 数据库设置跨平台化——嵌入式 PG 主推 + 外部 createdb 引导 (F4, DOC-02) ([34eb25e](https://github.com/shi00/qTrading/commit/34eb25ebd254cfd0152a20810788629360e10583))
* **contributing:** 数据库设置跨平台化——嵌入式 PG 主推 + 外部 createdb 跨平台引导 (F4, DOC-02) ([2b66406](https://github.com/shi00/qTrading/commit/2b66406594cb7666c63f2748bb46f038f4b7ff71))
* **contributing:** 补录 docs-consistency hook 到 check_agents_md_sync 的映射说明 ([#728](https://github.com/shi00/qTrading/issues/728)) ([98e6e16](https://github.com/shi00/qTrading/commit/98e6e16c8f508dbce7eb7a67ad81dce793a46546))
* **dao:** how-to 新增DAO步骤登记 _DAO_REGISTRY 并限定 R13 括注 ([4164587](https://github.com/shi00/qTrading/commit/416458771ad8161e1054b9b534543dbffc0c7cfa))
* **dao:** 新增DAO步骤登记 _DAO_REGISTRY 并限定 R13 括注 ([02992ad](https://github.com/shi00/qTrading/commit/02992ad5a85e3b7ef02074104914f253d292729b))
* **data:** 明确数据库断连后必须 init_db 重连契约（review03-C13） ([#562](https://github.com/shi00/qTrading/issues/562)) ([3b64668](https://github.com/shi00/qTrading/commit/3b646680b6b5b1269b21148b43bd045b7deeda75))
* **debt:** remove resolved AIStrategyMixin and TushareClient entries (split to ai_context/ + capability_probe/) ([#659](https://github.com/shi00/qTrading/issues/659)) ([5badfd9](https://github.com/shi00/qTrading/commit/5badfd9580806dfb4b7e5dee6a87f835233c50f6))
* **debt:** remove resolved P3-WinE2E-Skip entry ([#658](https://github.com/shi00/qTrading/issues/658)) ([c7fdc00](https://github.com/shi00/qTrading/commit/c7fdc00e1002a62a170f1828d432a2ee6ea6dd04))
* **debt:** remove stale M11-001 (A3 already fixed) ([#657](https://github.com/shi00/qTrading/issues/657)) ([e516672](https://github.com/shi00/qTrading/commit/e5166722c5a5287a185d39bc8dbbdb22cfa8889f))
* **debt:** 删除 win-e2e-skip-revalidation 冗余归档目录 ([#521](https://github.com/shi00/qTrading/issues/521)) ([cebdaf5](https://github.com/shi00/qTrading/commit/cebdaf576ac2893d4f9d4d91ca02673c4936f8ce))
* **debt:** 技术债务清单复核——删除已解决条目并校准过期数值 ([#725](https://github.com/shi00/qTrading/issues/725)) ([945cdd0](https://github.com/shi00/qTrading/commit/945cdd023e99077512176fe5564c5f2d9e1849b8))
* DOC-06 决策树新增「修改治理文档/规则」路由指向 ADR-0002，补分层登记约定 ([#701](https://github.com/shi00/qTrading/issues/701)) ([10cdd88](https://github.com/shi00/qTrading/commit/10cdd88cda4139df71a7df22d3c0a217c6134486))
* **exceptions:** 精简 EX-0009~EX-0015 同因例外冗余文案 ([cfc7e6e](https://github.com/shi00/qTrading/commit/cfc7e6e681a56ec4fc16eb1bed013cca415d1956))
* **exceptions:** 精简 EX-0009~EX-0015 同因例外冗余文案 ([17757f2](https://github.com/shi00/qTrading/commit/17757f21e701c8e6e8704e468721ce98d733b61b))
* F1 评审收口——如实化检视登记可达性文案口径 ([1e58050](https://github.com/shi00/qTrading/commit/1e580500bebe7938bc467359d80429c8f2ee273e))
* **flet:** baseline §2.5 响应式栅格措辞改推荐使用 (UIX-11) ([#746](https://github.com/shi00/qTrading/issues/746)) ([e4ec382](https://github.com/shi00/qTrading/commit/e4ec382722824cb5f585bf922a088a9d336bfc0f))
* **flet:** baseline 例外清单登记 + M12-020 行号修正 (UIX-11) ([#745](https://github.com/shi00/qTrading/issues/745)) ([64fd746](https://github.com/shi00/qTrading/commit/64fd746cdb93e46e8a13a5849bf7e015760df450))
* **flet:** 集成 UI/UX 最佳实践到 CLAUDE.md 文档体系 ([#498](https://github.com/shi00/qTrading/issues/498)) ([cb223b5](https://github.com/shi00/qTrading/commit/cb223b58dcde22672eae8335b279031008db54bd))
* **governance:** §1.8 决策树补兜底行并登记 fallback 元条目 ([8cf9798](https://github.com/shi00/qTrading/commit/8cf979879829133a613427904bf9fe6cca4bae46))
* **governance:** §1.8 决策树补兜底行并登记 fallback 元条目 (F-11) ([b9ec83f](https://github.com/shi00/qTrading/commit/b9ec83f3f3690b6ece5de6e933acdeaf50e27599))
* **governance:** AGENTS.md 纳入版本一致性管理，补 verify-versions 实际校验 (F5, DOC-13) ([#719](https://github.com/shi00/qTrading/issues/719)) ([7a9e7a1](https://github.com/shi00/qTrading/commit/7a9e7a175206ab5152eb47a27265216f561776ea))
* **governance:** canonical-topics 增加 section 字段提升路由粒度 ([174c6de](https://github.com/shi00/qTrading/commit/174c6de7f3487e9816590d244f9e62cc92c3144e))
* **governance:** canonical-topics 增加 section 字段提升路由粒度 ([6a66dbf](https://github.com/shi00/qTrading/commit/6a66dbfa183817919797a4e55de2fe89b5dbe25b))
* **governance:** R11 强制状态如实化为 CI-test + CPU 池向量化判据规范化 (CON-12/16) ([#758](https://github.com/shi00/qTrading/issues/758)) ([068e4aa](https://github.com/shi00/qTrading/commit/068e4aabc0e26a0ac29969e387b4cd183e96e228))
* **governance:** R18 决策树增加非 Git 环境降级路径（AI 可执行性检视 F-02） ([d6076ca](https://github.com/shi00/qTrading/commit/d6076caad6c4797e1b1c009a86f6ba68bc19381e))
* **governance:** review07-G24 同步单例注册清单与宪法 R13 描述 ([#612](https://github.com/shi00/qTrading/issues/612)) ([cb8d306](https://github.com/shi00/qTrading/commit/cb8d30631a9656954b49153c0a6ab060beb7c2e5))
* **governance:** 新增书名号章节引用一致性门禁并修复死引用 (GDR-13) ([#793](https://github.com/shi00/qTrading/issues/793)) ([4dd6c6d](https://github.com/shi00/qTrading/commit/4dd6c6d2602b404e77ac5a79f4373a2773c5ffb9))
* **governance:** 新增红线 R19「未配套测试的业务逻辑变更」 ([f29b06d](https://github.com/shi00/qTrading/commit/f29b06da27a6f122e44817376a204c3028c6596c))
* **governance:** 新增红线 R19「未配套测试的业务逻辑变更」 ([eb730ea](https://github.com/shi00/qTrading/commit/eb730ea64f7037123992a32ba13037dd665c1c14))
* **governance:** 澄清删除边界并补充 AGENTS.md 任务路由（AI 可执行性检视 F-03/F-06/F-07/F-08） ([1e4e9fb](https://github.com/shi00/qTrading/commit/1e4e9fb95c18137dd264538066ac5f07c2e69d0e))
* **infra:** review01-A14 委托层保留为数据层门面决策记录 ([#604](https://github.com/shi00/qTrading/issues/604)) ([cc9de01](https://github.com/shi00/qTrading/commit/cc9de01f1eebd1b8e08e6471245bba46e65faa11))
* **infra:** review01-A6 循环依赖治理现状 — 两条 MAJOR 循环已消除 ([#600](https://github.com/shi00/qTrading/issues/600)) ([25f17b6](https://github.com/shi00/qTrading/commit/25f17b6d409c58080065f9eb2c688f001ceb3fdd))
* **redlines:** 新增 R20/R21/R22 三条业务红线并同步全量引用 ([67302e1](https://github.com/shi00/qTrading/commit/67302e1bea16ba29b37f0abb49d72145a9886277))
* **redlines:** 新增 R20/R21/R22 三条业务红线并同步全量引用 ([9a5c986](https://github.com/shi00/qTrading/commit/9a5c98654747a7907a2994c24d333f163f5a3178))
* **redline:** 修正 R3 模糊压制描述为 [error-code] ([20cdf6c](https://github.com/shi00/qTrading/commit/20cdf6cd9ee13f9e406cf715cb20760bc15b637f))
* **redline:** 修正 R3 模糊压制描述为 [error-code];原文档写为必带 [reason] 与实际脚本不一致,同步 CLAUDE.md 与 redlines.yml ([38fcadd](https://github.com/shi00/qTrading/commit/38fcaddf8e017faae41dfd403b36a3e280aec1e1))
* **reviews:** 新增 docs/reviews/README.md 检视轮次索引，打通既有结论一跳可达 (DOC-07/14) ([#702](https://github.com/shi00/qTrading/issues/702)) ([0fecddd](https://github.com/shi00/qTrading/commit/0fecdddd4cb2ec5c5b8a70a06e82f4127e9b6ab7))
* **reviews:** 明确 constraints/reviews 目录职责边界，封堵报告误落 docs/reviews ([d2d70c5](https://github.com/shi00/qTrading/commit/d2d70c54583305567c8bba3db158874c3f7ed5c8))
* **reviews:** 显性声明 reviews 目录职责边界，封堵报告误落 docs/reviews (P2-10) ([5e4a422](https://github.com/shi00/qTrading/commit/5e4a422c59bd0eab397ddc5d4abd9b71fa3580fd))
* **reviews:** 登记 2026-09-11 文档体系 AI 可执行性检视报告至轮次清单 ([aae33d8](https://github.com/shi00/qTrading/commit/aae33d8f2e3fbb9e5c8ee300ebbdf3e222c10438))
* **sizer:** clarify rank_weighted ordinal-rank semantics (D3-9) ([d626162](https://github.com/shi00/qTrading/commit/d6261623d87360537a8b82de8e1fea0499518246))
* **sizer:** clarify rank_weighted uses only ordinal rank, not signal magnitude (D3-9) ([3110456](https://github.com/shi00/qTrading/commit/31104565f5d7816f82571807debf0c8408f49e87))
* **strategies:** review02-B1 同步 _cancel_orphan_news_tasks 过期注释（已改用 gather_for_shutdown_cleanup） ([#663](https://github.com/shi00/qTrading/issues/663)) ([6985bbc](https://github.com/shi00/qTrading/commit/6985bbc4491c3d557101a1b287453c8384a63ad6))
* **sync:** concept_sync shield 取消语义与轮询间隔取值依据文档化（review02-B3） ([#572](https://github.com/shi00/qTrading/issues/572)) ([6024a6e](https://github.com/shi00/qTrading/commit/6024a6e40c3376df7b3cd4c8fce53a52ccfa9a55))
* **test:** clean stale P3-WinE2E-Skip refs (review07 G8) ([#661](https://github.com/shi00/qTrading/issues/661)) ([495fac7](https://github.com/shi00/qTrading/commit/495fac7f0f22fd826347d8a0673754a7b8337b97))
* **ui:** font-size token spec + R_no_bare_font_size_in_ui redline ([#491](https://github.com/shi00/qTrading/issues/491)) ([bb5b046](https://github.com/shi00/qTrading/commit/bb5b046e4fe82b6db2cf930bd9443d520ccedf3a))
* **ui:** PaginatedTable docstring 显式声明不做窗口化依赖调用方分页（review04 D21） ([#551](https://github.com/shi00/qTrading/issues/551)) ([98e72b9](https://github.com/shi00/qTrading/commit/98e72b902cc9850be3d59f114aaa2609e11c3082))
* **utils:** run_async 取消语义与关机路径线程策略文档化（review02-B4） ([#574](https://github.com/shi00/qTrading/issues/574)) ([e050bd3](https://github.com/shi00/qTrading/commit/e050bd3b27f922f8ba8615988940b2b616b39e77))
* 文档体系检视修复 DOC-01/02/03/04/05/09/10/11/12 (事实修正批) ([#699](https://github.com/shi00/qTrading/issues/699)) ([2050b35](https://github.com/shi00/qTrading/commit/2050b35e1a15d20e9cb94c017e673a6fb0ab798a))
* 新增检视方法论文档登记门禁 收紧 DOC-07 reviews 登记语义 (F1) ([cb0dee5](https://github.com/shi00/qTrading/commit/cb0dee5cc0cba166bff1e8a0352daf29e8d1bf2e))
* 新增检视方法论文档登记门禁 收紧 DOC-07 reviews 登记语义 (F1) ([da418c4](https://github.com/shi00/qTrading/commit/da418c41da02ace5e1ae9a55d6f131b19d1b1951))
* 机制批补全 5 项文档一致性检查 (DOC-01/04/05/07/09/11) ([#707](https://github.com/shi00/qTrading/issues/707)) ([a2f74f7](https://github.com/shi00/qTrading/commit/a2f74f7c49a5287ed96c68471c3264b836f771bf))
* 补全ADR-0006索引并新增ADR文件级索引完整性门禁 (GDR-12) ([#790](https://github.com/shi00/qTrading/issues/790)) ([bb58696](https://github.com/shi00/qTrading/commit/bb586962d2bb38b5e2417d4587c09a57289acf4a))


### Performance

* **app:** converge main.py top-level imports to minimal first-frame set (PRF-02) ([587db1c](https://github.com/shi00/qTrading/commit/587db1ced6290db1da7e6f8a69ca51b34e30b265))
* **app:** converge main.py top-level imports to minimal first-frame set (PRF-02) ([49b5479](https://github.com/shi00/qTrading/commit/49b5479f106f03f0afba6e938cf4864eb0784785))
* **backtest:** prune quote loading to signal symbols only (D4-9) ([024e2cb](https://github.com/shi00/qTrading/commit/024e2cb4376e8f1eca14c9f89585cc97cd5e0783))
* **backtest:** prune quote loading to signal symbols only (D4-9) ([76a69d1](https://github.com/shi00/qTrading/commit/76a69d15a18749c3e02511ffde379cfb38c9c3ea))
* **backtest:** 再平衡日判定改 O(1) dict 命中消除全表扫描 (D4-3) ([416ed92](https://github.com/shi00/qTrading/commit/416ed9266138bbb17ed7d39d3a7cbeba859a6ab7))
* **backtest:** 再平衡日判定改 O(1) dict 命中消除全表扫描 (D4-3) ([36a3b3c](https://github.com/shi00/qTrading/commit/36a3b3c4ba8b0859a7843834192af13e5dee8372))
* **data:** collapse column-wise numeric NULL normalization into single-pass whole-frame normalize (PRF-03) ([#771](https://github.com/shi00/qTrading/issues/771)) ([4f42707](https://github.com/shi00/qTrading/commit/4f42707ac6ff35f0b36b4ec295ec7bfdd3925645))
* **data:** push max_rows safety valve down to SQL LIMIT (PRF-06) ([#775](https://github.com/shi00/qTrading/issues/775)) ([928338e](https://github.com/shi00/qTrading/commit/928338ed34deba7f42e3829c8fc4008da3eced58))
* **services:** llama_cpp 探测改用 find_spec 避免加载 (PRF-04) ([#772](https://github.com/shi00/qTrading/issues/772)) ([fe9576a](https://github.com/shi00/qTrading/commit/fe9576ae8c59e095fda3d01a6779591716071305))
* **ui:** chart_utils mplfinance 惰性加载降低初始导入负担（P3-M12） ([#542](https://github.com/shi00/qTrading/issues/542)) ([1b1b910](https://github.com/shi00/qTrading/commit/1b1b910f819d3daace13765354849493a97b38ed))
* **ui:** slider 拖拽策略描述更新 debounce 150ms（review02-B14） ([#583](https://github.com/shi00/qTrading/issues/583)) ([cce42ef](https://github.com/shi00/qTrading/commit/cce42ef8dd79b0a7ab5073eadd09b1344f786a08))
* **ui:** UI 显示路径 iterrows 改写为向量化取值 (PRF-09) ([#782](https://github.com/shi00/qTrading/issues/782)) ([47d062e](https://github.com/shi00/qTrading/commit/47d062ef804252ca977fbd2287a00b06ec7721e2))
* **ui:** 选股表格渲染 memo 缓存避免 AI 流式更新高频重复转换（review02-B12） ([#566](https://github.com/shi00/qTrading/issues/566)) ([8bfd9b9](https://github.com/shi00/qTrading/commit/8bfd9b97615a01b8c7b8d52abd3af706ecbc08b9))
* **utils:** lazy-load pandas in sanitizers to de-weight crosscutting layer (PRF-01) ([5019beb](https://github.com/shi00/qTrading/commit/5019bebbbe8660ccd8f01c3ff77bac12a33fd1f9))
* **utils:** lazy-load pandas in sanitizers to de-weight the crosscutting layer (PRF-01) ([01cf25d](https://github.com/shi00/qTrading/commit/01cf25d168e2e80720d9e8774bb00313789a10a1))


### Refactoring

* **alembic:** 提取幂等 helper 并修正模板（DAT-19） ([#741](https://github.com/shi00/qTrading/issues/741)) ([50a9dd6](https://github.com/shi00/qTrading/commit/50a9dd617b14e19291735228346627f8516310f4))
* **alembic:** 提取迁移幂等 helper + 模板提示（DAT-19） ([#737](https://github.com/shi00/qTrading/issues/737)) ([9f45a8f](https://github.com/shi00/qTrading/commit/9f45a8f9c67ca5d8c98d10414bc4c227df8815a0))
* **app:** review01-A7 main.py 瘦身 — 启动编排迁入 app/application.py ([#607](https://github.com/shi00/qTrading/issues/607)) ([070cb05](https://github.com/shi00/qTrading/commit/070cb05681166db6de8b483516c74de39c145dd3))
* **app:** review01-A8 ApplicationSession 统一回滚 + initialize_services partial_state ([#614](https://github.com/shi00/qTrading/issues/614)) ([d0e47d6](https://github.com/shi00/qTrading/commit/d0e47d6570694235af4a0f2b97dfe766c34d81b0))
* **app:** review01-A9 _services_initialized 模块级 flag 收敛为 per-StartupController 实例状态 ([#615](https://github.com/shi00/qTrading/issues/615)) ([f77719e](https://github.com/shi00/qTrading/commit/f77719e5179b245efd364b5ae4775cb71019f603))
* **backtest:** remove dead target_weight column from signal schema (D4-7) ([45ba21f](https://github.com/shi00/qTrading/commit/45ba21f646afab460d6a5a398c5ffa6a6d4ba7ea))
* **backtest:** remove dead target_weight column from signal schema (D4-7) ([80a785a](https://github.com/shi00/qTrading/commit/80a785a4ce5cc6f08e3163be9a208ee480f61b9b))
* **config:** review05-E11 ConfigHandler 拆分 ([#678](https://github.com/shi00/qTrading/issues/678)) ([c2cd8d8](https://github.com/shi00/qTrading/commit/c2cd8d803fff7dcf03dfb012883408a69a0e638c))
* **data:** CacheManager 委托区拆分到 Mixin（review03-C11 Step1） ([#563](https://github.com/shi00/qTrading/issues/563)) ([8045c32](https://github.com/shi00/qTrading/commit/8045c32ecb9f9617ad7838d168232b9bc4580aa7))
* **data:** engine_provider 中立模块解 BaseDao↔CacheManager 反向查询（review03-C3+C11 Step2） ([#584](https://github.com/shi00/qTrading/issues/584)) ([d67accd](https://github.com/shi00/qTrading/commit/d67accd156e9448ccf4cc2823b6bc585d20fc977))
* **data:** review01-A11 精简 — concept_sync AIConceptTagSync 冗余代码移除 ([#653](https://github.com/shi00/qTrading/issues/653)) ([2384600](https://github.com/shi00/qTrading/commit/2384600cd6935cb3f82ce80b085bb829a3be198e))
* **data:** review01-A12 精简 — concept_sync AIConceptTagSync 冗余取消检查移除 ([#652](https://github.com/shi00/qTrading/issues/652)) ([7537d99](https://github.com/shi00/qTrading/commit/7537d996297b85d3c1bf3619639728f1dc071ab3))
* **data:** review01-A13 log_classified 收口 — concept_sync AIConceptTagSync 错误处理迁移 ([#651](https://github.com/shi00/qTrading/issues/651)) ([78283fa](https://github.com/shi00/qTrading/commit/78283fabc4132b35868df656faf94cda01577544))
* **data:** review01-A13 log_classified 收口 — data_processor/domain_services/persistence 错误处理迁移 ([#646](https://github.com/shi00/qTrading/issues/646)) ([61a0800](https://github.com/shi00/qTrading/commit/61a0800d38c979f9fb13432d2840657d5446edeb))
* **data:** review01-A13 log_classified 收口 — holder/historical/financial/concept_sync 同步策略错误处理迁移 ([#645](https://github.com/shi00/qTrading/issues/645)) ([aedceb2](https://github.com/shi00/qTrading/commit/aedceb2328e95db53528b883a7d399bb9559a4f5))
* **data:** review01-A13 log_classified 收口 — sw_industry/macro 同步策略错误处理迁移 ([#642](https://github.com/shi00/qTrading/issues/642)) ([ecd6d56](https://github.com/shi00/qTrading/commit/ecd6d5655f896d4644d925580ee6abb471918222))
* **data:** review01-A14 移除 CacheManager 委托 mixin，调用方直取 DAO ([#650](https://github.com/shi00/qTrading/issues/650)) ([69b33a2](https://github.com/shi00/qTrading/commit/69b33a29e78f699e1e83ca5ef6eb8178db12a004))
* **data:** review01-A4 CacheManager Step2 — 引擎生命周期/DAO 注册拆分为组合对象 ([#602](https://github.com/shi00/qTrading/issues/602)) ([36e96d5](https://github.com/shi00/qTrading/commit/36e96d5947660c9a89a3c1229859e8fe9837fdfd))
* **data:** review01-A5c tushare_client 组合根薄委托（TushareRateLimiter/CapabilityProbeService/TushareApiWrapper 三子模块拆出） ([#638](https://github.com/shi00/qTrading/issues/638)) ([640ca37](https://github.com/shi00/qTrading/commit/640ca3787af018f5c1053379e20f40bfcfb44a52))
* **data:** screener_dao 简单查询迁移 SQLAlchemy Core（review03-C7） ([#554](https://github.com/shi00/qTrading/issues/554)) ([0425022](https://github.com/shi00/qTrading/commit/042502230b8ffc3b2dd73755b63d31cab79f87df))
* **data:** screener/market/stock DAO 消除 f-string 拼 SQL（review03-C7） ([#586](https://github.com/shi00/qTrading/issues/586)) ([56ed247](https://github.com/shi00/qTrading/commit/56ed247b008c99e6220b54e7b3b69ddabb9c0bd7))
* **data:** 数据字典收缩，删除空 columns 定义（review03-C9） ([#555](https://github.com/shi00/qTrading/issues/555)) ([13126ae](https://github.com/shi00/qTrading/commit/13126ae59290670fa175a7961f45ad544ec0a56b))
* **debt:** repay top 3 tech debt (R16 litellm lazy-load / R9 sanitize / health-check cancel) ([#492](https://github.com/shi00/qTrading/issues/492)) ([3419712](https://github.com/shi00/qTrading/commit/3419712548735f6490406ce88a550a71b43a8314))
* **docs:** 删除 _resolve_target_doc 死代码 + 统一 api-verification-template 回链 ([#499](https://github.com/shi00/qTrading/issues/499)) ([c9d29e9](https://github.com/shi00/qTrading/commit/c9d29e930d8f199df41a4bd9577635fc57e0ad8d))
* **error-handling:** review05-E2 log_classified 迁移 ([#674](https://github.com/shi00/qTrading/issues/674)) ([a432675](https://github.com/shi00/qTrading/commit/a4326751b47585db7c6a8404774d505e97288afb))
* **i18n:** 跨层 progress_callback 透传 Message 而非已翻译字符串（review04 D7） ([#564](https://github.com/shi00/qTrading/issues/564)) ([c5f546e](https://github.com/shi00/qTrading/commit/c5f546ec421d17aa9415562ba62d5df9bc770853))
* **infra:** review01-A2 延迟 import 治理 — 夜间预测下沉 + lazy-import 白名单 + screener 清理 ([#599](https://github.com/shi00/qTrading/issues/599)) ([00eb8b3](https://github.com/shi00/qTrading/commit/00eb8b31a56114398e131ada442637ee8f3695fd))
* **infra:** review01-A3 ui→app 依赖消除 — 启动纯类型下沉 core + Controller Protocol 解耦 ([#601](https://github.com/shi00/qTrading/issues/601)) ([184d1d9](https://github.com/shi00/qTrading/commit/184d1d9bcd5b5250986dc7104b22c950d848464f))
* **news:** news_fetcher requests 直连迁移 httpx.AsyncClient（review02-B16） ([#585](https://github.com/shi00/qTrading/issues/585)) ([64777ea](https://github.com/shi00/qTrading/commit/64777eaab48d6addc1ef0030b4db0ef71a2a22e3))
* review01-A13 错误分级 log_classified 收口 — 高风险文件样板迁移 ([#640](https://github.com/shi00/qTrading/issues/640)) ([718f7af](https://github.com/shi00/qTrading/commit/718f7afe43d6ac34a3c1f5afe3ec81166dd5d44d))
* **services:** news_subscription_service 超时魔数提为命名常量（review02-B15） ([#581](https://github.com/shi00/qTrading/issues/581)) ([8bd1851](https://github.com/shi00/qTrading/commit/8bd185113430732fa3161d6fbd9a9e033df016d3))
* **services:** review01-A5b-1 ai_service 模块转包（token_budget/labels/output 子模块拆出） ([#632](https://github.com/shi00/qTrading/issues/632)) ([c309127](https://github.com/shi00/qTrading/commit/c30912741311a3b469d23597f45f2f82e89bdb16))
* **services:** review01-A5b-2 AIService 组合根薄委托（LiteLLMClient/TokenBudgetService/StockAnalysisService/NewsClassifier 四子模块拆出） ([#636](https://github.com/shi00/qTrading/issues/636)) ([3814ed6](https://github.com/shi00/qTrading/commit/3814ed67cb41417b9adeeceebb1d406b02f4c212))
* **strategies:** review01-A5a ai_mixin 拆出 ai_context 子包（渲染器移出，mixin 瘦身） ([#623](https://github.com/shi00/qTrading/issues/623)) ([5ebd40f](https://github.com/shi00/qTrading/commit/5ebd40feec6928e6ebcb0bea884c81aede88e6c8))
* **strategy-backtest:** rename RiskParitySizer to RankWeightedSizer (D3-5) ([f09c94e](https://github.com/shi00/qTrading/commit/f09c94e69c4f972e0284427cc1763fa6557a6e4f))
* **strategy-backtest:** rename RiskParitySizer to RankWeightedSizer (D3-5) ([d32c417](https://github.com/shi00/qTrading/commit/d32c4173aa5db6e729a4900cb934c010526819c4))
* **strategy:** consolidate RSI delta decomposition and add anti-drift gate (D3-2) ([#823](https://github.com/shi00/qTrading/issues/823)) ([394e187](https://github.com/shi00/qTrading/commit/394e1872bcc2483a49560f68cf9573a8758cb0c4))
* **toast:** make ToastManager stateless (review05-E15) ([#679](https://github.com/shi00/qTrading/issues/679)) ([6374f4b](https://github.com/shi00/qTrading/commit/6374f4b87bc0e8be799cdc61c7743e7b4d8290c8))
* **ui:** Backtest 策略/选中/上次提交状态下沉 VM，View 仅留纯 UI 校验态（review04 D2） ([#594](https://github.com/shi00/qTrading/issues/594)) ([0758767](https://github.com/shi00/qTrading/commit/0758767c3602740e999962bc534d8fa8cb3e7e53))
* **ui:** BacktestState.result 拆解为渲染就绪字段，删自定义 eq/hash（review04 D11） ([#587](https://github.com/shi00/qTrading/issues/587)) ([4079741](https://github.com/shi00/qTrading/commit/4079741dbdbce044b4ae6180fb17be9b659e32f7))
* **ui:** DataExplorerState.sql_error 改 SqlErrorInfo，VM 不感知 locale（review04 D6） ([#579](https://github.com/shi00/qTrading/issues/579)) ([f282d90](https://github.com/shi00/qTrading/commit/f282d901581803ad69e5e78452731b1a82d9fb3c))
* **ui:** DataExplorerView 消除三重订阅 — 子 Tab 改纯 props，state 由父组件唯一订阅（review04 D13） ([#598](https://github.com/shi00/qTrading/issues/598)) ([c8c21d8](https://github.com/shi00/qTrading/commit/c8c21d843e61fc1fb999ec769e5d3f8d8950cb5d))
* **ui:** decompose ScreenerView complexity into modular render sections (UIX-08) ([#781](https://github.com/shi00/qTrading/issues/781)) ([d4caf1e](https://github.com/shi00/qTrading/commit/d4caf1e57dc8131a9bfc398b8648075851ba60a8))
* **ui:** enforce _init_mixin_fields contract and remove notifier shim (UIX-09) ([#779](https://github.com/shi00/qTrading/issues/779)) ([f5a88df](https://github.com/shi00/qTrading/commit/f5a88dfd13e8fabbaed0336a71012f1596bd637e))
* **ui:** extract TableViewerTab render builders (D15) ([#666](https://github.com/shi00/qTrading/issues/666)) ([bd586ea](https://github.com/shi00/qTrading/commit/bd586eadd5826ea6225f912524572cdf2fa0c5c6))
* **ui:** frozen state 内嵌可变容器改不可变行对象，name 改 name_key（review04 D10） ([#597](https://github.com/shi00/qTrading/issues/597)) ([ef3e668](https://github.com/shi00/qTrading/commit/ef3e66821f11c6730fb04980236bde67e59f0186))
* **ui:** onboarding 语言下拉移除 locale 局部副本（review04 D17） ([#610](https://github.com/shi00/qTrading/issues/610)) ([2add3e9](https://github.com/shi00/qTrading/commit/2add3e9ede31fffb7f51e3ca4067d51a04e0fd7d))
* **ui:** provider 显示名改 i18n key，VM 不产出中文名字（review04 D8） ([#582](https://github.com/shi00/qTrading/issues/582)) ([48d8831](https://github.com/shi00/qTrading/commit/48d883123dd9e4312f237226889e1a4ebd08eb24))
* **ui:** review01-A10 ObservableViewModelMixin 组合化（ViewModelNotifier 承载状态机） ([#619](https://github.com/shi00/qTrading/issues/619)) ([b0e9732](https://github.com/shi00/qTrading/commit/b0e97321b08ba0ec42c269e36694114ec563f31a))
* **ui:** review01-A12/A15 配置面板 VM 收敛 — ConfigPanelViewModelBase 泛型基类吸收 ConfigPanelStatusMixin ([#639](https://github.com/shi00/qTrading/issues/639)) ([7ea55ee](https://github.com/shi00/qTrading/commit/7ea55eecedce9a4ab591e04c1ed965cc8853dfc1))
* **ui:** review01-A16 删除 View 侧 _format_history_date 重复实现 — 由 VM display_date/d_key 供给 ([#608](https://github.com/shi00/qTrading/issues/608)) ([c825605](https://github.com/shi00/qTrading/commit/c82560516e779ea8930cca2c8201022ba389a677))
* **ui:** ScreenerView 策略参数草稿下沉 VM — 消除 params_ref 双轨（review04 D3） ([#606](https://github.com/shi00/qTrading/issues/606)) ([11acc92](https://github.com/shi00/qTrading/commit/11acc92ed0c8aa0cfd361538e905336c831d5c4b))
* **ui:** SliderInput 声明式受控化消除命令式 update 调用（review04 D1） ([#550](https://github.com/shi00/qTrading/issues/550)) ([8ef8016](https://github.com/shi00/qTrading/commit/8ef801662cce527d8edb957d3c99b65062277ed1))
* **ui:** split ScreenerViewModel into responsibility mixins (UIX-07) ([#770](https://github.com/shi00/qTrading/issues/770)) ([5bf66fb](https://github.com/shi00/qTrading/commit/5bf66fbc0fcb5424a6b4b46c2615cae9561997a9))
* **ui:** TableViewerTab 过滤草稿下沉 VM — 消除 View 双轨 state（review04 D4） ([#605](https://github.com/shi00/qTrading/issues/605)) ([a45f06b](https://github.com/shi00/qTrading/commit/a45f06bc3fcd669a1d8527ec7a31609111276014))
* **ui:** VM 层 get_error_message 调用清零，改产出 Message(key)（review04 D5） ([#570](https://github.com/shi00/qTrading/issues/570)) ([02c9eda](https://github.com/shi00/qTrading/commit/02c9eda40fd52c178c5f1b678c7cb51203d601b1))
* **ui:** 拆分四个千行级 View — 巨型组件主体提出为模块级纯函数（review04 D15） ([#603](https://github.com/shi00/qTrading/issues/603)) ([cfd215b](https://github.com/shi00/qTrading/commit/cfd215bdb4599e99ee2bb5768fc110ebeb20052a))
* **ui:** 欢迎页精简顶部引导，删除火箭图标与渐变引导语 ([#502](https://github.com/shi00/qTrading/issues/502)) ([5dc0009](https://github.com/shi00/qTrading/commit/5dc00093de728beeb29220a141a512e90980f0f1))
* **ui:** 消除 ScreenerViewModel 分页双轨制 + data_version 归零 (批次3-C2b UIX-06) ([#749](https://github.com/shi00/qTrading/issues/749)) ([f15fcbf](https://github.com/shi00/qTrading/commit/f15fcbf78fffb75188590cecffbcfcf4e2255057))
* **ui:** 策略下拉返回 name_key 对，locale 切换即时更新（review04 D16） ([#589](https://github.com/shi00/qTrading/issues/589)) ([b85c735](https://github.com/shi00/qTrading/commit/b85c735d26a27a7bc156b83b110f5a2eb08bc27a))
* **utils:** E2E 判定统一收口到 is_e2e_mode（review03-C16） ([#561](https://github.com/shi00/qTrading/issues/561)) ([47e6f2b](https://github.com/shi00/qTrading/commit/47e6f2be6bbf9fb07bbc7fe05ea21b005f2427f3))
* **utils:** review01-A13 log_classified 收口 — security_utils/task_manager 错误处理迁移 ([#647](https://github.com/shi00/qTrading/issues/647)) ([0ac880a](https://github.com/shi00/qTrading/commit/0ac880aa05f6d9f28c2ddd47588065484a39afa9))
* **utils:** review01-A13 批量替换 12 文件标准三分支为 log_classified（特殊模式保留） ([#613](https://github.com/shi00/qTrading/issues/613)) ([8022003](https://github.com/shi00/qTrading/commit/80220038c151f104f1efbe6307775dd21a9ed55e))


### Tests

* **ai:** 收紧 F12 注入防御结构化边界断言语义 ([#687](https://github.com/shi00/qTrading/issues/687)) ([75b4490](https://github.com/shi00/qTrading/commit/75b449033e2ae8509ff6744047992492ea607a21))
* **ai:** 集成测试断言适配政策未确认返回带 ai_status 标记行 (D5-1) ([8e9a688](https://github.com/shi00/qTrading/commit/8e9a688423c878c256a1e36b6905509c1c203dfe))
* **backtest:** 补齐 profit_factor 分支覆盖，修复 diff-coverage (D4-5) ([016e2c4](https://github.com/shi00/qTrading/commit/016e2c49dd3a32084c32e72f5cd84e0de1b22230))
* **backtest:** 补齐差分调仓分支测试修复 D4-4 覆盖率门禁 ([bfa7e83](https://github.com/shi00/qTrading/commit/bfa7e83ad62bfb9f17e163efd350491f09b26fd5))
* **config:** fix call_args access in TestMultiProviderCredentials (D8-5) ([78acaac](https://github.com/shi00/qTrading/commit/78acaac337d873fa4b59b236a706ea837161c612))
* **config:** 修复 reload 测试污染 USER_DATA_ROOT/RESOURCE_ROOT（D8-4） ([86b85c4](https://github.com/shi00/qTrading/commit/86b85c4a41d7346880b827f168f52b153e37def4))
* **config:** 补齐 _user_data_dir 回退分支单测，修复 config.py 覆盖率门禁（D8-4） ([6f3c111](https://github.com/shi00/qTrading/commit/6f3c111dcde5e4d8f4dc01f056f620d57f304702))
* **conftest:** review07-G9 autouse mock 从业务方法改为真实外部边界 ([#629](https://github.com/shi00/qTrading/issues/629)) ([35aafd1](https://github.com/shi00/qTrading/commit/35aafd1dff5c8f7a9e06e7faf88a04a3b0c801d0))
* **data:** API 字段门禁从 TABLE_TO_API_MAP 派生并堵漏（DAT-22） ([#736](https://github.com/shi00/qTrading/issues/736)) ([3a3cdd8](https://github.com/shi00/qTrading/commit/3a3cdd89fdc981d66ca147b57d45849c715986e1))
* **data:** API 字段门禁从 TABLE_TO_API_MAP 派生并堵漏（DAT-22） ([#742](https://github.com/shi00/qTrading/issues/742)) ([1303e72](https://github.com/shi00/qTrading/commit/1303e72d53c8de60d870482e163dd3e286fc4b1f))
* **docs:** 补 fallback 元条目豁免方向2校验的单测（DOC-04，F-11） ([047f31b](https://github.com/shi00/qTrading/commit/047f31b72916ccbb796448a8c0f939cefe6dac79))
* **e2e:** scale tushare token verify confirm window by timeout multiplier ([#689](https://github.com/shi00/qTrading/issues/689)) ([c975d0d](https://github.com/shi00/qTrading/commit/c975d0d8ef3dc0297948713b6653b792c693c7ba))
* **e2e:** 新增 retry_until_triggered 步骤级确认重试辅助 ([#500](https://github.com/shi00/qTrading/issues/500)) ([c53f410](https://github.com/shi00/qTrading/commit/c53f4106d902db7851adc77ff796f8e99c9715bd))
* **financial:** 修正 ann_date NULL 拒绝测试的异常断言 ([667611d](https://github.com/shi00/qTrading/commit/667611df4e9175036818b8ec0bec624dd1bf946d))
* **financial:** 沿 __cause__ 链断言原始 NotNull 约束异常 ([250c198](https://github.com/shi00/qTrading/commit/250c198a20080106167f99b1797951bfdbd1a211))
* **flet:** accessibility-baseline API 存在性自检测试（UIX-11） ([#744](https://github.com/shi00/qTrading/issues/744)) ([29b3aef](https://github.com/shi00/qTrading/commit/29b3aef976c26b25ff421e94aa11c27b5bf39d88))
* **i18n:** 增加 i18n 未引用死 key 静态检测与棘轮基线门禁 (UIX-16) ([#756](https://github.com/shi00/qTrading/issues/756)) ([e570b37](https://github.com/shi00/qTrading/commit/e570b370feb9bd1710d3800f6c2c148c57bae899))
* **integration:** remove permanently-skipped spike tests (review07 G7) ([#660](https://github.com/shi00/qTrading/issues/660)) ([6b542ae](https://github.com/shi00/qTrading/commit/6b542ae8378865a144d9e41ee01aedeb6f920aba))
* **news-subscription:** CON-07 停机后队列解绑，修正排空断言并锁定契约 ([5b99033](https://github.com/shi00/qTrading/commit/5b990333f72e5aed3f4f4dcc04767d2cd827290f))
* **news-subscription:** CON-07 停机后队列解绑，修正排空断言并锁定契约 ([09fa6df](https://github.com/shi00/qTrading/commit/09fa6dfaa2911a08bd5cd5de8c429455abfe2649))
* **scheduler:** cover submit_task None-warning branches for catch-up and AI concept (D6-5) ([c52315c](https://github.com/shi00/qTrading/commit/c52315c4bc718a1c32818be5610ba6b7a1107913))
* **screener:** 适配 D7-3 三分区状态字段修复 CI 单元测试 ([9b85f0d](https://github.com/shi00/qTrading/commit/9b85f0d6c1ec9a36385ab9004044ac47fdbd282a))
* **strategy:** test_phase2_bypassed_when_dp_missing 显式确认 AI 外发政策以覆盖 dp 缺失场景 (D5-1) ([b62acdb](https://github.com/shi00/qTrading/commit/b62acdb83c00572948e1cc9d11967834466a97b0))
* **ta:** add known-input exact-value assertions for RSI/MACD/KDJ; fix get_rsi flat=100 latent bug (D3-8) ([fe58cc8](https://github.com/shi00/qTrading/commit/fe58cc8bccee9f5e50ced597bb721428d4a2e661))
* **ta:** strong numeric assertions for RSI/MACD/KDJ + fix get_rsi flat bug (D3-8) ([83b723b](https://github.com/shi00/qTrading/commit/83b723b2d458ee415e05b78b0deec0a6ab6b1c0a))
* **unit:** review07-G1 ai_mixin 未确认路径审计 + 具名 fixture 兜底 ([#628](https://github.com/shi00/qTrading/issues/628)) ([715445c](https://github.com/shi00/qTrading/commit/715445c607b134733b4cba4b2b57f60709dab003))
* **unit:** review07-G10 task_manager 统一线程池 mock（136 处重复 patch → 单点 fixture） ([#627](https://github.com/shi00/qTrading/issues/627)) ([98d26f7](https://github.com/shi00/qTrading/commit/98d26f7c1e95279b045a688054297b25cc408b4f))
* **unit:** review07-G11 singleton isolation 改行为断言（不焊死内部 key 名） ([#622](https://github.com/shi00/qTrading/issues/622)) ([94187bd](https://github.com/shi00/qTrading/commit/94187bd5afbeb32937c4e040e769da17ef1dc219))
* **unit:** review07-G2 test_base_dao 删除签名断言并参数化合并重复用例 ([#621](https://github.com/shi00/qTrading/issues/621)) ([b9e308f](https://github.com/shi00/qTrading/commit/b9e308faaecb20bd403bcd1eed5da1fcfcef454b))
* **utils:** autouse fixture 重置优雅停机标志修复跨测试污染（B2+B10 回归） ([#588](https://github.com/shi00/qTrading/issues/588)) ([de64ef3](https://github.com/shi00/qTrading/commit/de64ef3776714648ca2d29cdf8a7ef1af82bd8ed))
* **utils:** make pandas lazy-load subprocess test cwd-independent (PRF-01) ([f61e49d](https://github.com/shi00/qTrading/commit/f61e49dd07cd32cd31ba670d71d4e4ba98c4d83a))
* **utils:** 单测 autouse fixture 补清 loop-local 外层键空间（review02-B7） ([#576](https://github.com/shi00/qTrading/issues/576)) ([9c137a9](https://github.com/shi00/qTrading/commit/9c137a99fdcd93bda8988e32c6772adb419b507f))

## [0.9.0](https://github.com/shi00/qTrading/compare/v0.8.0...v0.9.0) (2026-07-27)


### Features
* **redline:** `scripts/check_redlines.py` 新增 `R_tushare_token_log` 检查（R9 红线专属守护）
  * 扫描 `data/external/tushare_client.py` 中 logger 调用是否直接打印 `self.token` 明文
  * 覆盖直接引用/f-string/format/%/dict 等包装形式
  * 放行 `DataSanitizer.sanitize_token()` / `hashlib.sha256()` 等已脱敏形式
* **flet:** upgrade 0.28.3 → 0.86.2（经 0.85.3/0.86.0/0.86.1 渐进升级，architecture-level rewrite）
  * R1: ft.app(target=) → ft.run(main=, [web_renderer=])
  * R2: page.on_resized → page.on_resize
  * R3: page.open/close/dialog → page.show_dialog/pop_dialog
  * R4: FilePicker 服务化（page.services 挂载）
  * R5: 样式 helper classmethod 化
  * R6: 按钮 text= → content=、ElevatedButton → Button
  * R7: flet-charts 拆包
  * R8: on_scroll_interval → scroll_interval
  * R10: client_storage → shared_preferences
  * R11: mock_flet 契约对齐 V1
  * R12.a: Dropdown on_change → on_select
  * R12.b: Tabs 三件套（TabBar + TabBarView）
  * R13: e.delta_x → e.primary_delta（回退 local_delta.x）
  * R14: TextField focused_border_color
  * R15: Image src_base64 → src（直接支持 base64）
  * window_icon → window.icon
  * 删除 _schedule_async/_scheduled_tasks/_run_task 兼容垫片
  * §8.2 spike 结论：V1 Prop.__set__ 值相等短路仍存在，但声明式 UI 改造后 refresh_dropdown_options() 生产零调用，已在 Phase R.4.1 删除（声明式下 options 由 state 派生，use_state 触发重建自动绕过值相等优化）


### Bug Fixes
* **data/sync:** M7 取消传播时间维度对齐（PR #309）
  * 4 个 sync 文件 7 处循环转 `time.monotonic()` 时间维度检查（M7.3-M7.9）
  * 修复 concept_sync NTP 时钟回退导致取消检查永久失效（P4 bug）
  * data/sync 层 `@require_quality` 豁免文档化（M7.10）
  * 949 测试通过 + ruff/pyright/pre-commit 全过


### Documentation
* **tushare:** 修复 Tushare 文档缺失问题（C2-C19 检视报告）
  * `docs/debt/known-technical-debt.md` 补登记 `tushare_client.py` 2 处 NOTE(lazy) 标记（pro 字段类型注解 + points_15000 API 集），新增 P3-Tushare-Client-Lazy-Markers 条目
  * `docs/patterns/data-sync.md` 新增 Tushare Syncer 设计模式章节（数据流向/限流重试/质量门控/错误处理/取消传播）
  * `docs/guides/how-to.md` 新增 §5.1 Tushare 集成工作流简述
  * `README.md` 新增 §4.1 配置 Tushare 数据源（token 获取/积分档位/降级行为）
  * `SECURITY.md` 新增 Tushare Token Security 章节（存储/脱敏/熔断/静态守护）
  * `docs/architecture/singleton-lifecycle.md` 新增 TushareClient 特殊说明（Token 注入/pro 字段简化/_token_invalid 熔断标志/Token 脱敏）
  * `docs/README.md` 补充 Tushare 文档索引
* **governance:** 修复 docs/review716/r6.md 检视报告问题
  * Flet 版本事实对齐 pyproject.toml（移除 0.85.3 硬编码，改为引用 pyproject.toml）
  * Dialog/Dropdown/Hooks cleanup 契约统一（ft.use_dialog/on_select/cleanup= 显式参数）
  * 测试 loop scope 矛盾消除（unit=function, integration/e2e override 另列）
  * R1/R13 自动化范围与文档声明对齐
  * check_docs_consistency.py 修复（Windows 编码 + man/ 受检 + Flet 版本漂移检查 + 相对链接死链）
  * CLAUDE.md 精简为稳定策略层，CONTRIBUTING.md 收敛为贡献者入口
  * man/flet-best-practices.md 从 1310 行收敛为项目差异指南（193 行）
  * 已解决事项（Windows 测试泄漏、V0 垫片删除、声明式迁移收官）从活动规范移入本 changelog


### Refactoring
* **test:** scripts/* tooling tests 新增 meta marker（Phase TO.2）


### 历史空缺说明
* v0.8.0 (2026-07-08) 为手动 annotated tag 创建于 2026-07-08 指向 commit edce43bd，未走 release-please 流程，CHANGELOG 未记录条目，本次补录占位
* v0.9.0 pyproject/manifest 已提前 bump，本次补发 tag 与 GitHub Release 以修复版本表面一致性

## [0.8.0](https://github.com/shi00/qTrading/compare/v0.7.0...v0.8.0) (2026-07-08)

历史空缺补录：v0.8.0 tag 为手动 annotated tag 创建于 2026-07-08 指向 commit edce43bd，未走 release-please 流程，CHANGELOG 未记录条目，本次补录占位。

## [0.7.0](https://github.com/shi00/qTrading/compare/v0.6.9...v0.7.0) (2026-06-15)


### Features

* **release:** add --fix option to verify_versions.py and write unit tests ([26b726c](https://github.com/shi00/qTrading/commit/26b726c914e3fbcbe752ea5d2d2f1a1fddd9177f))


### Bug Fixes

* **release:** remove non-standard packages key from manifest to fix release-please parsing error ([095465c](https://github.com/shi00/qTrading/commit/095465c0c8464b2e744f33d030f9a01c69158db1))
* **task_manager:** eliminate cross-thread dict race in submit_task ([06881a4](https://github.com/shi00/qTrading/commit/06881a474804fc278e64b2f576bc4fcc1c2481fc))


### Miscellaneous

* **db:** remove redundant 0004 migration ([a71fd86](https://github.com/shi00/qTrading/commit/a71fd86ccea3405b93062a560e5040f0f7abb530))
* **pre-commit:** add verify-versions auto-fix hook ([4088064](https://github.com/shi00/qTrading/commit/40880643d7de71eba682dd382464fe726db34130))
* **release:** configure changelog-sections and generic extra-files in release-please-config.json ([d0b44fb](https://github.com/shi00/qTrading/commit/d0b44fbb07b539745cc568c7646bacbc7529ecbf))
* **release:** switch release-please to manifest-driven mode and fix installer.iss version marker ([b9a12ef](https://github.com/shi00/qTrading/commit/b9a12ef813887d6fc252b60951e7587ee58cbdc3))
* update installer.iss fallback version to 0.6.9 to match pyproject.toml ([15dbee4](https://github.com/shi00/qTrading/commit/15dbee49d58375c43eb7de045eb0ad4a2377d467))


### Tests

* add type ignore with reason for scripts import to resolve CI pyright error ([9070f60](https://github.com/shi00/qTrading/commit/9070f60984c39dc26154962c7a4b57f8011592fc))
* **base_dao:** add direct unit tests for _guarded_begin covering all paths ([569635d](https://github.com/shi00/qTrading/commit/569635d90c25b4c860191103bcc3518d9d4bc151))
* expand unit test coverage for version sync script ([0a616d1](https://github.com/shi00/qTrading/commit/0a616d1fd2ef94579849eb1f1d349498ca3382e8))


## [0.6.9](https://github.com/shi00/qTrading/compare/v0.6.8...v0.6.9) (2026-06-14)


### Bug Fixes

* **db:** correct migration sequence and fix down_revision reference ([a1a1d7e](https://github.com/shi00/qTrading/commit/a1a1d7e6e3509b5027a9a276ee1a574530efd7c1))
* **db:** resolve integration test failures with orm/migration consistency ([3ed37de](https://github.com/shi00/qTrading/commit/3ed37de910719fe976f5a51d09dfa66270e41264))

## [0.6.8](https://github.com/shi00/qTrading/compare/v0.6.7...v0.6.8) (2026-06-14)


### Bug Fixes

* **db:** resolve schema consistency issues from review report ([58e8c43](https://github.com/shi00/qTrading/commit/58e8c43410d3d141225ff87c9a8f679818c76888))
* **db:** unify server_default to now() and fix integration test issues ([0ed97d6](https://github.com/shi00/qTrading/commit/0ed97d686b4f483113580e1ef12476134ad344a6))
* **orm:** Resolve FK cascade and partial index consistency test failures ([121a689](https://github.com/shi00/qTrading/commit/121a68934c05a2fb9f5c42a794425541a24c5a41))
* **orm:** 解决外键级联与局部索引一致性测试失败问题 ([d9a0101](https://github.com/shi00/qTrading/commit/d9a0101f9be4ef8a1eb26f02bba8a2bdc105feea))
* **persistence:** resolve DAO API parameter binding traps and ensure holder calculations atomicity ([f4064ff](https://github.com/shi00/qTrading/commit/f4064ff7958bc2d51d2de4ec1ec9ed5b0fbf801b))
* **persistence:** resolve DAO API parameter binding traps and ensure holder calculations atomicity ([1e87f37](https://github.com/shi00/qTrading/commit/1e87f37d6e08001b2b0dea1e45f211d011cac03d))
* sync installer.iss version to 0.6.7 and fix I18n initialization ([5fee19b](https://github.com/shi00/qTrading/commit/5fee19b5cbd00004c1500d0124248f7a40114789))

## [0.6.7](https://github.com/shi00/qTrading/compare/v0.6.6...v0.6.7) (2026-06-13)


### Bug Fixes

* **db/daos:** resolve schema consistency issues and refactor query safety ([ffd2efa](https://github.com/shi00/qTrading/commit/ffd2efa6ca09db31443b3071a579dd12bf2d44f8))
* **db/daos:** resolve schema consistency issues and refactor query safety ([1d538cf](https://github.com/shi00/qTrading/commit/1d538cf5be0199ea10144aa8698ae6f76bd188b1))
* **db:** add missing ORM server_defaults for sync_version and progress to align with DB ([95b3780](https://github.com/shi00/qTrading/commit/95b3780e03cf78a93b1a6e6cbf81f9a36f9ebeda))
* **db:** resolve schema consistency and align server defaults per architecture report ([429fb78](https://github.com/shi00/qTrading/commit/429fb78f9b68e381c24bbf09c264fd07da0b57e7))
* **db:** resolve schema consistency and align server defaults per architecture report ([b6e422b](https://github.com/shi00/qTrading/commit/b6e422b0fa1fa19b53777965a3a44a0bbf85b7c2))

## [0.6.5](https://github.com/shi00/qTrading/compare/v0.6.4...v0.6.5) (2026-06-13)


### Bug Fixes

* **test:** fix URL-decoding in test DB config for passwords with special characters ([0a44c59](https://github.com/shi00/qTrading/commit/0a44c59f68f4ef7a9fde17fe8d8682cbd69b4479))


### Documentation

* **test:** add detailed explanation for Playwright E2E canvaskit request interception workaround ([0a44c59](https://github.com/shi00/qTrading/commit/0a44c59f68f4ef7a9fde17fe8d8682cbd69b4479))


## [0.6.4](https://github.com/shi00/qTrading/compare/v0.6.3...v0.6.4) (2026-06-13)


### Bug Fixes

* **test:** fix CI timeouts and eliminate pyproject coverage config warning ([5a18fb5](https://github.com/shi00/qTrading/commit/5a18fb5eb362a4a2a6e5a28f07add53684d8dd23))
* **test:** prevent flet_app URL rebuild bypassing DATABASE_URL ([98c5e4d](https://github.com/shi00/qTrading/commit/98c5e4d77c97dcd782bf7a1ca76f923797b41bed))
* **test:** restrict external service mocks to unit tests ([87a0ba9](https://github.com/shi00/qTrading/commit/87a0ba95123c7b8018635eb0330e535dc999ab93))
* **test:** use step DATABASE_URL to avoid db auth failure in E2E tests ([5e68ca6](https://github.com/shi00/qTrading/commit/5e68ca611a5cd70215df998ba8bc7c1d5dd7cb4a))


### Documentation

* **config:** add warning about DATABASE_URL bypass due to db_host default ([a3531c2](https://github.com/shi00/qTrading/commit/a3531c2e7af3a579f7ae5a109e50814f2e7e7bc8))
* **test:** add detailed explanation for db_host hack in E2E conftest ([30d04b1](https://github.com/shi00/qTrading/commit/30d04b160c76e13cc2033cfde6e322da445e74d7))
* **test:** add explanation for E2E canvaskit request interception workaround ([2ce324a](https://github.com/shi00/qTrading/commit/2ce324a6ee59055eb2d9e35ea111e5bd0dcb71dc))

## [0.6.3](https://github.com/shi00/qTrading/compare/v0.6.2...v0.6.3) (2026-06-12)


### Bug Fixes

* **db:** prevent max_rows check ValueError from being swallowed by suppress_errors ([299b21d](https://github.com/shi00/qTrading/commit/299b21d5f921a2cbd123d3dd4e22315650787723))
* **db:** resolve database DAO and data synchronization quality issues ([51fb6e9](https://github.com/shi00/qTrading/commit/51fb6e9786d780549053a1b82393a9fdb63457e3))
* **ui:** translate strategy names in backtest selection dropdown ([1b4f5ee](https://github.com/shi00/qTrading/commit/1b4f5ee75f327045f1a37f12e70b9936f8bfd6a5))

## [0.6.2](https://github.com/shi00/qTrading/compare/v0.6.1...v0.6.2) (2026-06-12)


### Documentation

* resolve documentation alignment findings from review1.md ([aab2d05](https://github.com/shi00/qTrading/commit/aab2d0594bc5cf0162f08dfb04be4e183cb5f778))

## [0.6.1](https://github.com/shi00/qTrading/compare/v0.6.0...v0.6.1) (2026-06-12)


### Documentation

* use relative path for CONTRIBUTING.md link in CLAUDE.md ([35b38cb](https://github.com/shi00/qTrading/commit/35b38cbdedeb6c1924d2d46d25f8a1f7ea157b59))

## [0.6.0](https://github.com/shi00/qTrading/compare/v0.5.0...v0.6.0) (2026-06-12)


### Features

* **i18n:** add snack_full_sync_done_simple localization string ([fe4c596](https://github.com/shi00/qTrading/commit/fe4c596d3d06507ac8f522e824fe27ac852a54b8))


### Bug Fixes

* **async:** propagate CancelledError in gather and fix index_daily missing ts_code ([8b65018](https://github.com/shi00/qTrading/commit/8b65018ffba3aa26d0800465f97c46c37d1ee15e))
* **backtest:** cache BacktestQualityProxy and add missing test coverage ([836af36](https://github.com/shi00/qTrading/commit/836af36645629ff11479be25f6b2991748d86a49))
* **backtest:** resolve 7 audit findings in backtest engine and strategies ([2660ee8](https://github.com/shi00/qTrading/commit/2660ee844ae03a32b9fa0f9df0213f5a98c9230c))
* **backtest:** update test fixtures to use first-day QFQ base (missed in prev commit) ([0dab10e](https://github.com/shi00/qTrading/commit/0dab10e84c2377ea7fbe5ca4154f44b30ad53fc9))
* **dao:** convert all scalar NaN variants to None in _save_upsert ([bf402ec](https://github.com/shi00/qTrading/commit/bf402ec498d3669224ca33f6c9bb64cf5cf36905))
* **i18n:** add missing backtest col_* translation keys for zh_CN and en_US ([09f3570](https://github.com/shi00/qTrading/commit/09f357006ca7b406c9fde1d9282c4cf52bdcd75c))
* **marketdata:** address review findings from marketdata audit ([66ad460](https://github.com/shi00/qTrading/commit/66ad4601392970e0cc5d042b7af1bb9dece47ac7))
* **marketdata:** eliminate lookahead bias in as_of queries ([15a3545](https://github.com/shi00/qTrading/commit/15a35451ec6075a781848508750add1e940ad25e))
* **news:** improve hot concepts error handling and preserve UI state on failure ([bb0bfd0](https://github.com/shi00/qTrading/commit/bb0bfd06819facb96b79e96644ee053bc5aadbff))
* **news:** reset failure counter on successful empty response ([ac424bf](https://github.com/shi00/qTrading/commit/ac424bfbe38d0d267e7aecd79d1da6f5fc8763cc))
* **news:** return empty list on TimeoutError in get_hot_concepts ([f7f3071](https://github.com/shi00/qTrading/commit/f7f3071c21b087967915d50795c034f04d8cce71))
* **onboarding:** correct optional step blocking and sync double notification ([9ece732](https://github.com/shi00/qTrading/commit/9ece73252e063c874947ee5df4b7488240ad9820))
* **shutdown,async,singleton:** resolve event loop blocking and shutdown race conditions ([9c4040a](https://github.com/shi00/qTrading/commit/9c4040a786466d91138ba623f61378380031d372))
* **sync:** standardize error handling and address data_sync review findings ([2cec92b](https://github.com/shi00/qTrading/commit/2cec92b73dcb73be01fc35f33475a689316eba42))
* **test:** add null check for on_click to satisfy pyright ([032e326](https://github.com/shi00/qTrading/commit/032e3262af69bdfb0a21fc51bf1a87d4ab06cc0f))
* **test:** add type narrowing for optional callback in test ([a072ee8](https://github.com/shi00/qTrading/commit/a072ee83e81c131a5978a6c1e51f909cb1c4ecc3))
* **test:** pass show_snack_callback to DataSourceTab mock constructor ([df12952](https://github.com/shi00/qTrading/commit/df129527547c2b82fbe8dc0a76f78794ef06096b))
* **test:** resolve singleton pollution and atexit cleanup issues ([87a87b1](https://github.com/shi00/qTrading/commit/87a87b16dce4a3572885ba9c1b4b2689334d8c3a))
* **test:** update OnboardingWizard database validation tests for ViewModel ([1c63019](https://github.com/shi00/qTrading/commit/1c630197262228edf0d0583614ea3f32cafcba83))
* **ui/data-source:** sanitize health check errors and ensure busy state reset ([7347530](https://github.com/shi00/qTrading/commit/7347530af2d74ca03a1c0f644b0642c952f32bea))
* **ui/onboarding:** overlay state asymmetry and remove dead code ([82afdb0](https://github.com/shi00/qTrading/commit/82afdb02dbdbe038d56c9fb5046893abfccd4e52))
* **ui:** add disposed check in DataExplorerViewModel.export_data ([96c4d8e](https://github.com/shi00/qTrading/commit/96c4d8e8410a4e181e370bffdf12585d1f534fae))
* **ui:** add disposed guard in DataExplorerViewModel methods ([6c27f1e](https://github.com/shi00/qTrading/commit/6c27f1edd2e562da7f2091585b0a0666b5dbcaa2))
* **ui:** add UILogger logging for key interaction paths ([1ad18e7](https://github.com/shi00/qTrading/commit/1ad18e7c4d10f605be1f46b2432bfd4b32f274e5))
* **ui:** remove incomplete import statement in data_view.py ([f805484](https://github.com/shi00/qTrading/commit/f8054847a5e3d8d01d08e271f7fbe769c07665c9))
* **ui:** resolve Pyright type error in failover config panel ([cfd152c](https://github.com/shi00/qTrading/commit/cfd152c59fae26a27e3963d3436bcb89375b4bb6))
* **ui:** use dedicated i18n key for clear-cache sync warning ([a19ef98](https://github.com/shi00/qTrading/commit/a19ef980c842b21923aefca283734f020e659b49))
* **ui:** use Sequence[Control] return type for rendered_row_controls ([205c8fe](https://github.com/shi00/qTrading/commit/205c8fe4c4ba42d28f843c0325c8e2092b1beda4))


### Performance Improvements

* **ui:** implement viewport virtualization for PaginatedTable ([b2d8cf2](https://github.com/shi00/qTrading/commit/b2d8cf2568a0fb302920060cc547550f701e6b90))


### Documentation

* consolidate workflow documentation in CONTRIBUTING.md ([1ac2684](https://github.com/shi00/qTrading/commit/1ac268481072c1914ef9db99d15020d3620fc9dc))
* refine AI assistant interaction guidelines in CLAUDE.md ([c6a6b7e](https://github.com/shi00/qTrading/commit/c6a6b7efed31929abcedb311dac8fde0ba75db36))
* **shutdown:** add thread-safety and atexit cleanup clarifications from audit review ([0057267](https://github.com/shi00/qTrading/commit/005726773e9fc4ae4d8886e81bef2ca373ebc60b))
* update CLAUDE.md and CONTRIBUTING.md guidelines ([6fa9599](https://github.com/shi00/qTrading/commit/6fa9599acd6271f2cb98eb937c8d34d8bc32eaf1))

## [0.5.0](https://github.com/shi00/qTrading/compare/v0.4.2...v0.5.0) (2026-06-09)


### Features

* **ai:** complete Issue [#41](https://github.com/shi00/qTrading/issues/41) with enhanced label registration and test coverage ([1e2797a](https://github.com/shi00/qTrading/commit/1e2797a3266853b80c2b7d39e8a59d60e65c7e0f))
* **ai:** implement Issue [#41](https://github.com/shi00/qTrading/issues/41) available-data invariant system ([635f0f9](https://github.com/shi00/qTrading/commit/635f0f950d343c8933bf67e8b506b8e66a86f8cb))
* **correlation:** add ensure_correlation_id for entry-point tracing ([bbc217a](https://github.com/shi00/qTrading/commit/bbc217ada95b5d841af3436ab94a678ae8844246))
* **db:** consolidate Alembic migrations and add schema consistency tests ([5e92b44](https://github.com/shi00/qTrading/commit/5e92b446863ac6f9c6c23b0cc62bafb5eaa00244)), closes [#41](https://github.com/shi00/qTrading/issues/41)
* **tushare:** add point-tier presets for rate limiting ([#69](https://github.com/shi00/qTrading/issues/69)) ([fd4fd4d](https://github.com/shi00/qTrading/commit/fd4fd4d7c7ea4c01171b34dfb1cf9f2d65e4e62f))
* **ui:** add semantic labels for E2E accessibility ([de4e72f](https://github.com/shi00/qTrading/commit/de4e72f1eb32af167640007d0542cb895f0fdfbd))


### Bug Fixes

* adapt tests for DAO engine validation and strategy gating changes ([bf2575e](https://github.com/shi00/qTrading/commit/bf2575ee9b3d5ed75fd670b5cf6fa29493a36aa2))
* add missing CancelledError re-raise in 4 files ([97ca48e](https://github.com/shi00/qTrading/commit/97ca48edc0a000290c8aef902e0383e00c291f6f))
* **ai-service:** improve cross-provider failover and credential handling ([6bea6bd](https://github.com/shi00/qTrading/commit/6bea6bd066fa0531bc6c8d15a4ca1663a259b2df))
* **ai:** filter financial sentinel texts to avoid empty financials block ([2c1567b](https://github.com/shi00/qTrading/commit/2c1567b3f5ddcb921dc058a1d27f4318297319e8)), closes [#41](https://github.com/shi00/qTrading/issues/41)
* **alembic:** avoid ConfigParser interpolation error with URL-encoded passwords ([4a165af](https://github.com/shi00/qTrading/commit/4a165af32c177e5196e6bd8aac2849743cdf4ce1))
* **alembic:** make financial_reports column migration idempotent ([aecbda7](https://github.com/shi00/qTrading/commit/aecbda7ec6c3adcc2ddd8076f2b7186aef134601))
* **config:** add provider credential fallback to global api_key and harden LLM config panel ([6974fe2](https://github.com/shi00/qTrading/commit/6974fe2728ec75560106e09134ac4c5c67fbe0c7))
* **db:** comprehensive database config hardening - connection leak, sensitive info exposure, perf decorators, wizard save logic ([dbda4aa](https://github.com/shi00/qTrading/commit/dbda4aafb3711c39a9c4894e83e5596ad6367f6f))
* **db:** correctly identify non-existent database vs auth failure ([4d9e147](https://github.com/shi00/qTrading/commit/4d9e147dbe1e5ff1903f669770288974069e81c7))
* **db:** disambiguate ConnectionDoesNotExistError for non-existent database ([4a40f45](https://github.com/shi00/qTrading/commit/4a40f451f54dd87ec0854373b9a886cf076e47bd))
* **db:** fix connection leak, SQL injection risk and improve test quality ([a658026](https://github.com/shi00/qTrading/commit/a6580269e48bf10c4eed68343ed553dfb33f9349))
* **db:** harden database creation and migration with schema drift detection ([92a20bc](https://github.com/shi00/qTrading/commit/92a20bc82d8d31d3d397ca6012a7734a012342a4))
* **db:** resolve schema sync whitelist gap and DAO consistency issues ([aa41ec2](https://github.com/shi00/qTrading/commit/aa41ec2ccee1f6ef976497e9ca15324f6f698ce2))
* **db:** return CONNECTION_ERROR instead of AUTHENTICATION_ERROR when verification is inconclusive ([68b4e21](https://github.com/shi00/qTrading/commit/68b4e216739d5c9496b0b24fcb5f2020368a0630))
* **e2e:** pass timeout to page.goto and increase CI timeout multiplier ([d438741](https://github.com/shi00/qTrading/commit/d438741f1fa54eee1d22558674a7f66df643115a))
* **e2e:** resolve CI e2e test failures caused by redundant Flet process and timeout issues ([a59f15b](https://github.com/shi00/qTrading/commit/a59f15b9c78450a1cf9837f83f039d3f2c5bbde9))
* **e2e:** use fuzzy text matching for Windows Server headless mode ([edaf17f](https://github.com/shi00/qTrading/commit/edaf17f6d4ea6e03804a69c028ac1446ff01281b))
* **i18n:** register missing UI-facing i18n keys and replace hardcoded English strings ([6ea1053](https://github.com/shi00/qTrading/commit/6ea1053d014b4126e602fce4309498c6bdc0f63b))
* **i18n:** replace hardcoded English messages in create_database and run_migrations with i18n keys ([c0a365e](https://github.com/shi00/qTrading/commit/c0a365e54ed4c9762a1f5bfa376e303393f3236c))
* **i18n:** update db_err_interrupted message to cover both auth failure and network issues ([88e67db](https://github.com/shi00/qTrading/commit/88e67db06ac86dec8f1169214959e972f5f17e1b))
* **llm:** add missing _KEY_MASK_THRESHOLD in FailoverConfigPanel class ([bcbb9ef](https://github.com/shi00/qTrading/commit/bcbb9ef6057c632ef81cad1afe77ef7f284c06c4))
* **llm:** fix multiple bugs in wizard LLM config panel ([885d132](https://github.com/shi00/qTrading/commit/885d132a6e55aac15320414df400411fb229151b))
* **local-model:** handle OSError in _await_worker_ready queue reads and fix stale test mocks ([0ecc0e9](https://github.com/shi00/qTrading/commit/0ecc0e9d1fdc025dcb59ca3f38eacc07cb185394))
* **local-model:** pass configured timeout to worker ready wait and clear cancel event on reload ([9a5d06c](https://github.com/shi00/qTrading/commit/9a5d06cde4614491fcaf2718b2b14f238a81b790))
* **local-model:** resolve worker ready deadlock and UI hang on model verification ([0f14d25](https://github.com/shi00/qTrading/commit/0f14d25afd52bf45252e7c59cfe99ab5094ffa56))
* resolve pyright type errors and improve type safety ([57ff1bc](https://github.com/shi00/qTrading/commit/57ff1bcd9b01027131e7b70780094ffa29ddacfb))
* **security:** add missing _KEY_MASK_THRESHOLD in ProviderCredentialDialog, accept str in sanitize_error ([30778be](https://github.com/shi00/qTrading/commit/30778becd39cdff43b081d2fa7099af24505954d))
* **security:** sanitize all exception logs and normalize logger format ([c67b075](https://github.com/shi00/qTrading/commit/c67b07529b0c3eb0d58bb0aa9518d69c5ecef505))
* **security:** sanitize API keys in error logs to prevent credential leakage ([f8e6ce4](https://github.com/shi00/qTrading/commit/f8e6ce43f19cc2dc4080446f7e053f66d544f0d3))
* **security:** sanitize config errors, fix type annotations, extract tag constant, fix truncation order ([571793e](https://github.com/shi00/qTrading/commit/571793edf6c3b34c6dbedcbac151cbe28f234baf))
* **security:** sanitize sensitive data in logs and improve code quality ([e240728](https://github.com/shi00/qTrading/commit/e240728dbbc10e276b591eb1dae7c30b33265bda))
* **security:** upgrade aiohttp to 3.14.0 and litellm to 1.87.0 ([364661f](https://github.com/shi00/qTrading/commit/364661f48f0c4d3b10327226f4cbd1c57362b998))
* **shutdown,dao:** propagate CancelledError per R2 and unify DAO error handling ([2ad9bf7](https://github.com/shi00/qTrading/commit/2ad9bf7d8933c9f8334d9b8bbab9149ae408b985))
* **test:** add conn=None path SQL compilation coverage for update_prediction_result ([53bf393](https://github.com/shi00/qTrading/commit/53bf39351636a4029340cecee8718bf4ad132524))
* **test:** add missing mock for get_tushare_point_tier ([3c4ca71](https://github.com/shi00/qTrading/commit/3c4ca71b52b10a2d94e7245ca16e6144c9360123))
* **test:** align two failing tests with current implementation ([c435b9a](https://github.com/shi00/qTrading/commit/c435b9a151a291f7af8affa10214925979983cb6))
* **test:** correct DatabaseMigrator test to use public init_db API ([15d48ba](https://github.com/shi00/qTrading/commit/15d48ba868807f08669f864d77f891437777b3fa))
* **test:** defer AIService import to fixture to prevent keyring mock bypass ([b75aa1f](https://github.com/shi00/qTrading/commit/b75aa1f972de7cfe1e9c0b6fc1aa17b0c85000e4))
* **test:** handle special characters in database passwords ([24613f6](https://github.com/shi00/qTrading/commit/24613f631719e3fdade3afb587793048e1264c25))
* **test:** mock _disposed attribute in ScreenerDao unit tests ([e9bf3ee](https://github.com/shi00/qTrading/commit/e9bf3ee80f638858e1197f53150629c345c8b875))
* **test:** resolve pyright type error and refactor test helpers ([7f0e98b](https://github.com/shi00/qTrading/commit/7f0e98ba166d42f0c3a8ad0b69855afd7c0e93c5))
* **test:** update alembic config test to match new implementation ([780585c](https://github.com/shi00/qTrading/commit/780585cc93f294c1fc0bfa155f63e11876e32826))
* **test:** update i18n-asserted tests to check database name in message instead of English keyword ([cae8453](https://github.com/shi00/qTrading/commit/cae84530e5baf46f1acacf6ed8fdb20657c0efcf))
* **test:** update integration tests for update_prediction_result conn=None path change ([49657f6](https://github.com/shi00/qTrading/commit/49657f69eb9564243284f6c149f6856b7206ec54))
* **types:** add None guard for dropdown options iteration in test ([6f8759a](https://github.com/shi00/qTrading/commit/6f8759a0e6714297440dccfce7acc92521852ef1))
* **types:** resolve all pyright errors and key warnings across codebase ([f4e6e65](https://github.com/shi00/qTrading/commit/f4e6e65c1604eeaa856f70a8966f6b3934d9eed9))
* **types:** resolve pyright type check errors across backtest and strategy modules ([80110ce](https://github.com/shi00/qTrading/commit/80110cedc78d30cc1cb3e2f689a452e90c0195fd))
* **ui:** add correlation_id to remaining UI entry points ([5113cdb](https://github.com/shi00/qTrading/commit/5113cdb0d031150821e7d262a27ee0969a4b05f3)), closes [#22](https://github.com/shi00/qTrading/issues/22)
* **ui:** improve Tushare token validation error messages in onboarding wizard ([9361460](https://github.com/shi00/qTrading/commit/9361460c89e109c29c92b0a62f17529c349bf211))
* **ui:** resolve R16 violations in ai_brain_tab and db config ([6e24ffe](https://github.com/shi00/qTrading/commit/6e24ffe826e9371c353ce52aa79b6682310e5a1b))
* **ui:** use normalized locale in language dropdown to match option keys ([65054f1](https://github.com/shi00/qTrading/commit/65054f1d3acf69b3daa0448aa38ca4a92a9dd758))


### Documentation

* align CLAUDE.md with actual project state ([2b8baeb](https://github.com/shi00/qTrading/commit/2b8baeb6a8dd9861ae402e70bae37e3b62e3c12f))
* **db:** add comment explaining auth verification fallback rationale ([2e36ba2](https://github.com/shi00/qTrading/commit/2e36ba2a4fac5175d93a01b579acc0aa4bfc1b79))

## [0.4.2](https://github.com/shi00/qTrading/compare/v0.4.1...v0.4.2) (2026-05-31)


### Bug Fixes

* **ci:** use importlib.metadata to get playwright version ([348399f](https://github.com/shi00/qTrading/commit/348399f9ec29675b0aeda447c93327a8788d7d62))

## [0.4.1](https://github.com/shi00/qTrading/compare/v0.4.0...v0.4.1) (2026-05-31)


### Bug Fixes

* **e2e:** add fallback for fill_textbox when Playwright fill fails in Flet Web ([675adcd](https://github.com/shi00/qTrading/commit/675adcdab9b9f6f9499bb2c02b3a210a0bf9b0b2))
* **e2e:** resolve test failures and configure windows playwright with postgresql ([c65b374](https://github.com/shi00/qTrading/commit/c65b374545502ec5ddaaae13e602b92f12a78423))

## [0.4.0](https://github.com/shi00/qTrading/compare/v0.3.0...v0.4.0) (2026-05-30)


### Features

* data-driven locale update for SettingRow and SectionHeader ([68d5f25](https://github.com/shi00/qTrading/commit/68d5f251f3a2842df4b97922179452646332190b))


### Bug Fixes

* add missing 'import os' in main.py for _is_web_mode function ([89a0b71](https://github.com/shi00/qTrading/commit/89a0b717355cd4896bcb13871c2a610e5cf4bb0a))
* AI candidate analysis concurrency (Closes [#14](https://github.com/shi00/qTrading/issues/14)) ([506ab4d](https://github.com/shi00/qTrading/commit/506ab4d49f41fcc63382c7238afcff2bb4d1ad8a))
* mock_i18n 缺少 get_language_options/get_language_label 返回值导致测试失败 ([4c65c08](https://github.com/shi00/qTrading/commit/4c65c088a4cb87e9c337f69ce518a3ad34821d7d))
* onboarding wizard header title not updating on language switch ([f6f59ed](https://github.com/shi00/qTrading/commit/f6f59ed5408cf2921cf42b376a52498bfbd3094a))
* resolve 5 failing unit tests caused by test pollution and mock issues ([8ff0d91](https://github.com/shi00/qTrading/commit/8ff0d9191644651dc65f3b8332d7424367d040d5))
* revert zh_CN settings_language to pure Chinese label ([db5aa56](https://github.com/shi00/qTrading/commit/db5aa5606d1de8bb451cb274f2d5446a56143109))
* save SectionHeader as instance attr in system_tab + remove double super init ([be72043](https://github.com/shi00/qTrading/commit/be72043091060ed86592c426063fb0211927bec9))
* unify language dropdown label to bilingual format in en_US locale ([8e479c7](https://github.com/shi00/qTrading/commit/8e479c77b6b43babde33103b05fe6df614c19d59))

## [0.3.0](https://github.com/shi00/qTrading/compare/v0.2.1...v0.3.0) (2026-05-29)


### Features

* **ui:** 添加语言切换 UI 控件 (Fixes [#12](https://github.com/shi00/qTrading/issues/12)) ([597557f](https://github.com/shi00/qTrading/commit/597557fcb9bd35a803b559a4959cd0d3a093bbdb))
* **ui:** 添加语言切换 UI 控件 (Fixes [#12](https://github.com/shi00/qTrading/issues/12)) ([1b87867](https://github.com/shi00/qTrading/commit/1b87867c4e9451e2be063708eb0879422b9a845a))

## [0.2.1](https://github.com/shi00/qTrading/compare/v0.2.0...v0.2.1) (2026-05-28)


### Bug Fixes

* **backtest:** 月度收益计算改用复利公式 ([f0198dd](https://github.com/shi00/qTrading/commit/f0198ddefd03d8b9006fc7c925d0e89850584c35)), closes [#78](https://github.com/shi00/qTrading/issues/78)
* **backtest:** 月度收益计算改用复利公式 ([#78](https://github.com/shi00/qTrading/issues/78)) ([fe3082a](https://github.com/shi00/qTrading/commit/fe3082a6ffd5912f0c76de194ead7b85aa346b1d))
* CacheManager 单例模式竞态条件修复 ([1ccda37](https://github.com/shi00/qTrading/commit/1ccda378fab8aff715a738fe5c8093b41571b37a))
* prevent look-ahead bias in AI backtest context ([0ca9ef7](https://github.com/shi00/qTrading/commit/0ca9ef7dcc986df2d1be14c8fd7a9f209cae2c17))
* **scheduler:** add unique_key to nightly_prediction task ([51d651a](https://github.com/shi00/qTrading/commit/51d651ac521749f83e9b615880ece4c7e17fe319))
* **scheduler:** add unique_key to nightly_prediction task (Fixes [#68](https://github.com/shi00/qTrading/issues/68)) ([6f251f1](https://github.com/shi00/qTrading/commit/6f251f1b6c5718de5433bf31ffd9c3245000c901))
* TaskManager 单例模式 _initialized 改为类属性 ([da0b64d](https://github.com/shi00/qTrading/commit/da0b64dccdfbf3c09a9869b0deec3d7c0d9c09e4))
* **test:** remove unnecessary keyring patch in azure URL test ([7fbff0d](https://github.com/shi00/qTrading/commit/7fbff0d591050311fdc31501c0a2739298693ccc))
* **tests:** add explicit keyring mock for Linux CI compatibility ([719f791](https://github.com/shi00/qTrading/commit/719f791203bd686327ed1b4f036e2ee589086ada))


### Documentation

* update README with backtest framework and simplify test structure ([6f63ff5](https://github.com/shi00/qTrading/commit/6f63ff50f3760d2343745e9ef34363fb2ed1dddc))

## [0.2.0](https://github.com/shi00/qTrading/compare/v0.1.1...v0.2.0) (2026-05-27)


### Features

* **backtest:** add position sizing module with multiple allocation strategies ([4bfb724](https://github.com/shi00/qTrading/commit/4bfb724b3176c21099fb2cb1009c3b95ab3feca0))
* **backtest:** 实现印花税分段费率功能 ([2ded982](https://github.com/shi00/qTrading/commit/2ded982eda9cec278f408471b5d5ebcd5613c787))
* Tushare Capability productization loop ([fc4369d](https://github.com/shi00/qTrading/commit/fc4369d78090a4eeb27a6afd2443dd642192b322))
* 新增故障转移配置面板与增强测试覆盖 ([bdf3016](https://github.com/shi00/qTrading/commit/bdf3016ea647adfdbb45a6d0ab24517e94b697f7))


### Bug Fixes

* add page check before update() in ProviderCredentialDialog ([e963d56](https://github.com/shi00/qTrading/commit/e963d56692096c36a882d743fabdf91dd32f63fb))
* **ai_strategy:** unify quality gate pattern with PolarsBaseStrategy ([2b521eb](https://github.com/shi00/qTrading/commit/2b521eb746ce9b1bce7557cc0cf2f95a38b5598d))
* **ai-mixin:** add as_of_date filter to prevent lookahead bias in financial data queries ([50fdbd4](https://github.com/shi00/qTrading/commit/50fdbd4376d33cfd60ede7c330edaff28f9204a9))
* **ai-service:** failover cross-provider credentials, reasoning check, CancelledError, and test fixes ([c99b42f](https://github.com/shi00/qTrading/commit/c99b42f0f26ce8caaae7d3dba332df4167d5727b))
* **ai:** pass model parameter through failover chain to enable actual provider switching ([fff09ce](https://github.com/shi00/qTrading/commit/fff09ce3e737c7330bf267c55eb149b2b1d57cf5))
* **async:** convert start() to async def for NewsSubscriptionService and MarketDataService ([8a961ee](https://github.com/shi00/qTrading/commit/8a961eefd76b5654fae3c875cc3da4e99ac472ed))
* **async:** re-raise CancelledError instead of swallowing it ([132b9c6](https://github.com/shi00/qTrading/commit/132b9c61a185fc74c2e7c28cdb6f456b05086691))
* **backtest:** set strategy.key in BacktestService._get_strategy ([a505a41](https://github.com/shi00/qTrading/commit/a505a417a02818cab045cc88fe793a9402d1ee32))
* **backtest:** use ScreenerDao standard SQL to eliminate data path fork ([0d2a7f5](https://github.com/shi00/qTrading/commit/0d2a7f5229aac19c21e09eb111dcf435c40fa327))
* **config:** gracefully handle NoKeyringError in CI Linux environments ([3874181](https://github.com/shi00/qTrading/commit/387418155c3cc0f77ecf55d458f503fcbbf13412))
* **data:** add ann_date column to fina_mainbz and use it for as_of_date filtering ([a2a9848](https://github.com/shi00/qTrading/commit/a2a984805518a6ea8d69c6377cd9c31e295d2317))
* **data:** add ann_date to pledge_stat to eliminate lookahead bias ([55c1d65](https://github.com/shi00/qTrading/commit/55c1d6558f03e7a721188fcf83c7d660d66e98e7))
* **data:** add ann_date to tushare get_fina_mainbz API fields ([ba42a0c](https://github.com/shi00/qTrading/commit/ba42a0cf7ee37c699176e171a4ba88e2d0057677))
* **data:** correct margin_daily and suspend_d type from global to stock ([6e14142](https://github.com/shi00/qTrading/commit/6e14142311f1d13aeaae7cb1ece9d6718a5924b4))
* **data:** improve DAO error handling and review_manager robustness ([91551e2](https://github.com/shi00/qTrading/commit/91551e24257ee03779e6393770c0af788a1e9390))
* **gitleaks:** correct path regex to match all test files ([f9618b4](https://github.com/shi00/qTrading/commit/f9618b46991e4fb6f65eb1cf404b57e78b9170b6))
* handle TushareAPIPermissionError and improve type safety ([bda0b7a](https://github.com/shi00/qTrading/commit/bda0b7a2ce2f179107f90a792b77f167b6f8d268))
* **services:** extract _await_worker_ready from _ensure_worker in LocalModelManager ([e26c557](https://github.com/shi00/qTrading/commit/e26c557721f5bea5c0d257786656a1026247ecb7))
* **strategy:** clean up orphan news tasks on CancelledError in AIStrategyMixin ([03ed9a0](https://github.com/shi00/qTrading/commit/03ed9a0e59d0b6af657000cbdfdb77b7923ccf7e))
* **strategy:** set required_quality_tier=BRONZE for market strategies ([292859b](https://github.com/shi00/qTrading/commit/292859b98fe39f335f344f6226472872c5723df3))
* **test:** ensure test database is recreated from clean state ([8bea1ce](https://github.com/shi00/qTrading/commit/8bea1ceb8f93a28232baca3564f8bfce4111e45c))
* **test:** rename parametrize base_url to api_url to avoid pytest-base-url fixture scope conflict ([0a5d2eb](https://github.com/shi00/qTrading/commit/0a5d2eba4af68d5f1a64fc6a4bcdc12e9b0aa91d))
* **tests:** sync mock interfaces with production code refactoring ([b4bb688](https://github.com/shi00/qTrading/commit/b4bb6881289573b0e308378577a9e392da16da9c))
* **thread_pool:** handle logger exceptions during shutdown ([8dfe587](https://github.com/shi00/qTrading/commit/8dfe58778a9c4ea5e1ef241ad16c7f7f342e6a43))
* **utils:** add thread-safety to SecurityManager.get_key and fix migrate_to_derived_key ([7875013](https://github.com/shi00/qTrading/commit/787501393ba11445ad6b56a7b7cb1a75d9932098))
* 为策略测试添加 data_processor mock 以修复 QualityGate STRICT 模式下的测试失败 ([75dfed7](https://github.com/shi00/qTrading/commit/75dfed7c266d55472890edf7d9569aa5655ca6a4))
* 修复 test_automation_tab 类型检查错误 (reportOptionalCall) ([678cc41](https://github.com/shi00/qTrading/commit/678cc4147c3afa5178bbb06470f334cafa94af8c))

## [0.1.1](https://github.com/shi00/qTrading/compare/v0.1.0...v0.1.1) (2026-05-23)


### Bug Fixes

* **async:** re-raise CancelledError instead of swallowing it ([3e8ff96](https://github.com/shi00/qTrading/commit/3e8ff96466eb3d2d32290d8f541723984e9119fd))
* **config:** unify DEFAULT_AI_PROMPT/DEFAULT_NEWS_PROMPT to config_models.py single source ([4c84250](https://github.com/shi00/qTrading/commit/4c842503134fb8b083ebfce2fe332142d6e84981))
* **core:** resolve cache initialization and task manager state leak in tests ([1beef3e](https://github.com/shi00/qTrading/commit/1beef3e726968d6afc5f0cc362778356d2ce3d07))
* **dao:** align MarketNews unique constraint with UPSERT conflict key and update columns on conflict ([552ff3c](https://github.com/shi00/qTrading/commit/552ff3ccfd4755ee86f6c4c83b083bda24dddebe))
* **dao:** change null_protected default from True to False in _save_upsert ([2be83d6](https://github.com/shi00/qTrading/commit/2be83d6434221d0be42e7f9ab1b9668ec191ddfd))
* **dao:** set MarketNews.publish_time NOT NULL and harden Alembic downgrade ([e0f9e95](https://github.com/shi00/qTrading/commit/e0f9e95598a8aafe58e56f0ae45c5fadf412c67f))
* **data-safety:** raise EngineDisposedError instead of returning 0 on shutdown writes ([b7db669](https://github.com/shi00/qTrading/commit/b7db669bc07aab27dc27230a1112e40541a0766c))
* is_transient NameError, DataFrame cache pollution, engine=None after close ([3a308ca](https://github.com/shi00/qTrading/commit/3a308ca4893919d8c4823560236c3e75e67c6792))
* **lifecycle:** _initialized after engine creation, shutdown checks _instance ([0ec2482](https://github.com/shi00/qTrading/commit/0ec2482b2a699cc2dc50b96cee62e9a9f6b5c6c0))
* **security:** add _hide_file_windows after _copy_file in get_key() and fix raise e to bare raise ([b9d78c2](https://github.com/shi00/qTrading/commit/b9d78c2a09699880de85918fb48ff67f4c7eb066))
* **security:** replace AUTOCOMMIT with READ ONLY transaction in SQL Console ([5e63823](https://github.com/shi00/qTrading/commit/5e6382343bb1658692c2999f5b7d0c2204dbeab9))
* **security:** replace dead doubao_api_key with db_password_encrypted in SENSITIVE_KEYS ([9409117](https://github.com/shi00/qTrading/commit/9409117e5ff89f7857691e062750036a623323dd))
* **security:** sanitize sensitive values in set_typed validation log ([ec28ed7](https://github.com/shi00/qTrading/commit/ec28ed757d1affee24395b5f005feedd464c8cc4))
* **security:** sanitize ValidationError logs to prevent sensitive value leakage ([40c94ac](https://github.com/shi00/qTrading/commit/40c94acd24c27626abc8c88a4247906a47995cc3))
* **security:** set owner-only permissions on secret files for Linux/macOS ([ecc9ad5](https://github.com/shi00/qTrading/commit/ecc9ad5f016b2405350456370b1169bb7868ee13))
* **security:** use regex word-boundary matching for SQL keyword blacklist ([a278554](https://github.com/shi00/qTrading/commit/a2785546ddc9cd4152ae5fa4f5be59586676d323))
* **shutdown:** add EngineDisposedError handling to all sync strategies and news service ([8ecf56f](https://github.com/shi00/qTrading/commit/8ecf56f657d9fa5dbc06e8cbfa1c0c454b6828a7))
* **shutdown:** handle CancelledError in ShutdownCoordinator to prevent cleanup interruption ([4e4c35f](https://github.com/shi00/qTrading/commit/4e4c35f0620bc076e44cddb0a7c9e53babe19a39))
* **sync:** add CancelledError re-raise to holder and macro sync strategies; update test_model_indexes for composite constraint ([703e9a1](https://github.com/shi00/qTrading/commit/703e9a1a9be148a04ca2f40293fa788ecdb39672))
* **sync:** re-raise CancelledError in historical and financial sync strategies ([247fd01](https://github.com/shi00/qTrading/commit/247fd01bb5e50cfbf79de72ec1b6190f20b6c855))
* **task_manager:** use get_loop_local for Semaphore to prevent cross-loop issues ([c783089](https://github.com/shi00/qTrading/commit/c783089eab32d0ac0514e3140e2702b9bdbad4ec))
* **test:** correct sanitize assertion value and rename financial sync test ([fe54eaf](https://github.com/shi00/qTrading/commit/fe54eafcc7e61079d0d42f0726e101f2d9f2007f))
* **ui/cache:** handle CancelledError in tab switch test & clean lazy loaders in CacheManager ([e638b38](https://github.com/shi00/qTrading/commit/e638b38f970cbd9bc66af936ea6351ce773a72f5))


### Performance Improvements

* **review_manager:** fix N+1 query for benchmark index pre-fetch ([d2729d4](https://github.com/shi00/qTrading/commit/d2729d4c51025d5fc39b7a14dafa546b46f0bf76))

## 0.1.0 (2026-05-22)


### Features

* add loading overlay for wizard validation steps ([da7a9d7](https://github.com/shi00/qTrading/commit/da7a9d7695e89d446388fb2ef9075f06d5fafe1c))
* Add robust offline calendar fallback using pandas_market_calendars ([ffda635](https://github.com/shi00/qTrading/commit/ffda635f128816f66a7b79771c8937de223ebfca))
* add user-friendly database upgrade flow ([6abcda7](https://github.com/shi00/qTrading/commit/6abcda7f17976a0c5d32f81588adbafcb46ff13d))
* AI model performance optimization & critical bug fixes ([75a40dc](https://github.com/shi00/qTrading/commit/75a40dcc63aa6f8ada64890ad5cf92e087ec148c))
* **backtest:** implement vector backtest framework ([a01d859](https://github.com/shi00/qTrading/commit/a01d8597b7f33482d9d9bf91e07592e46f1b91ed))
* **backtest:** 实现向量回测框架并修复检视发现的问题 ([bd46e0c](https://github.com/shi00/qTrading/commit/bd46e0c2f018583bcc000c96a95c5c850376279a))
* **build:** migrate to OneFolder model and introduce Inno Setup installer ([8413031](https://github.com/shi00/qTrading/commit/84130315f319906de7d959675258bc825a1d404d))
* consolidate alembic migrations into native date baseline and apply architecture changes ([dde6ee8](https://github.com/shi00/qTrading/commit/dde6ee8edfb1b5196b509388ca5bf51d39a59084))
* **core:** Refactor CacheManager for strict concurrency safety and performance ([497218c](https://github.com/shi00/qTrading/commit/497218c033577f80c26a6ba2d16fd9ce9e1a8003))
* enhance oversold strategy AI analysis ([96ffe71](https://github.com/shi00/qTrading/commit/96ffe71c1d07cca30444bf3650606ad016d31bd7))
* Implement 5-step progress UI for system initialization ([f8f4e72](https://github.com/shi00/qTrading/commit/f8f4e7261fb3d29ec783533400ed014b0b42de1b))
* implement holder_num_change and holder_num_ratio calculation ([3533da5](https://github.com/shi00/qTrading/commit/3533da5401e4ac87e008ab8a615913fcc7cca882))
* Implement MarketDataService, refactor HomeView, optimize I18n ([9776cde](https://github.com/shi00/qTrading/commit/9776cdeede26fa605087990a80ab0c0c6efa36f6))
* introduce pip-tools for dependency management ([6986500](https://github.com/shi00/qTrading/commit/6986500508223987b3c030163addcbc0a9b01491))
* **logger:** Force new log file on startup via explicit rollover ([b353ded](https://github.com/shi00/qTrading/commit/b353ded772168ca233e3a6e95f1d39bace11fd20))
* **logging:** add JSON log format option for centralized log systems ([e4269d7](https://github.com/shi00/qTrading/commit/e4269d7dac6375285cbab6e9ef9112ce49848071))
* optimize AI params, remove GitHub theme & code cleanup ([cca89cc](https://github.com/shi00/qTrading/commit/cca89cc346144239db9c213853fdabe62ba5a8d4))
* optimize TushareClient and verify init order ([ee4bf8a](https://github.com/shi00/qTrading/commit/ee4bf8a60cf2ac7c7a08463a02eb2a5e47e4bed3))
* **P1-12:** implement multi-provider fallback for cloud analysis ([5597b94](https://github.com/shi00/qTrading/commit/5597b9422c3a6af90bdbc6d026713a94d7d5b5fe))
* **rate-limiter:** 实现自适应限流与慢速API专用限流器 ([d3a2b0d](https://github.com/shi00/qTrading/commit/d3a2b0d309f7ffaed75531db90293a1dab7a7319))
* **sync:** add peak disclosure season scheduling for financial sync ([54e360c](https://github.com/shi00/qTrading/commit/54e360c15d9dce29c5aa34461bbc784bd5a5c23f))
* test infrastructure overhaul + coverage improvements (75%-&gt;91%) ([128cf08](https://github.com/shi00/qTrading/commit/128cf08b2377d288db7fc94bf825ccec7da527e1))
* UI redesign, sync optimization, and stability fixes ([80b4e38](https://github.com/shi00/qTrading/commit/80b4e38d7d0239dcf7982817b6db7b98f26b1922))
* **ui:** visually gray out verify button during token check ([08577fd](https://github.com/shi00/qTrading/commit/08577fd3f003cf6a8df6be69815d8102bfffcda2))
* upgrade CI to Python 3.14 and align requires-python ([e5ec64a](https://github.com/shi00/qTrading/commit/e5ec64a87d2b2235e442d24cecdbafabf8a8d63f))
* 优化RSI超卖检测逻辑与复盘提示中文化 ([97a352c](https://github.com/shi00/qTrading/commit/97a352ca1abb0a3edb9cd00278acfd4c43e578a3))
* 提升代码覆盖率至86%，CI覆盖率阈值调整为80% ([4e27930](https://github.com/shi00/qTrading/commit/4e27930c049e7bed4fcdeba7d3d650bb8a0a1de1))
* 添加 run_id + params_snapshot 确保筛选历史可复现性 ([d7cb13a](https://github.com/shi00/qTrading/commit/d7cb13a9409a426fe0ab14acb442e50ac47e7cd5))
* 统一交易日历服务 + 超跌策略上下文增强 + 测试覆盖完善 ([0f051d4](https://github.com/shi00/qTrading/commit/0f051d4c5df91705fd5921646491d0b2f1861faf))


### Bug Fixes

* **A-1:** Add singleton management to LocalModelManager - _initialized flag and _reset_singleton method ([9e99908](https://github.com/shi00/qTrading/commit/9e99908657682829138c4be58ce8596bb4ddf9ae))
* **A-1:** SchedulerService._reset_singleton now shuts down APScheduler to prevent ghost threads ([6190957](https://github.com/shi00/qTrading/commit/619095782c8e55a7ce32611312f1f5fcf25a143d))
* add autouse fixture to reset ThreadPoolManager singleton between tests ([a412250](https://github.com/shi00/qTrading/commit/a4122506df9a66b2889bb95444d3a6514e0e63ab))
* Add None safety for hsgt data in HomeView to prevent subscript errors ([697e5b1](https://github.com/shi00/qTrading/commit/697e5b11a5fa213a0ecd08fe1db6bd801c641eab))
* add pandas-stubs for pyright type checking ([8fca584](https://github.com/shi00/qTrading/commit/8fca584dc8b3cfd7125cd1b662d54a94abe646a8))
* add shutdown guards to check_data_health and Step 5 ([86c504c](https://github.com/shi00/qTrading/commit/86c504c07558cf23d9f4e687bdfee36aa5721f8a))
* Add step failure handling and top-level exception tracking ([07ab772](https://github.com/shi00/qTrading/commit/07ab77246b7825f6d4e5713cdb40ad057d1fa728))
* add timeout config to test_connection static method ([15dc6d6](https://github.com/shi00/qTrading/commit/15dc6d6ef66e55bcc002e7c8e89259f564382b99))
* add type: ignore[index] for gather return_exceptions results ([1e2b1bd](https://github.com/shi00/qTrading/commit/1e2b1bd756dde117f4bd1a7b338a631373796da8))
* address all known issues from code review ([aa0ce89](https://github.com/shi00/qTrading/commit/aa0ce89a14869e32338b4d3e22b0dc6f9a2569fa))
* Alembic downgrade drop order - drop screening_thinking before screening_history ([03b3896](https://github.com/shi00/qTrading/commit/03b3896cd17bed6e41aef6fd3a6da192a7015f88))
* **cache:** remove dead code branch in prefetch_auxiliary_data ([1d8a588](https://github.com/shi00/qTrading/commit/1d8a588912f50925bb2c34264c54b0e78c4616bc))
* change AI prompt dump log level from DEBUG to INFO ([7632512](https://github.com/shi00/qTrading/commit/76325124704ec77ae46961ff275b1fcb1eefebb8))
* check submit_task return value and restore button state if None. ([9e0faa9](https://github.com/shi00/qTrading/commit/9e0faa98b9959b39537a00039ea787f2320dd4ee))
* CI verify step uses package-set comparison instead of strict diff ([cf81ad1](https://github.com/shi00/qTrading/commit/cf81ad168a80bde8fc06b74ae1781365e0a40402))
* **ci:** allow pre-commit to fail on main for auto-requirements-update ([9c33968](https://github.com/shi00/qTrading/commit/9c339686d4b548029811fa3ef38f651bda34ba87))
* **ci:** fix Inno Setup translation file missing error ([e34abf6](https://github.com/shi00/qTrading/commit/e34abf6dc4d949e4d846f513357d6d5492fffb6e))
* **ci:** improve requirements auto-fix PR creation and skip tests when outdated ([71d305b](https://github.com/shi00/qTrading/commit/71d305b78f7629109446da9d9663af11c431c672))
* **ci:** replace PR-based auto-fix with direct commit for requirements update ([fc189d8](https://github.com/shi00/qTrading/commit/fc189d883ddde3608fe51e563773616410b1c53e))
* **ci:** resolve pyright CI failure - fix module resolution and suppress litellm false positives ([0a43ae7](https://github.com/shi00/qTrading/commit/0a43ae782cffa5aa524bff9b0adb53ad1b388d5f))
* **ci:** restore missing cache_manager.py tracking ([96d4250](https://github.com/shi00/qTrading/commit/96d4250231fe41e92c8a8ce62bc32dd44a83189c))
* Clear progress text on sync failure/cancellation ([a300afa](https://github.com/shi00/qTrading/commit/a300afa5a5cdccd253c3ccdbe212db341e16c8b5))
* code review P0-P2 issues and add comprehensive tests ([b33f4a0](https://github.com/shi00/qTrading/commit/b33f4a0b3c2d3c3315e6ea616fc957af83356ea8))
* Complete unified cancellation pattern migration ([057e969](https://github.com/shi00/qTrading/commit/057e969b82368f600a4038bd2216aab347abe07c))
* concurrency audit - stop/stop_async race conditions, rate limiter, thread pool, i18n, tests ([0354873](https://github.com/shi00/qTrading/commit/0354873c96d7d32fa98418a00ea269b035af0f0c))
* concurrency audit P0/P1 issues and test failures ([10bbea2](https://github.com/shi00/qTrading/commit/10bbea22283921af6df6e3dbd3c2fc755dbc1d11))
* **concurrency:** 落地05-concurrency审计项 C-P1-1/C-P1-6/C-P2-3 ([92123eb](https://github.com/shi00/qTrading/commit/92123ebd20c4c24d76c65b7062606cf28b00b815))
* Config thread safety, resource leaks, and UI issues ([b2b292f](https://github.com/shi00/qTrading/commit/b2b292fca092e1d57481f0485f20e55e38e2ad2b))
* **config:** fix pydantic validation issues causing test failures ([d3ab9b7](https://github.com/shi00/qTrading/commit/d3ab9b718bfed9b45a676c0daaf7ce44186359ff))
* **core:** Address Pyright warnings, BigInt overflow, and connection pool shutdown ([5e8aaa4](https://github.com/shi00/qTrading/commit/5e8aaa434df04bf516ad99c0d69c3ce9055b4535))
* **core:** Resolve Alembic logger override and startup exceptions ([eb4e411](https://github.com/shi00/qTrading/commit/eb4e411f553852e012ea24b4fb34e67e8845683e))
* **core:** resolve litellm/tiktoken loading and PyInstaller build issues ([f887767](https://github.com/shi00/qTrading/commit/f887767b13defe1dd9b1fa5fbe27be5bef4cdd42))
* Correctly handle cancellation message by reordering checks ([77fc2de](https://github.com/shi00/qTrading/commit/77fc2de84ffe631b0d3005830c34116cfa2fc271))
* Critical safety fixes for hard_reset (Active Reader Blocking + Error Reporting) ([aa1c286](https://github.com/shi00/qTrading/commit/aa1c286d2805497f70438af1df2c9c0001f9c32e))
* critical security and correctness fixes with test coverage ([02e29c4](https://github.com/shi00/qTrading/commit/02e29c4fb44152fba88c9cd4c699c1d8a15d3d49))
* **DAO:** dividend PK mismatch (3-col) + screener_dao wrong _save_upsert params ([384955f](https://github.com/shi00/qTrading/commit/384955fc7c925dedc3a3e8da30cca6cc392a1a31))
* **dao:** migrate buggy dataframe-level null conversion to hyper-fast robust native loop checking for Pandas 3.0+ asyncpg compat ([7cb5a92](https://github.com/shi00/qTrading/commit/7cb5a92ea342ced63b700c4039b53390fe5115ef))
* **dao:** use strict trade_date &lt; as_of in learning context query ([4d11692](https://github.com/shi00/qTrading/commit/4d11692573714f5f96733b2076a0271c6d22389e))
* data health check O(Quarter) strategy + Flet UI race condition fix ([a82a505](https://github.com/shi00/qTrading/commit/a82a505b721d172b1b52c8f0350720a8d7704a79))
* **data_source_tab:** 深度修复任务生命周期和事件传递问题 ([a98f73b](https://github.com/shi00/qTrading/commit/a98f73b7dee5fc92266ac6a760d07e18534daaf5))
* **data:** add Decimal type compatibility for PostgreSQL Numeric columns ([d132539](https://github.com/shi00/qTrading/commit/d13253905f6748966aebc7199d30031d4ec0f90b))
* **data:** improve financial report dedup to consider ann_date ([ade88f2](https://github.com/shi00/qTrading/commit/ade88f2cc608244195f1818858db3c0e2b850169))
* **data:** improve financial report dedup with update_flag support ([ccb8e04](https://github.com/shi00/qTrading/commit/ccb8e047fac808b04ffc728e3133e3ca30a0caae))
* **data:** optimize trade calendar sync architecture and testing ([6758f87](https://github.com/shi00/qTrading/commit/6758f87abfbd4d6d3e37c6ed65fa397321457650))
* **data:** strictly propagate CancelledError in BaseDao to prevent task zombie execution ([fd1cefd](https://github.com/shi00/qTrading/commit/fd1cefd0d7c5652d2a298a7d67f8956bc8147b6c))
* **DB-P0-1:** DAO disposed时抛出EngineDisposedError而非静默吞写; 重排shutdown步骤确保flush在close之前; 补充15个测试用例覆盖disposed异常和步骤顺序 ([d473aa9](https://github.com/shi00/qTrading/commit/d473aa95af25c2cf55c1045078e6959034a0b4e7))
* **db:** align alembic migration schema with sqlalchemy models and add check to windows CI ([5fd656e](https://github.com/shi00/qTrading/commit/5fd656e2bd6fd15fd8933497645e3dc7c24b5768))
* **db:** make database upgrade mandatory with improved UX ([9cbb779](https://github.com/shi00/qTrading/commit/9cbb779c1635defdbb2c768c71171c87890c26b9))
* **db:** migrate Float to Numeric for financial precision ([a9f7296](https://github.com/shi00/qTrading/commit/a9f7296f6c3fa6558a3dc51456f6fb9a8e788e4d))
* deep review - batch query, color bug, redundant indexes, test improvements ([3b74bbd](https://github.com/shi00/qTrading/commit/3b74bbd56648b730244e66fbc13c8f5fd193ad31))
* Enable auto height for DataTable rows in DataExplorerView ([c2c09f3](https://github.com/shi00/qTrading/commit/c2c09f34150dbf06c3afb3faac5d7aacc3696e1c))
* Ensure DataProcessor.stop() triggers unified cancellation on window close ([0e2b199](https://github.com/shi00/qTrading/commit/0e2b199fd3378e89ee23dc4ce055abdc6d26e73f))
* **error_classifier:** add explicit handling for LiteLLM permanent errors (P1-17) ([ef7cb7d](https://github.com/shi00/qTrading/commit/ef7cb7d9a67d3d06e68c1af48a337f9af7444b6a))
* financial_reports merge before save to eliminate missing columns warning ([68c04a5](https://github.com/shi00/qTrading/commit/68c04a523fd68fe5064fa1766e900cdd036cd90d))
* Fully internationalize sync failure messages with format strings ([a96840d](https://github.com/shi00/qTrading/commit/a96840d7d38da83f882e10b5ce4a2643cd04ec82))
* Handle None tags in news item to prevent AttributeError ([8bab03d](https://github.com/shi00/qTrading/commit/8bab03d61c4897c3f72dbbbe5efe3ce3d079bdca))
* Handle None values in news feed display to prevent crashes ([cfc9313](https://github.com/shi00/qTrading/commit/cfc9313b7e93a77ce42beac7515899cbf136faa9))
* harden doubao auth state refresh ([fe61b0c](https://github.com/shi00/qTrading/commit/fe61b0c2e8d4d3e3f15ad370faf06d17b461ecb2))
* harden shutdown and doubao automation flows ([ecba623](https://github.com/shi00/qTrading/commit/ecba62340c6804d8cf508f52855a951408819f64))
* harden sync quality resume logic and low-frequency scoring ([39eb475](https://github.com/shi00/qTrading/commit/39eb475a350d5b93889937cb9d71d81a89990af2))
* Harden UI views against potential NoneType errors from API data ([8c37090](https://github.com/shi00/qTrading/commit/8c3709058958826c8fd6b80716988ac2bdefb787))
* **health:** add 5% tolerance to depth check + improve warning message ([c8291db](https://github.com/shi00/qTrading/commit/c8291dbf79a31fed6a088cc8813af862cd3095f8))
* **health:** code review fixes - remove duplicate import, add CANCELLED guard, strengthen test mocks ([6bfcee6](https://github.com/shi00/qTrading/commit/6bfcee6e4bfbd93a98d68129ecea18d631863e93))
* **health:** depth check always-fail algorithm + duplicate task submission ([ddb0803](https://github.com/shi00/qTrading/commit/ddb080336c8e02f21cde45d78ad9b23a28b49f9c))
* **health:** implement all 4 fixes from implementation_plan.md ([77b8098](https://github.com/shi00/qTrading/commit/77b80981069e09efa3afdcd2d3a7f0e358fa2cd7))
* **historical:** clear shutdown flag before resume sync run ([6fef6c9](https://github.com/shi00/qTrading/commit/6fef6c9d742cebb1d724bf697059395b65e5f08b))
* I18n for no-proxy hint text ([a394a57](https://github.com/shi00/qTrading/commit/a394a574c1ec11bc440d8b0f4fd41cff81d33658))
* I18n missing keys for no-proxy settings in System Tab ([5e04719](https://github.com/shi00/qTrading/commit/5e04719ef01e217237d6b7748e57afe02891c2d5))
* **i18n:** add missing comma in db_upgrade_migration entry causing JSON parse failure ([8ff9331](https://github.com/shi00/qTrading/commit/8ff9331c189cee25d6df9e3bacba6e177b6ac9b1))
* **i18n:** 完善策略名称翻译覆盖 ([f875013](https://github.com/shi00/qTrading/commit/f875013813d18198ba8ede814a5fc9ff8e0b9383))
* **i18n:** 添加 app_state 表的翻译键 ([928fa88](https://github.com/shi00/qTrading/commit/928fa882873e719d8b0d538dedd8844353dfc133))
* **i18n:** 补齐 data_dictionary 中 6 个缺失的翻译 key ([a684d5b](https://github.com/shi00/qTrading/commit/a684d5bc1aaa451b56a60c3d2968afc9e365b8c3))
* improve asyncio task lifecycle management and table UI centering ([bca646c](https://github.com/shi00/qTrading/commit/bca646cae801ae1184dfc3b4ceef413fd2b8dd27))
* improve shutdown handling and batch processing for sync tasks ([d3904a5](https://github.com/shi00/qTrading/commit/d3904a564edcf6e0c2b56db5e94178fee1ae0183))
* Improve sync_stock_basic with proper logging and error handling ([66ce465](https://github.com/shi00/qTrading/commit/66ce465df972226cb4241c0f47ad0f1c763b61a0))
* improve TRUNCATE error visibility and fix async context manager mock ([e67c714](https://github.com/shi00/qTrading/commit/e67c71407eb81f8bb60d84bc0484146f15cade95))
* Internationalize generic init failure message ([ad518f4](https://github.com/shi00/qTrading/commit/ad518f443cc3bc3e6b05961da810f409abf53f25))
* **lint:** resolve 12 ruff check warnings in unit tests ([3a9b287](https://github.com/shi00/qTrading/commit/3a9b287bd10654dcc61692afd1700fe268df1a57))
* Localize news category tags (Macro, Policy, etc.) for Chinese UI ([447babc](https://github.com/shi00/qTrading/commit/447babcf55c203c69d03191cc078f9b6b90365d4))
* Localize progress messages in DataProcessor ([99dc5c1](https://github.com/shi00/qTrading/commit/99dc5c18e9d93f9764bd936bef2280dc63172bf3))
* **logger:** Apply startup rotation logic to error.log ([b156717](https://github.com/shi00/qTrading/commit/b156717926585638e6c67ca4b2fb5395eb4dcb4a))
* make _on_input_change handle None event gracefully ([f63046a](https://github.com/shi00/qTrading/commit/f63046adabf8a0e376e2d50f87025064e141e70f))
* **manager:** use AUTOCOMMIT to prevent PostgreSQL InFailedSQLTransactionError contagion and fix MAX() syntax ([70c53e4](https://github.com/shi00/qTrading/commit/70c53e4e2355f5fad4341ac1fb132beee82641b7))
* **market:** rename TechnicalBreakoutStrategy to VolumeBreakoutStrategy (P1-19) ([23419ce](https://github.com/shi00/qTrading/commit/23419ce28b970b27a34d0cb9aca5af9b25f02a31))
* medium priority fixes - security and code quality ([b7be563](https://github.com/shi00/qTrading/commit/b7be5637e7ce7f5dd55fddae64ad57ad93459067))
* merge alembic migration scripts and fix schema drift ([3482671](https://github.com/shi00/qTrading/commit/348267128e9c901c37173301cce4dbfb157d3c98))
* **models:** remove redundant index=True from primary key columns ([7706c02](https://github.com/shi00/qTrading/commit/7706c02bf372b38634e3ef389c48e042f02a6dff))
* ModuleNotFoundError by updating data.ai_client imports to services.ai_service in legacy files ([4130f2d](https://github.com/shi00/qTrading/commit/4130f2dbe8e22ee60ad702029308f3ac37b480e4))
* narrow exception types in DB degradation paths caused CI failures ([7977187](https://github.com/shi00/qTrading/commit/7977187c9d3bffdd862dc06cc51174fedec62587))
* **news:** correct timestamp sorting logic and add configurable poll interval ([09d0ac9](https://github.com/shi00/qTrading/commit/09d0ac96a82224ae6c6717a5eb3c921b8ca80cab))
* np.issubdtype incompatible with pandas StringDtype (pandas 3.x) ([3607cc8](https://github.com/shi00/qTrading/commit/3607cc8dfe11ab4fcc0631473df65a442d0d3a19))
* optimize AI client timeout and improve cleanup logging ([11f1166](https://github.com/shi00/qTrading/commit/11f116688331d27376552e406fd2f6df92c38271))
* Optimize localized error message display (avoid double prefix) ([8759df5](https://github.com/shi00/qTrading/commit/8759df54eb19076ea352da9649cc5d7fc02c6c55))
* **oversold_strategy:** use qfq-adjusted prices in support context (P1-18) ([7b445af](https://github.com/shi00/qTrading/commit/7b445af88201ed80286b68d09322c3d7d9396936))
* P0 issues - I18n reverse dependency, proxy env pollution, type:ignore reasons ([17e9d34](https://github.com/shi00/qTrading/commit/17e9d342fa37f595b708ac3dd1903a4419018279))
* P0 issues comprehensive fix and review remediation ([fb24190](https://github.com/shi00/qTrading/commit/fb24190108bb32fd773eb6227e80607bd4763a30))
* **P0-1:** NorthboundFlowStrategy structural fix - use market flow as gating signal ([059665a](https://github.com/shi00/qTrading/commit/059665a0c90b52d5f73a2e82a5bbea1dee59657b))
* **P0-1:** sort by trade_date before .first() and add pe_ttm&gt;0 filter ([7ebe1ef](https://github.com/shi00/qTrading/commit/7ebe1efc772934e4c7655530b7beeb0b86bf2d19))
* **P0-3:** mark empty-data stocks complete to prevent infinite retry ([6fdc70b](https://github.com/shi00/qTrading/commit/6fdc70bb2ab38a496a8aae6e7a2a7f998eb5a179))
* **P0-4:** add as_of parameter to get_us_major_moves to prevent look-ahead bias ([0d466f3](https://github.com/shi00/qTrading/commit/0d466f3532c9121911195dcdf1154af434f15ac4))
* **P0-4:** correct datetime vs date type mismatch in look-ahead guard ([29d8f12](https://github.com/shi00/qTrading/commit/29d8f12e49c0ed5909ced31cf74dbcc439e209d6))
* **P0-5:** add as_of parameter to get_learning_context to prevent look-ahead bias ([34bed8a](https://github.com/shi00/qTrading/commit/34bed8a4701bbf73d463e16802aa232513a44076))
* **P0-5:** add defensive datetime-to-date conversion for as_of parameter ([48b0109](https://github.com/shi00/qTrading/commit/48b0109eb177e22aeccc11ca814c8436ebba1759))
* **P0-6:** propagate DatabaseMigrationNeeded from CacheManager to caller ([8a7d35b](https://github.com/shi00/qTrading/commit/8a7d35b66c3743c74ca8fd4eb46589ddf2ed6964))
* **P0-7:** extract bootstrap module from main.py, remove blanket pragma no cover ([267712e](https://github.com/shi00/qTrading/commit/267712e22b4b48a3f6ce289ab032c61258dbe7fb))
* **P0-8:** add smoke test subset for E2E, replace blanket skip with conditional skip ([1324419](https://github.com/shi00/qTrading/commit/1324419c5f5c04a183c90f8849d49f7f425f4474))
* **P0-8:** replace Playwright with urllib for server reachability check ([a390689](https://github.com/shi00/qTrading/commit/a3906894f5e744eeaaf35186c84524f9d4d26a62))
* **P0-9:** add chunked execution to BaseDao._save_upsert to prevent OOM ([d4ecf3d](https://github.com/shi00/qTrading/commit/d4ecf3d184a14604d85219e23d89b2138fcc958b))
* **P0:** resolve 3 critical issues - auto migrate, empty financial data, resume semantic gap ([79343c1](https://github.com/shi00/qTrading/commit/79343c17c3757e26a633fe3bb0803128bdcd915c))
* P0全量检视 - 补充6个关键测试用例，杜绝修改引入问题 ([7706b49](https://github.com/shi00/qTrading/commit/7706b4946ec4f08150309890c3dd19f0b0959912))
* **P1-12:** use litellm.exceptions for proper import ([b8c1e4a](https://github.com/shi00/qTrading/commit/b8c1e4ad2d39a56eea9055d871ad6542c3025a45))
* **P1-13:** enable JSON mode for streaming output ([7fed234](https://github.com/shi00/qTrading/commit/7fed234477346f8fbf35f92b06b448195397cb0d))
* **P1-14:** downgrade ui_prompt_override from system to user role ([4f42203](https://github.com/shi00/qTrading/commit/4f42203c7860038647e15e5f1421fa91ae64800f))
* **P1-15:** add prompt template consistency test ([31c08c9](https://github.com/shi00/qTrading/commit/31c08c983a690e1092c3fc40c7afcad09a53f3e2))
* **P1-15:** add prompt template consistency tests ([533e2e5](https://github.com/shi00/qTrading/commit/533e2e597397e021957338cf14b2b136d58a6d7c))
* **P1-26:** add TushareAPIPermissionError for capability tracking ([5dbea60](https://github.com/shi00/qTrading/commit/5dbea6086bc015d2d0250ed0c6e311bdd5ca1de6))
* **P1-27:** extend Tushare API rate limit config with slow and fast API tiers ([d8c34b4](https://github.com/shi00/qTrading/commit/d8c34b4c75d2a4b90b34be841a820d2aca040352))
* **P1:** Fix DataProcessor.stop() TypeError - don't pass sync strategy.cancel() to asyncio.gather ([84a4a79](https://github.com/shi00/qTrading/commit/84a4a7959d0887cd06b47b7bf103b0d433b8c624))
* parenthesize multi-exception except clauses for Python 3.13 compat ([f510efb](https://github.com/shi00/qTrading/commit/f510efbe01a098595ba28c181cbf779a7d09f9bb))
* patch path errors, MagicMock await error, and TRUNCATE ordering ([7181565](https://github.com/shi00/qTrading/commit/7181565b1ecd5163fd734e5b8d77dacd9d0f944b))
* Phase 2 code review - exception handling, DataDictionary alignment, loop-local refactor ([8830864](https://github.com/shi00/qTrading/commit/88308640f9dc034eeac8c28a0f1c837fc6b0b58e))
* **pip-audit:** handle YAML date object in reevaluate_at field ([ec03647](https://github.com/shi00/qTrading/commit/ec036471e4cd1790fe48a7cc372907d271a3b048))
* pipeline test failures and improve test coverage ([3b81254](https://github.com/shi00/qTrading/commit/3b81254980008f603a2607bdc706d914cdb8b99c))
* Propagate cancel_event to sync strategies to enable sync cancellation from UI ([c416cb0](https://github.com/shi00/qTrading/commit/c416cb0392fb97c377234b920364490575339d0d))
* pyright sort_values type error in ai_strategy.py ([a888c7e](https://github.com/shi00/qTrading/commit/a888c7eebb63faa41fb3c77bb4707dc9c380ca66))
* **pyright:** fix DataFrame __bool__ and Union string annotation errors ([d9296f5](https://github.com/shi00/qTrading/commit/d9296f50443dc624d94d4630ee06b97543f9f50e))
* **pyright:** set pythonVersion=3.13 to match runtime ([c0fe8f9](https://github.com/shi00/qTrading/commit/c0fe8f9c0677173213990b4ed79c74f7d0fb21f7))
* pyright类型检查修复 - test_review_round_trip: 用inspect.signature替代运行时调用 - review_manager: 修复6个类型警告 - conftest: 添加__all__导出声明 ([5227aa0](https://github.com/shi00/qTrading/commit/5227aa056805ae64aa459afbc249177c9d506bcb))
* Race condition in trade calendar sync (await queue.join()) ([f1f6c43](https://github.com/shi00/qTrading/commit/f1f6c435d0d791600423da0286616aeb99e2d39f))
* **regression:** Restore missing _on_tab_changed method in DataExplorerView ([836f2dd](https://github.com/shi00/qTrading/commit/836f2ddfca5c3d288562b1b481cf9030f2ff9bef))
* **regression:** Restore missing UI build logic in data_view.py ([4fe909a](https://github.com/shi00/qTrading/commit/4fe909a7387eaaeae990fc1df923557a5fbf24d9))
* remove await from page.window.destroy() to fix pyright test error and shutdown bug ([d2d415f](https://github.com/shi00/qTrading/commit/d2d415fe5d7bfb37768a09a360dc67a42b890b0e))
* Remove dead code and add Step 3/4 failure detection ([55e012d](https://github.com/shi00/qTrading/commit/55e012d6e9c05ce0d9cbe345e25ad1f133e93097))
* remove diskcache from requirements, ignore CVE-2025-69872 in pip-audit ([6bfcbf6](https://github.com/shi00/qTrading/commit/6bfcbf6200e800ea38656dc5e63b4888a73a7e86))
* remove non-existent fields from stk_holdernumber API request ([5344f74](https://github.com/shi00/qTrading/commit/5344f743325952abb4dab2f3d8bcd389656218cd))
* Remove unused loop variables and add date format validation ([bffac92](https://github.com/shi00/qTrading/commit/bffac92deac6d977bb6c8b4ba58babd29c44aee6))
* replace hardcoded dates with dynamic date variables to fix asyncpg DataError ([dfb0670](https://github.com/shi00/qTrading/commit/dfb067016c8c30a502f4b7f219210e92bf15c980))
* replace hardcoded DB passwords with env vars for CI compatibility ([56bb27f](https://github.com/shi00/qTrading/commit/56bb27f2e1f9ef32b398c2e1624810d600383dc7))
* Replace incorrect I18n.t() with I18n.get() in MarketDataService ([eda13a9](https://github.com/shi00/qTrading/commit/eda13a98b55a8e076e7738f6432588cfbbc052cc))
* resolve 3 integration test failures ([d1187be](https://github.com/shi00/qTrading/commit/d1187bebfbd12ada3b3ab503149dac6858727374))
* resolve all 5 residual risks from code-review3-audit ([3ce7526](https://github.com/shi00/qTrading/commit/3ce75268702ca65d39d539f167320b2909a70e55))
* resolve all P0 issues with test coverage and hardening ([6d4eadc](https://github.com/shi00/qTrading/commit/6d4eadc61f2097846a0de9e1f6983aad0dfe4e63))
* resolve all P0/P1 issues from code-review2 and reorganize test suite ([ce4a6f9](https://github.com/shi00/qTrading/commit/ce4a6f9f63551f68bbe3e3101f3f07b61ccf7733))
* resolve all P0/P1/S issues from code-review.md ([540e058](https://github.com/shi00/qTrading/commit/540e058c1484f438866bcc695248660219ff9175))
* resolve all pyright type check errors with type: ignore annotations ([bc05cf8](https://github.com/shi00/qTrading/commit/bc05cf865ddb104185bed981244c0af86b55aab0))
* Resolve AttributeError _ui_built in DataExplorerView ([ef62da7](https://github.com/shi00/qTrading/commit/ef62da77fe7cda891387090c8dce0fe86745dd99))
* resolve audit code quality issues (Q-P1-3, Q-P1-6, Q-P2-1, Q-P2-4, Q-P2-7, Q-P2-8) ([62f182e](https://github.com/shi00/qTrading/commit/62f182e5ada18464daf0fb78f4ac2c1a7e00ac06))
* resolve CI integration test hang caused by 4 cascading defects ([82185de](https://github.com/shi00/qTrading/commit/82185de3159e3c94e4b9ed40b4e78e46ea8e46b6))
* resolve code-review1.md issues and improve CI coverage config ([c57bef6](https://github.com/shi00/qTrading/commit/c57bef6cfa2b9c5dd8578ba47c738a8ea1c4ec90))
* resolve concurrency audit issues C-P1-1 through C-P2-2 ([94506ce](https://github.com/shi00/qTrading/commit/94506cebfd3f5ae1d7175a625a7aa7fd335feca0))
* resolve import errors after moving classify_error ([c06291a](https://github.com/shi00/qTrading/commit/c06291ae9d30fc680a10581a7e3ef39f017f091d))
* resolve multiple system issues, update tests and dependencies ([6031e5b](https://github.com/shi00/qTrading/commit/6031e5b683db64dceb162045425908f494e1c178))
* resolve pyright BaseException not iterable error in health check ([9914d21](https://github.com/shi00/qTrading/commit/9914d2107100e262771286508f56c794d3996d11))
* resolve pyright type errors in data modules ([1951665](https://github.com/shi00/qTrading/commit/195166524c536d2ff25de12a26a6573abe77b5fe))
* resolve pyright type errors in tests and utils ([b8e5bb3](https://github.com/shi00/qTrading/commit/b8e5bb387c9437e8280ac90fdc33f8e3831702bb))
* resolve silent failure bugs from audit report with test coverage ([d4118c1](https://github.com/shi00/qTrading/commit/d4118c196384be144080543405e54f9881afe98a))
* resolve type checking errors across 4 files ([4a3bc9d](https://github.com/shi00/qTrading/commit/4a3bc9d2ecf117f664dd93b0b3ad59ae87505c70))
* resolve type error in extract_method_source indent_level ([a3353bf](https://github.com/shi00/qTrading/commit/a3353bf69168f679d6055b86e471f7e428ff978e))
* review fixes - health_cache/docs/test_name ([4c0d316](https://github.com/shi00/qTrading/commit/4c0d3161eba0488b49a46645870e4b984640834b))
* **runtime:** Fix I18n NameError and ThreadPool reload race condition ([cb5f58d](https://github.com/shi00/qTrading/commit/cb5f58dceda77953d43203d6b610ae54d3b747a8))
* RuntimeWarning for unawaited DataProcessor.stop ([9985cda](https://github.com/shi00/qTrading/commit/9985cdaa2008420b1792e706c0ae2d53ffb851bc))
* screening_history run_id 全链路贯通 ([64608ea](https://github.com/shi00/qTrading/commit/64608eaf1653a2c2da1909c79e0d272619d956c4))
* **shutdown:** harden close flow and deterministic cleanup ([85d5e33](https://github.com/shi00/qTrading/commit/85d5e33e3065eb318ab5c664039a47e0bec35622))
* **shutdown:** improve graceful shutdown logging ([f0566f1](https://github.com/shi00/qTrading/commit/f0566f1ab862737f6ba84488d110bf0a4c8a1922))
* **shutdown:** per-step exception isolation and test coverage gaps ([c7a0e01](https://github.com/shi00/qTrading/commit/c7a0e018bc3323779671b764ab09e01919c3e02f))
* Step 2 failure now aborts initialization ([c9f00ff](https://github.com/shi00/qTrading/commit/c9f00ff1b9094e84b9fdd3940ac73aa56c0f0ca6))
* Step 3 historical sync improvements ([5cfa7ce](https://github.com/shi00/qTrading/commit/5cfa7cea64c52d989df4422f949512fb7d62db36))
* Step 4 financial sync improvements ([da258e6](https://github.com/shi00/qTrading/commit/da258e6364c534a88f232c8420e265026aa03c94))
* Step 4 now aborts on failure with consistent SyncResult handling ([3323921](https://github.com/shi00/qTrading/commit/33239217ab53391da8723a9ae26d0ad3527743b7))
* Step 5 health check improvements ([9ba3d4e](https://github.com/shi00/qTrading/commit/9ba3d4efc55e0d87f124b9b29ab39152854c7c62))
* **strategies:** add thread-safe lock to strategy registry ([35d2da8](https://github.com/shi00/qTrading/commit/35d2da85941f06d04200e2d1988a8556254d0e4e))
* **strategies:** use amount-weighted average for block trade price ([9d48aec](https://github.com/shi00/qTrading/commit/9d48aec5bd50b5174c75eba71dba7808f338e66d))
* **strategy:** add runtime validation for pct_chg_min/pct_chg_max params ([44aa781](https://github.com/shi00/qTrading/commit/44aa7815e3306639c2e054cd0013a912d49c44b7))
* **strategy:** P1-15 STRATEGY_PROMPTS 与 prompt_validator 字段对齐 ([11328b9](https://github.com/shi00/qTrading/commit/11328b975086135e9f87f384cef056fd277eb7de))
* suppress pyright error for intentional TypeError test ([f515b0b](https://github.com/shi00/qTrading/commit/f515b0b76d41ac14a4e3ae2ff6bc1097fbbad187))
* Sync UI sort state with logical sort state in DataExplorerView ([6801782](https://github.com/shi00/qTrading/commit/6801782c51dcac77046bb8e274ed095264d00cae))
* sync_status SQL monotonic protection and NULL handling ([b7641a0](https://github.com/shi00/qTrading/commit/b7641a0f5dd967917508d4b45d4ed355e6bd12fa))
* **sync,ui:** rewrite Tushare sync to O(Quarter) & fix Flet double-update race ([5bc0204](https://github.com/shi00/qTrading/commit/5bc020436f83b8ad38ac955fd1e9ec19d506cccf))
* **sync:** empty financial data no longer marked complete, allows future retry ([bee8c7d](https://github.com/shi00/qTrading/commit/bee8c7d53dfaaed5ff69e81b6661be28b77262dd))
* **sync:** Incr AI timeout, rm misleading warn, log errors ([25253fd](https://github.com/shi00/qTrading/commit/25253fd61be9c1f20494c2a1ef5e3e50a78f7375))
* test DB connection and coverage improvements ([0390dd2](https://github.com/shi00/qTrading/commit/0390dd2ec6b4a41dc856b5f843c8413787a25b9c))
* **test:** add None check before 'not in' operator for type safety ([461d041](https://github.com/shi00/qTrading/commit/461d04114c548ac6a76c094c3b338589060c12f4))
* **test:** add type annotations and assertions for captured_factory in backtest tests ([5a21758](https://github.com/shi00/qTrading/commit/5a217583c9135b5ebd7ffc775f6f051c4dd56fe4))
* **test:** add type assertion for detail field in test_bootstrap.py ([0cacb98](https://github.com/shi00/qTrading/commit/0cacb986151ecd3dbc7f01238b5be58c6901ffa5))
* **test:** add type ignore for optional playwright dependency ([235e257](https://github.com/shi00/qTrading/commit/235e257cba1446eb1b6366cd9bae3c1058951092))
* **test:** correct alert_listeners test to inspect _fetch_and_notify instead of _processing_loop ([577f270](https://github.com/shi00/qTrading/commit/577f270d9b602dff1999cb4bd800da1406a1bf40))
* **test:** correct mock setup for breakpoint resume test (P1-21) ([a942c65](https://github.com/shi00/qTrading/commit/a942c65553c26a7b28f0b76cd0c2334984630050))
* **test:** enable branch coverage and strengthen interruption recovery test ([5a45304](https://github.com/shi00/qTrading/commit/5a4530488e591da18aec852aed8e4cf53025567c))
* **test:** increase timeout in shutdown recovery test to prevent flakiness on slow CI runners ([e659956](https://github.com/shi00/qTrading/commit/e659956600b1e23c05398ddada07aa12a7519c46))
* **test:** inject test_engine to ai_core tests to ensure DB initialization ([a0e2949](https://github.com/shi00/qTrading/commit/a0e294971b36629064fdc796caaac59a318a534a))
* **test:** P0-8 E2E tests conditional skip instead of unconditional skip ([23aa918](https://github.com/shi00/qTrading/commit/23aa918d164393c7b15e71bf718db704ed7ae150))
* **test:** prevent test isolation contamination in calendar ranges ([c16fd7d](https://github.com/shi00/qTrading/commit/c16fd7d2a28f00719f764faaac2353c5540a80b4))
* **test:** resolve 94 test failures in CI pipeline ([02a487e](https://github.com/shi00/qTrading/commit/02a487e44cb1cceb098da966b285781057c01885))
* **test:** resolve CI hang caused by FakeCoordinator mock mismatch ([2493937](https://github.com/shi00/qTrading/commit/2493937edf304116339c23689fa4ae176c66b78b))
* **test:** resolve CI pipeline failures in database testing infrastructure, i18n, and async fixtures ([0dc855b](https://github.com/shi00/qTrading/commit/0dc855b1bd440898a1432b3f94d77bc475fe613e))
* **test:** resolve db_config and financial_sync test failures ([a476d06](https://github.com/shi00/qTrading/commit/a476d069618c39b1e4de677bfa3883bc34859857))
* **test:** resolve infinite loop in _persistent_worker tests ([2f49fff](https://github.com/shi00/qTrading/commit/2f49fff1537cd1af1e21f71784cbe2cc89a33d11))
* **test:** resolve integration test failures - asyncSetUp lifecycle and ThreadPoolManager shutdown ([b94be02](https://github.com/shi00/qTrading/commit/b94be0269e244ca41eda4a3610c16d427099a8bf))
* **test:** resolve pytest-asyncio and IsolatedAsyncioTestCase conflict by setting asyncio_mode to strict ([fa22fc0](https://github.com/shi00/qTrading/commit/fa22fc043d7cb209154b84ee21cd6e27d138b1e1))
* **test:** ruff auto-fix ([78f4574](https://github.com/shi00/qTrading/commit/78f4574c096fb1155f43ad889bb69caf950a15d7))
* **tests:** fix extract_cols_from_method regex for multi-line get_model_columns calls ([8c13526](https://github.com/shi00/qTrading/commit/8c13526d68d84f93732eaa2c734ecda1cdafea94))
* **tests:** fix P0/P1/P2 issues from unit test code review ([d3148e1](https://github.com/shi00/qTrading/commit/d3148e11d5982ae7e4cf2072491d92dd597d5a00))
* **tests:** fix UI test quality issues from code review ([c5c6799](https://github.com/shi00/qTrading/commit/c5c6799740c26a1dcaa067c71d2e174efbb65fc5))
* **Tests:** Isolate keyring & SecurityManager in conftest to prevent env pollution ([5f27c83](https://github.com/shi00/qTrading/commit/5f27c8350528f208b2351f62aa1fc340aa0815a7))
* **tests:** low-risk improvements from unit test code review round 2 ([5ef0aa5](https://github.com/shi00/qTrading/commit/5ef0aa5cb805d6fb2443dfdd5e8cc117cf3217e5))
* **tests:** resolve integration test failures caused by database migration mismatch ([34d42de](https://github.com/shi00/qTrading/commit/34d42deb4d9ac4752d46fd6cf5694ae4c9c22fc0))
* **tests:** update tests for P1-12 and P1-14 changes ([2b42da4](https://github.com/shi00/qTrading/commit/2b42da4ca6e2f9ee1e73777dc80dfbf165e8e602))
* **tests:** 修复单元测试代码检视标准P0/P1问题 ([8d877cb](https://github.com/shi00/qTrading/commit/8d877cb14cd13341155f4fd594a103aec022c679))
* **test:** update column type assertions from Float to Numeric after P0-11 migration ([209ab3c](https://github.com/shi00/qTrading/commit/209ab3c819f26d1ae0765f43ed202da0d8d03692))
* **test:** update expected exit code for graceful shutdown tests ([1b4fa85](https://github.com/shi00/qTrading/commit/1b4fa85e534d7230afef674abb6b8472f6973450))
* **test:** update Flet API usage for v0.28.3 compatibility ([51e7113](https://github.com/shi00/qTrading/commit/51e7113d5e9475408695103cf2ba7e3f15aa4e61))
* **test:** update strategy key name in i18n test (P1-19 follow-up) ([01236de](https://github.com/shi00/qTrading/commit/01236de1fb327b9f34ae88a81135189627c8c88f))
* **test:** use custom async context manager for CancelledError test ([18f1bd1](https://github.com/shi00/qTrading/commit/18f1bd1093f071a7a4130c04044d029cd8433026))
* **test:** use try-except instead of pytest.raises for CancelledError ([60ed630](https://github.com/shi00/qTrading/commit/60ed63004bab5b401fb563663bbac35d6974631a))
* **test:** 移除未使用的 mock_page 变量和 asyncio 导入 ([2ea9102](https://github.com/shi00/qTrading/commit/2ea910213b5591cc5b5b9e7f6ac999bc6d27325e))
* type check errors - index and return type issues ([5a4d2df](https://github.com/shi00/qTrading/commit/5a4d2df502ce87f7b796718a75335f2e0b980b85))
* **type:** add assertions for lazy init singletons to fix type checking errors ([eb138ac](https://github.com/shi00/qTrading/commit/eb138ace043e9d370811db67e1671ca1a9f6cd5f))
* **type:** preserve decorated singleton class types ([e8bcf6e](https://github.com/shi00/qTrading/commit/e8bcf6e33a65aa2f3e3253a2f624c0ac5f451aa6))
* **type:** resolve optional operand error in trade calendar service ([167df4c](https://github.com/shi00/qTrading/commit/167df4c6a5deaa5144b6b29b48ced2f68c92b970))
* **type:** resolve pyright OptionalOperand errors in trade_calendar_service ([f9aa65a](https://github.com/shi00/qTrading/commit/f9aa65aa48810c13bf8f131578d89af3d932fffc))
* **types:** add TypedDict return type for initialize_services ([8731f02](https://github.com/shi00/qTrading/commit/8731f02cfc014c12b0021f840da8e36b7e6a8ec8))
* **types:** resolve Pyright reportOptionalIterable errors by properly typing decorators ([cdde1ea](https://github.com/shi00/qTrading/commit/cdde1ea45f6e968b8b336e2f5bfbdfc703db13de))
* **types:** resolve Pyright type errors and harden CI/CD type safety ([c3c9474](https://github.com/shi00/qTrading/commit/c3c947407096285bced46b3d519af2f6cd0f2fc6))
* **types:** resolve pyright type errors and satisfy pre-commit hooks ([0533c47](https://github.com/shi00/qTrading/commit/0533c473cb1200b969eb4bd1296dfe1152f92e98))
* UI incorrectly showing success when sync failed (added return value check) ([d9a24f2](https://github.com/shi00/qTrading/commit/d9a24f2af24b4c40385c6ede2b314f039aa34301))
* **ui:** add CancelledError handling for health check task ([51b0c82](https://github.com/shi00/qTrading/commit/51b0c826c6110acf1ee259a3b6373c400ea04116))
* **ui:** add visual feedback on tushare validation double-clicks ([15e08bc](https://github.com/shi00/qTrading/commit/15e08bc832cfcf9fcf8dd41bc2c4af5594d52b8d))
* **ui:** correct verify button instance name to resolve attribute error ([5a22435](https://github.com/shi00/qTrading/commit/5a224350cd755563465ed6c7a7da979b4efadf87))
* **ui:** deep review - fix task cancellation race conditions and UI recovery ([6312543](https://github.com/shi00/qTrading/commit/631254319ea19b1b1dcc77614abad5f40435c067))
* **ui:** eliminate duplicate AI settings panel headers ([52667f2](https://github.com/shi00/qTrading/commit/52667f268dc6a2513918b3c4dfcfcde3b3c0deca))
* **ui:** eliminate event loop starvation and UI button race conditions ([48b9d8e](https://github.com/shi00/qTrading/commit/48b9d8e2e6df17d24c9acd559d2aeccb37494b52))
* **ui:** eliminate race condition via direct sync reverting and enhance offline prop safety ([db918c8](https://github.com/shi00/qTrading/commit/db918c8e12ea6e778bcb352601b2a7615dc11295))
* **ui:** fix type error in screener_view.py _format_cell_value ([734b699](https://github.com/shi00/qTrading/commit/734b6992d1d6290567cf46040f52a5df64b0888a))
* **ui:** handle submit_task returning None in all call sites ([55018bb](https://github.com/shi00/qTrading/commit/55018bb984ae84b55ec2341d9d8a796cbb32d883))
* **ui:** inject missing i18n keys and remove dangling repair button reference to prevent AttributeError ([3807161](https://github.com/shi00/qTrading/commit/3807161145b20175eea300d3b407037296e035ed))
* **ui:** prevent _active_task_ids memory leak from stale entries ([f21f6be](https://github.com/shi00/qTrading/commit/f21f6becbfd59a00ad6f681c300605f1d899f522))
* **ui:** prevent permanently disabled health check button on dedup rejection ([9e0faa9](https://github.com/shi00/qTrading/commit/9e0faa98b9959b39537a00039ea787f2320dd4ee))
* **ui:** prevent silent operation drops and render starvation during DB tasks ([48793bc](https://github.com/shi00/qTrading/commit/48793bcb287f78108379e95aff279346544bf2fe))
* **ui:** restore metric_storage and health_summary on health check cancel/error ([5b3aab9](https://github.com/shi00/qTrading/commit/5b3aab92d9c951d0f5b424e6e506c1c91b0ad769))
* update sorting test to match asc default and fix atexit logging ([5d2fd4a](https://github.com/shi00/qTrading/commit/5d2fd4ada9ef111617188c1c5004c443602857ab))
* Use page.run_task for async event handling in DataExplorerView ([8a7eb43](https://github.com/shi00/qTrading/commit/8a7eb43ac3c90a22cb4fa45be0eae1330d9c7e90))
* use string annotation for Page type in doubao_auto_tagger ([9fea6c9](https://github.com/shi00/qTrading/commit/9fea6c987276d9ea4b1643cbd99c3b4bd2ab709f))
* 代码检视修复汇总 - test_infra_base.py: 修复TABLE_NAMES过期问题，更新为models.py中的33个实际表；优化TRUNCATE为单事务批量执行 - review_manager.py: 删除无效的T0指数数据预加载循环；移除冗余导入；修复index_pct默认值为None ([6fb9bfb](https://github.com/shi00/qTrading/commit/6fb9bfb316f2ad188238429728ce370cd58f3e1d))
* 任务失败错误信息国际化处理 ([e7cbc95](https://github.com/shi00/qTrading/commit/e7cbc95dc11bf673b96bb0abd6ea1232f1c77b87))
* 修复 calendar_mixin.py 缺失 pandas 导入 ([e002ed3](https://github.com/shi00/qTrading/commit/e002ed3d909fde849d95e093e014bde84c1fe9f9))
* 修复 datetime 时区比较错误 ([8f7a1ae](https://github.com/shi00/qTrading/commit/8f7a1ae6d850a5d8249a5f8b4d58121a06ad4cd5))
* 修复 macro_economy period 字段 NOT NULL 违规问题 ([b19aaf1](https://github.com/shi00/qTrading/commit/b19aaf15f28353e611048cf4ddcf2fbb13ece8ec))
* 修复 P0 代码检视问题 ([62da92c](https://github.com/shi00/qTrading/commit/62da92ca75bd8007aa8852e00716797dbebdc3f8))
* 修复 P1 日期时间类型一致性问题 ([874774e](https://github.com/shi00/qTrading/commit/874774e3db156901f3c7c69b6ebe562805cabd22))
* 修复 ShutdownCoordinator 关闭流程中的关键问题 ([399e00b](https://github.com/shi00/qTrading/commit/399e00b1ebec1824593f7ea1683452ceef88169c))
* 修复 test_scheduler_service.py 类型检查错误 ([1717105](https://github.com/shi00/qTrading/commit/1717105511966d1fcdbc3afef3afef334930c382))
* 修复4个高优先级P1问题 (A-P1-5, D-P1-5, B-P1-5, E-P1-5) ([51fe022](https://github.com/shi00/qTrading/commit/51fe022b6df1afcb410335f0cf521193a863f6fa))
* 修复CI流水线22个collect错误 - multiprocessing.Queue类型注解运行时不兼容 ([4345795](https://github.com/shi00/qTrading/commit/4345795bf8e8540c71e8e9936e80d822aa884a4b))
* 修复CI测试失败问题 ([b240837](https://github.com/shi00/qTrading/commit/b2408371774a3e0e1c2164acdcfd39805a9ce576))
* 修复NaT值无法插入PostgreSQL的问题 ([e3615b9](https://github.com/shi00/qTrading/commit/e3615b9dcc850143a7edc77e7ee56bd7b1e69b55))
* 修复P0深度检视发现的7个BUG并补充测试覆盖 ([9b1bd5e](https://github.com/shi00/qTrading/commit/9b1bd5e55420c0f0777a9b7f56693aaae778aa77))
* 修复test_graceful_shutdown类型检查错误 ([e8b9046](https://github.com/shi00/qTrading/commit/e8b90463d39523fe511117f674ef1c19e941ab91))
* 修复两个测试失败 ([f604114](https://github.com/shi00/qTrading/commit/f604114191f63773a6ff6acc53653794bddcc442))
* 修复二次检视发现的9个P1/P2问题 ([7949414](https://github.com/shi00/qTrading/commit/79494141166c7e8c55ba9bb2886242f68ea0043d))
* 修复全部22项P0问题并添加CI Windows矩阵 ([f3a502a](https://github.com/shi00/qTrading/commit/f3a502a5b48548685d83e890e424ded66a8989df))
* 修复数据同步完整性问题并补充测试用例 ([3739083](https://github.com/shi00/qTrading/commit/373908318849971cdd9755be852ba09fb0a0917f))
* 修复数据库索引行大小超限和UI加载状态锁死问题 ([291e04e](https://github.com/shi00/qTrading/commit/291e04ede5f1d7f682947b4e73a34fab49af30c1))
* 修复新增 app_state 表导致的测试失败 ([3249c8d](https://github.com/shi00/qTrading/commit/3249c8da8a32fc9847af194515c3232bd2e93166))
* 修复新闻加载更多按钮异常消失问题 ([af34cdf](https://github.com/shi00/qTrading/commit/af34cdf5c70013969cf8a8384b94a563bfa83ea3))
* 修复本地 LLM 推理超时后底层线程不会被终止的问题 (C-P0-1) ([21b2357](https://github.com/shi00/qTrading/commit/21b23572c391d694ff84c854b25da40a0924c6fc))
* 修复检视发现的问题并补充测试覆盖 ([786dc33](https://github.com/shi00/qTrading/commit/786dc338daf85a2dadfba958fc7aa451a6dc3766))
* 修复检视报告中的P0-4/P1-5/P2-4/P2-10问题，补充测试用例 ([73929c0](https://github.com/shi00/qTrading/commit/73929c0e5b5cce02012e6e44b356647bb115ad21))
* 修复测试日期超出SQL查询窗口导致失败 ([7e5d7d2](https://github.com/shi00/qTrading/commit/7e5d7d2ad3f21c856003cbaf51560465aa2d1e73))
* 修复测试用例失败问题 ([06e1eb4](https://github.com/shi00/qTrading/commit/06e1eb47d871811ccb3ec59ed13e5586c75321b2))
* 单元测试P0级问题整改 - 根据python单元测试代码检视标准 ([649c63a](https://github.com/shi00/qTrading/commit/649c63a2f2c49b5d4e83a23bc720b0fc1a487796))
* 单元测试P1/P2级问题整改 ([a588165](https://github.com/shi00/qTrading/commit/a588165a06c265f00ff7164111f4be7cd79b5271))
* 历史档案中策略名称国际化处理 ([a8ca458](https://github.com/shi00/qTrading/commit/a8ca458395cc4a7bb30b27701088b9204c992e5a))
* 合并Alembic迁移并修复测试 ([bdc6691](https://github.com/shi00/qTrading/commit/bdc66917daa52d7e659abb132e6f68e8c80bda4b))
* 实施code_review_report关键修复并深度检视 ([ff4289c](https://github.com/shi00/qTrading/commit/ff4289c40442542b9616244b028ffa02eeed8158))
* 数据质量体系全面增强 - 修复6项P0/P1问题及4轮审计发现 ([9d78e26](https://github.com/shi00/qTrading/commit/9d78e26709b77946f08a995c460e5848f1a33fb1))
* 添加 typing.cast 解决 pyright 类型推断警告 ([8932290](https://github.com/shi00/qTrading/commit/8932290fef6aa79fa4caa439d1b0d031028aa7fd))
* 用 *args 解包替代 type: ignore，正确测试关键字参数约束 ([3c2a4b3](https://github.com/shi00/qTrading/commit/3c2a4b33d0042f351175d1bf61869f8e2925e174))
* 用 getattr 动态获取方法，彻底规避类型检查器的签名校验 ([b044ab6](https://github.com/shi00/qTrading/commit/b044ab6485c398308f363c6e10834f3d57da3e8c))
* 第二轮检视修复_cancel_event竞态条件和aux表异常日志 ([a21e8d0](https://github.com/shi00/qTrading/commit/a21e8d01ab5189bcba4b9b7d66c20d829cce1700))
* 类型检查器错误 - 在测试用例中添加 type: ignore 注释 ([cf65151](https://github.com/shi00/qTrading/commit/cf651517ca6655c96073875f41597ae8bbd8d569))
* 统一宏观 API 字段映射机制 ([0e2a347](https://github.com/shi00/qTrading/commit/0e2a34766e1fcc955ae0c846215318ba5a8ee751))


### Performance Improvements

* convert remaining f-string logger calls to lazy %s in hot paths ([0c63f39](https://github.com/shi00/qTrading/commit/0c63f39acff71bb3488ae4c4a4c81c7d87946601))
* Enhance hard_reset robustness for Windows file locking with retries ([1316bca](https://github.com/shi00/qTrading/commit/1316bca61eff51328a72844b21b63d4c0b45ead1))
* Fix AI blocking (timeout) and UI freeze (Lazy DataView); Harden async reliability ([6a7a094](https://github.com/shi00/qTrading/commit/6a7a0943cb206f06939a982e749b063e706f215b))
* implement code-review5 fixes - logger lazy formatting + max_rows safety valve ([65df2f2](https://github.com/shi00/qTrading/commit/65df2f224e6e10a8163b06dc9046e7ab526ab070))
* implement remaining performance audit items ([e66f0f1](https://github.com/shi00/qTrading/commit/e66f0f10812c14f35db43b8999f9e211262ca8cb))
* Optimize Clear Cache to use physical file deletion (hard reset) to avoid lock timeouts ([42812e6](https://github.com/shi00/qTrading/commit/42812e62e70762177f14049a3af1e93f176a0dbb))
* optimize get_bulk_expected_stock_counts slow query ([c51091f](https://github.com/shi00/qTrading/commit/c51091f35210ac3d239a03919a16af0fbf12d474))
* optimize slow operations from log analysis ([c948ecc](https://github.com/shi00/qTrading/commit/c948ecc92c24c5b9c81503947bb0da4479ae6f51))
* **task_manager:** remove pandas dependency for NaN check ([124222f](https://github.com/shi00/qTrading/commit/124222f47fc179cfd9b0b86b11d1467df17de600))


### Documentation

* **data:** fix DatabaseManager docstring to reference PostgreSQL ([a24d2da](https://github.com/shi00/qTrading/commit/a24d2daf4d6d8ea8d15189d0246d797ff1e594bd))
* **readme:** sync project structure with actual codebase ([93e53d0](https://github.com/shi00/qTrading/commit/93e53d0946d3c197e626c5e1011c7ec60eb1751c))
* **readme:** update README.md to reflect current stack and features ([bc31e62](https://github.com/shi00/qTrading/commit/bc31e624089a83c87551a6fbe7594b645d3dcf4f))
* update README with architecture details and refine progress reporting ([a17d7ef](https://github.com/shi00/qTrading/commit/a17d7efd3fa3e41be20d79ff353f7a818c349e72))
* 创建代码检视计划文档 ([42ef44a](https://github.com/shi00/qTrading/commit/42ef44ad8e1ee7de9b05f0c50b347024fa63541a))
* 新增全身代码检视方案 ([d7c7c39](https://github.com/shi00/qTrading/commit/d7c7c395b355a1f91441076aace865ea34546875))
* 新增静态代码检查工具防区 ([4d3246b](https://github.com/shi00/qTrading/commit/4d3246b7080c4ed1d5c4d7cd5ed58476a19595f8))
* 更新 README.md - 添加系统架构图与覆盖率说明 ([9b296db](https://github.com/shi00/qTrading/commit/9b296dbb8617cc3094b017734afbaf6db1375735))
* 更新 README.md 文档 ([23f2547](https://github.com/shi00/qTrading/commit/23f2547f5f5c72ae8f53a2c68c9e6fddd753e0b2))
* 更新架构原则文档，补充视图模型层说明 ([2a9d92c](https://github.com/shi00/qTrading/commit/2a9d92c2401fcf233a6c1d73241d3988d385eacf))
* 添加架构设计原则文档 ([951064d](https://github.com/shi00/qTrading/commit/951064d44ff31789961d7d1679d3c6f899998ee7))
* 移除过时的规划文档 ([d392f27](https://github.com/shi00/qTrading/commit/d392f2786e32b40f0b893b1742f1aa02df425964))
* 补充测试用例原则到架构设计文档 ([4e2faba](https://github.com/shi00/qTrading/commit/4e2faba3fd43e2d6da3f6aa391ed482e7d23b229))
