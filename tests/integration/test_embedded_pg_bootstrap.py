"""真实 sidecar + 真实 PG 的 embedded 模式启动协调集成测试。

覆盖 spec §3.5：``prepare_database_runtime()`` embedded 路径端到端验证。

与单元测试 ``test_bootstrap_prepare_database_runtime.py`` 的区别：
- 单元测试用 mock ``from_config`` / ``service.start``，验证分支逻辑
- 本测试用真实 sidecar（复用 ``real_embedded_pg`` session fixture），验证：
  - ``prepare_database_runtime()`` 调用真实 ``from_config(config).start()``
  - 复用已启动的 sidecar 单例（快速路径返回 info）
  - 返回的 URL 可连
  - ``EmbeddedPostgresService.get_instance()`` 返回已启动实例
  - 完整 bootstrap 流程：``prepare_database_runtime()`` → ``override_db_url(url)``
    → ``CacheManager.init_db()`` → DAO 读写

  注：产品代码 ``main.py`` 已改为永久设置 ``config.DB_URL = embedded_url``（spec.md §1.4，
  D15 决策），不再使用 ``override_db_url`` 上下文管理器。本集成测试用 ``override_db_url``
  作为**测试 fixture 临时覆盖 URL**（spec.md §1.4 明确保留该文件供测试使用），目的是
  在不污染 ``config.DB_URL`` 模块级变量的前提下完成 CacheManager 单例隔离测试。

依赖：
- ``real_embedded_pg`` fixture（session-scoped 真实 PG 实例）
- ``real_sidecar_binary`` fixture（提供 binary 路径给 config）

标记：
- ``pytest.mark.integration``
- ``pytest.mark.embedded_real``

loop_scope：
- 所有 async 测试用 ``loop_scope="session"``（对齐 ``real_embedded_pg`` session fixture）

隔离策略：
- 不重置 ``EmbeddedPostgresService`` 单例（复用 ``real_embedded_pg`` 启动的实例）
- ``TestFullBootstrapFlowEmbedded`` 重置 ``CacheManager`` 单例，测试后恢复
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.embedded_postgres.service import EmbeddedPostgresService
from tests._helpers import create_test_engine

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


def _make_config_dict(**overrides) -> dict:
    """构造完整 config dict（load_config 返回值）。

    以 ``get_default_config()`` 为 base 保证 ``AppConfig.model_validate`` 字段完整，
    仅覆盖 ``embedded_pg_*`` 相关字段。
    """
    from utils.config_models import get_default_config

    cfg = get_default_config()
    cfg.update(overrides)
    return cfg


class TestPrepareDatabaseRuntimeEmbeddedRealSidecar:
    """``prepare_database_runtime()`` 真实 sidecar 协调验证。

    复用 ``real_embedded_pg`` session fixture 启动的 sidecar 单例，
    验证 ``prepare_database_runtime()`` 能通过 ``from_config(config).start()``
    获取已启动的 sidecar 并返回有效 URL。
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_prepare_database_runtime_returns_url_with_real_sidecar(
        self,
        real_embedded_pg: ConnectionInfo,
        real_sidecar_binary: Path,
        monkeypatch,
    ) -> None:
        """``QTRADING_DATABASE_MODE=embedded`` + ``enabled=True`` → 返回有效 URL。

        验证：
        1. ``prepare_database_runtime()`` 返回非 None URL
        2. 返回的 URL 与 ``real_embedded_pg.url`` 一致（复用单例）
        3. 返回的 URL 可连（``SELECT 1``）
        4. ``EmbeddedPostgresService.get_instance()`` 返回已启动实例
        """
        monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
        # 显式注入 sidecar binary 路径（虽然单例已存在不需解析，但保证 from_config 逻辑一致）
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            staticmethod(
                lambda: _make_config_dict(
                    embedded_pg_enabled=True,
                    embedded_pg_sidecar_path=str(real_sidecar_binary),
                )
            ),
        )

        from app.bootstrap import prepare_database_runtime

        result = await prepare_database_runtime()

        # 1. 返回非 None URL
        assert result is not None, "期望返回 URL，实际 None"
        assert isinstance(result, str)
        # 2. 与 real_embedded_pg.url 一致（复用单例）
        assert result == real_embedded_pg.url, (
            f"期望 URL 与 real_embedded_pg 一致，实际：result={result}, info.url={real_embedded_pg.url}"
        )
        # 3. URL 可连
        engine = create_test_engine(result)
        try:
            async with engine.connect() as conn:
                row = await conn.execute(text("SELECT 1"))
                assert row.scalar() == 1
        finally:
            await engine.dispose()
        # 4. get_instance 返回已启动实例
        service = EmbeddedPostgresService.get_instance()
        assert service is not None
        assert service._connection_info is not None
        assert service._process is not None
        assert service._process.poll() is None, "sidecar 进程应仍在运行"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_prepare_database_runtime_external_mode_returns_none(
        self,
        real_embedded_pg: ConnectionInfo,
        monkeypatch,
    ) -> None:
        """``QTRADING_DATABASE_MODE=external`` → 返回 None，不启动 sidecar。

        验证 external 模式下 ``prepare_database_runtime()`` 不干扰已启动的 sidecar 单例。
        """
        monkeypatch.setenv("QTRADING_DATABASE_MODE", "external")
        # embedded_pg_enabled=True 触发 M5 WARNING（但不启动）
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
        )

        from app.bootstrap import prepare_database_runtime

        result = await prepare_database_runtime()

        assert result is None
        # 验证已启动的 sidecar 单例未受影响（real_embedded_pg 仍可用）
        service = EmbeddedPostgresService.get_instance()
        assert service._connection_info is not None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_prepare_database_runtime_embedded_disabled_returns_none(
        self,
        real_embedded_pg: ConnectionInfo,
        real_sidecar_binary: Path,
        monkeypatch,
    ) -> None:
        """``QTRADING_DATABASE_MODE=embedded`` + ``enabled=False`` → 返回 None。

        验证 config 禁用时 ``prepare_database_runtime()`` 不启动 sidecar，不影响已有单例。
        """
        monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            staticmethod(
                lambda: _make_config_dict(
                    embedded_pg_enabled=False,
                    embedded_pg_sidecar_path=str(real_sidecar_binary),
                )
            ),
        )

        from app.bootstrap import prepare_database_runtime

        result = await prepare_database_runtime()

        assert result is None
        # 已启动的 sidecar 单例未受影响
        service = EmbeddedPostgresService.get_instance()
        assert service._connection_info is not None


class TestFullBootstrapFlowEmbedded:
    """完整 embedded bootstrap 流程：``prepare_database_runtime()`` → ``CacheManager.init_db()``。

    验证 ``prepare_database_runtime()`` 返回的 URL 能被 ``CacheManager`` 正确使用：
    1. ``prepare_database_runtime()`` 返回 URL
    2. ``override_db_url(url)`` 作为测试 fixture 临时覆盖 URL（产品代码 main.py 已改用 config.DB_URL 永久设置）
    3. ``CacheManager._reset_singleton()`` + ``CacheManager()`` + ``init_db()``
    4. ``cache.engine`` 可连
    5. ``cache.stock_dao`` 可读写

    隔离：测试后 ``CacheManager.close()`` + ``_reset_singleton()`` 恢复状态。
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_full_bootstrap_flow_prepare_and_cache_manager_init(
        self,
        real_embedded_pg: ConnectionInfo,
        real_sidecar_binary: Path,
        monkeypatch,
    ) -> None:
        """端到端验证 ``prepare_database_runtime()`` + ``CacheManager.init_db()`` 协调。"""
        import asyncio

        from contextlib import ExitStack

        import pandas as pd

        from data.cache.cache_manager import CacheManager
        from data.persistence.db_url_override import override_db_url

        monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            staticmethod(
                lambda: _make_config_dict(
                    embedded_pg_enabled=True,
                    embedded_pg_sidecar_path=str(real_sidecar_binary),
                )
            ),
        )

        from app.bootstrap import prepare_database_runtime

        # 1. prepare_database_runtime 返回 URL
        url = await prepare_database_runtime()
        assert url is not None
        assert url == real_embedded_pg.url

        # 2. override_db_url + CacheManager.init_db
        #    保存原 CacheManager 单例，测试后恢复
        original_cm_instance = CacheManager._instance
        CacheManager._instance = None  # 绕过 __new__ 单例，允许创建新实例

        import datetime

        with ExitStack() as url_stack:
            url_stack.enter_context(override_db_url(url))
            cache = CacheManager()
            try:
                await cache.init_db(auto_migrate=True)

                # 3. cache.engine 可连
                assert cache.engine is not None
                async with cache.engine.connect() as conn:
                    result = await conn.execute(text("SELECT 1"))
                    assert result.scalar() == 1

                # 4. stock_dao 可读写（验证 DAO.engine 已同步到新 engine）
                df = pd.DataFrame(
                    [
                        {
                            "ts_code": "600519.SH",
                            "symbol": "600519",
                            "name": "贵州茅台",
                            "area": "贵州",
                            "industry": "白酒",
                            "market": "主板",
                            "list_date": datetime.date(2001, 8, 27),
                            "list_status": "L",
                        }
                    ]
                )
                written = await cache.stock_dao.save_stock_basic(df)
                assert written == 1

                read_df = await cache.stock_dao.get_stock_basic()
                assert read_df is not None
                assert len(read_df) >= 1
                assert "600519.SH" in set(read_df["ts_code"])

                # 清理测试数据
                assert cache.engine is not None
                async with cache.engine.begin() as conn:
                    await conn.execute(text("DELETE FROM stock_basic WHERE ts_code = '600519.SH'"))
            finally:
                try:
                    await cache.close()
                except asyncio.CancelledError:
                    raise  # R2: 不吞 CancelledError
                finally:
                    CacheManager._reset_singleton()
                    CacheManager._instance = original_cm_instance  # M2: 移入 finally，异常路径也能恢复
