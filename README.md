# Multi-coin signal screener

A screener that ranks the top coins by market cap on **12 backtested
trading systems** (9 trend, 3 mean-reversion), evaluated as of the last
settled daily close. Built for the **Build with CMC: API Hackathon,
Round 2** — track: **Markets and Trading Tools**.

## Why a real trader would use this

Most screeners show you one indicator at a time and leave you to guess
which ones ever worked. This one does the opposite: every system it
scores was backtested on BTC daily data first, and the backtests say
trend systems work and mean reversion mostly does not (see
[Backtest evidence](#backtest-evidence)). So the ranking leads with
the trend score — how many of the 9 trend systems are long — and shows
the mean-reversion score separately, so a coin flashing "oversold"
doesn't get mistaken for a coin in an uptrend.

For each coin you get the composite scores plus the levels that decide
the next move: RSI(14), distance from the 200-day average, the Donchian
long trigger (prior 20-day high), the Donchian exit (prior 10-day low),
and a 2xATR stop. Market context — Fear & Greed and BTC dominance —
sits on top, because it is market-wide, not per-coin.

## Quick start

```bash
pip install -r requirements.txt

# Zero-setup demo: BTC signals from the committed sample history,
# live price from the keyless CMC API. No key needed.
python3 screen.py --demo

# Full run: top 20 coins (history needs a key — see below)
python3 screen.py --top 20

# Quick test on a few coins
python3 screen.py --coins BTC,ETH,SOL

# Evaluate signals as of a past settled close (testing)
python3 screen.py --as-of 2026-11-17
```

Every run prints a ranked table and writes dated outputs to `results/`:

- `screen_YYYY-MM-DD.md` — the ranked table plus a per-coin system breakdown
- `screen_YYYY-MM-DD.csv` — one row per coin, for spreadsheets
- `screen_YYYY-MM-DD.json` — the full structured result
- `dashboard.html` — a standalone dashboard (inline styles, no external
  dependencies, works opened straight from disk), with a sortable table,
  trend-score bars, and expandable per-coin detail

A sample of the Markdown output is committed at
[`docs/sample_screen.md`](docs/sample_screen.md), and a real API
response at [`docs/sample_api_response.json`](docs/sample_api_response.json).

## API key

Live listings, quotes, Fear & Greed and global metrics all work through
the **keyless** public API — no account, no key. Historical OHLCV is
gated to the **Startup tier and above**, so the full multi-coin screen
needs a key:

```bash
cp .env.example .env   # then put your key in .env (git-ignored)
# or
export CMC_API_KEY=your-key-here
```

Get a key at <https://coinmarketcap.com/api/>. Hackathon participants
get a free Startup tier for the event window. The key is read from the
environment only; it is never printed and never written to any output
file. Without a key, the screener still runs: live prices and market
context come through keyless, and coins without cached history are
shown with live data only and a clear warning.

Cost per full run is small: OHLCV costs ceil(points / 100) credits per
coin, so 400 daily candles ≈ 4 credits per coin — about 80 credits for
a 20-coin screen, against 450,000/month on Startup. Responses are
cached under `data/cache/` (git-ignored), so re-runs are nearly free.

## CMC endpoints used

Named explicitly, as the hackathon requires:

| Endpoint | Used for | Access |
|---|---|---|
| `GET /v3/cryptocurrency/listings/latest` | Universe: top coins by market cap, with live quote fields | Keyless |
| `GET /v3/cryptocurrency/quotes/latest` | Fresh live price, 24h change, market cap for chosen ids | Keyless |
| `GET /v2/cryptocurrency/ohlcv/historical` | Daily candles (400 per coin) for the signal calculations | Keyed, Startup+ |
| `GET /v3/fear-and-greed/latest` | Market-wide sentiment context | Keyless |
| `GET /v1/global-metrics/quotes/latest` | BTC/ETH dominance and total market cap | Keyless |
| `GET /v3/cryptocurrency/quotes/historical` | Documented fallback for history only — see the trap below | Keyed |

The universe excludes stablecoins / pegged instruments (by tag and by
symbol denylist) and wrapped / staked duplicates (WBTC, stETH, wstETH,
WETH, …), so the screen ranks assets, not dollar proxies.

## How the signals work

Signals are computed from data through the close of day **t** and would
execute at the open of day **t+1** — the same no-lookahead rule as the
backtests. The current UTC day's candle is partial until 00:00 UTC, so
it is always dropped; only the last settled close feeds a signal.

| Group | Systems |
|---|---|
| Trend (9) | 200-day trend, momentum + trend filter, MACD crossover, Donchian 20/10 breakout, golden cross 50/200, Donchian + 200-day filter, Donchian + 2xATR stop, Donchian + ATR sizing, 200-day trend + ATR sizing |
| Mean reversion (3) | RSI mean reversion, Bollinger mean reversion, RSI(2) mean reversion |

Composite per coin: trend score x/9, mean-reversion score x/3. Coins
are ranked by trend score, then by 24h change. ATR-sized systems invest
a Turtle-style fraction (2% of the account risked per trade, 2xATR
stop) instead of the full account; they count as IN whenever the
position is above zero, and the invested fraction is shown.

Systems whose inputs are not yet available (SMA200 needs 200 candles,
12-1 momentum needs 252) are reported as unknown, not as long or out.
The `quotes/historical` fallback carries close prices only, so the five
systems that need true highs and lows (Donchian family and ATR sizing)
are reported as unknown on fallback data.

The indicator and system code in `signals.py` is adapted from the
author's BTC backtest harness and is credited there; the logic is
unchanged, so the backtest evidence below applies directly.

## Execution assumptions

- Signals use the close of day t, execute at the open of day t+1.
- Backtests charged **0.15% per side** (0.10% fee + 0.05% slippage).
- **Long only.** No leverage, no shorting. Cash earns nothing.
- Backtests start from $10,000 on Coinbase BTC-USD daily OHLCV.

## Backtest evidence

Results below are from the author's BTC backtest harness (four runs,
2018-02-01 to 2026-11-15, 3,210 trading days), in
`~/workspace/crypto/backtest/results/btc-systems{,2,3,4}-2026-11-15.md`.
They were not re-run for this project; the same code and the same rules
produce the screener's signals.

| System | CAGR | Sharpe | Max drawdown |
|---|---:|---:|---:|
| Donchian 20/10 breakout | 34.1% | 0.93 | -51.7% |
| MACD crossover | 28.6% | 0.79 | -53.8% |
| Donchian + 200-day filter | 28.4% | 0.89 | -47.6% |
| Donchian + 2xATR stop | 24.9% | 0.81 | -53.2% |
| 200-day trend | 22.9% | 0.69 | -64.4% |
| Golden cross 50/200 | 18.1% | 0.59 | -66.0% |
| Momentum + trend filter | 10.2% | 0.44 | -71.7% |
| Donchian + ATR sizing | 10.1% | 1.01 | -15.9% |
| 200-day trend + ATR sizing | 8.5% | 0.85 | -14.7% |
| RSI mean reversion | 0.5% | 0.23 | -60.9% |
| RSI(2) mean reversion | -3.1% | 0.18 | -71.0% |
| Bollinger mean reversion | -6.7% | 0.02 | -70.7% |
| Buy and hold (reference) | 24.7% | 0.67 | -76.7% |

Two findings shape the screener. First, **trend and breakout work on
BTC**: Donchian 20/10 is the best returner (CAGR 34.1%), and Donchian
with ATR sizing has the best risk-adjusted return (Sharpe 1.01, worst
drawdown only -15.9%). Second, **mean reversion fails on BTC**: RSI,
Bollinger and RSI(2) all end flat or negative over the same window,
which is why the screener reports the mean-reversion score separately
instead of blending it into one number.

The signals were cross-checked against the author's daily BTC dashboard:
as of the 2026-11-17 settled close ($64,873.18), all 12 systems match —
trend 1/9 (only the golden cross long) and mean-reversion 3/3 — with
identical readings (RSI(14) 24.1, SMA200 $72,797, Donchian trigger
$81,890, 2xATR stop $59,605). The dashboard's 6-of-14 count includes
two Fear & Greed systems that are market-wide and are not scored
per-coin here.

## What the CMC API made possible / where it got in the way

**Made possible.** One API covers the whole screener: universe,
live quotes, daily history back to 2010 on Startup, and the Fear &
Greed index — no second data source is needed for the full run. The
keyless tier is genuinely usable: the demo mode runs end-to-end with
no account, which is what makes this repo runnable by a judge with
zero setup. Credit costs are small enough that a daily 20-coin screen
is a rounding error on Startup.

**Got in the way.**

- **Keyless rate limiting.** Anonymous access is rate-limited per IP.
  From a shared VM IP the keyless endpoints returned error 1022 on
  2026-11-19; it cleared after ~20 seconds. The client sends at most
  one keyless request per second, retries 1022 with backoff, and
  caches everything — but a first run on a busy shared IP can still
  take a while.
- **OHLCV is gated to Startup.** The free Basic tier has no daily
  candles, so no signal can be computed without a paid (or hackathon)
  key. The keyless demo exists because of this gate.
- **The `quotes/historical` daily trap.** With `interval=daily`, that
  endpoint returns the *first* quote of each day, not the close, and
  it carries no high/low. It looks like a history endpoint and is not
  one for signal purposes. It is implemented only as a labelled
  fallback, and ATR / Donchian systems are disabled on it.
- **Keyless v3 response shapes differ from the keyed docs.** On the
  keyless endpoints, `quote` is a *list* of per-currency objects (the
  keyed API returns a dict keyed by currency), `quotes/latest` returns
  `data` as a list (keyed returns a dict keyed by id), and `tags` are
  plain strings on listings but objects on quotes. The client
  normalises all three, but it had to be discovered by calling the
  endpoints — the docs describe the keyed shapes.
- **One history call per coin.** There is no batch OHLCV endpoint, so
  a 20-coin screen is 20 history calls. At ~4 credits each this is
  cheap, but it is 20 round trips.

## Project structure

```
screen.py          CLI: universe, live quotes, history, ranking, outputs
cmc_client.py      CMC API client (keyed + keyless), cache, retries
signals.py         Indicators + the 12 backtested systems
dashboard.py       Standalone HTML dashboard builder
data/demo/         Committed BTC sample history for --demo (800 days)
data/cache/        Cached API responses (git-ignored)
docs/              Sample API response + sample screen output
results/           Screen outputs (git-ignored, regenerated per run)
```

## Status

v1. Live keyless paths (listings, quotes, Fear & Greed, global
metrics) are verified against the real API. The keyed OHLCV path is
code-complete but **not yet tested live** — it needs the Startup key,
which arrives with the hackathon registration. The demo mode and the
BTC cross-check above verify the signal engine end to end.

## Disclaimer

This is a research tool, not financial advice. Backtested systems
describe the past; regimes change, and the backtests above include
drawdowns over 50%. Never trade on a screener alone.
