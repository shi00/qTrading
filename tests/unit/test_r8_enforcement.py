"""R8 红线 enforcement 测试。

验证 pyproject.toml [tool.pytest.ini_options] filterwarnings 配置：
- 必须包含 "error::DeprecationWarning" 以将 DeprecationWarning 转为异常
- 必须保留 "ignore::DeprecationWarning:pytest_asyncio.plugin" 以豁免 pytest_asyncio 内部警告

R8 红线：使用 _write_db(is_many=True) 进行批量写入（必须用 _save_upsert()）。
enforcement 依赖 filterwarnings error::DeprecationWarning 将 warnings.warn(DeprecationWarning)
转为异常，使违规调用在测试阶段立即失败。
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


@pytest.mark.asyncio
async def test_r8_deprecated_write_db_is_many_raises_deprecation_warning() -> None:
    """R8 enforcement: _write_db(is_many=True) 必须抛 DeprecationWarning。

    业务场景验证：当 is_many=True 时，BaseDao._write_db 通过 warnings.warn
    发出 DeprecationWarning。在 error::DeprecationWarning 配置下，此 warning
    应被转为异常并立即抛出，不会继续执行到 engine 检查。

    若此测试失败，说明 filterwarnings 未将 DeprecationWarning 转为异常，
    R8 红线 enforcement 失效。
    """
    from data.persistence.daos.base_dao import BaseDao

    # 创建 stub DAO，跳过 __init__ 避免触发 engine 初始化逻辑
    dao = BaseDao.__new__(BaseDao)
    dao.engine = None  # 显式 None；若 warning 未抛出会继续到 engine 检查

    with pytest.raises(DeprecationWarning, match="_write_db\\(is_many=True\\) is deprecated"):
        await dao._write_db("SELECT 1", [("v1",)], is_many=True)
