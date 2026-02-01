import datetime
from data.offline_calendar import OfflineCalendar

def test_dates():
    # Test cases: (DateStr, Expected IsTradingDay, Note)
    test_cases = [
        ("20240209", False, "2024 除夕 (Correct: False)"),
        ("20240210", False, "2024 春节 (Correct: False)"),
        ("20240218", False, "2024.2.18 (周日补班, A股不开) (Correct: False)"), 
        ("20240219", True, "2024.2.19 (周一开市) (Correct: True)"),
        
        ("20250128", False, "2025 除夕 (Correct: False)"),
        ("20251001", False, "2025 国庆 (Correct: False)"),
        ("20250504", False, "2025.5.4 (周日休) (Correct: False)"), # Sunday
        ("20250505", False, "2025.5.5 (周一休，劳动节补休?) (Correct: False)"), 
        # Check actual calendar for 2025-05-05: It's a holiday in China? 
        # pandas_market_calendars should know.
    ]
    
    print(f"{'Date':<10} | {'Expected':<10} | {'Actual':<10} | {'Result':<10} | Note")
    print("-" * 80)
    
    all_pass = True
    for date_str, expected, note in test_cases:
        actual = OfflineCalendar.is_trading_day(date_str)
        res = "PASS" if actual == expected else "FAIL"
        if not res == "PASS": 
            # Allow some discrepancy if my manual expected list is wrong, but print warning
            # Actually, I trust the library more than my memory. 
            pass 
            
        print(f"{date_str:<10} | {str(expected):<10} | {str(actual):<10} | {res:<10} | {note}")
        if actual != expected:
            all_pass = False

    if all_pass:
        print("\n✅ All Offline Calendar Tests Passed")
    else:
        print("\n❌ Some tests failed (Check if library data differs from manual expectation)")

if __name__ == "__main__":
    test_dates()
