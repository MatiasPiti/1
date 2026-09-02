@echo off
REM =====================================================================
REM Compilacion de los 6 ejecutables portables de Otter + el servicio
REM oculto de stock (7 en total).
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
echo === 5/6 USB Mantenimiento (Desarrollador) ===
%PYI% %DATA% %ICON% --name USB_Mantenimiento --paths . apps\usb_dev\mantenimiento.py

echo.
echo === 6/6 Otter Dueno Remoto (otra PC, vía Tailscale) ===
%PYI% %DATA% %ICON% --name DuenoRemoto --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\dueno_remoto\main.py

echo.
echo === Servicio oculto de stock (Windows Service, consola oculta) ===
REM --noconsole = pythonw.exe embebido, sin ventana visible.
REM Los modulos de pywin32 del servicio se declaran explicitamente: cuando
REM el Administrador de servicios arranca el .exe, si falta alguno el
REM servicio muere sin dejar rastro visible (queda en "Detenido" y nada mas).
python -m PyInstaller --noconfirm --clean --onedir --noconsole %DATA% %ICON% ^
    --name StockService --paths . ^
    --hidden-import win32timezone ^
    --hidden-import servicemanager ^
    --hidden-import win32serviceutil ^
    --hidden-import win32service ^
    --hidden-import win32event ^
    services\stock_windows_service.py

echo.
echo === Copiando copia de referencia (espejo_apps) al USB de Mantenimiento ===
REM El USB de Mantenimiento necesita llevar encima una copia "conocida
REM buena" de cada app recien compilada: es contra eso que compara y
REM repone archivos danados/faltantes en una instalacion (ver
REM apps/usb_dev/mantenimiento.py::reparar_archivos_app). Se copia DESPUES
REM de compilar todo, asi siempre lleva la version mas reciente del build.
if exist dist\USB_Mantenimiento\espejo_apps rmdir /S /Q dist\USB_Mantenimiento\espejo_apps
xcopy /E /I /Y dist\MaestroCaja dist\USB_Mantenimiento\espejo_apps\MaestroCaja >nul
xcopy /E /I /Y dist\MaestroDueno dist\USB_Mantenimiento\espejo_apps\MaestroDueno >nul
xcopy /E /I /Y dist\USB_Caja dist\USB_Mantenimiento\espejo_apps\USB_Caja >nul
xcopy /E /I /Y dist\USB_Dueno dist\USB_Mantenimiento\espejo_apps\USB_Dueno >nul
xcopy /E /I /Y dist\StockService dist\USB_Mantenimiento\espejo_apps\StockService >nul

echo.
echo Listo. Los ejecutables quedan en dist\NombreApp\NombreApp.exe
echo Copia cada carpeta dist\MaestroCaja, dist\MaestroDueno, etc. a su
echo destino final (disco de la PC fija o raiz del USB correspondiente).
echo El USB de Mantenimiento (dist\USB_Mantenimiento) ya sale con su
echo espejo_apps\ incluido: al conectarlo, "REPARAR TODO AUTOMATICAMENTE"
echo puede reponer archivos danados o desactualizados en cualquier
echo instalacion usando esta misma copia recien compilada.
echo dist\DuenoRemoto se instala en la PC del dueño, en otra ubicación,
echo conectada a la PC del local por Tailscale (ver README, seccion de
echo Dueño Remoto, para habilitar [remoto] en el config.ini del Maestro).
