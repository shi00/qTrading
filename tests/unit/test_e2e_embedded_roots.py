"""E2E embedded PG 根目录隔离单元测试（D21-OTHER-001）。

验证 ``tests/e2e/_embedded_roots.py``：
- 三个根目录均创建在 tmp 内且互不重叠；
- 重复调用幂等，已存在目录内的哨兵文件字节/mtime 不变；
- 权限不足时抛出 OSError，不回退用户目录；
- Windows 下跳过 POSIX chmod（路径仍可用）；
- 用隔离根目录构造 AppConfig 时，``EmbeddedPostgresService`` /
  ``EmbeddedPgMaintenanceService`` 不再回退 platformdirs，真实用户目录
  （含 ``runtime/password`` 凭据）字节/mtime 不变。

不启动真实/fake sidecar 进程。
"""

from __future__ import annotations

import logging
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e import _embedded_roots
from tests.e2e._embedded_roots import create_isolated_embedded_roots
from utils.config_models import AppConfig


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def test_roots_created_under_tmp(tmp_path: Path) -> None:
    roots = create_isolated_embedded_roots(tmp_path)

    assert set(roots) == {
        "embedded_pg_data_root",
        "embedded_pg_install_root",
        "embedded_pg_log_dir",
    }
    resolved = [Path(value).resolve() for value in roots.values()]
    for path in resolved:
        assert path.is_absolute()
        assert path.is_relative_to(tmp_path.resolve())
        assert path.is_dir()
    assert len(set(resolved)) == len(resolved), f"根目录不得重叠: {resolved}"


@pytest.mark.skipif(_embedded_roots.os.name == "nt", reason="POSIX 权限位语义")
def test_posix_roots_are_private(tmp_path: Path) -> None:
    roots = create_isolated_embedded_roots(tmp_path)

    for value in roots.values():
        mode = stat.S_IMODE(Path(value).stat().st_mode)
        assert mode == 0o700, f"{value} 权限应为 0700，实际 {oct(mode)}"


def test_windows_skips_posix_chmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_embedded_roots, "os", SimpleNamespace(name="nt"))

    def _fail_chmod(self: Path, mode: int) -> None:
        raise AssertionError("Windows 无 POSIX 权限位，不得调用 chmod")

    monkeypatch.setattr(Path, "chmod", _fail_chmod)

    roots = create_isolated_embedded_roots(tmp_path)
    assert all(Path(value).is_dir() for value in roots.values())


def test_repeated_calls_keep_existing_content(tmp_path: Path) -> None:
    data_root = tmp_path / "data_root"
    data_root.mkdir()
    sentinel = data_root / "sentinel.bin"
    sentinel.write_bytes(b"existing-user-data")
    before = _snapshot(sentinel)
    before_entries = sorted(p.name for p in data_root.iterdir())

    create_isolated_embedded_roots(tmp_path)
    create_isolated_embedded_roots(tmp_path)  # 幂等：目录已存在不得报错/清空

    assert _snapshot(sentinel) == before
    assert sorted(p.name for p in data_root.iterdir()) == before_entries


def test_permission_error_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_mkdir(self: Path, parents: bool = False, exist_ok: bool = False) -> None:
        raise PermissionError("denied by test")

    monkeypatch.setattr(Path, "mkdir", _fail_mkdir)

    with pytest.raises(PermissionError, match="denied by test"):
        create_isolated_embedded_roots(tmp_path)


def test_isolated_roots_prevent_platformdirs_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离根目录生效时，服务解析不得触碰真实用户目录（含运行期凭据）。"""
    import platformdirs

    from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService
    from data.persistence.embedded_postgres import service as service_module
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    real_user_data = tmp_path / "real-user-data"
    real_password = real_user_data / "postgres" / "16" / "runtime" / "password"
    real_password.parent.mkdir(parents=True)
    real_password.write_bytes(b"real-user-credential")
    real_log_dir = real_user_data / "postgres-logs"
    real_log_dir.mkdir()
    before = _snapshot(real_password)
    before_log_entries = sorted(p.name for p in real_log_dir.iterdir())

    def _forbidden(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("配置已显式指定根目录，不得回退 platformdirs.user_data_dir")

    monkeypatch.setattr(platformdirs, "user_data_dir", _forbidden)

    roots = create_isolated_embedded_roots(tmp_path / "worker-0")
    config = AppConfig.model_validate({**roots, "embedded_pg_sidecar_path": str(tmp_path / "fake-sidecar")})

    # 构造服务会挂全局 FileHandler，测试内替换为无副作用 logger（R7 状态隔离）。
    monkeypatch.setattr(service_module, "_setup_service_logger", lambda _log_dir: logging.getLogger(__name__))
    EmbeddedPostgresService._reset_singleton()
    service = EmbeddedPostgresService.from_config(config)
    assert service._data_dir == Path(roots["embedded_pg_data_root"]) / "data"
    assert service._install_dir == Path(roots["embedded_pg_install_root"])
    assert service._log_dir == Path(roots["embedded_pg_log_dir"])

    # maintenance 服务读取同一份 AppConfig：data_dir 同样落在 tmp 内。
    monkeypatch.setattr(
        "services.embedded_pg_maintenance_service.ConfigHandler.load_config",
        staticmethod(lambda: dict(config.model_dump())),
    )
    EmbeddedPgMaintenanceService._reset_singleton()
    maintenance = EmbeddedPgMaintenanceService()
    _sidecar_path, data_dir = maintenance._get_sidecar_path_and_data_dir()
    assert Path(data_dir) == Path(roots["embedded_pg_data_root"]) / "data"
    assert Path(data_dir).resolve().is_relative_to(tmp_path.resolve())

    # 真实用户目录（含凭据）未被读写改写。
    assert _snapshot(real_password) == before
    assert sorted(p.name for p in real_log_dir.iterdir()) == before_log_entries
