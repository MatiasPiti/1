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
REM pos_core.instancia_unica se importa adentro de una funcion (el candado
REM que evita abrir la misma app dos veces). Se declara a mano para que no
REM quede afuera del .exe por un descuido del analisis automatico.
set UNICA=--hidden-import pos_core.instancia_unica

echo.
echo === 1/7 Otter Caja ===
%PYI% %DATA% %ICON% --name MaestroCaja --paths . ^
    %UNICA% apps\master_caja\main.py

echo.
echo === 2/7 Otter Dueno ===
%PYI% %DATA% %ICON% --name MaestroDueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    %UNICA% apps\master_dueno\main.py

echo.
echo === 3/7 USB Caja (emergencia) ===
%PYI% %DATA% %ICON% --name USB_Caja --paths . ^
    %UNICA% apps\usb_caja\main.py

echo.
echo === 4/7 USB Dueno (emergencia) ===
%PYI% %DATA% %ICON% --name USB_Dueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    %UNICA% apps\usb_dueno\main.py

echo.
echo === 5/7 USB Mantenimiento (Desarrollador) ===
%PYI% %DATA% %ICON% --name USB_Mantenimiento --paths . apps\usb_dev\mantenimiento.py

echo.
echo === 6/7 Otter Dueno Remoto (otra PC, vía Tailscale) ===
%PYI% %DATA% %ICON% --name DuenoRemoto --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\dueno_remoto\main.py

echo.
echo === 7/7 Instalador ===
REM El instalador busca las carpetas ya compiladas (MaestroCaja,
REM DuenoRemoto, StockService...) AL LADO SUYO cuando se ejecuta, no al
REM compilarse: por eso alcanza con que dist\ viaje entero al pendrive.
REM win32com.client se importa adentro de una funcion (para crear los
REM accesos directos), asi que hay que declararlo a mano.
%PYI% %DATA% %ICON% --name OtterInstalador --paths . ^
    --hidden-import win32com.client ^
    apps\instalador\main.py

echo.
echo === Servicio oculto de stock (Windows Service) ===
REM OJO: este es el UNICO ejecutable que NO lleva --noconsole, y es a
REM proposito. Con --noconsole el .exe se queda sin stdout, y lo primero
REM que hace "StockService.exe install" es imprimir "Installing service...":
REM al no existir la salida, falla y la instalacion se aborta sin mostrar
REM ningun error (el servicio simplemente nunca aparece).
REM
REM No hace falta ocultar nada igual: un Servicio de Windows lo arranca el
REM sistema en una sesion aislada, asi que NUNCA muestra ventana, este
REM compilado como este. La consola solo se ve al correr install/start/stop
REM a mano desde una terminal, que es justo cuando conviene verla.
REM
REM Los modulos de pywin32 se declaran explicitamente: cuando el
REM Administrador de servicios arranca el .exe, si falta alguno el
REM servicio muere sin dejar rastro visible (queda en "Detenido" y nada mas).
python -m PyInstaller --noconfirm --clean --onedir %DATA% %ICON% ^
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
