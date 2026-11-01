"""
core/quant_risk.py - Quantitative Hedge Fund Risk & Portfolio Defense Architecture.
Implements:
1. ATR-calibrated stop loss evaluation (14-period ATR; verifies if stops are within 2.0-3.0x ATR noise band).
2. Thematic Factor Cluster tracking across 5 clusters (AI Power, Nordic Monopolies, Defensive Yield, Turnarounds, Materials).
3. Concentration alerts if any high-beta cluster > 35% of NAV.
4. Automated Macro Defensive Shield (triggers if US 10Y >= 5.08% or Brent >= $110).
5. Convexity / Asymmetry Ratio (DCF Margin of Safety / Downside Stop Distance).
6. Portfolio High-Water Mark tracking & Drawdown watchdog.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Any, Tuple
from core.models import Holding, CashAccount, MarketQuote, AlertEvent, AlertSeverity

logger = logging.getLogger(__name__)


class QuantRisk:
    """
    Quantitative Risk & Portfolio Defense Engine.
    """

    MANDATE_TOTAL_NAV = 696.50
    HIGH_BETA_CONCENTRATION_LIMIT_PCT = 35.0  # 35% of NAV

    THEMATIC_CLUSTERS = {
        "cluster_a": {
            "name": "Cluster A: AI Compute & Power Infrastructure",
            "is_high_beta": True,
            "symbols": {
                "CEG", "VRT", "CCJ", "ORCL", "NOW", "ARM", "LRCX",
                "AMD", "MU", "MRVL", "CRWV", "NBIS", "VST", "ETN",
                "AMZN", "GOOG", "GOOGL", "MINI L AMD AVA 117"
            }
        },
        "cluster_b": {
            "name": "Cluster B: Nordic Industrial Monopolies",
            "is_high_beta": False,
            "symbols": {
                "ATCO B", "ATCO-B.ST", "SAAB B", "SAAB-B.ST", "INVE B", "INVE-B.ST",
                "MCAP", "MCAP.ST", "LINJE", "LINJE.ST", "EQT", "EQT.ST"
            }
        },
        "cluster_c": {
            "name": "Cluster C: Defensive Yield & Capital Preservation",
            "is_high_beta": False,
            "symbols": {"RSG", "GOLD", "GC=F", "CASH_AF", "CASH_KF", "AVANZA_AF", "AVANZA_KF"}
        },
        "cluster_d": {
            "name": "Cluster D: Tactical Turnarounds & Satellites",
            "is_high_beta": True,
            "symbols": {
                "SINCH", "SINCH.ST", "GRAB", "BKNG", "AEHR", "XE", "AXTI", "Z"
            }
        },
        "cluster_e": {
            "name": "Cluster E: Materials & Spinoffs",
            "is_high_beta": False,
            "symbols": {"LUN", "LUN.ST", "LUMI", "LUMI.ST", "MP", "VYLR"}
        }
    }

    def __init__(self, initial_hwm: float = 696.50):
        self.high_water_mark = initial_hwm
        self.shield_active = False

    # ---------------------------------------------------------
    # 1. ATR-Calibrated Volatility Stops
    # ---------------------------------------------------------
    @staticmethod
    def calculate_atr_14(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """
        Computes 14-period Average True Range (ATR).
        TR_t = max(High_t - Low_t, |High_t - Close_{t-1}|, |Low_t - Close_{t-1}|)
        """
        if len(closes) < 2 or len(highs) < len(closes) or len(lows) < len(closes):
            return 0.0

        tr_values: List[float] = []
        for i in range(1, len(closes)):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_values.append(tr)

        if not tr_values:
            return 0.0

        subset = tr_values[-period:] if len(tr_values) >= period else tr_values
        return sum(subset) / len(subset)

    def evaluate_atr_stop(
        self,
        symbol: str,
        current_price: float,
        stop_price: float,
        atr_14: float
    ) -> Dict[str, Any]:
        """
        Evaluates whether a stop is positioned appropriately relative to market volatility noise.
        Optimal noise band: 2.0x <= k <= 3.0x ATR (up to 3.5x tolerated).
        k < 2.0x -> TOO_TIGHT: vulnerable to random intraday noise knock-out.
        k > 3.5x -> LOOSE: excessive capital at risk, check Gate 4 limits.
        """
        if current_price <= 0 or stop_price <= 0:
            return {"status": "INVALID_PRICE", "k_atr": 0.0, "details": "Non-positive price."}

        stop_dist = current_price - stop_price
        if stop_dist <= 0:
            return {
                "symbol": symbol,
                "status": "STOP_BREACHED",
                "k_atr": 0.0,
                "stop_distance": stop_dist,
                "details": f"Price {current_price:.2f} is already at or below stop {stop_price:.2f}."
            }

        if atr_14 <= 0:
            return {
                "symbol": symbol,
                "status": "NO_ATR_DATA",
                "k_atr": 0.0,
                "stop_distance": stop_dist,
                "details": "ATR data unavailable."
            }

        k_atr = round(stop_dist / atr_14, 2)

        if k_atr < 2.0:
            status = "TOO_TIGHT"
            recommendation = (
                f"Stop is only {k_atr:.1f}x ATR from market price (noise band is 2.0-3.0x ATR). "
                f"Position is vulnerable to noise knock-out. Consider widening to {current_price - 2.5 * atr_14:.2f}."
            )
        elif k_atr <= 3.5:
            status = "CALIBRATED"
            recommendation = f"Stop is well-calibrated at {k_atr:.1f}x ATR (within 2.0-3.5x volatility cushion)."
        else:
            status = "LOOSE"
            recommendation = (
                f"Stop is {k_atr:.1f}x ATR away. Ensure sizing complies strictly with Gate 4 (<= 3.48 pts risk)."
            )

        return {
            "symbol": symbol,
            "current_price": current_price,
            "stop_price": stop_price,
            "atr_14": round(atr_14, 4),
            "stop_distance": round(stop_dist, 2),
            "k_atr": k_atr,
            "status": status,
            "recommendation": recommendation
        }

    # ---------------------------------------------------------
    # 2. Thematic Factor Cluster Matrix & NAV Concentration
    # ---------------------------------------------------------
    def _match_symbol_to_cluster(self, symbol: str) -> Optional[str]:
        sym_clean = symbol.strip().upper().replace("-B.ST", " B").replace(".ST", "")
        for cluster_id, data in self.THEMATIC_CLUSTERS.items():
            for target_sym in data["symbols"]:
                t_clean = target_sym.strip().upper().replace("-B.ST", " B").replace(".ST", "")
                if sym_clean == t_clean or symbol.strip().upper() == target_sym.strip().upper():
                    return cluster_id
        return None

    def calculate_cluster_breakdown(
        self,
        holdings: List[Holding],
        cash_accounts: Dict[str, CashAccount],
        total_nav: float = 696.50
    ) -> Dict[str, Any]:
        """
        Aggregates portfolio holdings and targeted cash into the 5 thematic factor clusters.
        Monitors aggregate cluster exposure and alerts if any high-beta cluster > 35% of NAV.
        """
        breakdown: Dict[str, Dict[str, Any]] = {
            cid: {
                "name": cdata["name"],
                "is_high_beta": cdata["is_high_beta"],
                "points": 0.0,
                "pct_nav": 0.0,
                "holdings": []
            }
            for cid, cdata in self.THEMATIC_CLUSTERS.items()
        }
        breakdown["unclustered"] = {
            "name": "Unclustered / General Mandate",
            "is_high_beta": False,
            "points": 0.0,
            "pct_nav": 0.0,
            "holdings": []
        }

        # 1. Map active holdings
        for h in holdings:
            if h.status != "active" or h.points <= 0:
                continue
            cid = self._match_symbol_to_cluster(h.symbol)
            if not cid:
                # Check name
                cid = self._match_symbol_to_cluster(h.name)

            if cid and cid in breakdown:
                breakdown[cid]["points"] += h.points
                breakdown[cid]["holdings"].append(f"{h.symbol} ({h.points:.1f} pts)")
            else:
                breakdown["unclustered"]["points"] += h.points
                breakdown["unclustered"]["holdings"].append(f"{h.symbol} ({h.points:.1f} pts)")

        # Helper to extract points whether CashAccount model or dict
        def _get_pts(acc_obj: Any) -> float:
            if hasattr(acc_obj, "points"):
                return float(acc_obj.points)
            if isinstance(acc_obj, dict):
                return float(acc_obj.get("points", 0.0))
            if isinstance(acc_obj, (int, float)):
                return float(acc_obj)
            return 0.0

        cash_map = {str(k).lower(): v for k, v in cash_accounts.items()}

        # 2. Map Cluster C cash pools: Avanza AF (Macro Capital Preservation) and Avanza KF
        if "avanza_af" in cash_map:
            af_pts = _get_pts(cash_map["avanza_af"])
            breakdown["cluster_c"]["points"] += af_pts
            breakdown["cluster_c"]["holdings"].append(f"Avanza AF Cash ({af_pts:.1f} pts)")

        if "avanza_kf" in cash_map:
            kf_pts = _get_pts(cash_map["avanza_kf"])
            breakdown["cluster_c"]["points"] += kf_pts
            breakdown["cluster_c"]["holdings"].append(f"Avanza KF Cash ({kf_pts:.1f} pts)")

        # 3. Map remaining cash to unclustered
        for acc_id in ["avanza_isk", "revolut_invest"]:
            if acc_id in cash_map:
                c_pts = _get_pts(cash_map[acc_id])
                breakdown["unclustered"]["points"] += c_pts
                breakdown["unclustered"]["holdings"].append(f"{acc_id} Cash ({c_pts:.1f} pts)")

        # Calculate percentages and check concentration breaches
        warnings: List[str] = []
        for cid, data in breakdown.items():
            pts = data["points"]
            pct = round((pts / total_nav) * 100, 2)
            data["points"] = round(pts, 2)
            data["pct_nav"] = pct

            # Cluster A has strict 25.0% NAV ceiling (Upgrade 5.3)
            limit_pct = 25.0 if cid == "cluster_a" else self.HIGH_BETA_CONCENTRATION_LIMIT_PCT

            if data["is_high_beta"] and pct > limit_pct:
                warnings.append(
                    f"⚠️ **CONCENTRATION WARNING:** {data['name']} represents `{pct:.1f}%` of NAV "
                    f"({pts:.2f} pts), exceeding the {limit_pct:.0f}% limit!"
                )

        return {
            "total_nav": total_nav,
            "clusters": breakdown,
            "concentration_warnings": warnings,
            "has_warnings": len(warnings) > 0
        }

    # ---------------------------------------------------------
    # 3. Automated Macro Defensive Shield (Tri-State Matrix)
    # ---------------------------------------------------------
    def evaluate_macro_shield(
        self,
        macro_quotes: Dict[str, float],
        tnx_5d_change_bps: float = 0.0
    ) -> Dict[str, Any]:
        """
        Automated Macro Defensive Shield (2026 Recalibrated Tri-State Matrix).
        1. NORMAL_REGIME: ^TNX < 5.30% AND 5d_TNX_change < +15 bps AND BZ=F < $102.00.
        2. STRESSED_REGIME: ^TNX 5.30%–5.44% OR 5d_TNX_change +15 to +24 bps OR BZ=F $102.00–$109.99.
        3. CRISIS_REGIME: ^TNX >= 5.45% OR 5d_TNX_change >= +25 bps OR BZ=F >= $110.00.
        """
        breaches: List[str] = []
        stressed_notes: List[str] = []
        us10y = macro_quotes.get("^TNX") or macro_quotes.get("TNX")
        brent = macro_quotes.get("BZ=F") or macro_quotes.get("BZ") or macro_quotes.get("BRENT")

        is_crisis = False
        is_stressed = False

        if us10y is not None:
            if us10y >= 5.45:
                is_crisis = True
                breaches.append(f"US 10-Year Treasury Yield at {us10y:.2f}% >= 5.45% Crisis Barrier")
            elif 5.30 <= us10y < 5.45:
                is_stressed = True
                stressed_notes.append(f"US 10-Year Treasury Yield at {us10y:.2f}% in Stressed Band (5.30%–5.44%)")

        if tnx_5d_change_bps >= 25.0:
            is_crisis = True
            breaches.append(f"US 10Y 5-Day Velocity at +{tnx_5d_change_bps:.1f} bps >= +25 bps Crisis Surge")
        elif 15.0 <= tnx_5d_change_bps < 25.0:
            is_stressed = True
            stressed_notes.append(f"US 10Y 5-Day Velocity at +{tnx_5d_change_bps:.1f} bps in Stressed Band (+15 to +24 bps)")

        if brent is not None:
            if brent >= 110.0:
                is_crisis = True
                breaches.append(f"Brent Crude Oil at ${brent:.2f}/bbl >= $110.00 Crisis Barrier")
            elif 102.0 <= brent < 110.0:
                is_stressed = True
                stressed_notes.append(f"Brent Crude Oil at ${brent:.2f}/bbl in Stressed Band ($102.00–$109.99)")

        self.shield_active = is_crisis

        if is_crisis:
            regime = "CRISIS_REGIME"
            status = "DEFENSIVE_SHIELD_ACTIVE"
            directives = [
                "1. Activate Full Defensive Shield.",
                "2. Freeze all new high-beta limit buys.",
                "3. Ratchet trailing stops across active holdings.",
                "4. Preserve unencumbered cash in Avanza AF (risk-free yield buffer)."
            ]
        elif is_stressed:
            regime = "STRESSED_REGIME"
            status = "STRESSED_REGIME"
            directives = [
                "1. Halve derivative caps to 2.50 pts.",
                "2. Tighten Bucket 1 & 2 stops by 20%.",
                "3. Enforce strict hurdle rate > 10.22% on prospective allocations."
            ]
        else:
            regime = "NORMAL_REGIME"
            status = "NORMAL_REGIME"
            directives = [
                "1. Full operational capital allocation active.",
                "2. Normal swing entries and dip buys permitted."
            ]

        return {
            "shield_active": self.shield_active,
            "regime": regime,
            "status": status,
            "breaches": breaches if is_crisis else stressed_notes,
            "directives": directives,
            "derivative_cap_multiplier": 0.0 if is_crisis else (0.50 if is_stressed else 1.0),
            "stop_tightening_pct": 0.30 if is_crisis else (0.20 if is_stressed else 0.0),
        }

    # ---------------------------------------------------------
    # 4. Convexity & Asymmetry Scorecard
    # ---------------------------------------------------------
    def calculate_convexity_scorecard(
        self,
        holdings: List[Holding],
        quotes: Dict[str, MarketQuote],
        fundamentals: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Convexity Ratio = (DCF Margin of Safety %) / (Downside Stop Distance %)
        Ranks portfolio holdings by upside-to-downside asymmetry.
        """
        scorecard: List[Dict[str, Any]] = []

        for h in holdings:
            if h.status != "active" or h.points <= 0:
                continue

            q = quotes.get(h.symbol)
            if not q and hasattr(h, "ticker_override") and h.ticker_override:
                q = quotes.get(h.ticker_override)
            if not q:
                sym_alt = h.symbol.replace("-B.ST", " B").replace(".ST", "")
                q = quotes.get(sym_alt)
            if not q:
                continue

            px = q.price
            if px <= 0:
                continue

            # Determine intrinsic value
            fund_data = (fundamentals or {}).get(h.symbol, {})
            intrinsic = fund_data.get("intrinsic_value")
            if not intrinsic or intrinsic <= 0:
                # Default 20% upside benchmark if not specified
                intrinsic = px * 1.20

            mos_pct = ((intrinsic - px) / px) * 100.0

            # Determine downside distance to stop
            stop_px = None
            if h.stop_loss and h.stop_loss.price:
                stop_px = h.stop_loss.price
            elif h.tactical_tranche and h.tactical_tranche.stop_loss_price:
                stop_px = h.tactical_tranche.stop_loss_price

            if stop_px and stop_px < px:
                downside_pct = ((px - stop_px) / px) * 100.0
            elif h.stop_loss and getattr(h.stop_loss, "trailing_pct", None):
                downside_pct = h.stop_loss.trailing_pct * 100.0
                stop_px = round(px * (1.0 - h.stop_loss.trailing_pct), 2)
            elif h.stop_loss and getattr(h.stop_loss, "percentage", None):
                downside_pct = h.stop_loss.percentage * 100.0
                stop_px = round(px * (1.0 - h.stop_loss.percentage), 2)
            else:
                downside_pct = 15.0  # Default 15% downside baseline

            convexity_ratio = round(mos_pct / downside_pct, 2) if downside_pct > 0 else 0.0

            scorecard.append({
                "symbol": h.symbol,
                "name": h.name,
                "points": h.points,
                "current_price": round(px, 2),
                "intrinsic_value": round(intrinsic, 2),
                "margin_of_safety_pct": round(mos_pct, 2),
                "downside_risk_pct": round(downside_pct, 2),
                "convexity_ratio": convexity_ratio,
                "stop_price": stop_px
            })

        # Rank descending by convexity ratio
        scorecard.sort(key=lambda x: x["convexity_ratio"], reverse=True)
        return scorecard

    # ---------------------------------------------------------
    # 5. Portfolio High-Water Mark & Drawdown Watchdog
    # ---------------------------------------------------------
    def update_high_water_mark(self, current_nav: float) -> Dict[str, Any]:
        """
        Updates portfolio high-water mark and calculates drawdown distance.
        Alerts at 5%, 10%, and 15% drawdowns.
        """
        if current_nav > self.high_water_mark:
            self.high_water_mark = round(current_nav, 2)

        drawdown_pts = self.high_water_mark - current_nav
        drawdown_pct = round((drawdown_pts / self.high_water_mark) * 100.0, 2) if self.high_water_mark > 0 else 0.0

        alert_level = None
        if drawdown_pct >= 15.0:
            alert_level = "EMERGENCY_P2"
        elif drawdown_pct >= 10.0:
            alert_level = "WARNING_P1"
        elif drawdown_pct >= 5.0:
            alert_level = "ADVISORY_P0"

        return {
            "current_nav": round(current_nav, 2),
            "high_water_mark": self.high_water_mark,
            "drawdown_pts": round(drawdown_pts, 2),
            "drawdown_pct": drawdown_pct,
            "alert_level": alert_level
        }
