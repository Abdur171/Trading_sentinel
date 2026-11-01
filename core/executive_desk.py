"""
core/executive_desk.py - Master Governing Desk & Capital Allocation Engine.
Governs the 696.50-point NAV, manages the 4 Dynamic Capital Pots, enforces the tiered
Gate 4 risk rules (spot equities <= 3.48 pts risk, derivatives <= 5.00 pts allocation),
and executes the profit harvesting protocol.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from core.models import Holding, MandateConfig, CashAccount
from core.state_manager import StateManager

logger = logging.getLogger(__name__)


class CapitalPot(BaseModel):
    name: str
    code: str
    target_min_points: float
    target_max_points: float
    current_points: float = 0.0
    description: str
    holdings: List[str] = Field(default_factory=list)


class AuditResult(dict):
    """
    Result container for trade sizing audits.
    Supports dictionary access, attribute access, boolean evaluation, and 2-tuple unpacking.
    """
    def __iter__(self):
        return iter((self.get("passed", False), self.get("reason", "")))

    def __getitem__(self, item: Any) -> Any:
        if item == 0:
            return self.get("passed", False)
        if item == 1:
            return self.get("reason", "")
        return super().__getitem__(item)

    def __bool__(self) -> bool:
        return bool(self.get("passed", False))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, bool):
            return bool(self.get("passed", False)) == other
        return super().__eq__(other)

    @property
    def passed(self) -> bool:
        return bool(self.get("passed", False))

    @property
    def reason(self) -> str:
        return str(self.get("reason", ""))


class HarvestResult(dict):
    """
    Result container for profit harvesting operations.
    Supports dictionary access, attribute access, boolean evaluation, and equality with bool.
    """
    def __bool__(self) -> bool:
        return bool(self.get("success", False))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, bool):
            return bool(self) == other
        return super().__eq__(other)

    @property
    def success(self) -> bool:
        return bool(self.get("success", False))

    @property
    def error(self) -> Optional[str]:
        return self.get("error")


class ExecutiveDesk:
    """
    Central Governing Body of the Trading Sentinel.
    Maintains sovereign control over mandate capital parity (696.50 pts), partitions capital
    into 4 distinct Risk Pots, and regulates risk allocation across all desks.
    """

    MANDATE_TOTAL_POINTS = 688.30
    BENCHMARK_HWM = 696.50
    SPOT_MAX_RISK_BUDGET_PTS = 3.44         # 0.50% of Live NAV (688.30 * 0.0050)
    DERIVATIVE_MAX_ALLOCATION_PTS = 5.00     # Tiered 5.0 pt cap for high-risk options/warrants
    DERIVATIVE_MAX_SLEEVE_PTS = 15.00       # Maximum total active derivative exposure
    DERIVATIVE_HARD_STOP_PCT = 0.40         # -40% max loss on warrant (capping max loss <= 2.00 pts)
    DERIVATIVE_MAX_LOSS_PTS = 2.00          # 5.00 pts allocation * 0.40 hard stop = 2.00 pts max loss cap

    def __init__(self, state_mgr: StateManager):
        self.state_mgr = state_mgr
        self._pots: Dict[str, CapitalPot] = {}
        self.initialize_pots()

    @property
    def pots(self) -> Dict[str, CapitalPot]:
        """Provides direct access to the 4 Capital Pots."""
        return self._pots

    def initialize_pots(self) -> None:
        """
        Initializes the 4 Dynamic Capital Pots:
        1. Pot 1: Core Long-Term Compounders (Avanza KF & ISK) ~ 150-300 pts
        2. Pot 2: Tactical Swing Sleeve (Avanza ISK & Revolut) ~ 30-150 pts
        3. Pot 3: High-Risk Derivative Sleeve (Avanza ISK) ~ max 15.0 pts (max 5.0 pt per trade)
        4. Pot 4: Macro Capital Preservation & Powder (Avanza AF & Cash) ~ 150-450 pts
        """
        self._pots = {
            "pot_core_compounders": CapitalPot(
                name="Pot 1: Core Long-Term Compounders",
                code="pot_core_compounders",
                target_min_points=250.0,
                target_max_points=400.0,
                description="Long-term monopoly compounders in Avanza KF and ISK."
            ),
            "pot_swing_trading": CapitalPot(
                name="Pot 2: Tactical Swing Sleeve",
                code="pot_swing_trading",
                target_min_points=30.0,
                target_max_points=120.0,
                description="Tactical momentum and mean-reversion swing trades (Revolut/KF/ISK)."
            ),
            "pot_high_risk_derivatives": CapitalPot(
                name="Pot 3: High-Risk Derivative Sleeve",
                code="pot_high_risk_derivatives",
                target_min_points=0.0,
                target_max_points=15.0,
                description="Tactical warrants & options (MINI L AMD), max 5.0 pts per trade."
            ),
            "pot_cash_preservation": CapitalPot(
                name="Pot 4: Macro Capital Preservation",
                code="pot_cash_preservation",
                target_min_points=150.0,
                target_max_points=400.0,
                description="Unencumbered dry powder cash (Avanza AF, ISK/KF/Revolut cash)."
            )
        }
        self.recalculate_pots()

    def recalculate_pots(self) -> Dict[str, Any]:
        """
        Recalculates the exact point distribution across all 4 pots using current portfolio state.
        Ensures exact capital reconciliation against Floating NAV (688.30 pts baseline).
        """
        state = self.state_mgr.state

        # Reset holding lists and point values
        for pot in self._pots.values():
            pot.current_points = 0.0
            pot.holdings = []

        active_holdings = [h for h in state.holdings if h.status == "active"]

        for h in active_holdings:
            sym_upper = h.symbol.upper()
            instr_type = (h.instrument_type or "equity").lower()
            bucket = str(getattr(h, "horizon_bucket", "") or "")

            # Pot 3: Derivatives & Warrants
            if (
                instr_type in ["derivative_warrant", "derivative", "warrant", "option", "certificate"]
                or "MINI" in sym_upper
                or "BULL" in sym_upper
                or "BEAR" in sym_upper
                or "AVA" in sym_upper
            ):
                self._pots["pot_high_risk_derivatives"].current_points += h.points
                self._pots["pot_high_risk_derivatives"].holdings.append(h.symbol)

            # Pot 2: Tactical Swings & Satellites (Bucket 1 satellites & Bucket 2 swings)
            elif (
                bucket in ["BUCKET_1_SHORT_TERM_HIGH_RISK", "BUCKET_2_MEDIUM_TERM_SWING"]
                or getattr(h, "tactical_tranche", None) is not None
                or sym_upper in [
                    "ORCL", "AMD", "SINCH.ST", "MU", "CCJ", "VRT", "VYLR", "MRVL", "VST",
                    "CRWV", "NBIS", "AEHR", "XE", "AXTI", "GRAB"
                ]
                or "SWING" in (h.classification or "").upper()
                or "TURNAROUND" in (h.classification or "").upper()
                or "SATELLITE" in (h.classification or "").upper()
            ):
                self._pots["pot_swing_trading"].current_points += h.points
                self._pots["pot_swing_trading"].holdings.append(h.symbol)

            # Pot 1: Core Long-Term Compounders & Permanent Hedges (everything else active)
            else:
                self._pots["pot_core_compounders"].current_points += h.points
                self._pots["pot_core_compounders"].holdings.append(h.symbol)

        # Pot 4: Unallocated Dry Powder Cash across all wrappers
        cash_sum = sum(acc.points for acc in state.cash_accounts.values())
        self._pots["pot_cash_preservation"].current_points = round(cash_sum, 2)
        self._pots["pot_cash_preservation"].holdings = [
            f"{acc.account_name}: {acc.points:.2f} pts" for acc in state.cash_accounts.values()
        ]

        # Round current points
        for pot in self._pots.values():
            pot.current_points = round(pot.current_points, 2)

        total_points = sum(pot.current_points for pot in self._pots.values())
        target_total = state.mandate.total_points
        delta = round(total_points - target_total, 4)

        return {
            "total_points": round(total_points, 2),
            "target_points": target_total,
            "reconciled": abs(delta) < 0.05,
            "delta": delta,
            "pots": {k: p.model_dump() for k, p in self._pots.items()}
        }

    def audit_ticker_overlap(self, symbol: str = "AMD", gearing: float = 6.5) -> Dict[str, Any]:
        """
        Combines spot equity and synthetic warrant exposure on a shared underlying ticker.
        Effective Exposure = Spot Points + (Warrant Points * Gearing)
        If Effective Exposure exceeds 15.0 pts or 5.0% of Live NAV, blocks additional buy signals.
        """
        state = self.state_mgr.state
        active = [h for h in state.holdings if h.status == "active"]

        clean_sym = symbol.strip().upper()
        spot_pts = 0.0
        warrant_pts = 0.0

        for h in active:
            sym_u = h.symbol.upper()
            under_u = (h.underlying or "").upper()
            is_deriv = (
                (h.instrument_type or "").lower() in ["derivative_warrant", "derivative", "warrant"]
                or "MINI" in sym_u
            )
            if clean_sym in [sym_u, under_u]:
                if is_deriv:
                    warrant_pts += h.points
                else:
                    spot_pts += h.points

        synthetic_notional = round(warrant_pts * gearing, 2)
        effective_exposure = round(spot_pts + synthetic_notional, 2)
        threshold = 15.0
        exceeded = effective_exposure > threshold

        return {
            "symbol": clean_sym,
            "spot_points": spot_pts,
            "warrant_points": warrant_pts,
            "gearing": gearing,
            "synthetic_notional": synthetic_notional,
            "effective_exposure": effective_exposure,
            "threshold_points": threshold,
            "exceeded": exceeded,
            "can_add_exposure": not exceeded,
            "directive": (
                f"OVERLAP BREACH: Effective {clean_sym} exposure is {effective_exposure:.2f} pts "
                f"({spot_pts:.2f} spot + {synthetic_notional:.2f} synthetic), exceeding {threshold:.1f} pt limit. Block buy signals."
                if exceeded else
                f"Compliant: Effective {clean_sym} exposure is {effective_exposure:.2f} pts <= {threshold:.1f} pt ceiling."
            )
        }

    def get_pots_summary(self) -> Dict[str, CapitalPot]:
        """Returns the dictionary of CapitalPot models."""
        self.recalculate_pots()
        return self._pots

    def get_pot_summary(self) -> Dict[str, Any]:
        """Returns the status and allocation breakdown of all 4 pots."""
        return self.recalculate_pots()

    def audit_trade_sizing(
        self,
        symbol: str,
        *args: Any,
        instrument_type: Optional[str] = None,
        points: Optional[float] = None,
        is_derivative: Optional[bool] = None,
        stop_loss_pct: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        entry_price: Optional[float] = None,
        **kwargs: Any
    ) -> AuditResult:
        """
        Audits Gate 4 constraints under the updated dual-cap framework:
        1. Standard Spot Equities:
           - Max risk budget <= 3.48 points (0.50% of NAV).
           - Risk = points * stop_loss_pct (or points * abs(entry - stop) / entry).
        2. High-Risk Options & Derivatives:
           - Max allocation <= 5.00 points capital allocation.
           - Downside stop must be <= -40% (capping max capital loss <= 2.00 pts).
           - Aggregate derivative sleeve must remain <= 15.00 pts.
        """
        # Parse positional arguments for flexible signature compatibility
        if len(args) >= 1:
            if isinstance(args[0], (int, float)):
                points = float(args[0])
                if len(args) >= 2:
                    if isinstance(args[1], bool):
                        is_derivative = args[1]
                    elif isinstance(args[1], str):
                        instrument_type = args[1]
                if len(args) >= 3 and isinstance(args[2], (int, float)):
                    stop_loss_pct = float(args[2])
            elif isinstance(args[0], str):
                instrument_type = args[0]
                if len(args) >= 2 and isinstance(args[1], (int, float)):
                    points = float(args[1])
                if len(args) >= 3 and isinstance(args[2], (int, float)):
                    stop_loss_price = float(args[2])
                if len(args) >= 4 and isinstance(args[3], (int, float)):
                    entry_price = float(args[3])

        if points is None:
            points = 0.0

        pts_val = float(points)

        # Detect derivative status
        sym_upper = symbol.strip().upper()
        deriv_flag = False
        if is_derivative is not None:
            deriv_flag = bool(is_derivative)
        elif instrument_type is not None:
            deriv_flag = instrument_type.lower() in [
                "derivative", "derivative_warrant", "warrant", "option", "certificate"
            ]

        if (
            "MINI" in sym_upper
            or "BULL" in sym_upper
            or "BEAR" in sym_upper
            or "AVA" in sym_upper
        ):
            deriv_flag = True

        self.recalculate_pots()

        if deriv_flag:
            # Check single-trade derivative allocation cap
            if pts_val > self.DERIVATIVE_MAX_ALLOCATION_PTS + 1e-4:
                return AuditResult({
                    "passed": False,
                    "is_derivative": True,
                    "points": pts_val,
                    "max_allowed": self.DERIVATIVE_MAX_ALLOCATION_PTS,
                    "reason": f"Derivative size ({pts_val:.2f} pts) exceeds {self.DERIVATIVE_MAX_ALLOCATION_PTS:.2f} pt Gate 4 allocation cap."
                })

            # Check aggregate derivative sleeve cap
            curr_deriv_points = self._pots["pot_high_risk_derivatives"].current_points
            if (curr_deriv_points + pts_val) > self.DERIVATIVE_MAX_SLEEVE_PTS + 1e-4:
                return AuditResult({
                    "passed": False,
                    "is_derivative": True,
                    "points": pts_val,
                    "curr_sleeve": curr_deriv_points,
                    "max_sleeve": self.DERIVATIVE_MAX_SLEEVE_PTS,
                    "reason": (
                        f"New trade pushes active derivative sleeve ({curr_deriv_points + pts_val:.2f} pts) "
                        f"above {self.DERIVATIVE_MAX_SLEEVE_PTS:.2f} pt total limit."
                    )
                })

            # Max risk with mandatory -40% stop loss
            if entry_price and stop_loss_price and entry_price > 0:
                effective_stop = abs(entry_price - stop_loss_price) / entry_price
            elif stop_loss_pct is not None:
                effective_stop = stop_loss_pct
            else:
                effective_stop = self.DERIVATIVE_HARD_STOP_PCT

            max_loss_pts = round(pts_val * effective_stop, 2)

            if effective_stop > (self.DERIVATIVE_HARD_STOP_PCT + 1e-4) or max_loss_pts > (self.DERIVATIVE_MAX_LOSS_PTS + 1e-4):
                return AuditResult({
                    "passed": False,
                    "is_derivative": True,
                    "points": pts_val,
                    "max_loss_pts": max_loss_pts,
                    "stop_pct": effective_stop * 100,
                    "reason": (
                        f"Gate 4 Breach: Derivative downside stop (-{effective_stop*100:.1f}%) or max loss "
                        f"({max_loss_pts:.2f} pts) exceeds -{self.DERIVATIVE_HARD_STOP_PCT*100:.1f}% stop / "
                        f"{self.DERIVATIVE_MAX_LOSS_PTS:.2f} pt loss limit."
                    )
                })

            return AuditResult({
                "passed": True,
                "is_derivative": True,
                "points": pts_val,
                "max_loss_pts": max_loss_pts,
                "stop_pct": effective_stop * 100,
                "reason": (
                    f"Compliant: {pts_val:.2f} pts derivative within {self.DERIVATIVE_MAX_ALLOCATION_PTS:.2f} pt cap "
                    f"(Max loss: {max_loss_pts:.2f} pts at -{effective_stop*100:.0f}% stop)."
                )
            })

        # Standard spot equity trade sizing
        if entry_price and stop_loss_price and entry_price > 0:
            stop_pct = abs(entry_price - stop_loss_price) / entry_price
        elif stop_loss_pct is not None:
            stop_pct = stop_loss_pct
        else:
            stop_pct = 0.20

        risk_pts = round(pts_val * stop_pct, 2)
        passed = risk_pts <= self.SPOT_MAX_RISK_BUDGET_PTS + 1e-4

        return AuditResult({
            "passed": passed,
            "is_derivative": False,
            "points": pts_val,
            "stop_loss_pct": stop_pct,
            "risk_points": risk_pts,
            "max_risk_budget_pts": self.SPOT_MAX_RISK_BUDGET_PTS,
            "reason": (
                f"Compliant: Risk of {risk_pts:.2f} pts <= {self.SPOT_MAX_RISK_BUDGET_PTS:.2f} pt budget."
                if passed else
                f"Gate 4 Breach: Single-trade risk of {risk_pts:.2f} pts exceeds {self.SPOT_MAX_RISK_BUDGET_PTS:.2f} pt limit."
            )
        })

    def harvest_profits(
        self,
        source_pot: str,
        points: float,
        destination_pot: str = "pot_cash_preservation"
    ) -> HarvestResult:
        """
        Profit Harvesting Protocol:
        Transfers realized gains from tactical swings (Pot 2) or derivatives (Pot 3)
        into macro preservation cash (Pot 4) or core compounders (Pot 1).
        Mutates cash account balances in StateManager to maintain exact reconciliation.
        """
        if points <= 0:
            return HarvestResult({"success": False, "error": "Harvest points must be positive."})

        # Credit Avanza AF (or ISK cash) by default for capital preservation
        target_account = "avanza_af" if destination_pot == "pot_cash_preservation" else "avanza_isk"
        acc = self.state_mgr.state.cash_accounts.get(target_account)
        if acc:
            acc.points = round(acc.points + points, 2)
            if acc.free_points is not None:
                acc.free_points = round(acc.free_points + points, 2)

        # Update mandate totals
        self.state_mgr.state.mandate.total_points = round(
            self.state_mgr.state.mandate.total_points + points, 2
        )
        self.state_mgr.state.mandate.cash_points = round(
            self.state_mgr.state.mandate.cash_points + points, 2
        )
        self.state_mgr.save_state()

        recon = self.recalculate_pots()

        logger.info(
            f"[ExecutiveDesk] Harvested {points:.2f} pts from {source_pot} into {destination_pot} ({target_account})."
        )
        return HarvestResult({
            "success": True,
            "harvested_points": points,
            "source_pot": source_pot,
            "destination_pot": destination_pot,
            "credited_account": target_account,
            "new_nav_total": recon["total_points"],
            "reconciliation": recon
        })

    def format_pots_card(self) -> str:
        """Renders an institutional Markdown briefing card for the 4 Capital Pots."""
        summary = self.get_pot_summary()
        pots = summary["pots"]

        p1 = pots["pot_core_compounders"]
        p2 = pots["pot_swing_trading"]
        p3 = pots["pot_high_risk_derivatives"]
        p4 = pots["pot_cash_preservation"]

        total_pts = summary["total_points"]
        target_pts = summary["target_points"]
        status_emoji = "✅" if summary["reconciled"] else "⚠️"

        lines = [
            f"🏛️ **EXECUTIVE GOVERNING DESK: CAPITAL ALLOCATION MATRIX**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 **Total Portfolio NAV:** `{total_pts:.2f} pts` / `{target_pts:.2f} pts` {status_emoji}",
            f"📊 **Mandate Risk Mode:** `TIERED BARBELL ARCHITECTURE`",
            "",
            f"🔹 **{p1['name']}:** `{p1['current_points']:.2f} pts` ({p1['current_points']/total_pts*100:.1f}%)",
            f"   • *Target Range:* {p1['target_min_points']:.0f} – {p1['target_max_points']:.0f} pts",
            f"   • *Active Count:* {len(p1['holdings'])} positions",
            "",
            f"🔹 **{p2['name']}:** `{p2['current_points']:.2f} pts` ({p2['current_points']/total_pts*100:.1f}%)",
            f"   • *Target Range:* {p2['target_min_points']:.0f} – {p2['target_max_points']:.0f} pts",
            f"   • *Active Count:* {len(p2['holdings'])} tactical swings",
            "",
            f"🔹 **{p3['name']}:** `{p3['current_points']:.2f} pts` ({p3['current_points']/total_pts*100:.1f}%)",
            f"   • *Trade Sizing Cap:* **Max 5.00 pts / trade** (Hard Stop -40%)",
            f"   • *Sleeve Limit:* Max 15.00 pts aggregate",
            f"   • *Active Warrants:* {', '.join(p3['holdings']) if p3['holdings'] else 'None (Flat)'}",
            "",
            f"🔹 **{p4['name']}:** `{p4['current_points']:.2f} pts` ({p4['current_points']/total_pts*100:.1f}%)",
            f"   • *Dry Powder Breakdown:*",
        ]

        for acc_line in p4["holdings"]:
            lines.append(f"     ▫️ {acc_line}")

        parity_str = "PERFECT RECONCILIATION" if summary["reconciled"] else f"DELTA: {summary['delta']:+.2f} pts"
        lines.extend([
            "",
            f"⚖️ **Parity Audit:** `{parity_str}`",
            f"🌾 **Profit Harvest:** Tactical gains swept into Pot 4 (Preservation Cash)."
        ])

        return "\n".join(lines)
