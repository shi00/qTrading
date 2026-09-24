# review08 — AKShare 开源组件复用专项检视（结论登记）

> **来源报告**：`reviews/akshare检视.md`（本地 gitignored 正文，检视日期 2026-09-23；结论均基于 akshare 1.18.97 源码/签名实测，未凭记忆断言）
> **登记规则**：见 [README.md](./README.md)（GOV-04）。发现 `id` 沿用报告内短名（A1~D3），不带轮次前缀（避免与 [governance-ids.md](../../governance/governance-ids.md) 已登记 ID 混淆）。
> **修复批次**：B1/B2/B3/B4/C2/D1/D2/D3 的修复与复核记录见本地计划文件 `Plans.md`（AKShare 检视报告修复计划，gitignored 不入库）；复核产出的低 severity 完善项与「登记不改」项见文末「复核跟进（2026-09-24）」。

## 发现登记（13 项）

| id | ruleId | severity | location | summary | disposition | 可验证判据 |
|----|--------|----------|----------|---------|-------------|-----------|
| A1 | - | P3 | `data/external/news_fetcher.py` | CLS 财联社电报由项目手写 httpx 客户端（≈140 行）而非 akshare `stock_info_global_cls`：async 可取消的知情取舍，需固化决策记录 | resolved（PR #1185） | 模块 docstring 写明「CLS/新浪美股走 httpx 而非 akshare（akshare 同步调用不可取消）；接口漂移由本项目自维护」 |
| A2 | - | P3 | `data/external/news_fetcher.py` | 新浪美股行情手工 JSONP 切片解析（找括号），对响应体含括号/截断/HTML 错误页无保护，仅靠 `json.JSONDecodeError` 兜底 | waived（报告判定「当前不动」，P3） | 无修复动作即视为按决策关闭；若引入统一 JSONP 解析或 akshare 提供异步接口时重开 |
| A3 | - | P2 | `data/external/news_fetcher.py`（`get_hot_concepts`） | 概念热度（新浪）与概念成分同步（东财）双数据源并存：两套失败计数器、两条限速策略互不一致 | waived（2026-09-24 两轮实测后维持现状） | 维持新浪 HTTPS 直连（PR #1174），且实测证据登记在案：东财 14 次 avg 16.6s / max 41.55s、2 次 RemoteDisconnected；新浪 6/6 成功 avg 0.25s（切换将显著退化 AI 取数质量） |
| B1 | - | P0 | `data/external/akshare_concept_client.py` | akshare `stock_board_concept_name_em` 内部 `lru_cache` 无 TTL，同进程第二次起不发请求 → 概念板块列表静默冻结，新增板块永不同步 | resolved（PR #1171；复核完善 PR #1188） | `get_concept_list` 每次取数前清理 akshare 侧缓存（`_clear_concept_list_cache`），且有单测断言数据源变化后连续两次取数拿到新数据 |
| B2 | - | P2 | `data/external/news_fetcher.py` | akshare `stock_sector_spot` 内部走明文 HTTP 且无 timeout，与项目 SEC-006（HTTPS 防 MITM）冲突，链路上无真正生效的超时保护 | resolved（PR #1174；复核补丁见「复核跟进」） | `get_hot_concepts` 不再经 akshare `stock_sector_spot`，改 httpx 直连 HTTPS 新浪端点 + 显式 timeout |
| B3 | - | P0 | `data/external/news_fetcher.py` | 依赖 akshare 私有模块 + `__defaults__[1]` 位置索引取 market 默认值（四层内部细节耦合），任何变动静默降级；两处重复 | resolved（PR #1169；注释/断言完善见「复核跟进」） | 全库无 `__defaults__[1]` 动态解析、无以取参为目的的 `akshare.stock_feature.stock_disclosure_cninfo` 私有导入；直接传 `market="沪深京"` |
| B4 | - | P1 | `data/external/news_fetcher.py`、`data/external/akshare_rate_limiter.py` | NewsFetcher 的 3 个 akshare 调用无限速（`stock_news_em` 按个股高频调用最易触发封禁），与 AkshareConceptClient 限速待遇不一致 | resolved（PR #1177；复核缺陷 PR #1189） | NewsFetcher 与 AkshareConceptClient 共用同一模块级 TokenBucket（`get_akshare_rate_limiter`），无未接限速的 akshare 出站调用 |
| B5 | - | P3 | `data/external/news_fetcher.py` | 顶层 `import akshare as ak` 使 `data/external/__init__.py` 惰性导入的收益被抵消（触达新闻即加载 akshare 全依赖树） | resolved（PR #1190） | 顶层无 `import akshare`；子进程断言「import 模块后 akshare 不在 `sys.modules`」通过（执行期实测排除 PEP 562 模块 `__getattr__` 方案：不服务于模块内全局名查找，会退化为 NameError 静默降级） |
| C1 | - | P1 | （未落代码；拟落点 sync strategy 层，待 ADR 定夺） | Tushare 积分不足时直接 skip 功能，akshare 免费同类接口（涨跌停池/龙虎榜/两融/北向/个股资金流）已装未用——最大未兑现价值；报告已深化为「补足而非替换」并给出字段映射 | open | 已出 ADR 并完成试点（权限降级 → 切源 → 来源标注两侧可区分）；前置：F9（review06）关闭 + akshare 候选接口联网可用性验证 + D3 结论（原 limit_list 试点已因 D3 撤回） |
| C2 | - | P3 | `data/domain_services/offline_calendar.py` | `_OFFLINE_TRUSTED_UNTIL` 为人工每年更新的可信区间常量，忘记更新则超出区间 `is_trading_day` 静默返回 `None` | resolved（PR #1173；日期源修正 PR #1188） | `tests/unit/test_offline_calendar.py` 存在「可信区间不足 6 个月」失败告警断言（防忘更新） |
| D1 | - | P2 | `data/external/news_fetcher.py` | `get_stock_news` 与 `get_stock_news_documents` 约 100 行重复，且同一源数据两种时间口径（字符串 vs UTC naive，下游混用差 8 小时） | resolved（PR #1175；复核回归 PR #1186） | 两公开方法共用 `_fetch_stock_news_core` 内核；`publish_time` 统一 UTC naive，展示侧 `_news_date_label` 经 `from_utc_to_cst` 还原 CST |
| D2 | R21（精神） | P1 | `data/sync/concept_sync.py` | `_to_ts_code` 按代码前缀猜交易所后缀、其余一律 fallback `.SZ` → 静默产生错误 `ts_code` 污染概念关联表 | resolved（PR #1170 + #1176；策略级用例 PR #1188） | `_to_ts_code` 改查 stocks 表（`get_ts_code_map`），查不到则跳过并计入 `result.warnings`，不再前缀猜测 |
| D3 | - | P0 | `data/sync/concept_sync.py`、`data/persistence/daos/stock_dao.py` | LIMIT_ 概念管线把股票名当概念名写入（`concept_id = LIMIT_{ts_code}` 自指概念），且经无前缀过滤的 `get_concepts` 泄漏进所有概念消费方 | resolved（PR #1172；文案/断言 PR #1188） | LIMIT_ 已停写 + 存量清理；`get_concepts` 带 `concept_id NOT LIKE 'LIMIT_%'` 过滤；`clear_all_limit_concepts` 已重命名（原 `clear_today_limit_concepts` 名实不符） |

> severity 复述报告原值（D2 由报告从 P3 上调为 P1，因其为 C1 的前置阻塞项）；`ruleId` 仅填报告显式引用的项目规则，其余为 `-`（报告未标注规则 ID）。
> 报告另有「边界（未覆盖项）」：未做联网实测、未评估各候选接口数据质量/历史深度、C1 字段映射未验证取值语义与单位换算——均属报告自述范围限制，随 C1 实施时补齐。

## 复核跟进（2026-09-24）

> 对已修复项执行两轮复核检视（项目宪法检视协议 + 对抗性检视），产出如下；复核确认的真实缺陷沿用分支 A 七步流程闭环修复。

| 复核项 | 复核结论 | 处置 |
|--------|---------|------|
| B1 docstring「single cache slot」表述不精确（实为 `maxsize=128` 零参数单键） | 低（表述） | 已修（PR #1188） |
| B1 缓存清理降级分支缺 WARNING 断言 | 低（测试缺口） | 已补（PR #1188） |
| B2 proxy 配置与 NO_PROXY 意图冲突：httpx 0.28 显式 `proxy` 时忽略 NO_PROXY 域，国内域名（新浪/CLS）被走代理隧道，与 ProxyManager「国内直连/国际走代理」定位及「直连域名配置」用户设置相悖；三处 httpx 调用点口径不一 | 真实缺陷（低-中） | 已修（PR #1191） |
| B2 TimeoutError/Exception 两分支日志升级分支内容无区分（同文案） | 低（可观测性） | 已修（PR #1191） |
| B3 注释「仅支持固定 market 值」措辞夸大 + `market="沪深京"` 缺测试断言 | 低（表述/测试缺口） | 已修（PR #1191） |
| B4-FIND-03 限速令牌在 `_pd_options_lock` 临界区内 sleep，并发批量抓取（ai_mixin semaphore 并发）放大锁等待 → 10s 超时静默降级 | 真实缺陷（中） | 已修（PR #1189） |
| B4-FIND-04 `reset_akshare_rate_limiter` 不同步已初始化客户端实例桶 | 低（测试脆弱性） | 已修（PR #1188） |
| C2 测试日期源用 `date.today()`（系统时区），与项目「测试日期源须与生产同源」约定不一致 | 低 | 已修（PR #1188） |
| D1（三项）`_news_date_label` 未还原 CST / EM 时间主键列名不存在 / `day_only` 缺护栏 | 真实缺陷（中+低） | 已修（PR #1186） |
| D2 缺策略级 FAILED 传播用例 | 低（测试缺口） | 已补（PR #1188） |
| D3 失败文案与实现不符、分块分支缺 SQL/参数断言 | 低（文案/测试缺口） | 已修（PR #1188） |

**登记不改**（依据检视协议「测试缺口/非阻断建议不混入阻断发现」，记录在案、不单独立项）：

- D2：空 `code_map` 时策略 status 仍 SUCCESS（仅 warning）——独立告警设计待评估；
- D3：`LIKE 'LIMIT_%'` 的 `_` 通配符语义为既存模式（同 `EM_%`/`AI_LLM_%`），当前无实际误伤；
- C2：新增断言与既有 `test_trusted_until_is_in_future` 语义部分重叠（保留双保险）。