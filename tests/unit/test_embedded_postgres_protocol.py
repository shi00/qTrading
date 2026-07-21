"""ConnectionInfo 数据类单元测试（Phase 2 §3.1）。"""

from __future__ import annotations

import dataclasses

import pytest

from data.persistence.embedded_postgres.protocol import ConnectionInfo


class TestConnectionInfo:
    def test_connection_info_fields(self) -> None:
        info = ConnectionInfo(
            url="postgresql+asyncpg://qtrading:secret@127.0.0.1:55432/qtrading",
            port=55432,
            pid=12345,
            data_dir="C:/fake/postgres/17/data",
        )
        assert info.url.startswith("postgresql+asyncpg://")
        assert info.port == 55432
        assert info.pid == 12345
        assert info.data_dir.endswith("data")

    def test_connection_info_is_frozen(self) -> None:
        info = ConnectionInfo(url="u", port=1, pid=2, data_dir="d")
        with pytest.raises(dataclasses.FrozenInstanceError):
            info.port = 9999  # type: ignore[misc]

    def test_connection_info_uses_slots(self) -> None:
        info = ConnectionInfo(url="u", port=1, pid=2, data_dir="d")
        # frozen + slots 组合下赋值会先触发 FrozenInstanceError；slots 的核心特征是
        # 实例无 __dict__，此处直接验证。
        assert not hasattr(info, "__dict__")

    def test_connection_info_equality(self) -> None:
        a = ConnectionInfo(url="u", port=1, pid=2, data_dir="d")
        b = ConnectionInfo(url="u", port=1, pid=2, data_dir="d")
        assert a == b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
