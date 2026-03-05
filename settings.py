"""
╔══════════════════════════════════════════════════════════════════════╗
║  HYDRA BOT — TU CONFIGURACIÓN PERSONAL                              ║
║                                                                      ║
║  EDITA ESTE ARCHIVO para ajustar el bot a tu situación.              ║
║  Cada parámetro tiene una explicación en español.                    ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# ═══════════════════════════════════════════════════════════════════
# 1. MODO DE OPERACIÓN
# ═══════════════════════════════════════════════════════════════════
#
# True  = PAPER TRADING (simulación, NO toca tu dinero real)
#         Usa precios reales de Binance pero las órdenes son ficticias.
#         SIEMPRE empezar aquí para verificar que todo funciona.
#
# False = TRADING REAL (usa tu dinero, cuidado)
#
PAPER_TRADING = False

# ═══════════════════════════════════════════════════════════════════
# 2. CAPITAL INICIAL (solo para Paper Trading)
# ═══════════════════════════════════════════════════════════════════
#
# Cuántos USDC simulados quieres usar para probar.
# En modo REAL, el bot usa automáticamente el saldo que tengas
# en tu cuenta de Cross Margin en Binance.
#
PAPER_STARTING_BALANCE_USDC = 1000.0

# ═══════════════════════════════════════════════════════════════════
# 3. RIESGO POR TRADE
# ═══════════════════════════════════════════════════════════════════
#
# Porcentaje de tu capital total que arriesgas en CADA trade.
# Ejemplo: con $1000 y 4% de riesgo, cada trade arriesga $40.
#
# RECOMENDACIONES:
#   0.01 = 1%  → Conservador (para cuentas grandes)
#   0.02 = 2%  → Moderado
#   0.04 = 4%  → Agresivo (default del bot)
#   0.06 = 6%  → Muy agresivo
#   0.10 = 10% → Máximo permitido (el bot bloquea más de 10%)
#
RISK_PER_TRADE = 0.04

# ═══════════════════════════════════════════════════════════════════
# 4. APALANCAMIENTO
# ═══════════════════════════════════════════════════════════════════
#
# Multiplicador de tu capital. Con 5x y $1000, puedes abrir
# posiciones de hasta $5000.
#
# OPCIONES: 3, 5, o 10
# Mayor apalancamiento = mayor riesgo de liquidación
#
LEVERAGE = 5

# ═══════════════════════════════════════════════════════════════════
# 5. TAKE PROFIT / STOP LOSS (Distancias en múltiplos de ATR)
# ═══════════════════════════════════════════════════════════════════
#
# ATR = Average True Range = la "volatilidad normal" de cada vela.
# Si ETH tiene un ATR de $50 en 5min:
#   SL a 1.8x ATR = stop loss a $90 del precio de entrada
#   TP1 a 2.2x ATR = primer objetivo a $110 de la entrada
#   TP2 a 4.5x ATR = segundo objetivo a $225 de la entrada
#
# Más alto = más distancia, menos stops prematuros, menos trades.
# Más bajo = más trades, más stops tocados.
#
SL_ATR_MULTIPLIER = 1.8     # Distancia del Stop Loss (default: 1.8)
TP1_ATR_MULTIPLIER = 2.2    # Distancia del Take Profit 1 (default: 2.2)
TP2_ATR_MULTIPLIER = 4.5    # Distancia del Take Profit 2 (default: 4.5)
TRAILING_ATR_MULTIPLIER = 2.0  # Distancia del Trailing Stop (default: 2.0)

# ═══════════════════════════════════════════════════════════════════
# 6. CIERRE PARCIAL EN TP1
# ═══════════════════════════════════════════════════════════════════
#
# Cuando el precio llega a TP1, ¿qué porcentaje de la posición cierras?
# El resto se deja correr con trailing stop hacia TP2.
#
# 0.60 = cierras 60% en TP1, dejas 40% corriendo
# 0.50 = mitad y mitad
# 0.75 = aseguras más ganancias rápido, menos potencial de TP2
#
# AMBOS DEBEN SUMAR 1.0
#
TP1_CLOSE_PERCENT = 0.60
TP2_CLOSE_PERCENT = 0.40     # Esto = 1.0 - TP1_CLOSE_PERCENT

# ═══════════════════════════════════════════════════════════════════
# 7. PROTECCIONES (Circuit Breakers)
# ═══════════════════════════════════════════════════════════════════
#
# El bot se DETIENE automáticamente si:
#
# Pérdida diaria máxima (% del equity al inicio del día)
MAX_DAILY_LOSS = 0.15        # 15% = para el bot por hoy

# Pérdida total desde el máximo histórico del equity
MAX_TOTAL_DRAWDOWN = 0.30    # 30% = para el bot completamente

# Máximo de trades por día (evita overtrading)
MAX_TRADES_PER_DAY = 12

# Pausa después de X pérdidas consecutivas
MAX_CONSECUTIVE_LOSSES = 4

# ═══════════════════════════════════════════════════════════════════
# 8. PAR DE TRADING
# ═══════════════════════════════════════════════════════════════════
#
# ETHUSDC es el default. Otros pares posibles:
#   "BTCUSDC"  → Menos volátil, más seguro
#   "SOLUSDC"  → Muy volátil, más oportunidades pero más riesgo
#
# IMPORTANTE: Si cambias el par, verifica que tenga Cross Margin
# habilitado en Binance y ajusta QTY_PRECISION.
#
SYMBOL = "ETHUSDC"
BASE_ASSET = "ETH"
QUOTE_ASSET = "USDC"
QTY_PRECISION = 4    # ETH=4 decimales, BTC=5, SOL=2
PRICE_PRECISION = 2

# ═══════════════════════════════════════════════════════════════════
# 9. TIMEFRAMES
# ═══════════════════════════════════════════════════════════════════
#
# Macro: para detectar la tendencia general
# Entry: para encontrar el punto exacto de entrada
#
MACRO_TIMEFRAME = "4h"    # Opciones: "1h", "4h", "1d"
ENTRY_TIMEFRAME = "5m"    # Opciones: "1m", "3m", "5m", "15m"

# ═══════════════════════════════════════════════════════════════════
# 10. LOGGING
# ═══════════════════════════════════════════════════════════════════
#
# "DEBUG" = muestra TODO (muy verboso, útil para debugging)
# "INFO"  = muestra operaciones y eventos importantes (recomendado)
# "WARNING" = solo advertencias y errores
#
LOG_LEVEL = "INFO"
