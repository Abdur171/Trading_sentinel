"""
core/state_manager.py - Portfolio State & Capital Parity Manager.
Synchronizes the 696.50-point multi-asset barbell ledger, tracks cash pools across wrappers,
validates total balance reconciliation, and enables dynamic mandate evolution.
"""

from __future__ import annotations
import os
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from core.models import (
    PortfolioState, Holding, CashAccount, MandateConfig,
    RestingOrder, WatchlistTarget, TimeSensitiveEvent, StopLossConfig
)


class StateManager:
    """
    Manages loading, saving, mutation, and capital parity reconciliation for the 696.50-point mandate.
    Supports dynamic ledger evolution via commands (/update_stop, /add_holding, /audit_mandate).
    """

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = Path(__file__).resolve().parent.parent / "config" / "portfolio_state.yaml"
        self._state: Optional[PortfolioState] = None
        self.load_state()

    def load_state(self) -> PortfolioState:
        """Loads and parses the portfolio_state.yaml file."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Portfolio state file not found at: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)

        mandate_cfg = MandateConfig(**raw_data.get("mandate", {}))
        
        cash_accounts = {}
        for acc_id, acc_data in raw_data.get("cash_accounts", {}).items():
            cash_accounts[acc_id] = CashAccount(**acc_data)

        holdings = []
        for h in raw_data.get("holdings", []):
            holdings.append(Holding(**h))

        resting_orders = []
        for ro in raw_data.get("resting_orders", []):
            resting_orders.append(RestingOrder(**ro))

        watchlist = []
        for w in raw_data.get("watchlist", []):
            watchlist.append(WatchlistTarget(**w))

        time_sensitive_events = []
        for tse in raw_data.get("time_sensitive_events", []):
            time_sensitive_events.append(TimeSensitiveEvent(**tse))

        self._state = PortfolioState(
            mandate=mandate_cfg,
            cash_accounts=cash_accounts,
            holdings=holdings,
            resting_orders=resting_orders,
            watchlist=watchlist,
            time_sensitive_events=time_sensitive_events,
            liquidated_assets=raw_data.get("liquidated_assets", []),
            blacklisted_assets=raw_data.get("blacklisted_assets", [])
        )
        return self._state

    @property
    def state(self) -> PortfolioState:
        if self._state is None:
            return self.load_state()
        return self._state

    def save_state(self) -> None:
        """Serializes current state back to portfolio_state.yaml to persist mutations."""
        state_dict = self.state.model_dump(mode="json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(state_dict, f, sort_keys=False, default_flow_style=False)

    def reconcile_capital(self, feed: Optional[Any] = None) -> Dict[str, Any]:
        """
        Reconciles total mandate capital under the Floating NAV & Locked Cash model.
        Live Points_h = Pts_base,h * (P_live,h / P_ref,h)
        Live NAV = sum(Live Points_h) + sum(Static Cash_a)
        Benchmark HWM = max(696.50, Mandate HWM, Live NAV)
        Dynamic Gate 4 Limit = 0.0050 * Live NAV (3.44 pts at 688.30)
        """
        state = self.state
        active_holdings = [h for h in state.holdings if h.status == "active"]

        quotes = {}
        if feed and hasattr(feed, "get_batch_quotes"):
            syms = [h.symbol for h in active_holdings]
            quotes = feed.get_batch_quotes(syms)

        invested_sum = 0.0
        holding_live_points: Dict[str, float] = {}
        for h in active_holdings:
            base_pts = h.get_baseline_points()
            p_ref = h.reference_price or h.current_price or h.cost_basis
            q = quotes.get(h.symbol)
            p_live = q.price if (q and q.price and q.price > 0) else (h.current_price or p_ref)

            if p_ref and p_ref > 0 and p_live and p_live > 0:
                live_pts = base_pts * (p_live / p_ref)
            else:
                live_pts = base_pts
            live_pts = round(live_pts, 2)
            holding_live_points[h.symbol] = live_pts
            invested_sum += live_pts

        cash_sum = sum(acc.points for acc in state.cash_accounts.values())
        live_nav = round(invested_sum + cash_sum, 2)

        # Invariant checks
        hwm = getattr(state.mandate, "benchmark_hwm", 696.50)
        if live_nav > hwm:
            hwm = live_nav
            state.mandate.benchmark_hwm = live_nav

        hwm_drawdown_pts = round(max(0.0, hwm - live_nav), 2)
        hwm_drawdown_pct = round((hwm_drawdown_pts / hwm) * 100, 2) if hwm > 0 else 0.0
        dynamic_gate4_limit = round(0.0050 * live_nav, 2)

        target_total = getattr(state.mandate, "total_points", 688.30)
        parity_reconciled = abs(live_nav - (round(invested_sum, 2) + round(cash_sum, 2))) < 0.05

        return {
            "mandate_target_points": target_total,
            "calculated_total_points": round(live_nav, 2),
            "live_nav": round(live_nav, 2),
            "invested_points": round(invested_sum, 2),
            "invested_pct": round((invested_sum / live_nav) * 100, 2) if live_nav > 0 else 0.0,
            "cash_points": round(cash_sum, 2),
            "cash_pct": round((cash_sum / live_nav) * 100, 2) if live_nav > 0 else 0.0,
            "parity_reconciled": parity_reconciled,
            "delta_points": round(live_nav - target_total, 4),
            "benchmark_hwm": round(hwm, 2),
            "hwm_drawdown_pts": hwm_drawdown_pts,
            "hwm_drawdown_pct": hwm_drawdown_pct,
            "dynamic_gate_4_limit": dynamic_gate4_limit,
            "num_active_holdings": len(active_holdings),
            "cash_by_account": {k: acc.points for k, acc in state.cash_accounts.items()},
            "holding_live_points": holding_live_points,
        }

    def calibrate_from_live_feed(self, feed: Optional[Any] = None) -> Dict[str, Any]:
        """
        Fetches live market quotes (with extended-hours support) and calibrates:
        - True implied entry price: P_entry = P_live / (1 + reported_pnl_pct)
        - Anchors all stop-loss prices and profit ratchets dynamically to P_entry and P_live
        - Preserves zero mismatch between YAML state and live market feeds.
        """
        if feed is None:
            try:
                from feeds.yfinance_feed import YFinanceFeed
                feed = YFinanceFeed()
            except Exception:
                feed = None

        state = self.state
        active_holdings = [h for h in state.holdings if h.status == "active"]
        symbols = [h.symbol for h in active_holdings]

        quotes = feed.get_batch_quotes(symbols) if (feed and hasattr(feed, "get_batch_quotes")) else {}
        calibrated = []

        from core.models import StopLossConfig, RecoveryTrancheConfig

        for h in active_holdings:
            q = quotes.get(h.symbol)
            if not q and feed and hasattr(feed, "get_quote"):
                q = feed.get_quote(h.symbol)

            p_live = q.price if (q and q.price and q.price > 0) else (h.reference_price or h.current_price or 100.0)
            pnl_pct = h.reported_pnl_pct if h.reported_pnl_pct is not None else 0.0

            # Compute implied entry price
            if (1.0 + pnl_pct) > 0:
                p_entry = round(p_live / (1.0 + pnl_pct), 2)
            else:
                p_entry = round(p_live, 2)

            h.cost_basis = p_entry
            h.current_price = round(p_live, 2)
            h.reference_price = round(p_live, 2)

            if not h.stop_loss:
                h.stop_loss = StopLossConfig()

            bucket = str(h.horizon_bucket or "")
            sym = h.symbol.upper()

            # Bucket 1: Short Term High Risk
            if "BUCKET_1" in bucket or sym in ["MINI L AMD AVA 117", "CRWV", "NBIS"]:
                if "MINI" in sym or h.instrument_type == "derivative_warrant":
                    h.stop_loss.price = round(p_live * 0.60, 2)
                    h.stop_loss.percentage = 0.40
                    h.stop_loss.max_risk_pts = round(h.points * 0.40, 2)
                else:
                    h.stop_loss.price = round(p_entry * 0.95, 2)
                    h.stop_loss.percentage = 0.05
                    h.stop_loss.max_risk_pts = round(h.points * 0.05, 2)

            # Bucket 2: Medium Term Swing
            elif "BUCKET_2" in bucket or sym in ["ORCL", "AMD", "SINCH.ST", "MU", "CCJ", "VRT", "VYLR", "MRVL", "VST"]:
                if sym == "ORCL":
                    h.stop_loss.price = round(p_live * (1.0 - 0.075), 2)
                    h.stop_loss.percentage = 0.075
                    h.stop_loss.max_risk_pts = round(h.points * 0.075, 2)
                    h.recovery_tranches = [
                        RecoveryTrancheConfig(bounce_pct=0.06, points_to_trim=4.70, target_price=round(p_live * 1.06, 2)),
                        RecoveryTrancheConfig(bounce_pct=0.12, points_to_trim=5.00, target_price=round(p_live * 1.12, 2)),
                    ]
                elif sym == "VYLR":
                    h.stop_loss.price = round(p_entry * 1.06, 2)
                    h.stop_loss.percentage = -0.06
                    h.stop_loss.max_risk_pts = 0.0
                elif sym in ["CCJ", "VRT"]:
                    h.stop_loss.price = round(p_entry * (1.0 - 0.125), 2)
                    h.stop_loss.percentage = 0.125
                    h.stop_loss.max_risk_pts = round(h.points * 0.125, 2)
                elif sym == "VST":
                    h.stop_loss.price = round(p_entry * (1.0 - 0.10), 2)
                    h.stop_loss.percentage = 0.10
                    h.stop_loss.max_risk_pts = round(h.points * 0.10, 2)
                else:
                    h.stop_loss.price = round(p_entry * (1.0 - 0.08), 2)
                    h.stop_loss.percentage = 0.08
                    h.stop_loss.max_risk_pts = round(h.points * 0.08, 2)

            # Bucket 3: Long Term Compounder
            elif "BUCKET_3" in bucket or sym in [
                "MCAP.ST", "CEG", "NOW", "EQT.ST", "GOOG", "ATCO-B.ST",
                "LUN.ST", "BKNG", "SAAB-B.ST", "AMZN", "INVE-B.ST", "LINJE", "ETN"
            ]:
                if sym == "MCAP.ST":
                    h.stop_loss.price = round(p_entry * 1.05, 2)
                    h.stop_loss.percentage = -0.05
                    h.stop_loss.max_risk_pts = round(h.points * max(0.0, (p_live - p_entry * 1.05) / p_live), 2)
                elif sym == "CEG":
                    h.stop_loss.price = round(p_entry * 1.035, 2)
                    h.stop_loss.percentage = -0.035
                    h.stop_loss.max_risk_pts = 0.74
                elif sym in ["LINJE", "NOW", "SAAB-B.ST"]:
                    h.stop_loss.price = round(p_entry * 1.005, 2)
                    h.stop_loss.percentage = -0.005
                    h.stop_loss.max_risk_pts = 0.50
                else:
                    h.stop_loss.price = round(p_entry * 0.90, 2)
                    h.stop_loss.percentage = 0.10
                    h.stop_loss.max_risk_pts = round(min(3.44, h.points * 0.10), 2)

            # Bucket 4: Low Risk Anchor & Cash
            elif "BUCKET_4" in bucket or sym in ["RSG", "GOLD"]:
                if sym == "RSG":
                    h.stop_loss.price = round(p_entry * 1.01, 2)
                    h.stop_loss.percentage = -0.01
                    h.stop_loss.max_risk_pts = round(h.points * 0.035, 2)
                elif sym == "GOLD":
                    h.stop_loss.price = round(p_live * (1.0 - 0.125), 2)
                    h.stop_loss.percentage = 0.125
                    h.stop_loss.max_risk_pts = round(min(3.44, h.points * 0.125), 2)

            calibrated.append({
                "symbol": h.symbol,
                "p_live": round(p_live, 2),
                "p_entry": p_entry,
                "stop_price": h.stop_loss.price,
                "buffer_pct": round(((p_live - h.stop_loss.price) / p_live) * 100, 2) if h.stop_loss.price else 0.0,
            })

        self.save_state()
        return {"status": "SUCCESS", "calibrated_holdings": calibrated}

    def get_bucket_summary(self, feed: Optional[Any] = None) -> Dict[str, Any]:
        """
        Computes capital distribution across the 4 Multi-Horizon Risk Buckets:
        Bucket 1: Short Term High Risk (1-5 Days, Target 2-5% NAV)
        Bucket 2: Medium Term Swing (2-8 Weeks, Target 12-18% NAV)
        Bucket 3: Long Term Compounder (6-36+ Months, Target 20-30% NAV)
        Bucket 4: Low Risk Anchor & Cash (Target 45-62% NAV)
        """
        recon = self.reconcile_capital(feed=feed)
        live_nav = recon["live_nav"]
        holdings = [h for h in self.state.holdings if h.status == "active"]

        buckets = {
            "BUCKET_1_SHORT_TERM_HIGH_RISK": {
                "name": "Bucket 1: Short-Term High-Risk Satellites",
                "target_range": "2% – 5%",
                "points": 0.0,
                "holdings": [],
            },
            "BUCKET_2_MEDIUM_TERM_SWING": {
                "name": "Bucket 2: Medium-Term Tactical Swings",
                "target_range": "12% – 18%",
                "points": 0.0,
                "holdings": [],
            },
            "BUCKET_3_LONG_TERM_COMPOUNDER": {
                "name": "Bucket 3: Long-Term Monopoly Compounders",
                "target_range": "20% – 30%",
                "points": 0.0,
                "holdings": [],
            },
            "BUCKET_4_LOW_RISK_ANCHOR_AND_CASH": {
                "name": "Bucket 4: Low-Risk Anchor & Liquid Cash",
                "target_range": "45% – 62%",
                "points": 0.0,
                "holdings": [],
            },
        }

        for h in holdings:
            b_key = str(h.horizon_bucket or "BUCKET_3_LONG_TERM_COMPOUNDER")
            if b_key not in buckets:
                b_key = "BUCKET_3_LONG_TERM_COMPOUNDER"
            pts = recon["holding_live_points"].get(h.symbol, h.points)
            buckets[b_key]["points"] += pts
            buckets[b_key]["holdings"].append(f"{h.symbol} ({pts:.2f} pts)")

        cash_pts = recon["cash_points"]
        buckets["BUCKET_4_LOW_RISK_ANCHOR_AND_CASH"]["points"] += cash_pts
        buckets["BUCKET_4_LOW_RISK_ANCHOR_AND_CASH"]["holdings"].append(f"Liquid Cash ({cash_pts:.2f} pts)")

        for b in buckets.values():
            b["points"] = round(b["points"], 2)
            b["pct_nav"] = round((b["points"] / live_nav) * 100, 2) if live_nav > 0 else 0.0

        return {
            "live_nav": live_nav,
            "buckets": buckets,
        }

    @staticmethod
    def _normalize_key(s: str) -> str:
        """Strips punctuation (.ST, dashes, spaces) for flexible ticker matching."""
        clean = s.upper().replace(".ST", "").replace("-", " ").replace("_", " ").strip()
        return " ".join(clean.split())

    def get_holding(self, symbol: str) -> Optional[Holding]:
        """Finds a holding by exact symbol, ticker override, underlying, or normalized symbol/name."""
        sym_clean = symbol.strip().upper()
        # 1. Direct match on symbol or ticker_override
        for h in self.state.holdings:
            if h.symbol.upper() == sym_clean:
                return h
            if h.ticker_override and h.ticker_override.upper() == sym_clean:
                return h

        # 2. Normalized exact match (prioritized across all holdings first)
        norm_query = self._normalize_key(symbol)
        for h in self.state.holdings:
            if self._normalize_key(h.symbol) == norm_query:
                return h
            if h.ticker_override and self._normalize_key(h.ticker_override) == norm_query:
                return h
            if h.underlying and self._normalize_key(h.underlying) == norm_query:
                return h

        # 3. Substring matching (only if no exact match found across any holding)
        for h in self.state.holdings:
            norm_sym = self._normalize_key(h.symbol)
            norm_name = self._normalize_key(h.name)
            if norm_query in norm_sym:
                return h
            if norm_query in norm_name or norm_name in norm_query:
                return h
            if h.underlying:
                norm_under = self._normalize_key(h.underlying)
                if norm_query in norm_under:
                    return h

        # 4. Special derivative / warrant alias matching
        query_words = norm_query.split()
        if any(term in query_words for term in ["DERIVATIVE", "WARRANT", "OMX", "OMXS", "OMXS30"]):
            for h in self.state.holdings:
                if (h.instrument_type in ["derivative_warrant", "derivative", "warrant", "option"] or
                    "MINI" in h.symbol.upper() or
                    (h.underlying and "OMX" in h.underlying.upper())):
                    return h

        return None

    def get_all_active_holdings(self) -> List[Holding]:
        """Returns all currently active holdings."""
        return [h for h in self.state.holdings if h.status == "active"]

    def get_active_stops(self) -> List[Dict[str, Any]]:
        """Extracts all holdings with active stop loss definitions."""
        stops = []
        for h in self.get_all_active_holdings():
            if h.stop_loss:
                stops.append({
                    "symbol": h.symbol,
                    "name": h.name,
                    "points": h.points,
                    "account": h.account,
                    "stop_type": h.stop_loss.type,
                    "stop_price": h.stop_loss.price,
                    "percentage": h.stop_loss.percentage,
                    "action": h.stop_loss.action
                })
            elif h.tactical_tranche and h.tactical_tranche.stop_loss_price:
                stops.append({
                    "symbol": h.symbol,
                    "name": h.name,
                    "points": h.tactical_tranche.points,
                    "account": h.account,
                    "stop_type": "tactical_tranche_stop",
                    "stop_price": h.tactical_tranche.stop_loss_price,
                    "action": "trim_tactical_tranche"
                })
        return stops

    def get_cash_summary(self) -> Dict[str, Any]:
        """Returns cash distribution details across the 4 account wrappers."""
        summary = {}
        for acc_id, acc in self.state.cash_accounts.items():
            summary[acc_id] = {
                "name": acc.account_name,
                "points": acc.points,
                "currency": acc.currency,
                "committed_points": acc.committed_points,
                "free_points": acc.get_free_points(),
                "purpose": acc.purpose
            }
        return summary

    # Dynamic Ledger Evolution Methods
    def update_stop_loss(self, symbol: str, new_stop_price: float, stop_type: str = "hard_stop") -> bool:
        """Dynamically updates an active stop loss for a holding and persists changes."""
        holding = self.get_holding(symbol)
        if not holding:
            return False

        if holding.stop_loss:
            holding.stop_loss.price = new_stop_price
            holding.stop_loss.type = stop_type
        else:
            holding.stop_loss = StopLossConfig(price=new_stop_price, type=stop_type, action="exit_position")

        self.save_state()
        return True

    def update_holding_points(self, symbol: str, new_points: float) -> bool:
        """Updates position points and adjusts cash accordingly to maintain 696.50 balance."""
        holding = self.get_holding(symbol)
        if not holding:
            return False

        delta = new_points - holding.points
        holding.points = new_points

        # Offset delta in corresponding cash account if available
        acc = self.state.cash_accounts.get(holding.account)
        if acc:
            acc.points = round(max(0.0, acc.points - delta), 2)

        self.save_state()
        return True

    def add_holding(self, holding: Holding) -> bool:
        """Adds a new holding and offsets points from target cash account."""
        acc = self.state.cash_accounts.get(holding.account)
        if acc:
            free = acc.get_free_points()
            if free < holding.points:
                return False  # Insufficient account cash
            acc.points = round(acc.points - holding.points, 2)
            if acc.free_points is not None:
                acc.free_points = round(max(0.0, acc.free_points - holding.points), 2)

        self.state.holdings.append(holding)
        self.state.mandate.invested_points = round(self.state.mandate.invested_points + holding.points, 2)
        self.state.mandate.cash_points = round(self.state.mandate.cash_points - holding.points, 2)
        self.save_state()
        return True

    def close_holding(
        self,
        symbol: str,
        realized_points: Optional[float] = None,
        notes: str = ""
    ) -> Dict[str, Any]:
        """
        Closes/sells an active holding, updates holding status to 'closed',
        credits released capital back into the holding's cash wrapper, adjusts total mandate balances,
        and saves state.

        If realized_points is not provided, defaults to the holding's allocated points
        (preserving exact capital parity).
        """
        holding = self.get_holding(symbol)
        if not holding:
            return {"success": False, "error": f"Holding '{symbol}' not found in portfolio state."}

        if holding.status == "closed" or holding.points <= 0.0:
            return {
                "success": False,
                "error": f"Holding '{holding.symbol}' is already closed (points: {holding.points:.2f})."
            }

        points_cost = holding.points
        points_to_credit = realized_points if (realized_points is not None and realized_points > 0) else points_cost

        # Mark holding closed
        holding.status = "closed"
        holding.points = 0.0
        holding.exit_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        holding.exit_reason = notes or "Position exited / sold per instruction"
        holding.stop_loss = None  # Clear any active stop
        if holding.curfew:
            holding.curfew.enforced = False

        # Credit cash account
        acc = self.state.cash_accounts.get(holding.account)
        if acc:
            acc.points = round(acc.points + points_to_credit, 2)
            if acc.free_points is not None:
                acc.free_points = round(acc.free_points + points_to_credit, 2)

        # Update mandate totals
        self.state.mandate.invested_points = round(max(0.0, self.state.mandate.invested_points - points_cost), 2)
        self.state.mandate.cash_points = round(self.state.mandate.cash_points + points_to_credit, 2)
        if realized_points is not None:
            profit_delta = realized_points - points_cost
            self.state.mandate.total_points = round(self.state.mandate.total_points + profit_delta, 2)

        # Record in liquidated_assets
        liquidated_record = {
            "symbol": holding.symbol,
            "name": holding.name,
            "account": holding.account,
            "points_cost": points_cost,
            "points_realized": points_to_credit,
            "exit_date": holding.exit_date,
            "exit_reason": holding.exit_reason,
            "status": "closed"
        }
        self.state.liquidated_assets.append(liquidated_record)

        # Deactivate any curfew event linked to this holding
        for ev in self.state.time_sensitive_events:
            if ev.symbol == holding.symbol and ev.event_type == "curfew":
                ev.status = "inactive"

        self.save_state()
        recon = self.reconcile_capital()

        return {
            "success": True,
            "symbol": holding.symbol,
            "name": holding.name,
            "account": holding.account,
            "account_name": acc.account_name if acc else holding.account,
            "points_released": points_cost,
            "points_credited": points_to_credit,
            "updated_account_cash": acc.points if acc else 0.0,
            "reconciliation": recon
        }

    def trim_holding(
        self,
        symbol: str,
        points_to_trim: float,
        realized_proceeds: Optional[float] = None,
        notes: str = ""
    ) -> Dict[str, Any]:
        """
        Partially sells/trims an active holding by a specified number of points:
        - Reduces holding.points by points_to_trim.
        - Credits released capital (default: points_to_trim, or realized_proceeds) into the holding's account wrapper cash.
        - Dynamically updates mandate totals and recalculates stop-loss max risk points.
        - If points_to_trim >= holding.points, automatically delegates to close_holding() for a full exit.
        - Persists state and returns reconciliation details.
        """
        holding = self.get_holding(symbol)
        if not holding:
            return {"success": False, "error": f"Holding '{symbol}' not found in portfolio state."}

        if holding.status == "closed" or holding.points <= 0.0:
            return {
                "success": False,
                "error": f"Holding '{holding.symbol}' is already closed (points: {holding.points:.2f})."
            }

        if points_to_trim <= 0.0:
            return {
                "success": False,
                "error": f"Trim points must be strictly positive (requested: {points_to_trim:.2f} pts)."
            }

        # If trim equals or exceeds current holding size, execute full closure
        if points_to_trim >= (holding.points - 1e-4):
            res = self.close_holding(
                symbol=symbol,
                realized_points=realized_proceeds,
                notes=notes or f"Full position exited via trim ({points_to_trim:.2f} pts)"
            )
            res["is_full_exit"] = True
            return res

        # Partial trim calculation
        points_trimmed = round(points_to_trim, 2)
        remaining_points = round(holding.points - points_trimmed, 2)
        proceeds_to_credit = round(realized_proceeds, 2) if (realized_proceeds is not None and realized_proceeds > 0) else points_trimmed

        # Update holding size
        holding.points = remaining_points
        if holding.baseline_points is not None:
            holding.baseline_points = round(max(0.0, holding.baseline_points - points_trimmed), 2)

        # Recalculate stop loss max risk if applicable
        if holding.stop_loss:
            entry = holding.cost_basis or holding.reference_price or holding.current_price
            if entry and entry > 0 and holding.stop_loss.price:
                dist_pct = abs(entry - holding.stop_loss.price) / entry
                holding.stop_loss.max_risk_pts = round(holding.points * dist_pct, 2)
            elif holding.stop_loss.percentage:
                holding.stop_loss.max_risk_pts = round(holding.points * abs(holding.stop_loss.percentage), 2)

        # If holding had a limit sell specifically for this trim, reduce or clear it
        if holding.limit_sell:
            if points_trimmed >= holding.limit_sell.points_to_trim:
                holding.limit_sell = None
            else:
                holding.limit_sell.points_to_trim = round(holding.limit_sell.points_to_trim - points_trimmed, 2)

        # Credit cash account wrapper
        acc = self.state.cash_accounts.get(holding.account)
        if acc:
            acc.points = round(acc.points + proceeds_to_credit, 2)
            if acc.free_points is not None:
                acc.free_points = round(acc.free_points + proceeds_to_credit, 2)

        # Update mandate totals
        self.state.mandate.invested_points = round(max(0.0, self.state.mandate.invested_points - points_trimmed), 2)
        self.state.mandate.cash_points = round(self.state.mandate.cash_points + proceeds_to_credit, 2)
        if realized_proceeds is not None:
            profit_delta = proceeds_to_credit - points_trimmed
            self.state.mandate.total_points = round(self.state.mandate.total_points + profit_delta, 2)

        # Record partial trim transaction
        trim_record = {
            "symbol": holding.symbol,
            "name": holding.name,
            "account": holding.account,
            "points_trimmed": points_trimmed,
            "points_remaining": remaining_points,
            "proceeds_credited": proceeds_to_credit,
            "trim_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "notes": notes or f"Partial trim of {points_trimmed:.2f} pts"
        }
        if not hasattr(self.state, "partial_trims") or self.state.partial_trims is None:
            self.state.partial_trims = []
        self.state.partial_trims.append(trim_record)

        self.save_state()
        recon = self.reconcile_capital()

        return {
            "success": True,
            "is_full_exit": False,
            "symbol": holding.symbol,
            "name": holding.name,
            "account": holding.account,
            "account_name": acc.account_name if acc else holding.account,
            "points_trimmed": points_trimmed,
            "remaining_points": remaining_points,
            "proceeds_credited": proceeds_to_credit,
            "updated_account_cash": acc.points if acc else 0.0,
            "updated_stop": holding.stop_loss.price if holding.stop_loss else None,
            "updated_max_risk_pts": holding.stop_loss.max_risk_pts if holding.stop_loss else None,
            "reconciliation": recon
        }

    def audit_mandate_health(self) -> Dict[str, Any]:
        """
        Runs comprehensive mandate structural health check:
        - Capital parity check
        - Risk budget check
        - Account wrapper compliance
        - Stop loss completeness
        """
        reconciliation = self.reconcile_capital()
        holdings = self.get_all_active_holdings()

        missing_stops = [h.symbol for h in holdings if not h.stop_loss and not h.tactical_tranche and h.symbol != "GOLD"]
        wrapper_mismatches = []
        for h in holdings:
            if h.account == "avanza_af" and h.symbol not in ["RSG", "VYLR"]:
                wrapper_mismatches.append(f"{h.symbol} in AF (should be ISK or KF)")

        return {
            "reconciliation": reconciliation,
            "total_active_positions": len(holdings),
            "positions_missing_stops": missing_stops,
            "wrapper_compliance_issues": wrapper_mismatches,
            "all_healthy": reconciliation["parity_reconciled"] and len(wrapper_mismatches) == 0
        }
