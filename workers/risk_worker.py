"""
workers/risk_worker.py - Periodic Risk, Stops, and Curfew Watchdog Worker.
Audits the 14 active stops, trims, resting orders, and 19:45 CEST OMX derivative curfew.
Features state-transition macro alerting, progressive stop-loss filtering, and sleep-resume recovery.
"""

from __future__ import annotations
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from core.models import Holding, MarketQuote, AlertEvent, AlertSeverity
from core.gate_auditor import GateAuditor
from core.curfew_manager import CurfewManager
from core.state_manager import StateManager
from feeds.base_feed import BaseFeed
from dispatchers.composite_dispatcher import CompositeDispatcher

logger = logging.getLogger(__name__)


class RiskWorker:
    """
    Dedicated worker continuously monitoring:
    1. Active stop-loss orders across all holdings (with progressive drop filtering).
    2. Active profit trims and limit sells.
    3. Resting limit buy orders (INVE B).
    4. Watchlist trigger points (VST).
    5. Curfew deadlines on derivative holdings (MINI L OMX AVA 1615).
    6. Macro Defensive Shield state transitions (alerts only on state flips).
    7. Sleep-wake discontinuity recovery via PowerManager.
    """

    def __init__(
        self,
        state_mgr: StateManager,
        gate_auditor: GateAuditor,
        curfew_mgr: CurfewManager,
        feed: BaseFeed,
        dispatcher: CompositeDispatcher,
        interval_seconds: int = 60,
        watch_mgr: Optional[Any] = None,
        session_mgr: Optional[Any] = None,
        quant_risk: Optional[Any] = None,
        macro_sentinel: Optional[Any] = None,
        power_mgr: Optional[Any] = None
    ):
        self.state_mgr = state_mgr
        self.auditor = gate_auditor
        self.curfew_mgr = curfew_mgr
        self.feed = feed
        self.dispatcher = dispatcher
        self.interval = interval_seconds
        self.watch_mgr = watch_mgr
        self.session_mgr = session_mgr
        self.quant_risk = quant_risk
        self.macro_sentinel = macro_sentinel
        self.power_mgr = power_mgr
        self.last_shield_active: Optional[bool] = None
        self.last_alerted_stop_prices: Dict[str, float] = {}
        self._running = False

    def run_single_audit_cycle(self, override_dt: Optional[datetime] = None) -> List[AlertEvent]:
        """
        Executes one full audit pass across the entire portfolio state.
        Returns all generated alert events.
        """
        alerts: List[AlertEvent] = []
        holdings = self.state_mgr.get_all_active_holdings()

        # Collect symbols to fetch
        symbols = [h.symbol for h in holdings]
        for ro in self.state_mgr.state.resting_orders:
            symbols.append(ro.symbol)
        for w in self.state_mgr.state.watchlist:
            symbols.append(w.symbol)
        if self.watch_mgr:
            for wt in self.watch_mgr.get_active_targets():
                symbols.append(wt.symbol)
        symbols.extend(["^TNX", "BZ=F"])

        quotes = self.feed.get_batch_quotes(list(set(symbols)))

        # 1. Audit active holdings for stops, trims, and curfew
        for h in holdings:
            q = quotes.get(h.symbol)
            if q:
                # Audit stop loss with progressive worsening check
                stop_alert = self.auditor.audit_holding_stop_loss(h, q)
                if stop_alert:
                    sym = h.symbol
                    curr_px = q.price
                    prev_alert_px = self.last_alerted_stop_prices.get(sym)
                    should_emit_stop = True

                    if prev_alert_px is not None and prev_alert_px > 0:
                        # Only re-emit if price has deteriorated by >= 2.0% from previous alerted price
                        drop = (prev_alert_px - curr_px) / prev_alert_px
                        if drop < 0.02:
                            should_emit_stop = False

                    if should_emit_stop:
                        self.last_alerted_stop_prices[sym] = curr_px
                        alerts.append(stop_alert)

                # Audit profit trims
                trim_alerts = self.auditor.audit_holding_profit_trims(h, q)
                for ta in trim_alerts:
                    alerts.append(ta)

            # Audit Curfew (uses clock rather than quote price)
            curfew_alert = self.curfew_mgr.check_derivative_curfew(h, current_dt=override_dt)
            if curfew_alert:
                alerts.append(curfew_alert)

            # Audit T-48h and T-12h Pre-Flight Protocol 4 earnings
            earnings_alert = self.auditor.audit_earnings_preflight(h, current_dt=override_dt)
            if earnings_alert:
                alerts.append(earnings_alert)

        # Audit stop coverage (ensure zero unprotected equity holdings)
        unprotected_alerts = self.auditor.audit_stop_coverage(holdings)
        for ua in unprotected_alerts:
            alerts.append(ua)

        # 2. Audit resting orders
        for ro in self.state_mgr.state.resting_orders:
            q = quotes.get(ro.symbol)
            if q:
                ro_alert = self.auditor.audit_resting_order(ro, q)
                if ro_alert:
                    alerts.append(ro_alert)

        # 3. Audit watchlist triggers
        for w in self.state_mgr.state.watchlist:
            q = quotes.get(w.symbol)
            if q:
                w_alerts = self.auditor.audit_watchlist_target(w, q)
                for wa in w_alerts:
                    alerts.append(wa)

        # 4. Audit active watch targets & expirations
        if self.watch_mgr:
            for wt in self.watch_mgr.get_active_targets():
                q = quotes.get(wt.symbol)
                if q:
                    w_alerts = self.watch_mgr.evaluate_quote(wt.symbol, q.price, current_dt=override_dt)
                    for wa in w_alerts:
                        alerts.append(wa)
            exp_alerts = self.watch_mgr.check_expirations(current_dt=override_dt)
            for ea in exp_alerts:
                alerts.append(ea)

        # 5. Audit scheduled briefings (Morning 08:30 CEST, Evening 22:15 CEST)
        if self.session_mgr:
            briefing_alerts = self.session_mgr.check_scheduled_briefings(
                self.state_mgr, self.feed, self.macro_sentinel, current_dt=override_dt
            )
            for ba in briefing_alerts:
                alerts.append(ba)

        # 6. Audit Macro Defensive Shield (State-Transition Triggering)
        if self.quant_risk:
            macro_quotes = {}
            for sym in ["^TNX", "BZ=F"]:
                q = quotes.get(sym)
                if q:
                    macro_quotes[sym] = q.price
            shield_res = self.quant_risk.evaluate_macro_shield(macro_quotes)
            curr_active = bool(shield_res.get("shield_active", False))
            dt_str = override_dt.strftime('%Y%m%d_%H%M%S') if override_dt else datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')

            if self.last_shield_active is None:
                # Startup: record state; if breached, emit initial notification once
                self.last_shield_active = curr_active
                if curr_active:
                    alerts.append(AlertEvent(
                        id=f"macro_shield_{dt_str}",
                        severity=AlertSeverity.P2,
                        symbol="MACRO",
                        title="🚨 AUTOMATED MACRO DEFENSIVE SHIELD TRIGGERED (ACTIVATED)",
                        message=(
                            "Critical macro bearish barrier(s) crossed:\n" +
                            "\n".join([f"• {b}" for b in shield_res["breaches"]]) +
                            "\nDirectives: Freeze limit buys, tighten trailing stops on high-beta clusters."
                        ),
                        source="quant_risk",
                        metadata={"breaches": shield_res["breaches"], "status": "DEFENSIVE_SHIELD_ACTIVE"}
                    ))
            elif curr_active and not self.last_shield_active:
                # Transition: NORMAL -> DEFENSIVE
                self.last_shield_active = True
                alerts.append(AlertEvent(
                    id=f"macro_shield_{dt_str}",
                    severity=AlertSeverity.P2,
                    symbol="MACRO",
                    title="🚨 AUTOMATED MACRO DEFENSIVE SHIELD ACTIVATED",
                    message=(
                        "Critical macro bearish barrier(s) crossed:\n" +
                        "\n".join([f"• {b}" for b in shield_res["breaches"]]) +
                        "\nDirectives: Freeze limit buys, tighten trailing stops on high-beta clusters."
                    ),
                    source="quant_risk",
                    metadata={"breaches": shield_res["breaches"], "status": "DEFENSIVE_SHIELD_ACTIVE"}
                ))
            elif not curr_active and self.last_shield_active:
                # Transition: DEFENSIVE -> NORMAL
                self.last_shield_active = False
                alerts.append(AlertEvent(
                    id=f"macro_shield_deactivated_{dt_str}",
                    severity=AlertSeverity.P1,
                    symbol="MACRO",
                    title="🟢 MACRO DEFENSIVE SHIELD DEACTIVATED (Normal Regime Restored)",
                    message=(
                        "Macro indicators have recovered within neutral bands.\n"
                        "Directives: Normal Gate 4 deployment parameters restored."
                    ),
                    source="quant_risk",
                    metadata={"status": "NORMAL_REGIME"}
                ))

        # 7. Portfolio High-Water Mark & Drawdown Watchdog
        if self.quant_risk:
            recon = self.state_mgr.reconcile_capital()
            hwm_res = self.quant_risk.update_high_water_mark(recon["calculated_total_points"])
            if hwm_res.get("alert_level"):
                dt_str = override_dt.strftime('%Y%m%d_%H%M%S') if override_dt else datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                sev = AlertSeverity.P2 if hwm_res["alert_level"] == "EMERGENCY_P2" else (
                    AlertSeverity.P1 if hwm_res["alert_level"] == "WARNING_P1" else AlertSeverity.P0
                )
                alerts.append(AlertEvent(
                    id=f"drawdown_{dt_str}",
                    severity=sev,
                    symbol="MANDATE",
                    title=f"⚠️ PORTFOLIO DRAWDOWN WATCHDOG: {hwm_res['drawdown_pct']:.2f}% DRAWDOWN",
                    message=(
                        f"Mandate drawdown has reached {hwm_res['drawdown_pct']:.2f}% from High-Water Mark.\n"
                        f"• Peak NAV: `{hwm_res['high_water_mark']:.2f} pts`\n"
                        f"• Current NAV: `{hwm_res['current_nav']:.2f} pts`\n"
                        f"• Drawdown: `-{hwm_res['drawdown_pts']:.2f} pts` ({hwm_res['alert_level']})\n"
                        f"Action: Review Gate 4 risk budget and capital conservation rules."
                    ),
                    source="quant_risk",
                    metadata=hwm_res
                ))

        if alerts:
            self.dispatcher.dispatch_batch(alerts, current_dt=override_dt)

        return alerts

    async def start_loop(self) -> None:
        """Asynchronous execution loop for the background worker with sleep-resume watchdog."""
        self._running = True
        logger.info(f"RiskWorker launched (polling cycle: {self.interval}s).")
        while self._running:
            try:
                if self.power_mgr:
                    gap = self.power_mgr.check_discontinuity(expected_max_interval=float(self.interval * 3))
                    if gap:
                        logger.info(f"RiskWorker: Resumed execution after sleep gap ({gap:.0f}s). Re-syncing feeds...")
                        if hasattr(self.feed, "clear_cache"):
                            self.feed.clear_cache()
                self.run_single_audit_cycle()
            except Exception as e:
                logger.error(f"Error during risk audit cycle: {e}")
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self._running = False
