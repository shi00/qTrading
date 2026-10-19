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
- ModelConnectionForm: 模型连接共用表单（S5 双入口，连接字段组 + 凭据 + gguf 路径）
- ScenarioRouteEditor: 共用场景路由编辑器（S5 §11.4，三场景候选编辑）
- TushareConfigPanel: Tushare Token configuration
"""

from ui.components.config_panels.backup_restore_panel import BackupRestorePanel
from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.components.config_panels.database_status_panel import DatabaseStatusPanel
from ui.components.config_panels.embedded_status_card import EmbeddedStatusCard
from ui.components.config_panels.external_pg_form import ExternalPgForm
from ui.components.config_panels.model_connection_form import ModelConnectionForm
from ui.components.config_panels.scenario_route_editor import ScenarioRouteEditor
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel

__all__ = [
    "BackupRestorePanel",
    "DatabaseConfigPanel",
    "DatabaseStatusPanel",
    "EmbeddedStatusCard",
    "ExternalPgForm",
    "ModelConnectionForm",
    "ScenarioRouteEditor",
    "TushareConfigPanel",
]
