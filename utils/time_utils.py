import datetime
import pytz

# Constants for China Standard Time (UTC+8)
CST_TZ = pytz.timezone('Asia/Shanghai')

def get_now() -> datetime.datetime:
    """
    Get the current time in CST (China Standard Time) / UTC+8 timezone as an aware datetime object.
    Prevents time drift issues when deploying to non-East-8 timezone servers.
    """
    return datetime.datetime.now(CST_TZ)

def parse_date(date_str: str, fmt: str = '%Y%m%d') -> datetime.datetime:
    """
    Parse a date string and make it CST-aware.
    Prevents TypeError when subtracting from get_now() (which is timezone-aware).
    """
    return CST_TZ.localize(datetime.datetime.strptime(date_str, fmt))

def get_today_str() -> str:
    """Get the current date as a YYYYMMDD string in CST."""
    return get_now().strftime('%Y%m%d')
