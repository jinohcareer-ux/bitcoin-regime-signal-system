#!/usr/bin/env python3
"""Backtest the BTC 1.5x / 1.0x / cash regime allocation.

Data are Binance BTCUSDT 1-hour candles converted to New York time. The
strategy uses only a completed prior day to choose the following day's target
exposure. See README.md for assumptions and limitations.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from zoneinfo import ZoneInfo


SYMBOL = "BTCUSDT"
INTERVAL = "1h"
START_DATE = "2017-08-17"
BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
NY_TZ = ZoneInfo("America/New_York")

INITIAL_MONEY = 1_000.0
STRONG_BULL_LEVERAGE = 1.5
NEUTRAL_LEVERAGE = 1.0
BEAR_LEVERAGE = 0.0
FEE_RATE = 0.0005  # 0.05% per side

# False: charge turnover only when target exposure changes.
# True: close and reopen any non-cash position every day.
DAILY_CLOSE_REOPEN = False

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


def download_binance_klines(
    symbol: str = SYMBOL,
    interval: str = INTERVAL,
    start_date: str = START_DATE,
) -> pd.DataFrame:
    """Download Binance hourly candles from start_date through now."""
    start_dt = pd.Timestamp(start_date, tz="UTC")
    end_dt = pd.Timestamp.now(tz="UTC")
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    all_rows: list[list] = []

    while start_ms < end_ms:
        response = requests.get(
            BINANCE_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            break
        all_rows.extend(rows)
        start_ms = rows[-1][0] + 60 * 60 * 1000
        time.sleep(0.05)

    if not all_rows:
        raise RuntimeError("Binance returned no hourly data.")

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]
    hourly = pd.DataFrame(all_rows, columns=columns)
    hourly = hourly.drop_duplicates(subset="open_time")
    hourly["timestamp"] = pd.to_datetime(hourly["open_time"], unit="ms", utc=True)
    hourly["timestamp_ny"] = hourly["timestamp"].dt.tz_convert(NY_TZ)

    for column in ["open", "high", "low", "close", "volume"]:
        hourly[column] = pd.to_numeric(hourly[column])

    return hourly.set_index("timestamp_ny").sort_index()


def make_daily_data(hourly: pd.DataFrame) -> pd.DataFrame:
    """Aggregate New York daily candles and remove incomplete days."""
    daily = hourly.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    daily["hour_count"] = hourly["close"].resample("1D").count()
    daily = daily[daily["hour_count"] >= 23].dropna()
    today_ny = datetime.now(NY_TZ).date()
    return daily[daily.index.date < today_ny].copy()


def classify_regimes(daily: pd.DataFrame) -> pd.DataFrame:
    """Add moving averages, validity flag, and three-state regime."""
    daily = daily.copy()
    daily["SMA50"] = daily["close"].rolling(50).mean()
    daily["SMA200"] = daily["close"].rolling(200).mean()
    daily["SMA200_20"] = daily["SMA200"].shift(20)

    strong_bull = (
        (daily["close"] > daily["SMA200"])
        & (daily["SMA50"] > daily["SMA200"])
        & (daily["SMA200"] > daily["SMA200_20"])
    )
    bear = (
        (daily["close"] < daily["SMA200"])
        & (daily["SMA50"] < daily["SMA200"])
        & (daily["SMA200"] < daily["SMA200_20"])
    )
    daily["regime"] = np.select(
        [strong_bull, bear], ["STRONG_BULL", "BEAR"], default="NEUTRAL"
    )
    daily["valid_signal"] = daily["SMA200_20"].notna()
    return daily


def regime_to_leverage(regime: str) -> float:
    return {
        "STRONG_BULL": STRONG_BULL_LEVERAGE,
        "NEUTRAL": NEUTRAL_LEVERAGE,
        "BEAR": BEAR_LEVERAGE,
    }.get(regime, 0.0)


def get_action(row: pd.Series) -> str:
    previous = row["prev_leverage"]
    current = row["leverage"]
    if previous == 0 and current == 0:
        return "NO TRADE / CASH"
    if previous == 0 and current > 0:
        return f"BUY / OPEN {current:.1f}x"
    if previous > 0 and current == 0:
        return f"SELL / CLOSE {previous:.1f}x → CASH"
    if current > previous:
        return f"BUY MORE {previous:.1f}x → {current:.1f}x"
    if current < previous:
        return f"SELL PARTIAL {previous:.1f}x → {current:.1f}x"
    if current == previous and current > 0:
        return f"SELL & REBUY {current:.1f}x" if DAILY_CLOSE_REOPEN else f"HOLD {current:.1f}x"
    return "CHECK"


def run_backtest(hourly: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Align prior-day signals with midnight prices and calculate equity."""
    midnight = hourly[(hourly.index.hour == 0) & (hourly.index.minute == 0)].copy()
    midnight["trade_day"] = midnight.index.normalize()
    entry = pd.DataFrame({"entry_price": midnight["open"].values}, index=midnight["trade_day"])
    entry = entry[~entry.index.duplicated(keep="first")]
    entry["exit_price"] = entry["entry_price"].shift(-1)
    entry["btc_return"] = entry["exit_price"] / entry["entry_price"] - 1

    signals = daily[
        ["regime", "valid_signal", "close", "SMA50", "SMA200", "SMA200_20"]
    ].copy()
    signals.index = signals.index + pd.Timedelta(days=1)

    trades = entry.join(signals, how="left")
    trades = trades.dropna(subset=["entry_price", "exit_price", "btc_return", "regime"])
    trades = trades[trades["valid_signal"]].copy()
    if trades.empty:
        raise RuntimeError("No valid trades remained after signal alignment.")

    trades["leverage"] = trades["regime"].map(regime_to_leverage)
    trades["prev_leverage"] = trades["leverage"].shift(1).fillna(0.0)
    trades["action"] = trades.apply(get_action, axis=1)
    trades["gross_return"] = trades["btc_return"] * trades["leverage"]

    if DAILY_CLOSE_REOPEN:
        trades["fee"] = trades["leverage"] * FEE_RATE * 2
    else:
        trades["turnover"] = (trades["leverage"] - trades["prev_leverage"]).abs()
        trades["fee"] = trades["turnover"] * FEE_RATE

    trades["strategy_return"] = trades["gross_return"] - trades["fee"]
    already_cash = (trades["prev_leverage"] == 0) & (trades["leverage"] == 0)
    trades.loc[already_cash, "strategy_return"] = 0.0
    trades["equity"] = INITIAL_MONEY * (1 + trades["strategy_return"]).cumprod()
    trades["peak"] = trades["equity"].cummax()
    trades["drawdown"] = trades["equity"] / trades["peak"] - 1
    return trades


def make_summary(trades: pd.DataFrame) -> dict:
    first_date = trades.index.min()
    last_date = trades.index.max()
    years = max((last_date - first_date).days / 365.25, 1 / 365.25)
    final_value = float(trades["equity"].iloc[-1])
    total_return = final_value / INITIAL_MONEY - 1
    cagr = (final_value / INITIAL_MONEY) ** (1 / years) - 1
    return {
        "symbol": SYMBOL,
        "start_date": str(first_date.date()),
        "end_date": str(last_date.date()),
        "initial_money": INITIAL_MONEY,
        "final_value": final_value,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(trades["drawdown"].min()),
        "days": int(len(trades)),
        "fee_rate_per_side": FEE_RATE,
        "daily_close_reopen": DAILY_CLOSE_REOPEN,
    }


def print_table(title: str, frame: pd.DataFrame, rows: int) -> None:
    print(f"\n{title}")
    print(frame.tail(rows).to_string())


def main() -> None:
    hourly = download_binance_klines()
    print("Hourly data range:")
    print(hourly.index.min(), "to", hourly.index.max())

    daily = classify_regimes(make_daily_data(hourly))
    trades = run_backtest(hourly, daily)
    summary = make_summary(trades)

    print("\n" + "=" * 70)
    print("BTC REGIME BACKTEST WITH ACTION TABLE")
    print("=" * 70)
    print(json.dumps(summary, indent=2))

    columns = [
        "entry_price",
        "exit_price",
        "regime",
        "prev_leverage",
        "leverage",
        "action",
        "btc_return",
        "fee",
        "strategy_return",
        "equity",
        "drawdown",
    ]
    print_table("Last 30 calendar rows:", trades[columns], 30)

    action_changes = trades[trades["leverage"] != trades["prev_leverage"]]
    print_table("Last 50 actual action-change rows:", action_changes[columns], 50)

    strong_2026 = trades[(trades.index.year == 2026) & (trades["regime"] == "STRONG_BULL")]
    print_table("First 20 Strong Bull rows in 2026:", strong_2026[columns].head(20), 20)
    print("Number of 2026 Strong Bull days:", len(strong_2026))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUTPUT_DIR / "trades.csv")
    action_changes.to_csv(OUTPUT_DIR / "action_changes.csv")
    strong_2026.to_csv(OUTPUT_DIR / "strong_bull_2026.csv")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print("\nSaved output files to:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
