import asyncio
import logging
import os
import sys
import unittest

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.tushare_client import TushareClient
from utils.config_handler import ConfigHandler

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


class TestTushareLimits(unittest.TestCase):
    def test_fina_indicator_batch_permission(self):
        """
        Verify if the current token supports `fina_indicator` batch query (without ts_code).
        This is critical for the new "Batch Sync" strategy.
        """
        token = ConfigHandler.get_token()
        if not token:
            print("❌ No token found in settings. Please configure token first.")
            return

        print(f"Testing with Token: {token[:4]}***{token[-4:]}")

        client = TushareClient()

        # Try to fetch global data for a specific period with limit=1
        # This tests if we can query by period WITHOUT ts_code
        period = "20231231"
        print(f"Sending request: fina_indicator(period='{period}', limit=5)")

        try:
            # Note: We are using the raw pro API here to test pure permission
            # TushareClient.get_fina_indicator might not support limits yet (we are about to add it)
            # So we access .pro directly if possible, or use _handle_api_call

            # Using _handle_api_call to use the client's retry/error handling logic
            df = asyncio.run(
                client._handle_api_call(
                    client.pro.fina_indicator,
                    period=period,
                    limit=5,
                    fields="ts_code,end_date,roe",
                ),
            )

            if df is not None and not df.empty:
                print("API Success! Returned data:")
                print(df)
                print(f"Count: {len(df)}")
                print("CONCLUSION: Batch sync is SUPPORTED for this account.")
            elif df is not None and df.empty:
                print("API Success but returned EMPTY data.")
                print(
                    "This might mean the period is invalid or no data exists, but Permission is likely OK.",
                )
            else:
                print("API returned None (Unknown error).")

        except Exception as e:
            print(f"API Failed with error: {e}")
            print(
                "CONCLUSION: Batch sync MIGHT BE RESTRICTED. Fallback to Incremental/Looping mode needed.",
            )
            # Fail the test to alert CI/User
            self.fail(f"API Permission Check Failed: {e}")


if __name__ == "__main__":
    unittest.main()
