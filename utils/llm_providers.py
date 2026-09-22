"""LLM Provider Configuration Data & litellm 官方目录投影（AI-01 §3.1c）。

设计约定（docs/designs/ai01-unpriced-cost-plan.md §3.1c / §3.1c-review）：
- 供应商/模型清单以 **litellm 官方目录为唯一权威源**。项目不再预置任何硬编码型号数组、
  "推荐/主流"标签、或注册/控制台/定价/模型链接等 URL 字段。用户从 litellm 列表**自行选择**，
  无任何注册引导、无商业推荐。
- 展示层仅保留供应商识别元数据（name/name_en/icon/key_prefix/custom/azure_config/分类）。
- `LLM_PROVIDERS` 仍保留运行路由必需的 `base_url`（功能性 API 端点）与 `litellm_prefix`
  （`_build_litellm_params` 拼 `<prefix>/<model>` 走路由）。**UI 枚举（litellm_catalog_key）
  与运行路由（litellm_prefix）解耦**——qwen/zhipu/moonshot/minimax 的 litellm_prefix 均为
  "openai"（OpenAI 兼容端点），若 UI 按 litellm_prefix 枚举会把多家混进 openai 目录（错误设计）。
- litellm 为惰性加载（避免 UI 主循环 import 阻塞）；本模块内 ``import litellm`` 均在函数体内执行。
"""

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

AZURE_DEFAULT_API_VERSION = "2025-04-01-preview"

AZURE_API_VERSIONS = [
    "2025-04-01-preview",
    "2024-12-01-preview",
    "2024-10-21",
]

# 最近选用记录上限（取最近选用的前 N 条置顶）
RECENT_SELECTIONS_LIMIT = 8

# Mi3（review-pr1073）：recent 回记读改写互斥锁（模块级，跨 ModelPicker 实例）。
_RECENT_SELECTIONS_LOCK = threading.Lock()

# 语义化配置的键（recent 回记忆复用现有 ConfigHandler JSON 持久化，最小实现）
_LLM_RECENT_SELECTIONS_KEY = "llm_recent_selections"


# 仅保留供应商识别元数据 + 运行路由必需字段（无 models / 无 URL / 无 tag）。
# litellm_catalog_key：UI 枚举与计价回溯专用（litellm 官方目录 key），回退自身 provider_id。
LLM_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "name_en": "DeepSeek",
        "icon": "deepseek.png",
        "base_url": "https://api.deepseek.com",
        "key_prefix": "sk-",
        "litellm_prefix": "deepseek",
        "litellm_catalog_key": "deepseek",
    },
    "qwen": {
        "name": "通义千问",
        "name_en": "Alibaba Qwen",
        "icon": "qwen.png",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_prefix": "sk-",
        "litellm_prefix": "openai",
        "litellm_catalog_key": "dashscope",
        # dakscope 目录含第三方托管模型（deepseek/glm/kimi 等，探针实证），
        # 投影到 qwen 供应商时按品牌前缀过滤，避免"通义组混入他牌模型"（review-pr1073 D1）。
        "catalog_brand_prefixes": ("qwen",),
    },
    "zhipu": {
        "name": "智谱 AI",
        "name_en": "Zhipu AI",
        "icon": "zhipu.png",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "key_prefix": "",
        "litellm_prefix": "openai",
        # zhipu（智谱国内）在 litellm 目录的官方键为 "zai"（智谱国际品牌 Z.ai），
        # 与 qwen→dashscope 同一范式：UI 枚举与计价统一回溯 zai 目录。
        "litellm_catalog_key": "zai",
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "name_en": "Moonshot AI",
        "icon": "moonshot.png",
        "base_url": "https://api.moonshot.cn/v1",
        "key_prefix": "sk-",
        "litellm_prefix": "openai",
        "litellm_catalog_key": "moonshot",
    },
    "minimax": {
        "name": "MiniMax",
        "name_en": "MiniMax",
        "icon": "minimax.png",
        "base_url": "https://api.minimaxi.com/v1",
        "key_prefix": "",
        "litellm_prefix": "openai",
        "litellm_catalog_key": "minimax",
    },
    "openai": {
        "name": "OpenAI",
        "name_en": "OpenAI",
        "icon": "openai.png",
        "base_url": "https://api.openai.com",
        "key_prefix": "sk-",
        "litellm_prefix": "openai",
        "litellm_catalog_key": "openai",
    },
    "azure": {
        "name": "Azure OpenAI",
        "name_en": "Azure OpenAI",
        "icon": "azure.png",
        "base_url": "",
        "key_prefix": "",
        "azure_config": True,
        # azure 部署制，无 litellm 目录 → 保留部署名输入
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "name_en": "Anthropic Claude",
        "icon": "anthropic.png",
        "base_url": "https://api.anthropic.com",
        "key_prefix": "sk-ant-",
        "litellm_prefix": "anthropic",
        "litellm_catalog_key": "anthropic",
    },
    "google": {
        "name": "Google AI (Gemini)",
        "name_en": "Google Gemini",
        "icon": "google.png",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "key_prefix": "",
        "litellm_prefix": "gemini",
        "litellm_catalog_key": "gemini",
    },
    "mistral": {
        "name": "Mistral AI",
        "name_en": "Mistral AI",
        "icon": "mistral.png",
        "base_url": "https://api.mistral.ai",
        "key_prefix": "",
        "litellm_prefix": "mistral",
        "litellm_catalog_key": "mistral",
    },
    "custom": {
        "name": "自定义供应商",
        "name_en": "Custom Provider",
        "icon": "custom.png",
        "base_url": "",
        "key_prefix": "",
        "litellm_prefix": "openai",
        "litellm_catalog_key": "",
        "custom": True,
    },
}

PROVIDER_CATEGORIES = {
    "domestic": ["deepseek", "qwen", "zhipu", "moonshot", "minimax"],
    "international": ["openai", "azure", "anthropic", "google", "mistral"],
    "custom": ["custom"],
}


def get_provider_by_id(provider_id: str) -> dict:
    """获取供应商配置"""
    return LLM_PROVIDERS.get(provider_id, LLM_PROVIDERS["custom"])


def get_all_providers() -> dict:
    """获取所有供应商"""
    return LLM_PROVIDERS


def get_providers_by_category(category: str) -> list:
    """获取分类下的供应商列表"""
    provider_ids = PROVIDER_CATEGORIES.get(category, [])
    return [LLM_PROVIDERS[pid] for pid in provider_ids if pid in LLM_PROVIDERS]


def get_provider_icon_path(icon_name: str) -> str:
    """
    获取供应商图标绝对路径

    Args:
        icon_name: 图标文件名 (如 "deepseek.png")

    Returns:
        图标绝对路径，如果不存在则返回默认图标路径
    """
    base_path = Path(__file__).parent.parent / "assets" / "icons" / "providers"
    icon_path = base_path / icon_name
    if icon_path.exists():
        return str(icon_path)
    return str(base_path / "custom.png")


def get_provider_icon(provider_id: str) -> str:
    """
    获取供应商图标路径

    Args:
        provider_id: 供应商 ID (如 "deepseek", "openai")

    Returns:
        图标绝对路径
    """
    provider = LLM_PROVIDERS.get(provider_id, {})
    icon_name = provider.get("icon", "custom.png")
    return get_provider_icon_path(icon_name)


# ---------------------------------------------------------------------------
# litellm 官方目录投影（UI 枚举 + 计价回溯唯一权威源）
# ---------------------------------------------------------------------------


def get_enabled_provider_ids() -> list[str]:
    """返回供应商下拉应展示的 provider_id（litellm 目录推导，按分类顺序）。

    规则（AI-01 §3.1c 联动改造）：
    - azure（部署制）/ custom（自由文本）无目录但保留配置通道 → 始终包含；
    - 其余供应商仅当 litellm 投影目录非空时保留（目录为空 → 下拉隐藏，
      避免出现「可选但连不上」项；与 ModelPicker 分组口径一致）；
    - **目录投影尚未加载（首帧）/litellm 不可用**：回退 LLM_PROVIDERS 全量
      （只读缓存判断，不触发 litellm 惰性 import，R16）。

    调用方（View）额外保证：当前已配置 provider 即使目录为空也强制保留，
    避免存量配置回显被吞。
    """
    projection = get_cached_litellm_projection()
    result: list[str] = []
    for category in PROVIDER_CATEGORIES.values():
        for provider_id in category:
            conf = LLM_PROVIDERS.get(provider_id)
            if conf is None:
                continue
            if conf.get("azure_config") or conf.get("custom"):
                result.append(provider_id)
                continue
            if projection is not None and not projection.get(provider_id):
                # 目录已加载但该供应商无模型 → 隐藏（避免静态清单与目录漂移）。
                continue
            result.append(provider_id)
    return result


def get_cached_litellm_projection() -> dict[str, list[dict]] | None:
    """返回已加载的投影缓存（纯内存判断，不触发 litellm 惰性 import，R16）。

    目录加载由 ModelPicker 挂载 effect 经 ThreadPoolManager offload；此处仅在
    缓存已就绪（版本匹配）时返回，否则 None（调用方按「未加载」降级处理）。
    供 get_enabled_provider_ids（供应商下拉推导）与 ModelPickerViewModel
    （级联过滤的浏览重建）在 UI 线程安全复用。
    """
    if _PROJECTION_CACHE and _get_litellm_version() == _PROJECTION_CACHE_LITELLM_VERSION:
        return _PROJECTION_CACHE
    return None


def _get_litellm_version() -> str:
    """读取锁定的 litellm 版本号（缓存键，升级后失效重算）。"""
    from importlib.metadata import version  # type: ignore[import-untyped]  # stdlib

    try:
        return version("litellm")
    except Exception:
        return "unknown"


def _resolve_catalog_key(provider_id: str) -> str:
    """解析 provider 的 litellm 官方目录 key。

    显式配置优先；缺省回退**自身 provider_id**（不得回退 litellm_prefix——qwen 等
    litellm_prefix 为 "openai" 会与 GPT 撞车，见 §3.1c-review #9）。
    """
    return LLM_PROVIDERS.get(provider_id, {}).get("litellm_catalog_key") or provider_id


def _extract_context(cost_entry: dict) -> int:
    """从 litellm.model_cost 的模型条目中取 context（首非零 token 上限，缺全退 0）。

    升级韧性：对不可预期字段增删用 dict.get 防御，.get 首参数为 None 时返回默认 None。
    返回 int；无法计量时 0（R21：不伪装成业务合法值，UI 隐藏 context 呈现）。
    """
    for key in ("max_input_tokens", "max_tokens", "max_output_tokens"):
        value = (cost_entry or {}).get(key)
        if isinstance(value, (int, float)) and value:
            return int(value)
    return 0


def _context_with_fallback(raw_id: str, cost_entry: dict, litellm_module) -> int:
    """context 权威源：model_cost 首查，缺失回退 ``litellm.get_model_info``（review-pr1073 A3/C2）。

    litellm 存在第二通道 ``get_model_info(model)`` 可补齐 ``model_cost`` 中 ``max_*`` 全为
    None 的模型（实机验证 ``openai/gpt-4o``：``model_cost`` 的 max_* 全 None，而
    ``get_model_info(...)[\"max_tokens\"]=16384`` 有值）。仅当 model_cost 无法计量时回退，
    避免对全部模型重复查询。异常防御（升级韧性）：失败保持 0，R21 诚实呈现。
    """
    context = _extract_context(cost_entry)
    if context:
        return context
    try:
        info = litellm_module.get_model_info(raw_id)
    except Exception:
        return 0
    if not isinstance(info, dict):
        return 0
    for key in ("max_input_tokens", "max_tokens", "max_output_tokens"):
        value = info.get(key)
        if isinstance(value, (int, float)) and value:
            return int(value)
    return 0


def _normalize_model_id(model_id: str) -> str:
    """模型 id 可能是 ``provider/model`` 形态，按 `/` 右侧 id 归一（P2 去重键）。"""
    return model_id.split("/")[-1] if "/" in model_id else model_id


# 模块级投影缓存：缓存键含 litellm 版本号，升级后失效重算。
_PROJECTION_CACHE: dict[str, list[dict]] = {}
_PROJECTION_CACHE_LITELLM_VERSION: str = ""


def get_litellm_models_by_provider() -> dict[str, list[dict]]:
    """按 litellm 官方目录投影各项目供应商的模型清单。

    只投影项目支持的供应商（LLM_PROVIDERS 内的 provider_id），不把 litellm 全部
    供应商全量塞入。每项 ``{"id", "context"}``；同一 catalog_key 被多个项目 provider
    复用时按归一模型 id 去重。

    返回:
        ``{provider_id: [{"id": str, "context": int}, ...]}``。某 key 缺失时该 provider
        为空列表，不崩 UI（升级韧性）。azure（部署制）/custom（自由文本）无目录 → 空列表。
    """
    global _PROJECTION_CACHE, _PROJECTION_CACHE_LITELLM_VERSION

    import litellm  # type: ignore[import-untyped]  # 惰性加载，避免 UI 主循环 import 阻塞（R16）

    # 惰性 import 本身可能较慢（十秒级），但首次触发由调用方（VM 命令）经
    # ThreadPoolManager offload；此处仅做缓存命中判断（纯内存，快）。
    litellm_version = _get_litellm_version()
    if litellm_version == _PROJECTION_CACHE_LITELLM_VERSION and _PROJECTION_CACHE:
        return _PROJECTION_CACHE

    models_by_provider: dict = getattr(litellm, "models_by_provider", {}) or {}
    model_cost: dict = getattr(litellm, "model_cost", {}) or {}

    result: dict[str, list[dict]] = {}
    for provider_id in LLM_PROVIDERS:
        provider_conf = LLM_PROVIDERS[provider_id]
        # azure（部署制）/ custom（自由文本）不枚举 litellm 目录，保留空列表。
        if provider_conf.get("azure_config") or provider_conf.get("custom"):
            result[provider_id] = []
            continue
        catalog_key = _resolve_catalog_key(provider_id)
        raw_ids = models_by_provider.get(catalog_key, set()) if isinstance(models_by_provider, dict) else set()
        if not isinstance(raw_ids, (set, list)):
            raw_ids = set()

        # review-pr1073 D1：litellm 目录可能含跨品牌托管模型（如 dashscope 目录含
        # deepseek/glm/kimi），投影到项目供应商时按品牌前缀过滤（仅配置了
        # catalog_brand_prefixes 的 provider 启用；未配置即全部投影）。
        brand_prefixes = provider_conf.get("catalog_brand_prefixes") or ()

        seen: set[str] = set()
        entries: list[dict] = []
        for raw_id in raw_ids:
            norm = _normalize_model_id(raw_id)
            if not norm or norm in seen:
                continue
            if brand_prefixes and not any(norm.startswith(p) for p in brand_prefixes):
                continue
            seen.add(norm)
            cost_entry = model_cost.get(raw_id, {}) if isinstance(model_cost, dict) else {}
            entries.append(
                {
                    "id": norm,
                    "context": _context_with_fallback(raw_id, cost_entry, litellm),
                }
            )
        # 稳定排序（防抖动）：按模型 id。
        entries.sort(key=lambda e: e["id"])
        result[provider_id] = entries

    # 仅在目录数据可信时缓存：litellm 为 MagicMock / 目录缺失（测试会话全局 mock、
    # 依赖退化）时 models_by_provider 非 dict 或空，投影出的全空清单若入缓存会以
    # 真实版本号污染 get_cached_litellm_projection / get_enabled_provider_ids
    # （「全部供应商无模型」假象，导致供应商下拉被清空）。
    # 权衡（review-fix R3）：真实 litellm 的 models_by_provider 恰为空 dict 时同样不缓存——
    # ① 首次 import（耗时主因）已由 ensure_catalog_loaded offload，空投影重建极廉价；
    # ② 不缓存空投影正是防止 get_enabled_provider_ids 读到「全部供应商无模型」而
    #    把清单误清空的有意防护，不可为「省一次轻量重建」而放宽。
    if isinstance(models_by_provider, dict) and models_by_provider:
        _PROJECTION_CACHE = result
        _PROJECTION_CACHE_LITELLM_VERSION = litellm_version
    return result


def _provider_order(provider_id: str) -> int:
    """provider 在 PROVIDER_CATEGORIES 中的遍历顺序（稳定排序首键）。"""
    for idx, category in enumerate(PROVIDER_CATEGORIES.values()):
        if provider_id in category:
            return idx
    return len(PROVIDER_CATEGORIES)


def search_providers(keyword: str) -> list[dict]:
    """按关键字过滤**启用供应商**（provider-only 选择器搜索，与模型条目同构）。

    供应商清单以 ``get_enabled_provider_ids`` 目录推导为准；匹配 provider_id /
    name / name_en / key_prefix（大小写不敏感），按分类顺序稳定排序（与
    ``search_models`` 的排序键一致）。返回拍平列表：
    ``[{provider_id, provider_name, icon}]``。空/空白关键字返回空列表
    （UI 据此转浏览模式）。
    """
    if not keyword:
        return []
    needle = keyword.lower().strip()
    if not needle:
        return []

    result: list[dict] = []
    for provider_id in get_enabled_provider_ids():
        conf = LLM_PROVIDERS.get(provider_id, {})
        if (
            needle in provider_id.lower()
            or needle in str(conf.get("name", "")).lower()
            or needle in str(conf.get("name_en", "")).lower()
            or needle in str(conf.get("key_prefix", "")).lower()
        ):
            result.append(
                {
                    "provider_id": provider_id,
                    "provider_name": str(conf.get("name", provider_id)),
                    "icon": str(conf.get("icon", "custom.png")),
                }
            )
    return result


def search_models(keyword: str) -> list[dict]:
    """在全量投影结果上跨供应商子串搜索（大小写不敏感）。

    返回拍平列表，每项含 ``{provider_id, provider_name, icon, model_id, context}``；
    排序按（分类顺序, 供应商 name_en, 模型 id）稳定排序防抖动。空 keyword 返回空列表
    （UI 据此转浏览模式）。

    性能（R16）：预构建小写归一化索引（model id + provider 名），单次过滤 O(n) 命中
    集合较小；openai 231 项规模下按需惰性排序。事件处理器内过滤整体超出阈值时由调用方
    经 ThreadPoolManager offload（config-quality-perf 决策）。
    """
    if not keyword:
        return []

    projection = get_litellm_models_by_provider()

    # 重建扁平行（每次搜索重投影行，但投影结果有模块级缓存，二次命中快）。
    flat: list[dict] = []
    for provider_id, models in projection.items():
        provider = LLM_PROVIDERS.get(provider_id, {})
        for model in models:
            flat.append(
                {
                    "provider_id": provider_id,
                    "provider_name": str(provider.get("name", provider_id)),
                    "icon": str(provider.get("icon", "custom.png")),
                    "model_id": model["id"],
                    "context": int(model.get("context", 0)),
                }
            )

    needle = keyword.lower().strip()
    if not needle:
        return []

    # 内置 provider_id / provider_name（name_en 便于英文匹配）/ key_prefix 命中也可选。
    boost = [
        provider_id
        for provider_id in LLM_PROVIDERS
        if needle in provider_id.lower()
        or needle in str(LLM_PROVIDERS[provider_id].get("name", "")).lower()
        or needle in str(LLM_PROVIDERS[provider_id].get("name_en", "")).lower()
        or needle in str(LLM_PROVIDERS[provider_id].get("key_prefix", "")).lower()
    ]
    boost_set = set(boost)

    matches = [row for row in flat if needle in row["model_id"].lower() or row["provider_id"] in boost_set]

    matches.sort(
        key=lambda r: (
            _provider_order(r["provider_id"]),
            str(LLM_PROVIDERS.get(r["provider_id"], {}).get("name_en", r["provider_id"])).lower(),
            r["model_id"].lower(),
        )
    )
    return matches


# ---------------------------------------------------------------------------
# 最近选用回记（复用 ConfigHandler JSON 持久化，最小实现）
# ---------------------------------------------------------------------------


def get_recent_selections() -> list[dict]:
    """读取最近选用记录（有序去重，`[{"provider", "model"}]`）。

    ConfigHandler 同步 IO；UI 事件处理器经 ThreadPoolManager offload 后调入。
    """
    from utils.config_handler import ConfigHandler  # lazy-import: 避免模块顶层触发 IO

    try:
        config = ConfigHandler.load_config()
    except Exception as ex:
        logger.debug("[llm_providers] read recent selections failed: %s", ex)
        return []
    # config 为用户可编辑文件（trust boundary）：损坏/非字典降级为空列表，不崩 UI。
    if not isinstance(config, dict):
        return []
    raw = config.get(_LLM_RECENT_SELECTIONS_KEY, [])
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider", ""))
        model = str(entry.get("model", ""))
        if not provider or not model:
            continue
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        result.append({"provider": provider, "model": model})
    return result


def record_selection(provider: str, model: str) -> None:
    """记录一次选用（有序去重，上限 RECENT_SELECTIONS_LIMIT，新的在前）。

    Mi3（review-pr1073）：读改写加模块级锁——多个 ModelPicker 实例（主配置面板 +
    failover 对话框等）并发更新时，不加锁会丢尾部 1-2 条（读缓存→改→写非原子）。
    ConfigHandler 同步 IO，由调用方（UI 事件处理器）经 ThreadPool offload 后调入，
    此处锁保证同进程内并发写的读写原子性。
    """
    if not provider or not model:
        return
    from utils.config_handler import ConfigHandler  # lazy-import: 避免模块顶层触发 IO

    with _RECENT_SELECTIONS_LOCK:
        current = get_recent_selections()
        current = [entry for entry in current if not (entry["provider"] == provider and entry["model"] == model)]
        current.insert(0, {"provider": provider, "model": model})
        current = current[:RECENT_SELECTIONS_LIMIT]
        try:
            ConfigHandler.save_config({_LLM_RECENT_SELECTIONS_KEY: current})
        except Exception as ex:
            logger.debug("[llm_providers] record recent selection failed: %s", ex)


# ---------------------------------------------------------------------------
# 模型信息（对外契约：返回 {"id", "name", "context"}）
# ---------------------------------------------------------------------------


def get_model_info(provider_id: str, model_id: str) -> dict:
    """获取模型信息。

    对外契约**不变**：返回 ``{"id", "name", "context"}``。
    context 解析优先级 =
        1. litellm ``model_cost``（经投影目录，provider_id → catalog_key 归一到官方 key）；
        2. 项目 ``llm_custom_model_contexts`` 运行时覆盖；
        3. 0（litellm 未声明/无法计量，R21 诚实呈现）。

    ``token_budget.py`` 消费 ``get_model_info(...).get("context", 0)`` 不受影响。
    """
    context = 0

    try:
        projection = get_litellm_models_by_provider()  # 已缓存，命中快
        for model in projection.get(provider_id, []):
            if model["id"] == model_id:
                context = int(model.get("context", 0))
                break
    except Exception as ex:
        logger.debug("[llm_providers] get_model_info litellm lookup failed: %s", ex)

    if context == 0:
        try:
            from utils.config_handler import ConfigHandler  # lazy-import

            # llm_custom_model_contexts 经 get_llm_config()["custom_model_contexts"] 暴露
            llm_config = ConfigHandler.get_llm_config()
            cmc = llm_config.get("custom_model_contexts", {})
            if isinstance(cmc, dict) and model_id in cmc.get(provider_id, {}):
                context = int(cmc[provider_id][model_id] or 0)
        except Exception as ex:
            logger.debug("[llm_providers] get_model_info config lookup failed: %s", ex)

    return {"id": model_id, "name": model_id, "context": context}
