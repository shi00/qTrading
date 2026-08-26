"""启动流程纯类型（review01-A3 从 app 层下沉）。

``StartupState`` / ``StartupContext`` / ``EmbeddedPgStartupScenario`` 为纯数据类型
（Enum / dataclass），被 app 层（``StartupController``）与 ui 层（``startup_views``）
共享。下沉到 core 层解除 ``ui → app`` 反向依赖（契约 6）：ui 层从 core 导入类型，
app 层从 core 导入并保持导出（``app.bootstrap`` / ``app.startup_controller`` 兼容旧引用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class EmbeddedPgStartupScenario(Enum):
    """Embedded PostgreSQL 启动场景（UX 改进 spec §启动侧方案 A）。

    用于 LoadingView 差异化文案：
    - ``FIRST_RUN``: 首次启动（需解压 bundled binaries + initdb，预计 30-60s）
    - ``NORMAL``: 普通启动（仅 PG 启动+健康检查，预计 2-5s）
    - ``UNKNOWN``: 异常状态（marker 与 PG_VERSION 不一致，保守按 NORMAL 文案显示）
    """

    FIRST_RUN = "first_run"
    NORMAL = "normal"
    UNKNOWN = "unknown"


class StartupState(Enum):
    """Startup flow state machine 状态枚举。"""

    LOADING = auto()
    NEED_UPGRADE = auto()
    UPGRADE_IN_PROGRESS = auto()
    UPGRADE_SUCCESS = auto()
    UPGRADE_FAILED = auto()
    INIT_FAILED = auto()
    NEED_ONBOARDING = auto()
    READY = auto()


@dataclass
class StartupContext:
    """Extra context passed alongside state transitions."""

    error: str | None = None
    detail: str | None = None
    current_rev: str | None = None
    head_rev: str | None = None
    # UX 改进 spec §启动侧方案 A：embedded PG 启动场景，由 main.py 在
    # prepare_database_runtime 之前 detect 后注入，供 LoadingView 显示差异化文案。
    # None 表示 external 模式或未启用 embedded PG（显示原有 "Initializing..." 文案）。
    embedded_pg_scenario: EmbeddedPgStartupScenario | None = None
