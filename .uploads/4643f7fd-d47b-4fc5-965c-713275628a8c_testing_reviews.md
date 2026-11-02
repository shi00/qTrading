# D15 测试体系、夹具与证据可信度

> 
> 现行版本：全业务/测试文本静态续审后；首轮历史明确折叠保留，不与当前统计混算。

## 1. 结论与统计

**续审后现行结论：blocked / indeterminate。** 主责741文件：600完整语义阅读、0完成有限来源核对、141待闭合；部分阅读0。已覆盖的业务/测试文本不等于依赖、运行或资源许可通过。

独立接受的确定缺陷/场景遗漏共14项；其中本次新增接受9项缺陷、27项测试缺口。同根因重复已合并；完整分类以本维度证据与总台账的当前字段为准。

本次所有项目运行检查仍not_run；未修代码、不安装、不联网、不访问用户配置/数据库。新需求属于冻结后增量，未改变本次合同基线。

首轮统计（历史，现行统计以上方与coverage-current为准）

**任务 3.1：blocked；gate_verdict：indeterminate；不是全量检视完成或测试通过。**

主责清单为 `scope.csv` 的 D15 **741 文件**，其中 577 个 Python 文件共 **327,601 行**。**本轮无法全量语义审读**。实际完整读取 **49 文件**、部分读取 **8 文件**、未读 **684 文件**；完整读取 Python 11,846 行，另部分读取 Python 183 行。第三方 JS 的前缀读取不构成源码审读或来源核验。完整文件清单、真实行段、哈希和未读原因见 `D15-evidence.json#/file_reads`。

| 类别 | 数量 | ID |
| --- | --- | --- |
| 确定缺陷 defect | 5 | D15-F01、F02、F04～F06 |
| 场景遗漏 scenario_gap | 0 | — |
| 测试缺口 test_gap | 2 | D15-F07、F08 |
| 待确认问题 question | 1 | 原D15-F03：新增按签名还是occurrence |
| 待验证风险 risk | 1 | D15-F09 |
| 范围外风险 out_of_scope | 0 | — |
| 非阻断建议 suggestion | 0 | — |

最高严重度 P2。全部发现的变化关系为 `unknown`，无 Git 历史，不能归因于特定提交。**动态检查 0**；collected/passed/failed/skipped/xfail/xpass 均未知，不填写为 0。哈希、AST、计数、标准库关系反例均不是项目测试或完整覆盖。

## 2. 范围与契约

当前逐文件范围及精确累计读段见[D15-evidence.json](D15-evidence.json)，[续审覆盖](coverage-current.md)。原始scope-0.1/snapshot保持不变；本轮阅读只合并已有真实段与续审真实段，不用hash、AST或用例名填充语义覆盖。

首轮范围/契约记录（历史，保留原始当时未读边界）

- 冻结清单指纹：`be4d0bbd515585b3cdb45f9a452e34b05230aa5adc8c2de60ee163bff312ac98`。741 个 D15 文件的实际内容 SHA-256 均与冻结清单相符；这仅证明本地快照一致。
- 按 `all-run-brief.md`、Plans 3.1/D15、`docs\guides\testing.md`、`docs\reviews\ai-review.md`、review-policy、project/frontend-mobile/cli-iac/third-party-rpa Profile 及 0.2 矩阵取证。
- 完整读取根、unit、integration、e2e conftest，以及 unit-ui conftest、component_renderer、mock_flet、renderer/mock 契约测试；完整读取 runner、E2E launcher、字体/skip helper、10 个主责治理脚本。按真实故障候选追到 coverage/flaky/弱断言工具单测、smoke/full-mount、fake AI 取消断言及 golden 输出。
- `services\news_insight_e2e_fake.py` 虽在 services 目录，按 scope 实际归 D15，已与其单测完整读取。
- 弱断言 baseline 仅语义读取首尾区间；758 条记录的解析/签名统计不是全部条目审读。`.ambr`、其余业务测试/夹具、i18n dead-key baseline、二进制 Parquet、字体/WASM/符号文件仍未完成审读/来源核对。
- 当前无版本控制兜底。只写本报告和 `D15-evidence.json`；不改 scope、Plans、index、测试、快照、基线或产品代码。
- 未执行 pytest（含 collect）、仓库脚本、项目导入、浏览器、安装、联网、DB/Keyring/用户数据访问。0.2 的 E2/E3/E5/Git 准入缺口不因报告交付而解除。

## 3. 模型与场景

### 3.1 入口与边界

`pytest` → pyproject 配置 → 根 conftest 的模块替换/配置路径/测试 URL → 目录 fixture → 测试断言 → JUnit/coverage。默认 `-n auto --dist=loadgroup --timeout=300 --strict-markers`；function test/fixture loop scope；`network` 是标记而非网络隔离。`xfail_strict` 未在已读配置中设置。

unit 在前后重置注册单例、ProxyManager、loop-local、优雅停机标志、共享引擎、Toast/Egress/调度 provider/metadata/i18n；根层另清配置缓存、mock 凭据、Flet ContextVar。integration 不逐测重置全部单例，有 session 关闭；`no_db`/隔离 fixture 可跳过 schema 初始化，其余分支可 DROP/CREATE 测试库。`_db_config` 的 test_ 前缀/字符和主机白名单、worker 后缀是重要反证，**不是强隔离证明**。

`run_e2e_local.main` → 清空 addopts、禁 xdist/randomly、显式 JUnit → 真 sidecar/独立 app pool → browser context、tracing → 页面加载/canary → 断言 → screenshot/trace/tar → CI always upload。runner 返回子进程退出码，并非吞掉 pytest 失败。RO/MUT/full-mount/news 场景池隔离；磁盘配置恢复不等于子进程全部内存状态恢复，mut 池另有 locale 兜底。

### 3.2 实现—断言—证据矩阵

| 场景 | 已读实现/断言 | 当前证据与限制 |
| --- | --- | --- |
| 正常选择/默认并行 | pyproject、unit/integration collection hook；runner 明确清 addopts | 静态；实际 collection、worker 分配未知 |
| skip/xfail | sidecar 缺失 skip、Flet probe 双平台 skip、Windows helper、canary | F01；577 Python AST 仅辅助发现 18 个 literal skip、7 个 skipif、4 个 importorskip 调用节点、0 个 literal xfail 调用节点；不是运行数量，字符串/动态调用不在此计数 |
| R7 顺序污染 | unit/root/ui autouse；污染探测固定种子 42/43/44、8 文件子集、显式 `-n 0` | 未运行，不能外推 741 文件无污染；CI 解析为空会 fail，本地才 skip，是既有保护 |
| 配置/凭据边界 | root mock store；子进程 null keyring backend；测试 DB 限定 | 未触真实凭据；环境继承与全部导入副作用未动态核实 |
| 真实组件/换页 | renderer effect 真实调用；MockFletPage.run_task 关闭 coroutine；full-mount 保留输入/语言行为断言 | F07；不能由 mock 绿色推导客户端真实调度、焦点、卸载语义 |
| 取消/迟到完成 | fake 延迟用原生 asyncio.sleep；测试立即 task.cancel | F08；实现未证实吞取消，但测试没进入延迟体 |
| 异常/超时失败制品 | makereport、_make_page、_teardown_page、集中归档 | F04；源码有 JUnit/upload 不代表实际制品已生成/可解析 |
| coverage 输入异常 | diff main / get_diff_added_lines；per-file 缺文件退出 2 | F02；per-file 检查在正常 CI 路径提供部分补偿，不覆盖 merge-base/diff 不可用 |
| 弱断言增长/异步空测 | compute_new_issues、scan_file 与对应单测 | F03、F06；baseline 不是证明断言充分的白名单认证 |
| flaky 收集失败 | detector main / parse_json_report / 单测 | F05；一致失败与无有效运行必须区分 |
| 快照被篡改/恢复 | golden check/update 互斥；离线数据 hash；tamper/容差单测 | 固定输出是漂移 oracle，不是独立金融正确性 oracle；Parquet 仅 hash，未解码 |
| 离线第三方资源 | intercept、font helper、sync script、138 资源文件目录 | F09；basename/文件存在性不能证明 engineRevision、版本或许可 |

## 4. 已核实发现

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

以下均为完整静态可达链高置信度发现，无项目运行复现；未声称已发生实际 CI 事故。

### D15-F01 · P2 · E2E canary 把锚点回归转成 skip，允许无有效验收的绿色退出

- **契约/入口**：0.2 要求意外 skip、0 用例不能算通过；`run_e2e_local.main` 或 CI E2E 命令进入 `test_smoke.test_app_boots_and_shows_nav` 等 page 消费方。
- **逐跳**：`tests\e2e\conftest.py::_e2e_app_dep` → `_e2e_page_with_viewport` → `AnchorPage.expect_visible(EIDS.NAV.SCREENER)` 异常 → `_teardown_page(failed=True)` → `pytest.skip`。此时测试体断言未运行；runner 原样透传 pytest 状态，已读 CI 命令/上传步骤没有“至少执行一条/意外 skip 失败”约束。
- **触发/影响**：页面和导航文本正常，但 EID 包装或语义锚点损坏；seed 等待导航文本可成功，canary 单独失败。多个依赖同 fixture 的验收变为 skipped，而非定位为基础设施回归。
- **反证**：进程存活检查、seed 导航文本检查、`--run-windows-skip` 均不能把该运行期 skip 改为失败。注释“跳过整个文件”也不准确：实现对每个 item 的 fixture 分别 skip，不是模块级一次跳过。
- **最小修复**：只将 canary 基础设施回归分支改为保留原异常的 fixture 失败；保持 sidecar 明确缺失等受控前置 skip 独立。不通过删除 canary 或改变阈值解决。
- **回归输入→期望**：stub app 正常/seed 正常、仅 expect_visible 抛超时；应 setup failed、call 未执行、退出非零且尝试保存失败证据；正常 anchor 路径仍执行测试体。
- **未来验证**：在 `tests\unit\test_run_e2e_local.py` 所属工具测试域补 fixture 路径断言；E5 用现存 `tests\e2e\test_smoke.py` 目标复验。风险/回退：会暴露被隐藏的基础设施红灯；回退仅该分支，不改测试期望。

### D15-F02 · P2 · diff coverage 的 strict 模式对前置输入/历史失败 fail-open

- **契约/入口**：testing 正本强制 diff coverage ≥80%；CI 明确调用 `scripts\check_diff_coverage.py --strict --threshold 80`。
- **逐跳**：`main` 缺 coverage 文件直接返回 0；`get_diff_added_lines` 的 merge-base 不可用返回 `{}` → main 宣告“无源码变更”返回 0；git diff 报错抛 RuntimeError → main advisory 捕获返回 0。`args.strict` 仅在正常算出百分比后检查。
- **触发/影响**：覆盖数据缺失、无共同祖先/历史不全、diff 命令失败时，无法计算的检查被标成成功；真实“无变更”和“无法知道变更”混同。
- **反证**：同一 CI 中 per-file 缺 coverage 会先退出 2，可补偿通常的缺文件分支；但它不核验 merge-base/diff。CI fetch 失败 `|| true`、已有单测还显式断言 merge-base 缺失返回空字典，均不是 strict 的失败保护。不据此宣称当前某次 CI 已漏过。
- **最小修复**：将不可用历史/数据作为独立异常或结果状态；strict 下前置失败返回非零，advisory 保留警告语义；有证据的空 diff 仍返回 0。不要静默改为两点 diff。
- **回归**：缺 coverage、merge-base 128、diff 非零，strict 均非零且不能打印“无变更”；真实空 diff 和达到 80% 仍 0，低于 80% 为 1。
- **未来验证**：扩展 `tests\unit\test_check_diff_coverage.py` 的 main 参数化测试，命令见第 8 章。无 schema/DB 迁移；回退只影响工具，不放宽阈值。

### D15-F03 · question · 弱断言增量基线的签名/occurrence契约待裁决

- **入口/契约**：CI及staged承诺阻断新增，但`compute_new_issues`明确定义为签名不在baseline，`make_signature`有意不含occurrence。原确定缺陷撤回：未找到必须按重复实例配额阻断的明确契约，不能自行将集合改为Counter。
- **逐跳**：`scan_file` 产出全部 occurrence → `compute_new_issues` 将 baseline 转为签名 **set** → 同文件/同类型/同源行文本的新 occurrence 全命中旧签名 → `_run_base_mode` 的 new_issues 为空。`check_baseline_shrink` 比较的是基线文件条目数，不是本次扫描 occurrence 数。
- **最小反例**：baseline 一条 `m.assert_called_once()`，同文件新增第二个测试中的完全同文断言；baseline 文件不改，current=2、set 未见签名=0，新增未被阻断。标准库独立关系反例确认这一差异，非执行项目 scanner。
- **反证**：行号不参与签名是有意容忍漂移，应保留；KPI 只 warning，不能补偿新增阻断。实际 baseline 758 occurrence、461 唯一签名，297 个重复 occurrence 是元数据统计，不等于 297 个确定坏测试。
- **条件方案**：先裁定新增按签名种类还是occurrence；若批准后者，同步canonical再以Counter消耗配额，CI/staged共用，保留行漂移容忍。否则集合实现符合现有精确定义，重复增长仅为治理改善建议，不是已证缺陷。
- **回归**：baseline×1/current×2 → 恰报一条；baseline×2/current×2 → 0；×2→×1 → 0；仅行号漂移 → 0；不同文件同文本仍分别匹配。
- **未来验证**：补 `test_scan_weak_assertions.py::TestDiffMode` 及 `test_check_staged_weak_assertions.py`。可能揭出存量超额，不得重建或扩张 baseline 掩盖；回退仅比较算法。

### D15-F04 · P3 · E2E 截图失败或页面 setup 失败会丢失已启动的 trace

- **入口/逐跳**：`_make_page` 创建 context 后开启 tracing。① 页面加载异常时在 `bind_context` 前 `context.close()` 并抛出，尚无 fp 可供 fixture teardown；② call 失败时 `_teardown_page` 先 `page.screenshot`，后 `context.tracing.stop(path=...)`，两者同一 try，截图抛错即跳过 trace 保存，最后关闭 context。
- **实际/预期**：最需要网络/渲染诊断的白屏、已关闭 page、截图超时路径，可能有已录 trace 却未写出；应独立尽力保存 trace，setup 失败也走同一证据保留路径。
- **定级复核**：原失败仍传播，不是假绿色；确证影响为有限诊断制品丢失，缺提升到P2的独立运维证据，故降P3。
- **反证**：`_collect_e2e_artifacts` 只打包已存在文件和日志 tail，不能恢复未 stop 到文件的 trace；CI always upload/JUnit 证明配置存在，不证明这份 trace 留存。取消仍需传播，不应为诊断无限阻塞。
- **最小修复**：提取“保存失败证据并关闭 context”的内部 helper；截图、trace 各自独立 try/finally，setup 异常也调用；保留原始失败，不用诊断异常覆盖。明确 trace 保存失败的可观察状态。
- **回归**：截图 mock 抛 TimeoutError，仍断言 tracing.stop 带目标 path 被 await、context 关闭；open 失败同样尝试 trace；正常路径不保留成功 trace；trace 自身失败不遮蔽原错；取消及时传播。
- **未来验证**：工具域新增上述异步单测，E5 对 `test_smoke.py` 分别注入导航失败与截图失败，核验 JUnit/setup error 和 zip 内容。只用合成哨兵文本验证脱敏；不读取真实日志/截图。
- **风险/回退**：制品量可能增加，需要有界超时和既有 retention；回退 helper 不涉及业务数据。

### D15-F05 · P2 · flaky detector 在所有轮次收集失败时仍输出“结果一致”并返回 0

- **入口/逐跳**：`scripts\detect_flaky.py::main` 调 subprocess 得到 returncode → 只打印，不判运行错误 → 存在 JSON 即 `parse_json_report` → 缺 tests/空 tests 返回 `{}` → 各轮均空时 `identify_flaky_tests` 的 nodeid 并集为空 → format 宣告无 flaky → return 0。
- **触发/影响**：例如每轮 exit 2 且 JSON 记录 collection error/`tests=[]`，没有任何有效测试仍产生成功状态，违背脚本自身“运行出错退出 2”契约。
- **反证**：没有 JSON 已返回 2；已有测试覆盖空 report 解析、正常一致、pass/fail/pass，但未覆盖 main 的“有效 JSON + 所有轮次 collection error”。R7 污染探测另一个 runner 在 CI 解析为空时会 fail，不能代替本工具保护。
- **最小修复**：接纳并校验每轮 exitcode/collection errors/有效条目数；pytest 2/3/4/5、空测试和坏报告返回 2，非一致性结论；正常测试失败 exit 1 不应一概当运行错误，仍可比较 flaky。每轮报告防止复用旧文件。
- **回归**：三轮 exit2+empty report → 2；一轮有效、一轮 collection error → 2；正常一致 → 0；passed/failed/ passed → 1；全部合法失败的一致性语义保留并明确不是测试通过。
- **未来验证**：扩展现有 `tests\unit\test_detect_flaky.py::TestMainIntegration`；不真实派生 pytest。回退仅运行有效性判断。

### D15-F06 · P3 · 弱断言扫描器的函数级规则遗漏 async def

- **逐跳**：`scan_file` 只把 `ast.FunctionDef` 收入 test_funcs，空测试分支也只认该类型；`AsyncFunctionDef` 的 pass 没有其他命中分支，异步 print 替代断言、唯一 is-not-None 和比例规则也因找不到 parent_func 而漏检。literal `assert True`、weak Mock 等节点级规则仍能扫描异步体，**不是所有异步弱断言都漏检**。
- **触发/影响**：`async def test_empty(): pass` 是 pytest auto 模式可收集的空测试，但 scanner 返回空 issue，CI/staged 新增门禁无法拦截；同步同文函数可被识别。
- **反证**：标准库 AST 独立验证两种节点是不同类型；已读扫描器没有后续异步 fallback。所读工具测试主要用同步字符串，未证明异步函数级规则对称。
- **最小修复**：函数节点判断和 parent 查找统一接受 FunctionDef/AsyncFunctionDef；不改规则强度和 baseline。
- **回归**：sync/async 各测空体、仅 docstring、print 无断言、唯一存在性、比例阈值；async 有内容断言应不报相应规则，既有白名单仍有效。
- **未来验证**：在 `tests\unit\test_scan_weak_assertions.py` 补对称参数化。可能浮现旧债，但不得通过更新 baseline 解决；回退只收窄 AST 节点修改。

### 续审独立接受的发现（当前）

#### TF-F01 · P3 · i18n死键基线已收缩至214而独立天花板仍为216，棘轮留下两项回增空间

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[count / keys](../../tests/i18n_dead_keys_baseline.json)；[_BASELINE_CEILING](../../tests/unit/test_i18n_keys_completeness.py)。
- **契约**：baseline说明count只能减少；消费者83-85行注释要求清理死键递减后手动下调天花板，并明确其目的为阻止向baseline塞键且count同步+1。目标方法全部已读。
- **触发**：从当前214条出发，贡献者在zh/en locale加入一个未被Python字面量或动态allowlist使用的新key，同时把它加入baseline.keys并将count改为215；不修改Python门禁天花板。也可加两条到216。
- **可达链/当前行为**：基线当前count及唯一keys均214，本轮静态解析确认。
- **可达链/当前行为**：消费函数从JSON读取baseline_count后只与固定216做<=比较，无前一快照count参与此方法。
- **可达链/当前行为**：同一方法的集合差使用已被同步加入的新baseline_keys，因此不能代替更紧的独立上界。
- **可达链/当前行为**：定向搜索tests/scripts/.github的该baseline及天花板引用，仅发现该消费者；未执行全仓CI，不声称其他门禁也必然绿色。
- **可达链/当前行为**：局部门禁keys/count自洽成立，215<=216成立，新增key仍在locale且不属于动态allowlist，unreferenced_keys-baseline_keys为空且unreferenced总数<=215。该路径不触发防基线膨胀的独立天花板；不是说只加locale而不改baseline也能通过。
- **已尝试反证**：新key若不加入baseline，new_unreferenced断言仍阻断；count超过216仍阻断；动态活键误入baseline或locale缺键也各有保护。
- **已尝试反证**：任何规则文件都可能被显式修改，问题不是防恶意仓库作者，而是独立上界未跟随现有收缩、无法实现代码注释已承诺的局部防误放宽控制。
- **已尝试反证**：没有历史Git基线，无法认定何次提交遗漏下调；不归因于特定作者。
- **已尝试反证**：与D15-F03无同根因：本项是明确只减不增的i18n数量上界漂移，非弱断言按signature还是occurrence的未决契约。
- **最小修复**：将_BASELINE_CEILING从216下调为已接受的214，保持其独立于baseline JSON，不能改为直接读取JSON count当上界。
- **最小修复**：补一个隔离输入的负向回归：baseline和两locale同时新增一条、count=215但Python上限仍214时必须命中天花板拒绝。后续合法收缩时同步收紧独立上界。
- **最小修复**：不删除现存翻译、重建baseline或扩张allowlist来掩盖问题；本轮未实施任何修复。
- **回归输入与期望（未运行）**：已做独立PowerShell集合算术，不执行项目：214个唯一项→加入一个新项得215；keys/count自洽true，旧216上界接受true，新项同时属于baseline时集合差0。仅证明关系反例，不是pytest执行证据。
- **回归输入与期望（未运行）**：未来授权的隔离测试输入：原214条完整状态→保持可接受；原214+新key但不改baseline→新增死键拒绝；baseline+locale+count一同增至215且上界214→天花板拒绝；清理到213并同步收紧上界→接受。
- **回归输入与期望（未运行）**：未来仍需保留动态allowlist必须在locale、baseline/allowlist互斥、baseline缺locale条目及JSON count不自洽四类拒绝行为。目标是现有test_no_new_unreferenced_i18n_keys，不声称新增测试节点已存在或已运行。
- **回退/兼容**：当前只交报告无源改动。未来回退新增测试结构可保留原方法，但不应自动把已收紧的214上界恢复216；若确需提高预算，应由维护者显式修订只减不增契约，而不是静默增baseline。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；已读完整baseline和完整消费者：JSON声明count214，独立_BASELINE_CEILING仍216，注释明确清理后应下调。新死键同时进两locale和baseline、count215时，集合差为空且215<=216，确留下回增名额。只改locale不改baseline仍被拒，是有效保护但不推翻此反例。没有Git基线，不归因某次提交；与D15弱断言按签名计数的未决合同不同。
- **裁决限缩/方案约束**：独立上限下调214，不从同一JSON派生上限。合成baseline+两locale同步加一个未引用且非动态allowlist键、count215应拒绝；仅加locale仍拒绝，现有214及动态键/集合自洽保护保持。
- **完整原始取证**：[continuation-test-fixtures.json](continuation-test-fixtures.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TEST-P01-F01 · P3 · TierApiPanel 渲染测试跨轮累加同一元素，单个标题即可伪满足至少两项

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[test_system_tab_tier_api_panel_rendered](../../tests/e2e/test_settings_tabs.py)；[TierApiPanel](../../ui/views/settings_tabs/tier_api_panel.py)。
- **契约**：该测试明确要求 panel_title/tier_label/probe_button 至少两种关键元素渲染；生产组件将标题、档位选择、探测按钮分别构造，不是同一存在性事实。
- **触发**：只有 panel_title 持续存在，tier_label 和 probe_button 始终不存在；第一次轮询后列表长度1，第二次重复追加 panel_title 后长度2。
- **可达链/当前行为**：test_system_tab_tier_api_panel_rendered -> SettingsPage.open/click_tab(system) -> has_text 三次探测
- **可达链/当前行为**：独立标题渲染/语义可观察，但档位与探测按钮因回归或渲染缺失不可观察 -> found_keys 重复累计 -> 假通过
- **可达链/当前行为**：found_keys 为 list 且循环外初始化，两轮得到重复同名项即 break，最后 len>=2 通过；设置面板缺主要交互仍可被报告为渲染验证成功。
- **已尝试反证**：has_text 返回是否存在，不会返回出现次数；重复计数完全由测试 list 引入。
- **已尝试反证**：等待 theme_label 只证明 system tab 顶部渲染，不保证 TierApiPanel 的两个交互。
- **已尝试反证**：其他设置页/模型页签测试不代替本项 TierApiPanel 的至少两种关键元素判据；未在相关债务/例外关键字检索发现该根因。
- **最小修复**：使用去重集合或每轮新建命中集合，明示是否要求同轮；保留现有超时、候选元素及门槛，不用延长 sleep 掩盖。
- **最小修复**：为仅标题持续可见添加反例，使重复轮询不能增加独立元素计数。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：acceptance_cases: 只有标题连续30轮可见 -> 断言失败。；标题+档位标签可见 -> 通过；仅探测按钮重复可见 -> 失败。；若采用同时可见契约，标题与标签交替但从不同轮出现 -> 失败。
- **回退/兼容**：本轮未修复；未来变更仅回退该 oracle 及配套反例，不改 TierApiPanel 产品逻辑或放宽门槛。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；完整轮询测试在循环外创建 list，每轮重复 append panel_title；只有标题一直可见时第二轮即 len=2。主题标题等待与三个独立控件没有蕴含关系。实际 TierApiPanel 分别创建 Dropdown、探测 Button 和标题 Text，故删除两个交互但保留标题是可达渲染回归。接受测试 oracle 的确定错误，降为 P3：没有证明产品交互当前缺失，也不代表其他组件断言全部放行。
- **裁决限缩/方案约束**：每轮计算不同元素的集合，明确要求同轮可见还是累计出现；仅标题连续可见必须失败，标题加档位标签应通过。保留原超时，不靠延长等待解决重复计数。
- **完整原始取证**：[continuation-test-pack-01.json](continuation-test-pack-01.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TEST-P01-F02 · P2 · 向导 DB URL 解析失败时把可能含密码的完整 URL 写入 skip 异常消息

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[_parse_db_url](../../tests/e2e/test_onboarding_wizard.py)。
- **契约**：CLAUDE.md R9 禁止日志/异常消息直接含明文凭据。测试从 E2E_DATABASE_URL 或测试 URL helper 获得连接串，失败诊断同样需要脱敏。
- **触发**：external 模式执行 DB 成功测试，但输入不匹配该正则，例如合法的带凭据 IPv6 URL：host 捕获不允许冒号，匹配失败。
- **可达链/当前行为**：test_wizard_db_validation_success（external 分支）-> E2E_DATABASE_URL/default helper -> _parse_db_url
- **可达链/当前行为**：正则无法解析 -> pytest.skip(含原始 url 的消息) -> skip 异常/报告原因
- **可达链/当前行为**：pytest.skip 的 f-string 直接插入原始 url。skip reason 本身已携带连接串凭据，常规报告/显示 skip 原因时可能再传播；没有经过 DataSanitizer。未读取任何真实连接串。
- **已尝试反证**：embedded 模式 skipif 使默认模式不到达本分支，限制触发范围，但 external 测试仍是显式保留路径。
- **已尝试反证**：正常 URL 匹配后的 unquote_plus 解决填表编码问题，不保护匹配失败消息。
- **已尝试反证**：日志层脱敏及某些 CI 对已登记 secret 的显示遮罩不能保证 pytest skip 异常内容或本地报告被脱敏；未断言所有 CI 必然泄露。
- **最小修复**：删除 skip reason 中的原始 url，仅保留 E2E_DATABASE_URL 无法解析的诊断。
- **最小修复**：如需扩展合法 URL 支持，另用标准解析 API，仍不得在解析异常中回显原始凭据。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：acceptance_cases: 使用测试时生成的合成哨兵凭据构造不匹配 URL，捕获 skip 异常；哨兵及完整 URL 不得出现在 reason/报告。；合法普通 URL 解码结果保持不变；IPv6 等输入按明确支持契约解析或安全拒绝。；只用合成哨兵，不读取真实环境凭据。
- **回退/兼容**：本轮未修复；未来只回退 URL 解析/诊断局部变更，不引入恢复原始明文输出的兜底。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；_parse_db_url 的失败消息直接插入原始 url。external 分支实际从 E2E_DATABASE_URL/测试 URL helper 调用它；带 userinfo 的 IPv6 URL 不满足 host 正则，原值进入 pytest.skip 异常。embedded 的受控 skip 限制触发面但不保护 external 模式。未读取任何真实 URL；接受的是异常对象可含凭据，不声称本次实际输出了真实秘密。
- **裁决限缩/方案约束**：失败原因仅保留变量名与解析失败诊断，不回显 URL。用运行时生成的合成 userinfo 和不匹配输入捕获 skip reason，断言不含合成哨兵；合法普通 URL 解码保持不变。
- **完整原始取证**：[continuation-test-pack-01.json](continuation-test-pack-01.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TEST-P01-F03 · P2 · 账本混合并发用例把结算 False 当成功，且只核验四个连接中的第一个

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestConcurrentMixedOpsNoDeadlock.test_mixed_concurrent_operations_complete](../../tests/integration/test_ai_usage_ledger_pg.py)；[AIUsageDao.settle_attempt_usage](../../data/persistence/daos/ai_usage_dao.py)。
- **契约**：用例承诺4预留+4结算全部成功并核账；settle_attempt_usage -> bool，False 明确表示未推进，而非成功。
- **触发**：四个 reserve 均获准，conn-mixed-0 结算成功，但 conn-mixed-1/2/3 任一或全部返回 False 且未落账。
- **可达链/当前行为**：四个 _seed_backing_reservation -> gather(reserve_tasks + settle_tasks) -> 异构结果列表
- **可达链/当前行为**：settle False 未抛异常 -> 非None总断言放行 -> 仅连接0核账 -> 其余失败未被观察
- **可达链/当前行为**：False is not None 成立，all(r is not None for r in results) 通过；后续只查询 conn-mixed-0 的 attempt_count/cost_cny，缺失其他结算不会失败。
- **已尝试反证**：wait_for(60s) 能发现死锁/超时，gather 会传播异常；缺口仅是正常返回 False 的部分失败，不是所有失败都被吞。
- **已尝试反证**：同文件其他幂等/fencing 用例认真检查 False 或 True，反而确认该返回值不是 None；它们不核验这次混合并发的四笔终态。
- **已尝试反证**：生产 DAO 的拒绝语义本身可能正确，本发现不据此断言生产存在丢账。
- **最小修复**：按 gather 输入顺序拆分结果，reserve 非None、settle is True 分别断言。
- **最小修复**：按连接集合核验四条各0.10记录及场景总额0.40/attempt_count4，保留并发和超时，不改为串行消除场景。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：acceptance_cases: 只让 conn-mixed-2 的结算返回 False、不写入，其他七项成功 -> 用例必须失败。；全部成功 -> 四个连接各0.10，场景合计0.40，且均在既定超时内完成。；重复结算不推进仍由现有幂等测试保持 False 语义。
- **回退/兼容**：本轮未修复；未来只回退该断言与核账补充，不能删除并发步骤或放宽 DAO 幂等契约。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；混合 gather 的结算结果是 bool，但 all(r is not None) 接受 False，之后只核 conn-mixed-0。最小反例是连接0成功、连接2结算 False 且没有写账，其他任务成功；所有现有断言仍成立。wait_for/gather 能抓死锁和异常，却抓不住这种合法返回形状的部分失败；相邻各连接核账用例不是这次并发交错的终态 oracle。没有据此认定当前 DAO 丢账。
- **裁决限缩/方案约束**：分别检查4个预留非 None、4个结算 is True，逐连接核0.10及场景合计0.40/4次。只使 conn-mixed-2 不推进的变体必须失败；保留并发及超时，不能改串行规避。
- **完整原始取证**：[continuation-test-pack-01.json](continuation-test-pack-01.json#/findings/2)，原措辞仅在上述裁决边界内采用。

#### TEST-P01-F04 · P3 · 浮点金额精度回归用例选择可二进制精确表示的0.5，无法区分旧Decimal(float)路径

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestAmountGuardContract.test_settle_float_cost_persists_without_binary_noise](../../tests/integration/test_ai_usage_ledger_pg.py)；[_checked_amount](../../data/persistence/daos/ai_usage_dao.py)。
- **契约**：用例声称拦截 float 经 Decimal(float) 展开后持久化的二进制噪音；当前生产入口将非字符串先转 str，以最短往返十进制串落库。
- **触发**：归一化误退化为 Decimal(float) 后，仍以 cost_cny=0.5 运行当前回归用例。
- **可达链/当前行为**：test_settle_float_cost_persists_without_binary_noise -> settle_attempt_usage(cost_cny=0.5) -> _checked_amount
- **可达链/当前行为**：目标归一化回归仍写入精确0.5 -> ai_attempt读回 -> Decimal等值断言通过
- **可达链/当前行为**：0.5=1/2 可由二进制浮点精确表示；Decimal(0.5) 数值本来就等于 Decimal('0.50')，当前 PG 读回等值断言不会区分新旧行为。此为静态数值反例，未执行抽取代码。
- **已尝试反证**：该测试确实检查 settled=True 并读取实际 PG 行，不是 mock 自证；缺口在选值而不是未查数据库。
- **已尝试反证**：坏金额/NaN/负值/上界等其他用例覆盖不同契约，不会让0.5产生二进制噪音。
- **已尝试反证**：其他未读测试可能已覆盖0.1，因此仅指该集成回归的判别力，不声称全库精度保护失效；当前 _checked_amount 实现已存在正确归一化。
- **最小修复**：将本场景参数化为0.1、0.3等非精确值并保留0.5对照；期望由十进制文本定义，不从同一错误转换路径计算。
- **最小修复**：更新docstring，避免声称0.5会展开55位噪音。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：acceptance_cases: float 0.1 输入 -> numeric读回必须等于十进制0.10，而不是浮点全长展开值。；故障注入旧 Decimal(float) 归一化 -> 新非精确值回归应失败。；float 0.5、字符串及Decimal输入保持既有规范值。
- **回退/兼容**：本轮未修复；未来只回退该测试输入/断言/docstring，不修改金额规范化产品契约。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；用例实际结算 float 0.5 并查回 numeric，是有效落库测试；但0.5为二进制精确值，Decimal(0.5) 与 Decimal('0.50')数值相等。它不能区分注释声称防守的 Decimal(float) 噪音回归。生产 _checked_amount 当前先 str，故本项不是现存金额错误；仅收窄为该精度夹具无区分力。
- **裁决限缩/方案约束**：以 float 0.1/0.3 对照十进制文本期望，保留0.5对照；验证精确持久值而非用同一错误转换构造 expected，修正文档把0.5称为55位噪音的说法。
- **完整原始取证**：[continuation-test-pack-01.json](continuation-test-pack-01.json#/findings/3)，原措辞仅在上述裁决边界内采用。

#### TP02-F01 · P2 · 集成关闭测试恢复running而不恢复scheduler实体，向后续测试泄漏MagicMock调度器

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[mock_singletons](../../tests/integration/test_infra_base.py)；[test_safe_skip_empty_singletons](../../tests/integration/test_graceful_shutdown.py)；[SchedulerService.**new**/**init**/start](../../utils/scheduler_service.py)；[ShutdownCoordinator._step1_stop_services](../../utils/shutdown.py)；[cleanup_singletons_session](../../tests/integration/conftest.py)。
- **契约**：R7与testing.md要求隔离单例状态；临时替身退出后应恢复被替换对象，而不是只恢复该替身上的属性值。
- **触发**：同一worker先执行使用mock_singletons的关闭用例或test_safe_skip_empty_singletons，再执行读取/启动SchedulerService的测试；初始SchedulerService.scheduler为真实调度器或调用方自己的sentinel。
- **可达链/当前行为**：test_full_cleanup_all_steps等用例 -> mock_singletons -> SchedulerService() -> svc.scheduler = MagicMock()
- **可达链/当前行为**：fixture finally -> 只恢复running/stop -> SchedulerService.__new__返回原svc、__init__快速返回
- **可达链/当前行为**：后续SchedulerService.start/stop或ShutdownCoordinator._step1_stop_services消费遗留MagicMock，真实调度/回收行为不再被观察；原scheduler也不再由svc持有。
- **可达链/当前行为**：fixture将svc.scheduler整体改为MagicMock，但只保存orig_scheduler_running/orig_scheduler_stop；finally写回的是MagicMock.running。safe_skip用例同样只写回running。SchedulerService._instance仍指向svc且_initialized为True，所以后续构造不重建scheduler。
- **已尝试反证**：该fixture确实恢复TaskManager、NewsSubscriptionService、DataProcessor、CacheManager、MarketDataService、LocalModelManager、ThreadPoolManager实例；不是全部单例都未隔离。
- **已尝试反证**：D15报告已确认unit存在autouse reset，但integration没有逐测全单例reset；因此不能用unit隔离消除此处对象恢复遗漏。
- **已尝试反证**：原D15的'全部单例未隔离'候选被拒绝是正确的，本项是具体对象替换及恢复不对称，不沿用其泛化结论。
- **已尝试反证**：没有运行随机顺序/xdist；不声称某一次CI已经受影响。
- **最小修复**：在两处保存原scheduler对象并在finally恢复，优先使用monkeypatch/patch.object自动恢复对象及stop。
- **最小修复**：若必须创建独立SchedulerService替身，保存/恢复原_instance和必要初始化状态；不要通过reset停止可能属于session fixture的原真实scheduler。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 预置带sentinel scheduler的已初始化单例，进入并退出fixture清理路径，断言SchedulerService().scheduler is sentinel、stop绑定恢复。；分别覆盖正常结束/测试体异常；未预置单例的情况退出后应恢复原None状态。；同worker串联mock_singletons消费者与随后真实/受控scheduler消费者，后者不得拿到MagicMock。
- **回退/兼容**：仅修测试夹具和对应回归；回退不涉及schema、生产配置或数据。若兼容性有问题，保留原实例恢复策略，不以关闭R7检查规避。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；两处把 svc.scheduler 整体换成 MagicMock，却只保存/恢复 running；fixture 另恢复 stop，也没有恢复 scheduler 身份。SchedulerService 再次构造会复用已初始化实例。integration 的清理是 session teardown，不是逐测重置，所以同 worker 后续服务可拿到该替身。主动反证：unit 确有前后 reset，不能用来保护 integration；没有泛称所有单例未隔离。
- **裁决限缩/方案约束**：在作用域中保存/恢复 scheduler 对象、stop 绑定及原 _instance 状态；预置 sentinel 后正常/异常退出都应保持 identity。不要通过 reset 停止可能属于 session 的原真实服务。
- **完整原始取证**：[continuation-test-pack-02.json](continuation-test-pack-02.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP02-F04 · P3 · 退市样本与API日历健康回归在测试内重写算法，产品改坏不影响断言

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestQualityScanDelisted](../../tests/integration/test_health_check.py)；[HealthCheckMixin.run_quality_scan and API gold-standard lag calculation](../../data/mixins/health_mixin.py)；[test_api_extends_gold_standard](../../tests/unit/test_health_mixin.py)。
- **契约**：这些回归说明P1-6要求深扫包含D状态股票，P1-7要求API日历可揭露本地日历/行情同时陈旧；断言必须约束真实HealthCheckMixin输入到采样/lag/tier的路径。
- **触发**：把run_quality_scan的isin(['L','D'])误改为仅L；或移除生产API gold-standard扩展分支，保留测试自身的过滤与列表拼接。
- **可达链/当前行为**：产品run_quality_scan读取stock_basic -> L/D过滤 -> random.sample -> quote_dao.get_daily_quotes(ts_code_list=sample) -> score和_quality_tier。
- **可达链/当前行为**：健康检查读取trade_calendar与API日期 -> gold_standard_dates -> lag_days -> 健康结果；当前目标回归没有经过任一产品分支。
- **可达链/当前行为**：同包test_data_processor的深扫用例实际调用产品，但输入股票均为L，不能区分D遗漏；它的红黄绿用例也没有明确注入并断言API比本地日历更晚这一场景。
- **可达链/当前行为**：退市两用例只在本地DataFrame执行硬编码isin；API两个用例只在局部列表执行if/计数。没有构造processor或调用健康方法，因此以上产品变体仍得到相同局部断言结果。
- **已尝试反证**：test_health_check前半的同步清健康缓存用例实际调用DataProcessor；不是本文件全部测试空转。
- **已尝试反证**：当前产品源码确实包含L/D过滤和API扩展代码；没有将缺少有效回归当成当前生产已排除退市股或已错误计算lag的确定缺陷。
- **已尝试反证**：本包交易日历及深扫闭市锚定测试有真实调用，但其输入/断言契约与这里特定D过滤/API扩展不同。
- **最小修复**：退市回归通过真实scan入口，注入全部样本数和确定性sample，检查get_daily_quotes的实际ts_code_list与sample_size。
- **最小修复**：API回归通过真实health入口，注入产品实际date类型和API返回格式，断言有/无API新增日期时精确lag/状态，避免复写生产算法作为被测对象。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 输入L/D/P三股 -> batch查询包含L和D、不含P，误改L-only的受控变体必须失败。；本地日历/行情停在旧交易日，API提供更新交易日 -> 返回可观察的lag增加；API空或失败按既有回退约束。；当前日期已齐全 -> 不虚增lag；检查真实date/字符串边界，而非全部用本地字符串演算。
- **回退/兼容**：只更新测试边界和fixtures，保留既有采样/日期产品语义；不借修测试修改质量阈值或交易日判定。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；退市测试只在本地 DataFrame 写 isin(['L','D'])，API金标准测试只拼本地列表；不是对生产过滤/lag的行为约束。补读真实 health_mixin 单测发现 API extends 案例有调用产品，但仅断言颜色属于三态且有 lag 键，仍不区分具体 lag；深扫其他样本均 L。故接受 D-only遗漏及API扩展精确结果的窄缺口，降P3；不是整个健康模块没有有效测试，也不是当前生产错误。
- **裁决限缩/方案约束**：L/D/P三股通过真实 scan，断言实际查询代码含D不含P；真实 health 输入用 date 类型：本地日历/行情同停旧日，API多一个新交易日，断言精确 lag，另设API空回退对照。只删 D 或 API扩展分支应使新断言失败。
- **完整原始取证**：[continuation-test-pack-02.json](continuation-test-pack-02.json#/findings/3)，原措辞仅在上述裁决边界内采用。

#### TP03-T01 · P3 · 批量质量评分性能例在无效分母替身处早退，聚合路径未被约束

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestQuoteDaoPerformance.test_bulk_vs_individual_query_count](../../tests/integration/test_quote_dao.py)；[QuoteDao.get_bulk_expected_stock_counts](../../data/persistence/daos/quote_dao.py)。
- **契约**：该测试明确声称约束多年窗口的批量数据库调用规模；应先证明进入并完成有效评分路径，再断言调用数与日期数无关，不能把错误降级早退当作性能成功。
- **触发**：正常配置使测试进入get_bulk_sync_quality_scores；count_calls作为_read_db返回dict而非DataFrame。
- **可达链/当前行为**：测试await quote_dao.get_bulk_sync_quality_scores(多年范围) -> get_bulk_expected_stock_counts -> mock _read_db -> dict.empty异常 -> {}。
- **可达链/当前行为**：生产HistoricalSyncStrategy报告与resume都消费批量评分；后者缺评分会列入重同步，性能路径退化影响长窗口查询成本。此处未认定生产已存在N+1错误。
- **可达链/当前行为**：get_bulk_expected_stock_counts对该dict访问df.empty抛AttributeError，经普通异常分支返回{}；get_bulk_sync_quality_scores见无expected_bases即返回{}，尚未调用get_bulk_table_counts_multi。call_count此时为1，满足<=20，测试没有检查非空评分或聚合接线。
- **已尝试反证**：同文件真实DB精确multi-count用例可保护UNION列别名和返回形状，是有效测试；但它没有断言多年评分聚合调用数。
- **已尝试反证**：生产真实实现确实一次调用get_bulk_table_counts_multi；问题是该回归测试不会发现未来把此处改成逐日查询，非现有生产性能缺陷。
- **已尝试反证**：合法空分母是设计允许早退；这里不是专门测试该边界，而是性能fixture形状错误且仅有宽松上界。
- **最小修复**：仅修测试：显式tables并提供_read_db所需trade_date/expected_count DataFrame，或分别mock分母与其他固定开销依赖，使用可观测聚合替身。
- **最小修复**：断言结果含预期日期和expected_base/score，同时断言批量聚合被await一次；比较短/长窗口的固定查询开销，不能只加下界而仍允许错误降级。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：acceptance_cases: 有效两日期/两股数据 -> 非空精确评分且聚合一次。；扩成多日期长窗口 -> 同一批量聚合次数，不按日期线性增加。；仅在隔离验证副本中把聚合接线退化为逐日查询或始终返回{} -> 新测试应失败。
- **回退/兼容**：仅测试修改可逐文件撤回；不更改生产查询或质量阈值。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；_read_db 替身返回 dict，get_bulk_expected_stock_counts 访问 .empty 抛错后返回 {}，评分在聚合之前早退。测试只检查 call_count<=20，1次失败查询即可通过。真实代码目前一次调用多表聚合，其他真实DB计数测试也有效，故降为性能回归 oracle 的P3缺口，而非当前N+1缺陷。
- **裁决限缩/方案约束**：显式 tables、有效日期/expected_count DataFrame；断言至少两个日期的精确非空评分，并观察批量聚合一次。短长日期窗口固定调用数；改成逐日读取或直接空返回必须失败。
- **完整原始取证**：[continuation-test-pack-03.json](continuation-test-pack-03.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP03-F01 · P3 · 四个Schema数据库yield夹具在初始化失败时无法执行自建库清理

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[metadata_db_engine](../../tests/integration/test_schema_consistency.py)；[TestSchemaPathEquivalence.db_via_init_db](../../tests/integration/test_schema_path_equivalence.py)。
- **契约**：测试拥有的独立数据库/引擎在建库成功后，初始化或测试失败均应进入回收；pytest yield fixture只有成功到达yield才有其后teardown，不自动补跑未到达yield的清理。
- **触发**：CREATE DATABASE成功后，metadata.create_all、command.upgrade或await init_db在首次yield前抛错，例如迁移SQL失败、连接中断或初始化被取消。
- **可达链/当前行为**：TestMetadataVsAlembicConsistency请求metadata_db_engine/alembic_db_engine -> 建库 -> create_all/upgrade抛错 -> fixture未yield -> 后置DROP不可达。
- **可达链/当前行为**：test_schemas_are_identical请求两个async fixture -> 建库 -> init_db或直接upgrade抛错 -> 未yield -> DROP不可达。
- **可达链/当前行为**：生产DatabaseMigrator._run_alembic_upgrade对普通异常和CancelledError均重新抛出，未提供测试自建数据库的补偿删除；故不能假设被调方吞错后总能到yield。
- **可达链/当前行为**：四个fixture均先初始化再yield，dispose/DROP位于yield后的普通语句，没有包住setup的try/finally或提前注册finalizer；setup失败不进入这些清理语句，UUID命名的数据库和已创建引擎可遗留。
- **已尝试反证**：UUID降低同名碰撞，并不删除已建数据库。
- **已尝试反证**：_reflect_schema有finally dispose，但它只在fixture成功yield之后执行，不能补偿fixture setup失败。
- **已尝试反证**：test_init_fresh_database_with_real_engine的独立测试已有DROP finally，这是局部正确反例；不修复另外四个generator fixture。
- **已尝试反证**：一次性数据库集群若由外部销毁可最终回收，但文档支持本地integration运行，夹具本身未保证整个集群会销毁；因此限定为测试基础设施健壮性P3，不报告生产数据损坏。
- **最小修复**：仅修fixture：成功CREATE后立刻注册finalizer，或以try/finally覆盖初始化、yield和测试阶段。
- **最小修复**：dispose与DROP分别保证执行机会，连接close也置于finally；只删除记录的本次UUID库，清理失败不得掩盖原初始化异常。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：acceptance_cases: 用合成连接/迁移替身让建库成功、初始化抛RuntimeError -> 仍观察本次库DROP和engine dispose，原错误保留。；初始化抛CancelledError -> 清理自身资源后取消继续传播。；正常yield后测试断言失败 -> 同样回收；CREATE本身失败则不删除未拥有的库。；真实DB清理验收仅在另行授权的一次性隔离环境进行，本轮不执行。
- **回退/兼容**：仅回退fixture资源生命周期改动；不触及生产schema或迁移文件。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；四个fixture均在CREATE成功后先初始化再yield，DROP及engine.dispose在yield之后且没有覆盖setup的finally。create_all/upgrade/init_db在yield前失败时，pytest不会执行尚未到达的yield后清理。其他fixture的清理不知道这些UUID库名。仅接受测试自建库/连接遗留，未称产品库受损。
- **裁决限缩/方案约束**：从成功建库后即进入try/finally或注册finalizer，初始化和测试体均受保护；合成初始化异常时确认dispose/定向drop仍被调用，清理失败不遮蔽首异常。仅处理本fixture拥有的UUID库。
- **完整原始取证**：[continuation-test-pack-03.json](continuation-test-pack-03.json#/findings/2)，原措辞仅在上述裁决边界内采用。

#### TP05-T01 · P3 · force=True 迁移串行测试仅检查完成计数，不能区分真正互斥和并发重叠

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestConcurrentInitDbForce.test_concurrent_init_db_with_force_is_serialized](../../tests/unit/test_cache_manager.py)；[CacheManager.init_db](../../data/cache/cache_manager.py)。
- **契约**：该测试及类说明承诺五次 force=True 初始化都执行，但迁移不可重叠；生产公共 init_db 的 force 标志只绕过已初始化短路，不应绕过 _init_lock。
- **触发**：未来回归只让 force=True 绕过锁，非force仍使用锁；真实 DatabaseMigrator 在await IO时允许其他 force 调用进入。
- **可达链/当前行为**：已完整阅读本测试文件，具体侧效应与两个最终计数断言均可定位。
- **可达链/当前行为**：已完整阅读生产 init_db 函数，当前 async with self._init_lock 同时保护force与普通路径，反驳生产缺锁指控。
- **可达链/当前行为**：静态反例为仅force分支免锁且五次迁移均成功；当前oracle仍得到5/5。未执行此mutation或项目测试。
- **可达链/当前行为**：测试 asyncio.gather 五个 mgr.init_db(force=True) → CacheManager.init_db → await DatabaseMigrator.init_db → AsyncMock(side_effect=tracked_init_db)。
- **可达链/当前行为**：当前替身不暂停；生产迁移有await边界，测试承诺的高风险互斥场景无法由当前计数判别。该结论限定本测试，不声称当前生产并发迁移已发生。
- **可达链/当前行为**：tracked_init_db 只有同步 append，没有任何 await；断言只要求 call_count=5、len(call_order)=5，未记录退出顺序/在飞数量。无论互斥还是重叠，只要都完成五次这些条件都成立。无让出的替身更使常规单线程调度无法制造迁移段重叠。
- **已尝试反证**：test_concurrent_init_db_first_wins_second_skips 的slow_init_db确实await sleep，普通路径只迁移一次；因此简单删除整个锁可能被它抓住，不能声称全文件没有并发保护测试。
- **已尝试反证**：test_cache_manager_lifecycle.py 直接保护close不更换loop-local锁身份；它不测试force迁移段重叠。
- **已尝试反证**：生产现状是正确锁保护；这是测试判别力缺口而非确定生产缺陷。D15既有F01-F09未记录该force oracle反例；债务/例外窄检索未见同项。
- **最小修复**：仅修改该force回归测试的迁移替身：进入时active+=1并更新max_active，await asyncio.sleep(0)后在finally active-=1；可追加有界Event让首个调用明确占住迁移段。
- **最小修复**：保留call_count=5，新增max_active==1、active==0，并校验五次完整start/end配对。不得删锁、改迁移语义或为此真实连DB。
- **最小修复**：普通非force首次赢其余跳过测试保持；本轮不实施修改。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 当前锁实现 + 五个force调用 + 会让出执行权的替身：五次完成，max_active=1，active最终0。；隔离的仅force免锁反例：五次仍完成，但max_active>1，应由新增断言失败。；普通非force三并发：迁移恰一次，原既有测试继续通过。；若替身注入取消/异常，finally计数复位，创建的任务在测试finally中收束，不遗留pending任务。
- **回归输入与期望（未运行）**：proposed_command: python -m pytest tests\unit\test_cache_manager.py -k "ConcurrentInitDb" -v --tb=short
- **回归输入与期望（未运行）**：execution_precondition: 另获执行授权并在可信隔离测试环境运行；当前禁止pytest/项目导入。
- **回退/兼容**：仅回退该测试替身与新增断言；无业务、schema、依赖或数据变更，不降低生产锁要求。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；force用例的迁移替身只有同步append，不让出执行权；最终5次调用/5项也不约束互斥。仅让force路径绕过锁而普通路径保留锁，仍可满足它及普通首次成功后跳过测试。当前生产init_db确在同一_init_lock内，故不报现有并发迁移错误。
- **裁决限缩/方案约束**：替身进入active计数后await受控让出/屏障，finally减计数；5个force全部完成且max_active==1。仅force免锁变体应出现max_active>1；保留普通非force只迁移一次的独立对照。
- **完整原始取证**：[continuation-test-pack-05.json](continuation-test-pack-05.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP05-T02 · P3 · 未舍入对比度边界用例取4.478而非会舍入跨阈值的输入，无法拦截4.499误放行

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestCheckContrastUnroundedComparison.test_ratio_just_below_threshold_is_rejected](../../tests/unit/test_check_theme_contrast.py)；[check_contrast](../../scripts/check_theme_contrast.py)。
- **契约**：测试类、生产注释和正文4.5阈值均明确要求比较未舍入ratio：4.499虽然显示4.50，仍必须判失败。
- **触发**：未来将 if ratio < pair.threshold 改为 if round(ratio, 2) < pair.threshold；对4.499的正文色对将误放行。
- **可达链/当前行为**：完整阅读365行测试；其算法层有黑白21、同色1和sRGB预期，但没有另一个4.499决策用例。
- **可达链/当前行为**：完整阅读check_contrast函数及main，实际比较正确。
- **可达链/当前行为**：独立PowerShell数学式计算#777777/白色=4.478089453577214、两位4.48；4.499两位4.5。未导入或调用项目算法，此为夹具数值静态反证，非项目测试/运行复现。
- **可达链/当前行为**：测试patch单个正文ContrastPair与颜色解析 → 真实check_contrast → contrast_ratio → ratio/threshold分支 → errors → main退出码。
- **可达链/当前行为**：生产当前只在错误显示格式化:.2f，不舍入比较。候选针对未来守护能力，不声称当前主题已因舍入违规。
- **可达链/当前行为**：测试选择#777777/白色，ratio约4.478089453577214，round两位为4.48，错误的先舍入比较仍报错；assert errors保持成立。因而它保护一般低对比度拒绝，但没有覆盖注释指定的舍入跨界。
- **已尝试反证**：现有用例确实能抓普通阈值误配或全面放行，不是完全无效测试。
- **已尝试反证**：四主题errors=[]和Dark/Light实际比值断言保护已有色对；但只对当前有限色值，不能独立证明舍入决策边界。
- **已尝试反证**：D14实际控件配色发现与本候选根因不同；其未来验收提到未舍入边界，但未记录当前#777777夹具不能跨舍入阈值这一测试缺口。
- **已尝试反证**：没有证据表明生产check_contrast已错误舍入，因此仅列test_gap/P3。
- **最小修复**：保留#777777端到端低对比度例；另参数化check_contrast的比较边界，注入contrast_ratio返回4.499、4.5及4.501，避免用未核实RGB值近似关键边界。
- **最小修复**：4.499应有明确正文低阈值错误；4.5/4.501无该错误。正文与图标3.0可各自加入略低/相等边界；无需改生产算法、阈值或主题颜色。
- **最小修复**：保留现有算法纯函数oracle，防止只靠mock掩盖ratio计算错误；本轮不实施修改。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 真实check_contrast + ratio替身4.499 + threshold4.5：errors非空，即使文案显示4.50也必须失败。；ratio=4.5和4.501：当前单色对不产生错误；验证等于阈值可通过。；隔离地仅将比较改为round(ratio,2)：4.499新用例必须失败，而原#777777例仍通过，证明新增oracle能区分目标回归。；既有黑白21、同色1、四主题与main成功用例仍保留，不以更新主题或降低阈值消除失败。
- **回归输入与期望（未运行）**：proposed_command: python -m pytest tests\unit\test_check_theme_contrast.py -v --tb=short
- **回归输入与期望（未运行）**：execution_precondition: 另获执行授权并在可信隔离环境运行；当前不执行项目测试或脚本。
- **回退/兼容**：仅回退新增边界测试；生产对比度比较、颜色与门禁阈值完全不变。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；#777777白底约4.478，舍入两位仍4.48。把生产比较改成round(ratio,2)<4.5仍会被现有测试判低对比，故无法守护类说明中的4.499跨舍入边界。原用例依然有效保护普通低值拒绝，生产目前未舍入，不能升级为配色错误。
- **裁决限缩/方案约束**：保留真实灰色色对，另给真实比较函数注入ratio=4.499/4.5/4.501，分别失败/通过/通过。只在比较前round的变体必须被4.499拒绝；算法数值测试不删除。
- **完整原始取证**：[continuation-test-pack-05.json](continuation-test-pack-05.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP06-TG01 · P3 · 合法过滤条件的断言无法区分正确WHERE与完全不筛选

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestApplyFilters](../../tests/unit/test_data_explorer_query_client.py)；[DataExplorerQueryClient._apply_filters / get_table_count / query_table](../../data/persistence/data_explorer_query_client.py)；[DataExplorerViewModel._build_filters / query_data](../../ui/viewmodels/data_explorer_view_model.py)；[test_table_viewer_filter](../../tests/e2e/test_data_explorer.py)。
- **契约**：客户端公开合同是按传入条件过滤行与计数，UI从filter_col/filter_op/filter_val构造条件并把过滤后的count和rows写入state。测试缺口仅针对本文件这些行为测试，不声称全仓库没有其他保护。
- **触发**：静态反例：把_apply_filters意外简化为无条件return stmt，或只删除stmt.where赋值。此处未实际修改或运行该反例。
- **可达链/当前行为**：DataExplorerViewModel._build_filters从当前UI筛选状态产出tuple。
- **可达链/当前行为**：query_data同时传给get_table_count与query_table并展示其返回结果。
- **可达链/当前行为**：两个客户端方法均依赖_apply_filters构造SQL；UI没有再次按条件筛选返回DataFrame。
- **可达链/当前行为**：当前实现确有stmt.where(op_func(col_obj, val))，所以这是可辨识性缺口，不是已证实产品漏筛。
- **可达链/当前行为**：所有合法运算符正例只要求isinstance(result, sa.Select)，输入stmt本来就是Select；否定例的is stmt也全部成立。query/count固定mock仍返回相同数据，因此这些本包用例不区分条件未应用。
- **已尝试反证**：已读实现使用SQLAlchemy Core条件与schema列白名单，实际不是无条件返回。
- **已尝试反证**：非法表名、非法列、未知运算符、执行错误和空表已有有效反例，未否定这些保护。
- **已尝试反证**：EX-0010只涉及shutdown导入，不改变过滤合同；没有以新场景推翻有意识的静态契约测试。
- **最小修复**：只强化本测试文件：编译生成的SQLAlchemy Select，断言WHERE列/运算符及compiled.params，避免绑定参数名的易漂移硬编码。
- **最小修复**：参数化=、比较运算、LIKE无%/已有%、两个条件AND；保留现有否定例。
- **最小修复**：对get_table_count/query_table捕获conn.execute实际语句，至少各断言一个过滤条件确实进入SQL，不新增DB依赖。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: ('ts_code','=','000001.SZ') -> WHERE含ts_code等号条件，绑定值恰为000001.SZ，不是原stmt。；('name','LIKE','银行') -> 绑定值%银行%；输入%银行%仍恰为%银行%，不重复包裹。；两个合法条件 -> 两个谓词均保留；非法列或未知运算符 -> 不增加谓词。；临时替身_apply_filters返回stmt -> 新增合法条件断言必须失败；恢复实现后才可验证通过。
- **回归输入与期望（未运行）**：future_command: python -m pytest tests/unit/test_data_explorer_query_client.py -q --tb=short
- **回归输入与期望（未运行）**：boundary: 仅未来有批准隔离环境时运行；本次不执行测试或变异。
- **回退/兼容**：仅撤回新增/强化断言；不改过滤算法、UI、DB schema或既有结果合同。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；TestApplyFilters合法条件只检查Select类型，输入本来就是Select；query/count返回固定mock也不观察WHERE。主动反证：E2E test_table_viewer_filter具有目标存在、另一代码消失的有效等号oracle，所以不能说全套允许彻底不筛选。仍成立的窄缺口是LIKE通配符、其他比较符及多条件绑定：仅让LIKE不添加WHERE或不补%的变体不影响已读等号E2E，却通过这些类型断言。
- **裁决限缩/方案约束**：编译真实Select检查WHERE运算和params：LIKE银行应绑定%银行%，已有%不重复补，两个条件均保留；捕获query/count实际execute语句。保留非法列/运算符的有意静态合同，不把它们改为无关DB测试。
- **完整原始取证**：[continuation-test-pack-06.json](continuation-test-pack-06.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP06-TG02 · P3 · 大治理测试未验证main把检查错误转成非零退出

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestDocsConsistencyScript.test_main_returns_zero / TestFletHubCompleteness.test_main_returns_zero_with_flet_hub_check](../../tests/unit/test_docs_consistency.py)；[main](../../scripts/check_docs_consistency.py)；[docs-consistency](../../.pre-commit-config.yaml)。
- **契约**：docs-consistency hook以脚本退出状态判定阻断。守护器不仅要发现错误列表，还必须把非空错误聚合为main返回1；现行实现明确如此。
- **触发**：静态反例：main的if all_errors分支末尾return 1被误改为return 0，或某个checker的extend被删除。未实际实施或运行变异。
- **可达链/当前行为**：.pre-commit-config.yaml的docs-consistency直接执行scripts/check_docs_consistency.py。
- **可达链/当前行为**：__main__调用sys.exit(main())，没有第二层错误列表校验。
- **可达链/当前行为**：main聚合redlines/例外/route等checker错误后在all_errors非空时return1；真实消费者依赖这一输出边界。
- **可达链/当前行为**：当前main已正确实现非零退出；本次没有发现当前发生伪通过，只发现主入口失败合同未由此大测试文件锁定。
- **可达链/当前行为**：本文件大量反例直接调用单项checker并检查错误列表；main本身只有当前干净仓库返回0及编码不抛错测试。上述退出状态回归仍满足这些本包main断言，checker反例也不会经过主入口。
- **已尝试反证**：单项checker反例不是空测试，能发现字段/映射/解析本身的回归，应保留。
- **已尝试反证**：源码main明确return1，不能据缺少测试声称守护器目前fail-open。
- **已尝试反证**：编码测试声明无论0或1均只验UnicodeError，不将它自身的窄合同误判为错误。
- **已尝试反证**：原D15-F02/F05和D21-F03是其他工具的已证fail-open，不与此处未发生的治理主入口回归混为同一新缺陷。
- **最小修复**：仅在本测试文件增加main边界测试，monkeypatch检查器为确定返回值，禁止派生进程或执行真实Git/项目检查。
- **最小修复**：用一个被main消费的checker返回唯一错误哨兵，其余返回空；对glossary保留(errors,warnings)返回形态。断言main==1、stderr含哨兵/FAIL、stdout不含PASS。
- **最小修复**：补全空结果main==0；对新增checker进入main的接线用窄参数化断言，避免仅靠干净仓库返回0证明它被调用。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 代表性redlines/例外/决策树checker各返回一个唯一错误 -> main返回1并输出对应错误。；全部checker空结果 -> main返回0并且只有成功摘要。；仅glossary warning -> 按已批准非阻断合同输出warning但返回0；dual_meaning当前已翻转error，不混入此warning例。；将非空错误分支return1临时变为0 -> 新断言必须失败。
- **回归输入与期望（未运行）**：future_command: python -m pytest tests/unit/test_docs_consistency.py -q --tb=short
- **回归输入与期望（未运行）**：boundary: 仅未来批准隔离环境运行；不在当前共享主机执行。
- **回退/兼容**：仅撤回新增主入口测试；不更改规则语义、例外名单、checker、hook和退出码。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；main聚合非空错误后return1是hook实际消费的边界。现有main测试只验干净仓库0或不抛编码异常；单checker反例并不经过这个出口。只把错误分支return1改0，checker测试仍可正确返回错误而hook假绿。接受缺少该出口回归，不声称当前守护器fail-open，也不贬低大量有意的静态映射测试。
- **裁决限缩/方案约束**：将其余checker置为空，仅代表性checker返回唯一错误哨兵，真调main断言1、FAIL和哨兵；全空断言0，warning-only保留非阻断。无需启动子进程或真实修改治理文档。
- **完整原始取证**：[continuation-test-pack-06.json](continuation-test-pack-06.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP08-T01 · P3 · 历史年限/日历测试没有结果断言，设置失效与空日历仍可正常结束

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[test_config_and_calendar](../../tests/unit/test_infra_history_config.py)；[get_init_history_years/set_init_history_years](../../utils/config/app_prefs.py)；[DataProcessor.initialize_system](../../data/data_processor.py)；[history years helper测试](../../tests/unit/utils/test_config_handler.py)。
- **契约**：该测试声明检查历史年限设置与日历切片，至少必须对设置后的值及消费者入参作可失败断言。生产 setter 将年限钳到 [1,5]；初始化使用该值确保日历窗口并请求 250*years 历史交易日。不得把无异常打印当这些业务结果已验证。
- **触发**：反例为 setter 静默未写入而 getter 保留 1，或日历服务异常降级返回 []。只要调用没有未捕获异常，当前测试继续打印并返回 None。
- **可达链/当前行为**：当前测试直接调用 ConfigHandler.set_init_history_years/get_init_history_years，后者委托 app_prefs 的 typed 配置。
- **可达链/当前行为**：测试直接 await DataProcessor.trade_calendar.get_trade_dates；该方法允许输入无效/无数据/回退失败时返回 []，因此测试的空分支可达，而不是人为不可能状态。
- **可达链/当前行为**：真正的 DataProcessor.initialize_system 读取相同 years 后调用 ensure_calendar_range，并在非 quick 时 sync_historical_data(days=250*years)；测试没有调用或断言此消费者，只在自身做另一套切片。
- **可达链/当前行为**：本项只证明此文件的 oracle 缺口，不声称其他测试包从未覆盖历史配置，也不认定当前产品 setter 已损坏。
- **可达链/当前行为**：set(5) 后只 print getter；all_dates 为空有明确 else 打印；非空时 start_date 由测试自身公式计算后也只 print。I18n 返回未翻译 key 仍可 format 并打印。上述错误值没有触发失败的断言。
- **已尝试反证**：真实 setter 当前确实实现 [1,5] 钳制，未发现其本身错误。
- **已尝试反证**：未捕获异常仍会使本测试失败，因此不使用“永远通过”结论；缺的是业务值断言，不是完全没有执行产品。
- **已尝试反证**：根/unit fixture 有配置与单例隔离上下文，不能据局部 fixture 缺失认定修改了用户真实配置。
- **已尝试反证**：D15-F06 是异步弱断言扫描器漏检的独立根因，能解释该形态没有被工具拦截；不在本项重报扫描器缺陷。
- **最小修复**：将测试改为隔离配置与固定时钟的确定性用例；对真实 ConfigHandler setter/getter 和可观察保存值作断言，不靠 print。
- **最小修复**：若保留“日历/初始化联动”的承诺，调用真实 initialize_system 编排而 mock cache/API/各同步策略边界，核对 ensure_calendar_range 与 sync_historical_data 入参；不要把测试内复制切片算法当生产 oracle。
- **最小修复**：保留异常传播 smoke 价值，但把空数据/失败分支改成明确的 expected result 与零后续调用断言。禁止为补验证在本共享环境直接运行真实 DataProcessor IO。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: input: 隔离配置初值 1，调用 set_init_history_years(5)；expected: getter 和保存的 typed 值均为 5；setter 无操作的反向变异必须导致断言失败。；input: 分别设置 0、6；expected: getter 分别为 1、5，验证现有兼容钳制而非改成新异常接口。；input: 固定时钟、years=5、非 quick 初始化，各外部依赖使用内存替身；expected: ensure_calendar_range 起点为 now-(365*5+30) 天，sync_historical_data 的 days=1250；消费者误用固定年数的变异必须失败。；input: ensure_calendar_range 返回 False；expected: initialize_system 返回 None 且历史同步未被 await，不将缺日历当完成。
- **回归输入与期望（未运行）**：execution_boundary: 后续仅在获得项目测试执行授权且确认隔离后运行对应 unit；本次没有运行或变异源文件。
- **回退/兼容**：只调整测试，不改配置持久化格式、默认年数、生产 [1,5] 钳制或初始化窗口公式。若编排 fixture 与现有契约冲突，退回独立 setter/getter 断言并显式保留联动待验证，不能回退为 print 后宣称通过。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；完整测试只有print：set5后旧值1、日历空、I18n裸key均不会导致结果失败。不是完全不调用产品。主动补读config helper单测已保护getter转发及10钳制为5，不能宣称所有setter/钳制均无覆盖；但对5的实际持久往返和初始化消费年限，这个文件未提供oracle。生产initialize_system确用years算日历窗口/250*years，不能以测试自行切片替代。
- **裁决限缩/方案约束**：隔离配置初值1后set5，断言实际保存值/getter5；固定时钟通过真实初始化检查years5传1250及日历起点。只使合法5不保存或消费者固定用1的变体必须失败。ensure返回False时断言未继续历史同步；空日历合法回退另明确验收。
- **完整原始取证**：[continuation-test-pack-08.json](continuation-test-pack-08.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP08-T02 · P3 · S1-2 队列守护用例只验证标准库队列，无法检测产品容量/溢出处理回退

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestProcessingQueueMaxsize](../../tests/unit/test_infra_queue_threadpool_ratelimit.py)；[NewsSubscriptionService._queue_factory/processing_queue/_safe_queue_put](../../services/news_subscription_service.py)；[真实溢出及unfinished_tasks测试](../../tests/unit/test_news_subscription_service.py)；[test_safe_queue_put_timeout_path_drops_oldest_and_keeps_newest](../../tests/integration/test_news_subscription_shutdown.py)。
- **契约**：该组明确声明守护生产 processing_queue 的 maxsize 与 overflow。真实服务按 loop-local factory 创建容量 500 的队列，满队列超时后在锁内丢最旧项并配对 task_done，消费者处理完毕亦配对一次；这些产品分支需要被产品测试触达。
- **触发**：反向变异为产品 _queue_factory 改成无界 Queue()，或 _safe_queue_put 在满队列时不入队/不丢旧项。四个现有用例依旧自行创建 maxsize=500/2 的标准库队列，其断言不受产品变异影响。
- **可达链/当前行为**：生产 _fetch_and_notify 在初次和增量新闻保存后均 await _safe_queue_put(item)。
- **可达链/当前行为**：_safe_queue_put 通过 processing_queue 获取 loop-local _queue_factory 队列；满队列走 TimeoutError→锁内 get_nowait/task_done→put_nowait。
- **可达链/当前行为**：_processing_loop 消费同一队列、保存分析结果，并在 finally 调 task_done；积压与完成记账影响服务排空。
- **可达链/当前行为**：本项限定为该测试类不能守护其声明的 S1-2 合同，不声称整个仓库没有其他服务队列回归，也不认定真实服务当前无界或死锁。
- **可达链/当前行为**：类中没有导入、实例化或调用 NewsSubscriptionService；QueueFull 只证明 asyncio.put_nowait 对人工容量的行为，既没检查生产配置也没 await 实际溢出分支。
- **已尝试反证**：实际源码当前 maxsize=500，且丢弃项明确 task_done；因此没有将产品回归当作已发生缺陷。
- **已尝试反证**：同文件两组真实 ThreadPoolManager/TokenBucket 测试能检测各自产品行为，问题仅限四个队列用例。
- **已尝试反证**：标准库 QueueFull 用例对 asyncio 兼容性有有限价值，但不能替代生产 factory/overflow 的回归 oracle。
- **已尝试反证**：没有用 AST、旧运行退出码或全仓覆盖率证明缺口；依据是本类完整函数体与具体生产入口的静态对照。
- **最小修复**：用真实 NewsSubscriptionService._queue_factory 断言容量 500，并通过 service 的队列注入接口放入小容量内存队列测试实际 _safe_queue_put。
- **最小修复**：满队列测试受控触发等待超时而不真实长等待，断言队列内精确保留第二旧项与新项；mock 超时时必须关闭/回收被替换的等待协程，避免制造未 await 警告。
- **最小修复**：对余项 task_done 后用有界等待核对 join 完成，覆盖丢弃项完成记账；补直接 CancelledError 传播。依现有 unit 单例/loop-local 重置进行隔离，不启动轮询、AI、DB 或真实消费者外部依赖。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: input: 调用真实 _queue_factory；expected: maxsize==500；产品改成无界 factory 的变异必须使测试失败。；input: 容量 2 队列为空，await 真实 _safe_queue_put(new)；expected: 队列恰含 new，没有丢弃日志/旧项补偿。；input: 容量 2 队列已有 old1/old2，受控触发 put 超时，再入 new；expected: 按 FIFO 取出为 old2/new，old1 被丢弃；余项完成记账后 join 在短有界等待内结束，缺 task_done 的变异失败。；input: 实际 put 等待路径抛 CancelledError；expected: 原取消向上传播，不被转成溢出成功或普通日志，队列原有内容不被错误丢弃。
- **回归输入与期望（未运行）**：execution_boundary: 均为未来内存单测验收输入，当前没有执行服务、创建协程任务或修改产品实施变异。
- **回退/兼容**：仅替换/补充该测试类，保留产品容量、旧接口和丢最旧策略；不调整全局队列阈值或放宽停机超时。若新增测试需未获授权的 IO，应隔离依赖或标记未验证，不恢复标准库-only 用例后宣称产品合同有保障。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；该类只造标准库Queue，不能守护生产factory容量。主动反证非常重要：test_news_subscription_service实际调用_safe_queue_put，精确检查新项及task_done/join；integration也实际并发溢出，故原报告“溢出无有效测试”部分不采纳。保留的唯一窄缺口是把生产_queue_factory改无界，所有这些手工注入有界队列测试仍不受影响。
- **裁决限缩/方案约束**：真实factory/默认loop-local队列断言maxsize=500，填500项后再put_nowait应QueueFull；不能以测试自建Queue代替。复用而不重复重写已有生产overflow/task_done回归。
- **完整原始取证**：[continuation-test-pack-08.json](continuation-test-pack-08.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP10-F01 · P2 · 美股当前日期单测仍只mock旧线程池接缝，实际HTTP出口未隔离

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestNewsFetcherGetUsMajorMoves.test_get_us_major_moves_returns_list_or_str](../../tests/unit/test_news_fetcher.py)；[NewsFetcher.get_us_major_moves._fetch](../../data/external/news_fetcher.py)；[mock_external_services 的 opt-out 与实际HTTP替身](../../tests/conftest.py)。
- **契约**：unit应隔离外部网络，当前日期路径应由受控HTTP输入证明正常取数；过时的mock接缝不能构成网络隔离或成功行为证据。
- **触发**：执行上述任一as_of=None用例；或当前系统日期与get_now().date相同的today/datetime用例。文件no_auto_mock且clean_global_caches每例清空US缓存，测试仅替换ThreadPoolManager.run_async而未替换AsyncClient。
- **可达链/当前行为**：pytest选择该unit文件 → no_auto_mock标记和文件autouse清空_US_MOVES_CACHE。
- **可达链/当前行为**：test_none_as_of_fetches_normally / test_get_us_major_moves_returns_list_or_str → NewsFetcher.get_us_major_moves(as_of=None)，不进入历史短路。
- **可达链/当前行为**：get_us_major_moves → await _fetch → httpx.AsyncClient.**aenter** → client.get；ThreadPoolManager替身不在这条调用链。
- **可达链/当前行为**：返回行情摘要或降级错误字符串 → 测试的宽类型断言均接受，网络是否正常不能由测试结果区分。
- **可达链/当前行为**：生产get_us_major_moves缓存未命中后直接await内嵌_fetch，_fetch经ProxyManager构造httpx.AsyncClient并await client.get公网站点，不再调用ThreadPoolManager。联网时可发真实请求；离线/故障时最多三次请求和1/2秒退避，最后返回错误字符串也满足isinstance(list,str)。未实际执行或声称已经发生网络访问。
- **已尝试反证**：相同文件多数美股用例已使用_wire_http_get配置AsyncClient，说明现有可复用隔离接缝存在；本发现仅限上述遗漏，不指控整文件所有HTTP测试。
- **已尝试反证**：缓存命中与历史日期确可零网络，但autouse主动清缓存，as_of=None明确绕过日期守卫，不能保护这两个确定入口。
- **已尝试反证**：D15既有夹具报告与testing正本说明全局mock对象是NewsFetcher/ReviewManager并支持no_auto_mock；没有把network标记当物理隔离。本轮遵从brief未重读已全读的conftest源码。
- **已尝试反证**：本机网络、代理或其他外部拦截可能使请求在传输前失败；这不使测试本身获得确定的mock隔离，也未据此推断任何敏感数据出站。
- **最小修复**：仅修改这些单测：复用_wire_http_get提供固定JSONP数据，冻结get_now与as_of同源，删除误导性的ThreadPoolManager替身。
- **最小修复**：正常用例断言确切ticker摘要及AsyncClient.get一次await；历史用例保留返回空并增加AsyncClient未构造断言。不改变生产HTTP、重试或缓存政策。
- **回归输入与期望（未运行）**：未来获准隔离环境：合成JSONP含一条指定ticker与涨幅，as_of=None/同日date/同日datetime → 均返回该精确摘要、get仅一次、无线程池调用。
- **回归输入与期望（未运行）**：历史date/datetime输入 → 返回空字符串，AsyncClient构造和get均为零。
- **回归输入与期望（未运行）**：由HTTP替身抛ConnectError → 只在失败用例断言预定重试/降级；在禁止真实传输的测试传输替身下整组仍可执行，不依赖公网。以上均not_run。
- **回退/兼容**：回退仅涉及对应测试替身和断言；保留生产取数行为。若版本兼容要求恢复旧测试，必须保留独立HTTP阻断替身，不能恢复未隔离公网调用。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；文件no_auto_mock使根网络替身明确不生效，autouse清US缓存。两个as_of=None入口必然可到AsyncClient.get，而测试只patch已经不在该路径的ThreadPoolManager。today/datetime分支仅在日期等于get_now当天时同样到达，不能把它们全称确定出站。离线错误字符串仍满足list/str断言。未运行、未联网；结论是单测隔离缺陷，不是已发生信息外泄。
- **裁决限缩/方案约束**：复用_wire_http_get提供固定JSONP并冻结同源日期；正常分支断言指定ticker摘要及一次get，历史分支断言零HTTP。错误/重试单列，删除无效线程池隔离；不修改生产网络策略。
- **完整原始取证**：[continuation-test-pack-10.json](continuation-test-pack-10.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP10-T01 · P3 · 未来新闻剔除用例只检查当日桶，无法检测未来行被放入次日尾桶

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestAggregateDailyDecay.test_future_publish_dropped](../../tests/unit/test_news_sentiment_index_service.py)；[aggregate_daily](../../services/news_sentiment_index_service.py)。
- **契约**：aggregate_daily在as_of存在时应剔除publish_time晚于as_of的全部行；其输出可能含未来交易日尾桶，因此验收必须检查整个结果而非只找当日点。
- **触发**：当前测试输入2026-10-12 15:30 CST，as_of为当天10:00，trade_days含10-13；未来行按>=15:00规则归属10-13。
- **可达链/当前行为**：TestAggregateDailyDecay直接调用纯函数aggregate_daily，不经过DAO上界过滤。
- **可达链/当前行为**：未来15:30行 → assign_trade_day归10-13 → buckets[10-13]。
- **可达链/当前行为**：aggregate_daily将有数据的未来日期追加到output_dates，现有测试仅next查10-12，遗漏尾桶。
- **可达链/当前行为**：生产get_stock_daily_index也消费该返回序列，但当前实现已有过滤；该发现仅是保护此契约的测试oracle缺口。
- **可达链/当前行为**：测试只取10-12点并断言sample_count=0/index=None。若未来过滤被移除，10-12仍无样本，而10-13会额外出现sample_count=1/index=1的尾桶；该测试现有两条断言仍全部成立。这是静态反例分析，未修改源码或运行mutation。
- **已尝试反证**：真实aggregate_daily在publish_cst>as_of时已有continue，故没有证据称当前产品正在前视。
- **已尝试反证**：NewsSentimentIndexService._load_window还把now_cst转换为DAO查询end_utc；这是上游防线，不能代替纯函数直调用例的断言。
- **已尝试反证**：同文件test_future_bucket_appended确证已发布的收盘后新闻可以合法追加次日桶；修复不能用禁止所有未来日期桶替代正确时点判断。
- **最小修复**：只强化该测试：除当日点外断言返回交易日集合不含10-13，且所有输出sample_count均为0。
- **最小修复**：增加同日11:00发布/as_of10:00的参数输入，避免仅凭交易日归属绕过未来过滤oracle；保留15:30输入和合法收盘后尾桶正例。
- **回归输入与期望（未运行）**：未来获准隔离环境：as_of10:00分别输入11:00和15:30新闻 → 全输出零样本、无有样本次日桶；已知09:00新闻仍计入当日。
- **回归输入与期望（未运行）**：仅在可销毁副本中移除publish_cst>as_of过滤 → 强化用例必须失败；恢复过滤后通过。
- **回归输入与期望（未运行）**：as_of16:00且新闻16:00 → test_future_bucket_appended继续得到合法次日桶，避免过度修复。以上均not_run。
- **回退/兼容**：只回退测试输入/断言，不改变交易日归属或生产聚合政策；若日期窗口调整，保留遍历完整输出的时点oracle。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；未来行15:30/as_of10:00会归到次日；若只删发布时间过滤，当前日桶仍0/None，现有两条断言不变，但函数会追加有样本的次日尾桶。合法已发布收盘后消息确允许次日桶，是反对一刀切禁止未来日期输出的重要正例。当前生产已正确过滤，不报前视产品缺陷。
- **裁决限缩/方案约束**：遍历完整输出断言未来未发布行不贡献任何样本/尾桶；同时参数化11:00和15:30两种未来行。已发布16:00/as_of16:00合法次日桶继续通过，删除发布过滤的变体必须失败。
- **完整原始取证**：[continuation-test-pack-10.json](continuation-test-pack-10.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP11-F01 · P3 · R2 扫描器把被前置 BaseException 遮蔽的取消守卫也当作有效豁免

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[_try_has_cancelled_error_guard](../../tests/unit/test_no_cancelled_error_swallow.py)。
- **契约**：该守护自己的文档把兄弟 handler 豁免解释为 CancelledError 已由前者捕获并 raise；R2 是不可吞取消不变量。这里不要求扩展 SCANNED_DIRS 或实现完整控制流分析。
- **触发**：被扫描目录中出现合法 Python：try: await checkpoint()；第一个 handler 是 except BaseException: pass，之后才是 except asyncio.CancelledError: raise。无白名单、无嵌套函数、无动态别名。
- **可达链/当前行为**：TestR2NoSwallowInCurrentCodebase.test_no_silent_swallow_in_scanned_dirs
- **可达链/当前行为**：_scan_directory -> _find_violations -> _try_has_cancelled_error_guard
- **可达链/当前行为**：错误的 has_cancel_guard=True -> broad handler 被跳过 -> all_errors 不包含该吞取消点
- **可达链/当前行为**：_try_has_cancelled_error_guard 不看顺序，见后一个 handler 中的 raise 就返回 True；_find_violations 用这个 try 级布尔值跳过前一个 pass-only BaseException，因此静态推导结果为空。实际 Python 首个匹配 handler 已吞取消，后一个 handler 不会运行。现有兄弟守卫正例均把 CancelledError 放在前面，未排除此反例。
- **已尝试反证**：Exception 日志不报警符合 CancelledError 继承 BaseException 的语义，不是本问题。
- **已尝试反证**：有条件 raise、间接日志、裸 except、显式 narrow handler 等窄规则的已有放行不在此指控内；本反例原本是规则会报告的 BaseException: pass，仅因不可达的后置守卫错误豁免。
- **已尝试反证**：redlines.yml 明确 R2 partial 且仍需人工评审，因此不宣称它是所有 R2 门禁的完全绕过，也未发现当前生产源码已经采用该反例。
- **最小修复**：按每个 broad handler 的位置判断是否已有先于它的有效取消守卫，而非预先把整个 try 标成安全。
- **最小修复**：增加前置 broad/后置 narrow 的反例及反向顺序的正例；保留已批准的目录范围和既有窄检测边界。
- **回归输入与期望（未运行）**：not_run：用内存源码构造 async try/except，BaseException: pass 在前、CancelledError: raise 在后，调用现有 _find_violations，修复后期望 len(errors)==1 且含 R2。
- **回归输入与期望（未运行）**：not_run：仅对调这两个 handler，期望 errors==[]；去掉 narrow 守卫仍为一条；Exception+logger.exception 无取消捕获仍为零。
- **回退/兼容**：本轮未改检测器。若后续修复误报，只回滚 handler 顺序判定和新增用例，不删除 R2 守护或扩大白名单。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；_try_has_cancelled_error_guard不区分handler顺序，_find_violations据try级布尔值跳过所有broad handler。合法源码中BaseException:pass在前、CancelledError:raise在后，实际取消由前者吞掉，但检测返回无错。反例没有动态别名/复杂控制流，属于其自己已承诺的pass-only规则被错误豁免，不是要求实现全控制流分析或扩目录；未发现生产当前使用该模式。
- **裁决限缩/方案约束**：对每个broad handler仅承认它之前的有效取消守卫；用前置BaseException:pass、后置CancelledError:raise期望一条诊断，反向顺序期望零条。保留既定的有限扫描范围和Exception不捕获取消的语义。
- **完整原始取证**：[continuation-test-pack-11.json](continuation-test-pack-11.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### test-pack-12-F01 · P2 · 单例重置测试永久改写真实 AsyncIOScheduler.running 描述符，污染后续用例

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestSchedulerServiceSingleton.test_reset_singleton_shuts_down_running_scheduler](../../tests/unit/test_scheduler_service.py)；[SchedulerService.**init** / _reset_singleton / start](../../utils/scheduler_service.py)；[_reset_all_singletons](../../tests/unit/conftest.py)。
- **契约**：R7 与 testing.md 的逐测隔离要求；测试替身只应在当前测试作用域生效。重建 SchedulerService 实例不应继承前一个测试伪造的运行状态。
- **触发**：该测试在某 pytest worker 中先执行，之后同 worker 再构造真实 SchedulerService/AsyncIOScheduler。
- **可达链/当前行为**：test_reset_singleton_shuts_down_running_scheduler → SchedulerService() → **init** 构造真实 AsyncIOScheduler → 给该类型写 running 描述符。
- **可达链/当前行为**：测试返回/单例重置 → 同一类型对象仍驻留模块中 → 后续 SchedulerService() 的新 scheduler 继续受到描述符影响。
- **可达链/当前行为**：SchedulerService.start 首行读取 scheduler.running，遇伪造 True 直接返回；状态读数也会错误。影响限定为该测试进程/worker 的污染，未声称生产进程遭修改。
- **可达链/当前行为**：仅 ConfigHandler 被 patch，svc.scheduler 是真实 AsyncIOScheduler。type(svc.scheduler).running = PropertyMock(return_value=True) 是普通类属性赋值，不属于任何 patch/monkeypatch 上下文；退出测试不会恢复。_reset_singleton 只关闭旧实例、清空实例指针/初始化标志，不删除被覆盖的 scheduler 类属性。后续新 scheduler 也读取到伪造的 True。
- **已尝试反证**：现有 _reset_singleton 确实释放实例，但实读实现没有恢复 AsyncIOScheduler 类属性，不能隔离此项。
- **已尝试反证**：多数 start 测试主动把 svc.scheduler 换成 MagicMock(running=False)，能绕开污染而不能证明污染不存在；xdist 的 worker 隔离也不能隔离同一 worker 内的测试。
- **已尝试反证**：测试当前 shutdown.assert_called_once_with 验证了单个调用，未核对原描述符是否恢复；没有依赖真实 scheduler.start 的后续断言反证该类赋值。
- **最小修复**：只修改该测试：使用 patch.object(type(svc.scheduler), 'running', new_callable=PropertyMock, return_value=True) 的上下文，或 monkeypatch.setattr；shutdown 也用作用域内替身，禁止裸类赋值。
- **最小修复**：记录原 running 静态描述符，退出上下文后检查恢复，并检查新建真实 scheduler 的初始 running 为 False；无需修改 SchedulerService 的生产重置逻辑来迁就测试污染。
- **回归输入与期望（未运行）**：not_run：先单独运行修订用例，再在同一进程/worker 连续运行它与一个只构造新 scheduler、断言 running=False 的用例；交换顺序均应一致。
- **回归输入与期望（未运行）**：not_run：新建服务注册 _dummy_job，以可控的 start 替身证明 start 不会因前测残留 True 提前返回，并对比上下文前后原描述符 identity。
- **回退/兼容**：仅测试隔离调整；如局部 patch 不兼容，可退回为完整 scheduler 实例替身。不要恢复未托管的类属性赋值。业务与数据库无需回滚。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；测试只patch ConfigHandler，随后直接给真实type(svc.scheduler).running赋PropertyMock；这不是受管理patch。单例reset与unit autouse都只清实例/loop状态，不还原第三方类描述符。同worker新scheduler继续读True，start可错误早退。与TP02-F01的实例对象恢复遗漏不是同一修复点。
- **裁决限缩/方案约束**：改为作用域patch.object/monkeypatch或完整scheduler替身；保存原静态描述符，退出后identity恢复，新真实scheduler初始running=False。正常/异常退出均恢复；不要改生产reset去修测试污染。
- **完整原始取证**：[continuation-test-pack-12.json](continuation-test-pack-12.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### test-pack-12-F02 · P2 · 全体候选共同缺 T+1 的日历回归夹具缺 open，错误日历也会因另一守卫静默通过

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestReviewManagerMarketCalendar.test_suspended_missing_t1_not_misfilled](../../tests/unit/test_review_manager.py)；[ReviewManager.run_review](../../data/persistence/review_manager.py)。
- **契约**：该用例明确承诺区分全市场日历与候选股行情日期并集：两股共同停牌于 6/11 时，不能把 6/12 的可交易行情当 T+1。
- **触发**：回归把 T+1 日历错误改回行情并集 [6/10,6/12]，或把市场日历中缺行情的 6/11 过滤掉。
- **可达链/当前行为**：显式市场日历桩 [6/10,6/11,6/12] + 两股行情仅 [6/10,6/12] → run_review 以 market_pos 计算 T+1 并查 stock_pos。
- **可达链/当前行为**：错误日历选中 6/12 后并没有被当前 no-write 断言辨认：缺 open 导致下一独立 continue，尚未到收益暂存/批写。
- **可达链/当前行为**：若给 6/12 提供正数 open、有效 close/pct_chg，错误路径能算 T+1 收益并暂存 DRAW，正确路径仍无 6/11 行而停写。此为静态反事实，未实际修改/执行项目代码。
- **可达链/当前行为**：两股 fixture 只有 ts_code/trade_date/close/pct_chg，没有任何 open。正确实现因 6/11 缺行情而跳过；错误实现即使选中 6/12，也在 t1_row.get('open') 为 None 的独立守卫继续跳过。唯一终态断言 _batch_update_results.assert_not_called 在两种实现下都成立。
- **已尝试反证**：后面的 RV02 用不同的 T0 close/T1 open/T1 close 验证开盘基准，说明 open 的确是必需输入；它没有覆盖所有候选共同缺 T+1 这一日历反例。
- **已尝试反证**：T5 市场日历用例和缺 observed 日期时的告警用例有价值，但分别针对 T5 和日历缺失的其他路径，不能替代本场景。
- **已尝试反证**：T1 回填停牌用例有另一股票提供 6/11 行，因此行情并集本身包含正确日期，也不能反证共同停牌时的缺口。
- **已尝试反证**：当前生产 run_review 确实调用全市场日历；本项不声称当前生产已按错误日历算收益，仅指出被宣称的回归判据缺乏鉴别力。
- **最小修复**：仅修订该夹具：为实际存在的行情行补有效正数 open，尤其保证 6/12 的 open/close/pct_chg 可计算；无效数据守卫应在独立用例测试。
- **最小修复**：把无关基准/涨停预取明确 stub 为合法或空缓存，避免异常降级成为第二个遮蔽条件；保留正确市场日历下不写入的断言。
- **最小修复**：增加正向控制：恢复真实 6/11 行后应只写 6/11 的 T1 价格/收益，而不是恒不写。
- **回归输入与期望（未运行）**：not_run：正确日历 [6/10,6/11,6/12]、两股都缺 6/11、其他行有效 → 不写。
- **回归输入与期望（未运行）**：not_run：仅对日历来源做受控错误变异为 [6/10,6/12] → 原 no-write 断言必须失败；撤销变异后通过。不得用当前缺 open 的夹具宣称完成此红绿验证。
- **回归输入与期望（未运行）**：not_run：补齐 6/11 行 → batch 内两条 record_id 和 t1_price/pct 精确匹配 6/11 数据。
- **回退/兼容**：仅增加合成测试行情/控制桩，无业务或 schema 改动；如需退回夹具，保留独立的共同停牌鉴别用例，不能通过删除 no-write 断言消除失败。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；两股共同缺6/11的真实run_review用例只提供close/pct_chg，无open。错误日历若选6/12，仍被独立的basis_open None守卫跳过，assert_not_called仍满足。当前全市场日历实现正确，合法停牌不填也正确；问题是高风险T+1错位回归被无关无效行情遮蔽。必须让非目标行完整有效，不能仅因测试标题说停牌就算验证到日历。
- **裁决限缩/方案约束**：补已有行特别6/12的正open、有效close/pct_chg，显式隔离基准/涨停辅助读取。正确日历不写；只把日历错成[6/10,6/12]应导致错误写入而使断言失败；补回6/11行的正对照应按6/11精确写。
- **完整原始取证**：[continuation-test-pack-12.json](continuation-test-pack-12.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### CP13-T01 · P3 · split 成交量复权回归样本先被历史长度门排空，删除成交量复权仍可满足断言

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestOversoldVolumeThreshold.test_split_artifact_volume_spike_should_not_pass_threshold](../../tests/unit/test_strategy_oversold_volume.py)；[_compute_rsi_filter](../../strategies/oversold_strategy.py)。
- **契约**：该测试声明守护拆股导致原始量翻倍不应误过量比阈值；验收样本须到达生产量比判断，且能区别 qfq_vol 与原始 vol。
- **触发**：现有夹具只有6根K线，调用 _math_filter(rsi_period=2, rsi_threshold=101, vol_ratio_threshold=1.5)。
- **可达链/当前行为**：OversoldStrategy.filter 从 context.params 取得 RSI/量比参数并 await _math_filter。
- **可达链/当前行为**：_math_filter 获取历史和股票池，经 ThreadPoolManager.run_async(TaskType.CPU, _compute_rsi_filter, ...) 进入实际生产计算。
- **可达链/当前行为**：_compute_rsi_filter 按因子计算 qfq_close/qfq_vol，再过滤 day_count、RSI 与量比；测试直接调用该链的 _math_filter，但其样本被前置长度门阻挡。
- **可达链/当前行为**：生产 _compute_rsi_filter 在量比阈值之前要求 day_count >= rsi_period*5，即至少10根。当前样本无论原始量比2或复权量比1，都被6<10剔除，out.empty 无法区分两种实现。此为静态可判定的测试假阴性，不是声称生产复权已错误。
- **已尝试反证**：当前生产确有 qfq_vol=vol/qfq_ratio 和 shift(1) 的前5日均量保护，未发现该逻辑已失效。
- **已尝试反证**：test_strategy_base 的75根样本验证1.5/1.7量比门，但因子恒1，不检验拆股中性。
- **已尝试反证**：本包前三个 volume 测试复制表达式，没有替代真实带因子生产链的判别；未检查其他分包，结论仅限此测试缺口。
- **最小修复**：只修改该测试夹具和 oracle，不放宽生产历史门。改用至少75根、period14、复权价格连续下降的样本，保证 RSI 明确可用且低于30。
- **最小修复**：例如前74根 adj_factor=1、最后一根=2；历史 raw close 为目标复权下降路径的2倍、最后一根为目标值；前74根 raw vol=100、最后一根=200。复权量恒200，量比1，应被1.5门排除。
- **最小修复**：增加同价格/因子路径但最后 raw vol=400 的对照，复权量比2，应入选；断言确切股票代码与输入历史长度，并排除 strategy_oversold_insufficient_history 早退。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 75根连续复权下降价格 + split-only 原始量翻倍：结果空，且非历史不足。；相同数据仅最后 raw vol 改400：结果包含目标股票。；在获批隔离副本中将生产 qfq_vol 故意改为原始 vol，第一例必须失败；恢复后两例通过。
- **回归输入与期望（未运行）**：future_command: python -m pytest -n 0 tests\unit\test_strategy_oversold_volume.py tests\unit\test_strategy_base.py --tb=short
- **回归输入与期望（未运行）**：constraint: 需后续独立批准和匹配隔离环境；本轮未运行、未做 mutation。
- **回退/兼容**：未来修改仅限测试夹具/断言，逐项撤回即可；不得用降低 day_count 门、放宽阈值或 xfail/skip 让样本通过。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；完整split用例确只有6根，period2的生产day_count门要求10；删除qfq_vol调整仍先被长度门排除，out.empty相同。生产当前按因子调量并shift历史均量；其他恒因子量比用例不能区分split中性，故是夹具被前置条件遮蔽而不是当前复权缺陷。
- **裁决限缩/方案约束**：构造至少满足period*5且RSI明确可算的历史；例如75根/period14，复权价格连续下降，最后factor翻倍、raw vol100到200表示经济量不变，应排除；同路最后raw vol400应入选。只取消量复权须使首例失败，不降低生产长度门。
- **完整原始取证**：[continuation-test-pack-13.json](continuation-test-pack-13.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### CP13-T02 · P3 · VWAC 数值回归的可选正则不匹配真实文本，唯一数值比较被跳过

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestBuildSupportContext.test_build_support_levels_qfq_calculation_correctness](../../tests/unit/test_strategy_oversold_context.py)；[OversoldStrategy._build_support_context](../../strategies/oversold_strategy.py)；[AIStrategyMixin._mixin_analyze_single](../../strategies/ai_mixin.py)。
- **契约**：测试声明核验复权 VWAC 数值而非只验证含该名词；供 AI 使用的支撑位文本发生价格/成交量口径回归时，该测试应失败。
- **触发**：测试使用 re.search(r'VWAC:\s*(\d+.\d+)', result_text)，并只在 if vwac_match 内执行 assertAlmostEqual。
- **可达链/当前行为**：OversoldStrategy.**init** 将 _build_support_context 注册为 support builder。
- **可达链/当前行为**：_build_support_context 使用 qfq_ratio_series 调整 close/vol，计算最近最多60根 VWAC并生成含(VWAC):的有效文本。
- **可达链/当前行为**：AIStrategyMixin._mixin_analyze_single 遍历注册 builder，将有效 block_text 拼入 strategy_ctx，随后以 strategy_context 传给 ai_client.analyze_stock。
- **可达链/当前行为**：完整生产 builder 输出 '60日量价均价(VWAC): ...'，VWAC 后是右括号而非冒号，因此正则不命中；先前 assertIn('VWAC', result_text) 仍成立，数值断言不执行。即便数值任意错误但保留标签，该用例也不能阻断。
- **已尝试反证**：当前生产代码实际按调整后的 close×vol/总vol 计算，未据此判定产品 VWAC 错误。
- **已尝试反证**：该测试确实构造59根，超过 builder 至少20根门，不是因为短历史无支撑文本。
- **已尝试反证**：其他本包 support 测试主要检查 VWAC 或5.子串；这不能补偿本例被跳过的精确值比较。D07-F03 是主RSI与辅助形态价格口径差异，不是此测试正则根因。
- **最小修复**：将匹配改为真实标签，例如 r'(VWAC):\s*(\d+.\d+)'，先 assert match is not None，并断言 result_valid，再比较解析值；删除条件式可选比较。
- **最小修复**：现有59根输入的独立期望为 (40*500*31 + 10*1000*28)/(500*31+1000*28)=20.689655...，对应两位输出20.69；明确容差而非仅断言包含某数字。
- **最小修复**：另以无复权口径结果约15.25作错误对照，确认保留标签但数值错误时测试会红；不为测试需要改生产文本格式。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 现有合法59根数据返回有效文本，匹配必须存在，解析VWAC为20.69（按既定显示精度比较）。；在获批隔离副本中将 builder 的复权支撑计算临时退为原价/原量，测试必须因数值偏差失败。；若 VWAC 标签缺失或格式改变，匹配断言明确失败，不能通过 if 分支跳过。
- **回归输入与期望（未运行）**：future_command: python -m pytest -n 0 tests\unit\test_strategy_oversold_context.py --tb=short
- **回归输入与期望（未运行）**：constraint: 仅未来验证计划；本轮没有执行正则/提取项目函数、pytest或mutation。
- **回退/兼容**：未来只撤回此测试的匹配与强制比较改动即可，不触业务数据/数据库/依赖；不允许恢复静默跳过作为长期兼容方案。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；59根样本实际能到VWAC计算，但生产文本是(VWAC):，正则要求VWAC:，唯一数值assert藏在if match中而不执行。保留标签、改坏数值仍通过此用例。当前生产确用复权close/vol，不把测试缺口当数值已错；D07辅助价格口径是另一个根因。
- **裁决限缩/方案约束**：先无条件断言valid及真实标签匹配，再按显示精度比较独立期望20.69，去掉条件式数值断言。原价/原量得到约15.25的错误对照必须失败；不为测试改生产文本。
- **完整原始取证**：[continuation-test-pack-13.json](continuation-test-pack-13.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP14-T01 · P3 · 运行时能力标记回滚测试没有制造时间戳差异，无法保护其宣称的回滚行为

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestRuntimeUnavailableCacheTTL.test_probe_rollback_restores_runtime_marks](../../tests/unit/test_tushare_capability.py)；[CapabilityProbeService.mark_api_unavailable](../../data/external/tushare/capability_probe.py)。
- **契约**：该测试声称 probe 回滚时同时恢复 capability_cache 与 capability_runtime_marks；运行时负缓存必须保留其临时属性、可 TTL 过期且不升格为持久权威负缓存。
- **触发**：执行现有测试：先 mark_api_unavailable(top_list) 建立无时间戳的权威 False，probe 替身再对同 API 调 runtime=True，随后模拟持久化异常。
- **可达链/当前行为**：运行时权限失败可经 mark_api_unavailable(runtime=True) 建临时负缓存。
- **可达链/当前行为**：下一轮 probe 对该 API 成功 → mark_api_available 清除时间戳 → 持久化抛错 → 回滚应同时恢复两个快照。
- **可达链/当前行为**：若未来仅恢复 cache 不恢复 timestamp，is_api_available 的 TTL 消费方与 get_effective_synced_tables 会把临时不可用当长期不可用；当前生产恢复代码存在，本项不报现有产品故障。
- **可达链/当前行为**：生产 mark_api_unavailable 明确拒绝将权威 False 降级为 runtime 标记，因此测试中的 runtime=True 调用不增加时间戳。top_list 的 probe 又抛网络错误而不改该条目；字典在回滚前后一直为空。即使移除异常分支中的 runtime_marks.clear/update，这个用例的最终无时间戳断言仍成立。这是静态变异推演，未实际修改或运行变异。
- **已尝试反证**：同文件 test_runtime_mark_does_not_downgrade_durable 已正确验证不降级，正是末尾回滚测试注入无效的原因。
- **已尝试反证**：本包其他测试覆盖 TTL、token 变化/取消回滚和一般缓存恢复，但其 mark 字典为空或没有断言被实际变更后的恢复，不能替代此特定回归。
- **已尝试反证**：生产两处异常处理确实 clear/update runtime_marks；这是测试保护力缺口，而不是证明生产回滚实现错误。
- **已尝试反证**：正常 API 返回空或 None 但调用未抛错时，probe 判 True 是能力可调用合同；不能为制造测试失败擅自将合法空改为不可用。
- **最小修复**：保留权威负缓存不降级的独立测试，给回滚测试新增真实 public API 场景：入口通过 runtime=True 建立 False 缓存值及运行时时间戳，probe成功改True并移除时间戳，persist替身先断言差异再抛错。
- **最小修复**：失败后断言完整 runtime_marks 等于入口快照且 cache 还原；以固定时钟前进 TTL+1 验证恢复 None 和重新纳入有效表。取消回滚可复用同一组非空前置状态。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: 入口通过 mark_api_unavailable(top_list,runtime=True) 建标记并保存时间戳；probe成功后 persist替身确认值True且标记已移除，然后抛 RuntimeError。；回滚后 cache[top_list] 为 False 且原时间戳精确恢复；推进时钟超过TTL后 is_api_available 为 None，top_list 再次进入有效同步表。；在未来获授权的隔离测试中，移除 runtime_marks 恢复语句应使新增回归失败；恢复语句后通过。当前未运行 red/green 或任何项目测试。
- **回退/兼容**：只调整测试前置状态/断言，无产品、持久化和配置变更；若新增用例不稳定，改用注入时钟而非 sleep，不能删除关键差异断言获得通过。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；先建立durable False，再runtime=True时生产明确拒绝降级，timestamp从未产生；probe对该API网络异常又不改它。删除异常分支runtime_marks恢复操作后仍然没有timestamp，现有断言不变。其他TTL/不降级测试有效，却不证明真实变化后的回滚。当前生产确实clear/update两份快照，不报回滚已坏。
- **裁决限缩/方案约束**：入口runtime False带固定timestamp，probe成功改True并移除timestamp；persist替身先断言状态确变再抛错，最终完整cache/marks回到快照。仅删marks恢复的变体必须失败；TTL过期对照仍成立。
- **完整原始取证**：[continuation-test-pack-14.json](continuation-test-pack-14.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### test-pack-15-T02 · P3 · 人工标签乱序测试反转单元素列表，没有实际验证按身份而非位置对齐

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[test_evaluate_gate_matches_records_by_source_id_ignoring_order](../../tests/unit/scripts/test_laya_annotator.py)；[evaluate_gate](../../scripts/laya_annotator.py)。
- **契约**：evaluate_gate明确按(source,source_id)对齐人工标签；人工审核文件排序应不影响一致率和混淆矩阵。
- **触发**：测试pred和gold各仅一条id=0，gold.reverse()后内容/顺序完全相同。
- **可达链/当前行为**：人工抽检sample写文件→人工编辑/重排gold→CLI evaluate读取两个JSONL列表→evaluate_gate→报告及exit0/2。当前实现实际使用gold_by_key查找，是主动反证，不能报产品错配缺陷。
- **可达链/当前行为**：agreement==1只验证单行同标签；不能排除仅在等长且身份集合相同时误用位置配对的回归。该回归仍可通过其他有缺失/ghost身份的案例，因为那些案例可以走正确的回退分支；这些不是同样的非平凡置换输入。
- **已尝试反证**：predict_batch另有丢弃中间项后保持原始index的测试，保护模型输出回填，但不是gold文件顺序的同一个对齐边界。
- **已尝试反证**：已有无匹配、illegal与ghost记录检查身份缺失语义，仍没有等长、同身份集合、不同排列的非平凡输入。
- **已尝试反证**：源码当前按复合键查找正确；本项仅为回归防线空洞，不改变coverage仅观测、不参与PASS的既定政策。
- **最小修复**：将原单条用例改成pred=[(sina_7x24,0,利好),(sina_7x24,1,利空)]，gold用相反顺序，断言matched=2、agreement=1、coverage=1及两格对角计数各1。
- **最小修复**：加同source_id不同source且相反标签的两行，仍反序并要求100%匹配；无需修改产品或安装真实模型。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: input: 两不同身份/标签的pred及反序gold；expected: matched=2, agreement=1, coverage=1, confusion[利好][利好]=1且confusion[利空][利空]=1；input: 同source_id、不同source、不同标签且gold反序；expected: 按复合身份对齐，两格正确计数，不按单独id覆盖；input: 保持现有缺失预测/非法预测/零重叠案例；expected: 现有coverage和异常/退出码语义不变
- **回归输入与期望（未运行）**：future_target: tests\unit\scripts\test_laya_annotator.py
- **回归输入与期望（未运行）**：mutation_expectation: 在获准隔离验证中，临时位置zip配对版本必须被新反序案例拒绝；当前键查找版本应接受。此处未执行变异或测试。
- **回退/兼容**：仅撤回该测试数据/断言修改，不改门槛、标签顺序或生产对齐方式；不把删除反序输入当修复。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；原乱序测试只有同一条id0，reverse不改变排列。最小能区分的输入是两身份、不同标签且gold反序；错误的等长同身份集合位置配对会得0一致率，而当前键查找得1。缺失/ghost身份与不同长度测试有价值，但并不能排除仅在等长集合时误zip的重构。当前evaluate_gate确按(source,source_id)键，不报产品现错。
- **裁决限缩/方案约束**：pred利好id0/利空id1，gold反序，精确断言matched2、agreement1、coverage1及两个对角格；另测相同source_id不同source。保留coverage仅观测、不参与PASS的现行合同。
- **完整原始取证**：[continuation-test-pack-15.json](continuation-test-pack-15.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP16-F01 · P2 · Executor 单测使用硬编码绝对 deadline，合法的大单调时钟原点会提前跳过被测路径

- **类别/边界**：defect；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[_request](../../tests/unit/services/test_scenario_executor.py)；[ScenarioExecutor.**init** / ScenarioExecutor.execute](../../services/ai_service/scenario_executor.py)。
- **契约**：InferenceRequest.deadline 是与执行器 clock 同域的绝对单调截止值，不是超时秒数。正常成功、重试、预算和取消用例应由其局部输入决定，不依赖宿主单调时钟的原点。显式过期/假时钟用例可以自行指定绝对值。
- **触发**：未显式传 deadline 或 clock 的现有成功/取消等用例运行时，事件循环 loop.time() >= 1000000.0。单调时钟无必须小于百万秒的契约；宿主长时间运行或不同原点均是合法环境。本轮未读取宿主 uptime 或运行时钟，也未运行用例。
- **可达链/当前行为**：TestHappyPath.test_single_candidate_success → _request() 默认百万绝对值与 _executor() 无 clock 覆盖。
- **可达链/当前行为**：ScenarioExecutor.**init**(clock=None) → execute: clock=self._clock or loop.time。
- **可达链/当前行为**：execute 首候选 request.deadline-clock()<=0 → CandidateDecision(skipped, deadline exhausted) → break，先于 adapter.invoke。
- **可达链/当前行为**：同一路径被 TestGuards.test_cancelled_error_propagates 使用，故真实取消注入点也被绕过。
- **可达链/当前行为**：_request 默认 deadline=1000000.0；_executor 的常规 kwargs 不含 clock，生产构造默认 None，execute 选择 loop.time。ensure_invocation 后，首候选 remaining<=0 被记 skipped/deadline exhausted 并 break，适配器不被 await。成功测试得到 failed；期待适配器内 CancelledError 的测试未进入取消体，也不会产生所要求的 cancelled 终态。这是测试环境依赖缺陷，不是生产 deadline 守卫错误。
- **已尝试反证**：test_deadline_exhaustion_stops 本来显式使用 loop.time()-1，该用例的过期语义正确，不应随修复改变。
- **已尝试反证**：TestF29DeadlineEnforcement 使用注入的 1000 时钟并显式设置匹配的 1000.05/1030 截止，不受本缺陷影响。
- **已尝试反证**：本包 test_f01_dispatch_authorization_chain 同时注入 1000 时钟与 10000 截止；test_r06_d23_unified_chain_egress._request 用当前 loop.time()+30，均展示合法替代模式。
- **已尝试反证**：不能用当前主机碰巧小于百万或未运行用例作为反证；亦未声称本次实际 pytest 已失败。
- **最小修复**：只修改测试 helper：将 _request 的默认截止改为 None，未传时计算 asyncio.get_running_loop().time()+足够覆盖本用例重试/清理的相对预算；保留显式 deadline 参数。
- **最小修复**：注入假时钟的用例继续显式传同域绝对值，或给 helper 显式 now/clock 参数；不能改生产守卫、无限截止或放宽成功断言来掩盖。
- **最小修复**：为时钟原点平移补一个小参数化回归，分别在 0 与 2000000 原点使用 now+预算，断言相同适配器调用、结果与取消行为。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：input: 假时钟 now=2000000，请求截止=now+30，单候选成功适配器。
- **回归输入与期望（未运行）**：expected: succeeded，adapter.invoke 恰一次；与 now=0 同语义。
- **回归输入与期望（未运行）**：input: 相同大原点，适配器被实际 await 后抛 CancelledError。
- **回归输入与期望（未运行）**：expected: CancelledError 原样传播，提交 cancelled 终态；不是 deadline exhausted。
- **回归输入与期望（未运行）**：input: 显式截止=now-1。
- **回归输入与期望（未运行）**：expected: failed/deadline exhausted、零适配器调用，保持既有守卫。
- **回归输入与期望（未运行）**：command: python -m pytest tests\unit\services\test_scenario_executor.py
- **回归输入与期望（未运行）**：expected: 仅在获得项目执行授权和隔离后运行；本轮禁止执行。
- **回退/兼容**：只涉及该测试 helper 和针对性回归，无业务/DB/配置迁移；如 helper 改动影响显式假时钟用例，回退 helper 后按调用点传同域截止，不修改生产逻辑或吞掉测试失败。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；_request默认绝对deadline=1000000，_executor未注入clock，execute实际用loop.time。合法单调时钟原点大于百万时首候选在adapter前耗尽，正常成功/取消等测试会假失败。没有要求单调原点小于百万的合同；不需读取本机uptime来证明环境依赖。显式同域假时钟/过期测试是有效对照，不应随修复改变。
- **裁决限缩/方案约束**：默认以实际使用的同源clock()+相对预算构造截止，显式deadline继续可覆盖；原点0与2000000均用now+30时成功/取消路径一致，now-1保持零adapter调用。不能改生产守卫或无限截止掩盖。
- **完整原始取证**：[continuation-test-pack-16.json](continuation-test-pack-16.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP17-T01 · P2 · 市值快照优先级测试使用等比例数据，无法识别误用执行日市值

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestMarketCapWeightSizer.test_market_cap_prefers_signal_mv_over_quotes](../../tests/unit/strategies/backtest/test_position_sizing.py)；[MarketCapWeightSizer.compute_weights](../../strategies/backtest/position_sizer.py)；[_rebalance_diff的sizer/目标权重消费](../../strategies/backtest/portfolio.py)。
- **契约**：R24及该测试明确声称：市值加权必须优先信号日T快照，不能误用执行日T+1市值。用例必须让正确来源与错误来源产生不同可观察权重。
- **触发**：被测 sizer 改为优先 quotes.total_mv，或后续重构丢弃 signals 的来源优先级；当前测试 signals=[100,200]，quotes=[1000,2000]。
- **可达链/当前行为**：VectorBacktestEngine._generate_signals 调用 BacktestStrategyAdapter.generate_signal；_normalize_signal_output 透传 total_mv。
- **可达链/当前行为**：PortfolioSimulator.process_day→_rebalance_diff 将 signals_sorted 与 day_quotes 一起传给 MarketCapWeightSizer.compute_weights。
- **可达链/当前行为**：compute_weights 的权重进入 target_weights，再生成 investable×weight 的目标市值，影响实际建仓；测试直接触及这个真实方法。
- **可达链/当前行为**：两组归一化都为[1/3,2/3]，现有精确权重断言对错误来源与正确来源完全相同。这是静态数据关系核实，不是已运行变异测试。
- **已尝试反证**：当前 compute_weights 源码确实先检查 signals 的 total_mv，未证实当前生产实现存在前视缺陷，故分类为test_gap而不是defect。
- **已尝试反证**：test_adapter.py 已精确断言信号快照透传，但只证明上游数据存在，不能识别下游故意忽略信号快照。
- **已尝试反证**：缺列/零/负/None市值fallback有独立测试，修复本测试不得改变这些既有兼容契约。
- **已尝试反证**：未读取其它包，不声称全仓库没有其它能够发现相同回归的测试。
- **最小修复**：只修改该测试的执行日行情为非等比例且反向权重，例如signals=[100,200]、quotes=[400,100]；继续按ts_code断言权重[1/3,2/3]。
- **最小修复**：可增加同一signals配不同quotes的对照，证明输出不随执行日行情变化；保持测试隔离，不引入真实DAO/DB。
- **最小修复**：如补联通回归，将adapter产生的信号交真实sizer/broker，并关闭单票cap干扰，不能仅检查total_mv列名。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：input_expected: signals A=100/B=200、quotes A=400/B=100 → A权重1/3、B权重2/3，不能得到0.8/0.2。；同一signals、更换quotes为其它非等比例数值 → 权重保持不变。；在未来获准隔离环境中，将来源优先级临时变异为quotes优先，新测试必须失败；恢复当前正确实现后通过。
- **回归输入与期望（未运行）**：future_command: python -m pytest -n 0 tests\unit\strategies\backtest\test_position_sizing.py tests\unit\strategies\backtest\test_adapter.py
- **回退/兼容**：仅回退测试数据/新增断言，无产品状态、历史数据或schema回退。若失败揭示实现错误，另开经授权修复，不为保留绿灯恢复等比例输入。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；signals市值100/200与quotes1000/2000归一后均1/3、2/3，确不能辨认实际误用执行日数值的算法回归；权重真实进入portfolio目标仓位。当前生产先用signals，是反证。进一步校正原mutation说明：简单只交换if/elif未必构成有效变体，join仍保留左侧signals.total_mv，必须确认错误实现实际用到了quotes列，不能凭优先级文字宣称变异已验证。
- **裁决限缩/方案约束**：固定signals100/200，quotes改400/100并按代码断言1/3、2/3；再改变quotes仍保持输出。错误对照必须实际选择执行日0.8/0.2，留意join后缀和单票cap，不能用一个仍消费左侧列的无效mutant自证。
- **完整原始取证**：[continuation-test-pack-17.json](continuation-test-pack-17.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP17-T02 · P3 · 均量行序不变性测试的每股数据已按时序且恒量，未构造其声称的排序反例

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestLoadQuotesSortBeforeAvgVolume.test_avg_daily_volume_row_order_invariant](../../tests/unit/strategies/backtest/test_engine.py)；[VectorBacktestEngine._load_quotes / _compute_avg_daily_volume](../../strategies/backtest/engine.py)；[get_daily_quotes的排序合同及实现](../../data/persistence/daos/quote_dao.py)。
- **契约**：测试说明承诺乱序与有序输入结果一致，并守护计算均量前按ts_code/trade_date排序；oracle需在取消排序时真正可区分。
- **触发**：删除或移动_load_quotes中位于_compute_avg_daily_volume之前的排序。现有样本按日期外循环、股票内循环交错；同一股票的日期仍递增，且各股vol分别恒为100/200。
- **可达链/当前行为**：VectorBacktestEngine.run→_load_quotes 读取DAO结果并执行sort→_compute_avg_daily_volume。
- **可达链/当前行为**：_compute_avg_daily_volume 按股票将手×100转股，shift(1)后rolling_mean；对乱序的组内数据，窗口会使用错误时点。
- **可达链/当前行为**：avg_daily_volume供volume_ratio/sqrt_volume滑点成本消费；本测试真实调用_load_quotes，仅enrich依赖被短路。
- **可达链/当前行为**：实际计算有over('ts_code')，交错股票不会让组内日期倒序；恒定序列的末尾20日均量也无法区分时序。测试没有真的分别运行有序/乱序两份输入，只检查每股末值10000/20000，不能为该排序保证提供所声称的反例。
- **已尝试反证**：生产_load_quotes当前有显式排序，不能据测试样本缺陷断言当前生产算错均量。
- **已尝试反证**：test_position_sizing.py 的递增1..10手样本检查前五null及第六300股，确实保护shift(1)；该补偿不检验_load_quotes组内乱序。
- **已尝试反证**：test_converts_vol_from_lot_to_share与broker参与率样本确实保护手/股和名义数量换算；本发现不重复报告旧100倍单位错误。
- **已尝试反证**：此处为单个排序回归oracle的局限，不声称真实DAO一定返回乱序或其它包绝无排序验证。
- **最小修复**：保留_load_quotes真实路径，把两股各六个合法交易日的vol设为不同的逐日变化序列；明确反转/置换每股内部日期。
- **最小修复**：用同一逻辑数据的有序版和打乱版分别加载，按(ts_code,trade_date)对齐比较全部avg_daily_volume，不只取最后一行。
- **最小修复**：第六日设置明显量尖峰并手算仅前五日均量，防止有序/乱序两边同错；保持原有enrich隔离，不新增外部依赖。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：input_expected: 六日为2024-01-02/03/04/05/08/09；A的vol=[10,20,30,40,50,999]手，B=[100,200,300,400,500,9999]手；打乱每股日期后加载。；2024-01-09的A均量=3000股、B=30000股；各股前五个交易日仍为null，不能含第六日尖峰或其它股票的量。；有序/乱序两次加载按代码+日期逐行相等；未来获准隔离环境中取消前置sort时新用例必须失败，恢复后通过。
- **回归输入与期望（未运行）**：future_command: python -m pytest -n 0 tests\unit\strategies\backtest\test_engine.py tests\unit\strategies\backtest\test_position_sizing.py tests\unit\strategies\backtest\test_trade_simulation.py
- **回退/兼容**：只回退增强的测试样本与对齐断言，不调整生产排序/窗口/换算行为；遇差异先区分输入日期规范化与窗口契约，禁止放宽容差掩盖时点错误。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；所谓乱序只交错股票，每股日期仍递增且vol恒100/200；over(ts_code)不会混股，取消引擎前置sort仍得到相同末值。该测试也未比较两次加载。当前DAO有排序合同，是重要上游补偿，不能称生产必遇乱序；但测试明确要守引擎独立排序防线，现有样本对此无区分力，按P3维护性缺口接受。
- **裁决限缩/方案约束**：每股日期实际置换且vol变动，比较有序/乱序加载按代码日期对齐的全部结果；六日A量10/20/30/40/50/999手，第六日均量3000股，不能含当日尖峰。删引擎前置sort须失败，不重复报已修手股换算。
- **完整原始取证**：[continuation-test-pack-17.json](continuation-test-pack-17.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP18-T01 · P3 · 缓存清除信号测试未断言订阅者实际收到通知及退订后的隔离

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestNotifyCacheClearedSubscription.test_notify_triggers_observable_subscribers](../../tests/unit/ui/test_cache_cleared_state.py)；[TestDataSourceTabStateEffects.test_cache_cleared_version_change_triggers_notify](../../tests/unit/ui/test_data_source_tab_contract.py)；[CacheClearedState / notify / reset](../../ui/cache_cleared_state.py)；[TestDataExplorerViewCacheClearedObserver](../../tests/unit/ui/test_data_view_contract.py)；[seq effect消费](../../ui/views/data_view.py)。
- **契约**：本地测试自称验证 notify_cache_cleared 触发 Observable 订阅回调。值递增与观察者送达是不同契约；不能以只观察 seq 替代通知断言。
- **触发**：通知实现仍递增 seq，但属性通知链未触发，或 disposer 不移除订阅者。这里是静态判别反例，不是已执行的故障注入。
- **可达链/当前行为**：DataSourceTab 的 cache_cleared_version effect 发送 notify_cache_cleared。
- **可达链/当前行为**：ui\cache_cleared_state.py::notify_cache_cleared 就地更新模块级 Observable.seq。
- **可达链/当前行为**：ui\views\data_view.py::DataExplorerView 经 use_state(get_cache_cleared_state) 观察变化，active 时调用 vm.mark_tables_stale，后续表重载依赖该通知。
- **可达链/当前行为**：第一测试订阅 no-op，只核 callable(disposer) 和 seq=1，finally 调 disposer 后无后续通知。reset 测试的 collector 从未遇到 notify 就检查 []。数据源 View effect 的补充测试也只核全局 seq 增长。这些断言不能区分值更新且通知正常与值更新但通知丢失。
- **已尝试反证**：生产类明确继承 ft.Observable 且通知正常走 seq 属性赋值，没有证据证明目前真的丢通知；因此不是产品 defect。
- **已尝试反证**：现有测试确实验证了同一实例、seq 值、reset 新实例及发送方 effect，不能说信号链完全未测试。
- **已尝试反证**：D13-T01/D15-F07 处理真实框架挂载/cleanup 等价性，本项仅为此99行单测内可补的回调观测 oracle，不重复要求完整框架模拟。
- **已尝试反证**：未读取其他分包，不能把本项外推为全仓库无消费者测试。
- **最小修复**：保留现有 singleton/seq 测试，将 no-op subscriber 换为记录当前 seq 的 collector；按锁定 Observable 的实际通知语义同步或受控等待后断言收到的值。
- **最小修复**：增加两订阅者：通知、退订一个、再次通知，分别断言已退订者不增长、另一个仍更新；reset 后只新实例观察者收到新通知。
- **最小修复**：另行授权实施时不改生产逻辑，不新增运行服务、浏览器或 DB。
- **回归输入与期望（未运行）**：not_run：首次 notify 后 collector 包含1，第二次包含2；退订后第三次只有另一 collector 收到3。
- **回归输入与期望（未运行）**：not_run：reset 后新实例从0起，notify 后新 collector 收到1，旧 collector 不变。
- **回归输入与期望（未运行）**：not_run：使用保持 seq 增长但抑制送达的受控替身作 oracle 反证，新回调断言必须失败，不能仅看 seq 仍通过。
- **回归输入与期望（未运行）**：经授权的隔离环境仅运行 tests\unit\ui\test_cache_cleared_state.py 与本分包数据源发送方对应用例；本轮未运行任何测试。
- **回退/兼容**：仅撤回新增/强化的单测断言；不变更 Observable API、消费者或业务数据。不能通过放宽断言或扩张弱断言 baseline 宣称该缺口已闭合。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；完整信号测试subscribe的是no-op，notify后只看seq；reset collector从未遇通知就检查空。主动补读消费方测试确能标stale，但它手动run_render_effects强制重渲染，仍不观察通知是否真的送达。故接受仅“值更新但丢回调/退订无效”的本地oracle缺口，不把ft.Observable已提供的语义判成当前产品错误，也不重复D15真实调度/生命周期证据问题。
- **裁决限缩/方案约束**：按锁定Observable协议用两个collector观察1/2通知；退订一个后仅另一个收到3，reset后新实例通知不触旧实例订阅者。保持seq增而抑制回调的受控变体应失败；不以手工re-render代替送达证据。
- **完整原始取证**：[continuation-test-pack-18.json](continuation-test-pack-18.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP20-T01 · P3 · 筛选运行时若干关键断言恒真或与 fake 调用记录格式不匹配

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestOnStrategyChange.test_inits_params_defaults](../../tests/unit/ui/test_screener_view_runtime.py)；[策略变更/运行/分页/导出守卫](../../ui/views/screener_view.py)。
- **契约**：测试必须能区分所宣称的业务回调/早退/隐藏行为；初始化失败、误派发命令和隐藏态错误不能被恒真条件或日志编码不一致掩盖。
- **触发**：例如只删除 _on_strategy_change 的 vm.init_strategy_params(new_val)，或将非loading ProgressRing 固定可见；另如错误地在无策略时调用 run_strategy(None)、将非法页大小改成默认页大小、取消导出后仍调用 export_results('')。
- **可达链/当前行为**：真实 UI Dropdown.on_select→ScreenerView._on_strategy_change→vm.init_strategy_params→运行时读取 vm.state.strategy_params。
- **可达链/当前行为**：测试实际渲染该 View、触发同一事件，但没有能拒绝遗漏初始化的 oracle；其参数 fixture 另行手动设置状态，不能补证此事件。
- **可达链/当前行为**：真实 View _on_run_click、分页/导出 handler→fake 对应命令记录；此处确证的是测试无法观察错误调用，不声称当前生产早退已坏。
- **可达链/当前行为**：初始化用例的 assert any(...) or True 无条件为真；fake.get_strategy_params 自身没有记该名字，该谓词甚至不对应实际初始化结果。
- **可达链/当前行为**：fake 记录 run_strategy:<key>、change_page_size:<n>、export_results:<path>，负向却检查裸名称不在列表；这些错误调用不会使相应断言失败。
- **可达链/当前行为**：隐藏进度用例用 len(status_rings)>=0，任何列表都满足，即使 loading=False 仍有 visible=True 进度也能通过该断言。
- **已尝试反证**：当前生产 _on_strategy_change 确实调用初始化、_on_run_click 有无策略早退、progress_visible=state.loading；本轮没有产品回归证据。
- **已尝试反证**：同文件正向排序/导出传参、CSV与Excel分流、多个取消传播和 run_id 检查有效；问题限定在列出的 oracle，不否定整个4204行测试。
- **已尝试反证**：没有运行 mutation 或 pytest；以上为对断言布尔逻辑和记录格式的静态反例，不宣称全测试套件一定放行相同变更。
- **最小修复**：去掉 or True，直接检查 fake_vm.state.strategy_params 和初始化调用。
- **最小修复**：将裸字符串成员判断改为命令级 Mock 未调用或 not any(c.startswith('<command>:')...)；清空前置记录以隔离本次事件。
- **最小修复**：为状态栏进度使用稳定唯一定位和可见性断言，而非长度下界0。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: input: 策略value，number参数num_param默认42，触发dropdown选择；expected: 初始化调用一次，state.strategy_params准确为42；删除初始化调用时本用例失败。；input: 无策略运行、页大小not_a_number/None、文件选择返回空字符串；expected: 对应业务命令均未调用；人为插入带参数的错误调用时断言失败。；input: 无stream_cards，loading先True再False；expected: 状态栏进度先可见后隐藏；固定visible=True时失败。
- **回退/兼容**：仅补测试 oracle；若真实控件定位不稳定，保留正向用例并回退该定位改动，使用唯一状态栏识别而非恢复恒真断言。业务代码无需回退。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；逐一核对真实fake记录：初始化断言or True恒真；run_strategy/change_page_size/export_results实际记录带冒号参数，否定断言却查裸词；隐藏进度断言len>=0恒真。删init调用、无策略误run(None)、非法页大小误提交50、取消导出仍export('')、非loading进度固定可见，分别都不被对应oracle拒绝。当前View早退/派生实现正确，正向参数/取消测试有价值，不泛化整个大文件无效。
- **裁决限缩/方案约束**：精确检查初始化state.num_param=42和init调用；负向按命令前缀或spy未调用判断；无stream卡时唯一状态栏进度按loading True/False正反断言。不得只删or True后继续检查fake根本没记录的get_strategy_params。
- **完整原始取证**：[continuation-test-pack-20.json](continuation-test-pack-20.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP20-T02 · P3 · 固定提供AI prompt参数的用例把必需保存/恢复控件丢失转为skip

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[screener_view_with_params_env](../../tests/unit/ui/test_screener_view_runtime.py)；[build_param_control](../../ui/views/screener_view.py)。
- **契约**：fixture 已提供 selected_strategy=value 与 ai_system_prompt textarea；在该固定适用场景，保存/恢复是必需交互，不是缺环境时可选测试。
- **触发**：仅一侧 save 或 restore TextButton 在重构中被删、错误label导致无法定位，或参数面板没有渲染该入口。
- **可达链/当前行为**：fixture提供ai_system_prompt→真实build_param_control→两个TextButton→_on_save_prompt/_on_restore_prompt→page.run_task→VM命令。
- **可达链/当前行为**：该链中按钮丢失会真实移除用户入口，而局部回归测试主动将该现象转skip；没有可选平台条件支持此skip。
- **可达链/当前行为**：两个恢复用例与三个保存用例都在找不到按钮时 pytest.skip，错误态/成功态/inline校验断言全部不执行。另一个结构用例只检查TextButton总数>=2，不唯一识别save/restore；同树还有go_sync等TextButton，不能保证具体入口存在。
- **已尝试反证**：当前源码明确构造两个按钮；控件存在时五个测试会执行真实View闭包、AsyncMock和精确toast/inline断言，有实际价值。
- **已尝试反证**：通用参数渲染用例可能捕获整个面板丢失，不能保证单个按钮丢失被拒绝；本结论不假设所有其他测试也会通过。
- **已尝试反证**：D15-F01 是 E2E canary 在另一fixture中的skip根因，本项是固定单元fixture对必需控件的独立处理，不重复计原E2E缺陷。
- **最小修复**：去除五处按钮缺失skip，用按稳定标识或精确内容/图标查找的断言，并分别保证数量为1。
- **最小修复**：保存/恢复行为测试验证策略key和输入prompt/默认prompt回填，保留失败inline及取消传播的独立契约。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: input: fixture的value策略及ai_system_prompt textarea；expected: 两个入口各1个；保存传正确策略/草稿，恢复回填默认草稿。；input: 受控变体中分别只移除save或restore按钮；expected: 对应测试失败而非skip；另一按钮用例仍独立有效。；input: VM返回(False,prompt_err_length)；expected: 明确字段inline错误且没有成功toast。
- **回退/兼容**：只调整测试适用性判断；若确有平台不可用条件，单独按已核实的外部前置条件skip，不能以被测控件消失为条件。无需业务修改。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；fixture明确给value策略及ai_system_prompt textarea；五个行为测试却在目标save/restore按钮丢失时skip。真实builder分别创建两个入口，缺一个可成为真实功能回归，而总TextButton数量并不唯一识别二者。按钮存在时的inline/toast断言有效，不要求删除这些断言，也不与D15另一E2E canary的skip根因合并。
- **裁决限缩/方案约束**：固定适用fixture找不到必需按钮直接失败，分别验证唯一save/restore及可调用回调；只移除其中一个的变体必须失败而非skip。保持真实平台前提的独立受控skip，不以被测控件消失作前提。
- **完整原始取证**：[continuation-test-pack-20.json](continuation-test-pack-20.json#/findings/1)，原措辞仅在上述裁决边界内采用。

#### TP20-T04 · P3 · 分割器卸载测试卸载的是另一个从未挂载的组件

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestUnmount.test_unmount_safe](../../tests/unit/ui/test_resizable_splitter_body.py)；[ResizableSplitter](../../ui/components/resizable_splitter.py)；[mount/unmount helpers](../../tests/unit/ui/component_renderer.py)。
- **契约**：卸载安全用例应将同一组件从mounted状态带入cleanup；新建但未渲染的Component没有该实例已创建的hooks、observable订阅或effect。
- **触发**：真实挂载实例在卸载时出现订阅清理异常或漏退订。
- **可达链/当前行为**：真实ResizableSplitter调用ft.use_state(get_observable_state)和ft.use_effect(_load_effect)，挂载后存在与特定Component关联的生命周期资源。
- **可达链/当前行为**：当前用例两个factory调用确定产生两个Component，目标卸载路径没有被该测试执行；不依赖随机调度即可看出观察对象错误。
- **可达链/当前行为**：先_render(_make_splitter())并丢弃实例，随后run_unmount_effects(_make_splitter())创建另一实例。测试没有让前者执行卸载；对空实例的无异常不能观察前者的cleanup。
- **已尝试反证**：该组件没有业务IO任务或自定义长期任务cleanup，故不推断当前存在资源泄漏或P2事故。
- **已尝试反证**：同文件折叠/展开测试确实保存并重渲染同一个实例；其他文件也有真实同实例unmount测试，说明测试辅助模式本身并非一概无效。
- **已尝试反证**：此局部对象错配不同于D15-F07的框架调度等价证据不足，即使保留现有替身也可独立修正。
- **最小修复**：把创建、挂载、卸载绑定到同一component变量；避免丢弃挂载实例。
- **最小修复**：增加挂载中observable通知会调度、卸载后相同通知不再调度的最小正反对照，不引入真实配置或客户端。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：cases: input: 创建一个splitter，挂载后改变其订阅的人工locale状态；expected: 该实例发生状态通知/更新调度，证明观察链有效。；input: 卸载同一实例，再改变同一人工状态源；expected: 无新调度且订阅不再回调；注入该实例cleanup异常时用例失败。
- **回退/兼容**：仅调整测试实例归属和观察；若辅助observable观察方式不兼容，保留同实例卸载修正并记录额外观察待补，不恢复对新实例卸载的假证明。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；两次_make_splitter确定创建不同Component，第一实例经_render真正挂载后被丢弃，unmount作用于从未挂载的第二实例。helper按传入实例解绑订阅/运行cleanup，故挂载实例的卸载异常或未退订不会被本例观察。当前组件无长期业务任务，不能推断已泄漏；这项局部对象归属错误不依赖真实Flet调度，区别于D15-F07。
- **裁决限缩/方案约束**：保存同一个component完成mount→unmount；挂载中人工locale通知有更新，卸载后相同通知不再调度。仅注入该实例cleanup异常时须失败，不以新空实例的no-error代替。
- **完整原始取证**：[continuation-test-pack-20.json](continuation-test-pack-20.json#/findings/3)，原措辞仅在上述裁决边界内采用。

#### test-pack-21-T01 · P3 · Toast的show/remove并发回归用例在所有show完成后才remove，未覆盖所声称的交错丢更新

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestShowBehavior.test_show_and_remove_thread_safety_consistency](../../tests/unit/ui/test_toast_manager.py)；[show](../../ui/components/toast_manager.py)。
- **契约**：该回归用例自身明确要求并发show与remove的读改写不丢更新；公开show可跨线程调用，dismiss移除不得用旧快照覆盖期间新加的通知。
- **触发**：测试启动16个show worker，for fut in futures逐个result并离开线程池上下文之后，才调用_remove_toast(first_id)。Barrier只同步show，不参与remove。
- **可达链/当前行为**：ui\components\toast_manager.py:ToastManager.show → 模块show(page,...) → _mutation_lock内读改写state.toasts。
- **可达链/当前行为**：ui\components\toast_manager.py:ToastManagerView → ToastCard(data,on_dismiss=_on_dismiss) → timer完成或关闭/action点击 → _on_dismiss → _remove_toast → 同一state.toasts。
- **可达链/当前行为**：反例输入：低于容量的初始队列[A]；无锁remove读取[A]准备移除A，另一线程show(B)写[A,B]，remove随后以旧快照写[]，B丢失。当前同锁实现阻止该交错，但现有测试未把它作为验收条件。
- **可达链/当前行为**：断言验证并发show后队列为5，再串行删除一项后为4。即使仅移除_remove_toast中的共享锁、保留show锁，该测试的既定执行顺序仍无重叠读改写，不会暴露该锁退化。此为静态语义反例推导，未执行变异测试。
- **已尝试反证**：生产_remove_toast当前持有与show相同的_mutation_lock，因此不能将此测试缺口写成当前产品存在race。
- **已尝试反证**：并发show的数量守恒、单例ID分配和串行remove不是无效断言；它们保护不同子契约，应保留。
- **已尝试反证**：文件内其他remove、ManagerView dismiss、timer/cleanup用例分别验证功能与任务句柄，但没有在worker show期间执行remove。
- **已尝试反证**：此处缺的是普通Python队列读改写的特定交错，不依赖Flet客户端效果是否与替身等价，故不以既有生命周期ID再计一次同根因。
- **最小修复**：保留现有并发show用例，可调整名称说明其实际范围；另加受控show/remove交错回归，不能仅重命名后宣告补齐。
- **最小修复**：使用测试专用Event/可观察真实锁包装及状态快照钩子标记remove已取旧快照、writer已尝试获取共享锁、两操作完成。正确实现验证writer在remove临界区不能提前写；在隔离变异中撤掉remove锁后，强制writer写入后再释放旧快照写回，并要求最终[B]断言失败。
- **最小修复**：以仅一条旧通知和一条新通知避开MAX_TOAST_COUNT裁剪干扰；所有等待有上界，finally释放屏障并join worker，不使用概率性sleep或仅最终数量碰运气。
- **最小修复**：只修改测试，不改当前生产锁，也不借机重构Toast生命周期。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：input: 初始[A]，受控并发remove(A)/show(B)，在正确共享锁路径等待两方完成。
- **回归输入与期望（未运行）**：expected: 最终ID/消息恰为[B]；A已移除，B未丢失，writer未在remove临界区提前提交；无遗留worker。
- **回归输入与期望（未运行）**：future_command: python -m pytest tests\unit\ui\test_toast_manager.py -q
- **回归输入与期望（未运行）**：input: 独立隔离副本中仅临时去掉_remove_toast共享锁，固定旧快照读取→show写新值→remove旧快照写回顺序。
- **回归输入与期望（未运行）**：expected: 新增回归稳定失败；恢复锁后通过。原有并发show与串行remove用例继续通过，不凭概率性压力或代码覆盖率宣告该场景已测。
- **回退/兼容**：仅回退新增测试及对应名称/说明调整；保留现有生产_mutation_lock和原回归。若同步钩子不稳定，应撤销该测试方案并标记场景未验证，不削弱最终内容断言换取绿色。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；16个show future全部result且线程池退出之后才remove；barrier未包含remove。仅撤去remove共享锁的变体不会让本测试读改写重叠。真实公开show可跨线程，dismiss来自View/计时器，初队列[A]、remove旧快照、show(B)、旧快照写回可丢B，是可达错误。当前两方法均持同锁；不把缺测试写成当前竞态，也不混同UIR-R01按位置组件状态继承。
- **裁决限缩/方案约束**：另加容量以下[A]与新B的受控show/remove交错，观测共享锁边界且最终只剩B；撤掉remove锁应由该交错失败。保留现有并发show数量/ID保护，所有屏障有界并在finally收束，不靠概率sleep。
- **完整原始取证**：[continuation-test-pack-21.json](continuation-test-pack-21.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP22-T01 · P3 · 回测 None 回调用例被测试 helper 替换成 mock，未进入目标分支

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[_render_panel](../../tests/unit/ui/components/backtest/test_backtest_config_panel.py)；[BacktestConfigPanel._on_run_click](../../ui/components/backtest/backtest_config_panel.py)。
- **契约**：显式传 None 的用例应覆盖可选消费者为空时不调用的分支，而不是默认 mock 正常调用分支。
- **触发**：test_run_click_none_callback_no_error 调 _render_panel(on_run_backtest=None)。
- **可达链/当前行为**：现行生产 handler 明确支持 None；错误发生在测试构造，不是声称产品当前会调用 None。
- **可达链/当前行为**：helper 对 None 无条件创建 MagicMock；组件永远收到可调用对象。即使产品删掉 is not None 守卫，这条仅不抛异常的用例也不能区分。
- **已尝试反证**：其它默认回调/字段回传用例仍有效；这里只否定此目标分支的覆盖声称。
- **最小修复**：用独立 sentinel 区分 helper 未传参数与显式 None，或该用例直接 make_component(on_run_backtest=None)。
- **回归输入与期望（未运行）**：not_run：显式 None 渲染并触发合法输入的运行 handler；对照删除产品 None 守卫的临时 mutant 应失败，恢复后通过。默认 spy 用例保持原有 call_args 检查。
- **回退/兼容**：仅涉及测试 helper/目标用例；如回退应保留原默认 spy 语义，不修改业务行为。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；_render_panel把显式None转换MagicMock，目标测试因此从未把None传给组件。默认合法输入不触validation早退，若去掉生产None守卫仍会调用这个mock而不抛错。其他配置字段/Enter正例有效，不能替代可选回调为空的分支。
- **裁决限缩/方案约束**：sentinel区分未传与显式None，或本例直接构造None组件；合法默认配置下触发handler不报错，去掉None守卫的变体必须失败，默认spy用例保持原有字段断言。
- **完整原始取证**：[continuation-test-pack-22.json](continuation-test-pack-22.json#/findings/3)，原措辞仅在上述裁决边界内采用。

#### TP22-T02 · P3 · 倒置下拉宽度边界用例与被修复旧公式返回相同，不能守护归一化

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[test_calc_dropdown_width_inverted_bounds](../../tests/unit/ui/theme/test_app_styles_dropdown.py)；[AppStyles.calc_dropdown_width](../../ui/theme.py)。
- **契约**：测试自述要防 min>max 时返回非法 min 的旧公式；输入应能区分归一化 lo/hi 与旧 max(min_width, min(calculated, max_width))。
- **触发**：现有用例以 min_width=400、max_width=200、100 个 A 调用。
- **可达链/当前行为**：这是纯函数回归用例的区分力缺口；已读生产函数有 lo=min/hi=max，未报告当前计算实现错误。
- **可达链/当前行为**：当前归一化实现和旧公式都返回 400；后续区间断言 200<=400<=400 也均为真。单把代码恢复旧公式时，该回归用例仍无法发现。
- **已尝试反证**：长文本到上限的断言本身成立，不能删除；它只不足以证明倒置边界的下界正确。
- **最小修复**：保留原上限样例，追加 options=['A']、倒置 400/200，明确断言结果 200；再用受控 char_width/padding 核中间值不被强制提到 400。
- **回归输入与期望（未运行）**：not_run：正常实现短文本返回 200；旧公式 mutant 返回 400 且测试失败。默认范围、CJK 及最大宽度既有用例保持不变。
- **回退/兼容**：仅新增目标测试数据，无生产修改；回退新增断言不影响主题行为，但须保留此缺口记录。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；倒置400/200与100字符输入，归一化算法和旧max(min,min(calc,max))都返回400；两条现有断言均不能区分。当前生产lo/hi归一正确，长文本上限例也有价值，缺的是倒置下界/中间值而非整个宽度算法测试。
- **裁决限缩/方案约束**：保留长文本例，加options=['A']、倒置400/200期望200，另固定char_width/padding构造中间300期望300；旧公式恒400须失败。
- **完整原始取证**：[continuation-test-pack-22.json](continuation-test-pack-22.json#/findings/4)，原措辞仅在上述裁决边界内采用。

#### TP24-T01 · P3 · singleton close 异常用例在被测调用前注销 Dummy，没有执行或断言所声称的回退分支

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestSingletonRegistry.test_reset_handles_close_exception](../../tests/unit/utils/test_singleton_registry.py)；[reset_all_singletons](../../utils/singleton_registry.py)；[后置reset](../../tests/unit/conftest.py)。
- **契约**：无 _reset_singleton 的测试单例若 close 抛异常，registry 应记录已脱敏告警、继续将 _instance 清空并继续重置后续单例。宣称覆盖该行为的用例应实际到达该分支并观察效果。
- **触发**：以后 reset_all_singletons 的 fallback 分支回归为 close 异常直接向外逃逸、异常后不清 _instance，或中断后续重置。
- **可达链/当前行为**：测试体 register_singleton→赋 DummyCrashyClose._instance→_registry.remove→返回，整个可见函数不存在触发点。
- **可达链/当前行为**：真实 reset_all_singletons 仅遍历当前 registry；fallback close 的 try/except 位于 _instance=None 之前。被测异常分支与声明的回归契约确实存在。
- **可达链/当前行为**：当前测试只定义 close 会抛异常的 Dummy、赋 _instance，再在 _lock 下移除 Dummy。没有调用 reset_all_singletons，也没有任何 assert。unit 的后置 reset 即便执行，也看不到已移除的 Dummy。
- **已尝试反证**：相邻 test_reset_calls_close_before_nuking_instance 实际调用 reset_all_singletons，但 close 成功，不会覆盖异常后的继续清理。
- **已尝试反证**：test_reset_all_handles_exception 覆盖的是 _reset_singleton 抛异常的另一分支，不是 fallback instance.close。
- **已尝试反证**：生产实现当前有 catch、脱敏日志和继续清空保护。本项不声称产品 reset 已坏，也不声称全库绝无其他覆盖。
- **已尝试反证**：原 D15-F06 是扫描器遗漏 async def；这里是同步测试完全未调用目标，根因不同，不重复登记扫描器缺陷。
- **最小修复**：仅修测试：用 monkeypatch 暂换 _registry 为专用的 failing-close 与后续正常-reset 两类，避免主动清理所有真实资源；在 Dummy 仍注册时调用 reset_all_singletons。
- **最小修复**：用调用计数或 spy 断言 close 一次，检查 _instance is None、后续 reset sentinel 发生，并通过 caplog 检查对应 warning；将 registry 恢复置于 fixture/finally，不能只在断言成功后 remove。
- **最小修复**：不通过删测试、增加裸 assert True 或只检测 RuntimeError 类名补齐。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：target: tests\unit\utils\test_singleton_registry.py::TestSingletonRegistry::test_reset_handles_close_exception
- **回归输入与期望（未运行）**：acceptance_cases: close 计数后抛合成 RuntimeError→close计数1、_instance=None、后续reset计数1、对应warning存在。；定点变异去掉 fallback except、去掉异常后清空、或异常后提前 return→新增断言至少一项失败。；即使测试断言失败，恢复后的 registry 不残留专用 Dummy；在授权隔离环境验证前后顺序不污染相邻用例。
- **回退/兼容**：只涉及该测试及局部 fixture 的修订，可逐项撤回；生产代码与真实单例状态不应改动。若最小修订依赖全局 registry，先恢复原 registry 对象再撤回，不能以跳过测试或放宽 oracle 回退。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；完整test_reset_handles_close_exception没有调用reset、没有assert，Dummy赋实例后即从registry移除；unit后置reset也看不到它。相邻成功close和_reset_singleton异常用例是不同分支。生产fallback目前捕获close异常并清实例，但若此分支抛出/不清/提前结束，目标测试不受影响。生产单例必须实现reset，限制了fallback影响面，保持P3。
- **裁决限缩/方案约束**：以专用registry仍注册的failing-close与后继正常类调用reset，断言close一次、实例None、后继reset及warning。去catch/不清实例/提前return三个变体应失败；finally恢复registry，不触真实资源。
- **完整原始取证**：[continuation-test-pack-24.json](continuation-test-pack-24.json#/findings/0)，原措辞仅在上述裁决边界内采用。

#### TP24-T02 · P2 · legacy key purge 成功用例只检查布尔值，漏删主备/marker 与清缓存回归均可逃过该 oracle

- **类别/边界**：test_gap；独立静态接受，运行未复现；引入时间unknown。
- **位置**：[TestPurgeLegacyKeyFiles.test_purge_removes_key_and_backup](../../tests/unit/utils/test_security_utils.py)；[SecurityManager.purge_legacy_key_files](../../utils/security_utils.py)；[purge_legacy_key_if_safe](../../utils/config/secrets.py)。
- **契约**：调用方已确认可安全清理后，purge 应尝试既有主key、备key和marker，并清空旧的内存key。返回 True 的契约只是至少一个文件删除，不是三者均清除的证明。
- **触发**：后续误将删除列表缩成仅主key、遗漏备key/marker，或者忘记将非空 _key 清除。
- **可达链/当前行为**：上层 purge_legacy_key_if_safe 的允许分支交给 SecurityManager.purge_legacy_key_files，底层无再次数据残留判断；此处属于已获准清理后的效果契约。
- **可达链/当前行为**：当前底层源代码实际遍历 (KEY_FILE, KEY_FILE_BAK, _LEGACY_MARKER)，逐路径捕获 OSError 后继续；布尔值为任一成功。删掉某个列表元素而保留主key删除，仍能满足当前成功测试的 removed is True。
- **可达链/当前行为**：TestPurgeLegacyKeyFiles 的失败测试把 exists 对全部路径设 True、所有 remove 都抛异常，只要求返回 False/日志；它也不区分列表缺项。
- **可达链/当前行为**：成功用例创建主备占位文件，但 mock os.remove 后不检查 calls，也不检查文件消失；marker 未重定向/建立。setup 已将 _key=None，故 _key is None 对移除清缓存语句也没有判别力。只删主key仍可返回 True，三个现有 purge 用例的这些断言不足以识别该回归。
- **已尝试反证**：当前生产循环确实包含三个路径，并非已确认的实际漏删缺陷；严重度针对敏感清理回归的验证缺口，不声称本机遗留了任何真实密钥。
- **已尝试反证**：同文件 TestSaveKeyBackup 实查合成主备字节，只验证写入，不验证 purge。ConfigHandler 的 purge tests 仅 mock 整个底层函数，同样不能证明删除效果。
- **已尝试反证**：D02-F01/D02-T01 是读取失败默认被误作无密文而不应删除的问题；本项是在已经允许删除后遗漏效果断言，触发、保护目标和修复对象不同。原问题只引用原ID，不重复计缺陷。
- **已尝试反证**：不能把 True 改为必须三项全部成功来迁就测试；现有 any-success 返回契约及缺文件/失败继续处理必须保留。
- **最小修复**：仅修测试：将 KEY_FILE、KEY_FILE_BAK 和模块 _LEGACY_MARKER 全部重定向同一受控合成目录，创建三个占位文件，将 _key 设非空合成 sentinel。
- **最小修复**：成功路径不 mock os.remove，检查三个文件均不存在及 _key is None；或者保留 mock 时至少精确核对三条调用路径，不以 call_count 单独替代路径集合。
- **最小修复**：增加一个路径 PermissionError、其他路径成功的场景，断言失败文件保留、其余删除/尝试、结果 True 且内存已清；全失败和全缺失仍 False。所有旧状态用 monkeypatch/finally 恢复，绝不能落到真实 key 路径。
- **回归输入与期望（未运行）**：status: not_run
- **回归输入与期望（未运行）**：target: tests\unit\utils\test_security_utils.py::TestPurgeLegacyKeyFiles
- **回归输入与期望（未运行）**：acceptance_cases: 合成三文件+非空缓存→三文件消失、缓存None、返回True。；仅备份存在→备份被删且返回True；仅marker存在→marker被删且返回True。；主key删除拒绝、备份/marker可删→继续处理其余两者、主key保留、返回True、缓存None；全部拒绝→返回False。；定点变异遗漏备key、遗漏marker或删掉 cls._key=None→新增断言失败；不执行真实 secret purge。
- **回退/兼容**：只撤回测试新增的合成场景/fixture，不改变生产删除契约。退出测试时恢复所有路径常量和缓存；如测试曾误触真实路径应立即停止，不以创建新key或扩大删除列表补偿。此审读没有执行清理。
- **独立裁决**：[continuation-review-tests.json](continuation-review-tests.json)；成功例创建主备但mock remove且不核调用，marker未隔离，setup已使_key=None。生产True只是任一成功，不是全部删除；仅删主key或不清非空缓存的回归可通过现有三例。调用方“允许删除”的安全判定是另一合同，不能代替删除效果。当前生产遍历三路径且清缓存是反证，未访问/声称残留任何真实密钥。
- **裁决限缩/方案约束**：合成目录隔离主/备/marker三路径、非空缓存sentinel，检查实际三文件消失或精确remove路径集合及缓存None。逐个仅存在/部分PermissionError/全失败对照保留any-success合同，不能把True改成三者全成功来迁就测试。
- **完整原始取证**：[continuation-test-pack-24.json](continuation-test-pack-24.json#/findings/1)，原措辞仅在上述裁决边界内采用。

## 5. 待确认待验证及测试缺口

首轮详细记录（历史：接受根因仍有效，暂缓/升级以现行台账为准）

### D15-F07 · test_gap · P2 · mock 生命周期与真实 Flet 调度等价证据不足

位置：`component_renderer.FakeSession.schedule_update/schedule_effect`、`MockFletPage.run_task`、`test_component_renderer`、`test_mock_flet_contract`、`test_flet_test_page_probe`。renderer 使用真实 hook/订阅，但 update 仅入列表、patch_control 为 no-op、effect 同步执行；MockFletPage 明确关闭 coroutine。契约测试核对成员/签名/字段，不证明调度语义。真实 Flet probe 在 Windows/headless Linux 跳过，已读 probe 主要测控件加入/查找/轮询，不覆盖真实异步 effect 的晚到 cleanup。

这不是 mock 应复制整个框架的缺陷，也不是断言产品生命周期已错误。full-mount E2E 确有状态保持/语言传播行为断言，是重要反证，不能说“全仓库只跑 mock”；其仍不能替代真实桌面事件循环/取消/卸载证据。

最小补证：E5 对锁定 Flet 先产生真实 mount→状态通知→reconcile→VM 替换→unmount 的观测序列，断言订阅归属、cleanup 次数、卸载后迟到通知不再更新；保留当前 helper 测试的窄契约。复用现存 `tests\integration\test_flet_test_page_probe.py`、`tests\e2e\test_production_mount.py` 承接；不在当前主机解除 skip、安装 Xvfb 或启动浏览器。

### D15-F08 · test_gap · P3 · fake AI 的取消断言在协程首次运行之前就取消

位置：`tests\unit\services\test_news_insight_e2e_fake.py::test_delayed_cancel_propagates_cancelled_error` → `E2EFakeAIService.analyze_news_risk`。测试 create_task 后没有让出执行权，立即 cancel，再 await。当前默认非 eager Task 调度下，fake 尚未进入 sleep/方法体；即使方法体错误地吞运行中取消，该测试也可能仍通过。实现原生 sleep 无 catch，是反证，**不据此判产品吞 CancelledError**。

最小补证：注入可控 sleep/barrier；等待 fake 已进入延迟段再 cancel，断言 entered 标志、取消传播、无结果写回及任务 cancelled；保持 no-events 的 None 语义。未来合并运行第 8 章 M1，当前不改测试。

### D15-F09 · risk · P2 · 字体/CanvasKit/WASM 的来源、版本配套及许可尚未闭合

范围：`tests\e2e\mock_assets` 的 116 woff2、8 JS、8 WASM、6 symbols。目录枚举未见本地 LICENSE/NOTICE/来源 manifest；6 个 CanvasKit JS 只读取前缀，不能推断文件全体没有许可。未读取仓库全部许可资料，不能断言侵权或供应链漏洞。

已读契约仅注释要求匹配 Flet/engineRevision；实际资源拦截按 basename/子目录路由，未核 URL 中 revision 与本地内容。font helper 校验所需 basename 集合，sync 下载校验新响应大小/魔数，但现存非空文件跳过；均不建立版本/许可来源链。冻结 hash 只证明本地一致，**不是 provenance_checked**。

最小补证：由 D15 联同 D19，在经批准 E8/可信离线来源下登记 source URL、engineRevision/Flet-Web 版本、包/资源摘要、许可证及 NOTICE、下载/变换方式；核对 8 JS/8 WASM 配套与所有字体的上游许可。运行匹配环境下请求 revision 不符应明确失败而非静默替代；不把 116 文件存在当来源审核。当前联网/依赖来源未获批准，保持 blocked。

### 续审暂缓/驳回/重复裁决（不计确定问题）

- **TP02-F02 / rejected**：fi_02 确实直接调用替换后的 init_db，再由测试主动 stop；它不能证明编排补偿。但原 claim 的具体坏变体已有有效补偿：test_bootstrap.test_db_init_failed 真调 initialize_services，精确检查 success=False/error=db_init_failed；test_startup_controller.test_init_services_db_init_failed 真调 controller，精确检查 INIT_FAILED。把这两个产品分支改成功，并不会保持相关测试绿色。产品允许重试/重配，也未确立迁移失败必须立即 stop 的合同。不能仅凭一个集成用例名称，把已分层验证的状态边界再登记 P2 缺口。 见[continuation-review-tests.json](continuation-review-tests.json)。
- **TP02-F03 / rejected**：原文件多个测试确实只检查自己造的 mock/DataFrame，性能上界也接受零工作。但不能据此接受其提出的实际批取缺口：独立补读 TestCacheManagerPrefetchAuxiliaryData 已真调生产函数，检查非空各来源、两代码分发、7路同时在飞及消费结果、部分来源失败、缺 ts_code、as_of 入参。return {}、零调用、丢财报来源、去掉已测 as_of 等主要反例会被这些断言拒绝。原 claim 未在这些有效补偿之外证明新的具体错误；冗余自证用例的清理建议不计高风险 test_gap。 见[continuation-review-tests.json](continuation-review-tests.json)。
- **TP03-T02 / rejected**：fresh init_db 确实也运行 Alembic，旧 metadata.create_all 注释过时；两初始化路径相等仍是有效的 wrapper/direct 合同，不能仅因同源就判无效。更关键的主动反证：CI 在 upgrade head 后实际执行 alembic check，env.py 明确 target_metadata=Base.metadata 且 compare_type=True。原反例仅改ORM的JSONB为Text、不改迁移，会由此独立类型比较拒绝。两个指定测试本身未比较独立类型属实，但“缺独立类型oracle”的整体缺口不成立。 见[continuation-review-tests.json](continuation-review-tests.json)。
- **test-pack-12-F03 / deferred**：实际读取了两个完整测试及sanitize_error。展示中的Bearer/URL片段被替换为星号，但同一读取通道在其他源码的password赋值等处也有自动掩码，无法据展示文本证明磁盘测试输入本来就是掩码，或断言哨兵确实未进入异常。相邻URL/token/registered-secret测试有真实变量插入的可见证据。不能把读取通道脱敏当成仓库无效夹具的确定事实；未绕过掩码读取原值。 见[continuation-review-tests.json](continuation-review-tests.json)。
- **test-pack-15-T01 / deferred**：按当前fake，showA/showB/hideA确实pop了B，页面activeA而manager指针B；这项局部演算成立。但完整测试只承诺错配不清manager.active，生产_hide_dialog也只明确这个指针合同。应用实际只通过同一管理器显示一个受visible门控的close确认框，未核到第二个管理器dialog及迟到A回调的生产可达链。原方案自行选择“错配不pop”改变合同，不能仅据fake扩展场景认定必须实现。保留待确认，而非宣称产品叠框错误或必有高风险覆盖缺口。 见[continuation-review-tests.json](continuation-review-tests.json)。
- **TP20-T03 / deferred**：已读完整普通错误页测试和真实builder，生产确有二次sanitize。但工具展示的detail含星号，不能证明磁盘字面量本身预脱敏：同一通道会自动掩盖敏感模式，原报告也可能把显示掩码误当源码。未绕过掩码；因此无法证实删除普通错误页sanitizer仍满足针对哨兵的断言。不能因另一个升级builder有覆盖就直接认定此边界被补偿，也不能直接认定其夹具无效。 见[continuation-review-tests.json](continuation-review-tests.json)。
- **TP22-F01 / duplicate**：独立重读save/resolve及实际编辑器后，TP22给出的A unknown→resolve(A)挂起→B unknown→旧A committed与VMCONFIG-F03逐条件一致：同一个committed else未按still_owner隔离，保留B私有id却清公开恢复旗标。不是第二个缺陷，也不能把此测试包视为额外独立确认计数。 归并 `VMCONFIG-F03`。见[continuation-review-ui.json](continuation-review-ui.json)。
- **TP22-F02 / duplicate**：不是只凭相同标题去重：普通update_name不推进_draft_generation、保存捕获A、同代committed清dirty/关editor，且真实字段在saving期间可编辑，正是VMCONFIG-F02。同代字段编辑与start_add换代已区分，CAS不能保护UI后续B。测试包没有另外的回执或持久化根因。 归并 `VMCONFIG-F02`。见[continuation-review-ui.json](continuation-review-ui.json)。
- **TP22-F03 / duplicate**：已核真实异步budget reader、筛选事件和load末段：两份候选均是DAO后检查已过、reader成功迟到未复核generation而覆盖B，精确同于VMD-F01。reader异常有守卫但成功没有这一细节也相同；不是预算保存或月汇总代次的独立问题。 归并 `VMD-F01`。见[continuation-review-ui.json](continuation-review-ui.json)。

## 6. 改进建议及方案

不另计泛化建议；只推荐针对上述根因的最小实施顺序：F01/F02/F05 的假绿色出口 → F03/F06 的增量守护 → F04 制品生命周期 → F07/F08 的真实场景补证 → F09 来源与许可闭环。所有修改须另行授权、隔离实施、先写能失败的回归。

快照边界已核对：golden `--check` 不写 expected，`--update`/`--generate-inputs` 是显式互斥模式；测试在副本篡改 expected 并期望 DIFF，具有真实回归判别力，不报告为“无条件自证”。五种 picks 与 NAV/metrics/summary 是当期实现的冻结漂移对照，不证明收益公式/数据时点本身正确；业务独立 oracle 仍由 D06/D07/D08 承接。本轮没有更新任何基线。

## 7. 反证与去重记录

| 候选/既有项 | 裁决 |
| --- | --- |
| 全部单例测试未隔离 | 驳回泛化指控；完整读取到 unit 前后 reset、loop-local/模块状态补充隔离。实际顺序/xdist 证据未跑，仍不能判全库 R7 通过 |
| runner 吞 pytest 返回码/无 JUnit 配置 | 驳回；subprocess.call 返回码透传，runner/CI 都有 junitxml；实际 JUnit 文件尚不存在于本次验证 |
| 只有 mock、没有生产挂载验收 | 驳回；full-mount 场景池及具体输入保持/语言切换断言确实存在；保留 F07 的窄差异 |
| 所有快照只是现场生成 expected 后比较 | 驳回；check 和 update 分离，篡改和容差测试存在；未读 `.ambr` 不作全库结论 |
| 所有取消都被测试覆盖 | 不成立；F08 的 Task 首次进入缺口；R2 其他链路未读，不外推 |
| sidecar 缺失 skip 即产品故障 | 不计缺陷；明确环境缺失应单列，而非等同 canary 已有路径回归 |
| Windows helper 能取消所有 skip | 不成立；它仅移除 item.own_markers 的 skipif，不影响运行期 skip、继承来源或未参与 collection 的场景；没有在本轮更改它 |
| D03-F04 / D03-T04 学习样本断言缺口 | 引用 `D03-database.md`，不新计；本轮不重复主读 DAO/该业务测试 |
| D03-F05 / D03-T05 同 run 重放缺口 | 引用既有发现；“不重置”载荷断言不能证明冲突保持，仍按 D03 原方案补验证，不新计 |
| 既有技术债/例外 | 已按相关关键字查 `known-technical-debt.md` / exceptions；真实 Flet probe 自身承认平台限制。检索不是两文档全读，不声称穷尽历史记录 |
| E2E seed SQL 字符串 | 读取到列常量、值参数绑定及计数查询；形式红线符合性留 D21/D22 交叉核对，不把常量标识符拼接直接宣称为可利用注入 |

### 续审去重与盲核

续审原主张的接受/驳回/暂缓/同根因映射见[continuation-adjudication.json](continuation-adjudication.json)。两份有真实产物的限定盲核与原先无输出的大范围尝试分别记录，不追认旧尝试成功；盲核overall仍indeterminate，不代表全域通过。

## 8. 验证证据

当前[统一制品验证](all-validation.json)只核验本地材料、读段、源哈希和统计；所有项目pytest/collect、驱动/迁移、SDK请求数、模型/并发故障、真实Flet/读屏、构建/安装仍not_run。历史静态反例和首轮检查保留在下方，不因续审范围增加变成项目运行证据。

首轮验证记录（历史）

### 已运行（仅标准库静态取证）

1. CSV 筛选 D15，741 路径逐一核对冻结内容 hash，差异 0；确认 577 Python / 327,601 LF 行。本代理统计/生成片段曾出现 raw string 尾反斜杠、unicodeescape、标题正则过度贪婪三次错误，修正后成功；不是仓库失败。
2. 577 Python AST 可解析；只用于调用点发现与定位，不标记语义已读。literal skip/skipif/importorskip 调用点为 18/7/4，literal xfail 调用点为 0。没有进行 pytest collection，真实结果未知。
3. baseline 元数据：758 occurrence、461 唯一规范化签名、297 重复 occurrence；类型为 weak_mock 358、weak_raises_only 258、weak_is_not_none_ratio 134、weak_called_flag 8。只用于 F03 关系分析，不等于逐条测试质量判断。
4. 独立标准库 set/Counter 反例：baseline=1/current=2，set 未见项 0、Counter 超额项 1。独立 AST 例：同步空测是 FunctionDef，异步空测是 AsyncFunctionDef；仅前者命中单类型判断。未 eval/exec 抽取代码。
5. golden manifest 四个输入的摘要按既有文本 LF 归一化规则匹配；三个 Parquet 仅哈希，不导入 pandas/项目，不把内容标已读。
6. 最终产物 JSON 解析、741 主责一一对应、range 合法性、状态/分类计数及九章一致性由标准库校验；结果记录于 evidence 的 `artifact_validation`。没有产生业务日志、DB、浏览器或测试制品。

### 未运行（全部 not_run，不提供伪造退出码）

以下是**未来隔离验证计划，不是本轮执行记录或批准**：

- **M1 / E2**：`python -m pytest -n 0 tests\unit\test_check_diff_coverage.py tests\unit\test_scan_weak_assertions.py tests\unit\test_check_staged_weak_assertions.py tests\unit\test_detect_flaky.py tests\unit\test_run_e2e_local.py tests\unit\ui\test_component_renderer.py tests\unit\services\test_news_insight_e2e_fake.py -v --tb=short`。先补上述精确反例，再验证旧保护不退化；真实插件/收集/skip/xfail 全量留证。
- **M2 / E2+真实 Git**：0.2 C11/C12/C13，冻结 baseline 只读；严格模式缺输入不得变成功，baseline 新 occurrence 必须阻断。无 Git 的当前快照不能出具历史增量通过。
- **M3 / E5**：`python run_e2e_local.py tests\e2e\test_smoke.py tests\e2e\test_production_mount.py`。匹配可信 sidecar/Flet-Web/资源，故障注入必须在可销毁隔离副本；检查 JUnit 的真实执行/skip、trace ZIP、截图、归档成员、摘要、合成哨兵脱敏及具体 PID 回收。不能靠空制品目录或 upload 配置判通过。
- **M4 / E2/E3/E5**：污染测试的显式串行多顺序与 CI 默认 xdist 结果分别登记；集成 fixture 的 DROP/CREATE、真实 sidecar/PG 均需独立准入/批准。当前不跑 `test_pollution_detection`（会嵌套 subprocess pytest）。
- **M5 / E8 或可信离线材料**：F09 的版本/许可来源核对，当前不联网、不下载资源。

当前阻塞：依赖与基线版本不齐、无确认强隔离、真实 Git 历史不可用、E5 制品/浏览器/sidecar 证据不存在；这些是验证缺口，不是自动新增产品缺陷。

## 9. 残余风险与下一步

当前业务/测试文本审读已经补齐；剩余原范围155项均有明确性质：受保护模板1、本地图像来源13、第三方字体/运行资源来源138、Parquet内容/来源3。全部已尝试可安全的本地核验；还需可信目标制品/来源许可/授权解析器或维护者证明，不安装、联网或读取秘密去绕过边界。

关键动态与范围增量合同未闭合，G仍未满足；本次只是完成可安全的静态续审，不是要求立即按方案修业务。后续具体条件见[剩余队列](remaining-coverage.csv)与[验证准入矩阵](validation-matrix.md)。

首轮后续工作（历史，已读条目不再作为当前未读阻塞）

- **684 未读 + 8 部分读取**：下一轮从 evidence 的 pending/reading 恢复，优先实际业务候选及其完整断言、未读 UI runtime/bridge、全部弱断言条目、snapshot/fixtures 和资源来源。不能以本轮 AST 覆盖替代。
- `.ambr` 全文、三个 Parquet 语义、其余第三方 JS/WASM/字体/符号来源与许可未闭合；未读业务测试可能推翻或补偿个别窄测试缺口，不能宣称全库安全。
- 49 文件静态全读不是 G 完成；既缺全部主责覆盖，也缺运行/失败制品和独立复核。`review.status=pending`；本任务未嵌套代理，后续由父任务安排独立复核。
- R5/R17：未新增 DAO/表列，无代码变更；R21：未新增填充值，fake 的成功/no-event 语义按测试边界核对，不给生产数据可信度通过结论；R7：隔离设计存在但全库行为未验；R18：无 Git 快照声明，逐文件写入仅本报告与 evidence。

| CHECK | 状态 |
| --- | --- |
| 01/03/08/11/13/14/15 | 范围、边界、Profile、分类、未运行项和剩余范围已明确；未越权 |
| 02 | 冻结清单已核对；无 Git，历史 diff/删除重命名无法判断 |
| 04/05/06/07/09 | 仅所读关键链完成建模/反证；不是全主责完成 |
| 10/12 | 已列发现均给入口、影响、反证和针对性方案；D03 同根因不重复计数 |

**交付的是可续审的部分静态证据，不是任务完成、发布批准或修复授权。**