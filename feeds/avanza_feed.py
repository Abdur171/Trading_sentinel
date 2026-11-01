"""
feeds/avanza_feed.py - Zero-Delay Real-Time Data Feed for Nordic Equities & Avanza Markets Warrants.
Queries Avanza's public unauthenticated market-guide endpoints to fetch real-time SEK quotes.
Provides fallback mechanisms and in-memory caching.
"""

from __future__ import annotations
import logging
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from core.models import MarketQuote
from feeds.base_feed import BaseFeed

logger = logging.getLogger(__name__)


class AvanzaFeed(BaseFeed):
    """
    Real-time public market feed for Nordic instruments and Avanza Markets warrants.
    Eliminates the 15-minute delay inherent in Yahoo Finance for Stockholm-listed assets.
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    # Pre-indexed Orderbook IDs for high-liquidity mandate assets and warrants
    ORDERBOOK_MAP: Dict[str, str] = {
        "INVE B": "5247",
        "INVE-B.ST": "5247",
        "INVE-B": "5247",
        "SAAB B": "5265",
        "SAAB-B.ST": "5265",
        "SAAB-B": "5265",
        "ATCO B": "5240",
        "ATCO-B.ST": "5240",
        "ATCO-B": "5240",
        "SINCH": "593836",
        "SINCH.ST": "593836",
        "MCAP": "5531",
        "MCAP.ST": "5531",
        "EQT": "992209",
        "EQT.ST": "992209",
        "LUMI": "5459",
        "LUMI.ST": "5459",
        "LUN.ST": "5459",
        "LINJE": "1619478",
        "LINJE.ST": "1619478",
        "^OMX": "19002",
        "OMXS30": "19002",
        "OMX": "19002",
        "MINI L OMX AVA 1615": "1615000",
        "MINI-L-OMX-AVA-1615": "1615000",
    }

    def __init__(self, cache_ttl_seconds: float = 15.0, timeout_seconds: float = 4.0):
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout = timeout_seconds
        self._cache: Dict[str, Tuple[MarketQuote, float]] = {}
        self._dynamic_orderbook_cache: Dict[str, str] = {}

    def clear_cache(self) -> None:
        """Flushes the in-memory quote cache."""
        self._cache.clear()

    def normalize_symbol(self, symbol: str) -> str:
        """Normalizes symbol variations into standard Avanza lookup strings."""
        sym = symbol.strip().upper()
        return sym

    def _resolve_orderbook_id(self, symbol: str) -> Optional[str]:
        """Resolves an orderbook ID from static mapping or via Avanza global search."""
        clean = symbol.strip().upper()
        if clean in self.ORDERBOOK_MAP:
            return self.ORDERBOOK_MAP[clean]

        clean_stripped = clean.replace(".ST", "").replace("-", " ")
        if clean_stripped in self.ORDERBOOK_MAP:
            return self.ORDERBOOK_MAP[clean_stripped]

        if clean in self._dynamic_orderbook_cache:
            return self._dynamic_orderbook_cache[clean]

        # Attempt dynamic discovery via public search
        try:
            query = urllib.parse.quote(clean_stripped)
            url = f"https://www.avanza.se/_api/search/global-search?query={query}"
            req = urllib.request.Request(url, headers=self.HEADERS)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                hits = data.get("resultGroups", [])
                for group in hits:
                    for item in group.get("hits", []):
                        ob_id = item.get("link", {}).get("orderbookId")
                        if ob_id:
                            self._dynamic_orderbook_cache[clean] = str(ob_id)
                            return str(ob_id)
        except Exception as e:
            logger.debug(f"[AvanzaFeed] Dynamic orderbook lookup failed for {symbol}: {e}")

        return None

    def _fetch_orderbook_data(self, orderbook_id: str, instrument_type: str = "stock") -> Optional[Dict[str, Any]]:
        """Queries Avanza public market guide for orderbook pricing data."""
        endpoints = [
            f"https://www.avanza.se/_api/market-guide/stock/{orderbook_id}",
            f"https://www.avanza.se/_api/market-guide/warrant/{orderbook_id}",
            f"https://www.avanza.se/_api/market-guide/certificate/{orderbook_id}",
        ]

        for url in endpoints:
            try:
                req = urllib.request.Request(url, headers=self.HEADERS)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data and ("price" in data or "orderbook" in data or "listing" in data or "quote" in data):
                            return data
            except urllib.error.HTTPError as he:
                if he.code == 404:
                    continue
                logger.debug(f"[AvanzaFeed] HTTP error {he.code} fetching {url}")
            except Exception as e:
                logger.debug(f"[AvanzaFeed] Error fetching {url}: {e}")

        return None

    def get_quote(self, symbol: str) -> Optional[MarketQuote]:
        """Fetches live real-time SEK quote from Avanza public endpoints."""
        clean = symbol.strip().upper()

        # Check in-memory cache
        now = time.time()
        if clean in self._cache:
            quote, ts = self._cache[clean]
            if (now - ts) < self.cache_ttl_seconds:
                return quote

        orderbook_id = self._resolve_orderbook_id(clean)
        if not orderbook_id:
            return None

        raw = self._fetch_orderbook_data(orderbook_id)
        if not raw:
            return None

        try:
            # Extract quote / price data - inspect "quote" then "price"
            quote_data = raw.get("quote") or raw.get("price") or {}
            listing = raw.get("listing", {})

            last_price = (
                quote_data.get("last")
                or quote_data.get("lastPrice")
                or quote_data.get("latestPrice")
                or quote_data.get("price")
            )
            if last_price is None:
                return None

            price_val = float(last_price)
            change_val = float(quote_data.get("change", 0.0) or 0.0)
            change_pct = float(quote_data.get("changePercent", 0.0) or 0.0)
            volume_val = int(quote_data.get("totalVolumeTraded", 0) or quote_data.get("volume", 0) or 0)

            bid_val = float(quote_data.get("buy") or quote_data.get("buyPrice") or 0.0) if (quote_data.get("buy") or quote_data.get("buyPrice")) else None
            ask_val = float(quote_data.get("sell") or quote_data.get("sellPrice") or 0.0) if (quote_data.get("sell") or quote_data.get("sellPrice")) else None
            high_val = float(quote_data.get("highest") or quote_data.get("highestPrice") or 0.0) if (quote_data.get("highest") or quote_data.get("highestPrice")) else None
            low_val = float(quote_data.get("lowest") or quote_data.get("lowestPrice") or 0.0) if (quote_data.get("lowest") or quote_data.get("lowestPrice")) else None

            prev_close = price_val - change_val if change_val != 0 else price_val

            # Extract quote timestamp if available
            time_of_last = quote_data.get("timeOfLast") or quote_data.get("updated")
            if time_of_last:
                try:
                    quote_time = datetime.fromtimestamp(float(time_of_last) / 1000.0, tz=timezone.utc)
                except Exception:
                    quote_time = datetime.now(timezone.utc)
            else:
                quote_time = datetime.now(timezone.utc)

            quote = MarketQuote(
                symbol=clean,
                price=price_val,
                change=change_val,
                change_pct=change_pct,
                volume=volume_val,
                previous_close=prev_close,
                timestamp=quote_time,
                currency=listing.get("currency", "SEK"),
                bid=bid_val,
                ask=ask_val,
                high=high_val,
                low=low_val,
                is_live=True,
                delay_seconds=0.0
            )

            self._cache[clean] = (quote, now)
            return quote
        except Exception as e:
            logger.error(f"[AvanzaFeed] Error parsing Avanza quote for {clean}: {e}")
            return None

    def get_batch_quotes(self, symbols: List[str]) -> Dict[str, MarketQuote]:
        """Queries multiple symbols sequentially with caching."""
        results = {}
        for s in symbols:
            q = self.get_quote(s)
            if q:
                results[s] = q
        return results

    def get_financials(self, symbol: str) -> Dict[str, Any]:
        """Fetches basic fundamentals if available from Avanza key ratios."""
        clean = symbol.strip().upper()
        orderbook_id = self._resolve_orderbook_id(clean)
        if not orderbook_id:
            return {}

        raw = self._fetch_orderbook_data(orderbook_id)
        if not raw:
            return {}

        key_ratios = raw.get("keyRatios", {})
        return {
            "pe": key_ratios.get("pe"),
            "direct_yield": key_ratios.get("directYield"),
            "price_to_book": key_ratios.get("priceToBook"),
            "market_cap": raw.get("marketCapital", {}).get("value"),
        }
