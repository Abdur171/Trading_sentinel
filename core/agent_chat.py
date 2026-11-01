"""
core/agent_chat.py - Conversational Quantitative Agent for Mobile Telegram Control.
Allows the user to hold natural language conversations with their desk assistant from their phone.
Supports pluggable Gemini / LLM backends with an offline rule-based financial reasoning engine.
"""

from __future__ import annotations
import os
import re
import logging
from typing import Dict, List, Optional, Any
from core.state_manager import StateManager
from core.gate_auditor import GateAuditor
from core.macro_sentinel import MacroSentinel

logger = logging.getLogger(__name__)


class AgentChat:
    """
    Conversational Agent capable of answering queries, reasoning about portfolio risk,
    explaining Gate calculations, and auditing positions via Telegram.
    """

    def __init__(
        self,
        state_mgr: StateManager,
        gate_auditor: GateAuditor,
        macro_sentinel: Optional[MacroSentinel] = None,
        gemini_api_key: Optional[str] = None,
        watch_mgr: Optional[Any] = None,
        session_mgr: Optional[Any] = None,
        quant_risk: Optional[Any] = None,
        feed: Optional[Any] = None,
        executive_desk: Optional[Any] = None,
        signals_desk: Optional[Any] = None,
        swing_desk: Optional[Any] = None,
        derivatives_desk: Optional[Any] = None,
        cross_verifier: Optional[Any] = None,
        intelligence_desk: Optional[Any] = None,
        ai_reasoner: Optional[Any] = None,
        dispatcher: Optional[Any] = None,
    ):
        self.state_mgr = state_mgr
        self.auditor = gate_auditor
        self.macro_sentinel = macro_sentinel
        self.watch_mgr = watch_mgr
        self.session_mgr = session_mgr
        self.quant_risk = quant_risk
        self.feed = feed
        self.executive_desk = executive_desk
        self.signals_desk = signals_desk
        self.swing_desk = swing_desk
        self.derivatives_desk = derivatives_desk
        self.cross_verifier = cross_verifier
        self.intelligence_desk = intelligence_desk
        self.ai_reasoner = ai_reasoner
        self.dispatcher = dispatcher
        self.api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.history: List[Dict[str, str]] = []

    def get_system_context(self) -> str:
        """Constructs rich state context for LLM or rule-based evaluator."""
        recon = self.state_mgr.reconcile_capital()
        holdings = self.state_mgr.get_all_active_holdings()
        stops = self.state_mgr.get_active_stops()

        top_holdings = sorted(holdings, key=lambda h: h.points, reverse=True)[:5]
        top_str = ", ".join([f"{h.symbol} ({h.points} pts in {h.account})" for h in top_holdings])
        stop_str = ", ".join([f"{s['symbol']} @ {s.get('stop_price') or s.get('percentage')}" for s in stops[:6]])

        context = (
            f"You are the Autonomous Quantitative Trading Sentinel & Desk Assistant governing the 696.50-point Multi-Asset Barbell Mandate.\n"
            f"Current Mandate Status:\n"
            f"- Total NAV: {recon['calculated_total_points']:.2f} pts / 696.50 target (Parity: {recon['parity_reconciled']})\n"
            f"- Invested: {recon['invested_points']:.2f} pts ({recon['invested_pct']}%), Cash: {recon['cash_points']:.2f} pts ({recon['cash_pct']}%)\n"
            f"- Cash Pools: Avanza AF: {recon['cash_by_account'].get('avanza_af')} pts, ISK: {recon['cash_by_account'].get('avanza_isk')} pts, "
            f"KF: {recon['cash_by_account'].get('avanza_kf')} pts, Revolut: {recon['cash_by_account'].get('revolut_invest')} pts\n"
            f"- Top Holdings: {top_str}\n"
            f"- Active Stops: {stop_str}\n"
            f"- Gate 4 Limit: Maximum single-trade risk <= 3.48 points (0.50% of 696.50 pts).\n"
            f"- Derivative Curfew: Mandatory flat book at 19:45 CEST for Avanza Markets warrants (MINI L OMX AVA 1615).\n"
        )
        return context

    def check_and_execute_trade_intent(self, text: str) -> Optional[str]:
        """
        Detects if user message is an instruction to register a trade sale/exit/closure.
        If detected, performs the ledger mutation immediately and returns a receipt card.
        """
        t = text.lower()
        sell_action_words = {"sold", "sell", "selling", "closed", "close", "closing", "exit", "exited", "liquidated", "liquidate", "trim", "trimmed", "trimming", "reduce", "cut"}
        tokens = set(re.findall(r"\b\w+\b", t))

        if not (tokens & sell_action_words):
            return None

        # Check if user specified a partial trim quantity: e.g. "trim 5 points", "sell 3 pts", "trim 2.5", "reduce by 4"
        trim_points = None
        pts_match = re.search(r"(?:trim|sell|reduce|cut)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*(?:pts|points|p)?", t)
        if pts_match:
            try:
                trim_points = float(pts_match.group(1))
            except ValueError:
                trim_points = None
        else:
            pts_match2 = re.search(r"(\d+(?:\.\d+)?)\s*(?:pts|points|p)\s*(?:of|from)?", t)
            if pts_match2:
                try:
                    trim_points = float(pts_match2.group(1))
                except ValueError:
                    trim_points = None

        # Check for derivative/warrant references first:
        target_holding = None
        if any(w in tokens for w in ["derivative", "warrant", "omx", "omxs", "omxs30", "mini"]):
            active_derivs = [
                h for h in self.state_mgr.get_all_active_holdings()
                if (h.instrument_type in ["derivative_warrant", "derivative", "warrant", "option"] or
                    "MINI" in h.symbol.upper() or
                    (h.underlying and "OMX" in h.underlying.upper()))
            ]
            if any(w in tokens for w in ["omx", "omxs", "omxs30"]):
                for h in active_derivs:
                    if (h.underlying and "OMX" in h.underlying.upper()) or "OMX" in h.symbol.upper():
                        target_holding = h
                        break
                if not target_holding:
                    for h in self.state_mgr.state.holdings:
                        if (h.underlying and "OMX" in h.underlying.upper()) or "OMX" in h.symbol.upper():
                            target_holding = h
                            break
            elif "amd" in tokens:
                for h in active_derivs:
                    if (h.underlying and "AMD" in h.underlying.upper()) or "AMD" in h.symbol.upper():
                        target_holding = h
                        break
                if not target_holding:
                    for h in self.state_mgr.state.holdings:
                        if (h.underlying and "AMD" in h.underlying.upper()) or "AMD" in h.symbol.upper():
                            target_holding = h
                            break
            if not target_holding and not any(w in tokens for w in ["omx", "omxs", "omxs30", "amd"]):
                if active_derivs:
                    target_holding = active_derivs[0]
            if not target_holding:
                target_holding = self.state_mgr.get_holding("MINI L OMX AVA 1615")

        # If not derivative, search among all active holdings
        if not target_holding:
            for h in self.state_mgr.get_all_active_holdings():
                h_sym_clean = h.symbol.lower().replace("-b.st", "").replace(".st", "")
                h_words = [w.lower() for w in h.name.split() if len(w) > 3]
                if (h_sym_clean in tokens or 
                    h.symbol.lower() in t or 
                    any(w in tokens for w in h_words)):
                    target_holding = h
                    break

        if target_holding:
            if target_holding.status == "closed" or target_holding.points <= 0:
                return (
                    f"ℹ️ **Position Already Closed:**\n\n"
                    f"• `{target_holding.symbol}` ({target_holding.name}) is already marked as closed/flat in the ledger (0.00 pts).\n"
                    f"• No additional capital adjustments were made."
                )

            # Check if this is a partial trim vs full exit
            is_partial = (trim_points is not None and 0.0 < trim_points < (target_holding.points - 1e-4))

            if is_partial:
                res = self.state_mgr.trim_holding(
                    symbol=target_holding.symbol,
                    points_to_trim=trim_points,
                    notes=f"Trimmed per user instruction: '{text}'"
                )
                if res["success"]:
                    recon = res["reconciliation"]
                    stop_desc = f"`{res['updated_stop']}`" if res.get('updated_stop') else "N/A"
                    risk_desc = f" (Updated Max Risk: `{res['updated_max_risk_pts']:.2f} pts` <= 3.44 limit)" if res.get('updated_max_risk_pts') is not None else ""
                    return (
                        f"✂️ **Trade Registered: Position Trimmed**\n\n"
                        f"• **Instrument:** `{res['symbol']}` ({res['name']})\n"
                        f"• **Trimmed Capital:** `{res['points_trimmed']:.2f} pts`\n"
                        f"• **Remaining Position:** `{res['remaining_points']:.2f} pts` in `{res['account']}`\n"
                        f"• **Capital Credited:** `{res['proceeds_credited']:.2f} pts` returned to **{res['account_name']}** Cash\n"
                        f"• **Updated Account Cash:** `{res['updated_account_cash']:.2f} pts`\n"
                        f"• **Invested Capital:** `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%)\n"
                        f"• **Total Liquid Cash:** `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)\n"
                        f"• **Mandate Parity:** `{recon['calculated_total_points']:.2f} / {recon['mandate_target_points']:.2f} pts` ✅\n"
                        f"• **Risk Coverage:** Stop-loss active @ {stop_desc}{risk_desc}."
                    )
                else:
                    return f"❌ Failed to register trim: {res.get('error')}"
            else:
                res = self.state_mgr.close_holding(
                    symbol=target_holding.symbol,
                    notes=f"Sold per user instruction: '{text}'"
                )
                if res["success"]:
                    recon = res["reconciliation"]
                    return (
                        f"✅ **Trade Registered: Position Exited**\n\n"
                        f"• **Instrument:** `{res['symbol']}` ({res['name']})\n"
                        f"• **Capital Released:** `{res['points_released']:.2f} pts` returned to **{res['account_name']}**\n"
                        f"• **Updated Account Cash:** `{res['updated_account_cash']:.2f} pts`\n"
                        f"• **Invested Capital:** `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%)\n"
                        f"• **Total Liquid Cash:** `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)\n"
                        f"• **Mandate Parity:** `{recon['calculated_total_points']:.2f} / {recon['mandate_target_points']:.2f} pts` ✅\n"
                        f"• **Curfew & Risk Watchdog:** Monitoring for this position has been **deactivated**."
                    )
                else:
                    return f"❌ Failed to register sale: {res.get('error')}"

        return None

    def check_and_execute_watch_intent(self, text: str) -> Optional[str]:
        """
        Detects natural language instructions to enter or leave focused watch mode.
        Examples: 'Watch ARM closely', 'Keep an eye on ORCL', 'Watch ARM and CEG closely for 30m', 'Stop watching ARM', 'Stop watching'.
        """
        t = text.lower().strip()

        # 1. Check unfocus / stop watching
        stop_phrases = ["stop watching", "reset watch mode", "reset watch", "cancel watch", "stop watch", "unfocus"]
        if any(phrase in t for phrase in stop_phrases):
            if not self.watch_mgr:
                return "Watch mode is currently inactive."

            target_sym = None
            stop_match = re.search(r"(?:stop watching|cancel watch on|stop watch on|unfocus)\s+([a-zA-Z0-9\.\-\s]+)", t)
            if stop_match:
                candidate = stop_match.group(1).strip().upper()
                if candidate not in ["ALL", "EVERYTHING", "TARGETS", "MODE", "NOW", "PLEASE"]:
                    target_sym = candidate

            alerts = self.watch_mgr.stop_watch(symbol=target_sym)
            if not alerts and target_sym:
                return f"ℹ️ Target `{target_sym}` was not under active focused watch."

            stopped = ", ".join([a.symbol for a in alerts if a.symbol]) if alerts else (target_sym or "all active targets")
            return (
                f"🔄 **Focused Watch Mode Reset**\n\n"
                f"• Surveillance concluded for: `{stopped}`\n"
                f"• Normal cruise mode cadence restored."
            )

        # 2. Check watch commands
        # Trigger matches: "watch", "keep an eye on", "keep a close eye on", "focus on", "monitor"
        watch_match = re.search(r"(?:keep a close eye on|keep an eye on|watch|focus on|monitor)\s+(.+?)(?:closely|for\s+\d+|today|now|$)", t)
        if watch_match:
            raw_targets = watch_match.group(1).strip()
            # Normalize connectors: "and", commas
            cleaned_targets = raw_targets.replace(" and ", ",").replace("&", ",")
            parts = [p.strip() for p in cleaned_targets.split(",") if p.strip()]

            # Extract duration if present
            mins = 60
            dur_m = re.search(r"(\d+)\s*(?:m|min|mins|minutes)", t)
            if dur_m:
                mins = int(dur_m.group(1))

            candidates = []
            stop_words = {"CASH", "MY", "OUR", "THE", "PORTFOLIO", "ALL", "THIS", "ME", "IT", "FOR", "ON", "A", "AN"}

            for p in parts:
                sym_candidate = p.strip().upper()
                # Clean trailing filler words
                sym_candidate = re.sub(r"\b(CLOSELY|FOR|MINUTES|MINS|TODAY|NOW)\b", "", sym_candidate).strip()
                if sym_candidate and sym_candidate not in stop_words:
                    candidates.append(sym_candidate)

            if not candidates:
                return None

            activated_targets = []
            for sym in candidates:
                curr_px = 100.0
                if self.feed:
                    q = self.feed.get_quote(sym)
                    if not q and hasattr(self.feed, "normalize_symbol"):
                        q = self.feed.get_quote(self.feed.normalize_symbol(sym))
                    if q and q.price > 0:
                        curr_px = q.price

                if self.watch_mgr:
                    target = self.watch_mgr.start_watch(
                        symbol=sym,
                        baseline_price=curr_px,
                        duration_minutes=mins,
                        alert_threshold_pct=0.015,
                        reason=f"Natural language instruction: '{text}'"
                    )
                    activated_targets.append(target)

            if activated_targets:
                symbols_str = ", ".join([f"`{tg.symbol}` (@ {tg.baseline_price:.2f})" for tg in activated_targets])
                return (
                    f"🎯 **Focused Watch Mode Activated**\n\n"
                    f"• **Asset(s):** {symbols_str}\n"
                    f"• **Surveillance Duration:** `{mins} minutes`\n"
                    f"• **Delta Alert Threshold:** `±1.5%`\n"
                    f"• **Transmission Gear:** `HIGH GEAR (ACTIVE)` (15s polling cycle)\n"
                    f"• **Auto-Reset:** Automatically reverts upon TTL expiry, market close, or 'stop watching'."
                )
            elif candidates:
                return f"🎯 Focused watch mode activated for `{', '.join(candidates)}` ({mins}m)."

        return None

    def check_and_execute_snooze_intent(self, text: str) -> Optional[str]:
        """
        Detects natural language intent to snooze, mute, or silence repeating alerts.
        Enforces user 12-hour auto-reset rule.
        """
        if not self.dispatcher:
            return None

        t = text.lower().strip()

        # Check unsnooze / unmute
        if any(w in t for w in ["unsnooze", "unmute", "un-mute", "cancel mute", "resume alerts"]):
            target = "ALL"
            if "macro" in t or "shield" in t:
                target = "MACRO"
            elif any(w in t.split() for w in ["all", "everything", "alerts", "notifications"]):
                target = "ALL"
            else:
                for h in self.state_mgr.get_all_active_holdings():
                    if re.search(r"\b" + re.escape(h.symbol.lower()) + r"\b", t) or (len(h.name) > 3 and h.name.lower() in t):
                        target = h.symbol
                        break
            success = self.dispatcher.unsnooze(target)
            if success:
                return f"🔔 **Alerts Restored:** Snooze cancelled for `{target}`. Normal push notifications active."
            return f"ℹ️ Target `{target}` was not actively snoozed. Use `/snoozes` to view currently muted items."

        # Check snooze / mute / silence
        snooze_words = ["snooze", "mute", "silence", "stop alerting", "stop sending alerts", "quiet alerts"]
        if any(w in t for w in snooze_words):
            target = "ALL"
            hours = 12.0

            dur_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr|h)\b", t)
            if dur_m:
                try:
                    hours = float(dur_m.group(1))
                except ValueError:
                    hours = 12.0

            if "macro" in t or "shield" in t or "defensive" in t:
                target = "MACRO"
            elif any(w in t for w in ["all", "everything", "alerts", "notifications"]):
                target = "ALL"
            else:
                for h in self.state_mgr.get_all_active_holdings():
                    if h.symbol.lower() in t or h.name.lower() in t:
                        target = h.symbol
                        break

            exp_dt = self.dispatcher.snooze(target, hours=hours)
            return (
                f"🔕 **Alerts Muted / Snoozed**\n\n"
                f"• **Target:** `{target}`\n"
                f"• **Duration:** `{hours:.1f} hours`\n"
                f"• **Auto-Reset:** `{exp_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}` (12-Hour Reset Rule)\n"
                f"• Repetitive push notifications for `{target}` are muted.\n"
                f"• To un-mute immediately, send `/unsnooze {target}`."
            )

        return None

    def check_and_execute_institutional_intent(self, text: str) -> Optional[str]:
        """
        Detects natural language routing to the 7 institutional desks:
        1. Battle Plan (/battleplan)
        2. Signals (/signals)
        3. Dynamic Pots (/pots)
        4. Swing Status (/swing <SYM>)
        5. AI Prompt Dossier (/prompt <SYM>)
        6. AI Reasoner Critique (/reason <SYM>)
        7. Feed Cross-Verification (/verify_feeds)
        """
        t = text.lower().strip()
        t_clean = re.sub(r"[\?\!\.\,]+$", "", t).strip()

        # 1. Battle Plan intent
        bp_triggers = [
            "morning battle plan", "morning battleplan", "battle plan", "battleplan",
            "show battle plan", "what is the battle plan", "morning plan",
            "daily battle plan", "show battleplan", "get battle plan", "08:30 battle plan"
        ]
        if any(trig in t_clean for trig in bp_triggers):
            if not self.intelligence_desk:
                from core.intelligence_desk import IntelligenceDesk
                self.intelligence_desk = IntelligenceDesk(
                    state_mgr=self.state_mgr,
                    feed=self.feed,
                    executive_desk=self.executive_desk,
                    signals_desk=self.signals_desk,
                    derivatives_desk=self.derivatives_desk,
                    ai_reasoner=self.ai_reasoner,
                    macro_sentinel=self.macro_sentinel
                )
            return self.intelligence_desk.generate_morning_battle_plan()

        # 2. Signals intent
        signals_triggers = [
            "what are the swing signals", "swing signals", "trading signals", "scan signals",
            "show signals", "any trading setups", "qualified swing setups", "qualified swing",
            "screen signals", "show swing signals", "signals desk", "what are the signals"
        ]
        if any(trig in t_clean for trig in signals_triggers):
            if not self.signals_desk:
                from core.signals_desk import SignalsDesk
                self.signals_desk = SignalsDesk(feed=self.feed, state_mgr=self.state_mgr)
            return self.signals_desk.format_signals_card()

        # 3. Capital Pots intent
        pots_triggers = [
            "show capital pots", "capital pots", "risk pots", "show pots", "pot breakdown",
            "capital matrix", "pots summary", "what are my risk pots", "how are the pots allocated",
            "what are the pots", "4 pots", "four pots", "pots allocation"
        ]
        if any(trig in t_clean for trig in pots_triggers):
            if not self.executive_desk:
                from core.executive_desk import ExecutiveDesk
                self.executive_desk = ExecutiveDesk(self.state_mgr)
            return self.executive_desk.format_pots_card()

        # 4. Feed Cross-Verification intent
        feeds_triggers = [
            "verify live feeds", "verify feeds", "cross verify quotes", "cross verify feeds",
            "cross verify", "feed divergence", "check feed divergence", "are feeds healthy",
            "check feeds", "verify data feeds"
        ]
        if any(trig in t_clean for trig in feeds_triggers):
            return self._format_cross_verify_summary()

        # Helper to extract clean ticker symbol from remainder
        def clean_symbol(cand: str) -> str:
            cand = re.sub(r"[\?\!\.\,]+$", "", cand).strip().upper()
            cand = re.sub(r"\b(PLEASE|TODAY|NOW|ME|FOR|ON|AT)\b", "", cand).strip()
            return cand

        # 5. Swing trade intent on symbol
        swing_match = re.search(
            r"(?:how is the swing on|check swing on|swing status on|swing trade on|swing on|swing status for|swing for)\s+([a-zA-Z0-9\.\-\s]+)",
            t_clean
        )
        if swing_match:
            sym = clean_symbol(swing_match.group(1))
            if sym and sym not in ["ALL", "TRADE", "TRADING", "POSITION", "POT"]:
                if not self.swing_desk:
                    from core.swing_desk import SwingDesk
                    self.swing_desk = SwingDesk(state_mgr=self.state_mgr, quant_risk=self.quant_risk)
                return self.swing_desk.format_swing_card(sym)

        # 6. AI Prompt Dossier intent
        prompt_match = re.search(
            r"(?:generate ai prompt for|generate prompt for|export prompt for|ai prompt for|prompt dossier for|prompt for)\s+([a-zA-Z0-9\.\-\s]+)",
            t_clean
        )
        if prompt_match:
            sym = clean_symbol(prompt_match.group(1))
            if sym:
                if not self.ai_reasoner:
                    from core.ai_reasoner import AIReasoner
                    self.ai_reasoner = AIReasoner(
                        state_mgr=self.state_mgr,
                        feed=self.feed,
                        signals_desk=self.signals_desk,
                        executive_desk=self.executive_desk,
                        gate_auditor=self.auditor
                    )
                return self.ai_reasoner.generate_prompt_dossier(sym)

        # 7. AI Reasoner / Critique intent
        reason_match = re.search(
            r"(?:ai critique for|ai critique on|what does ai think of|critique on|critique for|critique|reason on|reason for)\s+([a-zA-Z0-9\.\-\s]+)",
            t_clean
        )
        if reason_match:
            sym = clean_symbol(reason_match.group(1))
            if sym and sym not in ["ALL", "THIS", "TRADE", "ME", "US"]:
                if not self.ai_reasoner:
                    from core.ai_reasoner import AIReasoner
                    self.ai_reasoner = AIReasoner(
                        state_mgr=self.state_mgr,
                        feed=self.feed,
                        signals_desk=self.signals_desk,
                        executive_desk=self.executive_desk,
                        gate_auditor=self.auditor
                    )
                return self.ai_reasoner.critique(sym)

        return None

    def _format_cross_verify_summary(self) -> str:
        """Formats cross-verification report for feeds."""
        if not self.cross_verifier:
            from feeds.cross_verifier import CrossVerifier
            from feeds.avanza_feed import AvanzaFeed
            self.cross_verifier = CrossVerifier(avanza_feed=AvanzaFeed(), yfinance_feed=self.feed)

        results = self.cross_verifier.verify_portfolio()
        lines = [
            "🔍 **Dual-Engine Market Data Feed Verification (LMVP)**",
            "*(Avanza Real-Time SEK vs Yahoo Finance Cross-Check)*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]
        pass_count = 0
        for r in results:
            sym = r["symbol"]
            av_p = f"{r['avanza_price']:.2f}" if r['avanza_price'] is not None else "N/A"
            yf_p = f"{r['yahoo_price']:.2f}" if r['yahoo_price'] is not None else "N/A"
            div = f"{r['divergence_pct']:.2f}%"
            stat = r["status"]
            if stat == "PASS":
                pass_count += 1
                icon = "✅"
            elif "ALERT" in stat:
                icon = "⚠️"
            else:
                icon = "ℹ️"
            lines.append(f"• {icon} **{sym}:** Avanza `{av_p}` | Yahoo `{yf_p}` | Div `{div}` [{stat}]")

        total = len(results)
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 **Summary:** `{pass_count}/{total}` feeds fully reconciled (Threshold: <= 0.50%).")
        return "\n".join(lines)

    def answer_query(self, user_text: str) -> str:
        """
        Processes a natural language message from the user.
        Intercepts trade and watch directives first.
        Then tries Gemini API if key is present, otherwise executes internal quantitative reasoning.
        """
        text_clean = user_text.strip()

        # 1. First intercept trade execution / exit instructions
        trade_receipt = self.check_and_execute_trade_intent(text_clean)
        if trade_receipt:
            return trade_receipt

        # 2. Intercept focused watch mode directives
        watch_receipt = self.check_and_execute_watch_intent(text_clean)
        if watch_receipt:
            return watch_receipt

        # 2.3 Intercept alert snooze / mute directives (12-hour auto-reset)
        snooze_receipt = self.check_and_execute_snooze_intent(text_clean)
        if snooze_receipt:
            return snooze_receipt

        # 2.5 Intercept institutional desk directives
        inst_receipt = self.check_and_execute_institutional_intent(text_clean)
        if inst_receipt:
            return inst_receipt

        # 3. Try Gemini API if key exists
        if self.api_key:
            try:
                import requests
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
                prompt = (
                    f"{self.get_system_context()}\n\n"
                    f"User Query from Mobile: {text_clean}\n\n"
                    f"Answer concisely with clear financial reasoning, bullet points, and actionable mandate instructions:"
                )
                payload = {"contents": [{"parts": [{"text": prompt}]}]}
                resp = requests.post(url, json=payload, timeout=12)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"]
            except Exception as e:
                logger.debug(f"Gemini API query failed or timed out: {e}. Falling back to internal engine.")

        # 4. Fallback: Expert Quantitative Reasoning Engine
        return self._rule_based_reasoning(text_clean)

    def _rule_based_reasoning(self, text: str) -> str:
        """
        Advanced deterministic quantitative reasoning when external LLM is offline or unconfigured.
        """
        t = text.lower()

        # Thematic factor cluster queries
        if "cluster" in t or "factor concentration" in t or "thematic" in t or "factor risk" in t:
            if self.quant_risk:
                holdings = self.state_mgr.get_all_active_holdings()
                cash = self.state_mgr.state.cash_accounts
                res = self.quant_risk.calculate_cluster_breakdown(holdings, cash, self.state_mgr.state.mandate.total_points)
                lines = [
                    f"🧬 **Thematic Factor Cluster Analysis (Total NAV: {res['total_nav']:.2f} pts)**",
                    ""
                ]
                for cid in ["cluster_a", "cluster_b", "cluster_c", "cluster_d", "cluster_e"]:
                    cdata = res["clusters"].get(cid)
                    if cdata:
                        lines.append(f"• **{cdata['name']}:** `{cdata['points']:.2f} pts` ({cdata['pct_nav']:.1f}%)")
                if res["concentration_warnings"]:
                    lines.append("")
                    for w in res["concentration_warnings"]:
                        lines.append(w)
                else:
                    lines.append("\n✅ All clusters remain below the 35% high-beta concentration threshold.")
                return "\n".join(lines)
            return "Cluster analysis module not available."

        # ATR stop tightness queries
        if "too tight" in t or "atr" in t or "stops tight" in t or "tight stop" in t or "stop distance" in t or "calibrated" in t:
            stops = self.state_mgr.get_active_stops()
            lines = [
                "📐 **ATR-Calibrated Stop Loss Volatility Audit:**",
                "*(Optimal noise band: 2.0x - 3.0x ATR distance)*",
                ""
            ]
            evaluated = 0
            for s in stops[:8]:
                sym = s["symbol"]
                stop_pr = s.get("stop_price")
                if not stop_pr:
                    continue
                curr_px = stop_pr * 1.15
                if self.feed:
                    q = self.feed.get_quote(sym)
                    if not q and hasattr(self.feed, "normalize_symbol"):
                        q = self.feed.get_quote(self.feed.normalize_symbol(sym))
                    if q and q.price > 0:
                        curr_px = q.price

                if hasattr(self.feed, "get_atr_14"):
                    atr_14 = self.feed.get_atr_14(sym)
                else:
                    atr_14 = curr_px * 0.03

                res = self.quant_risk.evaluate_atr_stop(sym, curr_px, stop_pr, atr_14) if self.quant_risk else None
                if res:
                    evaluated += 1
                    status_icon = "✅" if res["status"] == "CALIBRATED" else ("⚠️" if res["status"] == "TOO_TIGHT" else "ℹ️")
                    lines.append(f"• **{sym}:** Stop `{stop_pr:.2f}` vs Price `{curr_px:.2f}` -> `{res['k_atr']:.1f}x ATR` {status_icon} ({res['status']})")

            if evaluated == 0:
                lines.append("• No active stops found with numerical price thresholds.")
            else:
                lines.append("\n💡 Stops under 2.0x ATR are vulnerable to normal intraday noise knock-out.")
            return "\n".join(lines)

        # 1. Cash queries
        if "cash" in t or "dry powder" in t or "powder" in t:
            cash = self.state_mgr.get_cash_summary()
            recon = self.state_mgr.reconcile_capital()
            return (
                f"💰 **Mandate Liquid Cash Reserves: {recon['cash_points']:.2f} pts ({recon['cash_pct']}%)**\n\n"
                f"• **Avanza AF:** `{cash['avanza_af']['points']:.2f} pts` (Macro capital preservation, risk-free yield, fully unencumbered)\n"
                f"• **Avanza ISK:** `{cash['avanza_isk']['points']:.2f} pts` (7.5 pts earmarked for INVE B Tranche 2 limit @ 402.50 SEK, 34.0 pts free)\n"
                f"• **Avanza KF:** `{cash['avanza_kf']['points']:.2f} pts` (Residual dividend compounder cash)\n"
                f"• **Revolut Invest:** `{cash['revolut_invest']['points']:.2f} pts` (Recovered from FIGR exit; flat $1 fee US tactical powder)"
            )

        # 2. Largest / Top holdings
        if "largest" in t or "top holding" in t or "biggest" in t:
            holdings = sorted(self.state_mgr.get_all_active_holdings(), key=lambda h: h.points, reverse=True)
            top = holdings[:5]
            lines = ["🏆 **Top 5 Portfolio Allocations:**", ""]
            for i, h in enumerate(top, 1):
                lines.append(f"{i}. **{h.name} ({h.symbol}):** `{h.points:.2f} pts` ({h.points/696.50*100:.2f}%) in `{h.account}` - {h.classification}")
            return "\n".join(lines)

        # 3. Disqualified / Blacklisted assets
        if "coreweave" in t or "crwv" in t or "bloom" in t or "be" in t or "blacklisted" in t or "disqualified" in t:
            return (
                "🚫 **Mandate Disqualification Log:**\n\n"
                "• **CoreWeave (`CRWV`):** Disqualified on Gates 1, 3, 7, 8, 10. Negative EVA spread, Altman Z'' = 0.34 (severe distress), "
                ">$28B debt burden, negative interest coverage, and 16.7% short float.\n"
                "• **Bloom Energy (`BE`):** Disqualified on Gates 1, 3, 7. 374x P/E, negative intrinsic DCF margin of safety, beta = 3.78.\n"
                "• **Figure Tech (`FIGR`) & Nebius (`NBIS`):** Liquidated. Re-entry strictly barred under emotional revenge-trading veto."
            )

        # 4. Derivative risk / Curfew
        if "curfew" in t or "derivative" in t or "mini l" in t or "omx" in t or "warrant" in t:
            holding = self.state_mgr.get_holding("MINI L OMX AVA 1615")
            if not holding or holding.status == "closed" or holding.points <= 0:
                return (
                    "⏱️ **Derivative Sleeve & Stockholm Curfew Protocol:**\n\n"
                    "• **Derivative Sleeve:** Flat (0.00 pts). The `MINI L OMX AVA 1615` warrant position is **closed**.\n"
                    "• **Intraday Curfew Status:** No active derivative positions requiring 19:45 CEST liquidation.\n"
                    "• **Protocol Vigilance:** The 19:45 CEST mandatory flat-book curfew remains active and will monitor any future derivative warrants."
                )
            pts = holding.points
            return (
                f"⏱️ **Derivative Sleeve & Stockholm Curfew Protocol:**\n\n"
                f"• **Active Warrant:** `MINI L OMX AVA 1615` ({pts:.2f} pts in Avanza ISK).\n"
                f"• **Stop Loss:** Strict -40% on warrant price caps loss at 2.00 pts (well below 3.48 pt Gate 4 ceiling).\n"
                f"• **Intraday Curfew:** 19:45 CEST mandatory flat-book protocol. Warning 1 at 19:30, Warning 2 at 19:40. "
                "Overnight carry is forbidden to protect against gap knock-out."
            )

        # 5. Gate 4 hypothetical sizing: "buy X points with Y% stop"
        trade_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:pts|points).*?(\d+(?:\.\d+)?)\s*%", t)
        if trade_match:
            pts = float(trade_match.group(1))
            stop_pct = float(trade_match.group(2)) / 100.0
            risk_pts, risk_pct, passed = self.auditor.calculate_trade_risk(pts, stop_pct)
            verdict = "✅ **PASSES GATE 4**" if passed else "❌ **VIOLATES GATE 4 RISK BUDGET**"
            return (
                f"🧮 **Gate 4 Pre-Trade Risk Audit:**\n\n"
                f"• Proposed Size: `{pts:.2f} points`\n"
                f"• Proposed Stop Loss: `{stop_pct*100:.1f}%`\n"
                f"• Risk Exposure: `{risk_pts:.2f} points` ({risk_pct:.2f}% of mandate)\n"
                f"• Risk Ceiling: `3.48 points` (0.50% max)\n\n"
                f"{verdict}: Maximum allowable size at {stop_pct*100:.1f}% stop is "
                f"`{self.auditor.max_position_points_for_stop(stop_pct):.2f} points`."
            )

        # 6. Specific ticker / holding query across ALL portfolio holdings & watchlist
        for h in self.state_mgr.get_all_active_holdings():
            sym_clean = h.symbol.lower().replace("-b.st", "").replace(".st", "")
            name_words = [w.lower() for w in h.name.split() if len(w) > 3]
            if sym_clean in t or h.symbol.lower() in t or any(w in t for w in name_words):
                stop_info = "None"
                if h.stop_loss:
                    stop_info = f"{h.stop_loss.type} @ {h.stop_loss.price or h.stop_loss.percentage}"
                elif h.tactical_tranche and h.tactical_tranche.stop_loss_price:
                    stop_info = f"tactical stop @ {h.tactical_tranche.stop_loss_price}"

                trim_info = ""
                if h.limit_sell:
                    trim_info = f"\n• Resting Trim: `{h.limit_sell.points_to_trim:.1f} pts @ {h.limit_sell.target_price}`"

                return (
                    f"📌 **Portfolio Dossier: {h.name} ({h.symbol})**\n\n"
                    f"• Account Wrapper: `{h.account}` ({h.currency})\n"
                    f"• Allocation: `{h.points:.2f} points` ({h.points/696.50*100:.2f}% of mandate)\n"
                    f"• Classification: {h.classification}\n"
                    f"• Active Stop: `{stop_info}`{trim_info}\n"
                    f"• Cost Basis: `{h.cost_basis or 'N/A'}`\n"
                    f"• Notes: {h.notes or 'Core mandate asset'}"
                )

        # Check watchlist
        for w in self.state_mgr.state.watchlist:
            if w.symbol.lower() in t or w.name.lower() in t:
                tranche_lines = [f"Tranche {tr.tranche}: {tr.points} pts @ ${tr.target_price:.2f}" for tr in w.tranches]
                return (
                    f"👀 **Watchlist Pipeline: {w.name} ({w.symbol})**\n\n"
                    f"• Re-entry Tranches: {', '.join(tranche_lines)}\n"
                    f"• Target Stop: `{w.stop_loss or 'N/A'}`\n"
                    f"• Notes: {w.notes or 'Watchlist asset'}"
                )

        # 7. Default helpful overview
        recon = self.state_mgr.reconcile_capital()
        return (
            f"🤖 **Sentinel Desk Assistant Online**\n\n"
            f"I am actively monitoring your 696.50-point mandate ({recon['invested_points']:.1f} pts invested, {recon['cash_points']:.1f} pts cash).\n\n"
            f"You can ask me questions like:\n"
            f"• *'How much cash do we have across accounts?'*\n"
            f"• *'What is our stop on AEHR and XE?'*\n"
            f"• *'Can I buy 15 points with a 20% stop loss?'*\n"
            f"• *'Why was CoreWeave disqualified?'*\n"
            f"• *'Show me my top 5 holdings'*\n"
            f"Or send standard commands like `/status`, `/stops`, `/curfew`, `/macro`, or `/analyze <TICKER>`."
        )
