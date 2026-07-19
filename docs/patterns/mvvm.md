# MVVM 表现层

> 来源：从 CONTRIBUTING.md 迁移

> 对应 [CLAUDE.md §3.2 UI 模型（强制）](../../CLAUDE.md#32--强制要求)；声明式渲染细则见 [V1 声明式 UI 开发规范](../flet/v1-api-constraints.md#v1-声明式-ui-开发规范)。

采用 MVVM + 声明式渲染复合范式：MVVM 负责架构分层，声明式负责 UI 渲染模型。`View = f(ViewModel.state)`，用户事件调 `ViewModel.command()`，VM 更新 state 后 View 自动重渲染。

### 三层职责

| 层 | 职责 | 禁止 |
|----|------|------|
| **View** (`ui/views/`, `@ft.component`) | 读 state 渲染控件树、事件调 commands | 持有业务状态、`did_mount`/`will_unmount`、`self.update()`、`UserControl`、`PageRefMixin` |
| **ViewModel** (`ui/viewmodels/`) | 持有业务状态、调 services/strategies/data；暴露不可变 `state` snapshot + `commands` 方法 | import flet、持有 Flet 控件、`page.update()`/`control.update()`、感知 locale |
| **Component** (`ui/components/`) | 可复用无状态控件（图表、对话框、虚拟表格、Toast） | 耦合具体业务 |
| **Theme** (`ui/theme.py`) | 亮/暗主题切换，颜色/字体 token 集中管理 | — |
| **i18n** (`ui/i18n.py`) | 对 `core.i18n` 的 UI 层薄封装，提供 Flet 文本绑定 | — |

### ViewModel 形态契约

```python
from collections.abc import Callable
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Message:
    """带参数的 i18n 消息：VM 产出 (key, params)，View 按当前 locale 渲染。"""
    key: str
    params: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class Row:
    """行数据 frozen dataclass；tuple[Row, ...] 保证 state 不可变。"""
    code: str
    name: str
    score: float

@dataclass(frozen=True)
class ScreenerState:
    rows: tuple[Row, ...]       # 不可变；DataFrame 转 tuple[Row, ...]，禁止 tuple[dict, ...]
    status: Message             # 带 params 的 i18n 消息
    loading: bool

class ScreenerViewModel:
    @property
    def state(self) -> ScreenerState:
        return ScreenerState(rows=tuple(...), status=Message(...), loading=...)

    async def run(self) -> None: ...                  # command（异步）
    def select_strategy(self, key: str) -> None: ...  # command（同步）

    def subscribe(self, callback: Callable[[ScreenerState], None]) -> Callable[[], None]:
        """订阅 state 变更；返回退订函数。hook 用此注册，_notify 调用时触发。"""
        ...

    def _notify(self) -> None:
        """内部状态变更后调用；遍历订阅者 callback(self.state)。不持有 View 引用。"""
        ...

    def dispose(self) -> None: ...   # 可选：卸载时清理资源
```

- `state` 必须不可变（frozen dataclass / NamedTuple / tuple）；内部状态变更后返回新 snapshot
- `state` 字段不得用 `dict` / `list` / `DataFrame` 等可变类型；行数据用 `tuple[Row, ...]`（Row 为 frozen dataclass），DataFrame 在 VM 内部转换为 Row tuple
- i18n 消息用 `Message(key, params)`，View 渲染时 `I18n.get(msg.key, **msg.params)`；VM 只产出 key+params，不感知当前 locale
- `commands` 即 VM 实例方法，稳定引用；异步 command 在 View 事件处理器 `await`
- VM 通过 `subscribe(callback) -> unsub` 暴露可观察性；`_notify()` 调用所有注册 callback，传入新 state snapshot；VM 不持有 View 引用，订阅关系由 `use_viewmodel` hook 建立

### 桥接 hook 契约

View 通过 `use_viewmodel(factory=...)` 或 `use_viewmodel(vm=...)` 消费 ViewModel（两种模式互斥，不可同时传入；完整签名与实现见 [ui/hooks.py](../../ui/hooks.py)）：

```python
import flet as ft
from core.i18n import I18n
from ui.hooks import use_viewmodel          # 已实现，见 ui/hooks.py
from ui.viewmodels.screener_view_model import ScreenerViewModel

@ft.component
def ScreenerView():
    # factory= 模式：hook 实例化 VM，卸载时退订 + dispose
    state, vm = use_viewmodel(ScreenerViewModel)   # 首次渲染实例化 + 订阅 _notify

    async def on_run(e):
        await vm.run()    # command -> _notify -> state 更新 -> 自动重渲染

    return ft.Column([
        ft.Text(I18n.get(state.status.key, **state.status.params)),  # Message 渲染
        ft.Button(I18n.get("run"), on_click=on_run),
    ])
```

`use_viewmodel` 契约（已实现，见 [ui/hooks.py](../../ui/hooks.py)，签名 `use_viewmodel(factory=None, *, vm=None, dispose_on_unmount=True) -> (state, vm)`）：

**两种互斥模式**：

| 模式 | 调用形式 | 适用场景 | 卸载时清理责任 |
|------|---------|---------|----------------|
| **内部 VM 模式**（`factory=`） | `use_viewmodel(factory=ScreenerViewModel)` 或位置参数 `use_viewmodel(ScreenerViewModel)` | View 内部独占 VM（如 `ScreenerView`、`TaskCenterView`） | hook 调 `unsub()` 退订 + `dispose_on_unmount=True` 时调 `vm.dispose()` |
| **外部 VM 模式**（`vm=`） | `use_viewmodel(vm=shared_vm)` | 消费方持有 VM 引用需调用 commands（如 config panel VM 由 `OnboardingWizard`/`AIBrainTab` 实例化，子组件需调 `save_config`/`verify_token`） | hook 仅调 `unsub()` 退订，**永远不 dispose**（外部 VM 生命周期由消费方管理） |

**生命周期与订阅**：

- 首次渲染：`factory()` 实例化 VM（内部模式）或直接使用传入的 `vm`（外部模式），调 `vm.subscribe(set_state)` 注册（保存返回的 unsub），返回 `(vm.state, vm)`
- `_notify` 触发：VM 遍历订阅者调 `callback(self.state)`，hook 注册的 callback 即 `set_state(new_state)`，触发组件重渲染
- 卸载时：`use_effect` 的显式 `cleanup=` 参数调 `unsub()` 退订；内部模式且 `dispose_on_unmount=True` 时额外调 `vm.dispose()`
- `factory` 必须是无参 callable；DI 参数在 factory 闭包里完成（如 `lambda: ScreenerViewModel(dep1, dep2)` 或 `functools.partial`），VM 的 `__init__` 接受 DI 参数，不在构造函数里隐式获取全局状态（遵循 [CLAUDE.md §4.3](../../CLAUDE.md#43-单例模式) DI 原则）

### 存量技术债

[ui/viewmodels/](../../ui/viewmodels/) 下所有 ViewModel 必须满足 [`_ViewModelProtocol`](../../ui/hooks.py)（`state` / `subscribe` / `dispose` 三方法）+ state snapshot + commands + `use_viewmodel` 目标范式。新代码必须沿用此范式，不得使用 `on_update`/`on_log` 回调注入。已知例外清单见 `ui/viewmodels/` 审查记录。
