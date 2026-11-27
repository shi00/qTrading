"""角色目录 / 会话模型的组合根构造单测（Task 11）。

覆盖：

- ``_build_role_services`` 的端口映射：精确断言映射到 ``app_state`` 的真实函数
  与参数（不用 ``assert_called*``）；
- ``AgentWiring`` 未注入时两字段为 ``None``（未接线不伪造，构造期零副作用）。

只测纯构造 helper：``wire_agent_runtime`` 的成功路径复用
``tests/unit/test_agent_wiring.py`` 的既有宿主夹具（另在该文件补一条断言），
本文件不重复构造重型宿主。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app import agent_wiring as wiring_mod
from app.agent_wiring import AgentWiring, _build_role_services
from services.agent_service.roles import RoleCatalogService
from services.agent_service.session_model import SessionModelStore

pytestmark = pytest.mark.unit


async def test_role_catalog_ports_map_to_app_state_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    """角色目录的三个端口分别映射到读 / 条件写 / 前缀扫描，键与参数逐项核对。"""

    engine = object()
    calls: list[tuple[Any, ...]] = []

    async def _get(_engine: Any, key: str) -> str | None:
        calls.append(("get", _engine, key))
        return None

    async def _list(_engine: Any, prefix: str) -> dict[str, str]:
        calls.append(("list", _engine, prefix))
        return {}

    async def _set_if(_engine: Any, key: str, expected: str | None, value: str) -> bool:
        calls.append(("set_if", _engine, key, expected, value))
        return True

    monkeypatch.setattr(wiring_mod, "get_app_state", _get)
    monkeypatch.setattr(wiring_mod, "list_app_state", _list)
    monkeypatch.setattr(wiring_mod, "set_app_state_if", _set_if)

    catalog, _session_models = _build_role_services(engine)
    assert isinstance(catalog, RoleCatalogService)

    assert await catalog.resolve("role-a") is None
    assert calls == [("get", engine, "agent.role.def.role-a")]

    calls.clear()
    assert await catalog.list_roles() == ()
    assert calls == [("list", engine, "agent.role.def.")]

    calls.clear()
    created = await catalog.create_new("role_b", "角色B", "正常提示词", updated_by="tester")
    assert created.role_id == "role_b"
    assert created.role_version == "r1"
    assert len(calls) == 1
    assert calls[0][:4] == ("set_if", engine, "agent.role.def.role_b", None)
    assert '"role_id":"role_b"' in calls[0][4]


async def test_session_model_ports_map_to_strict_write_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话模型选择：读取 / 严格覆盖写（``strict=True``）/ 按键删除逐项核对。"""

    engine = object()
    calls: list[tuple[Any, ...]] = []

    async def _get(_engine: Any, key: str) -> str | None:
        calls.append(("get", _engine, key))
        return None

    async def _set(_engine: Any, key: str, value: str, *, strict: bool = False) -> None:
        calls.append(("set", _engine, key, value, strict))
        return None

    async def _delete(_engine: Any, key: str) -> bool:
        calls.append(("delete", _engine, key))
        return True

    monkeypatch.setattr(wiring_mod, "get_app_state", _get)
    monkeypatch.setattr(wiring_mod, "set_app_state", _set)
    monkeypatch.setattr(wiring_mod, "delete_app_state", _delete)

    _catalog, session_models = _build_role_services(engine)
    assert isinstance(session_models, SessionModelStore)

    assert await session_models.read("s-1") is None
    assert calls == [("get", engine, "agent.session.model.s-1")]

    calls.clear()
    await session_models.write("s-1", "conn-a")
    assert calls == [("set", engine, "agent.session.model.s-1", "conn-a", True)]

    calls.clear()
    assert await session_models.clear("s-1") is True
    assert calls == [("delete", engine, "agent.session.model.s-1")]


def test_role_fields_default_to_none_without_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """未注入角色端口时两字段为 ``None``，构造不得触碰 ``app_state``（零副作用）。"""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("构造期不得访问 app_state")

    monkeypatch.setattr(wiring_mod, "get_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "set_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "delete_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "list_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "set_app_state_if", _boom)

    wiring = AgentWiring(service=None, bridge=cast(Any, SimpleNamespace()))

    assert wiring.role_catalog is None
    assert wiring.session_models is None


def test_build_role_services_constructs_without_touching_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_build_role_services`` 只做端口绑定：构造期不读 / 写 / 扫 ``app_state``。"""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("构造期不得访问 app_state")

    monkeypatch.setattr(wiring_mod, "get_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "set_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "delete_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "list_app_state", _boom)
    monkeypatch.setattr(wiring_mod, "set_app_state_if", _boom)

    catalog, session_models = _build_role_services(object())

    assert isinstance(catalog, RoleCatalogService)
    assert isinstance(session_models, SessionModelStore)


async def test_model_connection_validator_requires_research_route_and_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """角色模型覆盖校验：只接受 ``agent_research`` 路由候选且已启用的连接。"""

    from services import model_configuration_service as model_config_mod

    class _FakeModelConfigurationService:
        async def get_snapshot(self) -> Any:
            return SimpleNamespace(
                scenario_routes=(
                    {
                        "scenario_id": "agent_research",
                        "enabled": True,
                        "candidates": ("conn-a", "conn-off"),
                    },
                ),
                connections=(
                    {"connection_id": "conn-a", "enabled": True},
                    {"connection_id": "conn-off", "enabled": False},
                    {"connection_id": "conn-other", "enabled": True},
                ),
            )

    monkeypatch.setattr(model_config_mod, "ModelConfigurationService", _FakeModelConfigurationService)

    catalog, _session_models = _build_role_services(object())
    validator = catalog._model_connection_validator
    assert validator is not None

    assert await validator("conn-a") is True
    assert await validator("conn-off") is False, "停用连接不得作为角色覆盖"
    assert await validator("conn-other") is False, "路由外连接不得作为角色覆盖"
    assert await validator("conn-missing") is False
