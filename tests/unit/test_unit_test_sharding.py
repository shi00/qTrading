"""单测分片选择契约（P3-CI-Unit-Suite-Shard-Required）。

守护 ``tests/unit/_unit_test_sharding.py`` 与 ``tests/unit/conftest.py`` 的分片钩子：
环境变量解析必须显式失败、分片映射必须稳定且完整互斥、钩子必须只过滤测试文件。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

import tests.unit.conftest as unit_conftest
from tests.unit._unit_test_sharding import (
    SHARD_COUNT_ENV,
    SHARD_INDEX_ENV,
    parse_shard_env,
    shard_bucket,
)

pytestmark = pytest.mark.unit

UNIT_DIR = Path(__file__).resolve().parent


def test_sharding_disabled_without_env() -> None:
    assert parse_shard_env({}) is None


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({SHARD_INDEX_ENV: "0"}, "必须同时设置"),
        ({SHARD_COUNT_ENV: "4"}, "必须同时设置"),
        ({SHARD_INDEX_ENV: "0", SHARD_COUNT_ENV: "four"}, "必须为整数"),
        ({SHARD_INDEX_ENV: "4", SHARD_COUNT_ENV: "4"}, "必须位于"),
        ({SHARD_INDEX_ENV: "-1", SHARD_COUNT_ENV: "4"}, "必须位于"),
        ({SHARD_INDEX_ENV: "0", SHARD_COUNT_ENV: "0"}, ">= 1"),
    ],
)
def test_invalid_shard_env_is_rejected(env: dict[str, str], expected: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_shard_env(env)
    assert expected in str(excinfo.value), "半配置/非法分片参数必须给出可定位的显式错误"


def test_shard_bucket_is_stable_and_in_range() -> None:
    relative = "test_example.py"
    assert shard_bucket(relative, 4) == shard_bucket(relative, 4)
    for count in (1, 2, 3, 4, 7):
        assert 0 <= shard_bucket(relative, count) < count


def test_shards_partition_real_unit_test_files() -> None:
    files = sorted(path.relative_to(UNIT_DIR).as_posix() for path in UNIT_DIR.rglob("test_*.py"))
    assert len(files) > 100, "单测文件数量异常，分片契约应针对真实目录断言"
    for count in (2, 3, 4):
        buckets = Counter(shard_bucket(relative, count) for relative in files)
        assert set(buckets) == set(range(count)), f"{count} 分片存在空分片"
        mean = len(files) / count
        assert min(buckets.values()) >= mean * 0.4, f"{count} 分片分布过度倾斜: {buckets}"


def _hook(monkeypatch: pytest.MonkeyPatch, file_path: Path, index: int, count: int) -> bool | None:
    monkeypatch.setenv(SHARD_INDEX_ENV, str(index))
    monkeypatch.setenv(SHARD_COUNT_ENV, str(count))
    return unit_conftest.pytest_ignore_collect(file_path, config=None)


def test_hook_selects_only_own_shard_and_ignores_non_test_files(monkeypatch: pytest.MonkeyPatch) -> None:
    test_file = Path(__file__)
    relative = test_file.resolve().relative_to(UNIT_DIR).as_posix()
    own = shard_bucket(relative, 4)

    assert _hook(monkeypatch, test_file, own, 4) is False, "本分片测试文件必须被收集"
    assert _hook(monkeypatch, test_file, (own + 1) % 4, 4) is True, "他分片测试文件必须被排除"
    assert _hook(monkeypatch, UNIT_DIR / "conftest.py", own, 4) is None, "conftest 不得被分片过滤"
    assert _hook(monkeypatch, UNIT_DIR / "_unit_test_sharding.py", own, 4) is None, "辅助模块不得被分片过滤"


def test_hook_without_env_does_not_interfere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SHARD_INDEX_ENV, raising=False)
    monkeypatch.delenv(SHARD_COUNT_ENV, raising=False)
    assert unit_conftest.pytest_ignore_collect(Path(__file__), config=None) is None
