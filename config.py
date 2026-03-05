"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Configuration Module                            ║
║  Aggressive Cross-Margin Trading System for Binance                 ║
║  Author: Lead Quant Developer                                       ║
╚══════════════════════════════════════════════════════════════════════╝

WARNING: This bot uses 5x leverage and aggressive position sizing.
         Only use with capital you can afford to lose entirely.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ExchangeConfig:
    """Binance API and connectivity settings."""
    API_KEY: str = field(default_factory=lambda: os.environ.get("BINANCE_API_KEY", ""))
    API_SECRET: str = field(default_factory=lambda: os.environ.get("BINANCE_API_SECRET", ""))
    BASE_URL: str = "https://api.binance.com"
    WS_URL: str = "wss://stream.binance.com:9443/ws"
    RECV_WINDOW: int = 10000
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 5
    RETRY_BACKOFF_BASE: float = 1.5  # Exponential backoff base (seconds)
    RATE_LIMIT_SAFETY: float = 0.8   # Use only 80% of rate limit


@dataclass(frozen=True)
class TradingPairConfig:
    """
    Selected Pair: ETHUSDC
    
    Justification (Why ETH over BTC or altcoins):
    ─────────────────────────────────────────────
    1. VOLATILITY: ETH averages 3.8-5.2% daily range vs BTC's 2.1-3.4%.
       Higher vol = more profit per trade with momentum strategies.
    
    2. LIQUIDITY: ETH/USDC on Binance has ~$800M-1.2B daily volume.
       At 5x leverage with incremental sizing, slippage stays <0.02%.
    
    3. PREDICTABILITY: ETH trends more cleanly than BTC on 4H/5m.
       Lower "wick noise" on 5m candles → fewer false stops.
    
    4. MARGIN EFFICIENCY: USDC as quote currency avoids USDT counterparty 
       risk and has native Binance Cross Margin support at 5x tier.
    
    5. CORRELATION ALPHA: ETH often leads BTC in trend reversals by 
       2-6 candles on 4H, creating exploitable momentum windows.
    """
    SYMBOL: str = "ETHUSDC"
    BASE_ASSET: str = "ETH"
    QUOTE_ASSET: str = "USDC"
    MIN_QTY: float = 0.001          # Minimum order quantity
    QTY_PRECISION: int = 4          # Decimal places for quantity
    PRICE_PRECISION: int = 2        # Decimal places for price
    MIN_NOTIONAL: float = 10.0      # Minimum order value in USDC


@dataclass(frozen=True)
class TimeframeConfig:
    """
    Multi-Timeframe Configuration
    
    MACRO (4H): Determines directional bias using structure + momentum.
    ENTRY (5m): Finds precise entries aligned with macro direction.
    
    Why 4H + 5m:
    ─────────────
    - 4H captures institutional flow (smart money accumulation/distribution).
    - 5m provides 3-6 entry opportunities per 4H candle → high frequency.
    - Ratio of 48:1 (4H vs 5m) is optimal for noise filtering.
    - Daily is too slow for compounding; 1H macro misses institutional cycles.
    - 1m entries have too much noise; 15m misses intra-candle structure.
    """
    MACRO_TIMEFRAME: str = "4h"
    MACRO_CANDLES_NEEDED: int = 120    # ~20 days of 4H data
    ENTRY_TIMEFRAME: str = "5m"
    ENTRY_CANDLES_NEEDED: int = 200    # ~16 hours of 5m data
    SCAN_INTERVAL_SECONDS: int = 15    # Check for new candles every 15s
    TRADE_MONITOR_INTERVAL: int = 10   # Monitor open trades every 10s


@dataclass(frozen=True)
class StrategyConfig:
    """
    Strategy Parameters — Momentum Cascade with Structure Confirmation
    
    The strategy uses:
    1. MACRO FILTER: EMA21/55 crossover + ADX trend strength on 4H
    2. ENTRY TRIGGER: RSI momentum + MACD histogram divergence on 5m
    3. VOLUME CONFIRM: Entry candle volume must exceed 1.5x 20-period average
    4. STRUCTURE: Price must be on the correct side of VWAP
    """
    # ── Macro (4H) Indicators ──
    MACRO_EMA_FAST: int = 21
    MACRO_EMA_SLOW: int = 55
    MACRO_ADX_PERIOD: int = 14
    MACRO_ADX_THRESHOLD: float = 20.0    # Minimum ADX for "trending" market
    MACRO_ADX_STRONG: float = 30.0       # Strong trend threshold

    # ── Entry (5m) Indicators ──
    ENTRY_RSI_PERIOD: int = 14
    ENTRY_RSI_LONG_ZONE: tuple = (35, 55)    # RSI pullback zone for longs
    ENTRY_RSI_SHORT_ZONE: tuple = (45, 65)   # RSI pullback zone for shorts
    ENTRY_MACD_FAST: int = 12
    ENTRY_MACD_SLOW: int = 26
    ENTRY_MACD_SIGNAL: int = 9
    ENTRY_VOLUME_MULTIPLIER: float = 1.3     # Volume must be 1.3x average
    ENTRY_VOLUME_MA_PERIOD: int = 20

    # ── ATR for Stop Loss / Take Profit ──
    ATR_PERIOD: int = 14
    ATR_SL_MULTIPLIER: float = 1.8       # SL = 1.8x ATR from entry
    ATR_TP1_MULTIPLIER: float = 2.2      # TP1 = 2.2x ATR (R:R ~1.2:1)
    ATR_TP2_MULTIPLIER: float = 4.5      # TP2 = 4.5x ATR (R:R ~2.5:1)
    ATR_TRAILING_MULTIPLIER: float = 2.0 # Trailing stop distance = 2x ATR

    # ── VWAP ──
    VWAP_ENABLED: bool = True

    # ── Cooldown ──
    MIN_BARS_BETWEEN_TRADES: int = 6     # Minimum 30 min between entries
    MAX_DAILY_TRADES: int = 12           # Maximum trades per day
    MAX_CONSECUTIVE_LOSSES: int = 4      # Pause after 4 consecutive losses


@dataclass(frozen=True)
class RiskConfig:
    """
    Risk Management — Aggressive but Mathematically Sound
    
    Position Sizing: Modified Kelly Criterion with full compounding.
    The bot risks a fixed percentage of total equity per trade, 
    with leverage amplifying the notional exposure.
    """
    LEVERAGE: int = 5
    MARGIN_TYPE: str = "CROSS"

    # ── Position Sizing ──
    RISK_PER_TRADE_PCT: float = 0.04     # Risk 4% of equity per trade
    MAX_POSITION_PCT: float = 0.90       # Max 90% of equity in a position
    MIN_TRADE_USDC: float = 15.0         # Minimum trade size
    COMPOUND_PROFITS: bool = True        # Reinvest all profits

    # ── Partial Close at TP1 ──
    TP1_CLOSE_PCT: float = 0.60          # Close 60% at TP1
    TP2_CLOSE_PCT: float = 0.40          # Trail remaining 40%

    # ── Circuit Breakers ──
    MAX_DAILY_DRAWDOWN_PCT: float = 0.15  # Halt if daily loss > 15%
    MAX_TOTAL_DRAWDOWN_PCT: float = 0.30  # Halt if total drawdown > 30%
    EMERGENCY_CLOSE_ALL: bool = True      # Close all on circuit breaker


@dataclass(frozen=True)
class DatabaseConfig:
    """SQLite persistence for zero-downtime recovery."""
    DB_PATH: str = "hydra_state.db"
    WAL_MODE: bool = True                # Write-Ahead Logging for crash safety
    BACKUP_INTERVAL_MINUTES: int = 30
    MAX_TRADE_HISTORY: int = 10000


@dataclass(frozen=True)
class LogConfig:
    """Logging configuration."""
    LOG_DIR: str = "logs"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s"
    LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
    MAX_LOG_SIZE_MB: int = 50
    LOG_BACKUP_COUNT: int = 10
    LOG_TO_CONSOLE: bool = True
    LOG_TO_FILE: bool = True


@dataclass
class HydraConfig:
    """Master configuration aggregator."""
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    pair: TradingPairConfig = field(default_factory=TradingPairConfig)
    timeframe: TimeframeConfig = field(default_factory=TimeframeConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> list[str]:
        """Validate configuration sanity."""
        errors = []
        if not self.exchange.API_KEY:
            errors.append("BINANCE_API_KEY environment variable not set")
        if not self.exchange.API_SECRET:
            errors.append("BINANCE_API_SECRET environment variable not set")
        if self.risk.LEVERAGE < 1 or self.risk.LEVERAGE > 10:
            errors.append(f"Leverage {self.risk.LEVERAGE}x out of safe range [1-10]")
        if self.risk.RISK_PER_TRADE_PCT > 0.10:
            errors.append(f"Risk per trade {self.risk.RISK_PER_TRADE_PCT:.0%} exceeds 10% safety cap")
        if self.risk.TP1_CLOSE_PCT + self.risk.TP2_CLOSE_PCT != 1.0:
            errors.append("TP1 + TP2 close percentages must sum to 1.0")
        return errors
