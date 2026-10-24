import datetime
from zoneinfo import ZoneInfo

# Constants for China Standard Time (UTC+8)
CST_TZ = ZoneInfo("Asia/Shanghai")


def get_now() -> datetime.datetime:
    """
    Get the current time in CST (China Standard Time) / UTC+8 timezone as an aware datetime object.
    Prevents time drift issues when deploying to non-East-8 timezone servers.
    """
    return datetime.datetime.now(CST_TZ)


def parse_date(date_input, fmt="%Y%m%d") -> datetime.datetime:
    """
    Parse a date input and make it CST-aware.
    Supports: datetime.date, datetime.datetime, str (YYYYMMDD, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS).
    Prevents TypeError when subtracting from get_now() (which is timezone-aware).
    """
    if isinstance(date_input, datetime.datetime):
        if date_input.tzinfo is None:
            return date_input.replace(tzinfo=CST_TZ)
        return date_input.astimezone(CST_TZ)
    if isinstance(date_input, datetime.date):
        return datetime.datetime.combine(date_input, datetime.time()).replace(tzinfo=CST_TZ)
    date_str = str(date_input)
    # ISO-8601 的 "T" 分隔形式（如 2024-03-15T10:00:00、带 +08:00 偏移）无法由下方
    # 按长度选出的 strptime 格式解析：旧实现直接抛 ValueError，导致 to_yyyymmdd_str
    # 回退返回原始串（非 YYYYMMDD），to_date 再以 "Unsupported date value" 报错。
    if "T" in date_str:
        parsed = datetime.datetime.fromisoformat(date_str)
        return parsed.replace(tzinfo=CST_TZ) if parsed.tzinfo is None else parsed.astimezone(CST_TZ)
    if len(date_str) == 19 and "-" in date_str:
        fmt = "%Y-%m-%d %H:%M:%S"
    elif len(date_str) == 10 and "-" in date_str:
        fmt = "%Y-%m-%d"
    return datetime.datetime.strptime(date_str, fmt).replace(tzinfo=CST_TZ)


def get_today_str() -> str:
    """Get the current date as a YYYYMMDD string in CST."""
    return get_now().strftime("%Y%m%d")


def to_yyyymmdd_str(value) -> str | None:
    """Normalize date-like values to YYYYMMDD string."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, datetime.date):
        return value.strftime("%Y%m%d")

    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "nat", "none"}:
        return None

    try:
        return parse_date(raw).strftime("%Y%m%d")
    except (ValueError, TypeError):
        pass

    if len(raw) >= 8 and raw[:8].isdigit():
        return raw[:8]
    return raw


def to_date(value) -> datetime.date:
    """Normalize date-like values to datetime.date."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value

    yyyymmdd = to_yyyymmdd_str(value)
    if not yyyymmdd:
        raise ValueError(f"Unsupported date value: {value!r}")
    try:
        return datetime.datetime.strptime(yyyymmdd[:8], "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
    except ValueError:
        try:
            return parse_date(str(value)).date()
        except Exception as e:
            raise ValueError(f"Unsupported date value: {value!r}") from e


def to_utc_for_db(dt: datetime.datetime | None) -> datetime.datetime | None:
    """
    S1-6 fix: Convert datetime to UTC for database storage.
    Removes timezone info after converting to UTC for DB compatibility.
    Use this when writing datetime to database.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST_TZ)
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


def from_utc_to_cst(dt: datetime.datetime | None) -> datetime.datetime | None:
    """
    S1-6 fix: Convert UTC datetime from database to CST for display.
    Use this when reading datetime from database.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(CST_TZ)
