#!/bin/bash
# ══════════════════════════════════════════════
#  HYDRA BOT — Pi OS Lite Launcher
#  Equivalent to start.bat for Linux
# ══════════════════════════════════════════════

echo ""
echo "  ====  H Y D R A  ===="
echo "  Cross-Margin Trading Bot"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Cargar variables desde .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    echo "[OK] Variables de entorno cargadas desde .env"
else
    echo "[ERROR] No se encontro archivo .env"
    exit 1
fi

# Verificar keys
if [ -z "$BINANCE_API_KEY" ]; then
    echo "[ERROR] BINANCE_API_KEY no esta definida en .env"
    exit 1
fi
if [ -z "$BINANCE_API_SECRET" ]; then
    echo "[ERROR] BINANCE_API_SECRET no esta definida en .env"
    exit 1
fi
echo "[OK] API Keys detectadas"

# Detectar venv
PYTHON=""
if [ -f .venv/bin/python ]; then
    PYTHON=".venv/bin/python"
    echo "[OK] Virtual environment: .venv"
elif [ -f venv/bin/python ]; then
    PYTHON="venv/bin/python"
    echo "[OK] Virtual environment: venv"
else
    echo "[ERROR] No se encontro virtual environment."
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

echo ""

# Lanzar log viewer en background
echo "[OK] Abriendo Log Viewer en http://$(hostname -I | awk '{print $1}'):8777"
$PYTHON log_viewer.py &
LOG_VIEWER_PID=$!

# Esperar 1 segundo
sleep 1

# Funcion para limpiar al salir
cleanup() {
    echo ""
    echo "Cerrando Log Viewer (PID $LOG_VIEWER_PID)..."
    kill $LOG_VIEWER_PID 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

# Iniciar el bot
echo "Iniciando HYDRA bot..."
echo "========================================"
$PYTHON main.py

# Si el bot termina, matar el log viewer
cleanup
