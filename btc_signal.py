#!/usr/bin/env python3
import requests
import pandas as pd
import time
import subprocess

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


# ==========================================
# SETTINGS
# ==========================================

SYMBOL = "BTCUSDT"
INTERVAL = "1h"

BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"

NY_TZ = ZoneInfo("America/New_York")

LOOKBACK_DAYS = 260


# ==========================================
# DOWNLOAD BINANCE DATA
# ==========================================

def get_hourly_data():

    now_utc = datetime.now(timezone.utc)
    start_utc = now_utc - timedelta(days=LOOKBACK_DAYS)

    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(now_utc.timestamp() * 1000)

    all_rows = []

    while start_ms < end_ms:

        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000
        }

        response = requests.get(
            BINANCE_URL,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        rows = response.json()

        if not rows:
            break

        all_rows.extend(rows)

        last_open_time = rows[-1][0]
        start_ms = last_open_time + 60 * 60 * 1000

        time.sleep(0.1)

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
        "ignore"
    ]

    df = pd.DataFrame(all_rows, columns=columns)

    df = df.drop_duplicates(subset="open_time")

    df["timestamp"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )

    df["timestamp_ny"] = (
        df["timestamp"]
        .dt.tz_convert("America/New_York")
    )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])

    df = df.set_index("timestamp_ny")

    return df


# ==========================================
# CREATE NEW YORK DAILY DATA
# ==========================================

def create_daily_data(hourly):

    daily = hourly.resample("1D").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    })

    counts = hourly["close"].resample("1D").count()
    daily["hour_count"] = counts

    # DST 때문에 정상 하루는 23 / 24 / 25시간 가능
    daily = daily[daily["hour_count"] >= 23]

    # 현재 진행 중인 오늘 NY 날짜 제거
    today_ny = datetime.now(NY_TZ).date()

    daily = daily[daily.index.date < today_ny]

    daily = daily.dropna()

    return daily


# ==========================================
# REGIME CALCULATION
# ==========================================

def calculate_regime(daily):

    daily = daily.copy()

    daily["SMA50"] = daily["close"].rolling(50).mean()
    daily["SMA200"] = daily["close"].rolling(200).mean()
    daily["SMA200_20"] = daily["SMA200"].shift(20)

    latest = daily.iloc[-1]

    if pd.isna(latest["SMA200_20"]):
        raise ValueError("SMA calculation needs more data.")

    if (
        latest["close"] > latest["SMA200"]
        and latest["SMA50"] > latest["SMA200"]
        and latest["SMA200"] > latest["SMA200_20"]
    ):
        regime = "STRONG_BULL"
        action = "BUY"
        leverage = 1.5

    elif (
        latest["close"] < latest["SMA200"]
        and latest["SMA50"] < latest["SMA200"]
        and latest["SMA200"] < latest["SMA200_20"]
    ):
        regime = "BEAR"
        action = "DO NOT BUY"
        leverage = 0.0

    else:
        regime = "NEUTRAL"
        action = "BUY"
        leverage = 1.0

    return latest, regime, action, leverage


# ==========================================
# MAIN
# ==========================================

def main():

    hourly = get_hourly_data()

    daily = create_daily_data(hourly)

    latest, regime, action, leverage = calculate_regime(daily)

    latest_date = latest.name.date()
    tomorrow = latest_date + timedelta(days=1)

    print()
    print("=" * 55)
    print("BTC REGIME SIGNAL")
    print("=" * 55)
    print()

    print("Latest completed NY day:", latest_date)
    print("Decision for:", tomorrow)

    print()

    print(f"BTC Close:    ${latest['close']:,.2f}")
    print(f"SMA50:        ${latest['SMA50']:,.2f}")
    print(f"SMA200:       ${latest['SMA200']:,.2f}")
    print(f"SMA200 20d:   ${latest['SMA200_20']:,.2f}")

    print()
    # ANSI colors are supported in terminals and most notebook outputs.
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print("SIGNAL KEY:")
    print(f"{GREEN}{BOLD}🟢 STRONG BULL → 1.5× BUY{RESET}")
    print(f"{YELLOW}{BOLD}🟡 NEUTRAL → 1.0× BUY{RESET}")
    print(f"{RED}{BOLD}🔴 BEAR → CASH{RESET}")
    print()
    print("=" * 55)

    if regime == "STRONG_BULL":
        regime_color = GREEN
        regime_label = "🟢 STRONG BULL"
    elif regime == "NEUTRAL":
        regime_color = YELLOW
        regime_label = "🟡 NEUTRAL"
    else:
        regime_color = RED
        regime_label = "🔴 BEAR"

    print(f"REGIME: {regime_color}{BOLD}{regime_label}{RESET}")
    print()
    print("=" * 55)

    if action == "BUY":
        final_signal = f"BUY BTC | {leverage:.1f}x exposure"
        print(f"TOMORROW: {regime_color}{BOLD}{final_signal}{RESET}")

    else:
        final_signal = "DO NOT BUY BTC | CASH"
        print(f"TOMORROW: {regime_color}{BOLD}{final_signal}{RESET}")

    print("=" * 55)

    message = f"{regime_label} | {final_signal}"

    try:
        subprocess.run([
            "/usr/bin/osascript",
            "-e",
            f'display notification "{message}" with title "BTC Tomorrow Signal"'
        ])
    except Exception:
        pass

    return daily


if __name__ == "__main__":
    daily = main()
