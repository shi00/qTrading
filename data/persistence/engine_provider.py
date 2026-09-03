r"""引擎状态只读查询 provider（review03-C11 Step2；DAT-02 加固）。

解除 ``BaseDao._check_engine`` 对 ``CacheManager._instance`` 的反向运行时查询
（data/persistence → data/cache 循环）。引擎生命周期仍由 CacheManager 独占
管理（创建/释放/标记 disposed），本模块仅暴露"引擎是否可用"的只读状态，
供 DAO/维护流程做 R5 守卫。

DAT-02 修复：R5 守卫真正要识别的是"**曾释放过的引擎**"，而非"当前受管引擎
的身份"。旧实现用"全局单标记（\_disposed）+ 身份比较（engine is \_engine）"
判定，在引擎被替换/清除后失效——尤其 ``dispose()`` 会把 ``_engine`` 置 None，
使 ``is_disposed(已释放的旧引擎)`` 恒为 False，从而放行在已释放引擎上的操作。
本版本改为记录**已释放引擎的集合**（``_disposed_engines``），释放过的引擎一旦
登记永久可识别；新建引擎是不同对象，天然不在集合中。独立注入的引擎（如测试
引擎）只要未被标记释放，就不被误判——原 docstring 的目标保持，且不再依赖脆弱
的身份比较。

实现说明：用普通 set 而非 ``weakref.WeakSet``——WeakSet 无法容纳 ``object()``
等不可弱引用的测试替身，且已释放引擎在生命周期内极少、由
``reset_engine_provider()`` 清空，短期强引用不构成内存压力。

模块级状态 + 互斥锁：状态更新由 CacheManager 生命周期方法在同一临界区内完成；
测试隔离通过 reset_engine_provider()（由 CacheManager._reset_singleton 触发）
保证跨用例无残留。
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_engine: Any = None
_disposed_engines: set[Any] = set()


def set_engine(engine: Any) -> None:
    """记录当前引擎引用（语义化标记；引擎注册与同步仍由 CacheManager 负责）。"""
    global _engine
    with _lock:
        _engine = engine


def mark_disposed(flag: bool) -> None:
    """标记引擎释放。

    ``flag=True`` 时将**当前受管引擎**（``set_engine`` 登记）登记为已释放，
    之后永久不可用（DAT-02：已释放引擎不得因引擎被替换而重新放行）。
    ``flag=False`` 表示"新引擎已就绪"——新引擎是不同对象，天然不在已释放
    集合中，无需额外动作。

    注意：与旧实现不同，不再存在"同一引擎对象先标记 disposed 再标记可用"
    的语义；释放不可逆，重建引擎必须创建新对象（符合 R5 不变量）。
    """
    if flag:
        with _lock:
            if _engine is not None:
                _disposed_engines.add(_engine)


def is_disposed(engine: Any | None = None) -> bool:
    """引擎是否已释放（供 _check_engine 等 R5 守卫查询）。

    - 指定 engine：仅当该引擎曾被标记释放（在 ``_disposed_engines`` 中）才
      返回 True。独立注入的引擎（如测试引擎）只要未被标记释放，就不被误判。
    - 未指定 engine：当前无受管引擎（``_engine is None``）或受管引擎已被释放
      时返回 True。
    """
    with _lock:
        if engine is not None:
            return engine in _disposed_engines
        return _engine is None or _engine in _disposed_engines


def reset_engine_provider() -> None:
    """清空 provider 状态（测试隔离用，由 CacheManager._reset_singleton 触发）。"""
    global _engine
    with _lock:
        _engine = None
        _disposed_engines.clear()
