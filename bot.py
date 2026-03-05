"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Core Orchestrator                               ║
║  Main event loop: scan → analyze → execute → monitor → repeat       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import time
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import HydraConfig
from exchange import ExchangeClient
from database import HydraDatabase, TradeState
from strategy import MomentumCascadeStrategy
from risk_manager import RiskManager, CircuitBreakerTripped
from trade_manager import TradeManager
from reconciler import StateReconciler

logger = logging.getLogger("hydra.bot")


class HydraBot:
    """
    Main trading bot orchestrator.
    
    Architecture:
    ─────────────
    Two concurrent loops running in threads:
    
    1. SCANNER LOOP (every 15s):
       - Fetches candle data
       - Runs macro + entry analysis
       - If signal found → passes to TradeManager
    
    2. MONITOR LOOP (every 10s):
       - Iterates over all open trades
       - Checks SL/TP conditions
       - Updates trailing stops
       - Handles partial closes
    
    Both loops share the same database and exchange client
    (with thread-safe SQLite and session pooling).
    """

    def __init__(self, config: HydraConfig):
        self.config = config
        self._running = False
        self._shutdown_event = threading.Event()

        # Kill switch state
        self._kill_switch_file = Path(__file__).parent / "kill_switch.json"
        self._kill_switch_already_fired = False

        # Core components
        self.exchange = ExchangeClient(config.exchange)
        self.db = HydraDatabase(config.database.DB_PATH)
        self.risk = RiskManager(config, self.exchange, self.db)
        self.trade_mgr = TradeManager(config, self.exchange, self.db, self.risk)
        self.strategy = MomentumCascadeStrategy(config, self.exchange, self.db)
        self.reconciler = StateReconciler(config, self.exchange, self.db)

        # Threads
        self._scanner_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._backup_thread: Optional[threading.Thread] = None

    def start(self):
        """
        Start the bot. This is the main entry point.
        
        Sequence:
        1. Validate configuration
        2. Test exchange connectivity
        3. Reconcile state from any previous run
        4. Set daily equity baseline
        5. Launch scanner and monitor loops
        """
        logger.info("╔══════════════════════════════════════════════════════╗")
        logger.info("║       HYDRA MARGIN BOT — Starting Up                ║")
        logger.info("╚══════════════════════════════════════════════════════╝")

        # ── Step 1: Validate Config ──
        errors = self.config.validate()
        if errors:
            for err in errors:
                logger.critical(f"Config error: {err}")
            raise RuntimeError("Configuration validation failed")
        logger.info("Configuration validated ✓")

        # ── Step 2: Test Connectivity ──
        if not self._test_connectivity():
            raise RuntimeError("Cannot connect to Binance API")
        logger.info("Binance API connected ✓")

        # ── Step 3: Show Account Info ──
        self._log_account_info()

        # ── Step 4: Reconcile State ──
        recon_summary = self.reconciler.reconcile()
        if recon_summary["errors"]:
            logger.warning(f"Reconciliation had errors: {recon_summary['errors']}")
        logger.info("State reconciliation complete ✓")

        # ── Step 5: Set Daily Equity ──
        self.risk.set_daily_start_equity()
        logger.info("Daily equity baseline set ✓")

        # ── Step 6: Launch Loops ──
        self._running = True
        self._shutdown_event.clear()

        self._scanner_thread = threading.Thread(
            target=self._scanner_loop, name="Scanner", daemon=True
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, name="Monitor", daemon=True
        )
        self._backup_thread = threading.Thread(
            target=self._backup_loop, name="Backup", daemon=True
        )

        self._scanner_thread.start()
        self._monitor_thread.start()
        self._backup_thread.start()

        logger.info("All loops started ✓")
        logger.info(
            f"Trading: {self.config.pair.SYMBOL} | "
            f"Macro: {self.config.timeframe.MACRO_TIMEFRAME} | "
            f"Entry: {self.config.timeframe.ENTRY_TIMEFRAME} | "
            f"Leverage: {self.config.risk.LEVERAGE}x"
        )

    def stop(self):
        """Gracefully shut down the bot."""
        logger.info("Shutting down HYDRA bot...")
        self._running = False
        self._shutdown_event.set()

        # Wait for threads
        for t in [self._scanner_thread, self._monitor_thread, self._backup_thread]:
            if t and t.is_alive():
                t.join(timeout=30)

        # Final backup
        try:
            self.db.backup(suffix="_shutdown")
        except Exception as e:
            logger.error(f"Shutdown backup failed: {e}")

        self.db.close()
        logger.info("HYDRA bot stopped cleanly.")

    def wait(self):
        """Block until shutdown signal."""
        try:
            while self._running and not self._shutdown_event.is_set():
                self._shutdown_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            self.stop()

    # ─── Scanner Loop ────────────────────────────────────────────────

    def _scanner_loop(self):
        """
        Main scanning loop. Checks for entry signals periodically.
        """
        interval = self.config.timeframe.SCAN_INTERVAL_SECONDS
        logger.info(f"Scanner loop started (interval: {interval}s)")

        while self._running:
            try:
                # ── Kill switch check ──
                if self._read_kill_switch():
                    logger.warning("⚠️ Kill switch active — scanner paused, no new trades")
                    self._sleep(10)
                    continue

                # Daily equity reset check
                self.risk.set_daily_start_equity()

                # Circuit breaker check
                if self.risk.is_circuit_breaker_active():
                    logger.warning("Circuit breaker active — scanner paused")
                    self._sleep(60)
                    continue

                # Check for entry signal
                signal = self.strategy.check_entry()

                if signal and signal.valid:
                    logger.info(f"SIGNAL DETECTED: {signal.side} — executing trade...")
                    try:
                        trade = self.trade_mgr.open_trade(
                            signal=signal,
                            macro_bias=self.strategy.current_macro.bias,
                        )
                        if trade:
                            logger.info(f"Trade opened successfully: {trade.trade_id}")
                        else:
                            logger.warning("Trade execution returned None")
                    except CircuitBreakerTripped as cb:
                        logger.warning(f"Circuit breaker: {cb}")

            except Exception as e:
                logger.error(f"Scanner loop error: {e}", exc_info=True)

            self._sleep(interval)

        logger.info("Scanner loop stopped")

    # ─── Monitor Loop ────────────────────────────────────────────────

    def _monitor_loop(self):
        """
        Trade monitoring loop. Manages open positions.
        """
        interval = self.config.timeframe.TRADE_MONITOR_INTERVAL
        logger.info(f"Monitor loop started (interval: {interval}s)")

        while self._running:
            try:
                # ── Kill switch check ──
                if self._read_kill_switch():
                    if not self._kill_switch_already_fired:
                        logger.critical("⚠️ Kill switch activated — liquidating all positions!")
                        self.trade_mgr.kill_switch_liquidate()
                        self._kill_switch_already_fired = True
                    self._sleep(10)
                    continue
                else:
                    # Reset flag when kill switch is deactivated
                    self._kill_switch_already_fired = False

                open_trades = self.db.get_open_trades(self.config.pair.SYMBOL)

                for trade in open_trades:
                    if not self._running:
                        break
                    try:
                        updated = self.trade_mgr.monitor_trade(trade)
                        if updated.state != trade.state:
                            logger.info(
                                f"Trade {trade.trade_id}: {trade.state} → {updated.state}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Monitor error for {trade.trade_id}: {e}", exc_info=True
                        )

                # Check circuit breaker with current equity
                if open_trades and self.config.risk.EMERGENCY_CLOSE_ALL:
                    try:
                        if self.risk.is_circuit_breaker_active():
                            logger.critical("CIRCUIT BREAKER — closing all positions!")
                            self.trade_mgr.emergency_close_all(self.config.pair.SYMBOL)
                    except CircuitBreakerTripped:
                        self.trade_mgr.emergency_close_all(self.config.pair.SYMBOL)

            except Exception as e:
                logger.error(f"Monitor loop error: {e}", exc_info=True)

            self._sleep(interval)

        logger.info("Monitor loop stopped")

    # ─── Backup Loop ─────────────────────────────────────────────────

    def _backup_loop(self):
        """Periodic database backup."""
        interval = self.config.database.BACKUP_INTERVAL_MINUTES * 60
        logger.info(f"Backup loop started (interval: {interval/60:.0f} min)")

        while self._running:
            self._sleep(interval)
            if not self._running:
                break
            try:
                self.db.backup()
            except Exception as e:
                logger.error(f"Backup failed: {e}")

        logger.info("Backup loop stopped")

    # ─── Helpers ─────────────────────────────────────────────────────

    def _test_connectivity(self) -> bool:
        """Test Binance API connectivity with retries."""
        for attempt in range(3):
            try:
                if self.exchange.ping():
                    server_time = self.exchange.get_server_time()
                    local_time = int(time.time() * 1000)
                    drift = abs(server_time - local_time)
                    logger.info(f"Server time drift: {drift}ms")
                    if drift > 5000:
                        logger.warning(
                            f"High time drift ({drift}ms) — consider NTP sync"
                        )
                    return True
            except Exception as e:
                logger.warning(f"Connectivity test {attempt+1}/3 failed: {e}")
                time.sleep(2)
        return False

    def _log_account_info(self):
        """Log margin account information."""
        try:
            equity_info = self.exchange.get_margin_equity()
            usdc_balance = self.exchange.get_margin_asset_balance("USDC")
            eth_balance = self.exchange.get_margin_asset_balance("ETH")

            logger.info("═══ ACCOUNT INFO ═══")
            logger.info(f"  Margin Level: {equity_info['margin_level']:.2f}")
            logger.info(f"  Trade Enabled: {equity_info['trade_enabled']}")
            logger.info(f"  Borrow Enabled: {equity_info['borrow_enabled']}")
            logger.info(
                f"  USDC: free={usdc_balance['free']:.2f} "
                f"locked={usdc_balance['locked']:.2f} "
                f"net={usdc_balance['net']:.2f}"
            )
            logger.info(
                f"  ETH: free={eth_balance['free']:.4f} "
                f"borrowed={eth_balance['borrowed']:.4f} "
                f"net={eth_balance['net']:.4f}"
            )
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")

    def _read_kill_switch(self) -> bool:
        """Read kill switch state from shared JSON file."""
        try:
            if self._kill_switch_file.exists():
                data = json.loads(self._kill_switch_file.read_text(encoding="utf-8"))
                return data.get("active", False)
        except Exception:
            pass
        return False

    def _sleep(self, seconds: float):
        """Interruptible sleep."""
        self._shutdown_event.wait(timeout=seconds)
