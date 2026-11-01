"""
core/models.py - Pydantic models for the 696.50-Point Multi-Asset Barbell Mandate Sentinel.
"""

from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Union
from pydantic import BaseModel, Field


class AlertSeverity(str, Enum):
    P0 = "P0"  # Normal / Informational / Pre-market briefings
    P1 = "P1"  # High / Actionable order, trim, or nearing limit
    P2 = "P2"  # Emergency / Hard stop breach or derivative curfew


class AccountWrapper(str, Enum):
    AVANZA_AF = "avanza_af"
    AVANZA_ISK = "avanza_isk"
    AVANZA_KF = "avanza_kf"
    REVOLUT_INVEST = "revolut_invest"


class StopLossType(str, Enum):
    HARD_STOP = "hard_stop"
    EMERGENCY_HARD_STOP = "emergency_hard_stop"
    BREAKEVEN = "breakeven"
    PERCENTAGE_DRAWDOWN = "percentage_drawdown"
    PERCENTAGE_FROM_FILL = "percentage_from_fill"
    VOLATILITY_PCT = "volatility_pct"


class OrderType(str, Enum):
    LIMIT_BUY = "limit_buy"
    LIMIT_SELL = "limit_sell"
    STOP_LOSS = "stop_loss"


class HorizonBucket(str, Enum):
    BUCKET_1_SHORT_TERM_HIGH_RISK = "BUCKET_1_SHORT_TERM_HIGH_RISK"
    BUCKET_2_MEDIUM_TERM_SWING = "BUCKET_2_MEDIUM_TERM_SWING"
    BUCKET_3_LONG_TERM_COMPOUNDER = "BUCKET_3_LONG_TERM_COMPOUNDER"
    BUCKET_4_LOW_RISK_ANCHOR_AND_CASH = "BUCKET_4_LOW_RISK_ANCHOR_AND_CASH"


class PositionTier(str, Enum):
    TIER_1_CORE = "TIER_1_CORE"
    TIER_2_SATELLITE = "TIER_2_SATELLITE"


class MiniFutureSpec(BaseModel):
    underlying_symbol: str
    financing_level: float
    stop_loss_barrier: float
    parity: float = 10.0
    fx_pair: str = "USDSEK=X"
    issuer_residual_floor_pct: float = 0.50
    leverage: Optional[float] = None


class RecoveryTrancheConfig(BaseModel):
    bounce_pct: float
    points_to_trim: float
    target_price: Optional[float] = None
    executed: bool = False


class CurfewConfig(BaseModel):
    enforced: bool = True
    cutoff_time: str = "19:45"
    warning_1: str = "19:30"
    warning_2: str = "19:40"
    timezone: str = "Europe/Stockholm"
    directive: str = "Mandatory flat book intraday curfew"


class StopLossConfig(BaseModel):
    price: Optional[float] = None
    type: str = "hard_stop"
    action: str = "exit_position"
    percentage: Optional[float] = None
    trailing_pct: Optional[float] = None
    max_risk_pts: Optional[float] = None


class LimitSellConfig(BaseModel):
    target_price: float
    points_to_trim: float
    target_core_points: Optional[float] = None


class TacticalTrancheConfig(BaseModel):
    points: float
    stop_loss_price: float


class Holding(BaseModel):
    symbol: str
    name: str
    points: float
    account: str
    classification: str
    currency: str = "USD"
    cost_basis: Optional[float] = None
    current_price: Optional[float] = None
    status: str = "active"
    notes: Optional[str] = None
    instrument_type: Optional[str] = "equity"
    underlying: Optional[str] = None
    ticker_override: Optional[str] = None
    stop_loss: Optional[StopLossConfig] = None
    limit_sell: Optional[LimitSellConfig] = None
    limit_sells: Optional[List[LimitSellConfig]] = None
    tactical_tranche: Optional[TacticalTrancheConfig] = None
    curfew: Optional[CurfewConfig] = None
    time_sensitive: bool = False
    earnings_date: Optional[str] = None
    catalyst_date: Optional[str] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    # V3.1 Architecture Upgrade extensions
    baseline_points: Optional[float] = None
    reported_pnl_pct: Optional[float] = None
    reference_price: Optional[float] = None
    horizon_bucket: Optional[Union[HorizonBucket, str]] = None
    position_tier: Optional[Union[PositionTier, str]] = None
    mini_future_spec: Optional[MiniFutureSpec] = None
    recovery_tranches: Optional[List[RecoveryTrancheConfig]] = None

    def get_baseline_points(self) -> float:
        return self.baseline_points if self.baseline_points is not None else self.points


class CashAccount(BaseModel):
    account_name: str
    points: float
    currency: str = "SEK"
    purpose: str
    committed_points: float = 0.0
    committed_target: Optional[str] = None
    free_points: Optional[float] = None
    schablonskatt_exempt: bool = False
    restrictions: Optional[str] = None

    def get_free_points(self) -> float:
        if self.free_points is not None:
            return self.free_points
        return max(0.0, self.points - self.committed_points)


class MandateConfig(BaseModel):
    total_points: float = 688.30
    benchmark_hwm: float = 696.50
    initial_capital: float = 696.50
    invested_points: float = 305.90
    cash_points: float = 382.40
    base_currency: str = "SEK"
    risk_budget_pct: float = 0.0050  # 0.50%
    max_single_trade_risk_pts: float = 3.44  # 0.0050 * 688.30 Live NAV


class RestingOrder(BaseModel):
    symbol: str
    name: str
    account: str
    order_type: str
    target_price: float
    points: float
    currency: str = "SEK"
    status: str = "resting"


class WatchlistTranche(BaseModel):
    tranche: str
    points: float
    target_price: float
    min_mos_pct: Optional[float] = None


class WatchlistTarget(BaseModel):
    symbol: str
    name: str
    currency: str = "USD"
    tranches: List[WatchlistTranche] = Field(default_factory=list)
    stop_loss: Optional[float] = None
    notes: Optional[str] = None


class TimeSensitiveEvent(BaseModel):
    symbol: str
    event_type: str  # earnings, expiration, curfew, binary_catalyst
    event_date: str  # YYYY-MM-DD or date string
    description: str
    directive: Optional[str] = None
    days_warning_threshold: int = 5
    status: Optional[str] = "active"


class PortfolioState(BaseModel):
    mandate: MandateConfig
    cash_accounts: Dict[str, CashAccount]
    holdings: List[Holding]
    resting_orders: List[RestingOrder] = Field(default_factory=list)
    watchlist: List[WatchlistTarget] = Field(default_factory=list)
    time_sensitive_events: List[TimeSensitiveEvent] = Field(default_factory=list)
    liquidated_assets: List[Dict[str, Any]] = Field(default_factory=list)
    blacklisted_assets: List[Dict[str, Any]] = Field(default_factory=list)
    partial_trims: List[Dict[str, Any]] = Field(default_factory=list)


class MarketQuote(BaseModel):
    symbol: str
    price: float
    change: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    previous_close: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    currency: str = "USD"
    bid: Optional[float] = None
    ask: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    is_live: bool = True
    delay_seconds: float = 0.0


class MacroMetric(BaseModel):
    symbol: str
    name: str
    current_value: float
    neutral_low: float
    neutral_high: float
    bearish_barrier: float
    bullish_barrier: float
    unit: str
    status: str = "neutral"  # neutral, bearish_breach, bullish_relief
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertEvent(BaseModel):
    id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: AlertSeverity
    symbol: Optional[str] = None
    title: str
    message: str
    source: str = "sentinel"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GateAuditResult(BaseModel):
    gate_number: int
    gate_name: str
    passed: bool
    score: Optional[float] = None
    threshold: Optional[str] = None
    details: str
    recommendation: Optional[str] = None


class DeepDiveAnalysis(BaseModel):
    symbol: str
    company_name: str
    market_price: float
    currency: str
    cost_of_equity_hurdle: float
    intrinsic_value: float
    margin_of_safety_pct: float
    gate_results: List[GateAuditResult]
    overall_passed: bool
    recommended_wrapper: str
    max_position_points_gate4: float
    current_volatility_pct: float
    summary: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
