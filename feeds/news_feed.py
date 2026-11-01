"""
feeds/news_feed.py - Free Public RSS News & SEC EDGAR Filings Ingestion Engine.
Scans for market catalysts, 8-K filings, and company news for held and watchlist assets.
Filters out false positive word-ticker collisions (e.g. 'NOW') and enforces 12-hour deduplication.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import requests
import feedparser

logger = logging.getLogger(__name__)


class NewsFeed:
    """
    Parses public RSS feeds and SEC EDGAR disclosures without requiring paid API keys.
    """

    DEFAULT_RSS_FEEDS = [
        "https://finance.yahoo.com/news/rssindex",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "https://www.sec.gov/Archives/edgar/usgaap.rss.xml",
        "https://mfn.se/all/rss",
        "https://news.cision.com/regulatory-news-feed/rss",
    ]

    NORDIC_RSS_FEEDS = [
        "https://mfn.se/all/rss",
        "https://news.cision.com/regulatory-news-feed/rss",
    ]

    NORDIC_TICKER_MAP = {
        "EQT": "EQT.ST",
        "INVE": "INVE-B.ST",
        "INVESTOR": "INVE-B.ST",
        "ATCO": "ATCO-B.ST",
        "ATLAS COPCO": "ATCO-B.ST",
        "SAAB": "SAAB-B.ST",
        "MCAP": "MCAP.ST",
        "MEDCAP": "MCAP.ST",
        "SINCH": "SINCH.ST",
        "LUN": "LUN.ST",
        "LUNDIN": "LUN.ST",
        "LINJE": "LINJE",
        "LINJEMONTAGE": "LINJE",
    }

    MATERIAL_KEYWORDS = [
        "guidance", "acquisition", "offering", "downgrade", "upgrade",
        "ppa", "regulatory", "earnings", "contract", "investigation",
        "subpoena", "restructuring", "dividend", "split", "merger",
        "8-k", "10-q", "item 1.01", "item 2.02", "item 5.02",
        "delårsrapport", "kvartalsrapport", "bokslutskommuniké", "pressmeddelande"
    ]

    COMMON_WORD_TICKERS = {
        "NOW", "IT", "ON", "ALL", "AT", "BE", "DO", "GO", "IN", "IS", "ME",
        "MY", "NO", "OR", "SO", "TO", "UP", "US", "CAN", "ARE", "FOR", "GET",
        "NEW", "ONE", "OUT", "SEE", "TWO", "WAY", "WHO", "WHY"
    }

    def __init__(self, user_agent: str = "TradingSentinel/3.0 research@tradingsentinel.local"):
        self.headers = {"User-Agent": user_agent}
        self.seen_articles: Dict[str, datetime] = {}

    def fetch_news_for_ticker(self, symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Fetches the latest headlines for a specific ticker using Yahoo RSS.
        """
        clean_sym = symbol.replace(".ST", "").replace("-", "")
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={clean_sym}&region=US&lang=en-US"
        articles: List[Dict[str, Any]] = []

        try:
            resp = requests.get(url, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                parsed = feedparser.parse(resp.content)
                for entry in parsed.entries[:limit]:
                    articles.append({
                        "symbol": symbol,
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "summary": entry.get("summary", "")
                    })
        except Exception as e:
            logger.debug(f"Error fetching ticker RSS for {symbol}: {e}")

        return articles

    def scan_recent_material_news(self, tracked_symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Scans global market RSS feeds and matches against tracked symbols or material keywords.
        Strictly guards against common dictionary word ticker collisions (e.g., ServiceNow 'NOW').
        """
        matches: List[Dict[str, Any]] = []
        sym_set = {s.upper().replace(".ST", "") for s in tracked_symbols}
        now_utc = datetime.now(timezone.utc)

        # Prune articles older than 12 hours (12-hour deduplication window)
        self.seen_articles = {
            link: ts for link, ts in self.seen_articles.items()
            if (now_utc - ts).total_seconds() < 43200
        }

        for feed_url in self.DEFAULT_RSS_FEEDS:
            try:
                resp = requests.get(feed_url, headers=self.headers, timeout=8)
                if resp.status_code != 200:
                    continue

                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "").strip()
                    summary = entry.get("summary", "").strip()
                    link = entry.get("link", "").strip()

                    if not title or len(title) < 15:
                        continue

                    if link and link in self.seen_articles:
                        continue

                    text = f"{title} {summary}".lower()

                    # Check for tracked symbols
                    matched_symbol = None
                    for sym in sym_set:
                        sym_u = sym.upper()
                        if sym_u in self.COMMON_WORD_TICKERS:
                            # Strict syntax matching: require cashtag ($NOW), ticker bracket ((NOW)),
                            # exchange prefix (NYSE: NOW), or exact uppercase in headline
                            cashtag = f"${sym.lower()}"
                            paren = f"({sym.lower()})"
                            if (cashtag in text or 
                                paren in text or 
                                f"({sym_u})" in title or 
                                f" {sym_u} " in f" {title} " or 
                                f":{sym.lower()}" in text):
                                matched_symbol = sym_u
                                break
                        else:
                            if (f" {sym.lower()} " in f" {text} " or 
                                f"({sym.lower()})" in text or 
                                f"${sym.lower()}" in text or 
                                f" {sym_u} " in f" {title} "):
                                matched_symbol = sym_u
                                break

                    # Check for material keywords
                    found_keywords = [kw for kw in self.MATERIAL_KEYWORDS if kw in text]

                    # Macro keywords for systemic market news
                    is_macro = any(mkw in text for mkw in [
                        "federal reserve", "fomc", "treasury yield", "brent crude",
                        "riksbank", "interest rates", "inflation cpi"
                    ])

                    # Only alert if it directly pertains to a held/watchlist asset WITH material keywords,
                    # OR major systemic macro news WITH material keywords
                    if matched_symbol:
                        if found_keywords or any(w in title.lower() for w in ["earnings", "revenue", "profit", "quarter", "downgrade", "upgrade"]):
                            if link:
                                self.seen_articles[link] = now_utc
                            matches.append({
                                "title": title,
                                "summary": summary,
                                "link": link,
                                "matched_symbol": matched_symbol,
                                "keywords": found_keywords,
                                "published": entry.get("published", now_utc.isoformat())
                            })
                    elif is_macro and found_keywords:
                        if link:
                            self.seen_articles[link] = now_utc
                        matches.append({
                            "title": title,
                            "summary": summary,
                            "link": link,
                            "matched_symbol": "MACRO",
                            "keywords": found_keywords,
                            "published": entry.get("published", now_utc.isoformat())
                        })
            except Exception as e:
                logger.debug(f"Error scanning feed {feed_url}: {e}")

        return matches

    def fetch_sec_edgar_filings(self, symbol: Optional[str] = None, form_type: str = "8-K", limit: int = 5) -> List[Dict[str, Any]]:
        """
        Ingests free public SEC EDGAR filings (8-K, 10-Q, 10-K) without paid APIs.
        """
        filings: List[Dict[str, Any]] = []
        clean_sym = symbol.strip().upper() if symbol else ""
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK={clean_sym}&type={form_type}&company=&dateb=&owner=include&start=0&count={limit}&output=atom"
            if clean_sym else
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type={form_type}&company=&dateb=&owner=include&start=0&count={limit}&output=atom"
        )

        try:
            resp = requests.get(url, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                parsed = feedparser.parse(resp.content)
                for entry in parsed.entries[:limit]:
                    filings.append({
                        "symbol": clean_sym or "SEC",
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "summary": entry.get("summary", ""),
                        "form_type": form_type
                    })
        except Exception as e:
            logger.debug(f"Error fetching SEC EDGAR filings: {e}")

        return filings

    def fetch_nordic_news(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Ingests real-time regulatory releases from Nordic newswires (MFN.se and Cision).
        Matches against held Nordic base tickers (EQT, INVE, ATCO, SAAB, MCAP, SINCH, LUN, LINJE).
        """
        nordic_articles: List[Dict[str, Any]] = []
        for feed_url in self.NORDIC_RSS_FEEDS:
            try:
                resp = requests.get(feed_url, headers=self.headers, timeout=8)
                if resp.status_code == 200:
                    parsed = feedparser.parse(resp.content)
                    for entry in parsed.entries[:limit]:
                        title = entry.get("title", "")
                        summary = entry.get("summary", "")
                        text = f"{title} {summary}".upper()
                        matched_ticker = None
                        for base_name, full_sym in self.NORDIC_TICKER_MAP.items():
                            if base_name in text:
                                matched_ticker = full_sym
                                break
                        nordic_articles.append({
                            "title": title,
                            "summary": summary,
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "matched_symbol": matched_ticker or "NORDIC",
                            "source": "MFN.se" if "mfn" in feed_url else "Cision",
                        })
            except Exception as e:
                logger.debug(f"Error fetching Nordic news from {feed_url}: {e}")

        return nordic_articles
