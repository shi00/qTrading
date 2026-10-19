"""
Integration Tests for Onboarding Wizard Configuration Panels

声明式重写后，组件实例化需要 Renderer 上下文，集成测试改为直接测试
ViewModel 的业务逻辑（连接测试、配置保存、验证），覆盖原命令式 API 场景。

- DatabaseConfigPanelViewModel: get_config / validate / save_config / on_change
- ModelConnectionsViewModel / ModelRoutesViewModel: 与真实配置域（隔离配置文件 +
  ConfigHandler + ModelConfigurationService）交互，覆盖 CAS、凭据引用与两宿主
  提交范围读回（reviews/模型统一.md §13.2）
- DatabaseConfigService: run_migrations / ensure_tables_exist（service 层，保留真实 DB）
"""

import os
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from data.persistence.db_url_override import override_db_url
from services.model_configuration_service import ModelConfigurationService
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params
from tests.integration.test_data_db_migrator import (
    _create_isolated_db,
    _drop_isolated_db,
)
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel
from ui.viewmodels.model_connections_view_model import ModelConnectionsViewModel
from ui.viewmodels.model_routes_view_model import ModelRoutesViewModel
from utils.config_models import SCENARIO_IDS

# no_db: 跳过 db_schema_ready 中的 test_engine 加载（本文件的 DB 测试使用 isolated_db
# 自建独立 DB，无需共享 test_engine；UI/VM 测试不需要 DB）
pytestmark = [pytest.mark.integration, pytest.mark.no_db]


@pytest.fixture
def isolated_config(tmp_path):
    """Create an isolated config file for each test with cache cleared"""
    import utils.config_handler as config_module
    from utils.config_handler import ConfigHandler

    unique_name = f"test_config_{uuid.uuid4().hex}.json"
    test_config_file = str(tmp_path / unique_name)
    original_config_file = config_module.CONFIG_FILE
    config_module.CONFIG_FILE = test_config_file

    ConfigHandler._clear_cache()

    if os.path.exists(test_config_file):
        os.remove(test_config_file)

    yield test_config_file

    ConfigHandler._clear_cache()
    config_module.CONFIG_FILE = original_config_file


@pytest.fixture
def mock_db_thread_pool():
    """Mock DatabaseConfigPanelViewModel 模块的 ThreadPoolManager.run_async 为同步 passthrough。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_passthrough)
    with patch(
        "ui.viewmodels.database_config_panel_view_model.ThreadPoolManager",
        return_value=mock_tpm,
    ):
        yield mock_tpm


@pytest.fixture
def model_config_service(isolated_config):  # noqa: ARG001 - 确保 isolated_config 激活
    """真实配置域：隔离配置文件 + 默认 model_management + 真实配置服务。"""
    from utils.config_handler import ConfigHandler

    ConfigHandler.ensure_defaults()
    return ModelConfigurationService()


def _make_db_vm(
    isolated_config,  # noqa: ARG001 - 确保 isolated_config fixture 激活
    *,
    on_save_callback: Callable | None = None,
    on_test_success_callback: Callable | None = None,
    on_change: Callable | None = None,
    on_loading_change: Callable[[bool], None] | None = None,
    load_password: bool = False,
) -> DatabaseConfigPanelViewModel:
    """构造 DatabaseConfigPanelViewModel（在 isolated_config 上下文中调用）。"""
    return DatabaseConfigPanelViewModel(
        on_save_callback=on_save_callback,
        on_test_success_callback=on_test_success_callback,
        on_change=on_change,
        on_loading_change=on_loading_change,
        load_password=load_password,
    )


def _add_litellm_connection(
    vm: ModelConnectionsViewModel, *, name: str = "官方 API", api_key: str | None = None
) -> None:
    """在连接 VM 上填写一个合法 litellm 连接草稿。"""
    vm.start_add()
    vm.update_name(name)
    vm.update_model("deepseek-chat")
    vm.update_provider("deepseek")
    vm.update_api_base("https://api.deepseek.com")
    if api_key is not None:
        vm.update_credential(api_key)


async def _seed_connection(config_service: ModelConfigurationService) -> str:
    """经向导阶段提交落一个连接（宿主 A），返回已落盘 connection_id。"""
    from ui.views.onboarding_wizard import _commit_model_stage
    from utils.config_handler import ConfigHandler

    conn_vm = ModelConnectionsViewModel(config_service)
    routes_vm = ModelRoutesViewModel(config_service)
    await conn_vm.load()
    await routes_vm.load()
    _add_litellm_connection(conn_vm, name="Conn")
    assert await _commit_model_stage(conn_vm, routes_vm, config_service) is True
    disk = ConfigHandler.read_model_management_fresh()["model_management"]
    return str(disk["connections"][0]["connection_id"])


class TestDatabaseConfigPanel:
    """Tests for DatabaseConfigPanelViewModel business logic"""

    def test_get_config(self, isolated_config):
        """Test get_config returns correct structure"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")

        config = vm.get_config()

        assert config["host"] == "localhost"
        assert config["port"] == 5432
        assert config["user"] == "postgres"
        assert config["password"] == "password"
        assert config["database"] == "testdb"

    def test_validate_empty_host(self, isolated_config):
        """Test validation fails with empty host"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")

        is_valid, error = vm.validate()

        assert is_valid is False
        assert error is not None

    def test_validate_invalid_port(self, isolated_config):
        """Test validation fails with invalid port"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("invalid")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")

        is_valid, _ = vm.validate()

        assert is_valid is False

    def test_validate_empty_database(self, isolated_config):
        """Test validation fails with empty database name"""
        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("")

        is_valid, _ = vm.validate()

        assert is_valid is False


class TestConfigPanelsIntegration:
    """Integration tests for configuration panels on_change callback"""

    def test_database_panel_on_change_callback(self, isolated_config):
        """Test on_change callback is triggered when VM fields update"""
        callback_called = []

        def on_change():
            callback_called.append(True)

        vm = _make_db_vm(isolated_config, on_change=on_change)
        vm.update_host("newhost")

        assert len(callback_called) > 0


class TestModelDomainIntegration:
    """共用连接 / 路由 VM 与真实配置域（reviews/模型统一.md §13.2）。

    覆盖：CAS 冲突、凭据引用落盘、两宿主提交范围读回（向导阶段提交 vs 设置页
    单对象提交），全部走隔离配置文件 + 真实 ConfigHandler / ModelConfigurationService。
    """

    @pytest.mark.asyncio
    async def test_onboarding_stage_commit_reads_back_full_scope(self, model_config_service):
        """向导宿主一次原子提交连接 + 全部场景路由，落盘范围可读回。"""
        from ui.views.onboarding_wizard import _commit_model_stage
        from utils.config_handler import ConfigHandler

        conn_vm = ModelConnectionsViewModel(model_config_service)
        routes_vm = ModelRoutesViewModel(model_config_service)
        await conn_vm.load()
        await routes_vm.load()
        rev_before = conn_vm.state.config_revision

        _add_litellm_connection(conn_vm, name="官方 API")

        assert await _commit_model_stage(conn_vm, routes_vm, model_config_service) is True

        disk = ConfigHandler.read_model_management_fresh()["model_management"]
        assert disk["config_revision"] == rev_before + 1
        assert [c["name"] for c in disk["connections"]] == ["官方 API"]
        # 阶段提交范围含全部场景路由，不是只提交当前场景
        assert {r["scenario_id"] for r in disk["scenario_routes"]} == set(SCENARIO_IDS)

    @pytest.mark.asyncio
    async def test_connection_credential_ref_persisted_to_keyring(self, model_config_service):
        """新凭据经服务写入版本化 keyring 项，落盘仅存 credential_ref，草稿不回显。"""
        import utils.config_handler as cfg_mod
        from ui.views.onboarding_wizard import _commit_model_stage
        from utils.config_handler import ConfigHandler

        conn_vm = ModelConnectionsViewModel(model_config_service)
        routes_vm = ModelRoutesViewModel(model_config_service)
        await conn_vm.load()
        await routes_vm.load()
        _add_litellm_connection(conn_vm, name="官方 API", api_key="sk-integration-secret")

        assert await _commit_model_stage(conn_vm, routes_vm, model_config_service) is True

        disk = ConfigHandler.read_model_management_fresh()["model_management"]
        ref = disk["connections"][0]["credential_ref"]
        assert ref is not None
        assert ref.startswith("keyring:")
        _, item = ref.split(":", 1)
        assert cfg_mod.keyring.get_password(cfg_mod.KEYRING_SERVICE_NAME, item) == "sk-integration-secret"
        # 凭据不回显：编辑同一连接时草稿只带回引用，不回填明文
        reader = ModelConnectionsViewModel(model_config_service)
        await reader.load()
        reader.edit(str(disk["connections"][0]["connection_id"]))
        assert reader.state.draft.credential_api_key == ""
        assert reader.state.draft.credential_ref == ref

    @pytest.mark.asyncio
    async def test_settings_single_scenario_commit_scope_read_back(self, model_config_service):
        """设置页宿主只提交当前场景路由；其它场景与连接定义保持已提交值。"""
        from utils.config_handler import ConfigHandler

        conn_id = await _seed_connection(model_config_service)
        rev_before = ConfigHandler.read_model_management_fresh()["model_management"]["config_revision"]

        routes_vm = ModelRoutesViewModel(model_config_service)
        await routes_vm.load()
        routes_vm.select_scenario("stock_analysis")
        routes_vm.add_candidate(conn_id)

        await routes_vm.save_current_scenario()

        assert routes_vm.state.save_state == "committed"
        disk = ConfigHandler.read_model_management_fresh()["model_management"]
        assert disk["config_revision"] == rev_before + 1
        rows = {r["scenario_id"]: r["candidates"] for r in disk["scenario_routes"]}
        # 单对象提交范围：仅当前场景变更，其它场景保持已提交值（空候选）
        assert rows["stock_analysis"] == [conn_id]
        assert rows["news_annotation"] == []
        assert rows["news_risk"] == []
        # 连接定义不被路由提交改写
        assert [c["connection_id"] for c in disk["connections"]] == [conn_id]

    @pytest.mark.asyncio
    async def test_routes_stale_revision_conflict_keeps_disk_unchanged(self, model_config_service):
        """CAS：落后 revision 的提交冲突，保留草稿且不覆盖磁盘旧配置。"""
        from utils.config_handler import ConfigHandler

        conn_id = await _seed_connection(model_config_service)

        stale = ModelRoutesViewModel(model_config_service)
        await stale.load()

        newer = ModelRoutesViewModel(model_config_service)
        await newer.load()
        newer.select_scenario("news_risk")
        newer.add_candidate(conn_id)
        await newer.save_current_scenario()
        assert newer.state.save_state == "committed"

        stale.select_scenario("stock_analysis")
        stale.add_candidate(conn_id)
        await stale.save_current_scenario()

        assert stale.state.save_state == "conflict"
        assert stale.current_draft().candidates == (conn_id,)
        rows = {
            r["scenario_id"]: r["candidates"]
            for r in ConfigHandler.read_model_management_fresh()["model_management"]["scenario_routes"]
        }
        # 磁盘只保留成功提交的 newer 变更；stale 的草稿未落盘
        assert rows["news_risk"] == [conn_id]
        assert rows["stock_analysis"] == []

    @pytest.mark.asyncio
    async def test_two_hosts_read_back_shared_config(self, model_config_service):
        """两宿主 VM 独立，但读写同一真实配置域（双向读回）。"""
        conn_id = await _seed_connection(model_config_service)

        # 宿主 A（设置页）提交当前场景路由
        host_a_routes = ModelRoutesViewModel(model_config_service)
        await host_a_routes.load()
        host_a_routes.add_candidate(conn_id)
        await host_a_routes.save_current_scenario()
        assert host_a_routes.state.save_state == "committed"

        # 宿主 B（向导）新建 VM，读回宿主 A 的提交
        host_b_conn = ModelConnectionsViewModel(model_config_service)
        await host_b_conn.load()
        assert [r.connection_id for r in host_b_conn.state.connections] == [conn_id]

        host_b_routes = ModelRoutesViewModel(model_config_service)
        await host_b_routes.load()
        assert host_b_routes.state.selected_scenario == SCENARIO_IDS[0]
        assert host_b_routes.current_draft().candidates == (conn_id,)


class TestDatabaseConfigServiceMigrations:
    """Tests for DatabaseConfigService migration methods"""

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.database,
        pytest.mark.migration,
        pytest.mark.xdist_group("migration"),
    ]

    @pytest_asyncio.fixture(autouse=True)
    async def isolated_db(self):
        """每个 DDL 测试使用隔离数据库，避免污染共享 test_engine（INT-V2-1）。"""
        params = get_pg_connection_params()
        db_name = f"test_config_panels_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        with override_db_url(async_url):
            yield engine, db_name, params
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_run_migrations_creates_tables(self, isolated_db):
        """Test run_migrations creates all required tables"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        success, msg = await DatabaseConfigService.run_migrations(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )

        assert success is True
        assert "成功" in msg or "success" in msg.lower()

    @pytest.mark.asyncio
    async def test_run_migrations_invalid_credentials(self):
        """Test run_migrations returns False when init_db raises an exception"""
        from unittest.mock import AsyncMock, patch

        from data.persistence.db_config_service import DatabaseConfigService

        with patch(
            "data.persistence.db_migrator.DatabaseMigrator.init_db",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            success, msg = await DatabaseConfigService.run_migrations(
                host="invalid_host",
                port=5432,
                user="invalid_user",
                password="invalid_password",
                database="invalid_db",
            )

        assert success is False
        assert "failed" in msg.lower() or "失败" in msg or "error" in msg.lower() or "exception" in msg.lower()

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_empty_database(self, isolated_db):
        """Test ensure_tables_exist creates tables for empty database"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )

        assert success is True

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_existing_tables(self, isolated_db):
        """Test ensure_tables_exist skips creation when tables exist"""
        from data.persistence.db_config_service import DatabaseConfigService

        engine, db_name, params = isolated_db

        # Run migrations first so alembic_version table exists — this is the
        # authoritative marker that the database is already managed by Alembic.
        # Using Base.metadata.create_all alone does NOT create alembic_version,
        # so ensure_tables_exist would re-run migrations rather than skip.
        success1, _ = await DatabaseConfigService.run_migrations(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )
        assert success1 is True

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=db_name,
        )

        assert success is True
        assert "exist" in msg.lower() or "存在" in msg

    @pytest.mark.asyncio
    async def test_ensure_tables_exist_connection_failure(self):
        """Test ensure_tables_exist returns False on connection failure"""
        from data.persistence.db_config_service import DatabaseConfigService

        success, msg = await DatabaseConfigService.ensure_tables_exist(
            host="invalid_host",
            port=5432,
            user="invalid_user",
            password="invalid_password",
            database="invalid_db",
        )

        assert success is False


class TestDatabaseConfigPanelSaveConfig:
    """Tests for DatabaseConfigPanelViewModel.save_config with table creation"""

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.database,
        pytest.mark.migration,
        pytest.mark.xdist_group("migration"),
    ]

    @pytest_asyncio.fixture(autouse=True)
    async def isolated_db(self):
        """每个 DDL 测试使用隔离数据库，避免污染共享 test_engine（INT-V2-1）。"""
        params = get_pg_connection_params()
        db_name = f"test_config_panels_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        with override_db_url(async_url):
            yield engine, db_name, params
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_save_config_calls_ensure_tables_exist(self, isolated_config, isolated_db, mock_db_thread_pool):
        """Test save_config calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        vm = _make_db_vm(isolated_config)
        vm.update_host(params["host"])
        vm.update_port(str(params["port"]))
        vm.update_user(params["user"])
        vm.update_password(params["password"])
        vm.update_database(db_name)
        vm.update_create_if_not_exists(False)

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (True, "Tables created")

            result = await vm.save_config()

        assert result is True
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_handles_table_creation_failure(self, isolated_config, mock_db_thread_pool):
        """Test save_config returns False when table creation fails"""
        from data.persistence.db_config_service import DatabaseConfigService

        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")
        vm.update_create_if_not_exists(False)

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (False, "Table creation failed")

            result = await vm.save_config()

        assert result is False
        mock_ensure.assert_called_once()


class TestOnboardingWizardDatabaseValidation:
    """Tests for OnboardingWizard database validation via DatabaseConfigPanelViewModel.

    OnboardingWizard 声明式重写后，fn_validate_database 绑定为
    database_vm.save_config（见 onboarding_wizard.py L321）。本测试验证该绑定
    契约：通过 VM.save_config 作为 fn_validate_database 调用。
    """

    pytestmark = [
        pytest.mark.integration,
        pytest.mark.database,
        pytest.mark.migration,
        pytest.mark.xdist_group("migration"),
    ]

    @pytest_asyncio.fixture(autouse=True)
    async def isolated_db(self):
        """每个 DDL 测试使用隔离数据库，避免污染共享 test_engine（INT-V2-1）。"""
        params = get_pg_connection_params()
        db_name = f"test_config_panels_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(params, db_name)
        _, async_url = build_db_urls(params, db_name)
        engine = create_test_engine(async_url)
        with override_db_url(async_url):
            yield engine, db_name, params
        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_validate_and_save_database_calls_ensure_tables(
        self, isolated_config, isolated_db, mock_db_thread_pool
    ):
        """Test fn_validate_database (bound to database_vm.save_config) calls ensure_tables_exist"""
        from data.persistence.db_config_service import DatabaseConfigService
        from data.persistence.models import Base

        engine, db_name, params = isolated_db

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        vm = _make_db_vm(isolated_config)
        vm.update_host(params["host"])
        vm.update_port(str(params["port"]))
        vm.update_user(params["user"])
        vm.update_password(params["password"])
        vm.update_database(db_name)
        vm.update_create_if_not_exists(False)

        # 模拟 OnboardingWizard 的 fn_validate_database 绑定
        fn_validate_database = vm.save_config
        assert fn_validate_database is not None

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (True, "Tables created")

            result = await fn_validate_database()

        assert result is True
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_and_save_database_handles_failure(self, isolated_config, mock_db_thread_pool):
        """Test fn_validate_database returns False when save_config fails"""
        from data.persistence.db_config_service import DatabaseConfigService

        vm = _make_db_vm(isolated_config)
        vm.update_host("localhost")
        vm.update_port("5432")
        vm.update_user("postgres")
        vm.update_password("password")
        vm.update_database("testdb")
        vm.update_create_if_not_exists(False)

        # 模拟 OnboardingWizard 的 fn_validate_database 绑定
        fn_validate_database = vm.save_config
        assert fn_validate_database is not None

        with (
            patch.object(DatabaseConfigService, "test_connection") as mock_test,
            patch.object(DatabaseConfigService, "ensure_tables_exist") as mock_ensure,
        ):
            from data.persistence.db_config_service import (
                ConnectionResult,
                ConnectionStatus,
            )

            mock_test.return_value = ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message="Success",
                database_exists=True,
            )
            mock_ensure.return_value = (False, "Table creation failed")

            result = await fn_validate_database()

        assert result is False
        mock_ensure.assert_called_once()
