"""R8 红线 enforcement 测试。

验证 pyproject.toml [tool.pytest.ini_options] filterwarnings 配置：
- 必须包含 "error::DeprecationWarning" 以将 DeprecationWarning 转为异常
- 必须保留 "ignore::DeprecationWarning:pytest_asyncio.plugin" 以豁免 pytest_asyncio 内部警告

R8 红线：使用 _write_db(is_many=True) 进行批量写入（必须用 _save_upsert()）。
enforcement 通过删除 _write_db 的 is_many 参数彻底阻止违规调用，filterwarnings
error::DeprecationWarning 作为通用 DeprecationWarning 防护网保留。
"""

import warnings

import pytest


def test_r8_filterwarnings_error_deprecation_warning_active() -> None:
    """R8 enforcement: DeprecationWarning 必须被 filterwarnings 转为异常。

    当 pyproject.toml [tool.pytest.ini_options] filterwarnings 包含
    "error::DeprecationWarning" 时，warnings.warn(..., DeprecationWarning)
    会抛出异常而非仅打印。

    若此测试失败（DID NOT RAISE），说明 filterwarnings 配置未启用
    error::DeprecationWarning，R8 enforcement 失效。
    """
    with pytest.raises(DeprecationWarning, match="R8 enforcement probe"):
        warnings.warn("R8 enforcement probe", DeprecationWarning, stacklevel=2)
