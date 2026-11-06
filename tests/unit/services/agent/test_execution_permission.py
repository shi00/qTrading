"""A00：执行准入默认关闭与只读 allowlist（ADR-0008 §0.6）。"""

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from core.agent_types import AgentOperationError, SubmissionKey, SubmissionReceipt
from services.agent.permissions import BUSINESS_PROVIDER_ALLOWLIST, ExecutionPermission
from services.agent.service import AgentDaoProtocol, AgentService
from tests.builders.agent import make_request


class _AcceptOnlyDao:
    def __init__(self) -> None:
        self.accepted = 0

    async def accept_request(self, domain, request, digest):
        self.accepted += 1
        return SubmissionReceipt(
            status="accepted",
            key=request.key,
            intent_id=None,
            execution_id=None,
            input_revision=None,
            accepted_seq=None,
            problem=None,
        )


def _service(*, permission: ExecutionPermission | None = None) -> tuple[AgentService, _AcceptOnlyDao]:
    dao = _AcceptOnlyDao()
    return AgentService(domain="test-domain", dao=cast(AgentDaoProtocol, dao), permission=permission), dao


async def test_execution_is_closed_by_default_and_dispatches_nothing():
    service, dao = _service()
    request = replace(make_request(), mode="execute", key=SubmissionKey("test-domain", "tests", "exec-1"))

    with pytest.raises(AgentOperationError) as error:
        await service.submit(request)
    assert error.value.problem.code == "runtime_not_admitted"
    assert dao.accepted == 0


async def test_discuss_and_plan_still_work_with_closed_execution():
    service, dao = _service()

    assert (await service.submit(make_request())).status == "accepted"
    assert (
        await service.submit(replace(make_request(), mode="plan", key=SubmissionKey("test-domain", "tests", "p")))
    ).status == "accepted"
    assert dao.accepted == 2


async def test_execution_requires_both_independent_flags():
    for admitted, released in ((False, False), (True, False), (False, True)):
        service, dao = _service(permission=ExecutionPermission(runtime_admitted=admitted, release_permission=released))
        with pytest.raises(AgentOperationError) as error:
            await service.submit(replace(make_request(), mode="execute"))
        assert error.value.problem.code == "runtime_not_admitted"
        assert dao.accepted == 0


async def test_explicitly_admitted_execution_reaches_dao():
    service, dao = _service(permission=ExecutionPermission(runtime_admitted=True, release_permission=True))

    result = await service.submit(replace(make_request(), mode="execute"))
    assert result.status == "accepted"
    assert dao.accepted == 1


def test_permission_is_immutable_and_defaults_closed():
    permission = ExecutionPermission()
    assert permission.runtime_admitted is False
    assert permission.release_permission is False
    assert permission.allows("discuss") is True
    assert permission.allows("execute") is False

    with pytest.raises(FrozenInstanceError):
        permission.release_permission = True  # type: ignore[misc]  # 验证批准事实不可被运行期改写


def test_request_has_no_permission_field_to_self_approve():
    assert not hasattr(make_request(), "release_permission")
    assert not hasattr(make_request(), "runtime_admitted")


def test_provider_allowlist_is_exact_and_closed():
    assert BUSINESS_PROVIDER_ALLOWLIST == ("stock", "pool", "strategy", "backtest", "news")
    assert ExecutionPermission.is_provider_allowed("stock") is True
    assert ExecutionPermission.is_provider_allowed("browser") is False
    assert ExecutionPermission.is_provider_allowed("__import__") is False
