"""
LLM Provider Configuration Data

数据来源说明:
- 验证日期: 2026-05-05
- 数据来源: 各供应商官方 API 文档和定价页面
- 更新策略:
  1. 用户可通过"刷新模型列表"按钮动态获取最新模型
  2. 用户可手动输入任意模型 ID
  3. 开发团队定期同步各供应商最新模型列表
- 注意: 以下为静态默认列表，可能不是最新。建议使用动态刷新功能获取实时模型列表。
"""

from pathlib import Path

AZURE_DEFAULT_API_VERSION = "2025-04-01-preview"

AZURE_API_VERSIONS = [
    "2025-04-01-preview",
    "2024-12-01-preview",
    "2024-10-21",
]

LLM_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "name_en": "DeepSeek",
        "icon": "deepseek.png",
        "base_url": "https://api.deepseek.com",
        "models": [
            {
                "id": "deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "context": 1000000,
                "tag": ["旗舰", "reasoning"],
            },
            {
                "id": "deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "context": 1000000,
                "tag": "推荐",
            },
        ],
        "key_prefix": "sk-",
        "litellm_prefix": "deepseek",
        "console_url": "https://platform.deepseek.com/api_keys",
        "pricing_url": "https://api-docs.deepseek.com/quick_start/pricing",
        "models_url": "https://api-docs.deepseek.com/",
    },
    "qwen": {
        "name": "通义千问",
        "name_en": "Alibaba Qwen",
        "icon": "qwen.png",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {
                "id": "qwen3.6-max-preview",
                "name": "Qwen 3.6 Max",
                "context": 256000,
                "tag": ["旗舰", "reasoning"],
            },
            {
                "id": "qwen3.6-plus",
                "name": "Qwen 3.6 Plus",
                "context": 1000000,
                "tag": "推荐",
            },
            {
                "id": "qwen3.6-flash",
                "name": "Qwen 3.6 Flash",
                "context": 1000000,
                "tag": "高速",
            },
        ],
        "key_prefix": "sk-",
        "litellm_prefix": "openai",
        "console_url": "https://dashscope.console.aliyun.com/apiKey",
        "pricing_url": "https://help.aliyun.com/zh/model-studio/getting-started/models",
        "models_url": "https://help.aliyun.com/zh/model-studio/text-generation-model/",
    },
    "zhipu": {
        "name": "智谱 AI",
        "name_en": "Zhipu AI",
        "icon": "zhipu.png",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-5.1", "name": "GLM-5.1", "context": 200000, "tag": "最新"},
            {"id": "glm-5", "name": "GLM-5", "context": 200000, "tag": ["旗舰", "reasoning"]},
        ],
        "key_prefix": "",
        "litellm_prefix": "openai",
        "console_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "pricing_url": "https://open.bigmodel.cn/pricing",
        "models_url": "https://open.bigmodel.cn/cn/guide/start/model-overview",
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "name_en": "Moonshot AI",
        "icon": "moonshot.png",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "kimi-k2.6", "name": "Kimi K2.6", "context": 262144, "tag": "最新"},
            {"id": "kimi-k2.5", "name": "Kimi K2.5", "context": 262144, "tag": "推荐"},
        ],
        "key_prefix": "sk-",
        "litellm_prefix": "openai",
        "console_url": "https://platform.moonshot.cn/console/api-keys",
        "pricing_url": "https://platform.moonshot.cn/docs/pricing/chat",
        "models_url": "https://platform.moonshot.cn/docs/models",
    },
    "minimax": {
        "name": "MiniMax",
        "name_en": "MiniMax",
        "icon": "minimax.png",
        "base_url": "https://api.minimaxi.com/v1",
        "models": [
            {
                "id": "MiniMax-M2.7",
                "name": "MiniMax M2.7",
                "context": 204800,
                "tag": "最新",
            },
            {
                "id": "MiniMax-M2.5",
                "name": "MiniMax M2.5",
                "context": 204800,
                "tag": "推荐",
            },
        ],
        "key_prefix": "",
        "litellm_prefix": "openai",
        "console_url": "https://www.minimaxi.com/user-center/basic-information/interface-key",
        "pricing_url": "https://www.minimaxi.com/document/pricing",
        "models_url": "https://www.minimaxi.com/document/guides/chat",
    },
    "openai": {
        "name": "OpenAI",
        "name_en": "OpenAI",
        "icon": "openai.png",
        "base_url": "https://api.openai.com",
        "models": [
            {"id": "gpt-5.5", "name": "GPT-5.5", "context": 1050000, "tag": "最新旗舰"},
            {"id": "gpt-5.4", "name": "GPT-5.4", "context": 1050000, "tag": "旗舰"},
            {
                "id": "gpt-5.4-mini",
                "name": "GPT-5.4 Mini",
                "context": 400000,
                "tag": "推荐",
            },
            {
                "id": "gpt-5.4-nano",
                "name": "GPT-5.4 Nano",
                "context": 400000,
                "tag": "高速",
            },
            {"id": "o4-mini", "name": "o4 Mini", "context": 200000, "tag": ["推理", "reasoning"]},
            {"id": "o3-pro", "name": "o3 Pro", "context": 200000, "tag": ["推理增强", "reasoning"]},
        ],
        "key_prefix": "sk-",
        "litellm_prefix": "openai",
        "console_url": "https://platform.openai.com/api-keys",
        "pricing_url": "https://openai.com/api/pricing",
        "models_url": "https://platform.openai.com/docs/models",
    },
    "azure": {
        "name": "Azure OpenAI",
        "name_en": "Azure OpenAI",
        "icon": "azure.png",
        "base_url": "",
        "models": [],
        "key_prefix": "",
        "console_url": "https://portal.azure.com/",
        "pricing_url": "https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/",
        "models_url": "https://learn.microsoft.com/azure/ai-services/openai/concepts/models",
        "azure_config": True,
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "name_en": "Anthropic Claude",
        "icon": "anthropic.png",
        "base_url": "https://api.anthropic.com",
        "models": [
            {
                "id": "claude-opus-4-7",
                "name": "Claude Opus 4.7",
                "context": 1000000,
                "tag": ["最新旗舰", "reasoning"],
            },
            {
                "id": "claude-opus-4-6",
                "name": "Claude Opus 4.6",
                "context": 1000000,
                "tag": ["旗舰", "reasoning"],
            },
            {
                "id": "claude-sonnet-4-6",
                "name": "Claude Sonnet 4.6",
                "context": 1000000,
                "tag": ["推荐", "reasoning"],
            },
            {
                "id": "claude-haiku-4-5",
                "name": "Claude Haiku 4.5",
                "context": 200000,
                "tag": "高速",
            },
        ],
        "key_prefix": "sk-ant-",
        "litellm_prefix": "anthropic",
        "console_url": "https://console.anthropic.com/settings/keys",
        "pricing_url": "https://www.anthropic.com/pricing",
        "models_url": "https://docs.anthropic.com/en/docs/about-claude/models/overview",
    },
    "google": {
        "name": "Google AI (Gemini)",
        "name_en": "Google Gemini",
        "icon": "google.png",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": [
            {
                "id": "gemini-3.1-pro-preview",
                "name": "Gemini 3.1 Pro",
                "context": 1048576,
                "tag": ["最新", "reasoning"],
            },
            {
                "id": "gemini-3-flash",
                "name": "Gemini 3 Flash",
                "context": 1048576,
                "tag": "推荐",
            },
            {
                "id": "gemini-3.1-flash-lite-preview",
                "name": "Gemini 3.1 Flash Lite",
                "context": 1048576,
                "tag": "高速",
            },
        ],
        "key_prefix": "",
        "litellm_prefix": "gemini",
        "console_url": "https://aistudio.google.com/apikey",
        "pricing_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "models_url": "https://ai.google.dev/gemini-api/docs/models",
    },
    "mistral": {
        "name": "Mistral AI",
        "name_en": "Mistral AI",
        "icon": "mistral.png",
        "base_url": "https://api.mistral.ai",
        "models": [
            {
                "id": "mistral-medium-latest",
                "name": "Mistral Medium 3.5",
                "context": 262144,
                "tag": "最新",
            },
            {
                "id": "mistral-large-latest",
                "name": "Mistral Large 3",
                "context": 262144,
                "tag": "旗舰",
            },
            {
                "id": "mistral-small-latest",
                "name": "Mistral Small 4",
                "context": 131072,
                "tag": "推荐",
            },
            {
                "id": "magistral-medium-latest",
                "name": "Magistral Medium",
                "context": 131072,
                "tag": ["推理增强", "reasoning"],
            },
            {
                "id": "devstral-latest",
                "name": "Devstral 2",
                "context": 262144,
                "tag": "代码专用",
            },
        ],
        "key_prefix": "",
        "litellm_prefix": "mistral",
        "console_url": "https://console.mistral.ai/api-keys/",
        "pricing_url": "https://mistral.ai/pricing",
        "models_url": "https://docs.mistral.ai/models/overview",
    },
    "custom": {
        "name": "自定义供应商",
        "name_en": "Custom Provider",
        "icon": "custom.png",
        "base_url": "",
        "models": [],
        "key_prefix": "",
        "litellm_prefix": "openai",
        "console_url": "",
        "pricing_url": "",
        "models_url": "",
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


def get_model_info(provider_id: str, model_id: str) -> dict:
    """获取模型信息"""
    provider = get_provider_by_id(provider_id)
    for model in provider.get("models", []):
        if model["id"] == model_id:
            return model
    return {"id": model_id, "name": model_id, "context": 0}


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


def get_display_tag(tag: str | list[str]) -> str:
    """Get the display tag from a model's tag field.

    The tag field can be either a string or a list of strings.
    For lists, returns the first non-internal tag (skipping "reasoning" etc.).
    For strings, returns as-is.

    Args:
        tag: Model tag value (string or list of strings)

    Returns:
        Display-friendly tag string
    """
    if isinstance(tag, list):
        # Return first non-internal tag for display
        internal_tags = {"reasoning"}
        display_tags = [t for t in tag if t not in internal_tags]
        return display_tags[0] if display_tags else ""
    return tag
