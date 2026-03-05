"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA MARGIN BOT - Technical Analysis Engine                       ║
║  Pure-NumPy indicator calculations (no TA-Lib dependency)           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import logging
import numpy as np
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("hydra.analysis")


@dataclass
class OHLCV:
    """Candlestick data container."""
    timestamps: np.ndarray   # Unix ms
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray

    @classmethod
    def from_klines(cls, klines: list[list]) -> "OHLCV":
        """Parse Binance klines into OHLCV arrays."""
        arr = np.array(klines, dtype=object)
        return cls(
            timestamps=arr[:, 0].astype(np.float64),
            opens=arr[:, 1].astype(np.float64),
            highs=arr[:, 2].astype(np.float64),
            lows=arr[:, 3].astype(np.float64),
            closes=arr[:, 4].astype(np.float64),
            volumes=arr[:, 5].astype(np.float64),
        )

    def __len__(self):
        return len(self.closes)

    @property
    def last_close(self) -> float:
        return float(self.closes[-1])

    @property
    def last_high(self) -> float:
        return float(self.highs[-1])

    @property
    def last_low(self) -> float:
        return float(self.lows[-1])

    @property
    def last_volume(self) -> float:
        return float(self.volumes[-1])


def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    result = np.full_like(data, np.nan)
    if len(data) < period:
        return result
    cumsum = np.cumsum(data)
    result[period - 1:] = (cumsum[period - 1:] - np.concatenate(([0], cumsum[:-period]))) / period
    return result


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index using Wilder's smoothing."""
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    result = np.full(len(closes), np.nan)
    if len(closes) < period + 1:
        return result

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


def macd(
    closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD Line, Signal Line, Histogram."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    # Prepend first candle's range
    tr = np.concatenate(([highs[0] - lows[0]], tr))

    result = np.full_like(tr, np.nan)
    if len(tr) < period:
        return result

    result[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def adx(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Average Directional Index.
    Returns: (ADX, +DI, -DI)
    """
    n = len(closes)
    result_adx = np.full(n, np.nan)
    result_pdi = np.full(n, np.nan)
    result_mdi = np.full(n, np.nan)

    if n < period * 2:
        return result_adx, result_pdi, result_mdi

    # True Range
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    tr = np.concatenate(([highs[0] - lows[0]], tr))

    # Directional Movement
    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = np.concatenate(([0.0], plus_dm))
    minus_dm = np.concatenate(([0.0], minus_dm))

    # Wilder's smoothing
    atr_smooth = np.full(n, np.nan)
    pdm_smooth = np.full(n, np.nan)
    mdm_smooth = np.full(n, np.nan)

    atr_smooth[period] = np.sum(tr[1 : period + 1])
    pdm_smooth[period] = np.sum(plus_dm[1 : period + 1])
    mdm_smooth[period] = np.sum(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        atr_smooth[i] = atr_smooth[i - 1] - (atr_smooth[i - 1] / period) + tr[i]
        pdm_smooth[i] = pdm_smooth[i - 1] - (pdm_smooth[i - 1] / period) + plus_dm[i]
        mdm_smooth[i] = mdm_smooth[i - 1] - (mdm_smooth[i - 1] / period) + minus_dm[i]

    # +DI, -DI
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * pdm_smooth / atr_smooth
        mdi = 100.0 * mdm_smooth / atr_smooth
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)

    pdi = np.nan_to_num(pdi, nan=0.0)
    mdi = np.nan_to_num(mdi, nan=0.0)
    dx = np.nan_to_num(dx, nan=0.0)

    # ADX (smoothed DX)
    adx_start = period * 2
    if adx_start < n:
        result_adx[adx_start] = np.nanmean(dx[period + 1 : adx_start + 1])
        for i in range(adx_start + 1, n):
            result_adx[i] = (result_adx[i - 1] * (period - 1) + dx[i]) / period

    result_pdi = pdi
    result_mdi = mdi
    return result_adx, result_pdi, result_mdi


def vwap(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    volumes: np.ndarray, period: Optional[int] = None,
) -> np.ndarray:
    """
    Volume Weighted Average Price.
    If period is None, uses entire series (session VWAP).
    """
    typical = (highs + lows + closes) / 3.0
    if period:
        result = np.full_like(closes, np.nan)
        for i in range(period - 1, len(closes)):
            start = i - period + 1
            vol_slice = volumes[start : i + 1]
            tp_slice = typical[start : i + 1]
            total_vol = np.sum(vol_slice)
            if total_vol > 0:
                result[i] = np.sum(tp_slice * vol_slice) / total_vol
        return result
    else:
        cum_vol = np.cumsum(volumes)
        cum_tp_vol = np.cumsum(typical * volumes)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)


def volume_ma(volumes: np.ndarray, period: int = 20) -> np.ndarray:
    """Volume Simple Moving Average."""
    return sma(volumes, period)


@dataclass
class MacroAnalysis:
    """Result of macro (4H) timeframe analysis."""
    bias: str              # "BULLISH", "BEARISH", "NEUTRAL"
    ema_fast: float        # Current EMA fast value
    ema_slow: float        # Current EMA slow value
    adx_value: float       # ADX strength
    plus_di: float
    minus_di: float
    trend_strength: str    # "STRONG", "MODERATE", "WEAK"
    confidence: float      # 0.0 to 1.0


@dataclass
class EntrySignal:
    """Result of entry (5m) timeframe analysis."""
    valid: bool
    side: str              # "LONG" or "SHORT"
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    atr_value: float
    rsi_value: float
    macd_histogram: float
    volume_ratio: float    # Current vol / avg vol
    vwap_value: float
    reason: str            # Human-readable signal description


class AnalysisEngine:
    """
    Multi-timeframe technical analysis engine.
    
    Computes all indicators and generates structured signals
    that the strategy module consumes.
    """

    def analyze_macro(
        self,
        ohlcv: OHLCV,
        ema_fast_period: int = 21,
        ema_slow_period: int = 55,
        adx_period: int = 14,
        adx_threshold: float = 20.0,
        adx_strong: float = 30.0,
    ) -> MacroAnalysis:
        """
        Analyze macro (4H) timeframe for directional bias.
        
        Rules:
        - BULLISH: EMA21 > EMA55 AND ADX > threshold AND +DI > -DI
        - BEARISH: EMA21 < EMA55 AND ADX > threshold AND -DI > +DI
        - NEUTRAL: Otherwise
        """
        ema_f = ema(ohlcv.closes, ema_fast_period)
        ema_s = ema(ohlcv.closes, ema_slow_period)
        adx_vals, pdi, mdi = adx(ohlcv.highs, ohlcv.lows, ohlcv.closes, adx_period)

        current_ema_f = float(ema_f[-1])
        current_ema_s = float(ema_s[-1])
        current_adx = float(adx_vals[-1]) if not np.isnan(adx_vals[-1]) else 0.0
        current_pdi = float(pdi[-1]) if not np.isnan(pdi[-1]) else 0.0
        current_mdi = float(mdi[-1]) if not np.isnan(mdi[-1]) else 0.0

        # Determine trend strength
        if current_adx >= adx_strong:
            strength = "STRONG"
        elif current_adx >= adx_threshold:
            strength = "MODERATE"
        else:
            strength = "WEAK"

        # Determine bias
        if (
            current_ema_f > current_ema_s
            and current_adx >= adx_threshold
            and current_pdi > current_mdi
        ):
            bias = "BULLISH"
            confidence = min(1.0, (current_adx - adx_threshold) / (adx_strong - adx_threshold))
        elif (
            current_ema_f < current_ema_s
            and current_adx >= adx_threshold
            and current_mdi > current_pdi
        ):
            bias = "BEARISH"
            confidence = min(1.0, (current_adx - adx_threshold) / (adx_strong - adx_threshold))
        else:
            bias = "NEUTRAL"
            confidence = 0.0

        result = MacroAnalysis(
            bias=bias,
            ema_fast=current_ema_f,
            ema_slow=current_ema_s,
            adx_value=current_adx,
            plus_di=current_pdi,
            minus_di=current_mdi,
            trend_strength=strength,
            confidence=confidence,
        )

        logger.info(
            f"MACRO: bias={result.bias} ADX={result.adx_value:.1f} "
            f"+DI={result.plus_di:.1f} -DI={result.minus_di:.1f} "
            f"strength={result.trend_strength} conf={result.confidence:.2f}"
        )
        return result

    def analyze_entry(
        self,
        ohlcv: OHLCV,
        macro_bias: str,
        rsi_period: int = 14,
        rsi_long_zone: tuple = (35, 55),
        rsi_short_zone: tuple = (45, 65),
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        atr_period: int = 14,
        atr_sl_mult: float = 1.8,
        atr_tp1_mult: float = 2.2,
        atr_tp2_mult: float = 4.5,
        vol_multiplier: float = 1.3,
        vol_ma_period: int = 20,
        use_vwap: bool = True,
    ) -> EntrySignal:
        """
        Analyze entry (5m) timeframe for precise trigger.
        
        LONG Entry Rules (macro_bias = BULLISH):
        1. RSI exits oversold zone (crosses UP through rsi_long_zone)
        2. MACD histogram turns positive (momentum shift)
        3. Volume exceeds 1.3x 20-period average
        4. Price is above VWAP (strength confirmation)
        
        SHORT Entry Rules (macro_bias = BEARISH):
        1. RSI exits overbought zone (crosses DOWN through rsi_short_zone)
        2. MACD histogram turns negative
        3. Volume exceeds 1.3x average
        4. Price is below VWAP
        """
        null_signal = EntrySignal(
            valid=False, side="", entry_price=0, sl_price=0,
            tp1_price=0, tp2_price=0, atr_value=0, rsi_value=0,
            macd_histogram=0, volume_ratio=0, vwap_value=0, reason="",
        )

        if macro_bias == "NEUTRAL":
            logger.debug("ENTRY SKIP: Macro bias is NEUTRAL")
            return null_signal

        if len(ohlcv) < max(macd_slow + macd_signal, atr_period, vol_ma_period) + 5:
            null_signal.reason = "Insufficient data"
            logger.debug("ENTRY SKIP: Insufficient data")
            return null_signal

        # Calculate indicators
        rsi_vals = rsi(ohlcv.closes, rsi_period)
        macd_line, sig_line, hist = macd(ohlcv.closes, macd_fast, macd_slow, macd_signal)
        atr_vals = atr(ohlcv.highs, ohlcv.lows, ohlcv.closes, atr_period)
        vol_avg = volume_ma(ohlcv.volumes, vol_ma_period)
        vwap_vals = vwap(ohlcv.highs, ohlcv.lows, ohlcv.closes, ohlcv.volumes) if use_vwap else None

        # Current values
        cur_rsi = float(rsi_vals[-1])
        prev_rsi = float(rsi_vals[-2]) if not np.isnan(rsi_vals[-2]) else cur_rsi
        cur_hist = float(hist[-1])
        prev_hist = float(hist[-2])
        cur_atr = float(atr_vals[-1])
        cur_vol = float(ohlcv.volumes[-1])
        avg_vol = float(vol_avg[-1]) if not np.isnan(vol_avg[-1]) else cur_vol
        cur_price = float(ohlcv.closes[-1])
        cur_vwap = float(vwap_vals[-1]) if (vwap_vals is not None and not np.isnan(vwap_vals[-1])) else cur_price

        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0

        if np.isnan(cur_rsi) or np.isnan(cur_atr) or cur_atr == 0:
            null_signal.reason = "Indicator NaN"
            logger.debug("ENTRY SKIP: Indicator NaN")
            return null_signal

        reasons = []
        is_valid = False

        if macro_bias == "BULLISH":
            # ── LONG Entry Conditions ──
            rsi_in_zone = rsi_long_zone[0] <= cur_rsi <= rsi_long_zone[1]
            rsi_rising = cur_rsi > prev_rsi
            macd_bullish = cur_hist > 0 and prev_hist <= 0  # Histogram crosses zero
            macd_positive = cur_hist > prev_hist and cur_hist > 0  # Or histogram increasing and positive
            volume_ok = vol_ratio >= vol_multiplier
            above_vwap = cur_price >= cur_vwap if use_vwap else True

            if rsi_in_zone and rsi_rising:
                reasons.append(f"RSI pullback recovery ({cur_rsi:.1f})")
            if macd_bullish:
                reasons.append("MACD histogram zero-cross UP")
            elif macd_positive:
                reasons.append(f"MACD momentum accelerating ({cur_hist:.4f})")
            if volume_ok:
                reasons.append(f"Volume surge ({vol_ratio:.1f}x avg)")
            if above_vwap:
                reasons.append("Price above VWAP")

            # Need at least 3 of 4 conditions (RSI + MACD mandatory)
            has_rsi = rsi_in_zone and rsi_rising
            has_macd = macd_bullish or macd_positive
            score = sum([has_rsi, has_macd, volume_ok, above_vwap])

            if has_rsi and has_macd and score >= 3:
                is_valid = True
                side = "LONG"
                sl = cur_price - (atr_sl_mult * cur_atr)
                tp1 = cur_price + (atr_tp1_mult * cur_atr)
                tp2 = cur_price + (atr_tp2_mult * cur_atr)

        elif macro_bias == "BEARISH":
            # ── SHORT Entry Conditions ──
            rsi_in_zone = rsi_short_zone[0] <= cur_rsi <= rsi_short_zone[1]
            rsi_falling = cur_rsi < prev_rsi
            macd_bearish = cur_hist < 0 and prev_hist >= 0
            macd_negative = cur_hist < prev_hist and cur_hist < 0
            volume_ok = vol_ratio >= vol_multiplier
            below_vwap = cur_price <= cur_vwap if use_vwap else True

            if rsi_in_zone and rsi_falling:
                reasons.append(f"RSI overbought rejection ({cur_rsi:.1f})")
            if macd_bearish:
                reasons.append("MACD histogram zero-cross DOWN")
            elif macd_negative:
                reasons.append(f"MACD momentum decelerating ({cur_hist:.4f})")
            if volume_ok:
                reasons.append(f"Volume surge ({vol_ratio:.1f}x avg)")
            if below_vwap:
                reasons.append("Price below VWAP")

            has_rsi = rsi_in_zone and rsi_falling
            has_macd = macd_bearish or macd_negative
            score = sum([has_rsi, has_macd, volume_ok, below_vwap])

            if has_rsi and has_macd and score >= 3:
                is_valid = True
                side = "SHORT"
                sl = cur_price + (atr_sl_mult * cur_atr)
                tp1 = cur_price - (atr_tp1_mult * cur_atr)
                tp2 = cur_price - (atr_tp2_mult * cur_atr)

        if not is_valid:
            null_signal.reason = "Conditions not met"
            null_signal.rsi_value = cur_rsi
            null_signal.macd_histogram = cur_hist
            null_signal.volume_ratio = vol_ratio
            null_signal.vwap_value = cur_vwap
            null_signal.atr_value = cur_atr
            
            # Additional debug context for the user to understand why it skipped
            logger.debug(
                f"ENTRY SKIP [{macro_bias}]: "
                f"RSI={cur_rsi:.1f}, MACD={cur_hist:.5f}, "
                f"Vol Ratio={vol_ratio:.2f}x, Price={cur_price:.2f}, "
                f"VWAP={cur_vwap:.2f} | Valid reasons met: {' | '.join(reasons) if reasons else 'None'}"
            )
            return null_signal

        signal = EntrySignal(
            valid=True,
            side=side,
            entry_price=cur_price,
            sl_price=round(sl, 2),
            tp1_price=round(tp1, 2),
            tp2_price=round(tp2, 2),
            atr_value=cur_atr,
            rsi_value=cur_rsi,
            macd_histogram=cur_hist,
            volume_ratio=vol_ratio,
            vwap_value=cur_vwap,
            reason=" | ".join(reasons),
        )

        logger.info(
            f"ENTRY SIGNAL: {signal.side} @ {signal.entry_price:.2f} "
            f"SL={signal.sl_price:.2f} TP1={signal.tp1_price:.2f} TP2={signal.tp2_price:.2f} "
            f"| {signal.reason}"
        )
        return signal
