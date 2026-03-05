"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Risk Manager                                    ║
║  Aggressive position sizing with mathematical circuit breakers       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import time
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from config import HydraConfig
from exchange import ExchangeClient
from database import HydraDatabase
from analysis import EntrySignal

logger = logging.getLogger("hydra.risk")


class CircuitBreakerTripped(Exception):
    """Raised when a circuit breaker condition is met."""
    pass


class RiskManager:
    """
    Position sizing and risk control.
    
    Sizing Formula (Modified Kelly with Leverage):
    ─────────────────────────────────────────────
    equity = total USDC net asset in margin account
    risk_amount = equity × RISK_PER_TRADE_PCT
    distance_to_sl = |entry_price - sl_price|
    position_size = risk_amount / distance_to_sl
    notional_value = position_size × entry_price
    
    With 5x leverage:
    - Max borrowing = 4x equity (5x total exposure)
    - Effective position = min(calculated_size, max_position)
    
    Capital Compounding:
    - equity grows after each profitable trade
    - position_size automatically scales up with equity
    - losses reduce equity → position_size auto-scales down
    """

    def __init__(self, config: HydraConfig, exchange: ExchangeClient, db: HydraDatabase):
        self.config = config
        self.exchange = exchange
        self.db = db
        self._circuit_breaker_active = False

    def calculate_position_size(self, signal: EntrySignal) -> dict:
        """
        Calculate exact position size for a given signal.
        
        Returns dict with:
        - quantity: Position size in base asset (ETH)
        - notional: Position value in USDC
        - risk_usdc: Actual USDC at risk
        - leverage_used: Effective leverage ratio
        """
        cfg = self.config.risk
        pair = self.config.pair

        # Get current equity
        equity = self.exchange.get_usdc_equity()
        logger.info(f"Current USDC equity: ${equity:.2f}")

        if equity < cfg.MIN_TRADE_USDC:
            raise ValueError(f"Equity ${equity:.2f} below minimum ${cfg.MIN_TRADE_USDC}")

        # Circuit breaker check
        self._check_circuit_breakers(equity)

        # Calculate risk amount
        risk_amount = equity * cfg.RISK_PER_TRADE_PCT  # e.g. $1000 × 4% = $40

        # Distance to stop loss
        sl_distance = abs(signal.entry_price - signal.sl_price)
        if sl_distance == 0:
            raise ValueError("SL distance is zero — cannot calculate position size")

        sl_pct = sl_distance / signal.entry_price  # e.g. 0.02 = 2%

        # Position size in base asset
        # risk_amount = quantity × sl_distance
        # quantity = risk_amount / sl_distance
        quantity = risk_amount / sl_distance

        # Notional value
        notional = quantity * signal.entry_price

        # Cap at max position percentage of equity × leverage
        max_notional = equity * cfg.LEVERAGE * cfg.MAX_POSITION_PCT
        if notional > max_notional:
            notional = max_notional
            quantity = notional / signal.entry_price
            logger.info(f"Position capped at max notional: ${max_notional:.2f}")

        # Minimum quantity check
        if quantity < pair.MIN_QTY:
            raise ValueError(
                f"Calculated quantity {quantity:.6f} below minimum {pair.MIN_QTY}"
            )

        # Minimum notional check
        if notional < pair.MIN_NOTIONAL:
            raise ValueError(
                f"Notional ${notional:.2f} below minimum ${pair.MIN_NOTIONAL}"
            )

        # Precision truncation
        quantity = float(
            Decimal(str(quantity)).quantize(
                Decimal(10) ** -pair.QTY_PRECISION, rounding=ROUND_DOWN
            )
        )

        effective_leverage = notional / equity if equity > 0 else 0

        result = {
            "quantity": quantity,
            "notional": round(notional, 2),
            "risk_usdc": round(risk_amount, 2),
            "equity": round(equity, 2),
            "sl_distance_pct": round(sl_pct * 100, 3),
            "leverage_used": round(effective_leverage, 2),
        }

        logger.info(
            f"POSITION SIZE: {quantity:.4f} ETH = ${notional:.2f} notional | "
            f"Risk: ${risk_amount:.2f} ({cfg.RISK_PER_TRADE_PCT:.0%}) | "
            f"Leverage: {effective_leverage:.1f}x | SL dist: {sl_pct*100:.2f}%"
        )
        return result

    def _check_circuit_breakers(self, current_equity: float):
        """
        Circuit breakers to prevent catastrophic losses.
        
        Triggers:
        1. Daily drawdown exceeds MAX_DAILY_DRAWDOWN_PCT
        2. Total drawdown from peak exceeds MAX_TOTAL_DRAWDOWN_PCT
        """
        cfg = self.config.risk

        # ── Daily Drawdown Check ──
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_pnl = self.db.get_daily_pnl(today_str)
        start_equity = self.db.get_state("daily_start_equity", current_equity)

        if start_equity > 0:
            daily_dd = -daily_pnl / start_equity if daily_pnl < 0 else 0
            if daily_dd >= cfg.MAX_DAILY_DRAWDOWN_PCT:
                self._circuit_breaker_active = True
                raise CircuitBreakerTripped(
                    f"DAILY DRAWDOWN BREAKER: {daily_dd:.1%} loss today "
                    f"(limit: {cfg.MAX_DAILY_DRAWDOWN_PCT:.0%}). "
                    f"Trading halted until next UTC day."
                )

        # ── Total Drawdown from Peak ──
        peak_equity = self.db.get_peak_equity()
        if peak_equity > 0:
            total_dd = (peak_equity - current_equity) / peak_equity
            if total_dd >= cfg.MAX_TOTAL_DRAWDOWN_PCT:
                self._circuit_breaker_active = True
                raise CircuitBreakerTripped(
                    f"TOTAL DRAWDOWN BREAKER: {total_dd:.1%} from peak ${peak_equity:.2f} "
                    f"(limit: {cfg.MAX_TOTAL_DRAWDOWN_PCT:.0%}). "
                    f"MANUAL INTERVENTION REQUIRED."
                )

        # Record equity snapshot
        self.db.snapshot_equity(
            equity=current_equity,
            free_margin=current_equity,  # Simplified
            positions=len(self.db.get_open_trades()),
        )

    def calculate_partial_close_qty(self, total_qty: float, tp_level: int) -> float:
        """Calculate quantity to close at a given TP level."""
        cfg = self.config.risk
        pair = self.config.pair

        if tp_level == 1:
            qty = total_qty * cfg.TP1_CLOSE_PCT
        elif tp_level == 2:
            qty = total_qty * cfg.TP2_CLOSE_PCT
        else:
            qty = total_qty

        # Apply precision
        qty = float(
            Decimal(str(qty)).quantize(
                Decimal(10) ** -pair.QTY_PRECISION, rounding=ROUND_DOWN
            )
        )
        return max(qty, pair.MIN_QTY)

    def is_circuit_breaker_active(self) -> bool:
        """Check if any circuit breaker is currently active."""
        if self._circuit_breaker_active:
            # Check if daily breaker should reset (new UTC day)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            last_breaker_day = self.db.get_state("circuit_breaker_day", "")
            if last_breaker_day != today_str:
                self._circuit_breaker_active = False
                self.db.set_state("circuit_breaker_day", "")
                logger.info("Daily circuit breaker reset — new UTC day")
                return False
        return self._circuit_breaker_active

    def set_daily_start_equity(self):
        """Record equity at start of new trading day."""
        try:
            equity = self.exchange.get_usdc_equity()
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            last_date = self.db.get_state("daily_equity_date", "")

            if last_date != today_str:
                self.db.set_state("daily_start_equity", equity)
                self.db.set_state("daily_equity_date", today_str)
                logger.info(f"Daily start equity set: ${equity:.2f} for {today_str}")
        except Exception as e:
            logger.error(f"Failed to set daily start equity: {e}")
