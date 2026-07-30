"""prepare_database_runtime 单元测试（Phase 2 §3.4 红灯翻绿）。

测试分组（4 个）：
- noop 路径：external 模式 / embedded_pg_enabled=False
- 启动并返回 URL 路径：embedded + enabled=True → 返回 ConnectionInfo.url（D15）
- 失败传播路径：service.start raise → prepare_database_runtime 重新 raise

Mock 策略（D17/D18）：
- monkeypatch os.environ 设 QTRADING_DATABASE_MODE
- monkeypatch EmbeddedPostgresService.from_config 返回 mock service
- monkeypatch ConfigHandler.load_config 返回 dict（base = get_default_config()）
- AppConfig.model_validate 真实运行（不 mock）
- D15（pg-plan §22）：prepare_database_runtime 不再调 save_db_config，
  改为返回 URL 供调用方永久设置 config.DB_URL（不持久化到 config 文件）
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
    """env=external → 不调 from_config、返回 None。

    spec.md §3 不变量 1：QTRADING_DATABASE_MODE 默认值为 "embedded"，
    需显式设 env=external 才能进入 external 分支。
    """
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "external")

    from app.bootstrap import prepare_database_runtime

    from_config_calls: list[int] = []

    def _from_config(_cfg):
        from_config_calls.append(1)
        return MagicMock()

    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, cfg: _from_config(cfg)),
    )
    # M5: mock load_config 返回 embedded_pg_enabled=False，避免触发 WARNING
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=False)),
    )

    result = await prepare_database_runtime()

    assert from_config_calls == []
    assert result is None


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_warns_when_external_mode_but_config_enabled(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """M5: mode=external 但 embedded_pg_enabled=True → 记 WARNING（用户可能误配置），返回 None。

    spec.md §3 不变量 1：QTRADING_DATABASE_MODE 默认值为 "embedded"，
    需显式设 env=external 才能进入 external 分支触发 M5 WARNING。
    """
    import logging

    monkeypatch.setenv("QTRADING_DATABASE_MODE", "external")

    from app.bootstrap import prepare_database_runtime

    # mock load_config 返回 dict（embedded_pg_enabled=True）
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    from_config_calls: list[int] = []

    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: from_config_calls.append(1) or MagicMock()),
    )

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        result = await prepare_database_runtime()

    # 验证 WARNING 日志含关键信息
    assert any("embedded_pg_enabled=True" in r.message and "will NOT start" in r.message for r in caplog.records), (
        f"期望 WARNING 日志含误配置提示，实际：{[r.message for r in caplog.records]}"
    )
    # 验证不启动 service
    assert from_config_calls == []
    assert result is None


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_noop_when_config_disabled(monkeypatch, tmp_path: Path, caplog) -> None:
    """env=embedded 但 embedded_pg_enabled=False → 记 WARNING，不调 service.start、返回 None。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import prepare_database_runtime

    # mock load_config 返回 dict（embedded_pg_enabled=False）
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=False)),
    )

    start_calls: list[int] = []

    mock_service = MagicMock()
    mock_service.start = AsyncMock(side_effect=lambda: start_calls.append(1))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: mock_service),
    )

    import logging

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        result = await prepare_database_runtime()

    assert start_calls == []
    assert result is None
    # 验证 WARNING 日志含关键信息
    assert any("embedded_pg_enabled=False" in r.message for r in caplog.records), (
        f"期望 WARNING 日志含 embedded_pg_enabled=False，实际：{[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_starts_service_and_returns_url(monkeypatch, tmp_path: Path) -> None:
    """D15: env=embedded + enabled=True → mock service.start 返回 ConnectionInfo，验证返回 info.url。"""
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
    fake_url = "postgresql+asyncpg://qtrading:mock_password_55432@127.0.0.1:55432/qtrading"
    fake_info = ConnectionInfo(
        url=fake_url,
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

    # D15：mock save_db_config 验证不被调用（embedded URL 不再持久化）
    save_db_config_calls: list[dict] = []
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.save_db_config",
        staticmethod(lambda **kwargs: save_db_config_calls.append(kwargs) or True),
    )

    result = await prepare_database_runtime()

    # 验证返回 info.url（D15：不再 save_db_config 持久化）
    assert result == fake_url, f"期望返回 ConnectionInfo.url，实际：{result}"
    assert save_db_config_calls == [], f"D15: 不应调用 save_db_config，实际：{save_db_config_calls}"


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_warns_when_database_url_env_set(monkeypatch, tmp_path: Path, caplog) -> None:
    """R-Arch-2/Ske-1: embedded 模式 + DATABASE_URL env var 误设 → emit WARNING，仍启动 embedded PG。

    场景：QTRADING_DATABASE_MODE=embedded + DATABASE_URL env var 被外部误设。
    验证：记 WARNING 提示用户 main.py 会自动 pop 此 env var 并建议清理 shell profile，但仍继续启动 embedded PG。
    """
    import logging

    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://external:external@remote-host:5432/external")

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.protocol import ConnectionInfo

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    fake_info = ConnectionInfo(
        url="postgresql+asyncpg://postgres:mock_pwd@127.0.0.1:55432/qtrading",
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

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        result = await prepare_database_runtime()

    # 验证 WARNING 日志含关键提示（P4：文案已更新为说明自动 pop + 建议清理 shell profile）
    assert any("DATABASE_URL env var is set" in r.message and "shell profile" in r.message for r in caplog.records), (
        f"期望 WARNING 日志含 DATABASE_URL 误设提示及 shell profile 建议，实际：{[r.message for r in caplog.records]}"
    )
    # 验证仍启动 embedded PG（不阻断启动）
    assert result == fake_info.url


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_propagates_start_failure(monkeypatch, tmp_path: Path) -> None:
    """mock service.start raise EmbeddedPostgresStartError → prepare_database_runtime 重新 raise，不返回 URL。"""
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

    with pytest.raises(EmbeddedPostgresStartError, match="fake start failure"):
        await prepare_database_runtime()


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


# --- detect_embedded_pg_startup_scenario 单元测试（UX 改进 spec §启动侧方案 A） ---


def _make_mock_service_with_paths(install_dir: Path, data_dir: Path) -> MagicMock:
    """构造 mock EmbeddedPostgresService，暴露 _install_dir / _data_dir 私有属性。

    detect 函数复用 from_config 路径解析后访问这两个私有属性，测试时通过 mock 注入。
    """
    service = MagicMock()
    service._install_dir = install_dir  # type: ignore[attr-defined]  # [reason: MagicMock 替身注入私有属性，模拟 EmbeddedPostgresService.from_config 路径解析结果]
    service._data_dir = data_dir  # type: ignore[attr-defined]  # [reason: 同上，注入 _data_dir 私有属性]
    return service


def test_detect_scenario_returns_none_when_mode_external(monkeypatch) -> None:
    """env=external → 返回 None（不检测）。

    spec.md §3 不变量 1：QTRADING_DATABASE_MODE 默认值为 "embedded"，
    需显式设 env=external 才能进入 external 分支（不检测）。
    """
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "external")

    from app.bootstrap import detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=True))

    from_config_calls: list[int] = []

    def _from_config(_cls, _cfg):
        from_config_calls.append(1)
        return MagicMock()

    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(_from_config),
    )

    result = detect_embedded_pg_startup_scenario(config)

    assert result is None
    assert from_config_calls == [], "external 模式不应调用 from_config"


def test_detect_scenario_returns_none_when_embedded_disabled(monkeypatch) -> None:
    """env=embedded 但 embedded_pg_enabled=False → 返回 None。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=False))

    from_config_calls: list[int] = []

    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: from_config_calls.append(1) or MagicMock()),
    )

    result = detect_embedded_pg_startup_scenario(config)

    assert result is None
    assert from_config_calls == [], "embedded_pg_enabled=False 不应调用 from_config"


def test_detect_scenario_first_run_when_both_missing(monkeypatch, tmp_path: Path) -> None:
    """install marker 与 PG_VERSION 均不存在 → FIRST_RUN。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import EmbeddedPgStartupScenario, detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    install_dir = tmp_path / "install"
    data_dir = tmp_path / "data"
    install_dir.mkdir()
    data_dir.mkdir()
    # 不创建 .setup-complete 和 PG_VERSION

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=True))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: _make_mock_service_with_paths(install_dir, data_dir)),
    )

    result = detect_embedded_pg_startup_scenario(config)

    assert result == EmbeddedPgStartupScenario.FIRST_RUN


def test_detect_scenario_normal_when_both_exist(monkeypatch, tmp_path: Path) -> None:
    """install marker 与 PG_VERSION 均存在 → NORMAL。"""
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import EmbeddedPgStartupScenario, detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    install_dir = tmp_path / "install"
    data_dir = tmp_path / "data"
    install_dir.mkdir()
    data_dir.mkdir()
    (install_dir / ".setup-complete").write_text("sha256:fake\n")
    (data_dir / "PG_VERSION").write_text("17.0\n")

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=True))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: _make_mock_service_with_paths(install_dir, data_dir)),
    )

    result = detect_embedded_pg_startup_scenario(config)

    assert result == EmbeddedPgStartupScenario.NORMAL


def test_detect_scenario_unknown_when_only_marker_exists(monkeypatch, tmp_path: Path, caplog) -> None:
    """仅 install marker 存在 → UNKNOWN + WARNING 日志。"""
    import logging

    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import EmbeddedPgStartupScenario, detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    install_dir = tmp_path / "install"
    data_dir = tmp_path / "data"
    install_dir.mkdir()
    data_dir.mkdir()
    (install_dir / ".setup-complete").write_text("sha256:fake\n")
    # 不创建 PG_VERSION

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=True))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: _make_mock_service_with_paths(install_dir, data_dir)),
    )

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        result = detect_embedded_pg_startup_scenario(config)

    assert result == EmbeddedPgStartupScenario.UNKNOWN
    assert any(
        "UNKNOWN" in r.message and "install_marker=True" in r.message and "pg_version=False" in r.message
        for r in caplog.records
    ), f"期望 WARNING 日志含 UNKNOWN + marker=True + pg_version=False，实际：{[r.message for r in caplog.records]}"


def test_detect_scenario_unknown_when_only_pg_version_exists(monkeypatch, tmp_path: Path, caplog) -> None:
    """仅 PG_VERSION 存在 → UNKNOWN + WARNING 日志。"""
    import logging

    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")

    from app.bootstrap import EmbeddedPgStartupScenario, detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    install_dir = tmp_path / "install"
    data_dir = tmp_path / "data"
    install_dir.mkdir()
    data_dir.mkdir()
    # 不创建 .setup-complete
    (data_dir / "PG_VERSION").write_text("17.0\n")

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=True))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: _make_mock_service_with_paths(install_dir, data_dir)),
    )

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        result = detect_embedded_pg_startup_scenario(config)

    assert result == EmbeddedPgStartupScenario.UNKNOWN
    assert any(
        "UNKNOWN" in r.message and "install_marker=False" in r.message and "pg_version=True" in r.message
        for r in caplog.records
    ), f"期望 WARNING 日志含 UNKNOWN + marker=False + pg_version=True，实际：{[r.message for r in caplog.records]}"


# --- 默认值契约测试（spec.md §3 不变量 1：QTRADING_DATABASE_MODE 默认 embedded）---


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_defaults_to_embedded_mode(monkeypatch, tmp_path: Path) -> None:
    """env 未设（默认 embedded）+ embedded_pg_enabled=True → 进入 embedded 分支，启动 service 并返回 URL。

    spec.md §3 不变量 1: QTRADING_DATABASE_MODE 环境变量默认值为 "embedded"。
    用户旅程契约：新用户按 README 执行 `python main.py` 默认进入 embedded 模式，
    无需任何环境变量配置即可启动 sidecar。
    """
    monkeypatch.delenv("QTRADING_DATABASE_MODE", raising=False)

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.protocol import ConnectionInfo

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    fake_url = "postgresql+asyncpg://qtrading:mock_password_55432@127.0.0.1:55432/qtrading"
    fake_info = ConnectionInfo(
        url=fake_url,
        port=55432,
        pid=12345,
        data_dir="/fake/pgdata",
    )
    mock_service = MagicMock()
    mock_service.start = AsyncMock(return_value=fake_info)

    from_config_calls: list[int] = []
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: from_config_calls.append(1) or mock_service),
    )

    result = await prepare_database_runtime()

    assert from_config_calls == [1], (
        f"默认 embedded 模式应进入 embedded 分支并调用 from_config, 实际: {from_config_calls}"
    )
    assert result == fake_url, f"期望返回 ConnectionInfo.url, 实际: {result}"


def test_detect_scenario_runs_when_env_unset_defaults_embedded(monkeypatch, tmp_path: Path) -> None:
    """env 未设（默认 embedded）+ embedded_pg_enabled=True → 进入 embedded 分支并调用 from_config。

    spec.md §3 不变量 1: QTRADING_DATABASE_MODE 环境变量默认值为 "embedded"。
    detect_embedded_pg_startup_scenario 在默认配置下不应返回 None（不检测），
    而是进入 embedded 分支基于 install marker / PG_VERSION 判定 scenario。
    """
    monkeypatch.delenv("QTRADING_DATABASE_MODE", raising=False)

    from app.bootstrap import EmbeddedPgStartupScenario, detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    install_dir = tmp_path / "install"
    data_dir = tmp_path / "data"
    install_dir.mkdir()
    data_dir.mkdir()
    # 不创建 .setup-complete 和 PG_VERSION → FIRST_RUN

    config = AppConfig.model_validate(_make_config_dict(embedded_pg_enabled=True))
    monkeypatch.setattr(
        "data.persistence.embedded_postgres.service.EmbeddedPostgresService.from_config",
        classmethod(lambda cls, _cfg: _make_mock_service_with_paths(install_dir, data_dir)),
    )

    result = detect_embedded_pg_startup_scenario(config)

    assert result == EmbeddedPgStartupScenario.FIRST_RUN, (
        f"默认 embedded 模式应进入 embedded 分支并返回 FIRST_RUN, 实际: {result}"
    )


# --- QTRADING_EMBEDDED_PG_URL_FILE 单元测试（方案 B：E2E 测试支持） ---


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_writes_url_file_when_env_set(monkeypatch, tmp_path: Path) -> None:
    """QTRADING_EMBEDDED_PG_URL_FILE 设置时，prepare_database_runtime 将 sidecar URL 写入文件 + atexit 注册清理。

    方案 B（spec.md §3 不变量 1）：E2E 测试支持，sidecar 启动后把 URL 写入文件，
    供 E2E 主进程读取后连接 sidecar DB 播种数据。生产环境不设置此环境变量，无副作用。
    """
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
    url_file = tmp_path / "sidecar.url"
    monkeypatch.setenv("QTRADING_EMBEDDED_PG_URL_FILE", str(url_file))

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.protocol import ConnectionInfo

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    fake_url = "postgresql+asyncpg://qtrading:mock_password_55432@127.0.0.1:55432/qtrading"
    fake_info = ConnectionInfo(
        url=fake_url,
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

    atexit_calls: list = []
    monkeypatch.setattr("atexit.register", lambda func, *args: atexit_calls.append((func, args)))

    result = await prepare_database_runtime()

    assert result == fake_url
    assert url_file.exists(), f"URL 文件应已创建: {url_file}"
    assert url_file.read_text(encoding="utf-8") == fake_url, "URL 文件内容应为 sidecar URL"
    assert len(atexit_calls) == 1, f"应注册 1 个 atexit 清理函数, 实际: {atexit_calls}"
    registered_func = atexit_calls[0][0]
    assert registered_func.__name__ == "_cleanup_embedded_pg_url_file", (
        f"atexit 应注册 _cleanup_embedded_pg_url_file, 实际: {registered_func.__name__}"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_skips_url_file_when_env_not_set(monkeypatch, tmp_path: Path) -> None:
    """QTRADING_EMBEDDED_PG_URL_FILE 未设置时，prepare_database_runtime 不写文件（无副作用）。

    生产环境不设置此环境变量，sidecar 启动后不产生 URL 文件，不注册 atexit 清理。
    """
    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
    monkeypatch.delenv("QTRADING_EMBEDDED_PG_URL_FILE", raising=False)

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.protocol import ConnectionInfo

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    fake_url = "postgresql+asyncpg://qtrading:mock_password_55432@127.0.0.1:55432/qtrading"
    fake_info = ConnectionInfo(
        url=fake_url,
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

    atexit_calls: list = []
    monkeypatch.setattr("atexit.register", lambda func, *args: atexit_calls.append((func, args)))

    result = await prepare_database_runtime()

    assert result == fake_url
    assert atexit_calls == [], "未设环境变量时不应注册 atexit 清理函数"


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_database_runtime_url_file_write_failure_does_not_block(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    """URL 文件写入失败（OSError）时不阻塞启动，记 WARNING 日志。

    场景：QTRADING_EMBEDDED_PG_URL_FILE 指向已存在目录，write_text 抛 IsADirectoryError
    （OSError 子类）。验证：prepare_database_runtime 仍返回 URL，不抛异常，记 WARNING。
    """
    import logging

    monkeypatch.setenv("QTRADING_DATABASE_MODE", "embedded")
    # url_file 指向已存在目录 → write_text 抛 IsADirectoryError（OSError 子类）
    monkeypatch.setenv("QTRADING_EMBEDDED_PG_URL_FILE", str(tmp_path))

    from app.bootstrap import prepare_database_runtime
    from data.persistence.embedded_postgres.protocol import ConnectionInfo

    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.load_config",
        staticmethod(lambda: _make_config_dict(embedded_pg_enabled=True)),
    )

    fake_url = "postgresql+asyncpg://qtrading:mock_password_55432@127.0.0.1:55432/qtrading"
    fake_info = ConnectionInfo(
        url=fake_url,
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

    with caplog.at_level(logging.WARNING, logger="app.bootstrap"):
        result = await prepare_database_runtime()

    assert result == fake_url, "URL 文件写入失败不应阻塞启动, 仍应返回 URL"
    assert any("failed to write embedded PG URL file" in r.message for r in caplog.records), (
        f"期望 WARNING 日志含 failed to write embedded PG URL file, 实际: {[r.message for r in caplog.records]}"
    )


def test_cleanup_embedded_pg_url_file_deletes_file(tmp_path: Path) -> None:
    """_cleanup_embedded_pg_url_file 删除存在的 URL 文件（atexit 回调）。"""
    from app.bootstrap import _cleanup_embedded_pg_url_file

    url_file = tmp_path / "sidecar.url"
    url_file.write_text("postgresql+asyncpg://dummy", encoding="utf-8")
    assert url_file.exists()

    _cleanup_embedded_pg_url_file(url_file)

    assert not url_file.exists(), "清理后 URL 文件应被删除"


def test_cleanup_embedded_pg_url_file_handles_missing_file(tmp_path: Path) -> None:
    """_cleanup_embedded_pg_url_file 对不存在的文件不报错（missing_ok=True）。"""
    from app.bootstrap import _cleanup_embedded_pg_url_file

    url_file = tmp_path / "nonexistent.url"
    assert not url_file.exists()

    # 不应抛异常
    _cleanup_embedded_pg_url_file(url_file)


def test_cleanup_embedded_pg_url_file_handles_oserror(monkeypatch, tmp_path: Path) -> None:
    """_cleanup_embedded_pg_url_file 捕获 OSError（如 PermissionError），不抛异常。

    atexit 回调中抛异常会污染退出流程，必须吞没 OSError。
    """

    def _raise_oserror(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.unlink", _raise_oserror)

    from app.bootstrap import _cleanup_embedded_pg_url_file

    url_file = tmp_path / "sidecar.url"
    url_file.write_text("postgresql+asyncpg://dummy", encoding="utf-8")

    # 不应抛异常
    _cleanup_embedded_pg_url_file(url_file)
