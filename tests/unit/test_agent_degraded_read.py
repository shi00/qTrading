"""Agent 读路径降级/故障注入行为（第四类首版验收缺口）。

四类降级各自断言同一组不变量：

1. 不崩溃（异常类型受控，不以未捕获的底层异常终止进程）；
2. 不静默伪装成功（不返回空页/空成果/成功回执冒充正常）；
3. 显式 unknown/降级标记（``None`` / ``authoritative=False`` / 稳定 ``error_code``），
   不以业务上合法的具体值填充（R21）；
4. 用户可读出路（明确的稳定原因码 + 仍可用的只读替代路径）。

覆盖：

- 无模型/模型不可用：``resolve_agent_model_binding`` 路由缺失时零外发显式失败，
  历史读取不依赖模型仍可用；
- DB 不可用：读取显式抛出 ``DatabaseQueryError``，清理准入降级为「未知」而非
  「可清理」；引擎已释放不被伪装成未知（R5）；
- 磁盘不足：导出暂存 IO 失败落可确证失败回执，不发布、不写成功阶段；
- 索引/投影失效：关键词搜索缺投影显式 ``OPERATION_UNKNOWN``，会话浏览仍可用。

复用既有夹具：服务 facade 替身、历史查询构造、叶执行上下文、交付编排替身均来自
既有单测模块，不另建平行体系。真实 PG 与真实磁盘满属验收环境。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, SessionReadQuery
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError
from services.agent_service.cleanup_ports import DaoCleanupAdmissionFacts, DaoNativeVerification
from services.agent_service.leaf_runtime import resolve_agent_model_binding
from tests.unit.services.test_agent_leaf_runtime import _context as _leaf_context
from tests.unit.test_agent_dao_history_search_projection import _query as _history_query
from tests.unit.test_agent_delivery_service import (
    FakeArtifacts,
    FakePhaseDao,
    FakeStore,
    _command as _delivery_command,
    _context as _delivery_context,
    _read as _artifact_read,
    _service as _delivery_service,
)
from tests.unit.test_agent_service_facade import _FakeDao, _service as _facade_service

pytestmark = pytest.mark.unit


class _DbDownDao(_FakeDao):
    """DB 不可用替身：读取入口显式抛出，不返回空页。"""

    async def get_session_read(self, query: SessionReadQuery, *, user_scope: str) -> Any:
        raise DatabaseQueryError("connection refused")


class _FailingFactsDao:
    """清理事实端口替身：任一事实读取按注入异常失败。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def load_cleanup_job(self, user_scope: str, cleanup_job_id: str) -> Any:
        raise self._exc

    async def read_cleanup_native_facts(self, **kwargs: Any) -> Any:
        raise self._exc

    async def read_cleanup_content_manifest(self, user_scope: str, cleanup_job_id: str) -> Any:
        raise self._exc


def _session_query() -> SessionReadQuery:
    return SessionReadQuery(session_id="s1")


class TestModelUnavailable:
    async def test_route_missing_fails_closed_while_history_read_survives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _no_route(_scenario_id: str) -> None:
            return None

        monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _no_route)

        with pytest.raises(RuntimeError) as excinfo:
            await resolve_agent_model_binding(_leaf_context())
        message = str(excinfo.value)
        assert "agent_research" in message
        assert "zero model dispatch" in message

        # 读路径不依赖模型：模型不可用时历史读取仍返回既有事实（不崩、不需外发）。
        fake = _FakeDao()
        service = _facade_service(fake)
        assert await service.read_session(_session_query(), user_scope="u1") is fake.session


class TestDatabaseUnavailable:
    async def test_read_raises_typed_error_and_admission_reports_unknown(self) -> None:
        service = _facade_service(_DbDownDao())
        with pytest.raises(DatabaseQueryError) as excinfo:
            await service.read_session(_session_query(), user_scope="u1")
        assert str(excinfo.value) == "connection refused"

        facts = _FailingFactsDao(DatabaseQueryError("db down"))
        # 准入事实不可读 → None（未知），不得当成「可清理」。
        assert await DaoCleanupAdmissionFacts(facts).derive_precondition("u1", "job-1") is None
        verification = await DaoNativeVerification(facts).verify(("e-1",), user_scope="u1")
        assert verification.authoritative is False

    async def test_disposed_engine_propagates_instead_of_becoming_unknown(self) -> None:
        facts = _FailingFactsDao(EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError) as admission_exc:
            await DaoCleanupAdmissionFacts(facts).derive_precondition("u1", "job-1")
        assert str(admission_exc.value) == "disposed"
        with pytest.raises(EngineDisposedError) as verify_exc:
            await DaoNativeVerification(facts).verify(("e-1",), user_scope="u1")
        assert str(verify_exc.value) == "disposed"


class TestDiskExhaustedExport:
    async def test_stage_io_error_is_known_failure_without_fake_success(self) -> None:
        artifacts = FakeArtifacts([_artifact_read()])
        dao = FakePhaseDao()
        store = FakeStore()
        store.stage_error = OSError("No space left on device")
        service = _delivery_service(artifacts=artifacts, dao=dao, store=store)

        outcome = await service.deliver(_delivery_command(), _delivery_context())

        # 可确证失败：稳定原因码 + 未尝试发布 + 无输出摘要 + 暂存未冒充已清理。
        assert outcome.receipt.error_code == "delivery_local_io_error"
        assert outcome.receipt.publish_effect == "not_attempted"
        assert outcome.receipt.phase == "manifest_saved"
        assert outcome.receipt.output_digest is None
        assert outcome.staged_cleaned is False
        assert store.publish_attempts == 0
        # 用户可读出路：磁盘释放后从上次可靠阶段重试，不产生半文件成功记录。
        assert dao.phases == ["manifest_saved"]


class TestSearchIndexUnavailable:
    async def test_missing_projection_fails_explicitly_and_browse_path_remains(self) -> None:
        fake = _FakeDao()
        fake.error = AgentServiceError(
            AgentErrorCode.OPERATION_UNKNOWN,
            "history search projection missing",
            "u1/history",
        )
        service = _facade_service(fake)

        with pytest.raises(AgentServiceError) as excinfo:
            await service.search(_history_query(query_text="回撤"), user_scope="u1")
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert excinfo.value.sanitized_message == "history search projection missing"

        # 搜索降级为显式失败，但按会话浏览的只读替代路径仍可用。
        assert await service.read_session(_session_query(), user_scope="u1") is fake.session
