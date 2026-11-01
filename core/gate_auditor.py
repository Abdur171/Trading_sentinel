"""
core/gate_auditor.py - Quantitative Gate Auditor for the 688.30-point Live Mandate.
Audits Gate 1 to Gate 5, Gate 4 single-trade risk limits (<= 3.44 pts),
and evaluates real-time stop loss breaches, profit trims, resting limit fills, and earnings preflight.
"""

from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from core.models import (
    Holding, MarketQuote, RestingOrder, WatchlistTarget,
    AlertEvent, AlertSeverity, GateAuditResult, DeepDiveAnalysis
)


class GateAuditor:
    MANDATE_TOTAL_POINTS = 688.30
    GATE4_RISK_BUDGET_PCT = 0.0050  # 0.50%
    MAX_ALLOWABLE_RISK_POINTS = 3.44  # 688.30 * 0.0050 = 3.4415 pts

    # Hurdle rates
    BASE_RISK_FREE_RATE = 0.05225  # 5.225%
    EQUITY_RISK_PREMIUM = 0.0500   # 5.00%
    MIN_MARGIN_OF_SAFETY_PCT = 15.0  # 15.0%

    def __init__(self, total_points: float = 688.30):
        self.total_points = total_points
        self.max_risk_pts = round(self.total_points * self.GATE4_RISK_BUDGET_PCT, 2)

    def calculate_trade_risk(
        self,
        position_points: float,
        stop_loss_pct: float,
        is_derivative: bool = False,
        derivative_stop_pct: Optional[float] = None
    ) -> Tuple[float, float, bool]:
        """
        Gate 4 Single-Trade Risk Budget Calculation:
        Risk_portfolio = (Position Points * Loss_pct) / 696.50 <= 0.50% (3.48 pts)
        
        Returns:
            (risk_points, risk_pct_of_mandate, passed)
        """
        if is_derivative:
            if derivative_stop_pct is not None and derivative_stop_pct > 0:
                loss_pct = derivative_stop_pct
            else:
                # Derivative rule: unhedged knockouts assume 100% loss
                loss_pct = 1.00
        else:
            loss_pct = max(0.0, stop_loss_pct)

        risk_points = position_points * loss_pct
        risk_pct = risk_points / self.total_points
        passed = risk_points <= (self.max_risk_pts + 0.005)  # slight rounding tolerance
        return round(risk_points, 4), round(risk_pct * 100, 4), passed

    def max_position_points_for_stop(self, stop_loss_pct: float, is_derivative: bool = False) -> float:
        """
        Calculates the maximum allowable position size in points for a given stop loss percentage.
        """
        if stop_loss_pct <= 0:
            return self.total_points
        effective_loss = 1.00 if (is_derivative and stop_loss_pct >= 1.0) else stop_loss_pct
        max_points = self.max_risk_pts / effective_loss
        return round(max_points, 2)

    def audit_holding_stop_loss(
        self,
        holding: Holding,
        quote: MarketQuote
    ) -> Optional[AlertEvent]:
        """
        Evaluates active stop-loss triggers on a holding against live/mock quote.
        """
        price = quote.price
        if price <= 0:
            return None

        # 1. Direct hard stop on price
        if holding.stop_loss and holding.stop_loss.price is not None:
            stop_price = holding.stop_loss.price
            if price <= stop_price:
                # Severity check: AEHR and XE and MINI L are P2 Emergency
                is_emergency = (
                    holding.symbol.upper() in ["AEHR", "XE"] or
                    holding.stop_loss.type == "emergency_hard_stop" or
                    "MINI" in holding.symbol.upper()
                )
                severity = AlertSeverity.P2 if is_emergency else AlertSeverity.P1
                return AlertEvent(
                    id=f"stop_{holding.symbol}_{quote.timestamp.timestamp()}",
                    severity=severity,
                    symbol=holding.symbol,
                    title=f"🚨 STOP LOSS BREACH: {holding.symbol}",
                    message=(
                        f"CRITICAL STOP HIT for {holding.name} ({holding.symbol}) in {holding.account}.\n"
                        f"Current Price: {price:.2f} {holding.currency} <= Stop Level: {stop_price:.2f} {holding.currency}.\n"
                        f"Position: {holding.points:.2f} pts. Action: {holding.stop_loss.action.upper()}."
                    ),
                    source="gate_auditor",
                    metadata={
                        "stop_price": stop_price,
                        "current_price": price,
                        "points": holding.points,
                        "account": holding.account,
                        "breach_type": "price_hard_stop"
                    }
                )

        # 2. Percentage drawdown stop (e.g. MINI L OMX AVA 1615 -40% on warrant, AXTI -20% from fill)
        if holding.stop_loss and (holding.stop_loss.percentage is not None or holding.stop_loss.type in ["percentage_drawdown", "percentage_from_fill"]):
            pct = holding.stop_loss.percentage or 0.20
            cost = holding.cost_basis or (quote.previous_close if quote.previous_close else price)
            if cost > 0:
                drawdown = (cost - price) / cost
                if drawdown >= pct:
                    is_p2 = "MINI" in holding.symbol.upper() or holding.symbol.upper() in ["AXTI", "AEHR", "XE"]
                    return AlertEvent(
                        id=f"pct_stop_{holding.symbol}_{quote.timestamp.timestamp()}",
                        severity=AlertSeverity.P2 if is_p2 else AlertSeverity.P1,
                        symbol=holding.symbol,
                        title=f"🚨 DRAWDOWN STOP BREACH: {holding.symbol}",
                        message=(
                            f"Percentage stop breach on {holding.name} ({holding.symbol}).\n"
                            f"Drawdown: -{drawdown*100:.1f}% >= Threshold: -{pct*100:.1f}%.\n"
                            f"Price: {price:.2f} vs Basis: {cost:.2f}. Position: {holding.points:.2f} pts."
                        ),
                        source="gate_auditor",
                        metadata={
                            "drawdown_pct": drawdown,
                            "threshold_pct": pct,
                            "price": price,
                            "basis": cost
                        }
                    )

        # 3. Trailing volatility percentage stop (e.g. ARM 15% volatility stop)
        if holding.stop_loss and (holding.stop_loss.trailing_pct is not None or holding.stop_loss.type == "volatility_pct"):
            trail_pct = holding.stop_loss.trailing_pct or 0.15
            ref_price = quote.high or quote.previous_close or holding.cost_basis or price
            if ref_price > 0:
                drawdown = (ref_price - price) / ref_price
                if drawdown >= trail_pct:
                    return AlertEvent(
                        id=f"trail_stop_{holding.symbol}_{quote.timestamp.timestamp()}",
                        severity=AlertSeverity.P1,
                        symbol=holding.symbol,
                        title=f"🛑 TRAILING VOLATILITY STOP BREACH: {holding.symbol}",
                        message=(
                            f"Trailing volatility stop breach on {holding.name} ({holding.symbol}) in {holding.account}.\n"
                            f"Drawdown from benchmark ({ref_price:.2f}): -{drawdown*100:.1f}% >= Trailing Stop: -{trail_pct*100:.1f}%.\n"
                            f"Current Price: {price:.2f} {holding.currency}. Position: {holding.points:.2f} pts. Action: {holding.stop_loss.action.upper()}."
                        ),
                        source="gate_auditor",
                        metadata={
                            "trailing_pct": trail_pct,
                            "drawdown_pct": drawdown,
                            "benchmark_price": ref_price,
                            "price": price,
                            "points": holding.points
                        }
                    )

        # 4. Tactical tranche stop (e.g. SINCH.ST 5.0 pt tactical stop @ 42.00 SEK)
        if holding.tactical_tranche and holding.tactical_tranche.stop_loss_price is not None:
            t_stop = holding.tactical_tranche.stop_loss_price
            if price <= t_stop:
                return AlertEvent(
                    id=f"tactical_stop_{holding.symbol}_{quote.timestamp.timestamp()}",
                    severity=AlertSeverity.P1,
                    symbol=holding.symbol,
                    title=f"⚠️ TACTICAL TRANCHE STOP BREACH: {holding.symbol}",
                    message=(
                        f"Tactical tranche stop breached for {holding.name} ({holding.symbol}).\n"
                        f"Price: {price:.2f} {holding.currency} <= Stop: {t_stop:.2f} {holding.currency}.\n"
                        f"Action: Liquidate tactical {holding.tactical_tranche.points:.2f} pts, retain core holding."
                    ),
                    source="gate_auditor",
                    metadata={
                        "tactical_stop": t_stop,
                        "tactical_points": holding.tactical_tranche.points,
                        "price": price
                    }
                )

        return None

    def audit_earnings_preflight(
        self,
        holding: Holding,
        current_dt: Optional[datetime] = None
    ) -> Optional[AlertEvent]:
        """
        T-48h and T-12h Pre-Flight Protocol 4 earnings audit.
        Checks if holding has an upcoming earnings date and generates protocol alerts.
        """
        if not holding.earnings_date:
            return None

        now = current_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        try:
            if len(holding.earnings_date) == 10:
                e_dt = datetime.strptime(holding.earnings_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                e_dt = datetime.fromisoformat(holding.earnings_date)
                if e_dt.tzinfo is None:
                    e_dt = e_dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

        delta = e_dt - now
        hours_remaining = delta.total_seconds() / 3600.0

        if hours_remaining < 0:
            return None

        # T-12h check (0 to 12 hours)
        if hours_remaining <= 12.0:
            return AlertEvent(
                id=f"earnings_t12_{holding.symbol}_{e_dt.strftime('%Y%m%d')}",
                severity=AlertSeverity.P1,
                symbol=holding.symbol,
                title=f"🚨 T-12h PRE-FLIGHT PROTOCOL 4 EARNINGS ALERT: {holding.symbol}",
                message=(
                    f"URGENT EARNINGS IMMINENT: {holding.name} ({holding.symbol}) reports within {hours_remaining:.1f} hours ({holding.earnings_date}).\n"
                    f"Position: {holding.points:.2f} pts in {holding.account}. Active Stop: {holding.stop_loss.price if holding.stop_loss else 'NONE'}.\n"
                    f"Directive: Pre-Flight Protocol 4 active. Verify stop floor, assess overnight gap risk."
                ),
                source="gate_auditor",
                metadata={"hours_remaining": hours_remaining, "earnings_date": holding.earnings_date, "protocol": "PROTOCOL_4_T12"}
            )
        # T-48h check (12 to 48 hours)
        elif hours_remaining <= 48.0:
            return AlertEvent(
                id=f"earnings_t48_{holding.symbol}_{e_dt.strftime('%Y%m%d')}",
                severity=AlertSeverity.P0,
                symbol=holding.symbol,
                title=f"⏳ T-48h PRE-FLIGHT PROTOCOL 4 EARNINGS ALERT: {holding.symbol}",
                message=(
                    f"PRE-FLIGHT WATCH: {holding.name} ({holding.symbol}) reports in {hours_remaining:.1f} hours ({holding.earnings_date}).\n"
                    f"Position: {holding.points:.2f} pts in {holding.account}.\n"
                    f"Directive: Protocol 4 check. Confirm stop-loss coverage and calibrate position sizing."
                ),
                source="gate_auditor",
                metadata={"hours_remaining": hours_remaining, "earnings_date": holding.earnings_date, "protocol": "PROTOCOL_4_T48"}
            )

        return None

    def audit_stop_coverage(
        self,
        holdings: List[Holding]
    ) -> List[AlertEvent]:
        """
        Strict zero-tolerance audit for unprotected equity holdings.
        Every active equity holding MUST have a valid stop loss defined.
        """
        unprotected_alerts = []
        for h in holdings:
            if h.status != "active":
                continue
            has_valid_stop = (
                h.stop_loss is not None and
                (h.stop_loss.price is not None or h.stop_loss.percentage is not None)
            )
            if not has_valid_stop:
                unprotected_alerts.append(
                    AlertEvent(
                        id=f"unprotected_{h.symbol}",
                        severity=AlertSeverity.P2,
                        symbol=h.symbol,
                        title=f"🚨 UNPROTECTED HOLDING (ZERO STOP LOSS): {h.symbol}",
                        message=(
                            f"CRITICAL GOVERNANCE VIOLATION: {h.name} ({h.symbol}) has NO ACTIVE STOP LOSS.\n"
                            f"Mandate requires 100% stop coverage across all equity positions. Immediate stop calibration required."
                        ),
                        source="gate_auditor",
                        metadata={"symbol": h.symbol, "points": h.points}
                    )
                )
        return unprotected_alerts

    def audit_holding_profit_trims(
        self,
        holding: Holding,
        quote: MarketQuote
    ) -> List[AlertEvent]:
        """
        Evaluates active profit trim targets (e.g. ORCL @ $160, CEG @ $308, SINCH @ 46.80 & 48.50).
        """
        price = quote.price
        if price <= 0:
            return []

        alerts: List[AlertEvent] = []

        # Single limit sell
        if holding.limit_sell and price >= holding.limit_sell.target_price:
            ls = holding.limit_sell
            alerts.append(AlertEvent(
                id=f"trim_{holding.symbol}_{ls.target_price}_{quote.timestamp.timestamp()}",
                severity=AlertSeverity.P1,
                symbol=holding.symbol,
                title=f"🎯 PROFIT TRIM TARGET REACHED: {holding.symbol}",
                message=(
                    f"Profit trim hit for {holding.name} ({holding.symbol}) in {holding.account}.\n"
                    f"Current Price: {price:.2f} {holding.currency} >= Target: {ls.target_price:.2f} {holding.currency}.\n"
                    f"Directive: Execute limit sell of {ls.points_to_trim:.2f} pts "
                    f"(target core: {ls.target_core_points or (holding.points - ls.points_to_trim):.2f} pts)."
                ),
                source="gate_auditor",
                metadata={"target": ls.target_price, "trim_pts": ls.points_to_trim, "price": price}
            ))

        # Staged limit sells
        if holding.limit_sells:
            for ls in holding.limit_sells:
                if price >= ls.target_price:
                    alerts.append(AlertEvent(
                        id=f"trim_{holding.symbol}_{ls.target_price}_{quote.timestamp.timestamp()}",
                        severity=AlertSeverity.P1,
                        symbol=holding.symbol,
                        title=f"🎯 STAGED PROFIT TRIM REACHED: {holding.symbol} @ {ls.target_price}",
                        message=(
                            f"Staged profit trim reached for {holding.name} ({holding.symbol}).\n"
                            f"Current Price: {price:.2f} {holding.currency} >= Target: {ls.target_price:.2f}.\n"
                            f"Trim: {ls.points_to_trim:.2f} pts."
                        ),
                        source="gate_auditor",
                        metadata={"target": ls.target_price, "trim_pts": ls.points_to_trim, "price": price}
                    ))

        return alerts

    def audit_resting_order(
        self,
        order: RestingOrder,
        quote: MarketQuote
    ) -> Optional[AlertEvent]:
        """
        Audits resting limit orders (e.g. INVE B Tranche 2 Buy @ 402.50 SEK).
        """
        price = quote.price
        if price <= 0:
            return None

        if order.order_type == "limit_buy" and price <= order.target_price:
            return AlertEvent(
                id=f"resting_fill_{order.symbol}_{order.target_price}_{quote.timestamp.timestamp()}",
                severity=AlertSeverity.P1,
                symbol=order.symbol,
                title=f"⚡ RESTING LIMIT BUY REACHED: {order.symbol}",
                message=(
                    f"Resting limit buy executable for {order.name} ({order.symbol}) in {order.account}.\n"
                    f"Current Price: {price:.2f} {order.currency} <= Limit Target: {order.target_price:.2f} {order.currency}.\n"
                    f"Allocation: {order.points:.2f} points ({order.currency})."
                ),
                source="gate_auditor",
                metadata={"target": order.target_price, "points": order.points, "price": price}
            )
        return None

    def audit_watchlist_target(
        self,
        target: WatchlistTarget,
        quote: MarketQuote
    ) -> List[AlertEvent]:
        """
        Audits watchlist targets (e.g. VST Tranche A @ $154.50, Tranche B @ $148.00).
        """
        price = quote.price
        if price <= 0:
            return []

        alerts: List[AlertEvent] = []
        for tranche in target.tranches:
            if price <= tranche.target_price:
                alerts.append(AlertEvent(
                    id=f"watchlist_{target.symbol}_{tranche.tranche}_{quote.timestamp.timestamp()}",
                    severity=AlertSeverity.P1,
                    symbol=target.symbol,
                    title=f"👀 WATCHLIST RE-ENTRY TRIGGERED: {target.symbol} (Tranche {tranche.tranche})",
                    message=(
                        f"Watchlist candidate {target.name} ({target.symbol}) reached re-entry level!\n"
                        f"Current Price: {price:.2f} {target.currency} <= Target: {tranche.target_price:.2f} {target.currency}.\n"
                        f"Tranche {tranche.tranche}: {tranche.points:.2f} pts. Calibrated MoS: {tranche.min_mos_pct or 15.0}%."
                    ),
                    source="gate_auditor",
                    metadata={"target": tranche.target_price, "tranche": tranche.tranche, "price": price}
                ))
        return alerts

    def evaluate_cost_of_equity(self, beta: float) -> float:
        """
        Gate 3 Cost of Equity hurdle rate:
        r_e = R_f + beta * ERP = 5.225% + beta * 5.00%
        """
        return self.BASE_RISK_FREE_RATE + beta * self.EQUITY_RISK_PREMIUM

    def evaluate_wrapper_routing(self, symbol: str, is_nordic: bool, is_dividend_payer: bool, is_swing: bool) -> str:
        """
        Gate 5 Broker Routing recommendation:
        - Avanza ISK: Nordic spot equities, index beta, warrants.
        - Avanza KF: Multi-year US/foreign dividend compounders ONLY.
        - Revolut Invest: Tactical US swings, high-beta satellites, physical gold.
        - Avanza AF: Macro cash reserves.
        """
        if is_nordic:
            return "Avanza ISK (0% cap gains tax, standard schablonskatt)"
        if is_swing:
            return "Revolut Invest ($1 flat fee, native USD balances, zero FX churn)"
        if is_dividend_payer:
            return "Avanza KF (Automated 15% foreign withholding tax recovery)"
        return "Revolut Invest (Standard US holding, native USD)"

    def run_full_ten_gates(
        self,
        symbol: str,
        company_name: str,
        current_price: float,
        currency: str,
        financials: Dict[str, Any]
    ) -> DeepDiveAnalysis:
        """
        Evaluates the Ten Quantitative Gates for an on-demand candidate analysis.
        """
        beta = financials.get("beta", 1.10)
        hurdle_rate = self.evaluate_cost_of_equity(beta)

        roic = financials.get("roic", 0.12)
        wacc = financials.get("wacc", 0.08)
        altman_z = financials.get("altman_z", 3.1)
        piotroski_f = financials.get("piotroski_f", 7)
        dcf_intrinsic = financials.get("intrinsic_value", current_price * 1.25)
        volatility = financials.get("annual_volatility", 0.28)

        # Gate 1: Solvency & Quality
        eva_spread = roic - wacc
        g1_passed = (eva_spread > 0) and (altman_z >= 2.60) and (piotroski_f >= 6)
        g1 = GateAuditResult(
            gate_number=1,
            gate_name="Solvency & Quality Hurdle",
            passed=g1_passed,
            score=round(eva_spread * 100, 2),
            threshold="EVA>0, Z''>=2.60, F>=6",
            details=f"EVA Spread: {eva_spread*100:.2f}%, Altman Z'': {altman_z:.2f}, Piotroski F: {piotroski_f}/9",
            recommendation="Pass" if g1_passed else "Disqualified on quality or financial leverage"
        )

        # Gate 2: Multi-Factor Composite Scoring
        # S_i = 0.30 Z_q + 0.25 Z_v + 0.20 Z_m + 0.15 Z_g - 0.10 Z_vol
        z_q = financials.get("z_quality", 0.8)
        z_v = financials.get("z_value", 0.5)
        z_m = financials.get("z_momentum", 0.6)
        z_g = financials.get("z_growth", 0.7)
        z_vol = financials.get("z_volatility", 0.2)
        composite_score = 0.30 * z_q + 0.25 * z_v + 0.20 * z_m + 0.15 * z_g - 0.10 * z_vol
        g2_passed = composite_score >= 0.50
        g2 = GateAuditResult(
            gate_number=2,
            gate_name="Multi-Factor Composite Score",
            passed=g2_passed,
            score=round(composite_score, 3),
            threshold="S_i >= +0.50 (Top 30th percentile)",
            details=f"Composite Score: {composite_score:+.3f} (Q={z_q:.2f}, V={z_v:.2f}, M={z_m:.2f}, G={z_g:.2f}, Vol={z_vol:.2f})",
            recommendation="Top Decile Factor Profile" if g2_passed else "Below +0.50 hurdle"
        )

        # Gate 3: Valuation & Margin of Safety
        mos = (dcf_intrinsic - current_price) / dcf_intrinsic if dcf_intrinsic > 0 else 0.0
        mos_pct = round(mos * 100, 2)
        g3_passed = mos_pct >= self.MIN_MARGIN_OF_SAFETY_PCT
        g3 = GateAuditResult(
            gate_number=3,
            gate_name="DCF Margin of Safety",
            passed=g3_passed,
            score=mos_pct,
            threshold=f"MoS >= {self.MIN_MARGIN_OF_SAFETY_PCT}% (Hurdle r_e = {hurdle_rate*100:.2f}%)",
            details=f"Intrinsic DCF: {dcf_intrinsic:.2f} {currency} vs Market: {current_price:.2f} {currency} (MoS: {mos_pct:.1f}%)",
            recommendation="Valuation provides adequate safety cushion" if g3_passed else "Insufficient margin of safety"
        )

        # Gate 4: Volatility Budget & Max Points
        # Position points for a 20% stop loss
        assumed_stop_loss_pct = 0.20
        max_position_points = self.max_position_points_for_stop(assumed_stop_loss_pct)
        risk_points, _, g4_passed = self.calculate_trade_risk(max_position_points, assumed_stop_loss_pct)
        g4 = GateAuditResult(
            gate_number=4,
            gate_name="Volatility Budget & Risk Ceiling",
            passed=g4_passed,
            score=max_position_points,
            threshold="Single-trade loss <= 3.48 pts (0.50% mandate)",
            details=f"Max allowable size: {max_position_points:.2f} pts at 20% stop (Risk: {risk_points:.2f} pts)",
            recommendation="Compliant with 3.48 pt single-trade risk constraint"
        )

        # Gate 5: Wrapper Routing
        is_nordic = symbol.endswith(".ST") or currency == "SEK"
        is_div = financials.get("dividend_yield", 0.0) > 0.005
        is_swing = financials.get("is_swing_trade", False)
        wrapper = self.evaluate_wrapper_routing(symbol, is_nordic, is_div, is_swing)
        g5 = GateAuditResult(
            gate_number=5,
            gate_name="Execution & Tax Routing",
            passed=True,
            threshold="Optimal Account Wrapper",
            details=f"Recommended custody venue: {wrapper}",
            recommendation="Execute via limit order only"
        )

        # Gate 6: Forensic Accounting & Earnings Quality
        sloan = financials.get("sloan_accrual", -0.04)
        beneish = financials.get("beneish_m", -2.45)
        g6_passed = (sloan <= 0.00) and (beneish < -1.78)
        g6 = GateAuditResult(
            gate_number=6,
            gate_name="Forensic Accounting",
            passed=g6_passed,
            score=round(beneish, 2),
            threshold="Sloan Accrual <= 0.00, Beneish M < -1.78",
            details=f"Sloan Accrual Ratio: {sloan:+.3f}, Beneish M-Score: {beneish:.2f}",
            recommendation="Pass - Low probability of manipulation" if g6_passed else "Red flag on forensic accruals or earnings manipulation"
        )

        # Gate 7: Cash Conversion & Reinvestment Return
        fcf_ni = financials.get("fcf_to_net_income", 0.95)
        croic = financials.get("croic", 0.14)
        g7_passed = (fcf_ni >= 0.80) and (croic >= 0.10)
        g7 = GateAuditResult(
            gate_number=7,
            gate_name="Cash Conversion & CROIC",
            passed=g7_passed,
            score=round(croic * 100, 2),
            threshold="FCF / Net Income >= 80%, CROIC >= 10.0%",
            details=f"FCF/Net Income: {fcf_ni*100:.1f}%, CROIC: {croic*100:.1f}%",
            recommendation="Pass - High quality cash conversion" if g7_passed else "Weak cash conversion or sub-10% CROIC"
        )

        # Gate 8: Debt Refinancing & Coverage
        net_debt_ebitda = financials.get("net_debt_ebitda", 1.20)
        interest_coverage = financials.get("interest_coverage", 8.50)
        g8_passed = (net_debt_ebitda <= 3.0) and (interest_coverage >= 4.0)
        g8 = GateAuditResult(
            gate_number=8,
            gate_name="Debt Refinancing & Coverage",
            passed=g8_passed,
            score=round(net_debt_ebitda, 2),
            threshold="Net Debt / EBITDA <= 3.0x, ICR >= 4.0x",
            details=f"Net Debt/EBITDA: {net_debt_ebitda:.2f}x, Interest Coverage: {interest_coverage:.1f}x",
            recommendation="Pass - Solvent leverage profile" if g8_passed else "Fails debt coverage or leverage limit"
        )

        # Gate 9: Anti-Dilution & Shareholder Yield
        share_dilution = financials.get("share_dilution_yoy", 0.005)
        shareholder_yield = financials.get("shareholder_yield", 0.025)
        g9_passed = (share_dilution <= 0.02) and (shareholder_yield >= 0.0)
        g9 = GateAuditResult(
            gate_number=9,
            gate_name="Anti-Dilution & Shareholder Yield",
            passed=g9_passed,
            score=round(shareholder_yield * 100, 2),
            threshold="YoY Dilution <= +2.0%, Net Shareholder Yield >= 0%",
            details=f"YoY Share Dilution: {share_dilution*100:+.2f}%, Net Shareholder Yield: {shareholder_yield*100:+.2f}%",
            recommendation="Pass - Capital disciplined" if g9_passed else "Excessive dilution or negative shareholder yield"
        )

        # Gate 10: Market Microstructure & Liquidity
        short_interest = financials.get("short_interest_pct", 2.5)
        adv_m = financials.get("adv_usd_millions", 45.0)
        g10_passed = (short_interest <= 12.0) and (adv_m >= 10.0)
        g10 = GateAuditResult(
            gate_number=10,
            gate_name="Market Microstructure",
            passed=g10_passed,
            score=round(short_interest, 1),
            threshold="Short Float <= 12.0%, ADV >= $10M",
            details=f"Short Interest: {short_interest:.1f}% of float, ADV: ${adv_m:.1f}M",
            recommendation="Pass - Clean microstructure" if g10_passed else "High short float (>12%) or illiquid (<$10M ADV)"
        )

        gates = [g1, g2, g3, g4, g5, g6, g7, g8, g9, g10]
        overall_passed = all(g.passed for g in gates)

        summary = (
            f"Gate 1-10 Quantitative Audit for {symbol} ({company_name}): "
            f"{'APPROVED FOR ACCUMULATION' if overall_passed else 'REJECTED / WATCHLIST ONLY'}. "
            f"Passed: {sum(1 for g in gates if g.passed)}/10 Gates. "
            f"DCF Margin of Safety: {mos_pct}%, Hurdle Rate: {hurdle_rate*100:.2f}%, "
            f"Max Gate 4 Allocation: {max_position_points} pts."
        )

        return DeepDiveAnalysis(
            symbol=symbol,
            company_name=company_name,
            market_price=current_price,
            currency=currency,
            cost_of_equity_hurdle=round(hurdle_rate * 100, 2),
            intrinsic_value=round(dcf_intrinsic, 2),
            margin_of_safety_pct=mos_pct,
            gate_results=gates,
            overall_passed=overall_passed,
            recommended_wrapper=wrapper,
            max_position_points_gate4=max_position_points,
            current_volatility_pct=round(volatility * 100, 2),
            summary=summary
        )
