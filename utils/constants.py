"""utils 层共享常量。

本模块承载被多层（含 utils 自身）引用的纯常量定义，避免 utils 反向依赖 data 层。
依据 §4.1 分层架构：utils 为横切关注点，不得依赖 data/services/strategies/ui。
"""

# Tushare 积分档位枚举（5 档，按积分升序）
# 单一真相源：所有引用此枚举的代码必须从此常量派生，禁止散落硬编码字符串。
# 注：tushare_client.py 中的 _POINT_TIER_PRESETS / _TIER_ORDER / _TIER_API_COVERAGE
# 因 Python dict 字面量语法限制无法直接引用此常量，改用 assert 一致性断言防漂移。
TUSHARE_POINT_TIERS: tuple[str, ...] = (
    "points_120",
    "points_2000",
    "points_5000",
    "points_10000",
    "points_15000",
)
