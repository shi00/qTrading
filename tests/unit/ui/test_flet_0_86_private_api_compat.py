"""Flet 0.86.0 私有 API 兼容性验证（spike）。

目的：在 spike worktree（flet 0.86.0）中固化项目深度依赖的 Flet 私有 API
契约。任一断言失败即表示 0.86.0 引入了破坏性变更，需记录到 spike 报告并
标记升级阻塞（Plans-flet-0.86.0-upgrade.md Task 0.2 DoD）。

依赖点（项目内使用位置）：
- ``_context_page`` ContextVar: ``tests/unit/ui/conftest.py`` 的
  ``_reset_context_page`` autouse fixture、``tests/unit/ui/component_renderer.py``
  的 ``attach_fake_page``、多个 contract 测试的 ``test_uses_ft_context_page``
- ``Component, Renderer``: ``tests/unit/ui/component_renderer.py``、
  ``tests/unit/ui/test_system_tab_contract.py``
- ``ft.Control.page.fget``: ``tests/unit/ui/conftest.py`` 与
  ``tests/integration/conftest.py`` 的 V1 page 兼容桩（monkeypatch fget）
- ``PubSubClient``: ``tests/unit/ui/test_spike_pubsub_runtask_pattern.py``
  （R.5.1 关注 ``unsubscribe_topic`` session-scoped 语义）
- ``Observable, ObservableList``: ``scripts/spike_ui_debt/spike_observable.py``

验证手段：``import`` + ``hasattr()`` + ``inspect.signature()``。
"""

from __future__ import annotations

import contextvars
import inspect

import flet as ft
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _v1_page_compat():
    """禁用 conftest 的 V1 page 兼容桩。

    本测试需要验证 ``ft.Control.page`` 的原始 property 与 fget 签名，
    conftest 的 ``_v1_page_compat`` autouse fixture 会 monkeypatch
    ``ft.Control.page``，必须在此禁用以访问原始属性。
    """
    yield


def test_context_page_is_contextvar() -> None:
    """``flet.controls.context._context_page`` 仍为 ContextVar。

    conftest 的 ``_reset_context_page`` fixture 与 component_renderer 的
    ``attach_fake_page`` 均依赖 ``_context_page.set/get``，若类型变更将破坏
    全部声明式 UI 测试。
    """
    from flet.controls.context import _context_page

    assert isinstance(_context_page, contextvars.ContextVar)
    # ContextVar 名称用于调试与日志，变更不影响行为但应记录
    assert _context_page.name == "flet_session_page"


def test_component_and_renderer_are_classes() -> None:
    """``flet.components.component.Component`` 与 ``Renderer`` 仍为类。

    component_renderer 的 ``attach_fake_page`` 通过 ``Renderer.render`` 驱动
    声明式组件渲染；``Component`` 是所有声明式组件的基类。
    """
    from flet.components.component import Component, Renderer

    assert inspect.isclass(Component)
    assert inspect.isclass(Renderer)
    # Renderer 必须保留 render 入口（component_renderer 调用链依赖）
    assert hasattr(Renderer, "render"), "Renderer.render 缺失将破坏声明式渲染"
    # Component 必须保留 before_update/did_mount 生命周期钩子
    assert hasattr(Component, "before_update")
    assert hasattr(Component, "did_mount")


def test_control_page_fget_accessible() -> None:
    """``ft.Control.page`` 仍为 property 且 ``fget`` 可访问。

    conftest 的 V1 page 兼容桩通过 ``ft.Control.page.fget`` 保存原始 getter
    并 monkeypatch 为可读写 property；若 fget 不可访问将破坏全部 UI 测试。
    """
    page_attr = getattr(ft.Control, "page", None)
    assert isinstance(page_attr, property), "ft.Control.page 不再是 property"
    fget = page_attr.fget
    assert callable(fget), "ft.Control.page.fget 不可调用"

    # fget 签名应接受 self（单参数）。类型注解可能是字符串前向引用，
    # 不断言精确返回类型字符串，仅断言参数结构。
    sig = inspect.signature(fget)
    params = list(sig.parameters.values())
    assert len(params) == 1, f"Control.page.fget 参数数变化: {params}"
    assert params[0].name == "self"


def test_pubsub_client_class_and_init_signature() -> None:
    """``flet.pubsub.pubsub_client.PubSubClient`` 类与 ``__init__`` 签名兼容。

     R.5.1 调研依赖 ``unsubscribe_topic`` 的 session-scoped 语义，必须确认
    该方法在 0.86.0 仍存在。``__init__`` 接收 ``pubsub`` 与 ``session_id``
     两个参数（项目 spike 脚本按此签名构造）。
    """
    from flet.pubsub.pubsub_client import PubSubClient

    assert inspect.isclass(PubSubClient)

    sig = inspect.signature(PubSubClient.__init__)
    params = list(sig.parameters.values())
    # self + pubsub + session_id
    param_names = [p.name for p in params]
    assert "self" in param_names
    assert "pubsub" in param_names, f"PubSubClient.__init__ 参数变化: {param_names}"
    assert "session_id" in param_names, f"PubSubClient.__init__ 参数变化: {param_names}"

    # R.5.1 关注的 unsubscribe_topic 必须存在
    assert hasattr(PubSubClient, "unsubscribe_topic"), "PubSubClient.unsubscribe_topic 缺失，R.5.1 调研前提失效"
    # 项目使用的其他 PubSub 方法
    for method in ("subscribe_topic", "send_all", "send_others", "send_all_on_topic"):
        assert hasattr(PubSubClient, method), f"PubSubClient.{method} 缺失"


def test_observable_and_observable_list_are_classes() -> None:
    """``flet.components.observable.Observable`` 与 ``ObservableList`` 仍为类。

    spike_observable.py 通过 ``Observable.subscribe/notify`` 与
    ``ObservableList`` 的 list 接口实现可观察集合；i18n/theme 状态驱动也
    依赖 Observable 基类。
    """
    from flet.components.observable import Observable, ObservableList

    assert inspect.isclass(Observable)
    assert inspect.isclass(ObservableList)

    # Observable 必须保留 subscribe/notify（观察者模式入口）
    assert hasattr(Observable, "subscribe")
    assert hasattr(Observable, "notify")

    # ObservableList 必须保留核心 list 接口（项目状态集合操作依赖）
    for method in ("append", "remove", "insert", "pop", "clear", "extend"):
        assert hasattr(ObservableList, method), f"ObservableList.{method} 缺失"
