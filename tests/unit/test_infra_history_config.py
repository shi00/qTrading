import asyncio
import datetime

import pytest

from data.data_processor import DataProcessor
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.time_utils import get_now

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_config_and_calendar():
    print(f"Initial History Years: {ConfigHandler.get_init_history_years()}")

    # Test setting
    ConfigHandler.set_init_history_years(5)
    print(f"Updated History Years: {ConfigHandler.get_init_history_years()}")

    dp = DataProcessor()
    end_date = get_now().strftime("%Y%m%d")
    # Test slicing
    years = ConfigHandler.get_init_history_years()
    rough_start = (get_now() - datetime.timedelta(days=int(250 * years * 1.5))).strftime("%Y%m%d")
    all_dates = await dp.trade_calendar.get_trade_dates(start_date=rough_start, end_date=end_date)

    if all_dates:
        start_date = all_dates[-(250 * years)] if len(all_dates) >= (250 * years) else all_dates[0]
        print(
            f"Start date calculated for {years} years: {start_date}. Available dates: {len(all_dates)}",
        )
    else:
        print("No trade dates found. Calendar sync might be needed.")

    print(f"I18n resolution: {I18n.get('wizard_sync_full').format(years=years)}")


if __name__ == "__main__":
    asyncio.run(test_config_and_calendar())
