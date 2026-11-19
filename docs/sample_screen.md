# Multi-coin signal screen — 2026-11-19

Mode: demo. Signals as of the settled **2026-11-18** close (last settled UTC day). Fear & Greed: **11 (Extreme fear)**.

| # | Coin | Price | 24h | Mkt cap | Trend | Mean-rev | RSI(14) | vs SMA200 | Donchian trigger | 2xATR stop |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | BTC (Bitcoin) | $62,752 | -2.3% | $1.26T | 1/9 | 3/3 | 21.8 | -13.4% | $81,890 | $57,671 |

## Per-coin systems

### BTC — trend 1/9, mean-reversion 3/3 (800 days of history, source: demo-sample)

| System | Signal | Since |
|---|---|---|
| 200-day trend | OUT | 2026-11-12 |
| Momentum + trend filter | OUT | 2026-11-12 |
| MACD crossover | OUT | 2026-09-29 |
| Donchian 20/10 breakout | OUT | 2026-10-08 |
| Golden cross 50/200 | IN | 2026-09-08 |
| Donchian + 200-day filter | OUT | 2026-10-08 |
| Donchian + 2xATR stop | OUT | 2026-10-08 |
| Donchian + ATR sizing | OUT | 2026-10-08 |
| 200-day trend + ATR sizing | OUT | 2026-11-12 |
| RSI mean reversion | IN | 2026-11-12 |
| Bollinger mean reversion | IN | 2026-11-12 |
| RSI(2) mean reversion | IN | 2026-10-25 |

## Endpoints used

- `GET /v3/cryptocurrency/listings/latest`
- `GET /v3/cryptocurrency/quotes/latest`
- `GET /v2/cryptocurrency/ohlcv/historical (keyed)`
- `GET /v3/fear-and-greed/latest`
- `GET /v1/global-metrics/quotes/latest`

Signals use the close of day t and would execute at the open of day t+1. Backtests charged 0.15% per side and were long-only. Not financial advice.
