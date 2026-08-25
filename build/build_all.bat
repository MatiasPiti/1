@echo off
REM =====================================================================
REM Compilacion de los 5 ejecutables portables del Sistema Dual.
REM Ejecutar desde la raiz del repo: build\build_all.bat
REM Requiere: pip install -r requirements.txt
REM Genera cada app en --onedir (carpeta con .exe + dependencias), que es
REM lo que necesitan los USBs (estructura de carpetas portable).
REM =====================================================================

REM Se usa "python -m PyInstaller" en vez de "pyinstaller" a secas porque en
REM Windows es comun que pip instale el .exe en una carpeta Scripts que no
REM esta en el PATH; invocandolo como modulo de Python siempre funciona,
REM sin depender de esa configuracion.
set PYI=python -m PyInstaller --noconfirm --clean --onedir --windowed
set DATA=--add-data "sql\schema.sql;sql" --add-data "apps\assets;apps\assets"
set ICON=--icon apps\assets\logo_otter.ico

echo.
echo === 1/5 Otter Caja ===
%PYI% %DATA% %ICON% --name MaestroCaja --paths . apps\master_caja\main.py

echo.
echo === 2/5 Otter Dueno ===
%PYI% %DATA% %ICON% --name MaestroDueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\master_dueno\main.py

echo.
echo === 3/5 USB Caja (emergencia) ===
%PYI% %DATA% %ICON% --name USB_Caja --paths . apps\usb_caja\main.py

echo.
echo === 4/5 USB Dueno (emergencia) ===
%PYI% %DATA% %ICON% --name USB_Dueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\usb_dueno\main.py

echo.
echo === 5/5 USB Mantenimiento (Desarrollador) ===
%PYI% %DATA% %ICON% --name USB_Mantenimiento --paths . apps\usb_dev\mantenimiento.py

echo.
echo === Servicio oculto de stock (Windows Service, consola oculta) ===
REM --noconsole = pythonw.exe embebido, sin ventana visible.
python -m PyInstaller --noconfirm --clean --onedir --noconsole %DATA% %ICON% ^
    --name StockService --paths . ^
    --hidden-import win32timezone ^
    services\stock_windows_service.py

echo.
echo Listo. Los ejecutables quedan en dist\NombreApp\NombreApp.exe
echo Copia cada carpeta dist\MaestroCaja, dist\MaestroDueno, etc. a su
echo destino final (disco de la PC fija o raiz del USB correspondiente).
