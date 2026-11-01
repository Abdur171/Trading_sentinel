"""
core/derivatives_desk.py - High-Risk Tactical Derivatives & Warrant Desk.

Implements:
1. Strict Capital Allocation Cap:
   - Max 5.00 points single-trade allocation.
   - Max 15.00 points aggregate derivative sleeve.
2. Mandatory -40% Hard Stop on Warrant Price:
   - Downside loss strictly capped at <= 2.00 points (5.00 pts * 0.40 = 2.00 pts <= 3.48 pt limit).
3. Gearing (Effective Leverage Multiplier):
   - gearing = (underlying_price / warrant_price) * parity_ratio
4. Barrier Distance Tracking (Financing Level / Stop Loss Proximity):
   - barrier_distance_pct = abs(underlying_price - financing_level) / underlying_price * 100
5. Stockholm 19:45 CEST Mandatory Flat-Book Curfew:
   - 19:30 CEST: Advisory (P0)
   - 19:40 CEST: High Warning (P1)
   - 19:45 CEST: Mandatory Flat-Book Breach (P2)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Dict, List, Optional, Any, Tuple
import pytz

from core.models import Holding, AlertEvent, AlertSeverity
from core.state_manager import StateManager
from core.curfew_manager import CurfewManager

logger = logging.getLogger(__name__)


@dataclass
class WarrantMetrics:
    """Quantitative risk and leverage profile for a derivative warrant or option."""
    symbol: str
    underlying_price: float
    warrant_price: float
    financing_level: float
    parity_ratio: float
    gearing: float
    barrier_distance_pct: float
    hard_stop_price: float
    hard_stop_pct: float
    allocated_points: float
    max_loss_pts: float
    passed_cap: bool
    curfew_status: str
    is_stop_breached: bool = False
    directive: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying_price": self.underlying_price,
            "warrant_price": self.warrant_price,
            "financing_level": self.financing_level,
            "parity_ratio": self.parity_ratio,
            "gearing": self.gearing,
            "barrier_distance_pct": self.barrier_distance_pct,
            "hard_stop_price": self.hard_stop_price,
            "hard_stop_pct": self.hard_stop_pct,
            "allocated_points": self.allocated_points,
            "max_loss_pts": self.max_loss_pts,
            "passed_cap": self.passed_cap,
            "curfew_status": self.curfew_status,
            "is_stop_breached": self.is_stop_breached,
            "directive": self.directive,
        }


class DerivativesDesk:
    """
    Tactical Derivatives Desk governing high-risk warrants (e.g. MINI L OMX) and options.
    Enforces the 5.00 pt capital cap, -40% hard stop, gearing metrics, and 19:45 CEST curfew.
    """

    MAX_ALLOCATION_PTS = 5.00         # Single-trade derivative allocation cap
    MAX_SLEEVE_PTS = 15.00            # Total active derivative exposure cap
    HARD_STOP_PCT = 0.40              # -40% hard stop on warrant price
    MAX_DOWNSIDE_LOSS_PTS = 2.00      # 5.00 pts * 0.40 = 2.00 pts max risk
    CURFEW_CUTOFF_TIME = "19:45"      # 19:45 CEST mandatory flat book
    CURFEW_WARNING_1 = "19:30"        # 19:30 CEST advisory
    CURFEW_WARNING_2 = "19:40"        # 19:40 CEST urgent liquidation warning
    TIMEZONE_STR = "Europe/Stockholm"

    def __init__(
        self,
        state_mgr: Optional[StateManager] = None,
        curfew_mgr: Optional[CurfewManager] = None
    ):
        self.state_mgr = state_mgr
        self.curfew_mgr = curfew_mgr or CurfewManager()
        self.tz = pytz.timezone(self.TIMEZONE_STR)

    # -------------------------------------------------------------------------
    # 1. Gearing & Barrier Distance Analytics
    # -------------------------------------------------------------------------

    @staticmethod
    def calculate_gearing(
        underlying_price: float,
        warrant_price: float,
        parity_ratio: float = 1.0
    ) -> float:
        """
        Calculates effective leverage (Gearing) for a warrant:
        Gearing = (underlying_price / warrant_price) * parity_ratio
        """
        if warrant_price <= 0 or underlying_price <= 0:
            return 0.0
        return round((underlying_price / warrant_price) * parity_ratio, 2)

    @staticmethod
    def calculate_barrier_distance(
        underlying_price: float,
        financing_level: float
    ) -> float:
        """
        Calculates the percentage buffer distance to financing level (stop loss / knockout):
        barrier_distance_pct = abs(underlying_price - financing_level) / underlying_price * 100
        """
        if underlying_price <= 0:
            return 0.0
        return round(abs(underlying_price - financing_level) / underlying_price * 100.0, 2)

    @staticmethod
    def calculate_hard_stop(
        entry_price: float,
        stop_pct: float = HARD_STOP_PCT
    ) -> float:
        """
        Calculates the mandatory -40% stop loss price for a warrant:
        Stop Price = entry_price * (1.0 - stop_pct)
        """
        if entry_price <= 0:
            return 0.0
        return round(entry_price * (1.0 - stop_pct), 2)

    @staticmethod
    def calculate_synthetic_price(
        underlying_price: float,
        financing_level: float,
        parity: float = 10.0,
        fx_rate: float = 10.5
    ) -> float:
        """
        Computes synthetic warrant price (SEK) from underlying USD spot:
        Intrinsic Value (USD) = max(0.0, spot_usd - financing_level)
        Warrant Price (SEK) = (Intrinsic Value / parity) * fx_rate
        """
        intrinsic_usd = max(0.0, underlying_price - financing_level)
        if parity <= 0:
            return 0.0
        return round((intrinsic_usd / parity) * fx_rate, 2)

    @staticmethod
    def calibrate_warrant_parameters(
        warrant_price_sek: float,
        leverage: float,
        stop_barrier: Optional[float] = None,
        parity: float = 10.0,
        fx_rate: float = 10.5,
        underlying_usd: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Reverse-engineers financing level and barrier parameters from user inputs:
        Implied Underlying = Price_USD
        Financing Level = Underlying * (1 - 1 / Leverage)
        """
        if underlying_usd and underlying_usd > 0:
            implied_underlying = underlying_usd
        else:
            if fx_rate <= 0:
                fx_rate = 10.5
            implied_underlying = round((warrant_price_sek * leverage * parity) / fx_rate, 2)

        if leverage > 0:
            financing_level = round(implied_underlying * (1.0 - (1.0 / leverage)), 2)
        else:
            financing_level = 0.0

        barrier = stop_barrier if stop_barrier is not None else financing_level

        return {
            "implied_underlying": implied_underlying,
            "financing_level": financing_level,
            "stop_barrier": barrier,
            "leverage": leverage,
            "parity": parity,
            "fx_rate": fx_rate
        }

    @staticmethod
    def evaluate_barrier_proximity(
        spot_price: float,
        barrier: float,
        atr_1h: float = 2.0
    ) -> Dict[str, Any]:
        """
        Monitors proximity of underlying spot price to the knock-out barrier:
        - If spot - barrier <= 1.5 * ATR_1h: P1 Knock-Out Proximity Warning.
        - If spot - barrier <= 0.75 * ATR_1h: P2 Emergency Knock-Out Directive.
        """
        distance = spot_price - barrier
        dist_pct = round((distance / spot_price) * 100.0, 2) if spot_price > 0 else 0.0

        if distance <= (0.75 * atr_1h):
            alert_level = "P2"
            action = "EXIT_IMMEDIATELY"
            directive = (
                f"🚨 P2 EMERGENCY KNOCK-OUT DIRECTIVE: Spot (${spot_price:.2f}) is within {distance:.2f} "
                f"(<= 0.75x ATR = {0.75*atr_1h:.2f}) of barrier (${barrier:.2f})! Exit immediately before knock-out."
            )
        elif distance <= (1.5 * atr_1h):
            alert_level = "P1"
            action = "TIGHTEN_OR_PREPARE_EXIT"
            directive = (
                f"⚠️ P1 KNOCK-OUT PROXIMITY WARNING: Spot (${spot_price:.2f}) is within {distance:.2f} "
                f"(<= 1.5x ATR = {1.5*atr_1h:.2f}) of barrier (${barrier:.2f}). Tighten stops or reduce size."
            )
        else:
            alert_level = "P0"
            action = "HOLD"
            directive = f"Normal barrier buffer: {dist_pct:.1f}% ({distance:.2f} USD)."

        return {
            "spot_price": spot_price,
            "barrier": barrier,
            "distance": round(distance, 2),
            "distance_pct": dist_pct,
            "atr_1h": atr_1h,
            "alert_level": alert_level,
            "action": action,
            "directive": directive
        }

    # -------------------------------------------------------------------------
    # 2. Gate 4 Sizing & Loss Cap Auditing
    # -------------------------------------------------------------------------

    def audit_derivative_sizing(
        self,
        symbol: str,
        points: float,
        current_sleeve_points: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Audits derivative sizing constraints:
        1. Single-trade capital allocation <= 5.00 points.
        2. Downside stop of -40% guarantees max capital loss <= 2.00 points.
        3. Aggregate derivative sleeve <= 15.00 points.
        """
        # 1. Single trade cap
        if points > (self.MAX_ALLOCATION_PTS + 1e-4):
            return {
                "passed": False,
                "symbol": symbol,
                "points": points,
                "max_allowed": self.MAX_ALLOCATION_PTS,
                "max_loss_pts": round(points * self.HARD_STOP_PCT, 2),
                "reason": (
                    f"Gate 4 Breach: Derivative size ({points:.2f} pts) exceeds "
                    f"the strict {self.MAX_ALLOCATION_PTS:.2f} pt allocation cap."
                )
            }

        # 2. Total sleeve cap
        if current_sleeve_points is not None:
            total_projected = current_sleeve_points + points
            if total_projected > (self.MAX_SLEEVE_PTS + 1e-4):
                return {
                    "passed": False,
                    "symbol": symbol,
                    "points": points,
                    "current_sleeve": current_sleeve_points,
                    "max_sleeve": self.MAX_SLEEVE_PTS,
                    "max_loss_pts": round(points * self.HARD_STOP_PCT, 2),
                    "reason": (
                        f"Gate 4 Sleeve Breach: Active derivative sleeve ({total_projected:.2f} pts) "
                        f"would exceed {self.MAX_SLEEVE_PTS:.2f} pt limit."
                    )
                }

        # 3. Maximum loss at -40% hard stop
        max_loss_pts = round(points * self.HARD_STOP_PCT, 2)
        # Verify loss is capped at <= 2.00 pts
        loss_capped = max_loss_pts <= (self.MAX_DOWNSIDE_LOSS_PTS + 1e-4)

        return {
            "passed": True,
            "symbol": symbol,
            "points": round(points, 2),
            "max_loss_pts": max_loss_pts,
            "stop_pct": round(self.HARD_STOP_PCT * 100, 1),
            "loss_capped_at_2pt": loss_capped,
            "reason": (
                f"Compliant: {points:.2f} pts derivative within 5.00 pt cap. "
                f"Mandatory -40% stop loss guarantees max loss <= {max_loss_pts:.2f} pts (under 2.00 pt cap)."
            )
        }

    # -------------------------------------------------------------------------
    # 3. Stockholm 19:45 CEST Curfew Watchdog
    # -------------------------------------------------------------------------

    def check_curfew(
        self,
        current_dt: Optional[datetime] = None,
        holding: Optional[Holding] = None
    ) -> Dict[str, Any]:
        """
        Audits current time against the 19:45 CEST Stockholm curfew:
        - < 19:30: NORMAL
        - 19:30 - 19:39: ADVISORY (P0)
        - 19:40 - 19:44: WARNING (P1)
        - >= 19:45: BREACH / MANDATORY FLAT BOOK (P2)
        """
        loc_now = self.curfew_mgr.get_localized_time(current_dt)
        curr_time = loc_now.time()

        cutoff = time(19, 45, 0)
        w1 = time(19, 30, 0)
        w2 = time(19, 40, 0)

        is_weekday = loc_now.weekday() < 5
        active_points = holding.points if holding else 0.0

        if not is_weekday:
            return {
                "status": "WEEKEND",
                "severity": AlertSeverity.P0,
                "time_str": loc_now.strftime("%H:%M:%S CEST"),
                "is_breached": False,
                "message": "Stockholm market closed (weekend). Curfew inactive."
            }

        if curr_time >= cutoff:
            is_breached = active_points > 0
            return {
                "status": "BREACH",
                "severity": AlertSeverity.P2,
                "time_str": loc_now.strftime("%H:%M:%S CEST"),
                "is_breached": is_breached,
                "message": (
                    f"EMERGENCY CURFEW BREACH: Stockholm time is {loc_now.strftime('%H:%M:%S CEST')} "
                    f"(past 19:45 CEST cutoff). Mandatory immediate flat-book liquidation."
                )
            }
        elif curr_time >= w2:
            return {
                "status": "WARNING",
                "severity": AlertSeverity.P1,
                "time_str": loc_now.strftime("%H:%M:%S CEST"),
                "is_breached": False,
                "message": (
                    f"CURFEW WARNING: Stockholm time is {loc_now.strftime('%H:%M:%S CEST')}. "
                    f"5 minutes remaining until 19:45 CEST mandatory liquidation."
                )
            }
        elif curr_time >= w1:
            return {
                "status": "ADVISORY",
                "severity": AlertSeverity.P0,
                "time_str": loc_now.strftime("%H:%M:%S CEST"),
                "is_breached": False,
                "message": (
                    f"Curfew Advisory: Stockholm time is {loc_now.strftime('%H:%M:%S CEST')}. "
                    f"Prepare to close derivative positions prior to 19:45 CEST."
                )
            }

        return {
            "status": "NORMAL",
            "severity": AlertSeverity.P0,
            "time_str": loc_now.strftime("%H:%M:%S CEST"),
            "is_breached": False,
            "message": f"Curfew normal ({loc_now.strftime('%H:%M:%S CEST')}). Active monitoring."
        }

    # -------------------------------------------------------------------------
    # 4. Master Warrant & Option Evaluation
    # -------------------------------------------------------------------------

    def evaluate_warrant(
        self,
        symbol: str,
        underlying_price: float,
        warrant_price: float,
        financing_level: float,
        parity_ratio: float,
        current_points: float,
        entry_price: Optional[float] = None,
        current_time: Optional[datetime] = None
    ) -> WarrantMetrics:
        """
        Comprehensive evaluation of a derivative warrant:
        - Gearing multiplier
        - Barrier distance to financing level
        - 5.00 pt sizing cap audit
        - -40% hard stop price calculation & breach check
        - 19:45 CEST curfew status check
        """
        ref_entry = entry_price if entry_price is not None else warrant_price
        gearing = self.calculate_gearing(underlying_price, warrant_price, parity_ratio)
        barrier_dist = self.calculate_barrier_distance(underlying_price, financing_level)
        hard_stop_price = self.calculate_hard_stop(ref_entry, self.HARD_STOP_PCT)

        # Sizing check
        sizing = self.audit_derivative_sizing(symbol, current_points)
        passed_cap = sizing["passed"]
        max_loss_pts = sizing["max_loss_pts"]

        # Hard stop breach check
        is_stop_breached = (warrant_price <= hard_stop_price) and (warrant_price > 0)

        # Curfew audit
        curfew_res = self.check_curfew(current_dt=current_time)
        curfew_status = curfew_res["status"]

        # Determine directive
        directives: List[str] = []
        if not passed_cap:
            directives.append(f"Gate 4 Breach: Size exceeds {self.MAX_ALLOCATION_PTS:.2f} pt cap")
        if is_stop_breached:
            directives.append(f"HARD STOP BREACH: Warrant price {warrant_price:.2f} <= {hard_stop_price:.2f} (-40%)")
        if curfew_status == "BREACH":
            directives.append("19:45 CEST CURFEW: Mandatory immediate liquidation")
        elif curfew_status == "WARNING":
            directives.append("Curfew Warning: 5 mins to close position")

        if barrier_dist < 2.0:
            directives.append(f"High Knockout Risk: Barrier distance only {barrier_dist:.1f}%")

        directive_str = " | ".join(directives) if directives else "Position compliant and within operational parameters."

        return WarrantMetrics(
            symbol=symbol,
            underlying_price=round(underlying_price, 2),
            warrant_price=round(warrant_price, 2),
            financing_level=round(financing_level, 2),
            parity_ratio=parity_ratio,
            gearing=gearing,
            barrier_distance_pct=barrier_dist,
            hard_stop_price=hard_stop_price,
            hard_stop_pct=round(self.HARD_STOP_PCT * 100, 1),
            allocated_points=round(current_points, 2),
            max_loss_pts=max_loss_pts,
            passed_cap=passed_cap,
            curfew_status=curfew_status,
            is_stop_breached=is_stop_breached,
            directive=directive_str
        )

    # -------------------------------------------------------------------------
    # 5. Formatted Telegram Card Generator
    # -------------------------------------------------------------------------

    def format_derivatives_card(
        self,
        metrics: Optional[WarrantMetrics] = None,
        symbol: str = "MINI L OMX AVA 1615"
    ) -> str:
        """
        Formats a structured Telegram card for `/curfew`, `/gear`, or derivatives briefings.
        """
        if metrics is None:
            # Default evaluation using standard OMX benchmark parameters
            metrics = self.evaluate_warrant(
                symbol=symbol,
                underlying_price=2600.0,
                warrant_price=13.0,
                financing_level=2470.0,
                parity_ratio=0.05,
                current_points=5.00,
                entry_price=13.0
            )

        curfew_icon = "🟢" if metrics.curfew_status == "NORMAL" else ("🟡" if metrics.curfew_status == "ADVISORY" else "🔴")
        stop_icon = "🔴 STOP TRIGGERED" if metrics.is_stop_breached else "🟢 SAFE"

        lines = [
            f"⚡ **DERIVATIVES DESK — {metrics.symbol}**",
            f"_{metrics.directive}_\n",
            f"• **Warrant Price:** `{metrics.warrant_price:.2f} SEK` | **Underlying:** `{metrics.underlying_price:.2f}`",
            f"• **Financing Level:** `{metrics.financing_level:.2f}` | **Barrier Buffer:** `{metrics.barrier_distance_pct:.2f}%`",
            f"• **Effective Gearing:** `{metrics.gearing:.2f}x` (Parity: `{metrics.parity_ratio}`)",
            "",
            "🛡️ **Gate 4 Risk & Curfew Controls:**",
            f"• **Capital Allocated:** `{metrics.allocated_points:.2f} pts` / `{self.MAX_ALLOCATION_PTS:.2f} pts` cap ({'COMPLIANT ✅' if metrics.passed_cap else 'BREACH ⚠️'})",
            f"• **Hard Stop Price:** `{metrics.hard_stop_price:.2f} SEK` (-{metrics.hard_stop_pct:.0f}%) [{stop_icon}]",
            f"• **Max Loss Budget:** `{metrics.max_loss_pts:.2f} pts` (Strictly <= 2.00 pts)",
            f"• **19:45 CEST Curfew:** {curfew_icon} `{metrics.curfew_status}`"
        ]

        return "\n".join(lines)
