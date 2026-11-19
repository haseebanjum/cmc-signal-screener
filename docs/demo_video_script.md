# Demo video script (60-90 seconds)

Record a plain screen capture, no facecam needed. Narrate in your own
words; the lines below are a guide, not a script to read verbatim.
Record the full run (`--top 20`) once the Startup key works, so the
video shows all 20 coins with live signals.

## Shot 1 — the problem (0:00-0:10)

Show coinmarketcap.com homepage, then cut to the terminal.

> "Most screeners show you one indicator at a time. This one ranks
> the top 20 coins on twelve systems that were actually backtested
> first, so you can see at a glance which coins are in a real
> uptrend and which are just oversold."

## Shot 2 — run it (0:10-0:35)

Terminal, full screen. Type and run:

```bash
python3 screen.py --top 20
```

Let the ranked table print. Point at the columns while it renders.

> "One command. It pulls the top 20 from CoinMarketCap, fetches a
> year of daily candles per coin, and scores each one on nine trend
> systems and three mean-reversion systems, as of the last settled
> close. Trend first, because the backtests say trend works and
> mean reversion mostly doesn't."

## Shot 3 — the dashboard (0:35-0:55)

Open `results/dashboard.html` in the browser. Sort by trend score,
then expand one coin (BTC) to show its per-system breakdown.

> "Same result as a dashboard. Sortable, with the levels that
> decide the next move: the Donchian trigger, the 200-day average
> and a 2xATR stop. Fear and Greed sits on top as market context,
> not a per-coin signal."

## Shot 4 — the API evidence (0:55-1:15)

Show `docs/sample_api_response.json` briefly, then `cmc_client.py`
with the endpoint list.

> "Everything runs on the CMC API: listings for the universe,
> quotes for live prices, OHLCV history for the signals, plus Fear
> and Greed and global metrics for context. The repo names every
> endpoint and includes a real response."

## Shot 5 — close (1:15-1:30)

Back to the terminal table.

> "Built for the Build with CMC hackathon. Link to the repo and the
> DoraHacks submission are in the description."

## Recording notes

- Record at 1080p, terminal font large enough to read at half size.
- Keep the API key out of frame: the demo run needs no key, and
  the full run reads it from `.env`, which is never shown.
- If a live call hits the keyless rate limit mid-recording, wait
  20 seconds and re-run; the cache makes the second run instant.
- Export as MP4 and upload unlisted to YouTube, or attach the file
  directly to the DoraHacks submission.
