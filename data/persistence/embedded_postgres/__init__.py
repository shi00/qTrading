"""Embedded PostgreSQL sidecar 适配层（Phase 2）。

包结构：
- protocol.py：sidecar ready JSON 解析后的 ConnectionInfo 数据类
- service.py：EmbeddedPostgresService 单例（Popen 进程管理 + URL 注入）
"""
