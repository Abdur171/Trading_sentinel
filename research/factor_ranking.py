"""
research/factor_ranking.py - Gate 2 Cross-Sectional Factor Ranking Engine.
Standardizes factor metrics (Quality, Value, Momentum, Growth, Volatility)
into Z-scores to compute the Gate 2 composite score S_i >= +0.50.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, List, Any


@dataclass
class CandidateFactors:
    symbol: str
    company_name: str
    quality_raw: float    # e.g. ROIC or Gross Margin / Total Assets
    value_raw: float      # e.g. Free Cash Flow Yield or Earnings Yield
    momentum_raw: float   # e.g. 12m - 1m price return
    growth_raw: float     # e.g. 3-yr Revenue CAGR
    volatility_raw: float # e.g. Annualized 90d volatility


@dataclass
class RankedCandidate:
    rank: int
    symbol: str
    company_name: str
    composite_score: float
    z_quality: float
    z_value: float
    z_momentum: float
    z_growth: float
    z_volatility: float
    passes_gate_2: bool


class FactorRankingEngine:
    """
    Standardized multi-factor scoring engine implementing Gate 2:
    S_i = 0.30 Z_Quality + 0.25 Z_Value + 0.20 Z_Momentum + 0.15 Z_Growth - 0.10 Z_Volatility
    Hurdle: S_i >= +0.50 (Top 30th percentile).
    """

    HURDLE_SCORE = 0.50

    def compute_z_scores(self, values: List[float]) -> List[float]:
        """Calculates standard normal Z-scores for a sample distribution."""
        n = len(values)
        if n <= 1:
            return [0.0] * n
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        std = math.sqrt(variance)
        if std == 0:
            return [0.0] * n
        return [round((x - mean) / std, 4) for x in values]

    def rank_universe(self, candidates: List[CandidateFactors]) -> List[RankedCandidate]:
        """
        Ranks an equity universe across the five mandate factors.
        """
        if not candidates:
            return []

        # Extract vectors
        q_vec = [c.quality_raw for c in candidates]
        v_vec = [c.value_raw for c in candidates]
        m_vec = [c.momentum_raw for c in candidates]
        g_vec = [c.growth_raw for c in candidates]
        vol_vec = [c.volatility_raw for c in candidates]

        # Compute cross-sectional Z-scores
        z_q = self.compute_z_scores(q_vec)
        z_v = self.compute_z_scores(v_vec)
        z_m = self.compute_z_scores(m_vec)
        z_g = self.compute_z_scores(g_vec)
        z_vol = self.compute_z_scores(vol_vec)

        scored: List[RankedCandidate] = []
        for i, c in enumerate(candidates):
            comp = (
                0.30 * z_q[i] +
                0.25 * z_v[i] +
                0.20 * z_m[i] +
                0.15 * z_g[i] -
                0.10 * z_vol[i]
            )
            scored.append(RankedCandidate(
                rank=0,
                symbol=c.symbol,
                company_name=c.company_name,
                composite_score=round(comp, 3),
                z_quality=z_q[i],
                z_value=z_v[i],
                z_momentum=z_m[i],
                z_growth=z_g[i],
                z_volatility=z_vol[i],
                passes_gate_2=(comp >= self.HURDLE_SCORE)
            ))

        # Sort descending by composite score
        scored.sort(key=lambda x: x.composite_score, reverse=True)
        for rank, item in enumerate(scored, 1):
            item.rank = rank

        return scored
