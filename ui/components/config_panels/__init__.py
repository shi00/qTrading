"""
Shared Configuration Panels

Provides reusable UI components for configuration
used by both onboarding wizard and settings page.

Components:
- BackupRestorePanel: Database backup/restore panel with wizard (P3-11)
- DatabaseConfigPanel: Database configuration router (embedded/external)
- DatabaseStatusPanel: Embedded PostgreSQL status panel with refresh/open-dir commands (P3-10)
- EmbeddedStatusCard: Embedded PostgreSQL read-only status card (P3-9)
- ExternalPgForm: External PostgreSQL host/port/user/password form (P3-9)
- FailoverConfigPanel: LLM failover provider configuration
- LLMConfigPanel: Cloud LLM provider configuration
- LocalModelConfigPanel: Local GGUF model configuration
- TushareConfigPanel: Tushare Token configuration
"""

from ui.components.config_panels.backup_restore_panel import BackupRestorePanel
from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.components.config_panels.database_status_panel import DatabaseStatusPanel
from ui.components.config_panels.embedded_status_card import EmbeddedStatusCard
from ui.components.config_panels.external_pg_form import ExternalPgForm
from ui.components.config_panels.failover_config_panel import FailoverConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel

__all__ = [
    "BackupRestorePanel",
    "DatabaseConfigPanel",
    "DatabaseStatusPanel",
    "EmbeddedStatusCard",
    "ExternalPgForm",
    "FailoverConfigPanel",
    "LLMConfigPanel",
    "LocalModelConfigPanel",
    "TushareConfigPanel",
]
