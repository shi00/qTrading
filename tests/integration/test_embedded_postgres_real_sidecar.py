"""真实 Rust sidecar binary + 真实 embedded PostgreSQL 集成测试。

覆盖 spec §3.2：真实 sidecar 启动协议端到端验证。
- ready JSON 解析（schema v1 / status running / port > 0）
- ConnectionInfo.url 可连
- password_file 真实创建
- SHA256 校验通过路径
- stop() 进程释放 + 端口释放
- 日志收集

依赖：
- ``real_sidecar_binary`` fixture（定位真实 sidecar binary，缺失则 skip）
- ``real_embedded_pg`` fixture（session-scoped 真实 PG 实例）

标记：
- ``pytest.mark.integration``
- ``pytest.mark.embedded_real``

loop_scope：
- 所有 async 测试用 ``loop_scope="session"``（对齐 ``real_embedded_pg`` session fixture，
  避免跨 loop ``Future attached to a different loop`` 错误，见 project_memory 教训）
"""

# pyright: reportPrivateUsage=false
# 测试需访问 EmbeddedPostgresService 私有属性（_install_dir / _sidecar_binary 等）验证内部状态

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.embedded_postgres.service import EmbeddedPostgresService
from tests._helpers import create_test_engine

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


class TestRealSidecarStartupProtocol:
    """真实 sidecar 启动协议验证（复用 ``real_embedded_pg`` session fixture）。

    所有测试复用同一 sidecar 实例（session-scoped），避免重复 initdb（30s+）。
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_sidecar_start_returns_valid_connection_info(self, real_embedded_pg: ConnectionInfo) -> None:
        """``start()`` 返回 ConnectionInfo，字段非空且类型正确。"""
        info = real_embedded_pg
        assert isinstance(info, ConnectionInfo)
        assert info.url.startswith("postgresql+asyncpg://")
        assert info.port > 0
        assert info.pid >= 0
        assert info.data_dir
        assert info.host

    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_sidecar_url_is_connectable(self, real_embedded_pg: ConnectionInfo) -> None:
        """``ConnectionInfo.url`` 可连（``SELECT 1``）。"""
        info = real_embedded_pg
        engine = create_test_engine(info.url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                assert result.scalar() == 1
        finally:
            await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_sidecar_password_file_created(self, real_embedded_pg: ConnectionInfo) -> None:
        """``password_source == 'password_file'``，password 文件真实存在且非空。

        password 文件路径对齐 ``service._start_sync_impl``：
        ``runtime_dir = data_dir.parent / "runtime"``，``password_file = runtime_dir / "password"``。
        """
        info = real_embedded_pg
        assert info.password_source == "password_file"
        password_file = Path(info.data_dir).parent / "runtime" / "password"
        assert password_file.is_file(), f"password file not found: {password_file}"
        assert password_file.read_text(encoding="utf-8").strip(), "password file is empty"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_sidecar_sha256_verification_passes(
        self,
        real_embedded_pg: ConnectionInfo,
        real_sidecar_binary: Path,
    ) -> None:
        """真实 binary + 真实 ``.sha256`` 文件校验通过（``start`` 成功即通过）。

        ``real_embedded_pg`` fixture 的 ``start()`` 成功即证明 ``_verify_sidecar_sha256``
        校验通过或跳过。本测试额外验证 ``.sha256`` 文件内容（如存在）与 binary 实际
        SHA256 匹配，覆盖真实校验路径。

        缺失 ``.sha256`` 文件时（本地开发无 ``SIDECAR_SHA256``），仅验证 ``start`` 成功
        （service 会跳过校验，开发场景容错）。
        """
        # real_embedded_pg fixture 的 start() 成功即证明校验通过或跳过
        assert real_embedded_pg.url

        sha256_path = real_sidecar_binary.with_suffix(real_sidecar_binary.suffix + ".sha256")
        if not sha256_path.exists():
            pytest.skip(
                ".sha256 file missing (local dev without SIDECAR_SHA256); sha256 verification skipped by service"
            )

        expected = sha256_path.read_text(encoding="utf-8").strip().split()[0].lower()
        actual = hashlib.sha256(real_sidecar_binary.read_bytes()).hexdigest().lower()
        assert actual == expected, "sha256 mismatch between binary and .sha256 file"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_sidecar_logs_collected(self, real_embedded_pg: ConnectionInfo) -> None:
        """``collect_logs_summary()`` 返回 4 个日志 key，``sidecar.log`` 和 ``embedded-pg-service.log`` 非空。"""
        service = EmbeddedPostgresService.get_instance()
        logs = service.collect_logs_summary()

        assert set(logs.keys()) == {
            "sidecar.log",
            "sidecar.stderr.log",
            "postgres-start.log",
            "embedded-pg-service.log",
        }
        # sidecar.log 应有 tracing 日志（非 <missing>）
        assert logs["sidecar.log"] != "<missing>", "sidecar.log should exist after real sidecar start"
        # embedded-pg-service.log 应有 Python 侧日志
        assert logs["embedded-pg-service.log"] != "<missing>", (
            "embedded-pg-service.log should exist after real sidecar start"
        )


class TestRealSidecarStopReleasesProcess:
    """``stop()`` 行为验证（独立 service 实例，不破坏 ``real_embedded_pg`` session fixture）。

    用保存/恢复 ``_instance`` 的方式创建独立 service 实例：
    - 复用 ``real_embedded_pg`` 的 ``install_dir`` 和 ``sidecar_binary``（避免 PG binaries 重复下载）
    - 用独立 ``data_dir``（避免 PGDATA 锁冲突）
    - 测试后恢复原 ``_instance``，不影响后续测试使用 ``real_embedded_pg``
    """

    @pytest.mark.asyncio(loop_scope="session")
    async def test_real_sidecar_stop_releases_process(self, real_embedded_pg: ConnectionInfo) -> None:
        """``stop()`` 后 ``_process is None``，``_connection_info is None``，端口释放。"""
        # 获取原 service 的路径配置（复用，避免 PG binaries 下载）
        original_service = EmbeddedPostgresService.get_instance()
        install_dir = original_service._install_dir
        sidecar_binary = original_service._sidecar_binary
        log_dir = original_service._log_dir

        # 保存原单例，创建独立实例（绕过 __new__ 单例逻辑）
        original_instance = EmbeddedPostgresService._instance
        EmbeddedPostgresService._instance = None

        data_root: Path | None = None
        try:
            data_root = Path(tempfile.mkdtemp(prefix="stop_test_"))
            service = EmbeddedPostgresService(
                sidecar_binary=sidecar_binary,
                data_dir=data_root / "data",
                install_dir=install_dir,
                log_dir=log_dir,
                start_timeout=300.0,
            )
            info = await service.start()
            # start 成功：进程运行中
            assert service._process is not None
            assert service._process.poll() is None

            await service.stop()
            # stop 后：进程与连接信息已清理
            assert service._process is None
            assert service._connection_info is None

            # 端口释放：连 info.url 应失败
            # asyncpg 连接拒绝抛 OSError（ConnectionRefusedError 子类），
            # SQLAlchemy 包装为 OperationalError；两者皆覆盖
            engine = create_test_engine(info.url)
            try:
                with pytest.raises((OSError, OperationalError)):  # noqa: weak-assertion 端口释放验证：exception type 已收窄到连接失败类（OSError/OperationalError），message 跨平台差异大不 match
                    async with engine.connect() as conn:
                        await conn.execute(text("SELECT 1"))
            finally:
                await engine.dispose()
        finally:
            if data_root is not None:
                shutil.rmtree(data_root, ignore_errors=True)
            # 恢复原单例（real_embedded_pg 的 service 实例）
            EmbeddedPostgresService._instance = original_instance
