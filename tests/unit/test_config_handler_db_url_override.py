from unittest.mock import patch

from utils.config_handler import ConfigHandler


def test_get_db_url_prefers_embedded_url_override():
    """embedded 模块级 override 命中时，get_db_url() 原样返回该完整 URL（不重建）。

    真实 embedded 运行时 main.py 会 pop 残留的 DATABASE_URL env var 后才注入
    模块级 override（见 test_main_bootstrap_order.py 的 p3 断言），因此此处清空
    DATABASE_URL 以模拟该真实状态，避免被 P1 env var 干扰。
    """
    embedded = "postgresql+asyncpg://postgres:***@127.0.0.1:23500/qtrading"
    ConfigHandler.set_embedded_db_url(embedded)
    try:
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            url = ConfigHandler.get_db_url()
        assert url == embedded, f"应原样返回 embedded URL（不重建），实际 {url}"
    finally:
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
