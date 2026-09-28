"""A 股涨跌停幅度与涨跌停状态判定（业务语义唯一正本）。

本模块为横切叶子模块（``utils/``）：可被任意层引用，但自身不导入任何业务层。
集中承载两项业务语义，避免各处重复实现导致判定口径漂移：

- ``get_limit_pct``：按板块/风险警示制度推导涨跌停幅度（仅在交易所公布的逐日
  涨跌停价 ``stk_limit`` 不可用时作为降级近似）。
- ``classify_limit_status``：以交易所公布的涨跌停价判定「涨停 / 跌停」，是**优先**
  于幅度近似的事实判定入口（避免用百分比容差误标未封板股票）。

现行制度依据（截至 2026-09，以交易所现行规则为准）：

- 主板（``.SH`` / ``.SZ``，含原中小板）：一般股 ±10%；风险警示股（ST/*ST）±5%。
- 创业板（``300`` / ``301``）与科创板（``688`` / ``689``）：注册制下 ±20%；
  板块内的风险警示股涨跌幅与板块一致（同为 ±20%），**不**适用主板 5% 规则。
- 北交所（``.BJ``，含 ``8`` / ``4`` / ``920`` 开头）：±30%，ST 同为 ±30%。

**无法判定时返回 ``None``**（R21：不猜），由调用方显式降级或标注缺失。
"""

from __future__ import annotations

import math

# 价格判定容差（元）：按 A 股最小计价单位「分」计价（0.005 元 ≈ 半分）。
# 不得改为百分点口径（如原实现的 0.5 个百分点）——那会把「涨幅接近但未封板」
# 的股票误标为涨停，是本次修复的核心缺陷。
_PRICE_TOLERANCE = 0.005


def get_limit_pct(ts_code: str, name: str = "") -> float | None:
    """返回降级路径用的涨跌停幅度（百分比），无法判定时返回 ``None``。

    判定顺序（**板块分支优先于 ST 分支**，这是与原实现的关键差异）：

    1. 交易所后缀 ``.BJ`` → ``30.0``（北交所，含 ST，覆盖 ``8`` / ``4`` / ``920`` 开头）；
    2. 代码前缀 ``688`` / ``689`` → ``20.0``（科创板，ST 同为 20%）；
    3. 代码前缀 ``300`` / ``301`` → ``20.0``（创业板，ST 同为 20%）；
    4. 后缀 ``.SH`` / ``.SZ``（主板，含原中小板）→ 风险警示股 ``5.0``，否则 ``10.0``；
    5. 其余（无交易所后缀 / 未知板块）→ ``None``。

    Args:
        ts_code: 带交易所后缀的股票代码，如 ``600000.SH`` / ``300001.SZ`` / ``830001.BJ``。
        name: 股票名称（用于主板风险警示 ST/*ST 判定），可为空。

    Returns:
        涨跌停幅度（如 ``20.0`` 表示 ±20%），无法判定时为 ``None``。
    """
    code = (ts_code or "").strip().upper()
    digits = code.split(".", 1)[0]
    suffix = code.rsplit(".", 1)[-1] if "." in code else ""

    # 1. 北交所：按交易所后缀判定，覆盖 8/4/920 开头（ST 同为 30%）
    if suffix == "BJ":
        return 30.0
    # 2. 科创板（注册制，ST 同为 20%）
    if digits.startswith(("688", "689")):
        return 20.0
    # 3. 创业板（注册制，ST 同为 20%）
    if digits.startswith(("300", "301")):
        return 20.0
    # 4. 主板（含原中小板）：风险警示股 ±5%，一般股 ±10%
    if suffix in ("SH", "SZ"):
        return 5.0 if "ST" in (name or "").upper() else 10.0
    # 5. 无法判定：不猜（R21）
    return None


def classify_limit_status(close, up_limit, down_limit) -> str | None:
    """以交易所公布的涨跌停价判定涨跌停状态。

    Args:
        close: 当日收盘价。
        up_limit: 交易所公布的当日涨停价。
        down_limit: 交易所公布的当日跌停价。

    Returns:
        ``"up"``（涨停）、``"down"``（跌停），未触及涨跌停或无法判定时为 ``None``。

    容差说明：按最小计价单位「分」计价（``0.005`` 元），用于吸收浮点误差与价格
    四舍五入。**不得放宽为百分点口径**（原实现 ``day_limit - 0.5`` 会把主板涨
    9.6% 未封板的股票误标为涨停）。

    ``close <= 0`` 或 ``up_limit/down_limit <= 0``（停牌占位价 ``0``、异常负价 / 占位涨跌停价
    等）一律返回 ``None``：``close <= 0`` 会满足 ``close <= down_limit + 容差`` 被误判为跌停；
    ``up_limit/down_limit <= 0`` 会让任意正 ``close`` 满足 ``close >= up_limit - 容差`` 被误判
    为涨停。两者同属「无有效价格」而非「涨跌停」。
    """
    if close is None or up_limit is None or down_limit is None:
        return None
    try:
        c = float(close)
        u = float(up_limit)
        d = float(down_limit)
    except (TypeError, ValueError):
        return None
    if any(math.isnan(x) or math.isinf(x) for x in (c, u, d)):
        return None
    # 无效价守卫：收盘价与涨跌停价均须 > 0（停牌占位价 0 / 异常负价，含 up_limit=down_limit=0）
    if c <= 0 or u <= 0 or d <= 0:
        return None
    if c >= u - _PRICE_TOLERANCE:
        return "up"
    if c <= d + _PRICE_TOLERANCE:
        return "down"
    return None
