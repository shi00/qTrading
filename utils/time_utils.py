import datetime

import pytz

# Constants for China Standard Time (UTC+8)
CST_TZ = pytz.timezone("Asia/Shanghai")


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
            return CST_TZ.localize(date_input)
        return date_input.astimezone(CST_TZ)
    if isinstance(date_input, datetime.date):
        return CST_TZ.localize(datetime.datetime.combine(date_input, datetime.time()))
    date_str = str(date_input)
    if len(date_str) == 19 and "-" in date_str:
        fmt = "%Y-%m-%d %H:%M:%S"
    elif len(date_str) == 10 and "-" in date_str:
        fmt = "%Y-%m-%d"
    return CST_TZ.localize(datetime.datetime.strptime(date_str, fmt))


def get_today_str() -> str:
    """Get the current date as a YYYYMMDD string in CST."""
    return get_now().strftime("%Y%m%d")


def to_utc_for_db(dt: datetime.datetime | None) -> datetime.datetime | None:
    """
    S1-6 fix: Convert datetime to UTC for database storage.
    Removes timezone info after converting to UTC for DB compatibility.
    Use this when writing datetime to database.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = CST_TZ.localize(dt)
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
