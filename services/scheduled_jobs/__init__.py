"""定时任务业务编排（review01-A2-1 下沉）。

将原 ``utils/scheduler_service.py`` 的三个业务 job（每日更新 / AI 概念标注 / 夜间 AI 预测）
的完整编排下沉到 services 层：SchedulerService 仅保留"注册 callable + 调度 + 上报进度"职责，
不再感知具体业务类（DataProcessor / TaskManager / AISelectionStrategy / ReviewManager），
消除 ``utils → data/services/strategies`` 方向性违规（契约 5）与隐藏三角依赖。

每个模块导出 ``build_<job>_job() -> Callable[[SchedulerService], Awaitable[None]]``：
- job 函数签名接收 SchedulerService 实例（提供 idempotency 状态与进度上报接口）
- 内部完成交易日检查、TaskManager.submit_task 提交与业务编排
"""
