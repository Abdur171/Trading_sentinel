"""
workers/analyst_worker.py - On-Demand Deep-Dive Quantitative Analyst Worker.
Executes the Ten Quantitative Gates and DCF Margin of Safety evaluations.
"""

from __future__ import annotations
import logging
from typing import Dict, Any, Optional
from core.models import DeepDiveAnalysis
from core.gate_auditor import GateAuditor
from feeds.base_feed import BaseFeed

logger = logging.getLogger(__name__)


class AnalystWorker:
    """
    On-demand background worker that evaluates prospective candidates or active holdings
    against the Ten Quantitative Gates and formats executive memorandum report cards.
    """

    def __init__(self, gate_auditor: GateAuditor, feed: BaseFeed):
        self.auditor = gate_auditor
        self.feed = feed

    def analyze_ticker(self, symbol: str) -> DeepDiveAnalysis:
        """
        Runs the full 10-Gate quantitative audit on a symbol.
        """
        clean_sym = symbol.strip().upper()
        # Fetch quote
        quote = self.feed.get_quote(clean_sym)
        current_price = quote.price if quote else 100.0
        currency = quote.currency if quote else "USD"

        # Fetch fundamental metrics
        financials = self.feed.get_financials(clean_sym)
        company_name = financials.get("shortName") or clean_sym

        # Run Ten Gates
        analysis = self.auditor.run_full_ten_gates(
            symbol=clean_sym,
            company_name=company_name,
            current_price=current_price,
            currency=currency,
            financials=financials
        )
        return analysis

    def format_memo_card(self, analysis: DeepDiveAnalysis) -> str:
        """
        Formats DeepDiveAnalysis into a Telegram Markdown Investment Memorandum card.
        """
        verdict_badge = "🟢 **APPROVED FOR ACCUMULATION**" if analysis.overall_passed else "🔴 **REJECTED / WATCHLIST ONLY**"
        
        gate_lines = []
        for g in analysis.gate_results:
            icon = "✅" if g.passed else "❌"
            gate_lines.append(f"• {icon} **Gate {g.gate_number} ({g.gate_name}):** {g.details}")

        gates_str = "\n".join(gate_lines)

        card = (
            f"📑 **QUANTITATIVE INVESTMENT MEMORANDUM**\n"
            f"**Target:** `{analysis.symbol}` ({analysis.company_name})\n"
            f"**Status:** {verdict_badge}\n\n"
            f"💵 **Valuation & Safety:**\n"
            f"• Market Price: `{analysis.market_price:.2f} {analysis.currency}`\n"
            f"• Intrinsic DCF: `{analysis.intrinsic_value:.2f} {analysis.currency}`\n"
            f"• Margin of Safety: `{analysis.margin_of_safety_pct:+.1f}%` (Min Req: 15.0%)\n"
            f"• Hurdle Rate (r_e): `{analysis.cost_of_equity_hurdle:.2f}%`\n\n"
            f"🛡️ **Risk & Routing (Gates 4 & 5):**\n"
            f"• Max Gate 4 Size: `{analysis.max_position_points_gate4:.2f} points` (Risk <= 3.48 pts)\n"
            f"• Annual Volatility: `{analysis.current_volatility_pct:.1f}%`\n"
            f"• Optimal Wrapper: `{analysis.recommended_wrapper}`\n\n"
            f"⚖️ **Ten Quantitative Gate Audits (1-10):**\n"
            f"{gates_str}\n\n"
            f"💡 **Executive Summary:**\n"
            f"{analysis.summary}"
        )
        return card
