"""
core/macro_sentinel.py - Macroeconomic Transmission Matrix & Barrier Auditor.
Monitors US 10Y, 30Y, 2Y yields, Brent crude, and Gold against strict macro barriers.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from core.models import MacroMetric, AlertEvent, AlertSeverity, MarketQuote


class MacroSentinel:
    """
    Monitors macroeconomic indicators and enforces portfolio transmission rules.
    2026 Recalibrated Barriers:
    - US 10Y (^TNX): Normal < 5.30%, Stressed 5.30%–5.44%, Crisis >= 5.45%.
    - US 30Y (^TYX) >= 5.45%: Long-duration terminal DCF penalty.
    - US 2Y (^IRX) >= 4.85%: Restrictive liquidity regime.
    - Brent (BZ=F): Normal < $102.00, Stressed $102.00–$109.99, Crisis >= $110.00/bbl.
    - Gold (GC=F) >= $4,450.00/oz: Overbought warning.
    """

    DEFAULT_BARRIERS = {
        "^TNX": {
            "name": "US 10-Year Treasury Yield",
            "neutral_low": 5.00,
            "neutral_high": 5.25,
            "stressed_barrier": 5.30,
            "bearish_barrier": 5.45,
            "bullish_barrier": 5.00,
            "unit": "%",
            "breach_directive": "Crisis Regime! US 10Y >= 5.45%. Freeze non-core limit buys & tighten trailing stops."
        },
        "^TYX": {
            "name": "US 30-Year Treasury Yield",
            "neutral_low": 5.05,
            "neutral_high": 5.25,
            "stressed_barrier": 5.35,
            "bearish_barrier": 5.45,
            "bullish_barrier": 4.95,
            "unit": "%",
            "breach_directive": "Long-duration terminal penalty! Compress DCF growth assumptions."
        },
        "^IRX": {
            "name": "US 2-Year Treasury Yield",
            "neutral_low": 4.60,
            "neutral_high": 4.75,
            "stressed_barrier": 4.80,
            "bearish_barrier": 4.85,
            "bullish_barrier": 4.50,
            "unit": "%",
            "breach_directive": "Restrictive liquidity regime! Cap speculative credit and fintech."
        },
        "BZ=F": {
            "name": "Brent Crude Oil",
            "neutral_low": 92.00,
            "neutral_high": 101.99,
            "stressed_barrier": 102.00,
            "bearish_barrier": 110.00,
            "bullish_barrier": 92.00,
            "unit": "USD/bbl",
            "breach_directive": "Stagflationary shock! Brent >= $110.00. Overweight critical materials & activate full defense."
        },
        "GC=F": {
            "name": "Spot Gold",
            "neutral_low": 4250.00,
            "neutral_high": 4350.00,
            "stressed_barrier": 4400.00,
            "bearish_barrier": 4450.00,
            "bullish_barrier": 4290.00,
            "unit": "USD/oz",
            "breach_directive": "Overbought condition! Maintain bullion buffer; halt all additions."
        }
    }

    def __init__(self, custom_barriers: Optional[Dict[str, dict]] = None):
        self.barriers = custom_barriers or self.DEFAULT_BARRIERS
        self.metrics: Dict[str, MacroMetric] = {}

    def evaluate_tri_state_regime(
        self,
        tnx_price: Optional[float] = None,
        brent_price: Optional[float] = None,
        tnx_5d_change_bps: float = 0.0
    ) -> Dict[str, Any]:
        """
        Evaluates the Tri-State Macro Transmission Matrix:
        1. NORMAL_REGIME: ^TNX < 5.30% AND 5d_TNX_change < +15 bps AND BZ=F < $102.00.
        2. STRESSED_REGIME: ^TNX 5.30%–5.44% OR 5d_TNX_change +15 to +24 bps OR BZ=F $102.00–$109.99.
        3. CRISIS_REGIME: ^TNX >= 5.45% OR 5d_TNX_change >= +25 bps OR BZ=F >= $110.00.
        """
        tnx = tnx_price if tnx_price is not None else (self.metrics.get("^TNX").current_value if "^TNX" in self.metrics else 5.20)
        brent = brent_price if brent_price is not None else (self.metrics.get("BZ=F").current_value if "BZ=F" in self.metrics else 95.0)

        crisis_triggers = []
        stressed_triggers = []

        if tnx is not None and tnx >= 5.45:
            crisis_triggers.append(f"US 10Y Yield at {tnx:.2f}% >= 5.45% Crisis Barrier")
        if tnx_5d_change_bps >= 25.0:
            crisis_triggers.append(f"US 10Y 5-Day Velocity at +{tnx_5d_change_bps:.1f} bps >= +25 bps Crisis Surge")
        if brent is not None and brent >= 110.0:
            crisis_triggers.append(f"Brent Crude at ${brent:.2f}/bbl >= $110.00 Crisis Barrier")

        if crisis_triggers:
            return {
                "regime": "CRISIS_REGIME",
                "shield_active": True,
                "triggers": crisis_triggers,
                "directives": [
                    "1. Activate Full Defensive Shield.",
                    "2. Freeze all new high-beta limit buys.",
                    "3. Ratchet trailing stops across Buckets 1, 2, and 3.",
                    "4. Preserve unencumbered cash in Avanza AF (risk-free yield buffer)."
                ],
                "derivative_cap_multiplier": 0.0,
                "stop_tightening_pct": 0.30
            }

        if (tnx is not None and 5.30 <= tnx < 5.45):
            stressed_triggers.append(f"US 10Y Yield at {tnx:.2f}% in Stressed Band (5.30%–5.44%)")
        if 15.0 <= tnx_5d_change_bps < 25.0:
            stressed_triggers.append(f"US 10Y 5-Day Velocity at +{tnx_5d_change_bps:.1f} bps in Stressed Band (+15 to +24 bps)")
        if brent is not None and 102.0 <= brent < 110.0:
            stressed_triggers.append(f"Brent Crude at ${brent:.2f}/bbl in Stressed Band ($102.00–$109.99)")

        if stressed_triggers:
            return {
                "regime": "STRESSED_REGIME",
                "shield_active": False,
                "triggers": stressed_triggers,
                "directives": [
                    "1. Halve derivative trade caps to 2.50 pts.",
                    "2. Tighten Bucket 1 & 2 stop losses by 20%.",
                    "3. Exercise heightened discipline on entry multiples."
                ],
                "derivative_cap_multiplier": 0.50,
                "stop_tightening_pct": 0.20
            }

        return {
            "regime": "NORMAL_REGIME",
            "shield_active": False,
            "triggers": [],
            "directives": [
                "Full operational capital allocation active.",
                "Tactical swing entries and compounder dip buys permitted."
            ],
            "derivative_cap_multiplier": 1.0,
            "stop_tightening_pct": 0.0
        }

    def check_embargo(self, current_dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Checks whether the current Stockholm time falls inside a scheduled Macro Embargo window:
        1. 14:15–14:40 CEST (Mon-Fri): US Tier-1 Releases (CPI, PPI, NFP, Retail Sales, GDP).
        2. 19:45–20:30 CEST (Wednesdays): FOMC Rate Decisions & Press Conferences.
        """
        import pytz
        tz = pytz.timezone("Europe/Stockholm")
        now_dt = current_dt or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = pytz.utc.localize(now_dt)
        loc_dt = now_dt.astimezone(tz)

        weekday = loc_dt.weekday() # 0 = Monday, 6 = Sunday
        hour = loc_dt.hour
        minute = loc_dt.minute
        time_minutes = hour * 60 + minute

        # 1. Mon-Fri 14:15 - 14:40 (855 - 880 minutes)
        if 0 <= weekday <= 4:
            if 14 * 60 + 15 <= time_minutes <= 14 * 60 + 40:
                return True, "US Tier-1 Macro Release (14:15–14:40 CEST)"

        # 2. Wednesday FOMC 19:45 - 20:30 (1185 - 1230 minutes)
        if weekday == 2: # Wednesday
            if 19 * 60 + 45 <= time_minutes <= 20 * 60 + 30:
                return True, "FOMC Rate Decision & Press Conference (19:45–20:30 CEST)"

        return False, "No active macro embargo."

    def update_quote(self, symbol: str, price: float) -> Tuple[MacroMetric, Optional[AlertEvent]]:
        """
        Updates a macro indicator and evaluates barrier triggers.
        """
        cfg = self.barriers.get(symbol)
        if not cfg:
            cfg = {
                "name": symbol,
                "neutral_low": 0.0,
                "neutral_high": 100.0,
                "stressed_barrier": 95.0,
                "bearish_barrier": 100.0,
                "bullish_barrier": 0.0,
                "unit": "",
                "breach_directive": ""
            }

        status = "neutral"
        alert: Optional[AlertEvent] = None

        if price >= cfg["bearish_barrier"]:
            status = "bearish_breach"
            severity = AlertSeverity.P2 if (symbol in ["^TNX", "BZ=F"]) else AlertSeverity.P1
            alert = AlertEvent(
                id=f"macro_bearish_{symbol}_{datetime.now(timezone.utc).timestamp()}",
                severity=severity,
                symbol=symbol,
                title=f"🚨 MACRO CRISIS BARRIER BREACH: {cfg['name']} ({symbol})",
                message=(
                    f"MACRO CRITICAL THRESHOLD BREACHED!\n"
                    f"{cfg['name']}: {price:.2f} {cfg['unit']} >= Crisis Barrier: {cfg['bearish_barrier']:.2f} {cfg['unit']}.\n"
                    f"Directive: {cfg.get('breach_directive', 'Review mandate exposures.')}"
                ),
                source="macro_sentinel",
                metadata={"symbol": symbol, "price": price, "barrier": cfg["bearish_barrier"]}
            )
        elif "stressed_barrier" in cfg and price >= cfg["stressed_barrier"]:
            status = "stressed_breach"
            alert = AlertEvent(
                id=f"macro_stressed_{symbol}_{datetime.now(timezone.utc).timestamp()}",
                severity=AlertSeverity.P1,
                symbol=symbol,
                title=f"⚠️ MACRO STRESSED BARRIER: {cfg['name']} ({symbol})",
                message=(
                    f"Macro stressed barrier reached: {cfg['name']} at {price:.2f} {cfg['unit']} "
                    f">= {cfg['stressed_barrier']:.2f} {cfg['unit']}. Derivatives capped to 2.50 pts."
                ),
                source="macro_sentinel",
                metadata={"symbol": symbol, "price": price, "barrier": cfg["stressed_barrier"]}
            )
        elif price <= cfg["bullish_barrier"]:
            status = "bullish_relief"
            alert = AlertEvent(
                id=f"macro_relief_{symbol}_{datetime.now(timezone.utc).timestamp()}",
                severity=AlertSeverity.P0,
                symbol=symbol,
                title=f"🟢 MACRO RELIEF BARRIER: {cfg['name']} ({symbol})",
                message=(
                    f"Bullish relief barrier crossed: {cfg['name']} at {price:.2f} {cfg['unit']} "
                    f"<= {cfg['bullish_barrier']:.2f} {cfg['unit']}."
                ),
                source="macro_sentinel",
                metadata={"symbol": symbol, "price": price, "barrier": cfg["bullish_barrier"]}
            )

        metric = MacroMetric(
            symbol=symbol,
            name=cfg["name"],
            current_value=price,
            neutral_low=cfg["neutral_low"],
            neutral_high=cfg["neutral_high"],
            bearish_barrier=cfg["bearish_barrier"],
            bullish_barrier=cfg["bullish_barrier"],
            unit=cfg["unit"],
            status=status,
            last_updated=datetime.now(timezone.utc)
        )
        self.metrics[symbol] = metric
        return metric, alert

    def format_macro_matrix(self) -> str:
        """
        Formats markdown table for the /macro command.
        """
        if not self.metrics:
            return "No macro data collected yet."

        tnx_px = self.metrics.get("^TNX").current_value if "^TNX" in self.metrics else None
        brent_px = self.metrics.get("BZ=F").current_value if "BZ=F" in self.metrics else None
        regime_info = self.evaluate_tri_state_regime(tnx_px, brent_px)

        regime_emoji = "🟢" if regime_info["regime"] == "NORMAL_REGIME" else ("🟡" if regime_info["regime"] == "STRESSED_REGIME" else "🔴")

        lines = [
            f"📊 **Macroeconomic Transmission Matrix** ({regime_emoji} `{regime_info['regime']}`)",
            f"• **Active Directives:** {', '.join(regime_info['directives'][:2])}",
            "",
            "| Indicator | Current | Neutral Band | Stressed | Crisis Barrier | Status |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |"
        ]
        for sym, m in self.metrics.items():
            cfg = self.barriers.get(sym, {})
            stressed_str = f">={cfg.get('stressed_barrier', m.bearish_barrier):.2f}"
            status_icon = "🟢" if m.status == "bullish_relief" else ("🔴" if m.status == "bearish_breach" else ("🟡" if m.status == "stressed_breach" else "⚪"))
            lines.append(
                f"| {m.name} | `{m.current_value:.2f} {m.unit}` | "
                f"{m.neutral_low:.2f} - {m.neutral_high:.2f} | "
                f"{stressed_str} {m.unit} | "
                f">={m.bearish_barrier:.2f} {m.unit} | {status_icon} {m.status} |"
            )
        return "\n".join(lines)
