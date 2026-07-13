# Changelog

## Unreleased

### Features
* **flet:** upgrade 0.28.3 → 0.85.3 (architecture-level rewrite)
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

## [0.7.0](https://github.com/shi00/qTrading/compare/v0.6.10...v0.7.0) (2026-06-15)


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


## [0.6.10](https://github.com/shi00/qTrading/compare/v0.6.9...v0.6.10) (2026-06-15)


### Bug Fixes

* **task_manager:** eliminate cross-thread dict race in submit_task ([06881a4](https://github.com/shi00/qTrading/commit/06881a474804fc278e64b2f576bc4fcc1c2481fc))
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

## [0.5.0](https://github.com/shi00/qTrading/compare/v0.4.2...v0.5.0) (2026-06-08)


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
