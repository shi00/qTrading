"""
Data layer - 数据层

提供数据访问、缓存、持久化和外部服务接口。

子模块:
- cache: 缓存管理
- persistence: 持久化层 (数据库、ORM模型、DAO)
- external: 外部服务 (Tushare API、新闻获取)
- domain_services: 领域服务 (交易日历、市场数据)
- sync: 数据同步策略
- mixins: Mixin 类
"""

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor

__all__ = ["CacheManager", "DataProcessor"]
