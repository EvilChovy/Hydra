#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║    ██╗  ██╗██╗   ██╗██████╗ ██████╗  █████╗                        ║
║    ██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██╔══██╗                       ║
║    ███████║ ╚████╔╝ ██║  ██║██████╔╝███████║                       ║
║    ██╔══██║  ╚██╔╝  ██║  ██║██╔══██╗██╔══██║                       ║
║    ██║  ██║   ██║   ██████╔╝██║  ██║██║  ██║                       ║
║    ╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝                       ║
║                                                                      ║
║    Aggressive Cross-Margin Trading Bot for Binance                   ║
║                                                                      ║
║    Edita settings.py para configurar el bot.                         ║
║    Pon tus API keys en el archivo .env                               ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

WINDOWS:
    1. Edita .env con tus API keys
    2. Edita settings.py con tu configuración
    3. Ejecuta: start.bat   (o:  venv\\Scripts\\python main.py)

LINUX:
    1. source .env && export BINANCE_API_KEY BINANCE_API_SECRET
    2. python main.py
"""

import os
import sys
import signal
import logging
import logging.handlers
from pathlib import Path


# ── Load .env file if it exists (Windows support) ──
def load_dotenv():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        os.environ[key] = value

load_dotenv()

# ── Import settings and config ──
from config import (
    HydraConfig, ExchangeConfig, TradingPairConfig,
    TimeframeConfig, StrategyConfig, RiskConfig, LogConfig,
)

try:
    import settings
except ImportError:
    print("ERROR: No se encontró settings.py")
    print("Copia settings.py al directorio del bot y configúralo.")
    sys.exit(1)


def build_config_from_settings() -> HydraConfig:
    """Build HydraConfig from user's settings.py values."""
    config = HydraConfig(
        exchange=ExchangeConfig(
            API_KEY=os.environ.get("BINANCE_API_KEY", ""),
            API_SECRET=os.environ.get("BINANCE_API_SECRET", ""),
        ),
        pair=TradingPairConfig(
            SYMBOL=getattr(settings, "SYMBOL", "ETHUSDC"),
            BASE_ASSET=getattr(settings, "BASE_ASSET", "ETH"),
            QUOTE_ASSET=getattr(settings, "QUOTE_ASSET", "USDC"),
            QTY_PRECISION=getattr(settings, "QTY_PRECISION", 4),
            PRICE_PRECISION=getattr(settings, "PRICE_PRECISION", 2),
        ),
        timeframe=TimeframeConfig(
            MACRO_TIMEFRAME=getattr(settings, "MACRO_TIMEFRAME", "4h"),
            ENTRY_TIMEFRAME=getattr(settings, "ENTRY_TIMEFRAME", "5m"),
        ),
        strategy=StrategyConfig(
            ATR_SL_MULTIPLIER=getattr(settings, "SL_ATR_MULTIPLIER", 1.8),
            ATR_TP1_MULTIPLIER=getattr(settings, "TP1_ATR_MULTIPLIER", 2.2),
            ATR_TP2_MULTIPLIER=getattr(settings, "TP2_ATR_MULTIPLIER", 4.5),
            ATR_TRAILING_MULTIPLIER=getattr(settings, "TRAILING_ATR_MULTIPLIER", 2.0),
            MAX_DAILY_TRADES=getattr(settings, "MAX_TRADES_PER_DAY", 12),
            MAX_CONSECUTIVE_LOSSES=getattr(settings, "MAX_CONSECUTIVE_LOSSES", 4),
        ),
        risk=RiskConfig(
            LEVERAGE=getattr(settings, "LEVERAGE", 5),
            RISK_PER_TRADE_PCT=getattr(settings, "RISK_PER_TRADE", 0.04),
            TP1_CLOSE_PCT=getattr(settings, "TP1_CLOSE_PERCENT", 0.60),
            TP2_CLOSE_PCT=getattr(settings, "TP2_CLOSE_PERCENT", 0.40),
            MAX_DAILY_DRAWDOWN_PCT=getattr(settings, "MAX_DAILY_LOSS", 0.15),
            MAX_TOTAL_DRAWDOWN_PCT=getattr(settings, "MAX_TOTAL_DRAWDOWN", 0.30),
        ),
        log=LogConfig(
            LOG_LEVEL=getattr(settings, "LOG_LEVEL", "INFO"),
        ),
    )
    return config


def setup_logging(config: HydraConfig) -> None:
    """Configure production logging with rotation."""
    log_dir = Path(config.log.LOG_DIR)
    log_dir.mkdir(exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.log.LOG_LEVEL))

    formatter = logging.Formatter(
        config.log.LOG_FORMAT, datefmt=config.log.LOG_DATE_FORMAT,
    )

    if config.log.LOG_TO_CONSOLE:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root_logger.addHandler(console)

    if config.log.LOG_TO_FILE:
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "hydra.log",
            maxBytes=config.log.MAX_LOG_SIZE_MB * 1024 * 1024,
            backupCount=config.log.LOG_BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        error_handler = logging.handlers.RotatingFileHandler(
            log_dir / "hydra_errors.log",
            maxBytes=config.log.MAX_LOG_SIZE_MB * 1024 * 1024,
            backupCount=config.log.LOG_BACKUP_COUNT, encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root_logger.addHandler(error_handler)

        trade_handler = logging.handlers.RotatingFileHandler(
            log_dir / "hydra_trades.log",
            maxBytes=config.log.MAX_LOG_SIZE_MB * 1024 * 1024,
            backupCount=config.log.LOG_BACKUP_COUNT, encoding="utf-8",
        )
        trade_handler.setFormatter(formatter)
        logging.getLogger("hydra.trades").addHandler(trade_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def print_paper_summary(bot, logger):
    """Print paper trading results on exit."""
    if hasattr(bot.exchange, "get_paper_summary"):
        s = bot.exchange.get_paper_summary()
        logger.info("═══ RESUMEN PAPER TRADING ═══")
        logger.info(f"  Balance inicial:  ${s['starting_balance']:,.2f}")
        logger.info(f"  Balance final:    ${s['current_equity']:,.2f}")
        logger.info(f"  P&L:              ${s['pnl_usdc']:+,.2f} ({s['pnl_pct']:+.2f}%)")
        logger.info(f"  Total trades:     {s['total_trades']}")
        logger.info(f"  Fees pagados:     ${s['total_fees']:,.4f}")


def main():
    """Main entry point."""
    config = build_config_from_settings()
    setup_logging(config)
    logger = logging.getLogger("hydra.main")

    is_paper = getattr(settings, "PAPER_TRADING", True)

    if is_paper:
        starting_bal = getattr(settings, "PAPER_STARTING_BALANCE_USDC", 1000.0)
        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════╗")
        logger.info("║         MODO PAPER TRADING (SIMULACIÓN)              ║")
        logger.info(f"║    Balance simulado: ${starting_bal:>10,.2f} USDC             ║")
        logger.info("║    No se tocará tu dinero real.                       ║")
        logger.info("╚══════════════════════════════════════════════════════╝")
    else:
        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════╗")
        logger.info("║       ⚠️  MODO TRADING REAL — DINERO REAL ⚠️          ║")
        logger.info(f"║    Par: {config.pair.SYMBOL:>8s} | Leverage: {config.risk.LEVERAGE}x               ║")
        logger.info(f"║    Riesgo por trade: {config.risk.RISK_PER_TRADE_PCT:.0%}                          ║")
        logger.info("╚══════════════════════════════════════════════════════╝")

    # Validate config
    errors = config.validate()
    if errors:
        for err in errors:
            logger.critical(f"Config error: {err}")
        if any("API_KEY" in e or "API_SECRET" in e for e in errors):
            logger.critical("")
            logger.critical("NECESITAS API KEYS para obtener datos de mercado.")
            logger.critical("Pasos:")
            logger.critical("  1. Ve a https://www.binance.com/en/my/settings/api-management")
            logger.critical("  2. Crea un API key (solo necesita permiso de lectura para Paper)")
            logger.critical("  3. Edita el archivo .env con tus keys")
            sys.exit(1)
        if not is_paper:
            sys.exit(1)

    # ── Create Bot ──
    from bot import HydraBot
    bot = HydraBot(config)

    if is_paper:
        from paper_exchange import PaperExchangeClient
        starting_bal = getattr(settings, "PAPER_STARTING_BALANCE_USDC", 1000.0)
        paper_client = PaperExchangeClient(config.exchange, starting_bal)
        # Replace all exchange references with paper client
        bot.exchange = paper_client
        bot.strategy.exchange = paper_client
        bot.risk.exchange = paper_client
        bot.trade_mgr.exchange = paper_client
        bot.reconciler.exchange = paper_client

    # Signal handler
    def handle_shutdown(signum, frame):
        logger.info(f"Señal {signal.Signals(signum).name} recibida — cerrando...")
        if is_paper:
            print_paper_summary(bot, logger)
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Print active config
    logger.info("")
    logger.info("═══ CONFIGURACIÓN ACTIVA ═══")
    logger.info(f"  Modo:             {'PAPER (simulación)' if is_paper else 'REAL'}")
    logger.info(f"  Par:              {config.pair.SYMBOL}")
    logger.info(f"  Macro TF:         {config.timeframe.MACRO_TIMEFRAME}")
    logger.info(f"  Entry TF:         {config.timeframe.ENTRY_TIMEFRAME}")
    logger.info(f"  Leverage:         {config.risk.LEVERAGE}x")
    logger.info(f"  Riesgo/trade:     {config.risk.RISK_PER_TRADE_PCT:.0%}")
    logger.info(f"  SL:               {config.strategy.ATR_SL_MULTIPLIER}x ATR")
    logger.info(f"  TP1:              {config.strategy.ATR_TP1_MULTIPLIER}x ATR (cierre {config.risk.TP1_CLOSE_PCT:.0%})")
    logger.info(f"  TP2:              {config.strategy.ATR_TP2_MULTIPLIER}x ATR (cierre {config.risk.TP2_CLOSE_PCT:.0%})")
    logger.info(f"  Trailing:         {config.strategy.ATR_TRAILING_MULTIPLIER}x ATR")
    logger.info(f"  Max daily trades: {config.strategy.MAX_DAILY_TRADES}")
    logger.info(f"  Max daily loss:   {config.risk.MAX_DAILY_DRAWDOWN_PCT:.0%}")
    logger.info(f"  Max drawdown:     {config.risk.MAX_TOTAL_DRAWDOWN_PCT:.0%}")
    logger.info("")

    # Start
    try:
        logger.info("Iniciando HYDRA bot... (Ctrl+C para detener)")
        logger.info("")
        bot.start()
        bot.wait()
    except KeyboardInterrupt:
        logger.info("Ctrl+C — cerrando...")
        if is_paper:
            print_paper_summary(bot, logger)
        bot.stop()
    except Exception as e:
        logger.critical(f"Error fatal: {e}", exc_info=True)
        bot.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()