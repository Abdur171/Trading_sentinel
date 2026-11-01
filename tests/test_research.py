"""
tests/test_research.py - Tests for Macro Regime-Switching Models and Factor Ranking.
"""

import pytest
from research.regime_models import MacroRegimeModel, MacroRegimeState
from research.factor_ranking import FactorRankingEngine, CandidateFactors


def test_macro_regime_classification():
    model = MacroRegimeModel()

    # 1. Restrictive yield spike (10Y >= 5.08%)
    res_spike = model.classify_regime(us_10y_yield=5.12, brent_oil_price=95.0)
    assert res_spike.regime == MacroRegimeState.RESTRICTIVE_SPIKE
    assert res_spike.recommended_cash_pct == 55.0

    # 2. Stagflation (Brent >= $110 and 10Y >= 4.85%)
    res_stag = model.classify_regime(us_10y_yield=4.90, brent_oil_price=112.0)
    assert res_stag.regime == MacroRegimeState.STAGFLATION
    assert res_stag.recommended_cash_pct == 50.0

    # 3. Expansion (Yield <= 4.80% and Brent <= 95)
    res_exp = model.classify_regime(us_10y_yield=4.75, brent_oil_price=88.0)
    assert res_exp.regime == MacroRegimeState.EXPANSION
    assert res_exp.recommended_cash_pct == 40.0


def test_factor_ranking_engine():
    engine = FactorRankingEngine()

    candidates = [
        CandidateFactors("TICK_A", "Alpha Corp", quality_raw=0.25, value_raw=0.10, momentum_raw=0.40, growth_raw=0.20, volatility_raw=0.18),
        CandidateFactors("TICK_B", "Beta Corp", quality_raw=0.15, value_raw=0.06, momentum_raw=0.10, growth_raw=0.10, volatility_raw=0.25),
        CandidateFactors("TICK_C", "Gamma Corp", quality_raw=0.08, value_raw=0.02, momentum_raw=-0.15, growth_raw=0.02, volatility_raw=0.45),
    ]

    ranked = engine.rank_universe(candidates)
    assert len(ranked) == 3
    assert ranked[0].rank == 1
    assert ranked[0].symbol == "TICK_A"
    assert ranked[0].passes_gate_2 is True
    assert ranked[2].symbol == "TICK_C"
    assert ranked[2].passes_gate_2 is False
