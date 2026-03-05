"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Trade Manager                                   ║
║  Order execution, dynamic SL/TP, partial closes, trailing stops     ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

from config import HydraConfig
from exchange import ExchangeClient, BinanceAPIError
from database import HydraDatabase, TradeRecord, TradeState, TradeSide
from analysis import EntrySignal
from risk_manager import RiskManager

logger = logging.getLogger("hydra.trades")


class TradeManager:
    """
    Professional trade lifecycle management.
    
    State Machine:
    ──────────────
    PENDING → OPEN → TP1_HIT → TRAILING → CLOSED
                 ↓                   ↓
            STOPPED_OUT          STOPPED_OUT
                 ↓                   ↓
              CLOSED               CLOSED
    
    SL Management:
    ──────────────
    1. Initial: SL at entry - ATR × 1.8 (dynamic, structure-based)
    2. After TP1: SL moves to breakeven (entry price)
    3. During trailing: SL = trailing_high - ATR × 2.0 (for longs)
    """

    def __init__(
        self, config: HydraConfig, exchange: ExchangeClient,
        db: HydraDatabase, risk: RiskManager,
    ):
        self.config = config
        self.exchange = exchange
        self.db = db
        self.risk = risk

    def open_trade(self, signal: EntrySignal, macro_bias: str) -> Optional[TradeRecord]:
        """
        Execute a new trade based on entry signal.
        
        Steps:
        1. Calculate position size
        2. Place market entry order (auto-borrow for cross margin)
        3. Record trade in database
        4. Place stop-loss order
        """
        cfg = self.config
        symbol = cfg.pair.SYMBOL

        try:
            # ── Step 1: Position Sizing ──
            sizing = self.risk.calculate_position_size(signal)
            quantity = sizing["quantity"]

            if quantity <= 0:
                logger.error("Position size is zero — aborting trade")
                return None

            # ── Step 2: Create Trade Record ──
            trade_id = f"{symbol}_{int(time.time())}_{signal.side}"
            trade = TradeRecord(
                trade_id=trade_id,
                symbol=symbol,
                side=signal.side,
                state=TradeState.PENDING.value,
                entry_qty=quantity,
                remaining_qty=quantity,
                sl_price=signal.sl_price,
                tp1_price=signal.tp1_price,
                tp2_price=signal.tp2_price,
                atr_at_entry=signal.atr_value,
                macro_bias=macro_bias,
                entry_reason=signal.reason,
                opened_at=time.time(),
            )
            self.db.save_trade(trade)

            # ── Step 3: Market Entry ──
            if signal.side == "LONG":
                # BUY with auto-borrow
                order = self.exchange.market_buy_margin(
                    symbol=symbol,
                    quantity=quantity,
                    qty_precision=cfg.pair.QTY_PRECISION,
                    auto_borrow=True,
                )
            else:
                # SELL (short) with auto-borrow
                order = self.exchange.market_sell_margin(
                    symbol=symbol,
                    quantity=quantity,
                    qty_precision=cfg.pair.QTY_PRECISION,
                    auto_borrow=True,
                )

            # Parse fill price from order response
            fill_price = self._parse_fill_price(order)
            trade.entry_price = fill_price
            trade.entry_order_id = str(order.get("orderId", ""))
            trade.state = TradeState.OPEN.value

            # Recalculate SL/TP based on actual fill price
            atr = signal.atr_value
            if signal.side == "LONG":
                trade.sl_price = round(fill_price - cfg.strategy.ATR_SL_MULTIPLIER * atr, 2)
                trade.tp1_price = round(fill_price + cfg.strategy.ATR_TP1_MULTIPLIER * atr, 2)
                trade.tp2_price = round(fill_price + cfg.strategy.ATR_TP2_MULTIPLIER * atr, 2)
                trade.trailing_high = fill_price
            else:
                trade.sl_price = round(fill_price + cfg.strategy.ATR_SL_MULTIPLIER * atr, 2)
                trade.tp1_price = round(fill_price - cfg.strategy.ATR_TP1_MULTIPLIER * atr, 2)
                trade.tp2_price = round(fill_price - cfg.strategy.ATR_TP2_MULTIPLIER * atr, 2)
                trade.trailing_high = fill_price  # For shorts, this tracks the lowest price

            self.db.save_trade(trade)

            # ── Step 4: Place Stop-Loss Order ──
            self._place_stop_loss(trade)

            # Record last trade time
            self.db.set_state("last_trade_open_time", time.time())

            # Calculate fees from order
            fees = self._parse_fees(order)
            trade.pnl_fees = fees
            self.db.save_trade(trade)

            logger.info(
                f"═══ TRADE OPENED ═══ {trade.trade_id}\n"
                f"  Side: {trade.side} | Entry: ${trade.entry_price:.2f}\n"
                f"  Qty: {trade.entry_qty:.4f} ETH (${sizing['notional']:.2f})\n"
                f"  SL: ${trade.sl_price:.2f} | TP1: ${trade.tp1_price:.2f} | TP2: ${trade.tp2_price:.2f}\n"
                f"  Risk: ${sizing['risk_usdc']:.2f} | Leverage: {sizing['leverage_used']:.1f}x"
            )
            return trade

        except Exception as e:
            logger.error(f"Failed to open trade: {e}", exc_info=True)
            # If order was placed but recording failed, we need reconciliation
            if "trade" in locals() and trade.entry_order_id:
                trade.state = TradeState.ERROR.value
                self.db.save_trade(trade)
            return None

    def monitor_trade(self, trade: TradeRecord) -> TradeRecord:
        """
        Monitor an open trade and manage SL/TP.
        Called every TRADE_MONITOR_INTERVAL seconds.
        
        Returns updated TradeRecord.
        """
        if trade.state in (TradeState.CLOSED.value, TradeState.CANCELLED.value):
            return trade

        try:
            current_price = self.exchange.get_ticker_price(trade.symbol)
        except Exception as e:
            logger.error(f"Failed to get price for {trade.symbol}: {e}")
            return trade

        try:
            if trade.state == TradeState.OPEN.value:
                trade = self._handle_open_state(trade, current_price)
            elif trade.state == TradeState.TP1_HIT.value:
                trade = self._handle_tp1_state(trade, current_price)
            elif trade.state == TradeState.TRAILING.value:
                trade = self._handle_trailing_state(trade, current_price)
        except Exception as e:
            logger.error(f"Error monitoring trade {trade.trade_id}: {e}", exc_info=True)

        return trade

    def _handle_open_state(self, trade: TradeRecord, price: float) -> TradeRecord:
        """Handle trade in OPEN state — check SL and TP1."""
        is_long = trade.side == TradeSide.LONG.value

        # ── Check Stop Loss ──
        if (is_long and price <= trade.sl_price) or (not is_long and price >= trade.sl_price):
            logger.warning(f"SL HIT for {trade.trade_id} at ${price:.2f} (SL: ${trade.sl_price:.2f})")
            return self._close_trade(trade, price, "STOP_LOSS")

        # ── Check TP1 ──
        if (is_long and price >= trade.tp1_price) or (not is_long and price <= trade.tp1_price):
            logger.info(f"TP1 HIT for {trade.trade_id} at ${price:.2f}")
            return self._execute_tp1(trade, price)

        return trade

    def _handle_tp1_state(self, trade: TradeRecord, price: float) -> TradeRecord:
        """Handle trade after TP1 — SL at breakeven, waiting for TP2 or trailing."""
        is_long = trade.side == TradeSide.LONG.value

        # SL is now at breakeven (entry price)
        be_price = trade.entry_price

        # ── Check Breakeven Stop ──
        if (is_long and price <= be_price) or (not is_long and price >= be_price):
            logger.info(f"BE STOP for {trade.trade_id} at ${price:.2f}")
            return self._close_trade(trade, price, "BREAKEVEN_STOP")

        # ── Activate Trailing ──
        # Once price moves beyond TP1 by 0.5x ATR, activate trailing stop
        trail_activation = trade.atr_at_entry * 0.5
        if is_long:
            if price > trade.tp1_price + trail_activation:
                trade.state = TradeState.TRAILING.value
                trade.trailing_high = price
                trail_dist = self.config.strategy.ATR_TRAILING_MULTIPLIER * trade.atr_at_entry
                trade.trailing_stop = round(price - trail_dist, 2)
                self.db.save_trade(trade)
                self._update_stop_loss_order(trade, trade.trailing_stop)
                logger.info(
                    f"TRAILING activated for {trade.trade_id}: "
                    f"high=${price:.2f} trail_stop=${trade.trailing_stop:.2f}"
                )
        else:
            if price < trade.tp1_price - trail_activation:
                trade.state = TradeState.TRAILING.value
                trade.trailing_high = price  # For shorts, tracks lowest
                trail_dist = self.config.strategy.ATR_TRAILING_MULTIPLIER * trade.atr_at_entry
                trade.trailing_stop = round(price + trail_dist, 2)
                self.db.save_trade(trade)
                self._update_stop_loss_order(trade, trade.trailing_stop)
                logger.info(
                    f"TRAILING activated for {trade.trade_id}: "
                    f"low=${price:.2f} trail_stop=${trade.trailing_stop:.2f}"
                )

        return trade

    def _handle_trailing_state(self, trade: TradeRecord, price: float) -> TradeRecord:
        """Handle trade with trailing stop active."""
        is_long = trade.side == TradeSide.LONG.value
        trail_dist = self.config.strategy.ATR_TRAILING_MULTIPLIER * trade.atr_at_entry

        if is_long:
            # ── Check Trailing Stop Hit ──
            if price <= trade.trailing_stop:
                logger.info(f"TRAILING STOP HIT for {trade.trade_id} at ${price:.2f}")
                return self._close_trade(trade, price, "TRAILING_STOP")

            # ── Check TP2 ──
            if price >= trade.tp2_price:
                logger.info(f"TP2 HIT for {trade.trade_id} at ${price:.2f}")
                return self._close_trade(trade, price, "TP2_TARGET")

            # ── Update Trailing High ──
            if price > trade.trailing_high:
                trade.trailing_high = price
                new_stop = round(price - trail_dist, 2)
                if new_stop > trade.trailing_stop:
                    trade.trailing_stop = new_stop
                    self.db.save_trade(trade)
                    self._update_stop_loss_order(trade, new_stop)
                    logger.debug(
                        f"Trail updated: high=${price:.2f} stop=${new_stop:.2f}"
                    )
        else:
            # SHORT trailing
            if price >= trade.trailing_stop:
                logger.info(f"TRAILING STOP HIT for {trade.trade_id} at ${price:.2f}")
                return self._close_trade(trade, price, "TRAILING_STOP")

            if price <= trade.tp2_price:
                logger.info(f"TP2 HIT for {trade.trade_id} at ${price:.2f}")
                return self._close_trade(trade, price, "TP2_TARGET")

            if price < trade.trailing_high:
                trade.trailing_high = price
                new_stop = round(price + trail_dist, 2)
                if new_stop < trade.trailing_stop:
                    trade.trailing_stop = new_stop
                    self.db.save_trade(trade)
                    self._update_stop_loss_order(trade, new_stop)
                    logger.debug(
                        f"Trail updated: low=${price:.2f} stop=${new_stop:.2f}"
                    )

        return trade

    def _execute_tp1(self, trade: TradeRecord, price: float) -> TradeRecord:
        """
        Execute TP1: Close 60% of position, move SL to breakeven.
        """
        tp1_qty = self.risk.calculate_partial_close_qty(trade.entry_qty, tp_level=1)
        cfg = self.config

        try:
            # Cancel existing SL order
            self._cancel_stop_loss(trade)

            # Close partial position
            if trade.side == TradeSide.LONG.value:
                order = self.exchange.market_sell_margin(
                    symbol=trade.symbol,
                    quantity=tp1_qty,
                    qty_precision=cfg.pair.QTY_PRECISION,
                    auto_repay=True,
                )
            else:
                order = self.exchange.market_buy_margin(
                    symbol=trade.symbol,
                    quantity=tp1_qty,
                    qty_precision=cfg.pair.QTY_PRECISION,
                    auto_repay=True,
                )

            fill_price = self._parse_fill_price(order)
            fees = self._parse_fees(order)

            # Calculate realized P&L for partial close
            if trade.side == TradeSide.LONG.value:
                partial_pnl = (fill_price - trade.entry_price) * tp1_qty
            else:
                partial_pnl = (trade.entry_price - fill_price) * tp1_qty

            # Update trade record
            trade.remaining_qty = round(trade.entry_qty - tp1_qty, cfg.pair.QTY_PRECISION)
            trade.pnl_realized += partial_pnl
            trade.pnl_fees += fees
            trade.state = TradeState.TP1_HIT.value

            # Move SL to breakeven
            trade.sl_price = trade.entry_price
            self.db.save_trade(trade)

            # Place new SL at breakeven for remaining position
            self._place_stop_loss(trade)

            logger.info(
                f"═══ TP1 EXECUTED ═══ {trade.trade_id}\n"
                f"  Closed: {tp1_qty:.4f} ETH @ ${fill_price:.2f}\n"
                f"  P&L: ${partial_pnl:.2f} | Remaining: {trade.remaining_qty:.4f} ETH\n"
                f"  SL moved to breakeven: ${trade.entry_price:.2f}"
            )
            return trade

        except Exception as e:
            logger.error(f"TP1 execution failed: {e}", exc_info=True)
            return trade

    def _close_trade(self, trade: TradeRecord, exit_price: float, reason: str) -> TradeRecord:
        """
        Fully close a trade.
        """
        cfg = self.config

        try:
            # Cancel any existing SL order
            self._cancel_stop_loss(trade)

            # Close remaining position
            qty = trade.remaining_qty
            if qty <= 0:
                trade.state = TradeState.CLOSED.value
                trade.closed_at = time.time()
                self.db.save_trade(trade)
                return trade

            if trade.side == TradeSide.LONG.value:
                order = self.exchange.market_sell_margin(
                    symbol=trade.symbol,
                    quantity=qty,
                    qty_precision=cfg.pair.QTY_PRECISION,
                    auto_repay=True,
                )
            else:
                order = self.exchange.market_buy_margin(
                    symbol=trade.symbol,
                    quantity=qty,
                    qty_precision=cfg.pair.QTY_PRECISION,
                    auto_repay=True,
                )

            fill_price = self._parse_fill_price(order)
            fees = self._parse_fees(order)

            # Calculate P&L
            if trade.side == TradeSide.LONG.value:
                close_pnl = (fill_price - trade.entry_price) * qty
            else:
                close_pnl = (trade.entry_price - fill_price) * qty

            trade.remaining_qty = 0
            trade.pnl_realized += close_pnl
            trade.pnl_fees += fees
            trade.state = TradeState.CLOSED.value
            trade.closed_at = time.time()
            self.db.save_trade(trade)

            # Update daily stats
            total_pnl = trade.pnl_realized
            is_win = total_pnl > 0
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self.db.update_daily_stats(today_str, total_pnl, is_win, trade.pnl_fees)

            emoji = "✅" if is_win else "❌"
            logger.info(
                f"═══ TRADE CLOSED {emoji} ═══ {trade.trade_id}\n"
                f"  Reason: {reason}\n"
                f"  Exit: ${fill_price:.2f} | Entry: ${trade.entry_price:.2f}\n"
                f"  Total P&L: ${total_pnl:.2f} (fees: ${trade.pnl_fees:.2f})\n"
                f"  Duration: {(trade.closed_at - trade.opened_at)/60:.1f} min"
            )
            return trade

        except Exception as e:
            logger.error(f"Trade close failed: {e}", exc_info=True)
            trade.state = TradeState.ERROR.value
            self.db.save_trade(trade)
            return trade

    def _place_stop_loss(self, trade: TradeRecord):
        """Place a stop-loss order for the trade."""
        cfg = self.config
        try:
            # Determine SL order side (opposite of trade side)
            sl_side = "SELL" if trade.side == TradeSide.LONG.value else "BUY"

            # Limit price slightly beyond stop price for fill guarantee
            if trade.side == TradeSide.LONG.value:
                limit_price = round(trade.sl_price * 0.998, cfg.pair.PRICE_PRECISION)
            else:
                limit_price = round(trade.sl_price * 1.002, cfg.pair.PRICE_PRECISION)

            sl_client_id = f"SL_{trade.trade_id}"

            order = self.exchange.place_stop_loss_order(
                symbol=trade.symbol,
                side=sl_side,
                quantity=trade.remaining_qty,
                stop_price=trade.sl_price,
                limit_price=limit_price,
                price_precision=cfg.pair.PRICE_PRECISION,
                qty_precision=cfg.pair.QTY_PRECISION,
                client_order_id=sl_client_id,
            )

            trade.sl_order_id = sl_client_id
            self.db.save_trade(trade)
            logger.info(f"SL order placed: {sl_client_id} @ ${trade.sl_price:.2f}")

        except BinanceAPIError as e:
            # If SL placement fails, log but don't crash — we'll monitor manually
            logger.error(f"Failed to place SL order: {e}")
            # Fallback: we'll handle SL checking in monitor_trade

    def _cancel_stop_loss(self, trade: TradeRecord):
        """Cancel the existing stop-loss order."""
        if not trade.sl_order_id:
            return
        try:
            self.exchange.cancel_margin_order(
                symbol=trade.symbol,
                client_order_id=trade.sl_order_id,
            )
            logger.debug(f"SL order cancelled: {trade.sl_order_id}")
        except BinanceAPIError as e:
            if e.code == -2011:  # Unknown order (already filled or cancelled)
                logger.debug(f"SL order already gone: {trade.sl_order_id}")
            else:
                logger.error(f"Failed to cancel SL: {e}")
        trade.sl_order_id = None
        self.db.save_trade(trade)

    def _update_stop_loss_order(self, trade: TradeRecord, new_sl_price: float):
        """Cancel old SL and place new one at updated price."""
        trade.sl_price = new_sl_price
        self._cancel_stop_loss(trade)
        self._place_stop_loss(trade)

    def _parse_fill_price(self, order: dict) -> float:
        """Extract average fill price from order response."""
        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["price"]) * float(f["qty"]) for f in fills)
            return round(total_cost / total_qty, 2) if total_qty > 0 else 0
        # Fallback to cumulativeQuoteQty / executedQty
        exec_qty = float(order.get("executedQty", 0))
        cum_quote = float(order.get("cummulativeQuoteQty", 0))
        if exec_qty > 0:
            return round(cum_quote / exec_qty, 2)
        return 0.0

    def _parse_fees(self, order: dict) -> float:
        """Extract total fees from order response (in USDC equivalent)."""
        fills = order.get("fills", [])
        total_fee = 0.0
        for f in fills:
            fee = float(f.get("commission", 0))
            asset = f.get("commissionAsset", "USDC")
            if asset == "USDC":
                total_fee += fee
            elif asset == "ETH":
                # Approximate conversion
                price = float(f.get("price", 0))
                total_fee += fee * price
            elif asset == "BNB":
                # BNB fee discount — approximate
                total_fee += fee * 300  # Rough BNB/USDC
        return round(total_fee, 4)

    def emergency_close_all(self, symbol: str):
        """Emergency: close all open positions for a symbol."""
        logger.critical(f"EMERGENCY CLOSE ALL for {symbol}")
        open_trades = self.db.get_open_trades(symbol)
        for trade in open_trades:
            try:
                price = self.exchange.get_ticker_price(symbol)
                self._close_trade(trade, price, "EMERGENCY_CLOSE")
            except Exception as e:
                logger.critical(f"Failed emergency close for {trade.trade_id}: {e}")
