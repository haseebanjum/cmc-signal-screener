#!/usr/bin/env python3
"""Multi-coin signal screener — CLI.

Ranks the top coins by market cap on 12 backtested trading systems
(9 trend, 3 mean-reversion), evaluated as of the last settled daily
close. Live prices come from the CoinMarketCap API; history comes from
CMC daily OHLCV (keyed, Startup tier+) or, in demo mode, from a
committed BTC sample.

Usage:
    python3 screen.py --top 20            # full run (needs CMC_API_KEY
                                          # for history; live quotes
                                          # and Fear & Greed are keyless)
    python3 screen.py --demo              # zero-setup run, no key needed
    python3 screen.py --coins BTC,ETH,SOL # quick test on a few coins
    python3 screen.py --as-of 2026-11-17  # evaluate signals as of a past
                                          # settled close (testing)

Outputs (in results/, dated by run day in UTC):
    screen_YYYY-MM-DD.md / .csv / .json and dashboard.html.

The API key is read from the CMC_API_KEY environment variable or a
local .env file (git-ignored). It is never printed or written to any
output file.
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cmc_client as C
import signals as S
from dashboard import build_dashboard

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DEMO_CSV = ROOT / "data" / "demo" / "btc-coinbase-ohlcv-sample.csv"
DEMO_COIN_ID = 1  # BTC on CoinMarketCap

ENDPOINTS_USED = [
    "GET /v3/cryptocurrency/listings/latest",
    "GET /v3/cryptocurrency/quotes/latest",
    "GET /v2/cryptocurrency/ohlcv/historical (keyed)",
    "GET /v3/fear-and-greed/latest",
    "GET /v1/global-metrics/quotes/latest",
]


def _fmt_price(value):
    if value is None:
        return "—"
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def _fmt_pct(value):
    if value is None:
        return "—"
    return f"{value:+.1f}%"


def _fmt_mcap(value):
    if value is None:
        return "—"
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    return f"${value / 1e6:.0f}M"


# ------------------------------------------------------------------ inputs

def fetch_universe(client, top, coins=None, warnings=None):
    """Top coins by market cap, minus stablecoins / wrapped duplicates.

    Pages through listings (100 per page) until enough coins pass the
    filter. With --coins, keeps only the requested symbols.
    """
    warnings = warnings if warnings is not None else []
    raw = []
    seen = set()
    for page in range(3):
        batch = client.listings_latest(limit=100, start=page * 100 + 1)
        fresh = [c for c in batch if c["id"] not in seen]
        seen.update(c["id"] for c in fresh)
        raw.extend(fresh)
        kept = [c for c in raw if not C.is_excluded(c)]
        if len(kept) >= top and not coins:
            break
        if len(batch) < 100:
            break
    universe = [c for c in raw if not C.is_excluded(c)]
    universe.sort(key=lambda c: c.get("cmc_rank") or 10**9)
    if coins:
        wanted = {s.upper() for s in coins}
        picked = [c for c in universe
                  if (c.get("symbol") or "").upper() in wanted]
        found = {(c.get("symbol") or "").upper() for c in picked}
        for symbol in sorted(wanted - found):
            warnings.append(f"requested {symbol}: not in the screened "
                            f"universe (excluded or outside the top "
                            f"listings fetched)")
        universe = picked
    return universe[:top]


def fetch_live_quotes(client, coins, warnings):
    """Fresh live quotes by id; falls back to the quote fields already
    carried by the listings response when the quotes call fails."""
    ids = [c["id"] for c in coins]
    try:
        quotes = client.quotes_latest(ids)
        return {i: C.normalise_coin(q) for i, q in quotes.items()}
    except C.CMCError as exc:
        warnings.append(f"quotes/latest failed ({exc}); using the quote "
                        f"fields from listings/latest instead")
        return {c["id"]: C.normalise_coin(c) for c in coins}


def fetch_history(client, coin_id, warnings, symbol="?"):
    """Daily OHLCV for one coin (keyed). Falls back to the close-only
    quotes/historical series when OHLCV is unavailable, and to None
    when there is no key and no cached history. Returns (df, source)
    where source is 'ohlcv', 'quotes-fallback' or None."""
    try:
        df = client.ohlcv_historical(coin_id, count=400)
        if df.empty:
            raise C.CMCError("empty OHLCV response")
        return df, "ohlcv"
    except C.CMCError as exc:
        if getattr(exc, "code", None) == "NO_KEY":
            return None, None
        warnings.append(f"{symbol}: OHLCV failed ({exc}); trying the "
                        f"quotes/historical fallback")
    try:
        df = client.quotes_historical(coin_id, count=400)
        if df.empty:
            raise C.CMCError("empty quotes/historical response")
        return df, "quotes-fallback"
    except C.CMCError as exc:
        warnings.append(f"{symbol}: no history available ({exc})")
        return None, None


def fetch_context(client, warnings):
    """Market-wide context: Fear & Greed and global metrics. Best
    effort — a failure here never aborts the screen."""
    context = {"fear_greed": {}, "btc_dominance": None,
               "eth_dominance": None, "total_market_cap": None}
    try:
        fg = client.fear_greed_latest()
        context["fear_greed"] = {
            "value": fg.get("value"),
            "value_classification": fg.get("value_classification"),
            "update_time": fg.get("update_time"),
        }
    except C.CMCError as exc:
        warnings.append(f"fear-and-greed failed ({exc})")
    try:
        gm = client.global_metrics()
        context["btc_dominance"] = gm.get("btc_dominance")
        context["eth_dominance"] = gm.get("eth_dominance")
        quote = gm.get("quote") or {}
        usd = quote.get("USD") if isinstance(quote, dict) else None
        if usd is None and isinstance(quote, list):
            usd = next((q for q in quote if q.get("symbol") == "USD"), None)
        if usd:
            context["total_market_cap"] = usd.get("total_market_cap")
    except C.CMCError as exc:
        warnings.append(f"global-metrics failed ({exc})")
    return context


# ------------------------------------------------------------------ screen

def rank_rows(rows):
    """Rank by trend score (fraction of valid trend systems long), then
    by 24h change. Coins without signals sort last."""
    def key(row):
        sig = row.get("signals") or {}
        frac = sig.get("trend_fraction")
        chg = row.get("change_24h")
        return (-(frac if frac is not None else -1),
                -(chg if chg is not None else -1e9))
    return sorted(rows, key=key)


def build_rows(client, universe, live, as_of, warnings, history_override=None):
    """Evaluate signals for each coin in the universe. Returns a list
    of row dicts (ranked). ``history_override`` maps coin id -> (df,
    source) and is used by --demo."""
    rows = []
    for coin in universe:
        norm = live.get(coin["id"], C.normalise_coin(coin))
        row = {
            "id": coin["id"],
            "symbol": coin.get("symbol"),
            "name": coin.get("name"),
            "rank": coin.get("cmc_rank"),
            "price": norm.get("price"),
            "change_24h": norm.get("change_24h"),
            "change_7d": norm.get("change_7d"),
            "market_cap": norm.get("market_cap"),
            "volume_24h": norm.get("volume_24h"),
            "signals": None,
            "history_source": None,
        }
        if history_override and coin["id"] in history_override:
            df, source = history_override[coin["id"]]
        else:
            df, source = fetch_history(client, coin["id"], warnings,
                                       symbol=coin.get("symbol"))
        if df is None:
            warnings.append(f"{coin.get('symbol')}: no history, signals "
                            f"skipped (live price shown only)")
            rows.append(row)
            continue
        row["history_source"] = source
        try:
            row["signals"] = S.evaluate_coin(df, as_of=as_of)
        except ValueError as exc:
            warnings.append(f"{coin.get('symbol')}: {exc}")
        rows.append(row)
    return rank_rows(rows)


def print_table(rows, context, settled_date):
    fg = context.get("fear_greed") or {}
    fg_txt = ("n/a" if fg.get("value") is None
              else f"{fg['value']} ({fg.get('value_classification', '')})")
    dom = context.get("btc_dominance")
    print(f"settled close: {settled_date} | Fear & Greed: {fg_txt} | "
          f"BTC dominance: {dom:.1f}%"
          if dom is not None else f"settled close: {settled_date} | "
          f"Fear & Greed: {fg_txt}")
    print(f"{'#':>2} {'Coin':<6} {'Price':>12} {'24h':>7} {'MktCap':>8} "
          f"{'Trend':>6} {'MeanRev':>7} {'RSI14':>6} {'vsSMA200':>8} "
          f"{'DonchTrigger':>13} {'2xATRstop':>11}")
    for i, row in enumerate(rows, 1):
        sig = row.get("signals") or {}
        trend = ("—" if sig.get("trend_n") in (None, 0)
                 else f"{sig['trend_in']}/{sig['trend_n']}")
        meanrev = ("—" if sig.get("meanrev_n") in (None, 0)
                   else f"{sig['meanrev_in']}/{sig['meanrev_n']}")
        rsi = "—" if sig.get("rsi14") is None else f"{sig['rsi14']:.1f}"
        vs200 = ("—" if sig.get("pct_vs_sma200") is None
                 else f"{sig['pct_vs_sma200']:+.1%}")
        print(f"{i:>2} {row['symbol']:<6} "
              f"{_fmt_price(row.get('price')):>12} "
              f"{_fmt_pct(row.get('change_24h')):>7} "
              f"{_fmt_mcap(row.get('market_cap')):>8} "
              f"{trend:>6} {meanrev:>7} {rsi:>6} {vs200:>8} "
              f"{_fmt_price(sig.get('donchian_long_trigger')):>13} "
              f"{_fmt_price(sig.get('atr_stop_2x')):>11}")


# ----------------------------------------------------------------- outputs

def write_outputs(rows, context, mode, run_date):
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = run_date.isoformat()
    settled_dates = [r["signals"]["settled_date"] for r in rows
                     if r.get("signals")]
    settled_date = max(settled_dates) if settled_dates else "—"

    payload = {
        "run_date": stamp,
        "mode": mode,
        "settled_date": settled_date,
        "fear_greed": context.get("fear_greed") or {},
        "btc_dominance": context.get("btc_dominance"),
        "eth_dominance": context.get("eth_dominance"),
        "total_market_cap": context.get("total_market_cap"),
        "endpoints_used": ENDPOINTS_USED,
        "coins": rows,
    }
    json_path = RESULTS / f"screen_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    csv_path = RESULTS / f"screen_{stamp}.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "symbol", "name", "price", "change_24h",
                         "market_cap", "trend_in", "trend_n",
                         "meanrev_in", "meanrev_n", "rsi14",
                         "pct_vs_sma200", "donchian_long_trigger",
                         "donchian_exit", "atr_stop_2x", "settled_date",
                         "history_days", "history_source"])
        for row in rows:
            sig = row.get("signals") or {}
            writer.writerow([
                row.get("rank"), row.get("symbol"), row.get("name"),
                row.get("price"), row.get("change_24h"),
                row.get("market_cap"), sig.get("trend_in"),
                sig.get("trend_n"), sig.get("meanrev_in"),
                sig.get("meanrev_n"), sig.get("rsi14"),
                sig.get("pct_vs_sma200"), sig.get("donchian_long_trigger"),
                sig.get("donchian_exit"), sig.get("atr_stop_2x"),
                sig.get("settled_date"), sig.get("history_days"),
                row.get("history_source"),
            ])

    fg = context.get("fear_greed") or {}
    fg_txt = ("n/a" if fg.get("value") is None
              else f"{fg['value']} ({fg.get('value_classification', '')})")
    lines = [
        f"# Multi-coin signal screen — {stamp}",
        "",
        f"Mode: {mode}. Signals as of the settled **{settled_date}** "
        f"close (last settled UTC day). Fear & Greed: **{fg_txt}**.",
        "",
        "| # | Coin | Price | 24h | Mkt cap | Trend | Mean-rev | "
        "RSI(14) | vs SMA200 | Donchian trigger | 2xATR stop |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(rows, 1):
        sig = row.get("signals") or {}
        trend = ("—" if sig.get("trend_n") in (None, 0)
                 else f"{sig['trend_in']}/{sig['trend_n']}")
        meanrev = ("—" if sig.get("meanrev_n") in (None, 0)
                   else f"{sig['meanrev_in']}/{sig['meanrev_n']}")
        rsi = "—" if sig.get("rsi14") is None else f"{sig['rsi14']:.1f}"
        vs200 = ("—" if sig.get("pct_vs_sma200") is None
                 else f"{sig['pct_vs_sma200']:+.1%}")
        lines.append(
            f"| {i} | {row['symbol']} ({row['name']}) | "
            f"{_fmt_price(row.get('price'))} | "
            f"{_fmt_pct(row.get('change_24h'))} | "
            f"{_fmt_mcap(row.get('market_cap'))} | {trend} | {meanrev} | "
            f"{rsi} | {vs200} | "
            f"{_fmt_price(sig.get('donchian_long_trigger'))} | "
            f"{_fmt_price(sig.get('atr_stop_2x'))} |")
    lines.append("")
    lines.append("## Per-coin systems")
    lines.append("")
    for row in rows:
        sig = row.get("signals")
        if not sig:
            lines.append(f"### {row['symbol']}")
            lines.append("")
            lines.append("No history available in this run; no signals "
                         "computed.")
            lines.append("")
            continue
        lines.append(
            f"### {row['symbol']} — trend {sig['trend_in']}/{sig['trend_n']}, "
            f"mean-reversion {sig['meanrev_in']}/{sig['meanrev_n']} "
            f"({sig['history_days']} days of history, source: "
            f"{row.get('history_source')})")
        lines.append("")
        lines.append("| System | Signal | Since |")
        lines.append("|---|---|---|")
        for key, s in sig["systems"].items():
            state = ("IN" if s["in"] is True else "OUT"
                     if s["in"] is False else "n/a")
            since = s.get("since") or "—"
            note = f" ({s['note']})" if s.get("note") else ""
            lines.append(f"| {s['name']} | {state} | {since} |{note}")
        lines.append("")
    lines.append("## Endpoints used")
    lines.append("")
    lines.extend(f"- `{endpoint}`" for endpoint in ENDPOINTS_USED)
    lines.append("")
    lines.append("Signals use the close of day t and would execute at the "
                 "open of day t+1. Backtests charged 0.15% per side and "
                 "were long-only. Not financial advice.")
    md_path = RESULTS / f"screen_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n")

    dashboard_path = RESULTS / "dashboard.html"
    build_dashboard(rows, {
        "run_date": stamp,
        "mode": mode,
        "settled_date": settled_date,
        "fear_greed": context.get("fear_greed") or {},
        "btc_dominance": context.get("btc_dominance"),
        "total_market_cap": context.get("total_market_cap"),
    }, dashboard_path)
    return {"md": md_path, "csv": csv_path, "json": json_path,
            "dashboard": dashboard_path, "settled_date": settled_date}


# --------------------------------------------------------------------- main

def run_live(args, client, warnings):
    coins = ([c.strip() for c in args.coins.split(",") if c.strip()]
             if args.coins else None)
    universe = fetch_universe(client, args.top, coins=coins,
                              warnings=warnings)
    if not universe:
        raise SystemExit("no coins in the universe (check --coins)")
    live = fetch_live_quotes(client, universe, warnings)
    context = fetch_context(client, warnings)
    as_of = S.as_of_date(args.as_of) if args.as_of else None
    rows = build_rows(client, universe, live, as_of, warnings)
    return rows, context


def run_demo(args, client, warnings):
    """Zero-setup run: BTC signals from the committed sample history,
    plus keyless live quotes and market context when reachable."""
    if not DEMO_CSV.exists():
        raise SystemExit(f"demo data missing: {DEMO_CSV}")
    if args.coins:
        wanted = {c.strip().upper() for c in args.coins.split(",")}
        if wanted - {"BTC"}:
            warnings.append("demo mode only has BTC history; other "
                            "requested coins were skipped")
    demo_df = pd.read_csv(DEMO_CSV, parse_dates=["date"])
    universe = [{"id": DEMO_COIN_ID, "symbol": "BTC",
                 "name": "Bitcoin", "cmc_rank": 1}]
    live = fetch_live_quotes(client, universe, warnings)
    if DEMO_COIN_ID not in live:
        last_close = float(demo_df["close"].iloc[-1])
        live = {DEMO_COIN_ID: {"price": last_close, "change_24h": None,
                               "change_7d": None, "market_cap": None,
                               "volume_24h": None}}
        warnings.append("live quotes unreachable; demo shows the last "
                        "sample close as the price")
    context = fetch_context(client, warnings)
    as_of = S.as_of_date(args.as_of) if args.as_of else None
    rows = build_rows(client, universe, live, as_of, warnings,
                      history_override={DEMO_COIN_ID: (demo_df, "demo-sample")})
    return rows, context


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Multi-coin signal screener (CoinMarketCap API).")
    parser.add_argument("--top", type=int, default=20,
                        help="coins to screen (default 20)")
    parser.add_argument("--demo", action="store_true",
                        help="zero-setup run on the committed BTC sample "
                             "(no API key needed)")
    parser.add_argument("--coins",
                        help="comma-separated symbols to screen, e.g. "
                             "BTC,ETH,SOL")
    parser.add_argument("--as-of",
                        help="evaluate signals as of a past settled close, "
                             "YYYY-MM-DD (testing)")
    args = parser.parse_args(argv)

    C.load_dotenv()
    client = C.CMCClient()
    warnings = []
    mode = "demo" if args.demo else "live"
    if not args.demo and not client.has_key:
        warnings.append("no CMC_API_KEY set: history comes from cache only; "
                        "live quotes and market context still work keyless")

    if args.demo:
        rows, context = run_demo(args, client, warnings)
    else:
        rows, context = run_live(args, client, warnings)

    run_date = datetime.now(timezone.utc).date()
    outputs = write_outputs(rows, context, mode, run_date)
    print_table(rows, context, outputs["settled_date"])
    print()
    for warning in warnings:
        print(f"warning: {warning}")
    print(f"wrote: {outputs['md']}")
    print(f"wrote: {outputs['csv']}")
    print(f"wrote: {outputs['json']}")
    print(f"wrote: {outputs['dashboard']}")
    print(f"live API calls this run: {client.calls_made} "
          f"(everything else came from cache)")


if __name__ == "__main__":
    main()
