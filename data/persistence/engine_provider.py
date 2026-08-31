"""引擎状态只读查询 provider（review03-C11 Step2）。

解除 ``BaseDao._check_engine`` 对 ``CacheManager._instance`` 的反向运行时查询
（data/persistence → data/cache 循环）。引擎生命周期仍由 CacheManager 独占
管理（创建/释放/标记 disposed），本模块仅暴露"引擎是否可用"的只读状态，
供 DAO/维护流程做 R5 守卫。

模块级状态 + 互斥锁：状态更新由 CacheManager 生命周期方法在同一临界区内
完成；测试隔离通过 reset_engine_provider()（由 CacheManager._reset_singleton
触发）保证跨用例无残留。
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_engine: Any = None
_disposed = False


def set_engine(engine: Any) -> None:
    """记录当前引擎引用（语义化标记；引擎注册与同步仍由 CacheManager 负责）。"""
    global _engine
    with _lock:
        _engine = engine


def mark_disposed(flag: bool) -> None:
    """设置引擎释放标记（dispose 前置 True，重新初始化后置 False，R5 顺序不变量）。"""
    global _disposed
    with _lock:
        _disposed = flag


def is_disposed(engine: Any | None = None) -> bool:
    """引擎是否已释放（供 _check_engine 等 R5 守卫查询）。

    ``_disposed`` 追踪的是 CacheManager 独占管理的**受管引擎**（``set_engine``
    登记）。按引擎身份判定：仅当查询的 engine 是受管引擎且全局标记为 disposed
    时才返回 True；独立注入的引擎（如测试引擎）即使全局标记遗留为 True 也不得
    误判。未指定 engine 时回退到全局标记（兼容无受管引擎的调用方）。
    """
    with _lock:
        if engine is not None:
            return _disposed and engine is _engine
        return _disposed


def reset_engine_provider() -> None:
    """清空 provider 状态（测试隔离用，由 CacheManager._reset_singleton 触发）。"""
    global _engine, _disposed
    with _lock:
        _engine = None
        _disposed = False
