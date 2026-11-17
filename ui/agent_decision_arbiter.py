"""统一必要决定仲裁层（检视 P02；UX P05/F36，J §2.2）。

壳层公共区出现「研究观察 + 必要决定」后，决定提交不能再由各入口各自为政。
本模块提供**进程内唯一**的仲裁点，约束三件事：

1. **单框仲裁**：同一时刻只允许一个决定提交意向（owner + decision_id）。
   另一界面/另一决定在途时拒绝，不排队、不静默重试。
2. **旧 Dialog 不穿透**：页面已有打开中的模态对话（``page._dialogs`` 中
   ``open=True`` 的项）时拒绝新提交——旧 Dialog 的 Enter/按钮不会落到新决定上。
3. **无键盘批准**：本层不提供键盘入口，也不解析 Enter；批准只能由用户对具体
   决定显式点击触发。IME 组合期因此不会经键盘穿透成批准（结构约束，非启发式）。

拒绝一律返回稳定 i18n key（调用方渲染原因），不伪造成功、不吞错误（R21）。
"""

from __future__ import annotations

import threading
from typing import Any

# 拒绝原因 i18n key（与 locales/*/strings.json 同名同义）
REFUSAL_DIALOG_OPEN = "agent_decision_blocked_dialog"
REFUSAL_BUSY = "agent_decision_busy"
REFUSAL_OWNER_CONFLICT = "agent_decision_owner_conflict"


def _has_open_modal_dialog_control(page: Any) -> bool:
    """页面是否已有打开中的模态对话（Flet ``use_dialog`` 的 ``_dialogs`` 控件）。"""

    dialogs = getattr(page, "_dialogs", None)
    controls = getattr(dialogs, "controls", None)
    if not controls:
        return False
    return any(bool(getattr(dialog, "open", False)) for dialog in controls)


class DecisionArbiter:
    """必要决定的单框仲裁状态机（线程安全，纯内存，无 IO）。

    ``begin_submit`` 是唯一的提交入口：校验「旧 Dialog 未开 + 无在途意向」后登记
    意向；``end_submit`` 必须由提交方在完成（成功或失败）后调用释放，避免永久占用。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: str | None = None
        self._decision_id: str | None = None

    @property
    def owner(self) -> str | None:
        """当前占用提交意向的界面标识（None 表示空闲）。"""

        return self._owner

    @property
    def inflight_decision_id(self) -> str | None:
        """当前在途提交的决定 ID（None 表示空闲）。"""

        return self._decision_id

    def begin_submit(
        self,
        owner: str,
        decision_id: str,
        *,
        has_open_modal_dialog: bool,
    ) -> str | None:
        """登记提交意向；成功返回 ``None``，拒绝返回原因 i18n key。

        旧 Dialog 优先级高于占用冲突：已有对话打开时用户可能正在核对旧内容，
        此时任何新决定都不得接走输入。
        """

        if has_open_modal_dialog:
            return REFUSAL_DIALOG_OPEN
        with self._lock:
            if self._owner is not None:
                if self._owner == owner:
                    return REFUSAL_BUSY
                return REFUSAL_OWNER_CONFLICT
            self._owner = owner
            self._decision_id = decision_id
            return None

    def end_submit(self, owner: str) -> None:
        """释放提交意向（幂等；非持有者调用不改变状态）。"""

        with self._lock:
            if self._owner == owner:
                self._owner = None
                self._decision_id = None

    def reset(self) -> None:
        """清空仲裁状态（测试隔离/会话销毁用）。"""

        with self._lock:
            self._owner = None
            self._decision_id = None


_arbiter = DecisionArbiter()


def get_decision_arbiter() -> DecisionArbiter:
    """获取进程内唯一仲裁器（会话级共享，决定输入跨页不串台）。"""

    return _arbiter


def has_open_modal_dialog(page: Any) -> bool:
    """页面是否已有打开中的模态对话（供调用方在 ``begin_submit`` 前采事实）。"""

    return _has_open_modal_dialog_control(page)


def submit_decision(*, page: Any, vm: Any, owner: str, decision_id: str) -> str | None:
    """统一决定提交管线（唯一入口）：仲裁 → 经 ``page.run_task`` 调度 VM 写调用。

    返回 ``None`` 表示已受理（提交在事件循环任务中执行，完成后释放仲裁）；
    返回 i18n key 表示拒绝（旧 Dialog 打开 / 他人占用），调用方据此如实提示。
    本函数不接受键盘事件，也不解析 Enter：批准只能来自显式点击（IME 不穿透）。
    """

    arbiter = get_decision_arbiter()
    refusal = arbiter.begin_submit(
        owner,
        decision_id,
        has_open_modal_dialog=has_open_modal_dialog(page),
    )
    if refusal is not None:
        return refusal

    async def _run_submit() -> None:
        try:
            await vm.submit_answer(decision_id)
        finally:
            arbiter.end_submit(owner)

    page.run_task(_run_submit)
    return None
