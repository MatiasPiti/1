@echo off
REM =====================================================================
REM Compilacion de los 5 ejecutables portables del Sistema Dual, mas el
REM servicio de stock y la API del dueno para la app del celular.
REM Ejecutar: build\build_all.bat   (desde cualquier carpeta)
REM Requiere: python -m pip install -r requirements.txt
REM Genera cada app en --onedir (carpeta con .exe + dependencias), que es
REM lo que necesitan los USBs (estructura de carpetas portable).
REM =====================================================================
setlocal

REM Siempre desde la raiz del repo, aunque se lo llame desde build\ u otra carpeta.
pushd "%~dp0.."

REM Se usa "python -m PyInstaller" y no "pyinstaller": el comando suelto
REM vive en la carpeta Scripts de Python, que muchas instalaciones no ponen
REM en el PATH (pip lo avisa con un WARNING) y entonces ningun paso compila.
set "PY=python"
%PY% --version >nul 2>nul || set "PY=py"
%PY% --version >nul 2>nul || (
    echo ERROR: no se encontro Python. Instalalo desde python.org y volve a correr este script.
    popd & exit /b 1
)
%PY% -m PyInstaller --version >nul 2>nul || (
    echo ERROR: falta PyInstaller. Corre primero: %PY% -m pip install -r requirements.txt
    popd & exit /b 1
)

set "PYI=%PY% -m PyInstaller --noconfirm --clean --onedir"
set DATA=--add-data "sql\schema.sql;sql"
set "FALLARON="

echo.
echo === 1/7 Sistema Maestro - Caja ===
%PYI% --windowed %DATA% --name MaestroCaja --paths . apps\master_caja\main.py
if errorlevel 1 set "FALLARON=%FALLARON% MaestroCaja"

echo.
echo === 2/7 Sistema Maestro - Panel del Dueno ===
%PYI% --windowed %DATA% --name MaestroDueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\master_dueno\main.py
if errorlevel 1 set "FALLARON=%FALLARON% MaestroDueno"

echo.
echo === 3/7 USB Caja (emergencia) ===
%PYI% --windowed %DATA% --name USB_Caja --paths . apps\usb_caja\main.py
if errorlevel 1 set "FALLARON=%FALLARON% USB_Caja"

echo.
echo === 4/7 USB Dueno (emergencia) ===
%PYI% --windowed %DATA% --name USB_Dueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\usb_dueno\main.py
if errorlevel 1 set "FALLARON=%FALLARON% USB_Dueno"

echo.
echo === 5/7 USB Mantenimiento (Desarrollador) ===
%PYI% --windowed %DATA% --name USB_Mantenimiento --paths . apps\usb_dev\mantenimiento.py
if errorlevel 1 set "FALLARON=%FALLARON% USB_Mantenimiento"

echo.
echo === 6/7 Servicio oculto de stock (Windows Service, consola oculta) ===
REM --noconsole = pythonw.exe embebido, sin ventana visible.
%PYI% --noconsole %DATA% ^
    --name StockService --paths . ^
    --hidden-import win32timezone ^
    services\stock_windows_service.py
if errorlevel 1 set "FALLARON=%FALLARON% StockService"

echo.
echo === 7/7 API del Dueno para la app del celular (sin ventana) ===
REM uvicorn carga sus protocolos dinamicamente: --collect-submodules los
REM incluye. Se lanza con --base apuntando a la carpeta de MaestroDueno;
REM --definir-pin PIN define el PIN del dueno y sale. Como no tiene consola,
REM todo lo que informa queda en logs\api_dueno.log de esa carpeta.
%PYI% --noconsole %DATA% ^
    --name ApiDueno --paths . ^
    --collect-submodules uvicorn ^
    --collect-data pdfminer ^
    services\api_dueno.py
if errorlevel 1 set "FALLARON=%FALLARON% ApiDueno"

echo.
if defined FALLARON (
    echo ERROR: no se pudieron compilar:%FALLARON%
    echo Revisa los mensajes de arriba. Lo que si compilo quedo en dist\NombreApp\NombreApp.exe
    popd & exit /b 1
)
echo Listo. Los ejecutables quedan en %CD%\dist\NombreApp\NombreApp.exe
echo Copia cada carpeta dist\MaestroCaja, dist\MaestroDueno, dist\ApiDueno, etc.
echo completa a su destino final (disco de la PC fija o raiz del USB correspondiente).
popd
endlocal
