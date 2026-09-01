"""按配置域拆分的 ``ConfigHandler`` 领域模块包 (review05-E11)。

``utils/config/`` 承载原先堆叠在 ``utils/config_handler.py`` 中 ``ConfigHandler``
的按域逻辑：
- storage: 配置持久化基础设施（加载/保存/原子写/RWLock/缓存/深合并/迁移）
- secrets: token / db_password / provider credentials + keyring + AES 降级
- db: db_url / pool / embedded 模式 / _db_url_override
- llm: llm / failover / local_ai / prompt / 策略预设
- sync: sync / tushare / rate limits
- app_prefs: theme / locale / onboarding / log / scheduler 时辰 / 轮询

``utils/config_handler.py`` 仅保留薄 facade（``ConfigHandler``）向后兼容转发。
为保持现有单测对 ``utils.config_handler`` 命名空间的 mock 有效，领域模块一律经
``cfg = utils.config_handler`` 访问共享状态与跨域方法。
"""
