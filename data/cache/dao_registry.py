"""DAO engine 引用同步（review01-A4 Step2 拆分；OSS-03 改为按类型发现）。

从 ``CacheManager`` 拆出 DAO 引擎同步职责。DAO 实例化仍保留在
``CacheManager.__init__``（R13 红线静态检查要求 `self.<x>_dao = <ClassName>(...)`
出现在 __init__；check_R13 守护该维度）。

OSS-03（开源组件使用检视报告 §4）：删除重复的 ``_DAO_REGISTRY`` 元组与逐行
相同的 19 条 DAO import —— engine 同步改为按类型发现（``isinstance(BaseDao)``），
新增 DAO 无需在本模块登记，结构上无法漏改。

NOTE(lazy): sync_engines 按类型发现遍历 holder 实例属性，新增非 DAO 的 BaseDao
子类实例会被一并同步；当前 CacheManager 上唯一的 BaseDao 实例即为 19 个 DAO。
ceiling: 未来 holder 若有其它 BaseDao 子类实例需主动排除。
upgrade: 出现该类实例时改为显式白名单或属性名约定。
"""

from __future__ import annotations

from data.persistence.daos.base_dao import BaseDao


class DaoRegistry:
    """DAO engine 引用同步（按类型发现，无显式注册清单）。

    ``sync_engines(holder, engine)`` 遍历 ``holder``（宿主对象，即 CacheManager
    组合根）实例属性，凡 ``BaseDao`` 实例的 ``.engine`` 置为给定引擎（或 None）。
    消除 _create_engine/close 中逐 DAO 手写赋值的重复，且新增 DAO 无需登记。
    """

    def sync_engines(self, holder: object, engine) -> None:
        """按类型发现同步 holder 上所有 DAO 实例的 ``.engine``（create/dispose 共用）。"""
        for dao in vars(holder).values():
            if isinstance(dao, BaseDao):
                dao.engine = engine
