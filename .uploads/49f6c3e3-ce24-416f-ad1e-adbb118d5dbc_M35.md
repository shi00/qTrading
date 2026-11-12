# 代码检视报告｜M35｜数据浏览与数据源配置

本模块记录 **5 个问题**：P1=0、P2=2、P3=3。
以下列出问题、具体修改和验证方法；建议均未实施，仓库测试未运行。

## 问题与修复方案

### 1. P2｜健康检查未保存任务 ID，取消排队任务后状态不恢复

- **问题编号**：`PY-ui.viewmodels.data_source_view_model / DS-01`
- **代码位置**：[ui\viewmodels\data_source_view_model.py](../../ui/viewmodels/data_source_view_model.py)；[check_health / handle_task_update / dispose](../../ui/viewmodels/data_source_view_model.py)。
- **哪里有问题**：check_health置health_checking=True并提交cancellable任务，却不保存返回的health task_id；只有factory内finally清标志。真实TaskManager排队取消会跳过factory，状态通知只按_active_task_ids匹配；该映射只记录日更/清缓存/初始化。health-only时handle_task_update首个条件就return，故health_checking永久True，后续检查被重入守卫拒绝；dispose也不能定位该health任务取消。
- **造成什么影响**：从任务中心取消排队检查后健康按钮锁死。
- **原因**：已有重入守卫不覆盖factory未开始的生命周期，健康任务没有独立身份追踪。

**具体怎么改**

本模块单独跟踪health task_id、消费对应终态并在dispose清理；不要把health加入会释放同步busy的同一分支。接口影响：保持health与sync状态独立、TaskManager终态SSOT/R2；边界验证：排队取消/执行取消/提交拒绝、与同步并存、旧检查晚到均只清自身标志。公共接口不变。
  在本VM添加独立字段，并把终态处理放在现有 `handle_task_update` 早退之前：

```
# __init__ 中
self._health_task_id: str | None = None

# check_health 中，现有 submit_task 返回之后
self._health_task_id = task_id
if task_id is None:
    self._set_state(health_checking=False)

# handle_task_update 中，现有同步任务早退之前
health = next((t for t in current_tasks if t.id == self._health_task_id), None)
if health is not None and health.status in (
    TaskStatus.COMPLETED, TaskStatus.FAILED,
    TaskStatus.CANCELLED, TaskStatus.INTERRUPTED,
):
    self._health_task_id = None
    self._set_state(health_checking=False)
```

  factory的健康结果/错误只在 `self._health_task_id == task_id` 时提交，finally也只清自己的身份；原取消分支继续raise。dispose在退订/基类清理前增加：

```
if self._health_task_id is not None:
    self._tm.cancel_task(self._health_task_id)
    self._health_task_id = None
```

  所用TaskStatus/cancel_task已有实读证据；新字段只在本VM定义，不借用其他模块helper。健康终态不调用_set_sync_busy，不误释放同步任务。

**如何验证**

Given执行槽占满且health排队，When取消该task，Thenfactory未运行但health_checkingFalse、可重试、同步busy不改变；回归已启动检查取消。

### 2. P2｜数据页隐藏时消耗重置通知，重新显示后仍用旧数据

- **问题编号**：`PY-ui.views.data_view / UIV-DATA-01`
- **代码位置**：[ui\views\data_view.py](../../ui/views/data_view.py)；[`DataExplorerView._on_cache_seq_change`](../../ui/views/data_view.py)。
- **哪里有问题**：数据页常驻但 inactive 时收到重置通知，effect 先推进 last_seq，却不调用 `mark_tables_stale`；回到 active，seq 相等而早退。
- **造成什么影响**：通知永久丢弃，tables_loaded 仍 True，表目录不会走强制初始化；切回后查询不等于表目录重新初始化。
- **原因**：混淆“已接收”与“已处理”的序号。已读测试只覆盖 active 通知/同 seq 幂等。

**具体怎么改**

本文件在 active=False 时不消耗 last_seq；active=True 时先执行现有 `vm.mark_tables_stale()` 再确认 seq。**规范轮**：复用 Observable，不新建业务缓存、不改 VM 接口；**对抗轮**：隐藏期间多个通知合并为一次失效，重激活/重复渲染不重放，初次 mount 无通知不误失效。

  本模块原局部函数可替换为：

```
def _on_cache_seq_change() -> None:
    if cache_state.seq == last_seq.current or not active:
        return
    vm.mark_tables_stale()
    last_seq.current = cache_state.seq
    logger.debug("[DataExplorerView] Cache cleared - tables marked stale")

ft.use_effect(_on_cache_seq_change, dependencies=[cache_state.seq, active])
```

  兼容性：保留已有 signal、effect 依赖与 VM 命令接口，仅把确认时点从“收到”改为“已失效”。

**如何验证**

前置条件：active=False、seq 增加，操作：active=True，预期结果：恰一次 mark_tables_stale 且初始化可触发；未运行。

### 3. P3｜错误信息的参数映射可被修改，破坏状态快照

- **问题编号**：`PY-ui.viewmodels.data_explorer_view_model / DE-01`
- **代码位置**：[ui\viewmodels\data_explorer_view_model.py](../../ui/viewmodels/data_explorer_view_model.py)；[SqlErrorInfo / execute_sql](../../ui/viewmodels/data_explorer_view_model.py)。
- **哪里有问题**：frozen SqlErrorInfo.format_args虽标Mapping，default_factory为dict；execute_sql把普通文本错误装配为SqlErrorInfo(key, {"default": raw_err})，直接把可变dict放进state。读取state.sql_error.format_args后可原地修改旧快照，不经过_set_state，违反 [MVVM不可变快照](../../docs/patterns/mvvm.md)。已读SQL错误测试真实覆盖该装配路径，但未检查递归冻结。
- **造成什么影响**：错误快照可被外部别名无通知更改。
- **原因**：frozen只冻结属性引用，没有冻结内容。

**具体怎么改**

仅本模块SqlErrorInfo构造时复制并只读包装format_args，默认也只读；保留Mapping输入与**展开兼容。接口影响：raw_detail脱敏/Message契约不变；边界验证：原dict随后修改、空dict、旧快照与多订阅者读取均稳定。不得改变正常SQL/合法空结果返回。
  在本文件增加stdlib导入，并将该方法加入现有 `SqlErrorInfo` 类：

```
from types import MappingProxyType

def __post_init__(self) -> None:
    object.__setattr__(
        self, "format_args", MappingProxyType(dict(self.format_args))
    )
```

  字段契约为 `Mapping[str, str]`，子值不可变，复制外层即可；不依赖其他模块冻结helper，构造签名及View的 `**format_args` 不变。

**如何验证**

Given文本错误及调用方dict，When构造state并修改输入dict或state映射，Then输入修改不影响旧state，state直接修改被拒，View仍能格式化错误。

### 4. P3｜提示文本和状态颜色被缓存，切换语言或主题后不更新

- **问题编号**：`PY-ui.views.data_view / UIV-DATA-02`
- **代码位置**：[ui\views\data_view.py](../../ui/views/data_view.py)；[`SQLConsoleTab`](../../ui/views/data_view.py) 的 status_text/status_color 与 `_run_query`。
- **哪里有问题**：初始“就绪”和执行结果文字被翻译后存入 use_state，订阅 locale 后重渲染不重新初始化这些状态；保存的 Hex 状态色同样不随主题更新。
- **造成什么影响**：切英文仍显示中文旧状态，状态前景与新主题不一致。
- **原因**：把可从消息/语义色派生的呈现存成快照。

**具体怎么改**

本模块保存 locale 无关的消息 key/参数及颜色语义名，渲染时解析；真实 raw_detail 仍脱敏直显。**规范轮**：View 负责翻译、不让 VM 感知 locale、不创建业务结果双轨；**对抗轮**：ready/成功空结果/截断/失败/取消后的各态切语言与主题均保持内容事实、草稿不丢。

  关键片段：本文件增加已有共享 Message 类型导入；替换初始化、执行中/成功分支和底部 Text。其余失败/截断分支同样保存原 key/参数，而非翻译结果。

```
from core.i18n import Message

status_message, set_status_message = ft.use_state(Message("data_sql_ready"))
status_color_key, set_status_color_key = ft.use_state("TEXT_SECONDARY")

# _run_query 的执行中与已有非空成功分支
set_status_message(Message("data_status_executing"))
set_status_color_key("INFO")
# 使用该分支已有的 elapsed / row_count
set_status_message(Message("data_sql_success", {"time": elapsed, "rows": row_count}))
set_status_color_key("SUCCESS")

# 原状态栏 Text 的替代
ft.Text(
    I18n.get(status_message.key, **status_message.params),
    size=AppStyles.FONT_SIZE_BODY_SM,
    color=getattr(AppColors, status_color_key),
)
```

  错误分支对应关键赋值：

```
if err is not None:
    set_status_message(Message(err.message_key, err.format_args))
    set_detail_text(err.raw_detail or "")
else:
    set_status_message(Message("data_sql_error"))
set_status_color_key("ERROR")
```

  兼容性：SQLConsoleTab 的 props、execute_sql 与结果结构不变；Message 已存在，代码展示的是分支替换，不是让成功赋值紧接执行中赋值无条件执行。空成功、截断和异常分支须按其原 key/参数一并替换。

**如何验证**

前置条件：中文完成查询，操作：切英文/浅色而不再查询，预期结果：状态文字与配色更新、SQL 与结果不变；未运行。

### 5. P3｜已加载健康结果后切主题，图标/状态颜色仍使用导入时 AppColors 值

- **问题编号**：`PY-ui.views.settings_tabs.data_source_tab / UIV-DS-01`
- **代码位置**：[ui\views\settings_tabs\data_source_tab.py](../../ui/views/settings_tabs/data_source_tab.py)；[`_HEALTH_STATUS_VISUALS` / `_build_health_dashboard`](../../ui/views/settings_tabs/data_source_tab.py)。
- **哪里有问题**：已加载健康结果后切主题，图标/状态颜色仍使用导入时 AppColors 值。
- **造成什么影响**：图标/状态文字与同卡实时主题背景不一致。
- **原因**：映射保存 Hex 而非语义属性名；根组件主题订阅只能重新执行渲染，不能改固定字典。已读 [AppColors.load_theme](../../ui/theme.py) 更新类属性而不更新消费方字典；现有 [主题测试](../../tests/unit/ui/test_theme_dynamic_colors.py) 没有包含此映射。

**具体怎么改**

仅本文件映射保存 SUCCESS/WARNING/ERROR 等语义属性名，取用时动态解析；不改 health_key 与健康判断。**规范轮**：R23 语义 token、声明式主题；**对抗轮**：四主题×checking/error/red/yellow/green、旧结果保留时均配对，不把未知状态改为正常。

  替换本文件映射并定义本模块内部解析函数：

```
_HEALTH_STATUS_VISUALS: dict[str, tuple[ft.IconData, str]] = {
    "ds_health_ok": (ft.Icons.CHECK_CIRCLE, "SUCCESS"),
    "ds_health_lag": (ft.Icons.WARNING, "WARNING"),
    "ds_health_error": (ft.Icons.ERROR, "ERROR"),
    "ds_health_cancelled": (ft.Icons.CANCEL_OUTLINED, "WARNING"),
    "common_check_fail": (ft.Icons.ERROR, "ERROR"),
}

def _resolve_health_visuals(key: str) -> tuple[ft.IconData, str]:
    icon, color_name = _HEALTH_STATUS_VISUALS.get(
        key, (ft.Icons.HEALTH_AND_SAFETY, "WARNING")
    )
    return icon, getattr(AppColors, color_name)
```

  本文件 `_build_health_dashboard` 的 health_error 与 health_result 两处原 `.get(...)` 均替换为：

```
metric_health_icon, metric_health_color = _resolve_health_visuals(health_key)
```

  兼容性：新 helper 的定义与消费均位于所属文件；沿用原 IconData、icon 和 AppColors 属性，不改健康状态判断、VM/API 或页面签名。

**如何验证**

前置条件：模块在主题 A 导入，操作：切主题 B 渲染既有 health_result，预期结果：状态图标/文字使用 B 的值；未运行。

## 检查范围与尚未核实的事项

所有源码文件的原检查记录保留如下。测试只按注明的范围读取，没有运行 pytest、构建、迁移或应用。
尚未核实的观察不计入上述问题数；没有经过验证的条件，不用“可能有问题”代替确定缺陷。

| 源码 | 原检视对象 | 已记录问题数 |
| --- | --- | --- |
| [data\persistence\data_explorer_query_client.py](../../data/persistence/data_explorer_query_client.py) | `PY-data.persistence.data_explorer_query_client` | 0 |
| [ui\components\config_panels\tushare_config_panel.py](../../ui/components/config_panels/tushare_config_panel.py) | `PY-ui.components.config_panels.tushare_config_panel` | 0 |
| [ui\viewmodels\data_explorer_view_model.py](../../ui/viewmodels/data_explorer_view_model.py) | `PY-ui.viewmodels.data_explorer_view_model` | 1 |
| [ui\viewmodels\data_source_view_model.py](../../ui/viewmodels/data_source_view_model.py) | `PY-ui.viewmodels.data_source_view_model` | 1 |
| [ui\viewmodels\tushare_config_panel_view_model.py](../../ui/viewmodels/tushare_config_panel_view_model.py) | `PY-ui.viewmodels.tushare_config_panel_view_model` | 0 |
| [ui\views\data_view.py](../../ui/views/data_view.py) | `PY-ui.views.data_view` | 2 |
| [ui\views\settings_tabs\data_source_tab.py](../../ui/views/settings_tabs/data_source_tab.py) | `PY-ui.views.settings_tabs.data_source_tab` | 1 |
| [ui\views\settings_tabs\tier_api_panel.py](../../ui/views/settings_tabs/tier_api_panel.py) | `PY-ui.views.settings_tabs.tier_api_panel` | 0 |

展开逐文件测试读取范围与未核实观察

### data\persistence\data_explorer_query_client.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - test_data_explorer_query_client.py实读1–112、606–731；根fixture1–91、单元fixture1–129实读；其他VM/生命周期/集成候选未全文读，未执行库/DB/pytest。
- **未核实观察（不是确定问题）**：
  - `DataExplorerQueryClient.query_table/get_table_schema/_apply_filters`：schema失败空集合时是否有VM前置校验保证过滤有效，未知。
  - `DataExplorerQueryClient.close_all/_ensure_engine`：关闭/模式切换与worker查询交错的调度保护未完整核实。
  - `DataExplorerQueryClient.execute_sql`：fetchmany限制不证明driver全结果缓冲/单行宽度界限，真实DB资源语义未执行。

### ui\components\config_panels\tushare_config_panel.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - 直接测试1–120/471–674/762–949；其余未读；旧契约1–186全读、主题1–193、VM106–157实读；flags交叉组合和旧请求竞态未验证；全部未运行。
- **未核实观察（不是确定问题）**：
  - `ui\components\config_panels\tushare_config_panel.py:TushareConfigPanel compact/standard branches`：compact忽略show_save_button、standard忽略show_register_link；文档未限定组合，宿主约定未读，先澄清契约。
  - `ui\components\config_panels\tushare_config_panel.py:TushareConfigPanel token/Enter`：验证时Token可改和Enter可调度；VM对旧验证结果覆盖新Token的保护未核验。

### ui\viewmodels\data_explorer_view_model.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - VM测试1–202、439–490、864–929、995–1306；DataView测试1859–1888、2017–2054行实读，其余未读。
  - 未执行SQL/仓库代码/测试；确定缺陷已补独立8字段及本文件构造冻结片段。
- **未核实观察（不是确定问题）**：
  - `TableRow / SqlResultRow`：需核实真实QueryClient是否输出可变JSON单元格，不能从tuple[Any]直接断言查询污染。
  - `DataExplorerViewModel.query_data / execute_sql`：同表条件/SQL结果是否与新输入错配取决于View禁用和请求调度；换表代次保护已经存在。

### ui\viewmodels\data_source_view_model.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - VM测试1–182、302–365、850–959、1437–1481；Tab契约494–553；集成249行/locale123行/E2E展示72行全文实读，其余未读。
  - 未执行仓库代码/测试；确定缺陷已补独立8字段及本VM健康任务身份/终态/清理关键片段。
- **未核实观察（不是确定问题）**：
  - `DataSourceViewModel.save_tushare_token`：普通FAILED后仍应用会话Token；失败但允许会话应用的授权契约未核实。
  - `DataSourceViewModel._emit_snack`：Flet连续状态更新是否合并导致前置关键消息丢失未运行核验，seq不能证明逐条消费。

### ui\viewmodels\tushare_config_panel_view_model.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - 源码全文322行；VM测试读取1–74、270–444、590–735，面板测试800–893，单例缓存测试全文；余下测试段未读。
  - 未执行仓库代码、pytest、SDK、keyring、网络或数据库；测试线程池为passthrough，不能证明真实线程/超时语义。
- **未核实观察（不是确定问题）**：
  - `ui\viewmodels\tushare_config_panel_view_model.py::verify_token/update_token`：探活或保存挂起期间草稿被编辑，旧请求完成反馈是否应标记当前草稿尚未验证。
  - `ui\viewmodels\tushare_config_panel_view_model.py::verify_token`：锁定SDK初始化、超时覆盖和线程池取消后的工作是否有阻塞或残留。

### ui\views\data_view.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - 源码全文1354行；关联测试读取381–496、1830–1926、2060–2137，余下段未读。
  - 缺inactive通知重激活和SQL状态热切换反例；未执行查询、导出或pytest。两项确定缺陷已补八独立字段、Python关键片段、双轮校验和接口说明。
- **未核实观察（不是确定问题）**：
  - `ui\views\data_view.py::SQLConsoleTab/TableViewerTab`：锁定CodeEditor事件、FilePicker、Tabs及pandas单元格语义未运行核实，SQL只读保证依赖下层事务/校验。

### ui\views\settings_tabs\data_source_tab.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - 源码全文1383行；组件测试571–682、2001–2081、2675–2811，质量等级三态测试全文；其他段未读。
  - 未执行同步/重置/pytest/数据库；确定缺陷已补八字段和本模块Python映射/解析片段。
- **未核实观察（不是确定问题）**：
  - `ui\views\settings_tabs\data_source_tab.py::DataSourceTab/_build_health_dashboard`：真实健康字段/等级、首次通知含义、同步互斥/重置服务保护与独立量化复核未完成。

### ui\views\settings_tabs\tier_api_panel.py

- **源码**：原报告记录完整读取。
- **测试读取限制**：
  - 源码全文447行；测试101–201、642–760、805–871，余下段未读。
  - resize测试prev=None；未运行框架/probe/pytest，无法证明async原处理器或多订阅清理。
- **未核实观察（不是确定问题）**：
  - `ui\views\settings_tabs\tier_api_panel.py::_setup_resize/_cleanup_resize`：原handler为async或后挂其他handler时，链式执行/清理是否吞掉原任务或覆盖新所有者。
  - `ui\views\settings_tabs\tier_api_panel.py::_sync_selected/TierApiPanel`：probe标志不变但外部档位变化，或独立主题变化时是否重渲染同步。