#!/usr/bin/env python3
"""Signal engine for the multi-coin screener.

The indicator and system functions below are adapted from the author's
BTC backtest harness (~/workspace/crypto/backtest/harness.py), where the
same 12 systems were tested on Coinbase BTC-USD daily OHLCV from 2015.
They are copied here so this repository is self-contained; the logic is
unchanged. The backtest evidence is summarised in README.md.

Execution model (identical to the backtests, no lookahead):
- A signal is computed from data through the close of day t.
- It would execute at the OPEN of day t+1.
- Backtests charged 0.15% of traded notional per side (0.10% fee +
  0.05% slippage). Positions are long-only: 1 is fully invested, 0 is
  cash.

Signals always use the LAST SETTLED daily close: the current UTC day's
candle is partial until 00:00 UTC, so it is dropped before any signal
is computed (see settled_frame).

12 systems per coin, in two groups:

Trend (9): trend200, momentum_trend, macd, donchian, golden_cross,
donchian_trend, donchian_atr_stop, donchian_atr_sized, trend_atr_sized.
Mean reversion (3): rsi_reversion, bollinger, rsi2.

Fear & Greed is market-wide, not per-coin: it is reported once as
market context by screen.py and is NOT scored per coin.

History requirements: SMA200 needs 200 candles, 12-1 momentum needs
252. Systems whose inputs are not yet available return NaN and are
reported as unknown (not counted as long or out). ATR and Donchian
systems also need true highs and lows; on close-only fallback data
(see cmc_client.quotes_historical) they are reported as unknown.
"""
import math
from datetime import date

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- indicators

def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close, n=20, k=2):
    mid = sma(close, n)
    std = close.rolling(n).std(ddof=0)
    return mid + k * std, mid, mid - k * std


def percent_b(close, n=20, k=2):
    upper, mid, lower = bollinger(close, n, k)
    return (close - lower) / (upper - lower)


def atr(high, low, close, n=14):
    """Wilder ATR from true range."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def momentum_12_1(close):
    """Classic 12-1 momentum: 12-month return skipping the last month."""
    return close.shift(21) / close.shift(252) - 1


def add_indicators(df):
    """Add every indicator the 12 systems and the screener readings
    need. Input: date, open, high, low, close, volume, oldest first."""
    df = df.copy()
    c, h, l = df["close"], df["high"], df["low"]
    df["sma50"] = sma(c, 50)
    df["sma200"] = sma(c, 200)
    df["rsi14"] = rsi(c, 14)
    df["rsi2"] = rsi(c, 2)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger(c)
    df["pct_b"] = percent_b(c)
    df["atr14"] = atr(h, l, c, 14)
    df["mom_12_1"] = momentum_12_1(c)
    df["donch_hi20_prev"] = h.rolling(20).max().shift(1)
    df["donch_lo10_prev"] = l.rolling(10).min().shift(1)
    return df


# ------------------------------------------------------------------- systems

def system_trend200(df):
    """Long when close is above its 200-day average, else cash."""
    pos = (df["close"] > df["sma200"]).astype(float)
    return pos.where(df["sma200"].notna(), np.nan)


def system_rsi_reversion(df):
    """Long when RSI(14) drops under 30, exit when it rises over 70."""
    rsi14 = df["rsi14"]
    pos = pd.Series(np.nan, index=df.index, dtype=float)
    state = 0.0
    for i in range(len(df)):
        r = rsi14.iloc[i]
        if pd.isna(r):
            continue
        if r < 30:
            state = 1.0
        elif r > 70:
            state = 0.0
        pos.iloc[i] = state
    return pos


def system_momentum_trend(df):
    """Long when 12-1 momentum is positive AND close is above 200DMA."""
    pos = ((df["mom_12_1"] > 0) & (df["close"] > df["sma200"])).astype(float)
    valid = df["mom_12_1"].notna() & df["sma200"].notna()
    return pos.where(valid, np.nan)


def system_macd(df):
    """Long when the MACD line is above its signal line, else cash."""
    pos = (df["macd"] > df["macd_signal"]).astype(float)
    return pos.where(df["macd"].notna() & df["macd_signal"].notna(), np.nan)


def system_bollinger(df):
    """Long when price closes below the lower Bollinger Band, exit when
    it closes back above the middle band (%B over 0.5)."""
    pct_b = df["pct_b"]
    pos = pd.Series(np.nan, index=df.index, dtype=float)
    state = 0.0
    for i in range(len(df)):
        p = pct_b.iloc[i]
        if pd.isna(p):
            continue
        if p < 0:
            state = 1.0
        elif p > 0.5:
            state = 0.0
        pos.iloc[i] = state
    return pos


def system_donchian(df):
    """Turtle-style breakout, long only. Enter when the close breaks
    above the highest high of the prior 20 days, exit when it breaks
    below the lowest low of the prior 10 days."""
    hi = df["donch_hi20_prev"]
    lo = df["donch_lo10_prev"]
    close = df["close"]
    pos = pd.Series(np.nan, index=df.index, dtype=float)
    state = 0.0
    for i in range(len(df)):
        if pd.isna(hi.iloc[i]) or pd.isna(lo.iloc[i]):
            continue
        if close.iloc[i] > hi.iloc[i]:
            state = 1.0
        elif close.iloc[i] < lo.iloc[i]:
            state = 0.0
        pos.iloc[i] = state
    return pos


def system_rsi2(df):
    """Short-term RSI(2) mean reversion, Connors-style: long when RSI(2)
    drops under 10, exit when it rises over 90."""
    rsi2 = df["rsi2"]
    pos = pd.Series(np.nan, index=df.index, dtype=float)
    state = 0.0
    for i in range(len(df)):
        r = rsi2.iloc[i]
        if pd.isna(r):
            continue
        if r < 10:
            state = 1.0
        elif r > 90:
            state = 0.0
        pos.iloc[i] = state
    return pos


def system_golden_cross(df):
    """Long when the 50-day average is above the 200-day average."""
    pos = (df["sma50"] > df["sma200"]).astype(float)
    valid = df["sma50"].notna() & df["sma200"].notna()
    return pos.where(valid, np.nan)


def system_donchian_trend(df):
    """Donchian 20/10 breakout, but only while price is above its
    200-day average. Exit on a 10-day low break or a close under the
    200-day average, whichever comes first."""
    hi = df["donch_hi20_prev"]
    lo = df["donch_lo10_prev"]
    close = df["close"]
    sma200 = df["sma200"]
    pos = pd.Series(np.nan, index=df.index, dtype=float)
    state = 0.0
    for i in range(len(df)):
        if pd.isna(hi.iloc[i]) or pd.isna(lo.iloc[i]) or pd.isna(sma200.iloc[i]):
            continue
        if close.iloc[i] > hi.iloc[i] and close.iloc[i] > sma200.iloc[i]:
            state = 1.0
        elif close.iloc[i] < lo.iloc[i] or close.iloc[i] < sma200.iloc[i]:
            state = 0.0
        pos.iloc[i] = state
    return pos


def _atr_size(atr14, close, risk=0.02, stop_mult=2.0):
    """Turtle-style position size as a fraction of the account.

    Risk `risk` of the account per trade with a `stop_mult` x ATR stop:
    size = risk / (stop_mult * ATR / price), capped at 1.0.
    """
    frac = risk / (stop_mult * atr14 / close)
    return np.clip(frac, 0.0, 1.0)


def system_donchian_atr_stop(df):
    """Donchian 20/10 breakout, long only, but the exit is a trailing
    2xATR stop: the stop starts 2xATR below the entry price (ATR
    measured at entry) and ratchets up each day to 2xATR below the
    close. The 10-day low exit is not used."""
    hi = df["donch_hi20_prev"]
    lo = df["donch_lo10_prev"]
    close = df["close"]
    atr14 = df["atr14"]
    pos = pd.Series(np.nan, index=df.index, dtype=float)
    state = 0.0
    stop = np.nan
    for i in range(len(df)):
        if pd.isna(hi.iloc[i]) or pd.isna(lo.iloc[i]) or pd.isna(atr14.iloc[i]):
            continue
        if state == 0.0 and close.iloc[i] > hi.iloc[i]:
            state = 1.0
            stop = close.iloc[i] - 2.0 * atr14.iloc[i]
        elif state == 1.0:
            stop = max(stop, close.iloc[i] - 2.0 * atr14.iloc[i])
            if close.iloc[i] < stop:
                state = 0.0
                stop = np.nan
        pos.iloc[i] = state
    return pos


def system_donchian_atr_sized(df):
    """Donchian 20/10 breakout with ATR position sizing: when the
    breakout signal is on, invest the Turtle-style fraction from
    _atr_size (2% risk, 2xATR stop) instead of the full account."""
    base = system_donchian(df)
    size = _atr_size(df["atr14"], df["close"])
    pos = base * size
    return pos.where(base.notna(), np.nan)


def system_trend_atr_sized(df):
    """200-day trend with ATR position sizing, same 2% risk / 2xATR
    rule as system_donchian_atr_sized."""
    base = system_trend200(df)
    size = _atr_size(df["atr14"], df["close"])
    pos = base * size
    return pos.where(base.notna(), np.nan)


SYSTEMS = {
    "trend200": ("200-day trend", "trend", system_trend200),
    "momentum_trend": ("Momentum + trend filter", "trend", system_momentum_trend),
    "macd": ("MACD crossover", "trend", system_macd),
    "donchian": ("Donchian 20/10 breakout", "trend", system_donchian),
    "golden_cross": ("Golden cross 50/200", "trend", system_golden_cross),
    "donchian_trend": ("Donchian + 200-day filter", "trend", system_donchian_trend),
    "donchian_atr_stop": ("Donchian + 2xATR stop", "trend", system_donchian_atr_stop),
    "donchian_atr_sized": ("Donchian + ATR sizing", "trend", system_donchian_atr_sized),
    "trend_atr_sized": ("200-day trend + ATR sizing", "trend", system_trend_atr_sized),
    "rsi_reversion": ("RSI mean reversion", "mean-reversion", system_rsi_reversion),
    "bollinger": ("Bollinger mean reversion", "mean-reversion", system_bollinger),
    "rsi2": ("RSI(2) mean reversion", "mean-reversion", system_rsi2),
}
TREND_KEYS = [k for k, (_, g, _) in SYSTEMS.items() if g == "trend"]
MEANREV_KEYS = [k for k, (_, g, _) in SYSTEMS.items() if g == "mean-reversion"]
# Systems that need true highs/lows (ATR or Donchian levels). On
# close-only fallback data these are reported as unknown.
NEEDS_HIGH_LOW = {"donchian", "donchian_trend", "donchian_atr_stop",
                  "donchian_atr_sized", "trend_atr_sized"}


# ------------------------------------------------------------- evaluation

def settled_frame(df, as_of=None):
    """Drop candles after the last settled UTC day.

    ``as_of`` is a date used as the settled close (for tests and
    back-checks); when omitted it defaults to yesterday in UTC. The
    current UTC day's candle is always partial, so it is never used.
    """
    if as_of is None:
        from cmc_client import last_settled_utc_day
        as_of = last_settled_utc_day()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    settled = df[df["date"] <= pd.Timestamp(as_of)].copy()
    return settled.sort_values("date").reset_index(drop=True)


def _is_close_only(df):
    """True when high/low carry no information (fallback data where
    open/high/low were all set to the close)."""
    return bool(((df["high"] == df["close"]) & (df["low"] == df["close"])).all())


def _current_run(pos):
    """(value, first index of the current consecutive run) of a position
    series, ignoring leading NaNs. The run is tracked on the binary
    (position > 0) series so a changing ATR size does not reset the
    "since" date."""
    binary = pd.Series(
        np.where(pos.notna(), (pos.fillna(0) > 0).astype(float), np.nan),
        index=pos.index)
    valid = binary.dropna()
    if len(valid) == 0:
        return None, None
    val = valid.iloc[-1]
    i = len(valid) - 1
    while i > 0 and valid.iloc[i - 1] == val:
        i -= 1
    return val, valid.index[i]


def _num(value):
    """NaN-safe float (or None) for JSON output."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def evaluate_coin(df, as_of=None):
    """Evaluate all 12 systems for one coin as of the last settled
    close. ``df`` is daily OHLCV (date, open, high, low, close, volume),
    oldest first; it may include the partial current-day candle, which
    is dropped. Returns a dict of scores, readings and per-system state.
    """
    settled = settled_frame(df, as_of=as_of)
    if settled.empty:
        raise ValueError("no settled candles available")
    close_only = _is_close_only(settled)
    ind = add_indicators(settled)
    last = ind.iloc[-1]
    settled_date = settled["date"].max().date()

    systems = {}
    for key, (name, group, fn) in SYSTEMS.items():
        if close_only and key in NEEDS_HIGH_LOW:
            systems[key] = {"name": name, "group": group, "in": None,
                            "size": None, "since": None,
                            "note": "needs true high/low (close-only data)"}
            continue
        pos = fn(ind)
        val, run_idx = _current_run(pos)
        is_in = bool(val == 1.0) if val is not None else None
        last_pos = pos.iloc[-1]
        size = float(last_pos) if (is_in and last_pos is not None
                                   and not pd.isna(last_pos)) else None
        systems[key] = {
            "name": name,
            "group": group,
            "in": is_in,
            "size": size,
            "since": (ind["date"].iloc[run_idx].date().isoformat()
                      if run_idx is not None else None),
            "note": None,
        }

    def score(keys):
        known = [systems[k]["in"] for k in keys
                 if systems[k]["in"] is not None]
        return sum(1 for v in known if v), len(known)

    trend_in, trend_n = score(TREND_KEYS)
    meanrev_in, meanrev_n = score(MEANREV_KEYS)
    close = _num(last["close"])
    sma200 = _num(last["sma200"])
    atr14 = _num(last["atr14"])

    return {
        "settled_date": settled_date.isoformat(),
        "history_days": len(settled),
        "close_only_data": close_only,
        "close": close,
        "systems": systems,
        "trend_in": trend_in,
        "trend_n": trend_n,
        "meanrev_in": meanrev_in,
        "meanrev_n": meanrev_n,
        "trend_fraction": (trend_in / trend_n) if trend_n else None,
        "rsi14": _num(last["rsi14"]),
        "rsi2": _num(last["rsi2"]),
        "sma50": _num(last["sma50"]),
        "sma200": sma200,
        "pct_vs_sma200": ((close / sma200 - 1)
                          if close and sma200 else None),
        "macd_hist": _num(last["macd_hist"]),
        "pct_b": _num(last["pct_b"]),
        "mom_12_1": _num(last["mom_12_1"]),
        "atr14": atr14,
        "atr_size": _num(_atr_size(atr14, close)) if atr14 and close else None,
        "donchian_long_trigger": _num(last["donch_hi20_prev"]),
        "donchian_exit": _num(last["donch_lo10_prev"]),
        "atr_stop_2x": ((close - 2 * atr14)
                        if close and atr14 else None),
    }


def as_of_date(value):
    """Parse a YYYY-MM-DD --as-of value into a date."""
    return date.fromisoformat(value)
