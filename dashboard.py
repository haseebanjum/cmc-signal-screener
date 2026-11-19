#!/usr/bin/env python3
"""Standalone HTML dashboard for the screener.

build_dashboard writes a single self-contained file (inline CSS, a
small inline script for column sorting, no external dependencies) that
works when opened straight from disk. It shows the ranked coin table
with trend-score bars, an expandable per-coin system breakdown, the
market context header, and a footer naming the CMC endpoints used.
"""
import html

ENDPOINTS_FOOTER = (
    "Data: CoinMarketCap API — "
    "GET /v3/cryptocurrency/listings/latest, "
    "GET /v3/cryptocurrency/quotes/latest, "
    "GET /v2/cryptocurrency/ohlcv/historical (keyed), "
    "GET /v3/fear-and-greed/latest, "
    "GET /v1/global-metrics/quotes/latest"
)


def _fmt_price(value):
    if value is None:
        return "&mdash;"
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def _fmt_pct(value, signed=True):
    if value is None:
        return "&mdash;"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _fmt_mcap(value):
    if value is None:
        return "&mdash;"
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def _score_bar(in_count, total):
    if total is None or total == 0:
        return '<span class="na">n/a</span>'
    pct = round(100 * in_count / total)
    return (f'<span class="bar"><span class="bar-fill" '
            f'style="width:{pct}%"></span></span> '
            f'<span class="score">{in_count}/{total}</span>')


def _system_rows(coin):
    systems = coin.get("signals", {}).get("systems", {})
    rows = []
    for key, s in systems.items():
        state = "IN" if s["in"] is True else ("OUT" if s["in"] is False
                                             else "n/a")
        cls = ("in" if s["in"] is True else "out" if s["in"] is False
               else "na")
        size = f" ({s['size']:.0%} sized)" if s.get("size") else ""
        since = s.get("since") or "&mdash;"
        note = f" — {html.escape(s['note'])}" if s.get("note") else ""
        rows.append(
            f'<tr><td>{html.escape(s["name"])}</td>'
            f'<td class="{cls}">{state}{size}</td>'
            f'<td>{since}</td><td>{html.escape(s["group"])}{note}</td></tr>')
    return "\n".join(rows)


def _coin_rows(rows):
    out = []
    for coin in rows:
        sig = coin.get("signals") or {}
        name = (f'<strong>{html.escape(coin["symbol"])}</strong><br>'
                f'<span class="muted">{html.escape(coin["name"])} '
                f'#{coin["rank"]}</span>')
        trend = _score_bar(sig.get("trend_in"), sig.get("trend_n"))
        meanrev = _score_bar(sig.get("meanrev_in"), sig.get("meanrev_n"))
        chg = coin.get("change_24h")
        chg_cls = ("up" if chg and chg > 0 else "down" if chg and chg < 0
                   else "")
        chg_txt = ("&mdash;" if chg is None
                   else f"{chg:+.1f}%")
        rsi = sig.get("rsi14")
        rsi_txt = "&mdash;" if rsi is None else f"{rsi:.1f}"
        vs200 = _fmt_pct(sig.get("pct_vs_sma200"))
        trigger = _fmt_price(sig.get("donchian_long_trigger"))
        stop = _fmt_price(sig.get("atr_stop_2x"))
        out.append(
            f'<tr class="coin-row">'
            f'<td>{name}</td>'
            f'<td class="num">{_fmt_price(coin.get("price"))}</td>'
            f'<td class="num {chg_cls}">{chg_txt}</td>'
            f'<td class="num">{_fmt_mcap(coin.get("market_cap"))}</td>'
            f'<td>{trend}</td><td>{meanrev}</td>'
            f'<td class="num">{rsi_txt}</td>'
            f'<td class="num">{vs200}</td>'
            f'<td class="num">{trigger}</td>'
            f'<td class="num">{stop}</td></tr>')
        if sig.get("systems"):
            out.append(
                f'<tr class="detail-row"><td colspan="10"><details>'
                f'<summary>Systems for {html.escape(coin["symbol"])} '
                f'(settled close {sig.get("settled_date", "&mdash;")}, '
                f'{sig.get("history_days", "&mdash;")} days of history)'
                f'</summary><table class="systems"><tr>'
                f'<th>System</th><th>Signal</th><th>Since</th>'
                f'<th>Group</th></tr>'
                f'{_system_rows(coin)}</table></details></td></tr>')
        else:
            out.append(
                f'<tr class="detail-row"><td colspan="10">'
                f'<span class="muted">No history available for this coin '
                f'in this run, so no signals were computed. Live price '
                f'and 24h change above come from the CMC quote.</span>'
                f'</td></tr>')
    return "\n".join(out)


def build_dashboard(rows, context, path):
    """Write the standalone dashboard HTML.

    ``rows`` is the ranked list of coin dicts from screen.py (each with
    ``signals`` or None). ``context`` carries run metadata: run date,
    settled date, mode, Fear & Greed, BTC dominance, total market cap,
    universe size and the endpoints used.
    """
    fg = context.get("fear_greed") or {}
    fg_txt = ("&mdash;" if fg.get("value") is None
              else f"{fg['value']} ({fg.get('value_classification', '')})")
    dom = context.get("btc_dominance")
    dom_txt = "&mdash;" if dom is None else f"{dom:.1f}%"
    total = _fmt_mcap(context.get("total_market_cap"))
    title = "Multi-coin signal screener"
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} &mdash; {context.get("run_date", "")}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         max-width: 1100px; margin: 2rem auto; padding: 0 1rem;
         color: #1a1a1a; background: #fff; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.2rem; }}
  .sub {{ color: #555; margin-bottom: 1.2rem; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 0.8rem; margin-bottom: 1.4rem; }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 0.7rem 1rem;
          min-width: 150px; background: #f8f9fa; }}
  .card .label {{ font-size: 0.75rem; color: #666; text-transform: uppercase; }}
  .card .value {{ font-size: 1.25rem; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ border-bottom: 1px solid #e5e5e5; padding: 0.45rem 0.5rem;
           text-align: left; vertical-align: middle; }}
  th {{ background: #f1f3f5; cursor: pointer; white-space: nowrap; }}
  th:hover {{ background: #e9ecef; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .muted {{ color: #666; font-size: 0.85em; }}
  .up {{ color: #137333; }} .down {{ color: #a50e0e; }}
  .bar {{ display: inline-block; width: 70px; height: 10px; background: #e9ecef;
         border-radius: 5px; overflow: hidden; vertical-align: middle; }}
  .bar-fill {{ display: block; height: 100%; background: #1f6feb; }}
  .score {{ font-variant-numeric: tabular-nums; }}
  .systems {{ margin: 0.5rem 0; }}
  .systems th {{ cursor: default; }}
  .in {{ color: #137333; font-weight: 600; }}
  .out {{ color: #666; }} .na {{ color: #999; }}
  details summary {{ cursor: pointer; color: #1f6feb; }}
  footer {{ margin-top: 2rem; font-size: 0.8rem; color: #666;
           border-top: 1px solid #e5e5e5; padding-top: 0.8rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="sub">Run {context.get("run_date", "")} &middot; mode:
{html.escape(context.get("mode", ""))} &middot; signals as of the settled
{context.get("settled_date", "&mdash;")} close (last settled UTC day) &middot;
{len(rows)} coins screened.</p>
<div class="cards">
  <div class="card"><div class="label">Fear &amp; Greed</div>
    <div class="value">{fg_txt}</div></div>
  <div class="card"><div class="label">BTC dominance</div>
    <div class="value">{dom_txt}</div></div>
  <div class="card"><div class="label">Total market cap</div>
    <div class="value">{total}</div></div>
  <div class="card"><div class="label">Coins with signals</div>
    <div class="value">{sum(1 for r in rows if r.get("signals"))} / {len(rows)}</div></div>
</div>
<table id="screen">
<thead><tr>
  <th data-sort="text">Coin</th><th data-sort="num">Price</th>
  <th data-sort="num">24h</th><th data-sort="num">Mkt cap</th>
  <th data-sort="num">Trend</th><th data-sort="num">Mean-rev</th>
  <th data-sort="num">RSI(14)</th><th data-sort="num">vs SMA200</th>
  <th data-sort="num">Donchian trigger</th><th data-sort="num">2xATR stop</th>
</tr></thead>
<tbody>
{_coin_rows(rows)}
</tbody>
</table>
<footer>
<p>{html.escape(ENDPOINTS_FOOTER)}</p>
<p>Signals use the close of day t and would execute at the open of day
t+1. Backtests behind these systems charged 0.15% per side and were
long-only. Trend systems beat mean reversion on BTC in backtests; past
performance is not a prediction. Not financial advice.</p>
</footer>
<script>
// Click a header to sort. Detail rows follow their coin row.
document.querySelectorAll("#screen th").forEach((th, idx) => {{
  th.addEventListener("click", () => {{
    const tbody = document.querySelector("#screen tbody");
    const groups = [];
    let current = null;
    tbody.querySelectorAll("tr").forEach(tr => {{
      if (tr.classList.contains("coin-row")) {{ current = [tr]; groups.push(current); }}
      else if (current) {{ current.push(tr); }}
    }});
    const numeric = th.dataset.sort === "num";
    const val = (tr) => {{
      const cell = tr[0].children[idx];
      const text = cell.textContent.replace(/[$,%+]/g, "").replace(/,/g, "").trim();
      if (numeric) {{
        const m = text.match(/-?\\d+(\\.\\d+)?/);
        return m ? parseFloat(m[0]) : -Infinity;
      }}
      return text.toLowerCase();
    }};
    const asc = th.dataset.dir !== "asc";
    groups.sort((a, b) => {{
      const x = val(a), y = val(b);
      return asc ? (x > y ? 1 : x < y ? -1 : 0) : (x < y ? 1 : x > y ? -1 : 0);
    }});
    th.dataset.dir = asc ? "asc" : "desc";
    groups.forEach(g => g.forEach(tr => tbody.appendChild(tr)));
  }});
}});
</script>
</body>
</html>
"""
    path.write_text(html_doc)
    return path
