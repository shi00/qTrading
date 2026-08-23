from unittest.mock import patch

from utils.config_handler import ConfigHandler


def test_get_db_url_prefers_embedded_url_override_over_stale_env():
    """embedded(P1.5) 高于残留 env(P1)：即使 DATABASE_URL 残留废弃主机，
    embedded URL 也原样胜出（不重建）。钉死 priority 0/1.5/2 契约，修复后的
    优先级顺序使 embedded 运行时不再依赖 main.py 对 DATABASE_URL 的 pop。"""
    embedded = "postgresql+asyncpg://postgres:***@127.0.0.1:23500/qtrading"
    stale_env = "postgresql+asyncpg://postgres:***@stale-host:5432/wrongdb"
    ConfigHandler.set_embedded_db_url(embedded)
    try:
        with patch.dict("os.environ", {"DATABASE_URL": stale_env}):
            url = ConfigHandler.get_db_url()
        assert url == embedded, f"embedded 应恒胜残留 env(P1)，实际 {url}"
    finally:
        ConfigHandler.clear_embedded_db_url()


def test_get_db_url_contextvar_override_still_wins_over_embedded():
    """P0(ContextVar) 仍最高优先级，恒胜 P1.5(embedded) 模块级 override。"""
    contextvar_url = "postgresql+asyncpg://postgres:***@ctx-host:5433/ctxdb"
    embedded = "postgresql+asyncpg://postgres:***@127.0.0.1:23500/qtrading"
    ConfigHandler.set_embedded_db_url(embedded)
    token = ConfigHandler._db_url_override.set(contextvar_url)
    try:
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            url = ConfigHandler.get_db_url()
        assert url == contextvar_url, f"ContextVar(P0) 应胜 embedded(P1.5)，实际 {url}"
    finally:
        ConfigHandler._db_url_override.reset(token)
        ConfigHandler.clear_embedded_db_url()


def test_get_db_url_unaffected_when_override_empty():
    """override 未启用（None）时，走 P2 组件重建并返回 mock 的确定性 components URL（external 行为不变）。"""
    ConfigHandler.clear_embedded_db_url()
    components_url = "postgresql+asyncpg://persisted_user:persisted_pass@onboarding-host:5432/astock"

    def _mock_get_typed(key, typ, default):
        return {
            "db_host": "onboarding-host",
            "db_port": 5432,
            "db_user": "persisted_user",
            "db_name": "astock",
        }.get(key, default)

    with (
        patch.dict("os.environ", {"DATABASE_URL": ""}),
        patch.object(ConfigHandler, "get_typed", side_effect=_mock_get_typed),
        patch.object(ConfigHandler, "get_db_password", return_value="persisted_pass"),
        patch(
            "data.persistence.db_config_service.DatabaseConfigService.build_url",
            return_value=components_url,
        ),
    ):
        assert ConfigHandler.get_db_url() == components_url
