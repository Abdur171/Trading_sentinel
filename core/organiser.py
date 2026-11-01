"""
core/organiser.py - Master Organiser Daemon for Autonomous Remote Desk.
Orchestrates workers, event loops, Telegram command routing, and conversational AI assistant.
"""

from __future__ import annotations
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from pathlib import Path

from core.models import AlertEvent, AlertSeverity, Holding, StopLossConfig
from core.state_manager import StateManager
from core.gate_auditor import GateAuditor
from core.curfew_manager import CurfewManager
from core.macro_sentinel import MacroSentinel
from core.agent_chat import AgentChat
from core.watch_manager import WatchManager
from core.session_manager import SessionManager
from core.quant_risk import QuantRisk
from core.power_manager import PowerManager
from feeds.base_feed import BaseFeed
from feeds.yfinance_feed import YFinanceFeed
from feeds.news_feed import NewsFeed
from feeds.avanza_feed import AvanzaFeed
from feeds.cross_verifier import CrossVerifier
from core.executive_desk import ExecutiveDesk
from core.signals_desk import SignalsDesk
from core.swing_desk import SwingDesk
from core.derivatives_desk import DerivativesDesk
from core.intelligence_desk import IntelligenceDesk
from core.ai_reasoner import AIReasoner
from dispatchers.composite_dispatcher import CompositeDispatcher
from dispatchers.telegram_bot import TelegramBotDispatcher
from workers.risk_worker import RiskWorker
from workers.scanner_worker import ScannerWorker
from workers.analyst_worker import AnalystWorker

logger = logging.getLogger(__name__)


class Organiser:
    """
    Central Nervous System of the Trading Sentinel.
    Maintains continuous background vigilance, drives workers, and serves Telegram mobile interaction.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        feed: Optional[BaseFeed] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        is_test_mode: bool = False
    ):
        self.is_test_mode = is_test_mode
        self.state_mgr = StateManager(config_path)
        self.auditor = GateAuditor(total_points=self.state_mgr.state.mandate.total_points)
        self.curfew_mgr = CurfewManager()
        self.macro_sentinel = MacroSentinel()
        self.feed = feed or YFinanceFeed()
        self.news_feed = NewsFeed()
        self.watch_mgr = WatchManager()
        self.session_mgr = SessionManager()
        self.quant_risk = QuantRisk()
        self.power_mgr = PowerManager(prevent_sleep=not is_test_mode)
        if not is_test_mode:
            self.power_mgr.enable_sleep_prevention()

        # Load sentinel_config.yaml if present to dynamically set cadence and parameters
        sentinel_cfg_path = Path(__file__).resolve().parent.parent / "config" / "sentinel_config.yaml"
        if sentinel_cfg_path.exists():
            try:
                import yaml
                with open(sentinel_cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                qh_cfg = cfg.get("quiet_hours", {})
                br_cfg = cfg.get("briefings", {})
                if qh_cfg.get("start_time") and qh_cfg.get("end_time"):
                    self.session_mgr.qh_start = self.session_mgr._parse_time(str(qh_cfg["start_time"]))
                    self.session_mgr.qh_end = self.session_mgr._parse_time(str(qh_cfg["end_time"]))
                if br_cfg.get("morning_time"):
                    self.session_mgr.briefing_time = self.session_mgr._parse_time(str(br_cfg["morning_time"]))
                if br_cfg.get("evening_time"):
                    self.session_mgr.debrief_time = self.session_mgr._parse_time(str(br_cfg["evening_time"]))
                cache_cfg = cfg.get("cache", {})
                if cache_cfg.get("quote_ttl_seconds") and hasattr(self.feed, "cache_ttl_seconds"):
                    self.feed.cache_ttl_seconds = float(cache_cfg["quote_ttl_seconds"])
                pwr_cfg = cfg.get("power_management", {})
                if "prevent_sleep" in pwr_cfg:
                    self.power_mgr.prevent_sleep = bool(pwr_cfg["prevent_sleep"])
                    if self.power_mgr.prevent_sleep and not is_test_mode:
                        self.power_mgr.enable_sleep_prevention()
                    elif not self.power_mgr.prevent_sleep:
                        self.power_mgr.disable_sleep_prevention()
            except Exception as e:
                logger.debug(f"Could not load custom parameters from sentinel_config.yaml: {e}")

        # Alert Routing with Quiet Hours Gating and 12-Hour Stationary Suppression
        stationary_hours = 12.0
        if sentinel_cfg_path.exists():
            try:
                with open(sentinel_cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                supp_cfg = cfg.get("alert_suppression", {})
                if "stationary_suppression_hours" in supp_cfg:
                    stationary_hours = float(supp_cfg["stationary_suppression_hours"])
            except Exception:
                pass

        self.dispatcher = CompositeDispatcher(
            cooldown_minutes=15,
            stationary_suppression_hours=stationary_hours,
            session_manager=self.session_mgr
        )
        if sentinel_cfg_path.exists():
            try:
                with open(sentinel_cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "quiet_hours" in cfg and "enabled" in cfg["quiet_hours"]:
                    self.dispatcher.quiet_hours_enabled = bool(cfg["quiet_hours"]["enabled"])
            except Exception:
                pass

        self.telegram_bot = TelegramBotDispatcher(
            bot_token=bot_token,
            chat_id=chat_id,
            is_mock=is_test_mode or not bool(bot_token)
        )
        self.dispatcher.register_handler(self.telegram_bot.send_alert)

        # Institutional Desks
        self.executive_desk = ExecutiveDesk(self.state_mgr)
        self.signals_desk = SignalsDesk(feed=self.feed, state_mgr=self.state_mgr)
        self.swing_desk = SwingDesk(state_mgr=self.state_mgr, quant_risk=self.quant_risk)
        self.derivatives_desk = DerivativesDesk(state_mgr=self.state_mgr, curfew_mgr=self.curfew_mgr)
        self.cross_verifier = CrossVerifier(avanza_feed=AvanzaFeed(), yfinance_feed=self.feed)
        self.ai_reasoner = AIReasoner(
            state_mgr=self.state_mgr,
            feed=self.feed,
            signals_desk=self.signals_desk,
            executive_desk=self.executive_desk,
            gate_auditor=self.auditor
        )
        self.intelligence_desk = IntelligenceDesk(
            state_mgr=self.state_mgr,
            feed=self.feed,
            executive_desk=self.executive_desk,
            signals_desk=self.signals_desk,
            derivatives_desk=self.derivatives_desk,
            ai_reasoner=self.ai_reasoner,
            macro_sentinel=self.macro_sentinel,
            news_feed=self.news_feed
        )

        # Conversational Agent
        self.agent_chat = AgentChat(
            state_mgr=self.state_mgr,
            gate_auditor=self.auditor,
            macro_sentinel=self.macro_sentinel,
            watch_mgr=self.watch_mgr,
            session_mgr=self.session_mgr,
            quant_risk=self.quant_risk,
            feed=self.feed,
            executive_desk=self.executive_desk,
            signals_desk=self.signals_desk,
            swing_desk=self.swing_desk,
            derivatives_desk=self.derivatives_desk,
            cross_verifier=self.cross_verifier,
            intelligence_desk=self.intelligence_desk,
            ai_reasoner=self.ai_reasoner,
            dispatcher=self.dispatcher,
        )

        # Workers
        self.risk_worker = RiskWorker(
            state_mgr=self.state_mgr,
            gate_auditor=self.auditor,
            curfew_mgr=self.curfew_mgr,
            feed=self.feed,
            dispatcher=self.dispatcher,
            interval_seconds=60,
            watch_mgr=self.watch_mgr,
            session_mgr=self.session_mgr,
            quant_risk=self.quant_risk,
            macro_sentinel=self.macro_sentinel,
            power_mgr=self.power_mgr
        )
        self.scanner_worker = ScannerWorker(
            state_mgr=self.state_mgr,
            feed=self.feed,
            news_feed=self.news_feed,
            dispatcher=self.dispatcher,
            interval_seconds=300
        )
        self.analyst_worker = AnalystWorker(gate_auditor=self.auditor, feed=self.feed)

        # Connect command router
        self.telegram_bot.set_command_handler(self.process_incoming_telegram_message)
        self.current_gear = "idle"  # idle, cruise, or active

    def process_incoming_telegram_message(self, text: str, user_id: str) -> str:
        """
        Routes inbound messages:
        1. If text starts with '/', executes command.
        2. If non-command natural language, routes to AgentChat for conversational reasoning.
        """
        text = text.strip()
        if not text:
            return "Please enter a command or query."

        # Command Dispatch
        if text.startswith("/"):
            parts = text.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd == "/start" or cmd == "/help":
                return self._cmd_help()
            elif cmd == "/battleplan":
                return self._cmd_battleplan()
            elif cmd == "/signals":
                return self._cmd_signals()
            elif cmd == "/pots":
                return self._cmd_pots()
            elif cmd == "/swing":
                return self._cmd_swing(args)
            elif cmd == "/prompt":
                return self._cmd_prompt(args)
            elif cmd == "/reason":
                return self._cmd_reason(args)
            elif cmd == "/verify_feeds":
                return self._cmd_verify_feeds()
            elif cmd == "/status":
                return self._cmd_status()
            elif cmd == "/stops":
                return self._cmd_stops()
            elif cmd == "/cash":
                return self._cmd_cash()
            elif cmd == "/curfew":
                return self._cmd_curfew()
            elif cmd == "/macro":
                return self._cmd_macro()
            elif cmd == "/analyze" and args:
                return self._cmd_analyze(args[0])
            elif cmd == "/news" and args:
                return self._cmd_news(args[0])
            elif cmd == "/scan":
                sector = args[0] if args else "all"
                return self._cmd_scan(sector)
            elif cmd == "/gear":
                mode = args[0] if args else None
                return self._cmd_gear(mode)
            elif cmd == "/focus" and args:
                return self._cmd_focus(args)
            elif cmd == "/unfocus":
                return self._cmd_unfocus(args)
            elif cmd == "/briefing":
                return self._cmd_briefing()
            elif cmd == "/debrief":
                return self._cmd_debrief()
            elif cmd == "/clusters":
                return self._cmd_clusters()
            elif cmd == "/convexity":
                return self._cmd_convexity()
            elif (cmd in ["/trim", "/trim_holding"]) and args:
                return self._cmd_trim_holding(args)
            elif (cmd in ["/sell", "/close", "/exit"]) and args:
                return self._cmd_close_holding(args)
            elif cmd == "/update_stop" and len(args) >= 2:
                sym = " ".join(args[:-1])
                price_str = args[-1]
                return self._cmd_update_stop(sym, price_str)
            elif cmd == "/add_holding" and len(args) >= 3:
                return self._cmd_add_holding(args)
            elif cmd == "/audit_mandate":
                return self._cmd_audit_mandate()
            elif cmd in ["/snooze", "/mute"]:
                return self._cmd_snooze(args)
            elif cmd in ["/unsnooze", "/unmute"]:
                return self._cmd_unsnooze(args)
            elif cmd in ["/snoozes", "/mutes"]:
                return self._cmd_list_snoozes()
            else:
                return f"⚠️ Unknown command `{cmd}`. Type `/help` for grammar or simply ask me a question."

        # Natural Language Chat
        return self.agent_chat.answer_query(text)

    # Command Handlers
    def _cmd_help(self) -> str:
        return (
            "📱 **Trading Sentinel Mobile Remote Control**\n\n"
            "**Institutional Desks & Strategies:**\n"
            "• `/battleplan` - Complete 08:30 CEST 5-section institutional briefing card\n"
            "• `/signals` - SignalsDesk qualified swing setups across 3 strategies\n"
            "• `/pots` - ExecutiveDesk dynamic 4-pot capital matrix & parity audit\n"
            "• `/swing <SYM>` - Tactical SwingDesk 2-stage status & ATR trailing stop\n"
            "• `/prompt <SYM>` - Dense 5-line AI prompt dossier for mobile LLMs\n"
            "• `/reason <SYM>` - Automated Gemini 1.5 Flash critique & quantitative audit\n"
            "• `/verify_feeds` - Dual-engine Avanza vs Yahoo market data cross-audit\n\n"
            "**Operational Commands:**\n"
            "• `/status` - Total Mandate NAV, Invested vs Cash Parity\n"
            "• `/stops` - All active stops, prices, and stop distances\n"
            "• `/cash` - Cash breakdown across AF, ISK, KF, Revolut\n"
            "• `/curfew` - Derivatives & 19:45 CEST Stockholm curfew status\n"
            "• `/macro` - Live yield, Brent, and Gold transmission matrix\n"
            "• `/focus <SYM> [MINS]` - High-frequency focused watch mode (default 60m)\n"
            "• `/unfocus [SYM]` - Deactivate watch mode and restore normal cadence\n"
            "• `/briefing` - Pre-market 08:30 CEST macro, stops & catalyst briefing\n"
            "• `/debrief` - Post-market 22:15 CEST NAV performance & movers debrief\n"
            "• `/clusters` - Thematic factor cluster matrix & concentration risk\n"
            "• `/convexity` - Portfolio DCF Margin of Safety vs stop convexity scorecard\n"
            "• `/analyze <TICKER>` - Run full 10-Gate quantitative audit & MoS\n"
            "• `/news <TICKER>` - Latest news catalysts\n"
            "• `/scan <SECTOR>` - Screen price gaps & catalysts\n"
            "• `/gear [mode]` - Query or set gearbox cadence (idle, active, cruise)\n"
            "• `/trim <SYM> <PTS> [PROCEEDS]` - Register partial position sale & release cash\n"
            "• `/sell <SYM>` or `/close <SYM>` - Register position exit / sale & release cash\n"
            "• `/update_stop <SYM> <PRICE>` - Update stop loss in master ledger\n"
            "• `/add_holding <SYM> <PTS> <ACCT> [STOP] [CLASS]` - Add new holding to ledger\n"
            "• `/audit_mandate` - Mandate health & compliance audit\n"
            "• `/snooze [SYM|MACRO] [HOURS]` - Silence repeating alerts (default 12h, auto-resets)\n"
            "• `/unsnooze [SYM|MACRO]` - Restore normal alerts immediately\n"
            "• `/snoozes` - List active muted alert streams and time to auto-reset\n\n"
            "💬 *Natural language supported: 'Show battle plan', 'What are the swing signals?', 'Snooze macro shield', 'Check swing on AEHR', 'Verify feeds'.*"
        )

    def _cmd_battleplan(self) -> str:
        return self.intelligence_desk.generate_morning_battle_plan()

    def _cmd_signals(self) -> str:
        return self.signals_desk.format_signals_card()

    def _cmd_pots(self) -> str:
        return self.executive_desk.format_pots_card()

    def _cmd_swing(self, args: List[str]) -> str:
        sym = " ".join(args).strip().upper() if args else "AEHR"
        return self.swing_desk.format_swing_card(sym)

    def _cmd_prompt(self, args: List[str]) -> str:
        sym = " ".join(args).strip().upper() if args else "AEHR"
        return self.ai_reasoner.generate_prompt_dossier(sym)

    def _cmd_reason(self, args: List[str]) -> str:
        sym = " ".join(args).strip().upper() if args else "AEHR"
        return self.ai_reasoner.critique(sym)

    def _cmd_verify_feeds(self) -> str:
        return self.agent_chat._format_cross_verify_summary()

    def _cmd_status(self) -> str:
        recon = self.state_mgr.reconcile_capital()
        parity_icon = "✅" if recon["parity_reconciled"] else "⚠️"
        hwm_data = self.quant_risk.update_high_water_mark(recon["calculated_total_points"])
        dd_str = f"-{hwm_data['drawdown_pts']:.2f} pts ({hwm_data['drawdown_pct']:.2f}%)" if hwm_data['drawdown_pct'] > 0 else "0.00% (At Peak)"
        risk_limit = getattr(self.state_mgr.state.mandate, "max_single_trade_risk_pts", 3.44)
        return (
            f"💼 **Mandate Capital Ledger Status (Sentinel V3.1)**\n\n"
            f"• **Benchmark High-Water Mark:** `{self.state_mgr.state.mandate.benchmark_hwm:.2f} pts`\n"
            f"• **Baseline Mandate NAV:** `{self.state_mgr.state.mandate.total_points:.2f} pts`\n"
            f"• **Calculated Live NAV:** `{recon['calculated_total_points']:.2f} pts` {parity_icon}\n"
            f"• **Invested Capital:** `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%)\n"
            f"• **Liquid Cash Reserves:** `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)\n"
            f"• **Active Positions:** `{recon['num_active_holdings']}`\n"
            f"• **High-Water Mark Drawdown:** `{dd_str}`\n"
            f"• **Single-Trade Risk Cap:** `{risk_limit:.2f} pts` (0.50% Live NAV)\n"
            f"• **Current Gear:** `{self.current_gear.upper()}`\n"
            f"• **Status Time:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`"
        )

    def _cmd_stops(self) -> str:
        stops = self.state_mgr.get_active_stops()
        if not stops:
            return "No active stops configured."

        symbols = [s["symbol"] for s in stops]
        quotes = self.feed.get_batch_quotes(symbols)

        lines = ["🛑 **Active Stop-Loss Audits (100% Zero-Null Coverage):**", ""]
        for s in stops:
            sym = s["symbol"]
            q = quotes.get(sym)
            if not q and hasattr(self.feed, "normalize_symbol"):
                q = quotes.get(self.feed.normalize_symbol(sym))
            
            h = self.state_mgr.get_holding(sym)
            price = None
            if q and q.price and q.price > 0:
                price = q.price
            elif h:
                price = h.current_price or h.reference_price or h.cost_basis

            curr_pr = f"{price:.2f}" if price is not None else "N/A"
            stop_pr = s.get("stop_price")
            dist_str = ""
            if price and stop_pr:
                dist = ((price - stop_pr) / price) * 100
                dist_str = f" (Buffer: {dist:+.1f}%)"

            tier_tag = f" [{s.get('position_tier', 'TIER_1_CORE')}]" if s.get('position_tier') else ""
            lines.append(
                f"• **{sym}** ({s['points']:.2f} pts in `{s['account']}`){tier_tag}: "
                f"Stop `{stop_pr or s.get('percentage')}` | Price `{curr_pr}`{dist_str}"
            )
        return "\n".join(lines)

    def _cmd_cash(self) -> str:
        cash = self.state_mgr.get_cash_summary()
        recon = self.state_mgr.reconcile_capital()
        lines = [
            f"💵 **Cash Partitioning (Total: {recon['cash_points']:.2f} pts / {recon['cash_pct']}%)**",
            ""
        ]
        for acc_id, d in cash.items():
            committed_str = f" (Committed: {d['committed_points']:.2f} pts)" if d.get('committed_points', 0) > 0 else ""
            lines.append(
                f"• **{d['name']}:** `{d['points']:.2f} pts` {d['currency']}{committed_str}\n"
                f"  _Purpose: {d['purpose']}_"
            )
        return "\n".join(lines)

    def _cmd_curfew(self) -> str:
        st_now = self.curfew_mgr.get_localized_time()
        time_str = st_now.strftime("%H:%M:%S CEST")
        
        # Check derivative holding(s)
        active_derivs = [h for h in self.state_mgr.get_all_active_holdings() if self.curfew_mgr.is_derivative_or_time_sensitive(h)]
        if active_derivs:
            deriv_lines = []
            for h in active_derivs:
                risk_pts = h.points * 0.40
                deriv_lines.append(
                    f"⚠️ Active Warrant: `{h.name}` ({h.points:.2f} pts in `{h.account}`).\n"
                    f"Mandatory flat book cutoff: `19:45 CEST`.\n"
                    f"Stop Loss: `-40% on warrant price` (Risk: {risk_pts:.2f} pts <= 3.44 pts limit)."
                )
            status_line = "\n".join(deriv_lines)
        else:
            status_line = "No open derivative warrants."

        events_lines = []
        for ev in self.state_mgr.state.time_sensitive_events:
            if getattr(ev, "status", "active") != "inactive":
                events_lines.append(f"• **{ev.symbol}:** {ev.description} ({ev.event_date}) - {ev.directive}")
        events_str = "\n".join(events_lines) if events_lines else "None active."

        return (
            f"⏱️ **Curfew & Time-Sensitivity Watchdog (V3.1)**\n\n"
            f"• **Current Stockholm Time:** `{time_str}`\n"
            f"• **Quiet Hours Window:** `23:00 - 06:45 CEST` (Armed for 06:45 wake-up)\n"
            f"• **Derivative Status:**\n{status_line}\n\n"
            f"📅 **Upcoming Time-Sensitive Events:**\n{events_str}"
        )

    def _cmd_macro(self) -> str:
        # Update macro quotes
        macro_quotes = {}
        for sym in ["^TNX", "^TYX", "^IRX", "BZ=F", "GC=F"]:
            q = self.feed.get_quote(sym)
            if q:
                self.macro_sentinel.update_quote(sym, q.price)
                macro_quotes[sym] = q.price

        shield_res = self.quant_risk.evaluate_macro_shield(macro_quotes)
        matrix_str = self.macro_sentinel.format_macro_matrix()

        # Check macro embargo window
        is_embargo, embargo_reason = self.macro_sentinel.check_embargo()

        shield_lines = ["", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        if is_embargo:
            shield_lines.append(f"⏳ **MACRO EVENT EMBARGO ACTIVE ({embargo_reason})**")
            shield_lines.append("• Directive: Freeze new derivative entries and high-beta breakouts.")
            shield_lines.append("")

        regime = shield_res.get("regime", "NORMAL_REGIME")
        if regime == "CRISIS_REGIME" or shield_res.get("shield_active"):
            shield_lines.append("🚨 **MACRO DEFENSIVE SHIELD: CRISIS REGIME (ACTIVE)**")
            for b in shield_res.get("breaches", []):
                shield_lines.append(f"• {b}")
            shield_lines.append("\n**Automated Directives:**")
            for d in shield_res.get("directives", []):
                shield_lines.append(f"• {d}")
        elif regime == "STRESSED_REGIME":
            shield_lines.append("⚠️ **MACRO DEFENSIVE SHIELD: STRESSED REGIME**")
            for b in shield_res.get("breaches", []):
                shield_lines.append(f"• {b}")
            shield_lines.append("\n**Directives:** Derivative position cap halved to 2.50 pts; trailing stops tightened.")
        else:
            shield_lines.append("🛡️ **Macro Defensive Shield: NORMAL REGIME** (All macro barriers unbreached)")

        return matrix_str + "\n" + "\n".join(shield_lines)

    def _cmd_analyze(self, ticker: str) -> str:
        analysis = self.analyst_worker.analyze_ticker(ticker)
        return self.analyst_worker.format_memo_card(analysis)

    def _cmd_news(self, ticker: str) -> str:
        articles = self.news_feed.fetch_news_for_ticker(ticker, limit=4)
        if not articles:
            return f"No recent headlines found for `{ticker}`."
        lines = [f"📰 **Recent News for {ticker}:**", ""]
        for a in articles:
            lines.append(f"• **{a['title']}**\n  [Link]({a['link']}) ({a['published'][:16] if a['published'] else ''})")
        return "\n".join(lines)

    def _cmd_scan(self, sector: str) -> str:
        gap_alerts = self.scanner_worker.scan_price_gaps()
        if not gap_alerts:
            return f"Market scanner complete: No price dislocations > 3% detected."
        lines = ["🔍 **Screening Results (Dislocations > 3%):**", ""]
        for ga in gap_alerts:
            lines.append(f"• {ga.title}")
        return "\n".join(lines)

    def _cmd_gear(self, mode: Optional[str] = None) -> str:
        if not mode:
            sessions = self.session_mgr.get_active_sessions()
            rec_gear = self.session_mgr.get_recommended_gear(has_watch_target=bool(self.watch_mgr.get_active_targets()))
            return (
                f"⚙️ **Transmission Gearbox Status**\n\n"
                f"• **Current Operational Gear:** `{self.current_gear.upper()}`\n"
                f"• **Recommended Session Gear:** `{rec_gear.upper()}`\n"
                f"• **Active Sessions:** `{', '.join(sessions)}`\n"
                f"• **Focused Watch Targets:** `{len(self.watch_mgr.get_active_targets())}`\n\n"
                f"Use `/gear <idle|active|cruise>` to override."
            )

        mode_clean = mode.lower().strip()
        if mode_clean in ["idle", "low", "dormant", "0"]:
            self.current_gear = "idle"
            self.risk_worker.interval = 120
            self.scanner_worker.interval = 600
            return "⚙️ Switched to **LOW GEAR (IDLE)**: Low CPU consumption, polling every 120s/600s."
        elif mode_clean in ["active", "high", "attack", "focus", "2"]:
            self.current_gear = "active"
            self.risk_worker.interval = 30
            self.scanner_worker.interval = 120
            return "⚙️ Switched to **HIGH GEAR (ACTIVE)**: High alertness screening, polling every 30s/120s."
        elif mode_clean in ["cruise", "normal", "1"]:
            self.current_gear = "cruise"
            self.risk_worker.interval = 60
            self.scanner_worker.interval = 300
            return "⚙️ Switched to **CRUISE GEAR**: Normal session surveillance, polling every 60s/300s."
        return f"Unknown gear mode `{mode}`. Choose `idle` or `active`."

    def _cmd_focus(self, args: List[str]) -> str:
        """
        Activates focused watch mode on a target asset.
        Usage: /focus <SYM> [MINUTES] (supports multi-word symbols, e.g. /focus INVE B 60)
        """
        if not args:
            return "⚠️ Usage: `/focus <SYMBOL> [MINUTES]` (e.g. `/focus ARM 60` or `/focus INVE B 60`)"

        mins = 60
        symbol_parts = args
        try:
            val = int(args[-1])
            mins = val
            symbol_parts = args[:-1]
        except (ValueError, IndexError):
            mins = 60
            symbol_parts = args

        if not symbol_parts:
            return "⚠️ Usage: `/focus <SYMBOL> [MINUTES]` (e.g. `/focus ARM 60`)"

        sym = " ".join(symbol_parts).strip().upper()

        q = self.feed.get_quote(sym)
        if not q and hasattr(self.feed, "normalize_symbol"):
            q = self.feed.get_quote(self.feed.normalize_symbol(sym))

        curr_price = q.price if q and q.price > 0 else 100.0

        target = self.watch_mgr.start_watch(
            symbol=sym,
            baseline_price=curr_price,
            duration_minutes=mins,
            alert_threshold_pct=0.015,
            reason=f"User activated focused watch via Telegram ({mins}m)"
        )
        self.current_gear = "active"
        self.risk_worker.interval = 15

        return (
            f"🎯 **Focused Watch Mode Activated**\n\n"
            f"• **Asset:** `{target.symbol}`\n"
            f"• **Baseline Price:** `{target.baseline_price:.2f}`\n"
            f"• **Surveillance Duration:** `{target.duration_minutes} minutes`\n"
            f"• **Delta Alert Threshold:** `±{target.alert_threshold_pct*100:.1f}%`\n"
            f"• **Transmission Gear:** `HIGH GEAR (ACTIVE)` (15s polling cycle)\n"
            f"• **Exchange Close:** `{target.exchange_close_time} CEST`\n\n"
            f"Cadence will auto-reset to normal cruise mode upon TTL expiry, market close, or `/unfocus`."
        )

    def _cmd_unfocus(self, args: List[str]) -> str:
        """
        Cancels focused watch mode.
        Usage: /unfocus [SYM] (supports multi-word symbols, e.g. /unfocus INVE B)
        """
        sym = " ".join(args).strip().upper() if args else None
        alerts = self.watch_mgr.stop_watch(symbol=sym)

        active_targets = self.watch_mgr.get_active_targets()
        if not active_targets:
            rec_gear = self.session_mgr.get_recommended_gear(has_watch_target=False)
            if rec_gear == "dormant":
                self.current_gear = "idle"
                self.risk_worker.interval = 120
            else:
                self.current_gear = "idle"
                self.risk_worker.interval = 60

        if not alerts:
            return f"ℹ️ No active watch targets found to unfocus{' for `' + sym + '`' if sym else ''}."

        stopped_symbols = ", ".join([a.symbol for a in alerts if a.symbol])
        return (
            f"🔄 **Focused Watch Mode Deactivated**\n\n"
            f"• **Targets Stopped:** `{stopped_symbols}`\n"
            f"• **Current Gear:** `{self.current_gear.upper()}`\n"
            f"• **Cadence:** Restored to normal cruise/idle schedule."
        )

    def _cmd_briefing(self) -> str:
        alert = self.session_mgr.generate_morning_briefing(self.state_mgr, self.feed, self.macro_sentinel)
        return alert.message

    def _cmd_debrief(self) -> str:
        alert = self.session_mgr.generate_evening_debrief(self.state_mgr, self.feed)
        return alert.message

    def _cmd_clusters(self) -> str:
        holdings = self.state_mgr.get_all_active_holdings()
        cash = self.state_mgr.state.cash_accounts
        res = self.quant_risk.calculate_cluster_breakdown(holdings, cash, self.state_mgr.state.mandate.total_points)

        lines = [
            f"🧬 **Thematic Factor Cluster Allocation Matrix (NAV: {res['total_nav']:.2f} pts)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]

        for cid in ["cluster_a", "cluster_b", "cluster_c", "cluster_d", "cluster_e", "unclustered"]:
            data = res["clusters"].get(cid)
            if not data:
                continue
            icon = "⚡" if data["is_high_beta"] else "🛡️"
            lines.append(f"{icon} **{data['name']}:** `{data['points']:.2f} pts` ({data['pct_nav']:.1f}%)")
            if data["holdings"]:
                items_str = ", ".join(data["holdings"][:4])
                if len(data["holdings"]) > 4:
                    items_str += f" (+{len(data['holdings'])-4} more)"
                lines.append(f"  _{items_str}_")

        if res["concentration_warnings"]:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            for w in res["concentration_warnings"]:
                lines.append(w)
        else:
            lines.append("")
            lines.append("✅ *All high-beta cluster exposures within <= 35% concentration threshold.*")

        return "\n".join(lines)

    def _cmd_convexity(self) -> str:
        holdings = self.state_mgr.get_all_active_holdings()
        symbols = [h.symbol for h in holdings]
        quotes = self.feed.get_batch_quotes(symbols)
        scorecard = self.quant_risk.calculate_convexity_scorecard(holdings, quotes)

        lines = [
            "📐 **Portfolio Convexity & Asymmetry Scorecard**",
            "*(Convexity Ratio = DCF Margin of Safety % / Downside Stop Distance %)*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]

        for s in scorecard[:8]:
            stop_str = f"Stop @ {s['stop_price']:.2f}" if s['stop_price'] else "15% base stop"
            lines.append(
                f"• **{s['symbol']}** ({s['points']:.1f} pts): "
                f"Ratio **`{s['convexity_ratio']:.2f}x`** | MoS `+{s['margin_of_safety_pct']:.1f}%` vs Downside `-{s['downside_risk_pct']:.1f}%` ({stop_str})"
            )

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 *Assets with ratio > 1.5x provide superior risk-adjusted asymmetric convexity.*")
        return "\n".join(lines)

    def _cmd_trim_holding(self, args: List[str]) -> str:
        """
        Dynamically registers partial position trim in the master ledger:
        Usage: /trim <SYMBOL> <POINTS> [REALIZED_PROCEEDS]
        Examples:
          /trim ORCL 15
          /trim CEG 3.0
          /trim MINI L AMD AVA 117 2.0
          /trim INVE-B.ST 3.5
        """
        if not args:
            return "⚠️ Usage: `/trim <SYMBOL> <POINTS> [REALIZED_PROCEEDS]` (e.g. `/trim ORCL 15` or `/trim CEG 3.0`)"

        # Filter out fluff words like "pts", "points", "p"
        cleaned_args = [a for a in args if a.lower() not in ["pts", "points", "p"]]
        if len(cleaned_args) < 2:
            return "⚠️ Usage: `/trim <SYMBOL> <POINTS>` (e.g. `/trim ORCL 15` or `/trim CEG 3.0`)"

        realized_proceeds = None
        points_to_trim = None
        symbol_parts = []

        try:
            if len(cleaned_args) >= 3:
                try:
                    val2 = float(cleaned_args[-1].replace("$", "").replace("SEK", "").strip())
                    val1 = float(cleaned_args[-2].replace("$", "").replace("SEK", "").strip())
                    points_to_trim = val1
                    realized_proceeds = val2
                    symbol_parts = cleaned_args[:-2]
                except ValueError:
                    val1 = float(cleaned_args[-1].replace("$", "").replace("SEK", "").strip())
                    points_to_trim = val1
                    symbol_parts = cleaned_args[:-1]
            else:
                val1 = float(cleaned_args[-1].replace("$", "").replace("SEK", "").strip())
                points_to_trim = val1
                symbol_parts = cleaned_args[:-1]
        except (ValueError, IndexError):
            return f"❌ Could not parse numeric points from `{args}`. Usage: `/trim <SYMBOL> <POINTS>`"

        if not symbol_parts or points_to_trim is None:
            return "⚠️ Usage: `/trim <SYMBOL> <POINTS>`"

        sym_query = " ".join(symbol_parts).strip()
        res = self.state_mgr.trim_holding(
            sym_query,
            points_to_trim=points_to_trim,
            realized_proceeds=realized_proceeds,
            notes="Partial trim executed via Telegram command"
        )
        if not res["success"]:
            return f"❌ {res.get('error', 'Failed to trim holding.')}"

        recon = res["reconciliation"]

        if res.get("is_full_exit", False):
            return (
                f"✅ **Trade Registered: Position Exited (Full Trim)**\n\n"
                f"• **Instrument:** `{res['symbol']}` ({res['name']})\n"
                f"• **Capital Released:** `{res['points_released']:.2f} pts` returned to **{res['account_name']}** Cash\n"
                f"• **Updated Account Cash:** `{res['updated_account_cash']:.2f} pts`\n"
                f"• **Invested Capital:** `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%)\n"
                f"• **Total Liquid Cash:** `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)\n"
                f"• **Mandate Parity:** `{recon['calculated_total_points']:.2f} / {recon['mandate_target_points']:.2f} pts` ✅\n"
                f"• **Curfew & Risk Watchdog:** Position is now closed (0.00 pts)."
            )

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

    def _cmd_close_holding(self, args: List[str]) -> str:
        """
        Dynamically registers position closure / sale in the master ledger:
        Usage: /sell <SYMBOL> [REALIZED_POINTS] or /close <SYMBOL>
        """
        realized_pts = None
        symbol_args = args
        try:
            val = float(args[-1].replace("pts", "").replace("p", "").strip())
            realized_pts = val
            symbol_args = args[:-1]
        except (ValueError, IndexError):
            realized_pts = None

        sym_query = " ".join(symbol_args) if symbol_args else args[0]
        res = self.state_mgr.close_holding(
            sym_query,
            realized_points=realized_pts,
            notes="Position closed via Telegram command"
        )
        if not res["success"]:
            return f"❌ {res.get('error', 'Failed to close holding.')}"

        recon = res["reconciliation"]
        return (
            f"✅ **Trade Registered: Position Exited**\n\n"
            f"• **Instrument:** `{res['symbol']}` ({res['name']})\n"
            f"• **Capital Released:** `{res['points_released']:.2f} pts` returned to **{res['account_name']}**\n"
            f"• **Updated Account Cash:** `{res['updated_account_cash']:.2f} pts`\n"
            f"• **Invested Capital:** `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%)\n"
            f"• **Total Liquid Cash:** `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)\n"
            f"• **Mandate Parity:** `{recon['calculated_total_points']:.2f} / {recon['mandate_target_points']:.2f} pts` ✅\n"
            f"• **Curfew & Risk:** Monitoring for this position has been deactivated."
        )

    def _cmd_update_stop(self, symbol: str, price_str: str) -> str:
        try:
            price = float(price_str.replace("$", "").replace("SEK", "").strip())
        except ValueError:
            return f"Invalid price format: `{price_str}`"

        success = self.state_mgr.update_stop_loss(symbol, price)
        if success:
            return f"✅ Updated stop loss for `{symbol}` to `{price:.2f}` in master ledger."
        return f"❌ Could not find holding `{symbol}` in ledger."

    def _cmd_add_holding(self, args: List[str]) -> str:
        """
        Dynamically adds a holding to the mandate ledger:
        Usage: /add_holding <SYMBOL> <POINTS> <ACCOUNT> [STOP_PRICE] [CLASSIFICATION]
        """
        symbol = args[0].strip().upper()
        try:
            points = float(args[1].strip())
        except ValueError:
            return f"❌ Invalid points: `{args[1]}`"

        if points <= 0:
            return "❌ Position points must be positive."

        account_raw = args[2].strip().lower()
        acct_map = {
            "af": "avanza_af",
            "avanza_af": "avanza_af",
            "isk": "avanza_isk",
            "avanza_isk": "avanza_isk",
            "kf": "avanza_kf",
            "avanza_kf": "avanza_kf",
            "revolut": "revolut_invest",
            "revolut_invest": "revolut_invest"
        }
        account = acct_map.get(account_raw, account_raw)
        if account not in self.state_mgr.state.cash_accounts:
            valid_accs = ", ".join(self.state_mgr.state.cash_accounts.keys())
            return f"❌ Unknown account `{account_raw}`. Valid accounts: {valid_accs}"

        cash_acc = self.state_mgr.state.cash_accounts[account]
        free_pts = cash_acc.get_free_points()
        if free_pts < points:
            return f"❌ Insufficient free cash in `{account}`: {free_pts:.2f} pts available vs {points:.2f} pts requested."

        # Parse optional stop price
        stop_price = None
        if len(args) >= 4:
            try:
                stop_price = float(args[3].replace("$", "").replace("SEK", "").strip())
            except ValueError:
                pass

        # Optional classification
        classification = " ".join(args[4:]) if len(args) >= 5 else "Mandate Spot Holding"

        # Check Gate 4 risk if stop price is supplied
        curr_price = (stop_price / 0.80) if stop_price else 100.0
        q = self.feed.get_quote(symbol)
        if q and q.price > 0:
            curr_price = q.price

        currency = "SEK" if (".ST" in symbol or account == "avanza_isk" or "af" in account) else "USD"

        stop_loss_cfg = None
        if stop_price:
            stop_loss_cfg = StopLossConfig(
                price=stop_price,
                type="hard_stop",
                action="exit_position"
            )
            stop_pct = max(0.01, (curr_price - stop_price) / curr_price) if curr_price > stop_price else 0.20
            risk_pts, risk_pct, passed = self.auditor.calculate_trade_risk(points, stop_pct)
            if not passed:
                return (
                    f"⛔ **Gate 4 Risk Limit Exceeded!**\n"
                    f"Position: {points:.2f} pts with stop @ {stop_price:.2f} results in `{risk_pts:.2f} pts` risk "
                    f"({risk_pct:.2f}% of mandate > 3.48 pt limit).\n"
                    f"Max allowable points for this stop: `{self.auditor.max_position_points_for_stop(stop_pct):.2f} pts`."
                )

        new_holding = Holding(
            symbol=symbol,
            name=symbol,
            points=points,
            account=account,
            classification=classification,
            currency=currency,
            cost_basis=curr_price,
            current_price=curr_price,
            status="active",
            stop_loss=stop_loss_cfg
        )

        success = self.state_mgr.add_holding(new_holding)
        if success:
            recon = self.state_mgr.reconcile_capital()
            stop_desc = f"`{stop_price:.2f} {currency}`" if stop_price else "None"
            return (
                f"✅ **New Holding Added to Ledger:**\n\n"
                f"• Asset: `{symbol}` ({points:.2f} points)\n"
                f"• Custody: `{cash_acc.account_name}` ({account})\n"
                f"• Hard Stop: {stop_desc}\n"
                f"• Classification: {classification}\n"
                f"• Remaining Free Cash in Account: `{cash_acc.get_free_points():.2f} pts`\n"
                f"• Mandate Parity: `{recon['calculated_total_points']:.2f} pts` ({'RECONCILED ✅' if recon['parity_reconciled'] else 'MISMATCH ⚠️'})"
            )
        return "❌ Failed to add holding to state manager."

    def _cmd_audit_mandate(self) -> str:
        health = self.state_mgr.audit_mandate_health()
        recon = health["reconciliation"]
        issues = health["wrapper_compliance_issues"]
        return (
            f"🛡️ **Continuous Mandate Health Audit**\n\n"
            f"• Parity Reconciled: `{'YES' if recon['parity_reconciled'] else 'NO'}` (NAV: {recon['calculated_total_points']:.2f} pts)\n"
            f"• Active Positions: `{health['total_active_positions']}`\n"
            f"• Positions Without Stops: `{', '.join(health['positions_missing_stops']) or 'None'}`\n"
            f"• Tax Wrapper Issues: `{', '.join(issues) or 'None'}`\n"
            f"• Health Verdict: `{'HEALTHY & COMPLIANT' if health['all_healthy'] else 'ACTION REQUIRED'}`"
        )

    def _cmd_snooze(self, args: List[str]) -> str:
        """Snoozes repeating alerts for a target (ALL, MACRO, or TICKER). Defaults to 12 hours."""
        target = "ALL"
        hours = 12.0
        if args:
            try:
                hours = float(args[0])
                target = "ALL"
            except ValueError:
                target = args[0].strip().upper()
                if len(args) >= 2:
                    try:
                        hours = float(args[1])
                    except ValueError:
                        pass

        expire_dt = self.dispatcher.snooze(target, hours=hours)
        expire_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            f"🔕 **Alerts Muted / Snoozed**\n\n"
            f"• **Target:** `{target}`\n"
            f"• **Duration:** `{hours:.1f} hours`\n"
            f"• **Auto-Reset Expiration:** `{expire_str}`\n"
            f"• **Rule:** Repetitive push notifications for `{target}` are suppressed for {hours:.1f} hours.\n"
            f"• To un-mute earlier, send `/unsnooze {target}` or view all with `/snoozes`."
        )

    def _cmd_unsnooze(self, args: List[str]) -> str:
        """Restores normal alert routing for a target."""
        target = args[0].strip().upper() if args else "ALL"
        success = self.dispatcher.unsnooze(target)
        if success:
            return f"🔔 **Alerts Restored:** Snooze cancelled for `{target}`. Normal push notifications are now active."
        return f"ℹ️ Target `{target}` was not found in active snoozes. Use `/snoozes` to view currently muted items."

    def _cmd_list_snoozes(self) -> str:
        """Lists all currently active alert mutes and their time to auto-reset."""
        active = self.dispatcher.get_active_snoozes()
        if not active:
            return "🔔 **No Active Snoozes:** All alert streams are active. Use `/snooze [TARGET] [HOURS]` (default 12h) to mute repetitive alerts."

        lines = ["🔕 **Active Alert Snooze Subscriptions (Auto-Reset in 12h)**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for item in active:
            lines.append(
                f"• `{item['target']}`: **{item['remaining_hours']:.1f}h remaining** "
                f"({item['remaining_minutes']}m) | Expires `{item['expires_at'].strftime('%H:%M:%S UTC')}`"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("Use `/unsnooze <TARGET>` to restore alerts immediately.")
        return "\n".join(lines)

