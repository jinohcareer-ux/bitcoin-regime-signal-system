# BTC Regime Signal

An end-to-end Bitcoin research and automation project built with Binance BTCUSDT hourly data.

The project began as a search for persistent time-of-day return patterns. It then tested leverage, trading fees, stop-loss rules, and liquidation constraints. A weak 2026 result from the best historical hour pair motivated a regime model that changes exposure according to long-term trend conditions.

## Project goals

- Test every New York buy-hour and sell-hour combination.
- Include transaction costs and leverage constraints.
- Separate historical optimization from a recent-period check.
- Build a simple market-regime allocation model.
- Generate a daily signal automatically on macOS.

## Research workflow

### 1. Initial trading hypothesis

The original hypothesis was a 2.0× strategy that bought at 8:00 PM ET, sold at 11:00 AM ET the next day, and applied a 3.5% BTC stop, approximately a 7% position loss before fees.

On the August 17, 2017 through January 4, 2026 sample, the preliminary test produced approximately:

| Metric | Result |
|---|---:|
| Initial value | $1,000.00 |
| Final value before fees and funding | $2.91 |
| Trades | 3,035 |
| Win rate | 47.71% |
| Stop events | 470 |
| MDD | -99.89% |

Adding a 0.05% fee on each side reduced the ending value to approximately $0.0066. This rejected the original rule and motivated a systematic search rather than choosing another hour pair by intuition.

### 2. Exhaustive time-of-day search

The first stage evaluated all `24 × 24 = 576` combinations of entry hour and exit hour using Binance BTCUSDT 1-hour candles converted to `America/New_York` time.

The wider grid evaluated 17,280 configurations:

- 576 entry/exit hour pairs
- Leverage: `1.0×`, `1.5×`, and `2.0×`
- Stop settings: no stop and BTC stop levels from 2% through 10%

The early search, before full trading-cost and liquidation adjustments, favored a 1:00 AM entry and 7:00 PM exit. A $1,000 account grew to approximately $379,114 in that preliminary test, with a 0.242% average trade, 52.78% win rate, roughly 103% CAGR, -52.4% MDD, and about 3,045 trades. The unusually strong result was a discovery result and was therefore subjected to more realistic checks.

### 3. Fees and liquidation checks

The corrected comparison used a fee of 0.05% per side. A simple leverage liquidation boundary was also checked:

- `2.0×`: liquidation boundary near a 50% BTC decline
- `1.5×`: liquidation boundary near a 66.67% BTC decline

The leading historical configuration was:

| Configuration | Final value from $1,000 | MDD | Liquidation |
|---|---:|---:|---:|
| 1.5×, 1 AM → 7 PM ET, no stop | $26,092.86 | -85.64% | None in the tested sample |
| 2.0×, 1 AM → 7 PM ET, no stop | $18,051.18 | -94.51% | None in the tested sample |

The worst single BTC trade for the selected 1.5× configuration was approximately -30.79%. Avoiding a historical liquidation does not make the strategy safe: its drawdown was still extreme.

### 4. Recent-period check

The selected fixed-hour strategy was then tested from January 1 through August 31, 2026 with $1,000, 1.5× exposure, full compounding, no stop, and 0.05% fees on both entry and exit.

| Metric | 2026 result |
|---|---:|
| Final value | $626.18 |
| Total return | -37.38% |
| Trades | 243 |
| Win rate | 46.91% |
| Maximum drawdown | -60.32% |
| Liquidations | 0 |

Monthly returns were -17.04%, -17.93%, -13.47%, +16.71%, -6.08%, -31.51%, +13.09%, and +25.19% from January through August. The no-fee result was $902.02, showing that repeated trading costs materially reduced performance. This recent-period failure was the main reason to move from one fixed intraday rule to a slower trend-regime model.

## Regime strategy

The model uses only the latest completed New York trading day.

| Regime | Conditions | Target exposure |
|---|---|---:|
| **Strong Bull** | Close > SMA200, SMA50 > SMA200, and SMA200 > SMA200 from 20 days earlier | 1.5× BTC |
| **Bear** | Close < SMA200, SMA50 < SMA200, and SMA200 < SMA200 from 20 days earlier | Cash |
| **Neutral** | All other valid combinations | 1.0× BTC |

The first period without a valid 200-day moving average and 20-day slope comparison is excluded from trading.

### Why these variables?

- `Close vs. SMA200` identifies whether price is above or below its long-term trend.
- `SMA50 vs. SMA200` distinguishes stronger upward or downward trend structure.
- `SMA200 vs. SMA200_20` checks whether the long-term trend itself is rising or falling.
- The three-state output reduces exposure during clearly bearish periods without requiring a short position.

## Reference backtest results

Historical results discussed during project development covered August 17, 2017 through August 31, 2026.

| Strategy | Final value from $1,000 | Total return | CAGR | MDD |
|---|---:|---:|---:|---:|
| Regime: 1.5× / 1.0× / Cash | $19,831.81 | +1,883% | 39.2% | -85.8% |
| Strong-Bull-only, 1 AM → 7 PM, 1.5× | $4,582.88 | +358% | 18.3% | -76.4% |
| BTC buy and hold | $18,221.79 | +1,722% | 37.9% | -84.0% |

For January through August 2026, the regime model produced a reference result of $1,129.84 from $1,000, or +12.98%, with a -4.76% MDD. Buy and hold produced $895.22, or -10.48%, with a -40.81% MDD over the same comparison window.

These values are historical research outputs, not expected returns. Results can change with the data endpoint, fee assumptions, execution rules, and implementation details.

## Backtest implementation

`btc_regime_backtest.py` performs the following steps:

1. Downloads BTCUSDT 1-hour klines from Binance Vision.
2. Converts all timestamps to New York time.
3. Builds daily OHLCV observations and allows 23, 24, or 25-hour days for daylight saving time.
4. Removes the unfinished current New York day.
5. Calculates SMA50, SMA200, and the 20-day change in SMA200.
6. Shifts the daily regime forward by one day to prevent look-ahead bias.
7. Approximates a 12:01 AM trade with the 12:00 AM hourly candle open.
8. Maps the regime to 1.5×, 1.0×, or 0× exposure.
9. Charges fees on changes in target exposure.
10. Calculates compounded equity and maximum drawdown.
11. Saves detailed trades and summary files in `outputs/`.

Set `DAILY_CLOSE_REOPEN = True` to model closing and reopening the position every day and charging both sides each day. The default `False` charges trading fees only when target exposure changes.

## Daily signal service

`btc_signal.py` downloads enough recent data for the moving averages, evaluates the latest completed New York day, and sends one of these macOS notifications:

- 🟢 `STRONG BULL | BUY BTC | 1.5x exposure`
- 🟡 `NEUTRAL | BUY BTC | 1.0x exposure`
- 🔴 `BEAR | DO NOT BUY BTC | CASH`

The script produces a decision for the next New York calendar day.

## Repository files

| File | Purpose |
|---|---|
| `btc_regime_backtest.py` | Full-history regime backtest and CSV/JSON output |
| `btc_signal.py` | Lightweight daily signal and macOS notification |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Excludes logs, caches, and generated output |
| `README_KR.md` | Korean documentation |

## Installation

```bash
python -m pip install -r requirements.txt
```

Run the research backtest:

```bash
python btc_regime_backtest.py
```

Run the daily signal manually:

```bash
python btc_signal.py
```

## macOS cron automation

Open the cron editor:

```bash
crontab -e
```

Example for a 12:10 AM daily run:

```cron
10 0 * * * /opt/anaconda3/bin/python /Users/yourname/Bitcoin/btc_signal.py >> /Users/yourname/Bitcoin/btc_signal.log 2>&1
```

The Mac must be powered on and awake at the scheduled time. The notebook does not need to remain open.

## Important limitations

- Testing thousands of configurations creates a substantial multiple-testing and overfitting risk.
- The regime thresholds were also selected after observing historical behavior; the full historical result is not a clean out-of-sample estimate.
- The simplified backtest excludes funding or borrowing costs, bid-ask spread, slippage, taxes, and execution failure.
- The regime backtest does not simulate intraday liquidation from hourly high/low paths.
- Multiplying each day's BTC return by target leverage approximates daily leverage rebalancing. If the position is truly left untouched, leverage drifts as BTC moves and requires a units-and-cash portfolio simulation.
- The 12:00 AM hourly open is an approximation for a 12:01 AM order.
- Binance availability and API behavior can change.
- Cron does not execute a missed job at the original time if the Mac is asleep.

## Disclaimer

This repository is an educational research project. It is not financial advice, and historical performance does not guarantee future results.
