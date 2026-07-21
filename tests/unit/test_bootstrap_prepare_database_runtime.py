"""prepare_database_runtime 单元测试（Phase 2 §3.4 红灯翻绿）。

测试分组（4 个）：
- noop 路径：external 模式 / embedded_pg_enabled=False
- 启动并注入 URL 路径：embedded + enabled=True
- 失败传播路径：service.start raise → prepare_database_runtime 重新 raise

Mock 策略（D17/D18）：
- monkeypatch os.environ 设 QTRADING_DATABASE_MODE
- monkeypatch EmbeddedPostgresService.from_config 返回 mock service
- monkeypatch ConfigHandler.load_config 返回 dict（base = get_default_config()）
- AppConfig.model_validate 真实运行（不 mock）
- monkeypatch ConfigHandler.save_db_config 记录调用
- 复用 tests/conftest.py 既有 keyring mock（save_db_config 内部写 keyring）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_config_dict(**overrides) -> dict:
    """构造完整 config dict（load_config 返回值）。

    以 get_default_config() 为 base 保证 AppConfig.model_validate 字段完整，
    仅覆盖 embedded_pg_* 相关字段。
    """
    from utils.config_models import get_default_config

    cfg = get_default_config()
    cfg.update(overrides)
    return cfg


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_noop_when_mode_external(monkeypatch, tmp_path: Path) -> None:
    """env 未设 / =external → 不调 from_config、不调 save_db_config。"""
    monkeypatch.delenv("QTRADING_DATABASE_MODE", raising=False)

    from app.bootstrap import prepare_database_runtime

    from_config_calls: list[int] = []
    save_db_config_calls: list[tuple] = []

    def _from_config(_cfg):
        from_config_calls.append(1)
        return MagicMock()

    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, cfg: _from_config(cfg)),
    )
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.save_db_config",
        staticmethod(lambda **kwargs: save_db_config_calls.append(kwargs)),
    )
    # M5: mock load_config 返回 embedded_pg_enabled=False，避免触发 WARNING
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=False)),
    )

    await prepare_database_runtime()

    assert from_config_calls == []
    assert save_db_config_calls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_warns_when_external_mode_but_config_enabled(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """M5: mode=external 但 embedded_pg_enabled=True → 记 WARNING（用户可能误配置）。"""
    import logging

    monkeypatch.delenv("QTRADING_DATABASE_MODE", raising=False)

    from app.bootstrap import prepare_database_runtime

    # mock load_config 返回 dict（embedded_pg_enabled=True）
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    from_config_calls: list[int] = []
    save_db_config_calls: list[tuple] = []

    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: from_config_calls.append(1) or MagicMock()),
    )
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.save_db_config",
        staticmethod(lambda **kwargs: save_db_config_calls.append(kwargs)),
    )

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        await prepare_database_runtime()

    # 验证 WARNING 日志含关键信息
    assert any("embedded_pg_enabled=True" in r.message and "will NOT start" in r.message for r in caplog.records), (
        f"期望 WARNING 日志含误配置提示，实际：{[r.message for r in caplog.records]}"
    )
    # 验证不启动 service
    assert from_config_calls == []
    assert save_db_config_calls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_noop_when_config_disabled(monkeypatch, tmp_path: Path, caplog) -> None:
    """env=embedded 但 embedded_pg_enabled=False → 记 WARNING，不调 service.start、不调 save_db_config。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import prepare_database_runtime

    # mock load_config 返回 dict（embedded_pg_enabled=False）
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=False)),
    )

    start_calls: list[int] = []
    save_db_config_calls: list[tuple] = []

    mock_service = MagicMock()
    mock_service.start = AsyncMock(side_effect=lambda: start_calls.append(1))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: mock_service),
    )
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.save_db_config",
        staticmethod(lambda **kwargs: save_db_config_calls.append(kwargs)),
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        await prepare_database_runtime()

    assert start_calls == []
    assert save_db_config_calls == []
    # 验证 WARNING 日志含关键信息
    assert any("embedded_pg_enabled=False" in r.message for r in caplog.records), (
        f"期望 WARNING 日志含 embedded_pg_enabled=False，实际：{[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_starts_service_and_injects_url(monkeypatch, tmp_path: Path) -> None:
    """env=embedded + enabled=True → mock service.start 返回 ConnectionInfo，验证 save_db_config 被调用。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.protocol import ConnectionInfo

    # mock load_config 返回 dict（embedded_pg_enabled=True + 自定义 listen/username/database）
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(
            lambda: _make_config_dict(
                embedded_pg_enabled=True,
                embedded_pg_listen="127.0.0.1",
                embedded_pg_username="qtrading",
                embedded_pg_database="qtrading",
            )
        ),
    )

    # ConnectionInfo.url 含 password 用于 urlparse 解析
    fake_info = ConnectionInfo(
        url="postgresql+asyncpg://qtrading:mock_password_55432@127.0.0.1:55432/qtrading",
        port=55432,
        pid=12345,
        data_dir="/fake/pgdata",
    )
    mock_service = MagicMock()
    mock_service.start = AsyncMock(return_value=fake_info)
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: mock_service),
    )

    save_db_config_calls: list[dict] = []
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.save_db_config",
        staticmethod(lambda **kwargs: save_db_config_calls.append(kwargs) or True),
    )

    await prepare_database_runtime()

    # 验证 save_db_config 被调用且参数含正确 host/port/user/password/database
    assert len(save_db_config_calls) == 1, f"期望 save_db_config 调用 1 次，实际：{len(save_db_config_calls)}"
    kwargs = save_db_config_calls[0]
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 55432
    assert kwargs["user"] == "qtrading"
    assert kwargs["password"] == "mock_password_55432"
    assert kwargs["database"] == "qtrading"


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_propagates_start_failure(monkeypatch, tmp_path: Path) -> None:
    """mock service.start raise EmbeddedPostgresStartError → prepare_database_runtime 重新 raise，不调 save_db_config。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    mock_service = MagicMock()
    mock_service.start = AsyncMock(side_effect=EmbeddedPostgresStartError("fake start failure"))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: mock_service),
    )

    save_db_config_calls: list[tuple] = []
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.save_db_config",
        staticmethod(lambda **kwargs: save_db_config_calls.append(kwargs)),
    )

    with pytest.raises(EmbeddedPostgresStartError, match="fake start failure"):
        await prepare_database_runtime()

    assert save_db_config_calls == []


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_resets_singleton_on_failure(monkeypatch, tmp_path: Path) -> None:
    """H3: service.start 失败 → 调用 _reset_singleton 清理单例，再 re-raise。

    验证 H3 红线：start 失败后单例必须被重置，避免后续 CacheManager 误用残留状态。
    R2 合规：except Exception 不捕获 CancelledError（BaseException 子类）。
    """
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    mock_service = MagicMock()
    mock_service.start = AsyncMock(side_effect=EmbeddedPostgresStartError("fake start failure"))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: mock_service),
    )

    reset_calls: list[int] = []
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService._reset_singleton",
        classmethod(lambda cls: reset_calls.append(1)),
    )

    with pytest.raises(EmbeddedPostgresStartError, match="fake start failure"):
        await prepare_database_runtime()

    assert reset_calls == [1], f"期望 _reset_singleton 被调用 1 次，实际：{reset_calls}"


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_does_not_reset_singleton_on_cancelled(monkeypatch, tmp_path: Path) -> None:
    """R2 合规：CancelledError 不被 except Exception 捕获，不调用 _reset_singleton。

    CancelledError 是 BaseException 子类，必须传播以配合优雅停机。
    单例清理由后续 ShutdownCoordinator Step 8 负责。
    """
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    import asyncio

    from app.bootstrap import prepare_database_runtime

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    mock_service = MagicMock()
    mock_service.start = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: mock_service),
    )

    reset_calls: list[int] = []
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService._reset_singleton",
        classmethod(lambda cls: reset_calls.append(1)),
    )

    # CancelledError 无消息可 match；用 as exc_info 捕获后断言类型（R2 红线验证）
    with pytest.raises(asyncio.CancelledError) as exc_info:
        await prepare_database_runtime()
    assert isinstance(exc_info.value, asyncio.CancelledError)

    assert reset_calls == [], f"CancelledError 不应触发 _reset_singleton，实际：{reset_calls}"
