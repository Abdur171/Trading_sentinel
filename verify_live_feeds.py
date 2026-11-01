#!/usr/bin/env python3
"""
verify_live_feeds.py - CLI tool for Dual-Engine Data Pipeline Cross-Verification.
Cross-checks real-time Nordic market quotes between Avanza and Yahoo Finance,
flagging price divergences > threshold (default 0.5%) and stale quotes (> 180s).
"""

from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any

from feeds.cross_verifier import CrossVerifier
from feeds.avanza_feed import AvanzaFeed
from feeds.yfinance_feed import YFinanceFeed
from feeds.mock_feed import MockFeed


def build_mock_feeds() -> tuple[MockFeed, MockFeed]:
    """Constructs deterministic paired mock feeds for testing and offline runs."""
    avanza_prices = {
        "INVE B": 412.05,
        "SAAB B": 540.00,
        "ATCO B": 178.50,
        "SINCH": 45.20,
        "MCAP": 498.00,
        "EQT": 340.00,
        "LUMI": 160.00,
        "LINJE": 110.00,
        "^OMX": 2600.00,
    }
    yahoo_prices = {
        "INVE B": 412.00,   # 0.012% divergence
        "SAAB B": 539.50,   # 0.093% divergence
        "ATCO B": 178.60,   # 0.056% divergence
        "SINCH": 45.15,    # 0.111% divergence
        "MCAP": 498.20,    # 0.040% divergence
        "EQT": 339.80,     # 0.059% divergence
        "LUMI": 159.80,    # 0.125% divergence
        "LINJE": 110.00,   # 0.000% divergence
        "^OMX": 2600.00,   # 0.000% divergence
    }
    feed_avanza = MockFeed(avanza_prices)
    feed_yahoo = MockFeed(yahoo_prices)
    return feed_avanza, feed_yahoo


def format_staleness_str(res: Dict[str, Any]) -> str:
    """Formats human-readable staleness indicator."""
    if res["is_stale"]:
        return "STALE (>180s)"
    age_av = res.get("avanza_age_seconds")
    if age_av is not None:
        return f"LIVE ({age_av:.0f}s)"
    return "LIVE"


def run_verification(args: argparse.Namespace) -> int:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.mock:
        feed_av, feed_yf = build_mock_feeds()
        verifier = CrossVerifier(
            avanza_feed=feed_av,
            yfinance_feed=feed_yf,
            threshold=args.threshold,
        )
    else:
        verifier = CrossVerifier(
            avanza_feed=AvanzaFeed(),
            yfinance_feed=YFinanceFeed(),
            threshold=args.threshold,
        )

    results = verifier.verify_portfolio(symbols, threshold=args.threshold)

    passed_count = sum(1 for r in results if r["status"] == "PASS")
    divergent_count = sum(1 for r in results if r["is_divergent"])
    stale_count = sum(1 for r in results if r["is_stale"])
    total_count = len(results)

    if args.json:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "threshold_pct": args.threshold,
            "mode": "mock" if args.mock else "live",
            "summary": {
                "total": total_count,
                "passed": passed_count,
                "divergent": divergent_count,
                "stale": stale_count,
            },
            "results": [
                {
                    "symbol": r["symbol"],
                    "avanza_price": r["avanza_price"],
                    "yahoo_price": r["yahoo_price"],
                    "divergence_pct": r["divergence_pct"],
                    "is_divergent": r["is_divergent"],
                    "is_stale": r["is_stale"],
                    "status": r["status"],
                    "details": r["details"],
                    "avanza_age_seconds": r.get("avanza_age_seconds"),
                    "yahoo_age_seconds": r.get("yahoo_age_seconds"),
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    mode_label = "MOCK (Deterministic)" if args.mock else "LIVE (Network)"
    print("=" * 96)
    print(f"DUAL-ENGINE DATA PIPELINE CROSS-VERIFICATION REPORT [{mode_label}]")
    print(f"Threshold: <= {args.threshold:.2f}% | Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 96)
    print(f"{'Symbol':<16} {'Avanza (SEK)':>14} {'Yahoo (SEK)':>14} {'Divergence %':>14} {'Staleness':>16} {'Status':>14}")
    print("-" * 96)

    for r in results:
        p_av_str = f"{r['avanza_price']:.2f}" if r['avanza_price'] is not None else "N/A"
        p_yf_str = f"{r['yahoo_price']:.2f}" if r['yahoo_price'] is not None else "N/A"
        div_str = f"{r['divergence_pct']:.3f}%"
        stale_str = format_staleness_str(r)
        status_str = r["status"]
        print(f"{r['symbol']:<16} {p_av_str:>14} {p_yf_str:>14} {div_str:>14} {stale_str:>16} {status_str:>14}")

    print("=" * 96)
    pipeline_status = "HEALTHY" if divergent_count == 0 and stale_count == 0 else "ATTENTION_REQUIRED"
    print(f"Audit Summary: {total_count} audited | {passed_count} PASS | {divergent_count} DIVERGENT | {stale_count} STALE")
    print(f"Pipeline Health: {pipeline_status}")
    print("=" * 96)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify real-time Nordic market quotes between Avanza and Yahoo Finance."
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="INVE B,SAAB B,ATCO B,SINCH,MCAP,EQT",
        help="Comma-separated ticker symbols to cross-verify (default: INVE B,SAAB B,ATCO B,SINCH,MCAP,EQT)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Maximum acceptable divergence percentage between feeds (default: 0.5)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic mock feeds instead of making live network requests",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )

    args = parser.parse_args()
    sys.exit(run_verification(args))


if __name__ == "__main__":
    main()
