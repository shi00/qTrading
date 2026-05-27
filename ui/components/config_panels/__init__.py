"""
Shared Configuration Panels

Provides reusable UI components for configuration
used by both onboarding wizard and settings page.

Components:
- DatabaseConfigPanel: Database connection configuration
- FailoverConfigPanel: LLM failover provider configuration
- LLMConfigPanel: Cloud LLM provider configuration
- LocalModelConfigPanel: Local GGUF model configuration
- TushareConfigPanel: Tushare Token configuration
"""

from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.components.config_panels.failover_config_panel import FailoverConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel

__all__ = [
    "DatabaseConfigPanel",
    "FailoverConfigPanel",
    "LLMConfigPanel",
    "LocalModelConfigPanel",
    "TushareConfigPanel",
]
