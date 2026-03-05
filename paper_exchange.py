"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Paper Trading (Simulación)                      ║
║  Ejecuta toda la lógica REAL pero sin enviar órdenes a Binance      ║
║  Usa datos de mercado REALES + órdenes SIMULADAS                    ║
╚══════════════════════════════════════════════════════════════════════╝

Esto te permite:
- Verificar que las señales se generan correctamente
- Ver cuánto habría ganado/perdido cada trade
- Probar la conexión a la API sin riesgo
- Validar que los SL/TP/Trailing funcionan bien
"""

import time
import logging
import random
from typing import Optional
from decimal import Decimal, ROUND_DOWN

from exchange import ExchangeClient, BinanceAPIError
from config import ExchangeConfig

logger = logging.getLogger("hydra.paper")


class PaperExchangeClient(ExchangeClient):
    """
    Drop-in replacement for ExchangeClient that simulates orders.
    
    - Market data: REAL (from Binance API)
    - Orders: SIMULATED (local tracking)
    - Balances: SIMULATED (starting from configured amount)
    """

    def __init__(self, config: ExchangeConfig, starting_balance_usdc: float = 1000.0):
        super().__init__(config)
        self._starting_balance = starting_balance_usdc

        # Simulated balances
        self._balances = {
            "USDC": {
                "free": starting_balance_usdc,
                "locked": 0.0,
                "borrowed": 0.0,
                "interest": 0.0,
                "net": starting_balance_usdc,
            },
            "ETH": {
                "free": 0.0,
                "locked": 0.0,
                "borrowed": 0.0,
                "interest": 0.0,
                "net": 0.0,
            },
        }

        # Simulated orders
        self._orders: dict[str, dict] = {}
        self._order_counter = 100000
        self._trades_log: list[dict] = []

        logger.info(
            f"╔══════════════════════════════════════════╗\n"
            f"║   PAPER TRADING MODE ACTIVE              ║\n"
            f"║   Starting balance: ${starting_balance_usdc:,.2f} USDC       ║\n"
            f"║   NO real orders will be placed           ║\n"
            f"╚══════════════════════════════════════════╝"
        )

    # ─── Override: Balances (Simulated) ──────────────────────────────

    def get_margin_account(self) -> dict:
        """Return simulated margin account."""
        total_net_usdc = self._balances["USDC"]["net"]
        # Add ETH value
        try:
            eth_price = self.get_ticker_price("ETHUSDC")
            total_net_usdc += self._balances["ETH"]["net"] * eth_price
        except Exception:
            pass

        return {
            "totalAssetOfBtc": "0",
            "totalLiabilityOfBtc": "0",
            "totalNetAssetOfBtc": "0",
            "marginLevel": "999.0",
            "tradeEnabled": True,
            "borrowEnabled": True,
            "userAssets": [
                {
                    "asset": asset,
                    "free": str(bal["free"]),
                    "locked": str(bal["locked"]),
                    "borrowed": str(bal["borrowed"]),
                    "interest": str(bal["interest"]),
                    "netAsset": str(bal["net"]),
                }
                for asset, bal in self._balances.items()
            ],
        }

    def get_margin_asset_balance(self, asset: str) -> dict:
        """Return simulated balance for an asset."""
        if asset in self._balances:
            return self._balances[asset].copy()
        return {"free": 0.0, "locked": 0.0, "borrowed": 0.0, "interest": 0.0, "net": 0.0}

    def get_margin_equity(self) -> dict:
        """Return simulated equity info."""
        return {
            "total_asset_btc": 0,
            "total_liability_btc": 0,
            "total_net_btc": 0,
            "margin_level": 999.0,
            "trade_enabled": True,
            "borrow_enabled": True,
        }

    def get_usdc_equity(self) -> float:
        """Get total simulated USDC equity."""
        total = self._balances["USDC"]["net"]
        try:
            eth_price = self.get_ticker_price("ETHUSDC")
            total += self._balances["ETH"]["net"] * eth_price
        except Exception:
            pass
        return round(total, 2)

    # ─── Override: Orders (Simulated) ────────────────────────────────

    def place_margin_order(
        self, symbol, side, order_type, quantity=None, quote_qty=None,
        price=None, stop_price=None, time_in_force=None,
        side_effect="MARGIN_BUY", new_client_order_id=None,
        price_precision=2, qty_precision=4,
    ) -> dict:
        """Simulate order placement."""
        self._order_counter += 1
        order_id = self._order_counter
        client_id = new_client_order_id or f"PAPER_{order_id}"

        # Get current price for market orders
        if order_type == "MARKET":
            current_price = self.get_ticker_price(symbol)
            # Add small simulated slippage (0.01-0.03%)
            slippage = current_price * random.uniform(0.0001, 0.0003)
            if side == "BUY":
                fill_price = current_price + slippage
            else:
                fill_price = current_price - slippage
            fill_price = round(fill_price, price_precision)

            # Update simulated balances
            if quantity:
                qty = float(Decimal(str(quantity)).quantize(
                    Decimal(10) ** -qty_precision, rounding=ROUND_DOWN
                ))
                cost = qty * fill_price
                fee = cost * 0.001  # 0.1% taker fee

                if side == "BUY":
                    self._balances["USDC"]["free"] -= (cost + fee)
                    self._balances["USDC"]["net"] -= (cost + fee)
                    self._balances["ETH"]["free"] += qty
                    self._balances["ETH"]["net"] += qty
                else:
                    self._balances["USDC"]["free"] += (cost - fee)
                    self._balances["USDC"]["net"] += (cost - fee)
                    self._balances["ETH"]["free"] -= qty
                    self._balances["ETH"]["net"] -= qty

                self._trades_log.append({
                    "time": time.time(),
                    "side": side,
                    "qty": qty,
                    "price": fill_price,
                    "cost": cost,
                    "fee": fee,
                })

            order = {
                "symbol": symbol,
                "orderId": order_id,
                "clientOrderId": client_id,
                "transactTime": int(time.time() * 1000),
                "price": str(fill_price),
                "origQty": str(quantity or 0),
                "executedQty": str(quantity or 0),
                "cummulativeQuoteQty": str(round((quantity or 0) * fill_price, 2)),
                "status": "FILLED",
                "type": order_type,
                "side": side,
                "fills": [
                    {
                        "price": str(fill_price),
                        "qty": str(quantity or 0),
                        "commission": str(round(fee if quantity else 0, 6)),
                        "commissionAsset": "USDC",
                    }
                ],
            }

            equity = self.get_usdc_equity()
            pnl = equity - self._starting_balance
            pnl_pct = (pnl / self._starting_balance) * 100

            logger.info(
                f"📝 PAPER ORDER: {side} {quantity:.4f} ETH @ ${fill_price:.2f} "
                f"(${cost:.2f}) | Equity: ${equity:.2f} ({pnl_pct:+.2f}%)"
            )

        elif order_type == "STOP_LOSS_LIMIT":
            # Store the stop order for tracking (not auto-executed)
            order = {
                "symbol": symbol,
                "orderId": order_id,
                "clientOrderId": client_id,
                "price": str(price),
                "stopPrice": str(stop_price),
                "origQty": str(quantity or 0),
                "executedQty": "0",
                "status": "NEW",
                "type": order_type,
                "side": side,
                "fills": [],
            }
            self._orders[client_id] = order
            logger.info(
                f"📝 PAPER SL ORDER: {side} {quantity:.4f} ETH "
                f"stop=${stop_price:.2f} limit=${price:.2f}"
            )
        else:
            order = {
                "symbol": symbol,
                "orderId": order_id,
                "clientOrderId": client_id,
                "status": "NEW",
                "type": order_type,
                "side": side,
                "fills": [],
            }
            self._orders[client_id] = order

        return order

    def cancel_margin_order(self, symbol, order_id=None, client_order_id=None) -> dict:
        """Simulate order cancellation."""
        key = client_order_id or str(order_id)
        if key in self._orders:
            self._orders[key]["status"] = "CANCELED"
            del self._orders[key]
            logger.debug(f"📝 PAPER: Cancelled order {key}")
        return {"orderId": order_id, "clientOrderId": client_order_id, "status": "CANCELED"}

    def get_margin_order(self, symbol, order_id=None, client_order_id=None) -> dict:
        """Query a simulated order."""
        key = client_order_id or str(order_id)
        if key in self._orders:
            return self._orders[key]
        # Order not found = was already cancelled/filled
        return {"status": "CANCELED", "orderId": order_id, "clientOrderId": client_order_id}

    def get_open_margin_orders(self, symbol) -> list[dict]:
        """Get all simulated open orders."""
        return [o for o in self._orders.values()
                if o.get("symbol") == symbol and o.get("status") == "NEW"]

    def get_margin_trades(self, symbol, limit=50) -> list[dict]:
        """Get simulated trade history."""
        return self._trades_log[-limit:]

    # ─── Convenience (use parent class for real market data) ─────────

    def market_buy_margin(self, symbol, quantity, qty_precision=4, auto_borrow=True) -> dict:
        return self.place_margin_order(
            symbol=symbol, side="BUY", order_type="MARKET",
            quantity=quantity, side_effect="MARGIN_BUY", qty_precision=qty_precision,
        )

    def market_sell_margin(self, symbol, quantity, qty_precision=4, auto_repay=True) -> dict:
        return self.place_margin_order(
            symbol=symbol, side="SELL", order_type="MARKET",
            quantity=quantity, side_effect="AUTO_REPAY", qty_precision=qty_precision,
        )

    def place_stop_loss_order(
        self, symbol, side, quantity, stop_price, limit_price,
        price_precision=2, qty_precision=4, client_order_id=None,
    ) -> dict:
        return self.place_margin_order(
            symbol=symbol, side=side, order_type="STOP_LOSS_LIMIT",
            quantity=quantity, price=limit_price, stop_price=stop_price,
            time_in_force="GTC", side_effect="AUTO_REPAY",
            new_client_order_id=client_order_id,
            price_precision=price_precision, qty_precision=qty_precision,
        )

    def transfer_to_margin(self, asset, amount) -> dict:
        """Simulate transfer."""
        logger.info(f"📝 PAPER: Transfer {amount} {asset} to margin (simulated)")
        return {"tranId": 0}

    # ─── Paper-only Methods ──────────────────────────────────────────

    def get_paper_summary(self) -> dict:
        """Get summary of paper trading performance."""
        equity = self.get_usdc_equity()
        pnl = equity - self._starting_balance
        pnl_pct = (pnl / self._starting_balance) * 100 if self._starting_balance > 0 else 0
        return {
            "starting_balance": self._starting_balance,
            "current_equity": equity,
            "pnl_usdc": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "total_trades": len(self._trades_log),
            "total_fees": round(sum(t["fee"] for t in self._trades_log), 4),
            "balances": {k: v.copy() for k, v in self._balances.items()},
        }
