"""E2E: 真实 sidecar smoke — ready 事件 → Python 解析 → DB 迁移 → 主界面渲染端到端路径。

覆盖技术债 P3-E2E-Sidecar-Ready-Path：验证真实 sidecar binary 的完整启动链路，
仅在 release tag 触发的 CI job 运行（非 PR 必跑门禁）。

与 ``test_onboarding_embedded_real.py`` 的区别：
- 后者用 wizard 模式（onboarding_complete 未设置），验证 onboarding 流程；
  onboarding 模式下 ``TaskManager.init_db`` 不执行（DB 初始化在 onboarding 完成后才进行）
- 本测试用主界面模式（onboarding_complete=True），验证完整启动路径：
  1. 真实 sidecar 启动 → ready 事件 → Python ``_stdout_reader_task`` 解析
  2. ``TaskManager.init_db`` DB 迁移完成
  3. 主界面渲染（导航栏可见）

依赖：
- ``embedded_real_flet_page`` fixture（真实 sidecar + onboarding_complete=True 的主界面 app）

标记：
- ``pytest.mark.e2e``
- ``pytest.mark.embedded_real``
- ``pytest.mark.network``
- ``pytest.mark.slow``
"""

import pytest

from tests.e2e.helpers.app_launcher import PROJECT_ROOT
from tests.e2e.timeouts import TIMEOUTS
from ui.i18n import I18n

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.embedded_real,
    pytest.mark.network,
    pytest.mark.slow,
]


def _read_app_log() -> str:
    """读取 e2e-flet-app.log 全文用于断言诊断。

    R9: 日志可能含 DB 连接串（已由 ConfigHandler/EmbeddedPostgresService 注册为 secret
    并经 DataSanitizer 脱敏），此处只读取用于断言失败诊断，不回显到 CI 控制台
    （断言消息仅截取尾部 2000 字符）。
    """
    log_path = PROJECT_ROOT / "logs" / "e2e-flet-app.log"
    return log_path.read_text(encoding="utf-8", errors="replace")


async def test_sidecar_real_smoke_ready_migration_main_ui(embedded_real_flet_page) -> None:
    """E2E: 真实 sidecar smoke — ready → migration → 主界面渲染完整路径。

    验证：
    1. 主界面渲染（导航栏 ``nav_screener`` 可见）— 隐含 sidecar 启动 + DB 迁移成功
    2. sidecar ready 事件被 Python 解析（日志含 ``[Bootstrap] embedded postgres ready on 127.0.0.1:``）
    3. DB 迁移完成（日志含 ``[TaskManager] init_db``）

    失败诊断：断言失败时附加 e2e-flet-app.log 尾部 2000 字符，CI 上传 logs/ + e2e-artifacts/ 为 artifact。
    """
    # 1. 验证主界面渲染（导航栏可见）— 超时设为 NAV（slow marker 会自动倍率 2.5x）
    screener_label = I18n.get("nav_screener")
    await embedded_real_flet_page.expect_text(screener_label, timeout_ms=TIMEOUTS.NAV)

    # 2. 验证 sidecar ready 事件被 Python _stdout_reader_task 解析
    log_content = _read_app_log()
    ready_marker = "[Bootstrap] embedded postgres ready on 127.0.0.1:"
    assert ready_marker in log_content, (
        f"sidecar ready 事件未被 Python 解析（日志未含 '{ready_marker}'）。"
        f"e2e-flet-app.log 尾部 2000 字符:\n{log_content[-2000:]}"
    )

    # 3. 验证 DB 迁移完成
    migration_marker = "[TaskManager] init_db"
    assert migration_marker in log_content, (
        f"DB 迁移未完成（日志未含 '{migration_marker}'）。e2e-flet-app.log 尾部 2000 字符:\n{log_content[-2000:]}"
    )
