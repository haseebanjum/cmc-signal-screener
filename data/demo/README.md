# Demo data

`btc-coinbase-ohlcv-sample.csv` is the last 800 days of Coinbase BTC-USD
daily OHLCV (2024-09-10 to 2026-11-18), sampled from the author's local
backtest dataset. 800 days is enough for every system the screener runs:
SMA200 needs 200 candles, 12-1 momentum needs 252, and the stateful
systems (Donchian, RSI, Bollinger) converge well inside the window.

It exists so judges can run `python3 screen.py --demo` with no API key
and no network dependency on the keyed endpoints. Live prices in demo
mode still come from the keyless CMC quotes endpoint when reachable.

Note: this is a snapshot cut on 2026-11-19. The final 2026-11-18 candle
was refreshed from Coinbase after the UTC day settled (close
$62,981.71), so every candle in the file is a settled daily close.
The full run fetches fresh settled history from CMC OHLCV instead.
