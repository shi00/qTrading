"""UI 层 PubSub 主题常量。

发送方与接收方共享同一常量, 避免字符串硬编码导致的静默通信失败。
"""

CACHE_CLEARED_TOPIC = "cache_cleared"
"""缓存清除事件主题。data_source_tab 发送, home_view/data_view 订阅。"""

TOPIC_NAVIGATE = "navigate"
"""导航事件主题。home_view 发送 (ErrorState CTA), app_layout 订阅切换 NavigationRail selected_index。"""
