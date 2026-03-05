"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Exchange Interface                              ║
║  Binance Cross Margin API with exponential backoff & auto-recovery  ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import time
import hmac
import hashlib
import logging
from urllib.parse import urlencode
from typing import Optional
from decimal import Decimal, ROUND_DOWN

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import ExchangeConfig, TradingPairConfig

logger = logging.getLogger("hydra.exchange")


class BinanceAPIError(Exception):
    """Custom exception for Binance API errors."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Binance API Error [{code}]: {message}")


class ExchangeClient:
    """
    Production-grade Binance Cross Margin client.
    
    Features:
    - Automatic request signing (HMAC-SHA256)
    - Exponential backoff with jitter on failures
    - Session pooling with connection reuse
    - Rate limit awareness
    - Atomic order placement and cancellation
    """

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._session = self._build_session()
        self._last_request_time = 0.0
        self._min_request_interval = 0.05  # 50ms between requests

    def _build_session(self) -> requests.Session:
        """Build a resilient HTTP session with retry logic."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST", "DELETE"],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
        )
        session.mount("https://", adapter)
        session.headers.update({
            "X-MBX-APIKEY": self.config.API_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        })
        return session

    def _sign(self, params: dict) -> dict:
        """Add timestamp and HMAC signature to request params."""
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.config.RECV_WINDOW
        query = urlencode(params)
        signature = hmac.new(
            self.config.API_SECRET.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _throttle(self):
        """Enforce minimum interval between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        signed: bool = True,
        max_retries: Optional[int] = None,
    ) -> dict:
        """
        Execute API request with exponential backoff.
        
        Handles:
        - Network timeouts → retry with backoff
        - Rate limits (429) → wait and retry
        - Server errors (5xx) → retry with backoff
        - Client errors (4xx) → raise immediately
        """
        if params is None:
            params = {}
        if signed:
            params = self._sign(params)

        retries = max_retries or self.config.MAX_RETRIES
        url = f"{self.config.BASE_URL}{endpoint}"

        for attempt in range(retries + 1):
            try:
                self._throttle()

                if method == "GET":
                    resp = self._session.get(
                        url, params=params, timeout=self.config.REQUEST_TIMEOUT
                    )
                elif method == "POST":
                    resp = self._session.post(
                        url, data=params, timeout=self.config.REQUEST_TIMEOUT
                    )
                elif method == "DELETE":
                    resp = self._session.delete(
                        url, params=params, timeout=self.config.REQUEST_TIMEOUT
                    )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Rate limited
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 30))
                    logger.warning(f"Rate limited. Waiting {wait}s...")
                    time.sleep(wait)
                    params = self._sign({k: v for k, v in params.items()
                                        if k not in ("timestamp", "recvWindow", "signature")})
                    continue

                # Server error
                if resp.status_code >= 500:
                    raise requests.exceptions.ConnectionError(
                        f"Server error: {resp.status_code}"
                    )

                data = resp.json()

                # Binance error response
                if "code" in data and data["code"] < 0:
                    raise BinanceAPIError(data["code"], data.get("msg", "Unknown"))

                return data

            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ReadTimeout,
            ) as e:
                if attempt < retries:
                    wait = self.config.RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        f"Request failed (attempt {attempt+1}/{retries+1}): {e}. "
                        f"Retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    # Re-sign with fresh timestamp
                    if signed:
                        params = self._sign({
                            k: v for k, v in params.items()
                            if k not in ("timestamp", "recvWindow", "signature")
                        })
                else:
                    logger.error(f"Request failed after {retries+1} attempts: {e}")
                    raise

        raise RuntimeError(f"Request failed after {retries+1} attempts")

    # ─── Market Data (Public) ────────────────────────────────────────

    def get_klines(
        self, symbol: str, interval: str, limit: int = 500
    ) -> list[list]:
        """
        Fetch candlestick data.
        
        Returns list of:
        [open_time, open, high, low, close, volume, close_time, 
         quote_volume, trades, taker_buy_base, taker_buy_quote, ignore]
        """
        return self._request(
            "GET",
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
            signed=False,
        )

    def get_ticker_price(self, symbol: str) -> float:
        """Get current price."""
        data = self._request(
            "GET", "/api/v3/ticker/price", {"symbol": symbol}, signed=False
        )
        return float(data["price"])

    def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        """Get order book depth."""
        return self._request(
            "GET", "/api/v3/depth", {"symbol": symbol, "limit": limit}, signed=False
        )

    def get_exchange_info(self, symbol: str) -> dict:
        """Get trading pair rules and filters."""
        data = self._request(
            "GET", "/api/v3/exchangeInfo", {"symbol": symbol}, signed=False
        )
        for s in data.get("symbols", []):
            if s["symbol"] == symbol:
                return s
        raise ValueError(f"Symbol {symbol} not found in exchange info")

    # ─── Cross Margin Account ────────────────────────────────────────

    def get_margin_account(self) -> dict:
        """Get cross margin account details including balances and positions."""
        return self._request("GET", "/sapi/v1/margin/account")

    def get_margin_asset_balance(self, asset: str) -> dict:
        """Get balance for a specific asset in margin account."""
        account = self.get_margin_account()
        for a in account.get("userAssets", []):
            if a["asset"] == asset:
                return {
                    "free": float(a["free"]),
                    "locked": float(a["locked"]),
                    "borrowed": float(a["borrowed"]),
                    "interest": float(a["interest"]),
                    "net": float(a["netAsset"]),
                }
        return {"free": 0.0, "locked": 0.0, "borrowed": 0.0, "interest": 0.0, "net": 0.0}

    def get_margin_equity(self) -> dict:
        """Get total margin equity and margin level."""
        account = self.get_margin_account()
        return {
            "total_asset_btc": float(account.get("totalAssetOfBtc", 0)),
            "total_liability_btc": float(account.get("totalLiabilityOfBtc", 0)),
            "total_net_btc": float(account.get("totalNetAssetOfBtc", 0)),
            "margin_level": float(account.get("marginLevel", 0)),
            "trade_enabled": account.get("tradeEnabled", False),
            "borrow_enabled": account.get("borrowEnabled", False),
        }

    def transfer_to_margin(self, asset: str, amount: float) -> dict:
        """Transfer from spot to cross margin."""
        return self._request(
            "POST",
            "/sapi/v1/margin/transfer",
            {"asset": asset, "amount": f"{amount:.8f}", "type": 1},  # 1 = spot to margin
        )

    # ─── Cross Margin Orders ─────────────────────────────────────────

    def place_margin_order(
        self,
        symbol: str,
        side: str,          # BUY or SELL
        order_type: str,     # MARKET, LIMIT, STOP_LOSS_LIMIT, etc.
        quantity: Optional[float] = None,
        quote_qty: Optional[float] = None,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: Optional[str] = None,
        side_effect: str = "MARGIN_BUY",  # MARGIN_BUY = auto-borrow
        new_client_order_id: Optional[str] = None,
        price_precision: int = 2,
        qty_precision: int = 4,
    ) -> dict:
        """
        Place a cross margin order.
        
        sideEffectType:
        - MARGIN_BUY: Auto-borrow if needed (for opening positions)
        - AUTO_REPAY: Auto-repay when closing positions
        - NO_SIDE_EFFECT: Normal order, no borrow/repay
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "sideEffectType": side_effect,
            "isIsolated": "FALSE",  # Cross margin
        }

        if quantity is not None:
            params["quantity"] = str(
                Decimal(str(quantity)).quantize(
                    Decimal(10) ** -qty_precision, rounding=ROUND_DOWN
                )
            )

        if quote_qty is not None:
            params["quoteOrderQty"] = f"{quote_qty:.{price_precision}f}"

        if price is not None:
            params["price"] = f"{price:.{price_precision}f}"

        if stop_price is not None:
            params["stopPrice"] = f"{stop_price:.{price_precision}f}"

        if time_in_force:
            params["timeInForce"] = time_in_force

        if new_client_order_id:
            params["newClientOrderId"] = new_client_order_id

        logger.info(
            f"Placing margin order: {side} {order_type} {symbol} "
            f"qty={quantity} price={price} stop={stop_price} effect={side_effect}"
        )
        return self._request("POST", "/sapi/v1/margin/order", params)

    def cancel_margin_order(
        self, symbol: str, order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Cancel an open margin order."""
        params = {"symbol": symbol, "isIsolated": "FALSE"}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id

        logger.info(f"Cancelling margin order: {symbol} id={order_id or client_order_id}")
        return self._request("DELETE", "/sapi/v1/margin/order", params)

    def get_margin_order(
        self, symbol: str, order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Query a specific margin order."""
        params = {"symbol": symbol, "isIsolated": "FALSE"}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._request("GET", "/sapi/v1/margin/order", params)

    def get_open_margin_orders(self, symbol: str) -> list[dict]:
        """Get all open margin orders for a symbol."""
        return self._request(
            "GET",
            "/sapi/v1/margin/openOrders",
            {"symbol": symbol, "isIsolated": "FALSE"},
        )

    def get_margin_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Get recent margin trades."""
        return self._request(
            "GET",
            "/sapi/v1/margin/myTrades",
            {"symbol": symbol, "isIsolated": "FALSE", "limit": limit},
        )

    # ─── Convenience Methods ─────────────────────────────────────────

    def market_buy_margin(
        self, symbol: str, quantity: float, qty_precision: int = 4,
        auto_borrow: bool = True,
    ) -> dict:
        """Place a market buy order on cross margin."""
        return self.place_margin_order(
            symbol=symbol,
            side="BUY",
            order_type="MARKET",
            quantity=quantity,
            side_effect="MARGIN_BUY" if auto_borrow else "NO_SIDE_EFFECT",
            qty_precision=qty_precision,
        )

    def market_sell_margin(
        self, symbol: str, quantity: float, qty_precision: int = 4,
        auto_repay: bool = True,
    ) -> dict:
        """Place a market sell order on cross margin."""
        return self.place_margin_order(
            symbol=symbol,
            side="SELL",
            order_type="MARKET",
            quantity=quantity,
            side_effect="AUTO_REPAY" if auto_repay else "NO_SIDE_EFFECT",
            qty_precision=qty_precision,
        )

    def place_stop_loss_order(
        self, symbol: str, side: str, quantity: float,
        stop_price: float, limit_price: float,
        price_precision: int = 2, qty_precision: int = 4,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Place a stop-loss limit order."""
        return self.place_margin_order(
            symbol=symbol,
            side=side,
            order_type="STOP_LOSS_LIMIT",
            quantity=quantity,
            price=limit_price,
            stop_price=stop_price,
            time_in_force="GTC",
            side_effect="AUTO_REPAY",
            new_client_order_id=client_order_id,
            price_precision=price_precision,
            qty_precision=qty_precision,
        )

    def get_usdc_equity(self) -> float:
        """Get total USDC equity (net asset value) in margin account."""
        balance = self.get_margin_asset_balance("USDC")
        return balance["net"]

    def ping(self) -> bool:
        """Check API connectivity."""
        try:
            self._request("GET", "/api/v3/ping", signed=False, max_retries=1)
            return True
        except Exception:
            return False

    def get_server_time(self) -> int:
        """Get Binance server time in milliseconds."""
        data = self._request("GET", "/api/v3/time", signed=False, max_retries=1)
        return data["serverTime"]
