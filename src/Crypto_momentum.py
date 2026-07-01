import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime

# 1. Define Tokens and Configuration
TOKENS = [
    "AAVE", "AIXBT", "AVAX", "BCH", "BNB", "BTC", "COMP", "DOGE", "DOT", "DYDX",
    "EIGEN", "ENA", "ETH", "ETHFI", "FORM", "INJ", "JUP", "LTC", "NEAR", "PNUT",
    "RAY", "SOL", "SUI", "TRX", "UNI", "WIF", "XAU", "XAG", "BZ", "NVDA", "AMZN", "GOOG"
]

START_DATE = "2020-01-01"  # Extra padding for the 181-day lookback window
END_DATE = datetime.now().strftime("%Y-%m-%d")
FEE_RATE = 0.0005  # 0.05% per transaction

def fetch_binance_daily_closes(symbol, start_date, end_date):
    """Fetches full historical daily close prices from Binance Public API."""
    url = "https://api.binance.com/api/v3/klines"
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
    
    ticker = f"{symbol}USDT"
    data = []
    
    while start_ts < end_ts:
        params = {'symbol': ticker, 'interval': '1d', 'startTime': start_ts, 'limit': 1000}
        try:
            response = requests.get(url, params=params)
            if response.status_code == 400: # Token likely not on Binance
                return None
            elif response.status_code != 200:
                break
                
            klines = response.json()
            if not klines:
                break
                
            data.extend(klines)
            start_ts = klines[-1][0] + 86400000  # Jump to next day
            if len(klines) < 1000:
                break
            time.sleep(0.1)  # Rate limiting safety margin
        except Exception:
            return None

    if not data:
        return None

    df = pd.DataFrame(data, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'tb_base', 'tb_quote', 'ignore'
    ])
    df['date'] = pd.to_datetime(df['open_time'], unit='ms')
    df['close'] = df['close'].astype(float)
    return df.set_index('date')['close']

# 2. Retrieve Data for Available Assets
print("Fetching historical data from Binance...")
price_series = {}
for token in TOKENS:
    series = fetch_binance_daily_closes(token, START_DATE, END_DATE)
    if series is not None and len(series) > 181:
        price_series[token] = series
        print(f"  Successfully loaded: {token}")
    else:
        print(f"  Skipped (Not found/Insufficient data): {token}")

# Combine into a unified Dataframe
df_prices = pd.DataFrame(price_series).sort_index()
df_prices = df_prices.ffill()

# 3. Build Strategy Engine
print("\nRunning Backtest Engine with Sigmoid Weights...")
# Calculate daily asset returns
df_returns = df_prices.pct_change()

# Calculate specific momentum indicator: (Price(t-7) - Price(t-90)) / Price(t-90)
df_momo = (df_prices.shift(7) - df_prices.shift(30)) / df_prices.shift(30)

# Apply zero-centered sigmoid (hyperbolic tangent) to get raw weights between -1 and 1
df_raw_weights = np.tanh(df_momo)

# Ensure we don't allocate capital to assets with missing price data on a given day
df_raw_weights = df_raw_weights.where(df_prices.notna(), 0).fillna(0)

# Normalize daily weights so the total gross exposure (sum of absolute weights) equals 1.0 (100%)
total_abs_weight = df_raw_weights.abs().sum(axis=1)
# Prevent division by zero on days with zero signals
df_weights = df_raw_weights.div(total_abs_weight.replace(0, np.nan), axis=0).fillna(0)

# Shift weights by 1 day to ensure actionable execution without lookahead bias
df_weights_shifted = df_weights.shift(1).fillna(0)

# Calculate gross strategy portfolio returns
portfolio_gross_returns = (df_weights_shifted * df_returns).sum(axis=1)

# Compute daily turnover and apply 0.05% transaction fees
weight_changes = df_weights_shifted.diff().abs().sum(axis=1)
# Explicitly account for day 1 portfolio generation fees
weight_changes.iloc[0] = df_weights_shifted.iloc[0].abs().sum() 
transaction_fees = weight_changes * FEE_RATE

# Net portfolio returns
portfolio_net_returns = portfolio_gross_returns - transaction_fees

# Slicing the last 5 years for strict performance evaluation
five_years_ago = pd.Timestamp(END_DATE) - pd.DateOffset(years=5)
eval_returns = portfolio_net_returns.loc[five_years_ago:]

# 4. Performance Metrics Computations
cum_returns = (1 + eval_returns).cumprod()
total_return = cum_returns.iloc[-1] - 1

total_days = len(eval_returns)
cagr = (cum_returns.iloc[-1]) ** (365.25 / total_days) - 1 if cum_returns.iloc[-1] > 0 else -1

running_max = cum_returns.cummax()
drawdowns = (cum_returns - running_max) / running_max
max_dd = drawdowns.min()

# Sortino Ratio
annualized_mean_return = eval_returns.mean() * 365
downside_returns = eval_returns[eval_returns < 0]
if len(downside_returns) > 0:
    annualized_downside_std = downside_returns.std() * np.sqrt(365)
    sortino_ratio = annualized_mean_return / annualized_downside_std if annualized_downside_std != 0 else 0
else:
    sortino_ratio = np.nan

# 5. Output Performance Dashboard
print("\n" + "="*35)
print(f" SIGMOID BACKTEST RESULTS (Past {total_days/365.25:.1f} Years) ")
print("="*35)
print(f"Total Return:     {total_return * 100:.2f}%")
print(f"CAGR:             {cagr * 100:.2f}%")
print(f"Max Drawdown:     {max_dd * 100:.2f}%")
print(f"Sortino Ratio:    {sortino_ratio:.2f}")
print("="*35)