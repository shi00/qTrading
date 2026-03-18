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
    Supports: datetime.date, datetime.datetime, str (YYYYMMDD or YYYY-MM-DD).
    Prevents TypeError when subtracting from get_now() (which is timezone-aware).
    """
    if isinstance(date_input, datetime.datetime):
        if date_input.tzinfo is None:
            return CST_TZ.localize(date_input)
        return date_input.astimezone(CST_TZ)
    if isinstance(date_input, datetime.date):
        return CST_TZ.localize(datetime.datetime.combine(date_input, datetime.time()))
    date_str = str(date_input)
    if len(date_str) == 10 and '-' in date_str:
        fmt = "%Y-%m-%d"
    return CST_TZ.localize(datetime.datetime.strptime(date_str, fmt))


def get_today_str() -> str:
    """Get the current date as a YYYYMMDD string in CST."""
    return get_now().strftime("%Y%m%d")
