# DoraHacks submission draft

Fill in the [TODO] items once the repo is public and the demo video
is uploaded, then paste into the DoraHacks submission form.

## Project name

Multi-coin signal screener

## Track

Markets and Trading Tools

## One-line summary

Ranks the top 20 coins on 12 backtested trading systems, so a trader
can see at a glance which coins are in a real uptrend and which are
just oversold.

## Description

Most screeners show one indicator at a time and leave you to guess
which ones ever worked. This one does the opposite. Every system it
scores was backtested on BTC daily data first: trend and breakout
systems work (best: Donchian 20/10 breakout, 34.1% CAGR), mean
reversion mostly does not. So the ranking leads with the trend score,
how many of the 9 trend systems are long, and shows the mean-reversion
score separately.

One command screens the top 20 coins by market cap (stablecoins and
wrapped duplicates excluded), each scored on 12 systems as of the
last settled daily close, with RSI(14), distance from the 200-day
average, the Donchian long trigger and exit, and a 2xATR stop.
Output is a ranked terminal table, Markdown/CSV/JSON reports, and a
standalone HTML dashboard with no external dependencies. A demo mode
runs with no API key at all.

## Repository

[TODO] https://github.com/haseebanjum/cmc-signal-screener

## Demo video

[TODO] YouTube link (script in docs/demo_video_script.md)

## X post

[TODO] Post URL, with the DoraHacks submission link, the demo video
and #BuildwithCMC

## CMC API endpoints used

- GET /v3/cryptocurrency/listings/latest — universe: top coins by
  market cap, with tags for the stablecoin/wrapped exclusion
- GET /v3/cryptocurrency/quotes/latest — live price, 24h change,
  market cap per coin
- GET /v2/cryptocurrency/ohlcv/historical — 400 daily candles per
  coin for the signals (keyed, Startup tier)
- GET /v3/fear-and-greed/latest — market context
- GET /v1/global-metrics/quotes/latest — BTC dominance, total market
  cap
- GET /v3/cryptocurrency/quotes/historical — documented fallback
  only, not used in the main run

Visible evidence of a real API call: `docs/sample_api_response.json`
in the repo holds real keyless listings and Fear & Greed responses
fetched live, and every run caches its responses under `data/cache/`
(ignored by git).

## What the API made possible, and where it got in the way

Made possible: one API covered the whole product, from the ranked
universe to 400 daily candles per coin to the Fear & Greed context,
with generous Startup credits (about 4 credits per coin per full
refresh). The keyless tier made the zero-setup demo mode possible.

Got in the way: (1) OHLCV history is gated to Startup and above, so
the full screener cannot run on the free Basic key, and the error
(403, code 1006) is the only signal; (2) keyless anonymous access is
rate limited (error 1022) on shared IPs, which bit during
development until caching and backoff were added; (3) v3 responses
are inconsistent about shapes: `quote` is a list on listings/quotes
but a dict on historical quotes, `quotes/latest` data is a list
rather than an id-keyed dict, and `tags` are objects on quotes but
strings on listings, so the client normalises all three; (4) with
`interval=daily`, quotes history returns the first quote of each
day rather than the close, which is a trap for anyone building
signals on it.
