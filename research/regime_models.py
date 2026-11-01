"""
research/regime_models.py - Macroeconomic Regime Classification Engine.
Classifies macro transmission states and computes dynamic cash allocation targets
to advance the barbell mandate beyond fixed allocations.
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any


class MacroRegimeState(str, Enum):
    EXPANSION = "Goldilocks Expansion"
    STAGFLATION = "Stagflationary Pressure"
    RESTRICTIVE_SPIKE = "Restrictive Liquidity Spike"
    CRISIS_CONTRACTION = "Crisis Contraction / Deflation"


@dataclass
class RegimeClassificationResult:
    regime: MacroRegimeState
    recommended_cash_pct: float
    recommended_cash_points: float
    tactical_powder_directive: str
    risk_multiplier: float
    rationale: str


class MacroRegimeModel:
    """
    Evaluates yield curves, oil momentum, and volatility to classify regime
    and modulate the 696.50-point barbell mandate's cash buffer.
    """

    MANDATE_TOTAL_POINTS = 696.50

    def classify_regime(
        self,
        us_10y_yield: float,
        brent_oil_price: float,
        vix_or_volatility: float = 18.0
    ) -> RegimeClassificationResult:
        """
        Classifies macroeconomic state and computes dynamic target cash buffer.
        """
        # Restrictive Liquidity Spike: Yield >= 5.08%
        if us_10y_yield >= 5.08:
            cash_pct = 55.0  # Expand cash to 55%
            cash_pts = round(self.MANDATE_TOTAL_POINTS * (cash_pct / 100.0), 2)
            return RegimeClassificationResult(
                regime=MacroRegimeState.RESTRICTIVE_SPIKE,
                recommended_cash_pct=cash_pct,
                recommended_cash_points=cash_pts,
                tactical_powder_directive="Freeze high-beta additions. Expand liquid cash buffer to 55%.",
                risk_multiplier=0.70,
                rationale=f"US 10Y yield ({us_10y_yield:.2f}%) breached 5.08% barrier, inducing multiple contraction."
            )

        # Stagflation: Brent >= $110/bbl and Yield >= 4.85%
        if brent_oil_price >= 110.0 and us_10y_yield >= 4.85:
            cash_pct = 50.0
            cash_pts = round(self.MANDATE_TOTAL_POINTS * (cash_pct / 100.0), 2)
            return RegimeClassificationResult(
                regime=MacroRegimeState.STAGFLATION,
                recommended_cash_pct=cash_pct,
                recommended_cash_points=cash_pts,
                tactical_powder_directive="Overweight physical gold bullion and critical materials. Maintain 50% cash.",
                risk_multiplier=0.85,
                rationale=f"Brent crude at ${brent_oil_price:.2f} creates energy-driven margin compression."
            )

        # Crisis Contraction: Volatility spike > 35
        if vix_or_volatility >= 35.0:
            cash_pct = 60.0
            cash_pts = round(self.MANDATE_TOTAL_POINTS * (cash_pct / 100.0), 2)
            return RegimeClassificationResult(
                regime=MacroRegimeState.CRISIS_CONTRACTION,
                recommended_cash_pct=cash_pct,
                recommended_cash_points=cash_pts,
                tactical_powder_directive="Capital preservation protocol active. Prepare resting limits at extreme discounts.",
                risk_multiplier=0.50,
                rationale=f"Elevated macro volatility ({vix_or_volatility:.1f}) requires maximum liquidity cushion."
            )

        # Bullish Relief / Expansion: Yield <= 4.80% and Brent <= 95
        if us_10y_yield <= 4.80 and brent_oil_price <= 95.0:
            cash_pct = 40.0  # Deploy tactical powder down to 40%
            cash_pts = round(self.MANDATE_TOTAL_POINTS * (cash_pct / 100.0), 2)
            return RegimeClassificationResult(
                regime=MacroRegimeState.EXPANSION,
                recommended_cash_pct=cash_pct,
                recommended_cash_points=cash_pts,
                tactical_powder_directive="Deploy tactical dry powder into highest MoS compounders.",
                risk_multiplier=1.15,
                rationale="Yield relief below 4.80% opens liquidity window for quality compounding."
            )

        # Baseline Neutral Barbell: 49.25% cash (343.00 pts)
        default_cash_pts = 343.00
        default_cash_pct = round((default_cash_pts / self.MANDATE_TOTAL_POINTS) * 100, 2)
        return RegimeClassificationResult(
            regime=MacroRegimeState.EXPANSION,
            recommended_cash_pct=default_cash_pct,
            recommended_cash_points=default_cash_pts,
            tactical_powder_directive="Maintain standard barbell parity: 50.75% invested / 49.25% liquid cash.",
            risk_multiplier=1.00,
            rationale="Macro indicators reside within neutral transmission bands."
        )
