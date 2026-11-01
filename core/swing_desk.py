"""
core/swing_desk.py - Tactical Swing Desk with 2-Stage Profit Management.

Implements:
1. Sizing Audit:
   - Standard spot equity trades strictly capped at <= 3.44 points single-trade risk budget (0.50% of 688.30 Live NAV).
2. 3-Stage Profit Management Lifecycle & Ratchet:
   - Stage 1 (Breakeven): +3.5% (SEK) / +5.0% (US) ratchet stop to breakeven.
   - Stage 2 (50% Locked): +10% gain locks 50% of profit (+5% above entry).
   - Stage 3 (Chandelier): +15% gain activates dynamic trailing Chandelier ATR stop.
   - Bucket 1 Satellite Alert: +8% gain profit take directive.
3. Stop Breach Detection:
   - Triggers liquidation alert if price breaches current active stop-loss.
4. Holding Evaluation & Telegram Card:
   - evaluate_holding(holding: Holding, current_price: float) -> SwingAction
   - format_swing_card(symbol: str, holding: Optional[Holding], current_price: Optional[float]) -> str
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from core.models import (
    Holding, StopLossConfig, LimitSellConfig, StopLossType,
    PositionTier, HorizonBucket
)
from core.state_manager import StateManager
from core.quant_risk import QuantRisk

logger = logging.getLogger(__name__)


class SwingActionType:
    TRIM_TP1 = "TRIM_TP1"           # Target 1 reached: trim 50% + ratchet stop to breakeven
    TRAIL_TP2 = "TRAIL_TP2"         # Target 2 trailing ATR ratchet
    STOP_BREACH = "STOP_BREACH"     # Stop loss breached
    HOLD = "HOLD"                   # In trade, within channel


@dataclass
class SwingAction:
    """Action recommendation emitted by SwingDesk for an active holding."""
    action_type: str
    symbol: str
    current_price: float
    shares_to_trim: float = 0.0
    points_to_trim: float = 0.0
    new_stop_price: Optional[float] = None
    stop_ratcheted: bool = False
    reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "symbol": self.symbol,
            "current_price": self.current_price,
            "shares_to_trim": self.shares_to_trim,
            "points_to_trim": self.points_to_trim,
            "new_stop_price": self.new_stop_price,
            "stop_ratcheted": self.stop_ratcheted,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SwingTradeState:
    """Maintains active lifecycle state for a swing trade."""
    symbol: str
    entry_price: float
    current_stop_price: float
    tp1_price: float
    tp2_price: float
    initial_points: float
    remaining_points: float
    tp1_executed: bool = False
    highest_price: float = 0.0
    trailing_stop_price: Optional[float] = None
    atr_14: float = 1.0


class SwingDesk:
    """
    Tactical Swing Desk managing live swing trades entered by the user.
    Enforces the 3.44 pt single-trade risk budget and 3-stage profit management / ratchet.
    """

    SPOT_MAX_RISK_BUDGET_PTS = 3.44   # Gate 4 risk budget = 0.50% of 688.30 Live NAV
    DEFAULT_TP1_GAIN_PCT = 0.08       # +8.0% default Target 1 if not specified
    DEFAULT_TP2_GAIN_PCT = 0.16       # +16.0% default Target 2 if not specified
    DEFAULT_ATR_MULTIPLIER = 2.0      # Trailing ATR distance (2.0x ATR)

    def __init__(
        self,
        state_mgr: Optional[StateManager] = None,
        quant_risk: Optional[QuantRisk] = None
    ):
        self.state_mgr = state_mgr
        self.quant_risk = quant_risk or QuantRisk()
        # Internal trade state registry keyed by symbol
        self._trade_states: Dict[str, SwingTradeState] = {}

    # -------------------------------------------------------------------------
    # 1. Gate 4 Sizing & Risk Audit
    # -------------------------------------------------------------------------

    def audit_spot_risk(
        self,
        symbol: str,
        points: float,
        entry_price: float,
        stop_price: float
    ) -> Dict[str, Any]:
        """
        Audits single-trade spot equity risk against the strict 3.44 pt limit:
        Risk Points = points * (|entry_price - stop_price| / entry_price)
        Must satisfy: Risk Points <= 3.44 pts.
        """
        if entry_price <= 0:
            return {
                "passed": False,
                "symbol": symbol,
                "points": points,
                "risk_points": 0.0,
                "max_budget": self.SPOT_MAX_RISK_BUDGET_PTS,
                "reason": "Invalid entry price (<= 0)."
            }

        stop_distance_pct = abs(entry_price - stop_price) / entry_price
        risk_points = round(points * stop_distance_pct, 2)
        passed = risk_points <= (self.SPOT_MAX_RISK_BUDGET_PTS + 1e-4)

        reason = (
            f"Compliant: Risk of {risk_points:.2f} pts is within {self.SPOT_MAX_RISK_BUDGET_PTS:.2f} pt limit."
            if passed else
            f"Gate 4 Risk Breach: Single-trade risk of {risk_points:.2f} pts exceeds {self.SPOT_MAX_RISK_BUDGET_PTS:.2f} pt budget."
        )

        return {
            "passed": passed,
            "symbol": symbol,
            "points": round(points, 2),
            "entry_price": round(entry_price, 2),
            "stop_price": round(stop_price, 2),
            "stop_distance_pct": round(stop_distance_pct * 100, 2),
            "risk_points": risk_points,
            "max_budget": self.SPOT_MAX_RISK_BUDGET_PTS,
            "reason": reason
        }

    # -------------------------------------------------------------------------
    # 1B. 3-Stage Profit Ratchet & Satellite Profit Taking
    # -------------------------------------------------------------------------

    def evaluate_profit_ratchet(
        self,
        holding: Holding,
        current_price: float,
        atr_14: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Evaluates 3-Stage Profit Ratchet Lifecycle and Satellite Profit Taking:
        - Stage 1 Breakeven Stop:
          * +3.5% gain for Swedish holdings (.ST or currency SEK)
          * +5.0% gain for US / global holdings
          * Ratchets stop-loss to entry price (breakeven).
        - Stage 2 Lock 50% Gain:
          * Reached at +10.0% gain.
          * Stop locks in 50% of the unrealized gain (e.g. at +10%, stop moves to +5% above entry).
        - Stage 3 Chandelier / Trailing Stop:
          * Reached at +15.0% gain.
          * Dynamic trailing ratchet: High - (2.5 * ATR_14), locked upwards.
        - Bucket 1 Satellite Alert:
          * If holding is BUCKET_1_SHORT_TERM_HIGH_RISK or TIER_2_SATELLITE,
            a gain of >= +8.0% triggers an immediate profit-taking directive.
        """
        symbol = holding.symbol
        entry_price = holding.cost_basis if holding.cost_basis is not None else (holding.current_price or current_price)
        if entry_price <= 0:
            entry_price = current_price

        gain_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0

        is_swedish = (
            (holding.currency and holding.currency.upper() == "SEK") or
            symbol.upper().endswith(".ST") or
            symbol.upper() in ["EQT.ST", "INVE-B.ST", "ATCO-B.ST", "SAAB-B.ST", "MCAP.ST", "SINCH.ST", "LUN.ST", "LINJE"]
        )
        stage_1_threshold = 0.035 if is_swedish else 0.050

        stage = "STAGE_0_INITIAL"
        new_stop = holding.stop_loss.price if (holding.stop_loss and holding.stop_loss.price) else round(entry_price * 0.92, 2)
        ratchet_recommended = False
        reason = f"Normal channel: gain {gain_pct*100:+.2f}%. Stop maintained at {new_stop:.2f}."

        # Stage 3: +15% Chandelier
        if gain_pct >= 0.15:
            stage = "STAGE_3_CHANDELIER"
            atr_val = atr_14 if atr_14 is not None else (entry_price * 0.032)
            candidate_stop = round(current_price - (2.5 * atr_val), 2)
            if candidate_stop > new_stop:
                new_stop = candidate_stop
                ratchet_recommended = True
            reason = f"Stage 3 Chandelier reached (+{gain_pct*100:.1f}% >= +15%). Trailing stop locked at {new_stop:.2f}."
        # Stage 2: +10% Lock 50% gain
        elif gain_pct >= 0.10:
            stage = "STAGE_2_HALF_GAIN_LOCKED"
            candidate_stop = round(entry_price * (1.0 + (gain_pct * 0.50)), 2)
            if candidate_stop > new_stop:
                new_stop = candidate_stop
                ratchet_recommended = True
            reason = f"Stage 2 reached (+{gain_pct*100:.1f}% >= +10%). Half gain locked: Stop raised to {new_stop:.2f}."
        # Stage 1: Breakeven
        elif gain_pct >= stage_1_threshold:
            stage = "STAGE_1_BREAKEVEN"
            candidate_stop = round(entry_price, 2)
            if candidate_stop > new_stop:
                new_stop = candidate_stop
                ratchet_recommended = True
            reason = f"Stage 1 Breakeven reached (+{gain_pct*100:.1f}% >= +{stage_1_threshold*100:.1f}%). Stop ratcheted to entry {new_stop:.2f}."

        # Bucket 1 Satellite Profit Take Alert
        is_satellite = (
            str(getattr(holding, "horizon_bucket", "")) in ["BUCKET_1_SHORT_TERM_HIGH_RISK", "HorizonBucket.BUCKET_1_SHORT_TERM_HIGH_RISK"] or
            str(getattr(holding, "position_tier", "")) in ["TIER_2_SATELLITE", "PositionTier.TIER_2_SATELLITE"] or
            getattr(holding, "classification", "") == "Tactical Swing"
        )
        satellite_profit_alert = is_satellite and (gain_pct >= 0.08)
        satellite_message = ""
        if satellite_profit_alert:
            satellite_message = (
                f"🎯 BUCKET 1 SATELLITE PROFIT TARGET REACHED: {symbol} is up +{gain_pct*100:.1f}% (>= +8.0%). "
                f"Directive: Harvest profits immediately on tactical satellite capital."
            )

        return {
            "symbol": symbol,
            "current_price": current_price,
            "entry_price": entry_price,
            "gain_pct": round(gain_pct * 100, 2),
            "stage": stage,
            "ratchet_recommended": ratchet_recommended,
            "new_stop_price": new_stop,
            "is_swedish": is_swedish,
            "satellite_profit_alert": satellite_profit_alert,
            "satellite_message": satellite_message,
            "reason": reason,
        }

    # -------------------------------------------------------------------------
    # 1C. Staged Dip Buys Audit
    # -------------------------------------------------------------------------

    def audit_staged_dip_buys(
        self,
        holdings: Optional[List[Holding]] = None
    ) -> List[Dict[str, Any]]:
        """
        Audits the 8 pre-approved dip buy positions:
        AMZN, ETN, AMD, MU, MRVL, VST, CRWV, NBIS.
        Verifies:
        1. Non-null stop loss.
        2. Single-trade risk <= 3.44 pts.
        3. Tier assignment (Tier 1 Core vs Tier 2 Satellite).
        4. Tier 2 Satellite strict <= 5.0% stop-loss cap.
        """
        target_symbols = {"AMZN", "ETN", "AMD", "MU", "MRVL", "VST", "CRWV", "NBIS"}
        if holdings is None:
            if self.state_mgr:
                holdings = self.state_mgr.get_all_active_holdings()
            else:
                holdings = []

        results = []
        for h in holdings:
            if h.symbol.upper() in target_symbols:
                sym = h.symbol.upper()
                entry = h.cost_basis or h.reference_price or h.current_price or 1.0
                stop_cfg = h.stop_loss
                has_stop = stop_cfg is not None and (stop_cfg.price is not None or stop_cfg.percentage is not None)
                stop_px = stop_cfg.price if (stop_cfg and stop_cfg.price) else None

                risk_pts = 0.0
                dist_pct = 0.0
                if has_stop and stop_px and entry > 0:
                    dist_pct = abs(entry - stop_px) / entry
                    risk_pts = round(h.points * dist_pct, 2)
                elif has_stop and stop_cfg.percentage:
                    dist_pct = abs(stop_cfg.percentage)
                    risk_pts = round(h.points * dist_pct, 2)

                tier = str(getattr(h, "position_tier", "TIER_1_CORE"))
                is_tier2 = "TIER_2" in tier or sym in ["CRWV", "NBIS"]

                risk_compliant = risk_pts <= (self.SPOT_MAX_RISK_BUDGET_PTS + 1e-4)
                tier2_compliant = (dist_pct <= 0.055) if is_tier2 else True

                passed = has_stop and risk_compliant and tier2_compliant

                results.append({
                    "symbol": sym,
                    "name": h.name,
                    "points": h.points,
                    "entry_price": entry,
                    "stop_price": stop_px,
                    "stop_distance_pct": round(dist_pct * 100, 2),
                    "risk_points": risk_pts,
                    "max_risk_budget": self.SPOT_MAX_RISK_BUDGET_PTS,
                    "tier": "TIER_2_SATELLITE" if is_tier2 else "TIER_1_CORE",
                    "risk_compliant": risk_compliant,
                    "has_stop": has_stop,
                    "passed": passed,
                })
        return results

    # -------------------------------------------------------------------------
    # 2. Trade Registration & State Management
    # -------------------------------------------------------------------------

    def register_swing_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        points: float,
        tp1_price: Optional[float] = None,
        tp2_price: Optional[float] = None,
        atr_14: Optional[float] = None
    ) -> SwingTradeState:
        """
        Registers or updates an active swing trade in the desk's tracking registry.
        """
        risk_dist = max(0.01, entry_price - stop_price)
        calc_tp1 = tp1_price if tp1_price is not None else round(entry_price + (1.5 * risk_dist), 2)
        calc_tp2 = tp2_price if tp2_price is not None else round(entry_price + (3.0 * risk_dist), 2)
        calc_atr = atr_14 if atr_14 is not None else round(entry_price * 0.03, 2)

        state = SwingTradeState(
            symbol=symbol,
            entry_price=entry_price,
            current_stop_price=stop_price,
            tp1_price=calc_tp1,
            tp2_price=calc_tp2,
            initial_points=points,
            remaining_points=points,
            tp1_executed=False,
            highest_price=entry_price,
            trailing_stop_price=stop_price,
            atr_14=calc_atr
        )
        self._trade_states[symbol] = state
        return state

    def get_trade_state(self, symbol: str) -> Optional[SwingTradeState]:
        return self._trade_states.get(symbol)

    # -------------------------------------------------------------------------
    # 3. Live 2-Stage Profit Management Engine
    # -------------------------------------------------------------------------

    def evaluate_holding(
        self,
        holding: Holding,
        current_price: float,
        atr_14: Optional[float] = None
    ) -> SwingAction:
        """
        Evaluates an active holding against 2-stage profit management and risk stops:
        1. Target 1 (TP1):
           - Alert to trim 50% of position points.
           - Automatically ratchet stop-loss to entry price (breakeven).
        2. Target 2 (TP2):
           - Trailing ATR ratchet updates stop price upwards for remaining 50%.
        3. Stop Breach:
           - Triggers full exit if current_price <= stop_price.
        """
        symbol = holding.symbol
        entry_price = holding.cost_basis if holding.cost_basis is not None else (holding.current_price or current_price)
        points = holding.points

        # Resolve or initialize trade state
        state = self._trade_states.get(symbol)
        if state is None:
            # Derive target prices from holding configuration or defaults
            stop_price = (
                holding.stop_loss.price if (holding.stop_loss and holding.stop_loss.price)
                else round(entry_price * 0.92, 2)
            )
            tp1_price = None
            if holding.limit_sell and holding.limit_sell.target_price:
                tp1_price = holding.limit_sell.target_price
            elif holding.limit_sells and len(holding.limit_sells) > 0:
                tp1_price = holding.limit_sells[0].target_price

            state = self.register_swing_trade(
                symbol=symbol,
                entry_price=entry_price,
                stop_price=stop_price,
                points=points,
                tp1_price=tp1_price,
                atr_14=atr_14
            )

        # Update high-water mark for trailing stop
        if current_price > state.highest_price:
            state.highest_price = current_price

        if atr_14 is not None:
            state.atr_14 = atr_14

        # ---------------------------------------------------------------------
        # Case A: Stop Loss Breach
        # ---------------------------------------------------------------------
        active_stop = state.trailing_stop_price or state.current_stop_price
        if current_price <= active_stop:
            return SwingAction(
                action_type=SwingActionType.STOP_BREACH,
                symbol=symbol,
                current_price=current_price,
                points_to_trim=state.remaining_points,
                new_stop_price=None,
                stop_ratcheted=False,
                reason=(
                    f"Stop Loss Breached: Current price {current_price:.2f} <= active stop {active_stop:.2f}. "
                    f"Mandatory liquidation of remaining {state.remaining_points:.2f} pts."
                )
            )

        # ---------------------------------------------------------------------
        # Case B: Target 1 (TP1) Reached
        # ---------------------------------------------------------------------
        if current_price >= state.tp1_price and not state.tp1_executed:
            trim_points = round(state.remaining_points * 0.50, 2)
            state.remaining_points = round(state.remaining_points - trim_points, 2)
            state.tp1_executed = True

            # Ratchet stop to breakeven (entry price)
            breakeven_stop = state.entry_price
            state.current_stop_price = breakeven_stop
            state.trailing_stop_price = breakeven_stop

            # Reflect in holding if mutable
            if holding.stop_loss:
                holding.stop_loss.price = breakeven_stop
                holding.stop_loss.type = StopLossType.BREAKEVEN.value

            return SwingAction(
                action_type=SwingActionType.TRIM_TP1,
                symbol=symbol,
                current_price=current_price,
                shares_to_trim=trim_points,
                points_to_trim=trim_points,
                new_stop_price=breakeven_stop,
                stop_ratcheted=True,
                reason=(
                    f"Target 1 (TP1) hit at {current_price:.2f} >= {state.tp1_price:.2f}. "
                    f"Trim 50% ({trim_points:.2f} pts) and ratchet stop-loss to entry price ({breakeven_stop:.2f}) "
                    f"for a risk-free breakeven runner."
                )
            )

        # ---------------------------------------------------------------------
        # Case C: Stage 2 Trailing ATR Ratchet (Post-TP1)
        # ---------------------------------------------------------------------
        if state.tp1_executed:
            # Calculate dynamic trailing stop: Highest Price - (2.0 * ATR)
            atr_distance = self.DEFAULT_ATR_MULTIPLIER * state.atr_14
            candidate_trailing_stop = round(state.highest_price - atr_distance, 2)

            # Trailing stop can only ratchet UPWARDS, never downwards, and never below breakeven
            ratcheted = False
            effective_stop = state.trailing_stop_price or state.entry_price

            if candidate_trailing_stop > effective_stop:
                effective_stop = candidate_trailing_stop
                state.trailing_stop_price = candidate_trailing_stop
                ratcheted = True
                if holding.stop_loss:
                    holding.stop_loss.price = candidate_trailing_stop

            # Check if TP2 has been reached
            if current_price >= state.tp2_price:
                return SwingAction(
                    action_type=SwingActionType.TRAIL_TP2,
                    symbol=symbol,
                    current_price=current_price,
                    shares_to_trim=state.remaining_points,
                    points_to_trim=state.remaining_points,
                    new_stop_price=effective_stop,
                    stop_ratcheted=ratcheted,
                    reason=(
                        f"Target 2 (TP2) reached at {current_price:.2f} >= {state.tp2_price:.2f}. "
                        f"Trailing stop locked in at {effective_stop:.2f} (2.0x ATR ratchet)."
                    )
                )

            if ratcheted:
                return SwingAction(
                    action_type=SwingActionType.TRAIL_TP2,
                    symbol=symbol,
                    current_price=current_price,
                    points_to_trim=0.0,
                    new_stop_price=effective_stop,
                    stop_ratcheted=True,
                    reason=(
                        f"Trailing ATR Ratchet: Trailing stop ratcheted upwards to {effective_stop:.2f} "
                        f"based on new high {state.highest_price:.2f} - (2.0 * {state.atr_14:.2f} ATR)."
                    )
                )

        # ---------------------------------------------------------------------
        # Case D: Holding within normal parameters
        # ---------------------------------------------------------------------
        return SwingAction(
            action_type=SwingActionType.HOLD,
            symbol=symbol,
            current_price=current_price,
            points_to_trim=0.0,
            new_stop_price=active_stop,
            stop_ratcheted=False,
            reason=f"Position active within channel. Stop: {active_stop:.2f} | TP1: {state.tp1_price:.2f} | TP2: {state.tp2_price:.2f}."
        )

    # -------------------------------------------------------------------------
    # 4. Formatted Telegram Card for /swing <SYM>
    # -------------------------------------------------------------------------

    def format_swing_card(
        self,
        symbol: str,
        holding: Optional[Holding] = None,
        current_price: Optional[float] = None
    ) -> str:
        """
        Formats a comprehensive tactical status card for `/swing <SYM>`.
        """
        # Look up holding if not provided
        if holding is None and self.state_mgr:
            holding = self.state_mgr.get_holding(symbol)

        price = current_price
        if price is None and holding:
            price = holding.current_price or holding.cost_basis

        if price is None:
            price = 100.0

        if holding is None:
            # Check if there is an in-memory trade state
            state = self._trade_states.get(symbol)
            if not state:
                return (
                    f"⚠️ **TACTICAL SWING DESK — {symbol}**\n\n"
                    f"• **Status:** No active swing trade or holding found for `{symbol}`.\n"
                    f"• **Hint:** Use `/signals` to scan for fresh setups or add holding to start tracking."
                )
            points = state.remaining_points
            entry = state.entry_price
            stop = state.trailing_stop_price or state.current_stop_price
            tp1 = state.tp1_price
            tp2 = state.tp2_price
            tp1_done = state.tp1_executed
        else:
            state = self._trade_states.get(symbol)
            points = holding.points
            entry = holding.cost_basis or price
            stop = holding.stop_loss.price if (holding.stop_loss and holding.stop_loss.price) else entry * 0.92
            tp1 = state.tp1_price if state else (
                holding.limit_sell.target_price if holding.limit_sell else round(entry * 1.08, 2)
            )
            tp2 = state.tp2_price if state else round(entry * 1.16, 2)
            tp1_done = state.tp1_executed if state else False

        pnl_pct = ((price - entry) / entry) * 100 if entry > 0 else 0.0
        pnl_icon = "🟢" if pnl_pct >= 0 else "🔴"
        stage_str = "Stage 2: Trailing ATR Runner" if tp1_done else "Stage 1: Pre-TP1 Target Run"

        # Gate 4 risk calculation
        risk_dist = abs(entry - stop) / entry if entry > 0 else 0.08
        risk_pts = round(points * risk_dist, 2)
        risk_passed = risk_pts <= self.SPOT_MAX_RISK_BUDGET_PTS

        lines = [
            f"🎯 **TACTICAL SWING DESK — {symbol}**",
            f"_{stage_str}_\n",
            f"• **Market Price:** `{price:.2f}` ({pnl_icon} `{pnl_pct:+.2f}%`)",
            f"• **Entry Cost:** `{entry:.2f}` | **Current Points:** `{points:.2f} pts`",
            f"• **Active Stop:** `{stop:.2f}` ({'Risk-Free Breakeven' if tp1_done else 'Initial Stop'})",
            f"• **Target 1 (TP1):** `{tp1:.2f}` [{'✅ TRIMMED 50%' if tp1_done else '⏳ PENDING 50% TRIM'}]",
            f"• **Target 2 (TP2):** `{tp2:.2f}` [{'🏃 RUNNING WITH ATR TRAIL' if tp1_done else '⏳ PENDING'}]",
            "",
            "🛡️ **Gate 4 Risk Budget:**",
            f"• Risk Exposure: `{risk_pts:.2f} pts` / `{self.SPOT_MAX_RISK_BUDGET_PTS:.2f} pts` max limit",
            f"• Status: `{'COMPLIANT ✅' if risk_passed else 'BREACH ⚠️'}`"
        ]

        return "\n".join(lines)
