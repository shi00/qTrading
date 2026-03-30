# LLM 供应商模型列表全面刷新总结

**变更文件：** [llm_providers.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/utils/llm_providers.py)
**验证日期：** 2026-03-29

## 变更概览

根据各供应商官方 API 文档的最新调研，全面更新了 10 个 LLM 供应商的模型配置数据。

```diff:llm_providers.py
"""
LLM Provider Configuration Data

数据来源说明:
- 验证日期: 2026-03-23
- 数据来源: 各供应商官方 API 文档和定价页面
- 更新策略:
  1. 用户可通过"刷新模型列表"按钮动态获取最新模型
  2. 用户可手动输入任意模型 ID
  3. 开发团队定期同步各供应商最新模型列表
- 注意: 以下为静态默认列表，可能不是最新。建议使用动态刷新功能获取实时模型列表。
"""

AZURE_DEFAULT_API_VERSION = "2024-12-01-preview"

AZURE_API_VERSIONS = [
    "2024-12-01-preview",
    "2024-08-01-preview",
    "2024-06-01",
]

LLM_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "name_en": "DeepSeek",
        "icon": "deepseek.png",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "context": 64000, "tag": "推荐"},
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "context": 64000},
        ],
        "key_prefix": "sk-",
        "console_url": "https://platform.deepseek.com/api_keys",
        "pricing_url": "https://platform.deepseek.com/api-docs/pricing/",
        "models_url": "https://platform.deepseek.com/api-docs/",
    },
    "qwen": {
        "name": "阿里云通义千问",
        "name_en": "Alibaba Qwen",
        "icon": "qwen.png",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3.5-plus", "name": "Qwen3.5 Plus", "context": 131072, "tag": "最新"},
            {"id": "qwen3.5-max", "name": "Qwen3.5 Max", "context": 131072},
            {"id": "qwen-turbo", "name": "Qwen Turbo", "context": 131072},
            {"id": "qwen-plus", "name": "Qwen Plus", "context": 131072},
            {"id": "qwen-max", "name": "Qwen Max", "context": 32768},
        ],
        "key_prefix": "sk-",
        "console_url": "https://dashscope.console.aliyun.com/apiKey",
        "pricing_url": "https://help.aliyun.com/zh/dashscope/developer-reference/billing",
        "models_url": "https://help.aliyun.com/zh/dashscope/developer-reference/model-introduction",
    },
    "zhipu": {
        "name": "智谱 AI",
        "name_en": "Zhipu AI",
        "icon": "zhipu.png",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-5-plus", "name": "GLM-5 Plus", "context": 131072, "tag": "最新"},
            {"id": "glm-5-flash", "name": "GLM-5 Flash", "context": 131072, "tag": "推荐"},
            {"id": "glm-4-plus", "name": "GLM-4 Plus", "context": 131072},
            {"id": "glm-4-flash", "name": "GLM-4 Flash", "context": 131072},
        ],
        "key_prefix": "",
        "console_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "pricing_url": "https://open.bigmodel.cn/pricing",
        "models_url": "https://open.bigmodel.cn/dev/api",
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "name_en": "Moonshot AI",
        "icon": "moonshot.png",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "moonshot-v1-128k", "name": "Moonshot V1 128K", "context": 131072, "tag": "推荐"},
            {"id": "moonshot-v1-32k", "name": "Moonshot V1 32K", "context": 32768},
            {"id": "moonshot-v1-8k", "name": "Moonshot V1 8K", "context": 8192},
        ],
        "key_prefix": "sk-",
        "console_url": "https://platform.moonshot.cn/console/api-keys",
        "pricing_url": "https://platform.moonshot.cn/docs/pricing",
        "models_url": "https://platform.moonshot.cn/docs/intro",
    },
    "minimax": {
        "name": "MiniMax",
        "name_en": "MiniMax",
        "icon": "minimax.png",
        "base_url": "https://api.minimax.chat/v1",
        "models": [
            {"id": "MiniMax-Text-01", "name": "MiniMax Text 01", "context": 245000, "tag": "最新"},
            {"id": "abab6.5s-chat", "name": "ABAB 6.5S Chat", "context": 245000},
            {"id": "abab6.5-chat", "name": "ABAB 6.5 Chat", "context": 245000},
            {"id": "abab5.5-chat", "name": "ABAB 5.5 Chat", "context": 16384},
        ],
        "key_prefix": "",
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
            {"id": "gpt-5", "name": "GPT-5", "context": 200000, "tag": "最新"},
            {"id": "gpt-4.5-turbo", "name": "GPT-4.5 Turbo", "context": 128000},
            {"id": "gpt-4o", "name": "GPT-4o", "context": 128000, "tag": "推荐"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "context": 128000},
            {"id": "o3-mini", "name": "o3 Mini", "context": 200000, "tag": "推理增强"},
            {"id": "o1", "name": "o1", "context": 200000, "tag": "推理增强"},
        ],
        "key_prefix": "sk-",
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
            {"id": "claude-4-opus", "name": "Claude 4 Opus", "context": 200000, "tag": "最新"},
            {"id": "claude-4-sonnet", "name": "Claude 4 Sonnet", "context": 200000, "tag": "推荐"},
            {"id": "claude-3.7-sonnet", "name": "Claude 3.7 Sonnet", "context": 200000},
            {"id": "claude-3.5-haiku", "name": "Claude 3.5 Haiku", "context": 200000},
        ],
        "key_prefix": "sk-ant-",
        "console_url": "https://console.anthropic.com/settings/keys",
        "pricing_url": "https://www.anthropic.com/pricing",
        "models_url": "https://docs.anthropic.com/claude/docs/models-overview",
    },
    "google": {
        "name": "Google AI (Gemini)",
        "name_en": "Google Gemini",
        "icon": "google.png",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": [
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "context": 1048576, "tag": "最新"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "context": 1048576, "tag": "推荐"},
            {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "context": 2097152},
            {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "context": 1048576},
        ],
        "key_prefix": "",
        "console_url": "https://aistudio.google.com/apikey",
        "pricing_url": "https://ai.google.dev/pricing",
        "models_url": "https://ai.google.dev/gemini-api/docs/models",
    },
    "mistral": {
        "name": "Mistral AI",
        "name_en": "Mistral AI",
        "icon": "mistral.png",
        "base_url": "https://api.mistral.ai",
        "models": [
            {"id": "mistral-large-latest", "name": "Mistral Large", "context": 128000, "tag": "最新"},
            {"id": "mistral-medium-latest", "name": "Mistral Medium", "context": 128000},
            {"id": "mistral-small-latest", "name": "Mistral Small", "context": 128000, "tag": "推荐"},
            {"id": "codestral-latest", "name": "Codestral", "context": 256000, "tag": "代码专用"},
        ],
        "key_prefix": "",
        "console_url": "https://console.mistral.ai/api-keys/",
        "pricing_url": "https://mistral.ai/pricing",
        "models_url": "https://docs.mistral.ai/getting-started/models/",
    },
    "custom": {
        "name": "自定义供应商",
        "name_en": "Custom Provider",
        "icon": "custom.png",
        "base_url": "",
        "models": [],
        "key_prefix": "",
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
    return {"id": model_id, "name": model_id, "context": 4096}


def get_all_providers() -> dict:
    """获取所有供应商"""
    return LLM_PROVIDERS


def get_providers_by_category(category: str) -> list:
    """获取分类下的供应商列表"""
    provider_ids = PROVIDER_CATEGORIES.get(category, [])
    return [LLM_PROVIDERS[pid] for pid in provider_ids if pid in LLM_PROVIDERS]


import os
from pathlib import Path


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
===
"""
LLM Provider Configuration Data

数据来源说明:
- 验证日期: 2026-03-29
- 数据来源: 各供应商官方 API 文档和定价页面
- 更新策略:
  1. 用户可通过"刷新模型列表"按钮动态获取最新模型
  2. 用户可手动输入任意模型 ID
  3. 开发团队定期同步各供应商最新模型列表
- 注意: 以下为静态默认列表，可能不是最新。建议使用动态刷新功能获取实时模型列表。
"""

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
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner (V3.2)", "context": 64000, "tag": "推荐"},
            {"id": "deepseek-chat", "name": "DeepSeek Chat (V3.2)", "context": 64000},
        ],
        "key_prefix": "sk-",
        "console_url": "https://platform.deepseek.com/api_keys",
        "pricing_url": "https://platform.deepseek.com/api-docs/pricing/",
        "models_url": "https://platform.deepseek.com/api-docs/",
    },
    "qwen": {
        "name": "阿里云通义千问",
        "name_en": "Alibaba Qwen",
        "icon": "qwen.png",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen-max", "name": "Qwen Max", "context": 32768, "tag": "旗舰"},
            {"id": "qwen-plus", "name": "Qwen Plus (Qwen3.5)", "context": 131072, "tag": "推荐"},
            {"id": "qwen-turbo", "name": "Qwen Turbo", "context": 131072},
            {"id": "qwen-flash", "name": "Qwen Flash", "context": 131072, "tag": "高速"},
        ],
        "key_prefix": "sk-",
        "console_url": "https://dashscope.console.aliyun.com/apiKey",
        "pricing_url": "https://help.aliyun.com/zh/dashscope/developer-reference/billing",
        "models_url": "https://help.aliyun.com/zh/dashscope/developer-reference/model-introduction",
    },
    "zhipu": {
        "name": "智谱 AI",
        "name_en": "Zhipu AI",
        "icon": "zhipu.png",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": [
            {"id": "glm-5", "name": "GLM-5", "context": 200000, "tag": "旗舰"},
            {"id": "glm-5-plus", "name": "GLM-5 Plus", "context": 131072, "tag": "推荐"},
            {"id": "glm-5-flash", "name": "GLM-5 Flash", "context": 131072, "tag": "高速"},
            {"id": "glm-4-plus", "name": "GLM-4 Plus", "context": 131072},
            {"id": "glm-4-flash", "name": "GLM-4 Flash", "context": 131072},
        ],
        "key_prefix": "",
        "console_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "pricing_url": "https://open.bigmodel.cn/pricing",
        "models_url": "https://open.bigmodel.cn/dev/api",
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "name_en": "Moonshot AI",
        "icon": "moonshot.png",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "kimi-k2.5", "name": "Kimi K2.5", "context": 262144, "tag": "推荐"},
            {"id": "moonshot-v1-128k", "name": "Moonshot V1 128K", "context": 131072, "tag": "旧版"},
            {"id": "moonshot-v1-32k", "name": "Moonshot V1 32K", "context": 32768},
        ],
        "key_prefix": "sk-",
        "console_url": "https://platform.moonshot.cn/console/api-keys",
        "pricing_url": "https://platform.moonshot.cn/docs/pricing",
        "models_url": "https://platform.moonshot.cn/docs/intro",
    },
    "minimax": {
        "name": "MiniMax",
        "name_en": "MiniMax",
        "icon": "minimax.png",
        "base_url": "https://api.minimaxi.com/v1",
        "models": [
            {"id": "MiniMax-M2.7", "name": "MiniMax M2.7", "context": 245000, "tag": "最新"},
            {"id": "MiniMax-M2.5", "name": "MiniMax M2.5", "context": 245000, "tag": "推荐"},
            {"id": "MiniMax-Text-01", "name": "MiniMax Text 01", "context": 245000},
        ],
        "key_prefix": "",
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
            {"id": "gpt-5.4", "name": "GPT-5.4", "context": 1000000, "tag": "最新"},
            {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "context": 1000000, "tag": "推荐"},
            {"id": "gpt-4o", "name": "GPT-4o", "context": 128000},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "context": 128000},
            {"id": "o3-mini", "name": "o3 Mini", "context": 200000, "tag": "推理增强"},
        ],
        "key_prefix": "sk-",
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
            {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "context": 1000000, "tag": "旗舰"},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context": 1000000, "tag": "推荐"},
            {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "context": 1000000, "tag": "代码专精"},
            {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "context": 200000, "tag": "高速"},
        ],
        "key_prefix": "sk-ant-",
        "console_url": "https://console.anthropic.com/settings/keys",
        "pricing_url": "https://www.anthropic.com/pricing",
        "models_url": "https://docs.anthropic.com/claude/docs/models-overview",
    },
    "google": {
        "name": "Google AI (Gemini)",
        "name_en": "Google Gemini",
        "icon": "google.png",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": [
            {"id": "gemini-3.1-pro", "name": "Gemini 3.1 Pro", "context": 1048576, "tag": "最新"},
            {"id": "gemini-3-flash", "name": "Gemini 3 Flash", "context": 1048576, "tag": "推荐"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "context": 1048576},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "context": 1048576},
        ],
        "key_prefix": "",
        "console_url": "https://aistudio.google.com/apikey",
        "pricing_url": "https://ai.google.dev/pricing",
        "models_url": "https://ai.google.dev/gemini-api/docs/models",
    },
    "mistral": {
        "name": "Mistral AI",
        "name_en": "Mistral AI",
        "icon": "mistral.png",
        "base_url": "https://api.mistral.ai",
        "models": [
            {"id": "mistral-large-latest", "name": "Mistral Large 3", "context": 128000, "tag": "旗舰"},
            {"id": "mistral-small-latest", "name": "Mistral Small 4", "context": 128000, "tag": "推荐"},
            {"id": "magistral-medium-latest", "name": "Magistral Medium", "context": 128000, "tag": "推理增强"},
            {"id": "codestral-latest", "name": "Codestral 2", "context": 256000, "tag": "代码专用"},
        ],
        "key_prefix": "",
        "console_url": "https://console.mistral.ai/api-keys/",
        "pricing_url": "https://mistral.ai/pricing",
        "models_url": "https://docs.mistral.ai/getting-started/models/",
    },
    "custom": {
        "name": "自定义供应商",
        "name_en": "Custom Provider",
        "icon": "custom.png",
        "base_url": "",
        "models": [],
        "key_prefix": "",
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
    return {"id": model_id, "name": model_id, "context": 4096}


def get_all_providers() -> dict:
    """获取所有供应商"""
    return LLM_PROVIDERS


def get_providers_by_category(category: str) -> list:
    """获取分类下的供应商列表"""
    provider_ids = PROVIDER_CATEGORIES.get(category, [])
    return [LLM_PROVIDERS[pid] for pid in provider_ids if pid in LLM_PROVIDERS]


import os
from pathlib import Path


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
```

## 各供应商量化交易推荐模型

> [!TIP]
> 每个供应商已标注一个**"推荐"**标签模型，推荐依据为**推理能力、上下文窗口、性价比**三个维度的综合考量，专为 A 股量化分析场景优选。

| 供应商 | 推荐模型 | 推荐理由 |
|:------|:--------|:--------|
| DeepSeek | Reasoner (V3.2) | 链式推理专长，适合复杂量化逻辑分析 |
| 通义千问 | Qwen Plus (Qwen3.5) | 131K 上下文 + 性价比极佳，适合批量分析 |
| 智谱 AI | GLM-5 Plus | 131K 上下文 + 强推理，国产模型中的平衡之选 |
| Moonshot | Kimi K2.5 | 256K 超长上下文，适合处理大量财务报告 |
| MiniMax | M2.5 | 245K 上下文 + 成熟稳定，适合生产环境 |
| OpenAI | GPT-5.4 Mini | 1M 上下文 + 高性价比，适合高频调用 |
| Anthropic | Sonnet 4.6 | 1M 上下文 + 近旗舰级推理，日常量化首选 |
| Google | Gemini 3 Flash | 1M 上下文 + 低延迟，适合实时分析 |
| Mistral | Small 4 | 128K 上下文 + 多模态推理，欧洲合规友好 |

## 关键变更项

- **新增模型：** Kimi K2.5、MiniMax M2.7/M2.5、GPT-5.4 系列、Claude Opus/Sonnet 4.6、Gemini 3.1 Pro、Magistral Medium
- **移除已退役：** moonshot-v1-8k、gpt-5、gpt-4.5-turbo、o1、claude-4-opus/sonnet、claude-3.7-sonnet、gemini-1.5 系列、mistral-medium-latest、ABAB 系列
- **base_url 修正：** MiniMax 从 `api.minimax.chat` 更新为 `api.minimaxi.com`
- **Azure API 版本：** 更新至 `2025-04-01-preview`
