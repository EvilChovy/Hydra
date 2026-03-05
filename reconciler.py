"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - State Reconciler                                ║
║  Startup recovery: syncs local state with Binance exchange state    ║
╚══════════════════════════════════════════════════════════════════════╝

When the bot restarts (crash, server reboot, deployment), this module:
1. Reads all "open" trades from SQLite
2. Queries Binance for actual positions and open orders
3. Reconciles differences and restores consistent state
4. Ensures no orphaned positions or phantom trades exist
"""

import time
import logging
from typing import Optional

from config import HydraConfig
from exchange import ExchangeClient, BinanceAPIError
from database import HydraDatabase, TradeRecord, TradeState, TradeSide

logger = logging.getLogger("hydra.reconciler")


class StateReconciler:
    """
    Startup state reconciliation engine.
    
    Handles scenarios:
    ──────────────────
    A) Clean restart (no open trades): Normal startup.
    B) Trade in DB but no position on exchange: Mark as closed.
    C) Position on exchange but no trade in DB: Create recovery record.
    D) Trade and position match: Restore SL/TP monitoring.
    E) SL order filled while offline: Detect and update state.
    """

    def __init__(self, config: HydraConfig, exchange: ExchangeClient, db: HydraDatabase):
        self.config = config
        self.exchange = exchange
        self.db = db

    def reconcile(self) -> dict:
        """
        Main reconciliation routine. Called once at startup.
        
        Returns summary of actions taken.
        """
        logger.info("=" * 60)
        logger.info("STARTING STATE RECONCILIATION")
        logger.info("=" * 60)

        summary = {
            "db_open_trades": 0,
            "exchange_positions": {},
            "exchange_open_orders": 0,
            "trades_confirmed": 0,
            "trades_closed_stale": 0,
            "trades_recovered": 0,
            "orders_cancelled_orphan": 0,
            "errors": [],
        }

        try:
            # ── Step 1: Get DB state ──
            db_open = self.db.get_open_trades(self.config.pair.SYMBOL)
            summary["db_open_trades"] = len(db_open)
            logger.info(f"DB open trades: {len(db_open)}")

            # ── Step 2: Get exchange state ──
            exchange_positions = self._get_exchange_positions()
            summary["exchange_positions"] = exchange_positions
            logger.info(f"Exchange positions: {exchange_positions}")

            exchange_orders = self._get_open_orders()
            summary["exchange_open_orders"] = len(exchange_orders)
            logger.info(f"Open orders on exchange: {len(exchange_orders)}")

            # ── Step 3: Reconcile each DB trade ──
            for trade in db_open:
                try:
                    self._reconcile_trade(trade, exchange_positions, exchange_orders, summary)
                except Exception as e:
                    error_msg = f"Failed to reconcile {trade.trade_id}: {e}"
                    logger.error(error_msg, exc_info=True)
                    summary["errors"].append(error_msg)

            # ── Step 4: Check for orphaned positions (on exchange but not in DB) ──
            self._check_orphaned_positions(exchange_positions, db_open, summary)

            # ── Step 5: Clean up orphaned orders ──
            self._cleanup_orphaned_orders(exchange_orders, db_open, summary)

        except Exception as e:
            error_msg = f"Reconciliation failed: {e}"
            logger.critical(error_msg, exc_info=True)
            summary["errors"].append(error_msg)

        logger.info("=" * 60)
        logger.info(f"RECONCILIATION COMPLETE: {summary}")
        logger.info("=" * 60)
        return summary

    def _get_exchange_positions(self) -> dict:
        """
        Get current margin positions.
        Returns dict: {asset: {"free": float, "locked": float, "borrowed": float, "net": float}}
        """
        try:
            base = self.config.pair.BASE_ASSET
            balance = self.exchange.get_margin_asset_balance(base)
            return {base: balance}
        except Exception as e:
            logger.error(f"Failed to get exchange positions: {e}")
            return {}

    def _get_open_orders(self) -> list[dict]:
        """Get all open margin orders for the trading pair."""
        try:
            return self.exchange.get_open_margin_orders(self.config.pair.SYMBOL)
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []

    def _reconcile_trade(
        self, trade: TradeRecord, positions: dict, orders: list, summary: dict
    ):
        """Reconcile a single trade against exchange state."""
        base = self.config.pair.BASE_ASSET
        pos = positions.get(base, {})
        net_position = pos.get("net", 0.0)
        borrowed = pos.get("borrowed", 0.0)

        logger.info(
            f"Reconciling: {trade.trade_id} state={trade.state} "
            f"remaining_qty={trade.remaining_qty:.4f} "
            f"exchange_net={net_position:.4f} borrowed={borrowed:.4f}"
        )

        # Check if the position still exists on exchange
        has_position = False
        if trade.side == TradeSide.LONG.value:
            # For longs, we should have positive net ETH
            has_position = net_position > self.config.pair.MIN_QTY
        else:
            # For shorts, we should have borrowed ETH
            has_position = borrowed > self.config.pair.MIN_QTY

        if has_position:
            # Position exists — check if SL order is still active
            sl_active = self._is_order_active(trade.sl_order_id, orders)

            if not sl_active and trade.sl_order_id:
                # SL might have been filled while offline
                filled = self._check_sl_filled(trade)
                if filled:
                    logger.info(f"SL was filled while offline for {trade.trade_id}")
                    trade.state = TradeState.CLOSED.value
                    trade.closed_at = time.time()
                    self.db.save_trade(trade)
                    summary["trades_closed_stale"] += 1
                    return
                else:
                    # SL was cancelled/expired — re-place it
                    logger.warning(f"Re-placing SL for {trade.trade_id}")
                    trade.sl_order_id = None

            # Confirm trade is correctly tracked
            summary["trades_confirmed"] += 1
            logger.info(f"Trade confirmed: {trade.trade_id} (state={trade.state})")

            # Update remaining qty from exchange
            if trade.side == TradeSide.LONG.value:
                actual_qty = net_position
            else:
                actual_qty = borrowed

            # Sync quantity if there's a meaningful difference
            if abs(actual_qty - trade.remaining_qty) > self.config.pair.MIN_QTY:
                logger.warning(
                    f"Quantity mismatch for {trade.trade_id}: "
                    f"DB={trade.remaining_qty:.4f} Exchange={actual_qty:.4f}"
                )
                trade.remaining_qty = round(actual_qty, self.config.pair.QTY_PRECISION)
                self.db.save_trade(trade)

        else:
            # No position on exchange — trade was closed/stopped
            logger.info(f"No position found for {trade.trade_id} — marking as closed")
            trade.state = TradeState.CLOSED.value
            trade.closed_at = time.time()
            self.db.save_trade(trade)
            summary["trades_closed_stale"] += 1

    def _check_orphaned_positions(
        self, positions: dict, db_trades: list[TradeRecord], summary: dict
    ):
        """Check for positions on exchange not tracked in database."""
        base = self.config.pair.BASE_ASSET
        pos = positions.get(base, {})
        net = pos.get("net", 0.0)
        borrowed = pos.get("borrowed", 0.0)

        has_long = net > self.config.pair.MIN_QTY * 10  # Significant position
        has_short = borrowed > self.config.pair.MIN_QTY * 10
        db_has_active = len(db_trades) > 0

        if (has_long or has_short) and not db_has_active:
            logger.critical(
                f"ORPHANED POSITION DETECTED: net={net:.4f} borrowed={borrowed:.4f} "
                f"with no active trades in DB!"
            )
            # Create a recovery trade record
            trade = TradeRecord(
                trade_id=f"RECOVERY_{self.config.pair.SYMBOL}_{int(time.time())}",
                symbol=self.config.pair.SYMBOL,
                side=TradeSide.LONG.value if has_long else TradeSide.SHORT.value,
                state=TradeState.OPEN.value,
                entry_price=0,  # Unknown — will need manual review
                entry_qty=abs(net) if has_long else abs(borrowed),
                remaining_qty=abs(net) if has_long else abs(borrowed),
                sl_price=0,  # Will need to be set manually
                opened_at=time.time(),
                entry_reason="RECOVERED — orphaned position from restart",
            )
            self.db.save_trade(trade)
            summary["trades_recovered"] += 1
            logger.critical(f"Recovery trade created: {trade.trade_id}")

    def _cleanup_orphaned_orders(
        self, orders: list[dict], db_trades: list[TradeRecord], summary: dict
    ):
        """Cancel orders that don't belong to any tracked trade."""
        tracked_order_ids = set()
        for trade in db_trades:
            if trade.sl_order_id:
                tracked_order_ids.add(trade.sl_order_id)
            if trade.tp1_order_id:
                tracked_order_ids.add(trade.tp1_order_id)

        for order in orders:
            client_id = order.get("clientOrderId", "")
            order_id = order.get("orderId")

            if client_id not in tracked_order_ids and not client_id.startswith("SL_RECOVERY"):
                logger.warning(f"Orphaned order found: {client_id} (id={order_id})")
                try:
                    self.exchange.cancel_margin_order(
                        symbol=self.config.pair.SYMBOL,
                        order_id=order_id,
                    )
                    summary["orders_cancelled_orphan"] += 1
                    logger.info(f"Cancelled orphaned order: {order_id}")
                except BinanceAPIError as e:
                    logger.error(f"Failed to cancel orphaned order: {e}")

    def _is_order_active(self, client_order_id: Optional[str], orders: list[dict]) -> bool:
        """Check if an order with given client ID is in the open orders list."""
        if not client_order_id:
            return False
        return any(o.get("clientOrderId") == client_order_id for o in orders)

    def _check_sl_filled(self, trade: TradeRecord) -> bool:
        """Check if the SL order was filled by querying order status."""
        if not trade.sl_order_id:
            return False
        try:
            order = self.exchange.get_margin_order(
                symbol=trade.symbol,
                client_order_id=trade.sl_order_id,
            )
            status = order.get("status", "")
            return status == "FILLED"
        except BinanceAPIError:
            return False
