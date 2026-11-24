"""BusinessResearchViewModel — 从既有业务对象发起研究（准备草稿，不发送）R2-I06。

边界（CLAUDE.md §3.2 / docs/patterns/mvvm.md）：

- **只准备不发送**：命令只创建/写入草稿（``save_draft``）与登记草稿来源，
  不调用 ``submit``、不派发研究、不产生费用；用户必须在自己明确的发送动作后才研究；
- **准确对象/版本**：草稿携带由业务入口冻结的对象标识、内容摘要版本与可得时点
  （回测走 ``read_backtest_snapshot`` 严格读；其余入口冻结当前展示快照），
  不以「当前快照」冒充历史时点（R24），也不伪造可得时点（R21）；
- **不重跑业务**：本 VM 只读现存对象，不回放选股/回测；返回原业务现场由既有页面
  状态保持（本 VM 不导航、不改业务页状态）；
- **保稿**：来源失效/重复发起时不删除、不覆盖既有草稿，如实给出原因 key；
- **写结果未知**：提交已发起但确认中断只报「未知」并保留原写键待查回，不换键补写、
  不先清草稿（N12）；确定未写才报一般失败（可同键重试）；
- VM 不 import flet / 不感知 locale（只产出 i18n key）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from core.agent_types import Purpose
from services.agent_service.business_research import (
    BusinessDraftAlreadyPrepared,
    BusinessDraftCommitUnknown,
    BusinessObjectSnapshot,
    BusinessResearchPreparer,
    BusinessSourceUnavailable,
)
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

#: 桌面单用户域（与 app.agent_wiring 默认 user_scope 一致；UI 层不 import app）。
LOCAL_DESKTOP_USER_SCOPE = "local-desktop"

#: 不可用原因 key（未接线 / 运行时未就绪，R21：不伪装可用）。
UNAVAILABLE_NOT_WIRED = "agent_research_not_wired"
UNAVAILABLE_NOT_READY = "agent_research_not_ready"

#: 结束态提示 key。
NOTICE_DRAFT_PREPARED = "agent_research_draft_prepared"
NOTICE_DRAFT_EXISTS = "agent_research_draft_exists"
NOTICE_SOURCE_UNAVAILABLE = "agent_research_source_unavailable"
NOTICE_COMMIT_UNKNOWN = "agent_research_draft_commit_unknown"
NOTICE_PREPARE_FAILED = "agent_research_prepare_failed"

#: 业务入口对象类别（与草稿 object_context.object_kind 同值域）。
OBJECT_KIND_STOCK = "stock"
OBJECT_KIND_CANDIDATE = "screener_candidate"
OBJECT_KIND_NEWS = "news_item"
OBJECT_KIND_BACKTEST = "backtest_result"


class BacktestSnapshotReader(Protocol):
    """回测结果严格读端口（生产为 ``BacktestService``）。"""

    async def get_result(self, run_id: str, *, suppress_errors: bool = True) -> dict | None: ...


@dataclass(frozen=True)
class BusinessResearchRequest:
    """业务入口冻结的准确对象（只含用户已看到的字段；值一律为文本以保 CanonicalJson）。

    ``summary`` 为本次研究对象的内容摘要（键值对文本化）；``available_at`` 为
    该内容对用户可得的时点（未知填 ``None``，由服务层 fail-closed，不伪造）。
    """

    source_module: str
    object_kind: str
    object_id: str
    display_name: str
    summary: tuple[tuple[str, str], ...]
    available_at: str | None
    purpose: Purpose = Purpose.END_OF_DAY_RESEARCH
    period: str | None = None

    def as_snapshot(self) -> BusinessObjectSnapshot:
        return BusinessObjectSnapshot(
            source_module=self.source_module,
            object_kind=self.object_kind,
            object_id=self.object_id,
            display_name=self.display_name,
            summary=dict(self.summary),
            available_at=self.available_at,
            period=self.period,
        )


@dataclass(frozen=True)
class BusinessResearchState:
    """不可变快照：``notice_key`` / ``unavailable_reason_key`` 均为 i18n key。"""

    available: bool = False
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED
    preparing: bool = False
    last_session_id: str | None = None
    last_draft_revision: int | None = None
    notice_key: str | None = None
    last_error_code: str | None = None


class BusinessResearchViewModel(ObservableViewModelMixin[BusinessResearchState]):
    """从业务对象准备研究草稿的唯一 UI 命令入口。"""

    def __init__(
        self,
        service: Any | None,
        *,
        user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
        unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
    ) -> None:
        self._user_scope = user_scope
        self._service_key = id(service) if service is not None else None
        self._preparer = BusinessResearchPreparer(service) if service is not None else None
        self._state = BusinessResearchState(
            available=self._preparer is not None,
            unavailable_reason_key=unavailable_reason_key if self._preparer is None else "",
        )
        self._init_mixin_fields()

    async def prepare(
        self,
        request: BusinessResearchRequest,
        *,
        backtest_reader: BacktestSnapshotReader | None = None,
    ) -> None:
        """准备草稿（不发送）：成功仅有草稿与来源引用，``sent`` 恒为 False。"""

        if self._preparer is None:
            self._set_state(notice_key=self._state.unavailable_reason_key, last_error_code=None)
            return
        if self._state.preparing:
            return  # 重复发起：在途未结束时不再起第二份准备
        self._set_state(preparing=True, notice_key=None, last_error_code=None)
        # 写调用阶段：页面卸载放行自然完成，不宣称「未准备」（磁盘 SSOT）。
        self._mark_inflight_committing()
        try:
            if request.object_kind == OBJECT_KIND_BACKTEST:
                if backtest_reader is None:
                    raise BusinessSourceUnavailable("backtest_reader_unavailable")
                receipt = await self._preparer.prepare_backtest(
                    backtest_reader,
                    user_scope=self._user_scope,
                    result_id=request.object_id,
                    session_title=f"研究：{request.display_name}",
                )
            else:
                receipt = await self._preparer.prepare_object(
                    request.as_snapshot(),
                    user_scope=self._user_scope,
                    session_title=f"研究：{request.display_name}",
                )
        except asyncio.CancelledError:
            self._set_state(preparing=False)
            raise
        except BusinessDraftAlreadyPrepared as exc:
            # 保留既有草稿（可能已被用户编辑），不覆盖、不重发。
            self._set_state(
                preparing=False,
                notice_key=NOTICE_DRAFT_EXISTS,
                last_session_id=exc.session_id,
                last_draft_revision=exc.draft_revision,
            )
            return
        except BusinessSourceUnavailable as exc:
            # 来源失效/时点未知：如实说明，不生成空草稿（R21）；既有草稿不受影响。
            self._set_state(preparing=False, notice_key=NOTICE_SOURCE_UNAVAILABLE, last_error_code=exc.reason)
            return
        except BusinessDraftCommitUnknown as exc:
            # 提交已发起但确认中断：结果未知（草稿可能已保存），保原写键按原身份查回；
            # 不换键补写、不先清草稿（N12）。
            cause = exc.__cause__
            self._set_state(
                preparing=False,
                notice_key=NOTICE_COMMIT_UNKNOWN,
                last_session_id=exc.session_id,
                last_error_code=self._error_code(cause if isinstance(cause, Exception) else exc),
            )
            return
        except Exception as exc:  # facade 已脱敏；未知错误如实报码
            self._set_state(
                preparing=False,
                notice_key=NOTICE_PREPARE_FAILED,
                last_error_code=self._error_code(exc),
            )
            return
        self._set_state(
            preparing=False,
            last_session_id=receipt.session_id,
            last_draft_revision=receipt.draft_revision,
            notice_key=NOTICE_DRAFT_PREPARED,
            last_error_code=None,
        )

    @staticmethod
    def _error_code(error: Exception) -> str:
        code = getattr(error, "code", None)
        value = getattr(code, "value", None)
        if isinstance(value, str) and value:
            return value
        return type(error).__name__


_shared_vm: BusinessResearchViewModel | None = None


def shared_business_research(
    service: Any | None,
    *,
    user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
) -> BusinessResearchViewModel:
    """取本会话共享的准备 VM（同一 facade 复用；facade 变化或已销毁则重建）。"""

    global _shared_vm
    if _shared_vm is None or _shared_vm.disposed or _shared_vm._service_key != id(service):
        stale = _shared_vm
        _shared_vm = BusinessResearchViewModel(
            service,
            user_scope=user_scope,
            unavailable_reason_key=unavailable_reason_key,
        )
        if stale is not None and not stale.disposed:
            stale.dispose()
    return _shared_vm


def reset_shared_business_research() -> None:
    """销毁共享实例（测试隔离/会话重建；幂等）。"""

    global _shared_vm
    vm = _shared_vm
    _shared_vm = None
    if vm is not None:
        vm.dispose()


def resolve_business_research_service(agent_wiring: Any | None) -> tuple[Any | None, str]:
    """从 P01 接线结果解析 facade 与不可用原因 key（未接线/未就绪如实返回）。"""

    if agent_wiring is None:
        return None, UNAVAILABLE_NOT_WIRED
    service = getattr(agent_wiring, "service", None)
    if service is None:
        return None, UNAVAILABLE_NOT_WIRED
    if not bool(getattr(agent_wiring, "ready", False)):
        return None, UNAVAILABLE_NOT_READY
    return service, ""
