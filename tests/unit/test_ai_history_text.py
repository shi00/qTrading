import numpy as np
import pandas as pd

from strategies.ai_mixin import AIStrategyMixin


def test_ai_macro():
    size = 1250
    dates = [f"2020{(i % 12) + 1:02}{(i % 28) + 1:02}" for i in range(size)]
    close_prices = np.linspace(10, 50, size) + np.random.normal(0, 1, size)

    data = {
        "trade_date": dates,
        "close": close_prices.tolist(),
        "open": close_prices.tolist(),
        "high": (close_prices + 1).tolist(),
        "low": (close_prices - 1).tolist(),
        "vol": np.random.randint(1000, 10000, size=size).tolist(),
        "pct_chg": np.random.normal(0, 2, size=size).tolist(),
    }

    for k, v in data.items():
        assert len(v) == size, f"Mismatch: {k} length is {len(v)}, expected {size}"

    df = pd.DataFrame(data)

    result = AIStrategyMixin._build_history_text(df)
    assert isinstance(result, str)
    assert len(result) > 0
    print("AI STRATEGY PROMPT TEXT:")
    print("-" * 50)
    print(result)
    print("-" * 50)


if __name__ == "__main__":
    test_ai_macro()
