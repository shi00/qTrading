"""UIR-F15-001: 设置页切换为外置模式后「重启生效」的连线测试。

Given 默认内置模式运行，高级用户选择了外置实例 B 并保存（db_mode=external），
When 应用重启（env 未设），Then：
- 内置 sidecar 不启动；
- 业务 engine 与数据浏览 engine 均指向 B；
- 模式身份判定为外置（FR-DB-001「切换后可正常读写」的取数前提）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.config_models import get_default_config

pytestmark = pytest.mark.unit

EXTERNAL_HOST = "10.0.0.2"
EXTERNAL_PORT = 6543
EXTERNAL_USER = "user_b"
EXTERNAL_PASSWORD = "secret_b"
EXTERNAL_DATABASE = "stock_b"
EXTERNAL_URL = (
    f"postgresql+asyncpg://{EXTERNAL_USER}:{EXTERNAL_PASSWORD}@{EXTERNAL_HOST}:{EXTERNAL_PORT}/{EXTERNAL_DATABASE}"
)


def _external_config() -> dict:
    config = get_default_config()
    config.update(
        {
            "db_mode": "external",
            "embedded_pg_enabled": False,
            "db_host": EXTERNAL_HOST,
            "db_port": EXTERNAL_PORT,
            "db_user": EXTERNAL_USER,
            "db_name": EXTERNAL_DATABASE,
        }
    )
    return config


@pytest.mark.asyncio(loop_scope="function")
async def test_restart_in_external_mode_skips_sidecar_and_points_engines_to_target(monkeypatch) -> None:
    monkeypatch.delenv("QTRADING_DATABASE_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("utils.config_handler.ConfigHandler.load_config", staticmethod(_external_config))
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.get_db_password",
        staticmethod(lambda: EXTERNAL_PASSWORD),
    )
    monkeypatch.setattr("utils.config_handler.ConfigHandler._embedded_db_url", None)

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    from_config = MagicMock()
    monkeypatch.setattr(
        EmbeddedPostgresService,
        "from_config",
        classmethod(lambda cls, cfg: from_config(cfg)),
    )

    runtime_url = await prepare_database_runtime()

    assert runtime_url is None
    from_config.assert_not_called()

    from utils.config_handler import ConfigHandler

    assert ConfigHandler.is_embedded_mode() is False

    # 业务 engine：EngineManager 经 ConfigHandler.get_db_url() 解析到外置实例 B
    from data.cache.engine_manager import EngineManager

    assert EngineManager().get_connection_string() == EXTERNAL_URL

    # 浏览 engine：DataExplorerQueryClient 使用同一 URL（同步驱动）建共享引擎
    from data.persistence import data_explorer_query_client as deq

    created_urls: list[str] = []
    monkeypatch.setattr(
        deq.sa,
        "create_engine",
        lambda url, **kwargs: created_urls.append(url) or MagicMock(),
    )
    monkeypatch.setattr(deq.DataExplorerQueryClient, "_shared_engine", None)

    deq.DataExplorerQueryClient()._ensure_engine()

    assert created_urls == [EXTERNAL_URL.replace("+asyncpg", "")]
