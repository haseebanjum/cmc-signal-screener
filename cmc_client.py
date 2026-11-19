#!/usr/bin/env python3
"""CoinMarketCap API client for the multi-coin signal screener.

Two access modes, both against pro-api.coinmarketcap.com:

* Keyless (no account, no key) — base ``/public-api``. A curated subset
  of endpoints: listings, quotes, global metrics and the Fear & Greed
  index. Anonymous access is rate-limited (CMC error 1022 seen from a
  shared IP on 2026-11-19, cleared after ~20s), so this client sends at
  most one request per second, retries with backoff, and caches every
  response under ``data/cache/``. The API key header is NEVER sent on
  keyless calls.
* Keyed — base ``/`` with header ``X-CMC_PRO_API_KEY`` from the
  ``CMC_API_KEY`` environment variable (or a local ``.env`` file, which
  is git-ignored). Needed for historical OHLCV, which is gated to the
  Startup tier and above.

Response-shape notes (verified live 2026-11-19 — the keyless v3 shape
differs from the keyed v1/v2 docs, so every accessor below normalises):

* ``quotes/latest`` returns ``data`` as a LIST on keyless, but as a dict
  keyed by coin id on keyed. Both shapes are accepted.
* ``quote`` is a LIST of per-currency objects (``[{"symbol": "USD",
  "price": ...}]``) on keyless v3, but a dict keyed by currency
  (``{"USD": {...}}``) on keyed. Both shapes are accepted.
* ``tags`` are plain strings on ``listings/latest`` but objects
  (``{"slug": ..., "name": ..., "category": ...}``) on
  ``quotes/latest``. Both shapes are accepted.

Endpoints used by the screener:

* ``GET /v3/cryptocurrency/listings/latest`` — universe (top coins by
  market cap) and live quote fields. Keyless.
* ``GET /v3/cryptocurrency/quotes/latest`` — live price, 24h change and
  market cap for chosen ids. Keyless.
* ``GET /v2/cryptocurrency/ohlcv/historical`` — daily candles for the
  signal calculations. KEYED ONLY (Startup tier and above). Credit cost
  is ceil(points / 100) per coin, so ~4 credits per coin for 400 daily
  candles.
* ``GET /v3/fear-and-greed/latest`` — market-wide sentiment context.
  Keyless.
* ``GET /v1/global-metrics/quotes/latest`` — BTC/ETH dominance and total
  market cap for the dashboard header. Keyless.
* ``GET /v3/cryptocurrency/quotes/historical`` — DOCUMENTED FALLBACK for
  history if OHLCV is unavailable. Trap: with ``interval=daily`` it
  returns the FIRST quote of each day, not the close, and it carries no
  high/low, so ATR and Donchian systems cannot run on it. Prefer OHLCV.

No API key is ever written to disk by this module: the cache stores API
responses only, and ``.env`` is git-ignored.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
KEYED_BASE = "https://pro-api.coinmarketcap.com"
KEYLESS_BASE = "https://pro-api.coinmarketcap.com/public-api"

# Error codes worth retrying: 1022 is the keyless/plan rate limit, 1008
# is a per-minute limit. Anything else fails fast.
RETRYABLE_CODES = {"1022", "1008"}
RETRY_BACKOFF_SECONDS = (5, 15, 30)

# Cache lifetimes (seconds). Quotes move fast, history barely moves.
TTL_LISTINGS = 3600
TTL_QUOTES = 300
TTL_FEAR_GREED = 3600
TTL_GLOBAL = 3600
TTL_HISTORY = 12 * 3600

# Symbols excluded from the universe: stablecoins / pegged instruments
# and wrapped / staked duplicates. The tag filter below catches most
# stablecoins; this list catches the rest by symbol.
STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "USDT0", "USD1",
    "TUSD", "USDD", "BUSD", "FRAX", "USDP", "GUSD", "USDe", "SUSDE",
    "USDS", "AUSD",
}
WRAPPED_SYMBOLS = {
    "WBTC", "STETH", "WSTETH", "WETH", "WEETH", "CBETH", "RETH",
    "EZETH", "WBETH", "TBTC", "CBBTC",
}
EXCLUDED_SYMBOLS = STABLECOIN_SYMBOLS | WRAPPED_SYMBOLS


class CMCError(Exception):
    """An error returned by the CoinMarketCap API (or transport)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def load_dotenv(path=None):
    """Load KEY=VALUE lines from a .env file into os.environ (only for
    keys not already set). Never prints values."""
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ------------------------------------------------------------ normalisers
# The keyless and keyed APIs return the same data in different shapes;
# these helpers accept both so the rest of the code never has to care.

def usd_quote(coin):
    """Return the USD quote dict for a coin, whether ``quote`` is a dict
    keyed by currency (keyed API) or a list of per-currency objects
    (keyless v3). Returns None when no USD quote is present."""
    quote = coin.get("quote")
    if isinstance(quote, dict):
        return quote.get("USD")
    if isinstance(quote, list):
        for entry in quote:
            if isinstance(entry, dict) and entry.get("symbol") == "USD":
                return entry
    return None


def tag_slugs(coin):
    """Return the set of tag slugs for a coin, whether tags are plain
    strings (listings) or objects with a ``slug`` field (quotes)."""
    slugs = set()
    for tag in coin.get("tags") or []:
        if isinstance(tag, str):
            slugs.add(tag.lower())
        elif isinstance(tag, dict) and tag.get("slug"):
            slugs.add(str(tag["slug"]).lower())
    return slugs


def normalise_coin(coin):
    """Flatten one listings/quotes coin object to the fields the
    screener uses."""
    quote = usd_quote(coin) or {}
    return {
        "id": coin.get("id"),
        "symbol": coin.get("symbol"),
        "name": coin.get("name"),
        "slug": coin.get("slug"),
        "rank": coin.get("cmc_rank"),
        "price": quote.get("price"),
        "change_24h": quote.get("percent_change_24h"),
        "change_7d": quote.get("percent_change_7d"),
        "market_cap": quote.get("market_cap"),
        "volume_24h": quote.get("volume_24h"),
        "dominance": quote.get("market_cap_dominance"),
        "last_updated": quote.get("last_updated") or coin.get("last_updated"),
        "tags": sorted(tag_slugs(coin)),
    }


def is_excluded(coin):
    """True for coins that must not enter the screened universe:
    stablecoins / pegged instruments (by tag or symbol), wrapped or
    staked duplicates (by symbol), fiat, or inactive coins."""
    if coin.get("is_fiat"):
        return True
    if not coin.get("is_active", True):
        return True
    symbol = (coin.get("symbol") or "").upper()
    if symbol in EXCLUDED_SYMBOLS:
        return True
    tags = tag_slugs(coin)
    if "stablecoin" in tags:
        return True
    return False


class CMCClient:
    """Small CMC API client with caching, rate limiting and retries."""

    def __init__(self, api_key=None, cache_dir=CACHE_DIR,
                 keyless_interval=1.0, keyed_interval=0.25):
        self.api_key = api_key or os.environ.get("CMC_API_KEY")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.keyless_interval = keyless_interval
        self.keyed_interval = keyed_interval
        self._last_request = 0.0
        self.calls_made = 0  # live HTTP calls (cache hits do not count)

    @property
    def has_key(self):
        return bool(self.api_key)

    # ---------------------------------------------------------- transport

    def _cache_path(self, keyed, path, params):
        raw = json.dumps({"k": keyed, "p": path, "q": params},
                         sort_keys=True)
        digest = hashlib.sha1(raw.encode()).hexdigest()[:16]
        stem = path.strip("/").replace("/", "_")
        return self.cache_dir / f"{stem}_{digest}.json"

    def _read_cache(self, cache_path, ttl):
        if ttl is None or not cache_path.exists():
            return None
        try:
            record = json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        age = time.time() - record.get("fetched_at", 0)
        if age > ttl:
            return None
        return record.get("payload")

    def _write_cache(self, cache_path, payload):
        record = {"fetched_at": time.time(), "payload": payload}
        try:
            cache_path.write_text(json.dumps(record))
        except OSError:
            pass  # caching is best-effort; a failed write is not fatal

    def _throttle(self, keyed):
        interval = self.keyed_interval if keyed else self.keyless_interval
        wait = interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, path, params, keyed, ttl):
        """GET one endpoint, with cache, throttling and retries.

        Returns the parsed ``data`` field of the CMC response.
        """
        cache_path = self._cache_path(keyed, path, params)
        cached = self._read_cache(cache_path, ttl)
        if cached is not None:
            return cached
        base = KEYED_BASE if keyed else KEYLESS_BASE
        query = urllib.parse.urlencode(params)
        url = f"{base}{path}?{query}"
        headers = {"Accept": "application/json",
                   "User-Agent": "cmc-screener/1.0"}
        if keyed:
            if not self.api_key:
                raise CMCError("this endpoint needs an API key "
                               "(set CMC_API_KEY)", code="NO_KEY")
            headers["X-CMC_PRO_API_KEY"] = self.api_key
        last_error = None
        for attempt, backoff in enumerate((0,) + RETRY_BACKOFF_SECONDS):
            if backoff:
                time.sleep(backoff)
            self._throttle(keyed)
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=30) as resp:
                    body = json.loads(resp.read().decode())
                self.calls_made += 1
            except urllib.error.HTTPError as exc:
                last_error = CMCError(f"HTTP {exc.code} from {path}",
                                      code=str(exc.code))
                if exc.code in (429, 500, 502, 503, 504) \
                        and attempt < len(RETRY_BACKOFF_SECONDS):
                    continue
                raise last_error
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = CMCError(f"request to {path} failed: {exc}")
                if attempt < len(RETRY_BACKOFF_SECONDS):
                    continue
                raise last_error
            status = body.get("status") or {}
            error_code = str(status.get("error_code", "0"))
            if error_code != "0":
                last_error = CMCError(
                    f"CMC error {error_code} on {path}: "
                    f"{status.get('error_message', '')}",
                    code=error_code)
                if error_code in RETRYABLE_CODES \
                        and attempt < len(RETRY_BACKOFF_SECONDS):
                    continue
                raise last_error
            data = body.get("data")
            self._write_cache(cache_path, data)
            return data
        raise last_error

    # ---------------------------------------------------------- endpoints

    def listings_latest(self, limit=100, start=1, convert="USD"):
        """Top coins by market cap (keyless). Returns a list of raw coin
        objects, each carrying its live USD quote."""
        params = {"start": start, "limit": limit, "convert": convert,
                  "sort": "market_cap", "cryptocurrency_type": "all"}
        return self._get("/v3/cryptocurrency/listings/latest", params,
                         keyed=False, ttl=TTL_LISTINGS)

    def quotes_latest(self, ids, convert="USD"):
        """Live quotes for the given coin ids (keyless). Returns a dict
        mapping id -> raw coin object (normalises the keyless list shape
        and the keyed dict shape)."""
        params = {"id": ",".join(str(i) for i in ids), "convert": convert}
        data = self._get("/v3/cryptocurrency/quotes/latest", params,
                         keyed=False, ttl=TTL_QUOTES)
        if isinstance(data, dict):
            return {int(k): v for k, v in data.items()}
        return {coin["id"]: coin for coin in data}

    def fear_greed_latest(self):
        """Latest Crypto Fear & Greed index value (keyless). Returns
        {"value": int, "value_classification": str, "update_time": str}."""
        return self._get("/v3/fear-and-greed/latest", {},
                         keyed=False, ttl=TTL_FEAR_GREED)

    def global_metrics(self):
        """Global market metrics (keyless): total market cap, BTC/ETH
        dominance, volumes."""
        return self._get("/v1/global-metrics/quotes/latest",
                         {"convert": "USD"}, keyed=False, ttl=TTL_GLOBAL)

    def ohlcv_historical(self, coin_id, count=400, interval="daily",
                         time_end=None):
        """Daily OHLCV candles for one coin (KEYED ONLY, Startup tier+).

        Returns a DataFrame with columns date, open, high, low, close,
        volume — one row per UTC day, oldest first. The newest candle
        may be the partial current-UTC-day candle; callers must drop it
        before computing signals (see signals.settled_frame).

        Credit cost is ceil(points / 100) per coin, so ~4 credits for
        400 daily candles.
        """
        params = {"id": coin_id, "count": count, "interval": interval}
        if time_end:
            params["time_end"] = time_end
        data = self._get("/v2/cryptocurrency/ohlcv/historical", params,
                         keyed=True, ttl=TTL_HISTORY)
        rows = []
        for candle in data.get("quotes", []):
            quote = candle.get("quote") or {}
            usd = quote.get("USD") if isinstance(quote, dict) else None
            if usd is None and isinstance(quote, list):
                usd = next((q for q in quote
                            if q.get("symbol") == "USD"), None)
            if not usd:
                continue
            day = pd.Timestamp(candle.get("time_open")).date()
            rows.append({
                "date": pd.Timestamp(day),
                "open": usd.get("open"),
                "high": usd.get("high"),
                "low": usd.get("low"),
                "close": usd.get("close"),
                "volume": usd.get("volume"),
            })
        df = pd.DataFrame(rows).sort_values("date")
        return df.drop_duplicates("date").reset_index(drop=True)

    def quotes_historical(self, coin_id, count=400, interval="daily"):
        """FALLBACK history when OHLCV is unavailable (keyed).

        Returns a close-only DataFrame (open/high/low are set equal to
        the close). WARNING: with interval=daily this endpoint returns
        the FIRST quote of each day, not the close, and it carries no
        high/low — so ATR and Donchian systems cannot run on it and
        signals.evaluate_coin marks them unknown. Prefer
        ohlcv_historical whenever the key allows it.
        """
        params = {"id": coin_id, "count": count, "interval": interval}
        data = self._get("/v3/cryptocurrency/quotes/historical", params,
                         keyed=True, ttl=TTL_HISTORY)
        rows = []
        for point in data.get("quotes", []):
            quote = point.get("quote") or {}
            usd = quote.get("USD") if isinstance(quote, dict) else None
            if usd is None and isinstance(quote, list):
                usd = next((q for q in quote
                            if q.get("symbol") == "USD"), None)
            if not usd or usd.get("price") is None:
                continue
            day = pd.Timestamp(point.get("timestamp")).date()
            rows.append({
                "date": pd.Timestamp(day),
                "open": usd["price"],
                "high": usd["price"],
                "low": usd["price"],
                "close": usd["price"],
                "volume": usd.get("volume_24h"),
            })
        df = pd.DataFrame(rows).sort_values("date")
        return df.drop_duplicates("date").reset_index(drop=True)


def last_settled_utc_day(today=None):
    """The last fully settled UTC day (yesterday in UTC). Daily signals
    always use this close, never the partial current-day candle."""
    today = today or datetime.now(timezone.utc).date()
    return today - timedelta(days=1)
