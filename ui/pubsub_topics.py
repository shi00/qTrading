"""UI 层 PubSub 主题常量。

发送方与接收方共享同一常量, 避免字符串硬编码导致的静默通信失败。
"""

TOPIC_NAVIGATE = "navigate"
"""导航事件主题。watchlist_view/screener_view/home_view 发送, app_layout 订阅切换 NavigationRail selected_index."""
