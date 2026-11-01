"""
tests/test_signals_desk.py - Comprehensive Unit Tests for SignalsDesk.

Verifies:
1. Strategy 1: Breakout Momentum (20-day high channel, RVOL >= 1.3x, MACD expansion).
2. Strategy 2: Mean-Reversion Dips (high-ROIC >= 12%, pullback to 50/200 EMA or lower BB, RSI <= 38 turning up).
3. Strategy 3: Multi-Factor Composite Swings (Trend alignment, RVOL >= 1.1x, RSI 40-68, ATR stop with R:R >= 2.0:1).
4. Full universe scanning via scan_signals().
5. Telegram formatted card output via format_signals_card().
"""

import pytest
from core.signals_desk import SignalsDesk, SignalCandidate


def test_indicator_calculations():
    desk = SignalsDesk()
    closes = [10.0 + i * 0.5 for i in range(50)]

    # EMA calculation
    ema_20 = desk.calculate_ema(closes, 20)
    assert len(ema_20) == 31
    assert ema_20[-1] > ema_20[0]

    # RSI calculation
    rsi = desk.calculate_rsi(closes, 14)
    assert len(rsi) > 0
    assert rsi[-1] > 70  # Monotonically increasing series is overbought

    # Bollinger Bands
    bb = desk.calculate_bollinger_bands(closes, 20, 2.0)
    assert bb["upper"] > bb["middle"] > bb["lower"]

    # RVOL calculation
    volumes = [1000.0] * 20 + [2600.0]  # 2.6x surge
    rvol = desk.calculate_rvol(volumes, 20)
    assert rvol == 2.60


def test_strategy_1_breakout_momentum_success():
    desk = SignalsDesk()

    # Create 40 days of history: oscillating between 100 and 110, then breaking out to 115
    closes = [100.0 + (i % 10) for i in range(35)]
    # Create expansion with higher closes for positive MACD
    closes.extend([111.0, 112.5, 114.0, 116.0, 118.0])

    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    # RVOL >= 1.3x on breakout day
    volumes = [1000.0] * 39 + [1800.0]  # 1.8x volume

    candidate = desk.evaluate_breakout_momentum(
        symbol="CEG",
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=118.0
    )

    assert candidate is not None
    assert candidate.symbol == "CEG"
    assert candidate.strategy_name == "Breakout Momentum"
    assert candidate.entry_price == 118.0
    assert candidate.stop_loss < 118.0
    assert candidate.target_price > 118.0
    assert candidate.rr_ratio >= 2.0
    assert candidate.metrics["rvol"] >= 1.30


def test_strategy_1_breakout_momentum_low_volume_rejected():
    desk = SignalsDesk()

    closes = [100.0 + (i % 10) for i in range(35)] + [111.0, 112.5, 114.0, 116.0, 118.0]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    # Low volume: only 0.9x
    volumes = [1000.0] * 39 + [900.0]

    candidate = desk.evaluate_breakout_momentum(
        symbol="CEG",
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=118.0
    )

    assert candidate is None  # Rejection due to RVOL < 1.3x


def test_strategy_2_mean_reversion_dip_success():
    desk = SignalsDesk()

    # Generate pullback pattern: strong stock dropping into oversold territory near lower BB / 50 EMA
    closes = [150.0] * 30
    # Sharp drop
    closes.extend([145.0, 140.0, 134.0, 128.0, 122.0, 118.0, 115.0])
    # Hook upward on the last day (turning up)
    closes.append(117.0)

    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]

    # Test high ROIC company (e.g. 22% ROIC > 12% threshold)
    candidate = desk.evaluate_mean_reversion_dip(
        symbol="INVE-B.ST",
        closes=closes,
        roic=0.22,
        highs=highs,
        lows=lows,
        current_price=117.0
    )

    assert candidate is not None
    assert candidate.symbol == "INVE-B.ST"
    assert candidate.strategy_name == "Mean-Reversion Dip"
    assert candidate.metrics["roic"] == 0.22
    assert candidate.metrics["rsi"] <= 38.0
    assert candidate.metrics["rsi"] > candidate.metrics["prev_rsi"]
    assert candidate.stop_loss < 117.0
    assert candidate.target_price > 117.0


def test_strategy_2_mean_reversion_dip_low_roic_rejected():
    desk = SignalsDesk()

    closes = [150.0] * 30 + [145.0, 140.0, 134.0, 128.0, 122.0, 118.0, 115.0, 117.0]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]

    # Low ROIC company (5% ROIC < 12% hurdle)
    candidate = desk.evaluate_mean_reversion_dip(
        symbol="JUNK",
        closes=closes,
        roic=0.05,
        highs=highs,
        lows=lows,
        current_price=117.0
    )

    assert candidate is None  # Rejected by quality hurdle


def test_strategy_3_composite_swing_success():
    desk = SignalsDesk()

    # Trend alignment: upward trend with natural swing waves keeping RSI in healthy 40-70 range
    # Create 220 data points
    base = 100.0
    closes = [base + i * 0.15 + (1.5 if i % 4 < 2 else -1.0) for i in range(220)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    # RVOL >= 1.1x
    volumes = [1000.0] * 219 + [1300.0]

    candidate = desk.evaluate_composite_swing(
        symbol="SAAB-B.ST",
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        current_price=closes[-1]
    )

    assert candidate is not None
    assert candidate.symbol == "SAAB-B.ST"
    assert candidate.strategy_name == "Multi-Factor Composite Swing"
    assert candidate.rr_ratio >= 2.0
    assert candidate.metrics["trend_aligned"] is True
    assert candidate.metrics["rvol"] >= 1.10
    assert 40.0 <= candidate.metrics["rsi"] <= 70.0


def test_scan_signals_and_card_formatting():
    desk = SignalsDesk()

    # Inject two symbols into the desk's cache
    # Symbol 1: Breakout setup
    closes_bo = [100.0 + (i % 10) for i in range(35)] + [111.0, 112.5, 114.0, 116.0, 118.0]
    highs_bo = [c + 1.0 for c in closes_bo]
    lows_bo = [c - 1.0 for c in closes_bo]
    vols_bo = [1000.0] * 39 + [2000.0]
    desk.set_cached_history("CEG", closes=closes_bo, highs=highs_bo, lows=lows_bo, volumes=vols_bo)

    # Symbol 2: Dip buyer setup
    closes_dip = [150.0] * 30 + [145.0, 140.0, 134.0, 128.0, 122.0, 118.0, 115.0, 117.0]
    desk.set_cached_history("ATCO-B.ST", closes=closes_dip, roic=0.20)

    # Scan signals
    candidates = desk.scan_signals(["CEG", "ATCO-B.ST"])
    assert len(candidates) >= 1
    found_symbols = [c.symbol for c in candidates]
    assert "CEG" in found_symbols or "ATCO-B.ST" in found_symbols

    # Format card
    card = desk.format_signals_card(candidates)
    assert "SIGNALS DESK" in card
    assert "Risk/Reward:" in card
    assert "Entry:" in card
    assert "Stop:" in card
    assert "Target:" in card


def test_strategy_2_mean_reversion_dip_rsi_falling_rejected():
    desk = SignalsDesk()

    # Oversold but continuing to fall (RSI_t < RSI_{t-1}, no upward hook)
    closes = [150.0] * 30 + [145.0, 140.0, 134.0, 128.0, 122.0, 118.0, 115.0, 111.0]
    candidate = desk.evaluate_mean_reversion_dip(
        symbol="INVE-B.ST",
        closes=closes,
        roic=0.20,
        current_price=111.0
    )
    assert candidate is None


def test_strategy_3_composite_swing_rvol_low_rejected():
    desk = SignalsDesk()

    base = 100.0
    closes = [base + i * 0.15 + (1.5 if i % 4 < 2 else -1.0) for i in range(220)]
    # RVOL < 1.10x
    volumes = [1000.0] * 219 + [950.0]

    candidate = desk.evaluate_composite_swing(
        symbol="SAAB-B.ST",
        closes=closes,
        volumes=volumes,
        current_price=closes[-1]
    )
    assert candidate is None


def test_signals_empty_scan_card():
    desk = SignalsDesk()
    card = desk.format_signals_card([])
    assert "SIGNALS DESK" in card
    assert "None currently meeting strict mathematical hurdles" in card

