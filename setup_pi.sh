#!/bin/bash
# ══════════════════════════════════════════════
#  HYDRA BOT — Pi Setup (run once)
# ══════════════════════════════════════════════

echo ""
echo "  ====  HYDRA Pi Setup  ===="
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/5] Instalando dependencias del sistema..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-venv python3-pip

echo ""
echo "[2/5] Creando virtual environment..."
python3 -m venv .venv
echo "      Hecho: .venv/"

echo ""
echo "[3/5] Instalando paquetes Python..."
.venv/bin/pip install --quiet -r requirements.txt
echo "      Hecho: requests + numpy"

echo ""
echo "[4/5] Verificando .env..."
if [ -f .env ]; then
    echo "      .env encontrado"
else
    echo "      Creando .env de ejemplo..."
    cat > .env << 'ENVEOF'
# HYDRA BOT - Credenciales
BINANCE_API_KEY=PEGA_TU_API_KEY_AQUI
BINANCE_API_SECRET=PEGA_TU_SECRET_AQUI
ENVEOF
    echo "      EDITA .env con tus API keys: nano .env"
fi

echo ""
echo "[5/5] Dando permisos de ejecucion..."
chmod +x start.sh

echo ""
echo "========================================"
echo "  Setup completado!"
echo ""
echo "  Pasos:"
echo "    1. Edita tus keys:    nano .env"
echo "    2. Edita config:      nano settings.py"
echo "    3. Ejecuta:           ./start.sh"
echo ""
echo "  Para ejecutar como servicio (auto-arranque):"
echo "    sudo cp hydra.service /etc/systemd/system/"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl enable hydra"
echo "    sudo systemctl start hydra"
echo "========================================"
