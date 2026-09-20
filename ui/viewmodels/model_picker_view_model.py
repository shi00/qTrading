"""ModelPickerViewModel — ModelPicker 共享模型的 ViewModel（AI-01 §3.1c）。

声明式渲染范式（CLAUDE.md §3.2 MVVM）：
- 不可变 state snapshot（ModelPickerState frozen dataclass）
- subscribe/_notify 通知机制（View 通过 use_viewmodel hook 订阅）
- commands 作为实例方法

VM 不感知 locale：不 import flet、不持 Flet 控件、不调 page.update()，只产出数据。
结果行无 i18n 文案（供应商名/模型 id 均来自 litellm 目录，非翻译域）。

线程模型（R16）：
- 目录首次构建 + 搜索过滤（可能触发 litellm 惰性 import / 大量迭代）经
  ThreadPoolManager.run_async(TaskType.IO/CPU, ...) offload，带 is_loading 态。
- get_recent_selections / record_selection 走 ConfigHandler JSON，IO offload。
"""

from dataclasses import dataclass

from ui.viewmodels.config_panel_view_model_base import ConfigPanelViewModelBase
from utils.llm_providers import (
    LLM_PROVIDERS,
    PROVIDER_CATEGORIES,
    get_litellm_models_by_provider,
    get_model_info,
    get_provider_icon,
    get_recent_selections,
    record_selection,
    search_models,
)
from utils.thread_pool import TaskType, ThreadPoolManager


@dataclass(frozen=True)
class ModelRow:
    """搜索结果/浏览行（不可变）。"""

    provider_id: str
    provider_name: str
    icon: str
    model_id: str
    context: int


@dataclass(frozen=True)
class ProviderGroup:
    """浏览模式的供应商分组（不可变）。"""

    provider_id: str
    provider_name: str
    icon: str
    model_count: int
    models: tuple[ModelRow, ...]


@dataclass(frozen=True)
class ModelPickerState:
    """ModelPicker 的不可变 state snapshot。

    数据全部来自 litellm 目录投影 + 本地回记（非 locale 相关）。
    """

    # 搜索
    query: str = ""
    is_loading: bool = False
    searching: bool = False
    search_results: tuple[ModelRow, ...] = ()
    # 浏览（空 query）
    recent: tuple[ModelRow, ...] = ()
    groups: tuple[ProviderGroup, ...] = ()
    expanded: frozenset[str] = frozenset()
    # 当前已选（回显 + 判定 custom 历史用）
    selected_provider: str = ""
    selected_model: str = ""


class ModelPickerViewModel(ConfigPanelViewModelBase[ModelPickerState]):
    """ViewModel for ModelPicker。

    MVVM + declarative rendering paradigm：
    - Immutable state snapshot via subscribe/_notify
    - Commands as instance methods

    ModelPicker 是「共享」组件：不感知 provider 专用逻辑，只负责「从 litellm 目录选
    供应商+模型」。选中后由消费方（LLMConfigPanel / FailoverConfigPanel）写入自己的状态。
    """

    def __init__(self) -> None:
        self._state = ModelPickerState()
        self._init_mixin_fields()

    # --- Selection ---

    def set_selection(self, provider_id: str, model_id: str) -> None:
        """外部设定当前已选（消费方加载配置时调用，用于回显）。"""
        self._set_state(selected_provider=provider_id, selected_model=model_id)

    def select_model(self, row: ModelRow) -> None:
        """选中一行：一键设定 provider+model，并记录回记。

        record_selection 是 IO offload（ConfigHandler JSON 写入），不阻塞选择即时反馈。
        """
        self._set_state(
            selected_provider=row.provider_id,
            selected_model=row.model_id,
            query="",
            search_results=(),
            expanded=frozenset(),
        )
        self._notify()
        self._record_recent_async(row.provider_id, row.model_id)

    def update_model_query(self, query: str) -> None:
        """搜索框变化：有 keyword 进搜索模式，空退浏览模式（R16 offload 过滤）。

        目录由 ``ensure_catalog_loaded`` 在组件挂载时预载；搜索过滤为纯内存线性操作。
        """
        self._set_state(query=query, searching=query.strip() != "")
        if query.strip():
            self._on_search(query)
        else:
            self._set_state(search_results=())
            self._clone_browse_state()

    def toggle_group(self, provider_id: str) -> None:
        """折叠/展开供应商分组。"""
        expanded = set(self._state.expanded)
        if provider_id in expanded:
            expanded.discard(provider_id)
        else:
            expanded.add(provider_id)
        self._set_state(expanded=frozenset(expanded))

    # --- 目录加载 / 搜索（R16 offload） ---

    async def ensure_catalog_loaded(self) -> None:
        """首次进入面板时构建目录（R16：litellm 惰性 import 可达十秒级，必须 offload）。

        幂等：目录有模块级缓存，二次调用快速返回；is_loading 仅首次呈现加载态。
        """
        try:
            self._set_state(is_loading=True)
            projection = await ThreadPoolManager().run_async(TaskType.IO, get_litellm_models_by_provider)
            if not projection:
                self._set_state(is_loading=False, groups=(), recent=self._build_recent_rows())
                return
            self._set_state(is_loading=False)
            self._clone_browse_state()
        except Exception:
            # 目录加载失败：不静默吞错，is_loading 复位后保留空态（消费方经 status 呈现）。
            self._set_state(is_loading=False)

    def _on_search(self, query: str) -> None:
        """触发搜索（R16：search_models 可能首次触发目录构建，须 offload）。

        有运行 event loop（生产 UI）→ ``create_task`` 经 ThreadPoolManager 提交，
        不阻塞 Flet 事件循环；无运行 loop（测试/后台）→ 同步执行保证行为确定。
        """
        self._set_state(searching=True)
        loop = self._get_loop_or_none()
        if loop is not None and loop.is_running():
            loop.create_task(self._run_search(query))
        else:
            self._apply_search(self._compute_search(query))

    async def _run_search(self, query: str) -> None:
        """异步搜索（ThreadPool offload 后写回 state）。"""
        try:
            rows = await ThreadPoolManager().run_async(TaskType.CPU, self._compute_search, query)
            self._apply_search(rows)
        except Exception:
            # 搜索失败：不静默吞错，searching 复位保留空结果（消费方经状态呈现）。
            self._set_state(searching=False, search_results=())

    def _compute_search(self, query: str) -> tuple[ModelRow, ...]:
        """在线程池/同步路径执行搜索过滤，返回不可变行元组。"""
        results = search_models(query)
        return tuple(
            ModelRow(
                provider_id=r["provider_id"],
                provider_name=r["provider_name"],
                icon=get_provider_icon(r["provider_id"]),
                model_id=r["model_id"],
                context=int(r.get("context", 0)),
            )
            for r in results
        )

    def _apply_search(self, rows: tuple[ModelRow, ...]) -> None:
        """把搜索结果写入 state（searching 复位）。"""
        self._set_state(searching=False, search_results=rows)

    # --- 浏览模式构建 ---

    def _build_recent_rows(self) -> tuple[ModelRow, ...]:
        """读取最近选用记录 → 投影为 ModelRow 行（直连目录取 icon/context）。"""
        try:
            recents = get_recent_selections()
        except Exception:
            return ()
        rows: list[ModelRow] = []
        projection = get_litellm_models_by_provider()
        for entry in recents:
            provider_id = entry.get("provider", "")
            model_id = entry.get("model", "")
            if not provider_id or not model_id:
                continue
            info = get_model_info(provider_id, model_id)
            models = projection.get(provider_id, [])
            context = next((m.get("context", 0) for m in models if m.get("id") == model_id), 0)
            pinfo = LLM_PROVIDERS.get(provider_id, {})
            rows.append(
                ModelRow(
                    provider_id=provider_id,
                    provider_name=str(pinfo.get("name", provider_id)),
                    icon=get_provider_icon(provider_id),
                    model_id=model_id,
                    context=int(context or info.get("context", 0)),
                )
            )
        return tuple(rows)

    def _clone_browse_state(self) -> None:
        """同步浏览模式（recent + 供应商分组）。"""
        groups = self._build_groups()
        recent = self._build_recent_rows()
        self._set_state(groups=tuple(groups), recent=tuple(recent))

    def _build_groups(self) -> list[ProviderGroup]:
        """构建供应商分组（只含目录非空的 provider，按 PROVIDER_CATEGORIES 顺序）。"""
        projection = get_litellm_models_by_provider()
        groups: list[ProviderGroup] = []
        for category in PROVIDER_CATEGORIES.values():
            for provider_id in category:
                models = projection.get(provider_id, [])
                if not models:
                    continue
                pinfo = LLM_PROVIDERS.get(provider_id, {})
                rows = tuple(
                    ModelRow(
                        provider_id=provider_id,
                        provider_name=str(pinfo.get("name", provider_id)),
                        icon=get_provider_icon(provider_id),
                        model_id=m["id"],
                        context=int(m.get("context", 0)),
                    )
                    for m in models
                )
                groups.append(
                    ProviderGroup(
                        provider_id=provider_id,
                        provider_name=str(pinfo.get("name", provider_id)),
                        icon=get_provider_icon(provider_id),
                        model_count=len(models),
                        models=rows,
                    )
                )
        return groups

    # --- 异步回记 ---

    def _record_recent_async(self, provider_id: str, model_id: str) -> None:
        loop = self._get_loop_or_none()

        def _record() -> None:
            record_selection(provider_id, model_id)

        if loop is not None and loop.is_running():
            loop.create_task(ThreadPoolManager().run_async(TaskType.IO, _record))
        else:
            # 无运行 loop（测试/后台）：同步记录，保证回记可靠落库。
            _record()

    # --- 对外查询（供消费方读取选中态） ---

    def get_selected(self) -> tuple[str, str]:
        """返回 (provider, model)，供消费方读取选中态。"""
        return self._state.selected_provider, self._state.selected_model
