@echo off

echo.
echo  ====  H Y D R A  ====
echo  Cross-Margin Trading Bot
echo.

REM Cargar variables desde .env
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        echo %%a | findstr /r "^#" >nul || set "%%a=%%b"
    )
    echo [OK] Variables de entorno cargadas desde .env
) else (
    echo [ERROR] No se encontro archivo .env
    pause
    exit /b 1
)

REM Verificar keys
if "%BINANCE_API_KEY%"=="" (
    echo [ERROR] BINANCE_API_KEY no esta definida en .env
    pause
    exit /b 1
)
if "%BINANCE_API_SECRET%"=="" (
    echo [ERROR] BINANCE_API_SECRET no esta definida en .env
    pause
    exit /b 1
)
echo [OK] API Keys detectadas
echo.

REM Detectar Python
set PYTHON=
if exist .venv\Scripts\python.exe (
    set PYTHON=.venv\Scripts\python.exe
    echo [OK] Virtual environment: .venv
) else if exist venv\Scripts\python.exe (
    set PYTHON=venv\Scripts\python.exe
    echo [OK] Virtual environment: venv
) else (
    echo [ERROR] No se encontro virtual environment.
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

REM Lanzar log viewer en ventana separada
echo [OK] Abriendo Log Viewer en http://localhost:8777
start "HYDRA Log Viewer" %PYTHON% log_viewer.py

REM Esperar 1 segundo para que el servidor arranque
timeout /t 1 /nobreak >nul

REM Abrir el navegador
start http://localhost:8777

REM Iniciar el bot en esta ventana
echo.
echo Iniciando HYDRA bot...
echo ========================================
%PYTHON% main.py %*

pause