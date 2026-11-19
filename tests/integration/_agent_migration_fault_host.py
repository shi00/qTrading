"""发行兼容关键门故障宿主：双迁移中途硬退出（HANDOFF §8.1 第 2 条 / §10.6）。

以独立真实进程模拟「进程在迁移中途被杀」：

1. 先执行 DBOS 系统迁移并提交（``dbos.dbos_migrations`` 到达冻结合同值）；
2. 在 Datasource 迁移入口以 ``os._exit`` 硬退出（模拟被 kill），该迁移未提交。

父测试据此验证「中间失败不得启动、不丢已提交迁移、零派发」，并确认版本
守卫对不一致状态拒绝启动。退出码 3 = 按设计在 Datasource 迁移前被杀；
2 = 故障注入未生效（前置失败）。

环境变量：``AGENT_MIGRATION_FAULT_DB_URL``（含密码 URL 只经环境变量）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data.persistence.agent_migrator as migrator  # noqa: E402  # sys.path 先置


class _KilledDatasource:
    """Datasource 迁移替身：被杀前不做任何写入。"""

    @staticmethod
    def migrate(*_args: object, **_kwargs: object) -> None:
        os._exit(3)


def main() -> int:
    migrator.SQLAlchemyDatasource = cast(Any, _KilledDatasource)  # 故障注入：模拟中途被杀
    migrator.run_agent_runtime_migrations(os.environ["AGENT_MIGRATION_FAULT_DB_URL"])
    return 2  # 注入未生效


if __name__ == "__main__":
    sys.exit(main())
