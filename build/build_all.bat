@echo off
REM =====================================================================
REM Compilacion de Otter: los 10 ejecutables (9 portables + el servicio de
REM stock) + la API del celular (opcional). 11 carpetas en dist\ en total.
REM La API del celular (ApiCelular) va AL FINAL y es opcional: si no compila
REM o no pasa su autoprueba se avisa y lo demas queda igual (no se lleva al
REM local, nada mas).
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
echo === 1/11 Otter Caja ===
%PYI% %DATA% %ICON% --name MaestroCaja --paths . ^
    %UNICA% apps\master_caja\main.py

echo.
echo === 2/11 Otter Dueno ===
%PYI% %DATA% %ICON% --name MaestroDueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    %UNICA% apps\master_dueno\main.py

echo.
echo === 3/11 USB Caja (emergencia) ===
%PYI% %DATA% %ICON% --name USB_Caja --paths . ^
    %UNICA% apps\usb_caja\main.py

echo.
echo === 4/11 USB Dueno (emergencia) ===
%PYI% %DATA% %ICON% --name USB_Dueno --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    %UNICA% apps\usb_dueno\main.py

echo.
echo === 5/11 USB Mantenimiento (Desarrollador) ===
REM pos_core.servicio_windows y win32service se importan adentro de funciones
REM (revisar el servicio de stock y el de la API del celular): a mano, igual
REM que en el Actualizador.
%PYI% %DATA% %ICON% --name USB_Mantenimiento --paths . ^
    --hidden-import pos_core.servicio_windows --hidden-import win32service ^
    apps\usb_dev\mantenimiento.py

echo.
echo === 6/11 Otter Dueno Remoto (otra PC, vía Tailscale) ===
%PYI% %DATA% %ICON% --name DuenoRemoto --paths . ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    apps\dueno_remoto\main.py

echo.
echo === 7/11 Instalador ===
REM El instalador busca las carpetas ya compiladas (MaestroCaja,
REM DuenoRemoto, StockService...) AL LADO SUYO cuando se ejecuta, no al
REM compilarse: por eso alcanza con que dist\ viaje entero al pendrive.
REM win32com.client se importa adentro de una funcion (para crear los
REM accesos directos), asi que hay que declararlo a mano. Lo mismo
REM pos_core.servicio_windows, win32service y el filtro de datos del
REM Actualizador (apps.actualizador.main), que usa la instalacion opcional de
REM la API del celular.
%PYI% %DATA% %ICON% --name OtterInstalador --paths . ^
    --hidden-import win32com.client ^
    --hidden-import pos_core.servicio_windows --hidden-import win32service ^
    --hidden-import apps.actualizador.main ^
    apps\instalador\main.py

echo.
echo === 8/11 Actualizador ===
REM Pone al dia una instalacion que ya funciona, sin tocar la base ni el
REM config.ini. Busca las apps nuevas al lado suyo, igual que el instalador.
REM pos_core.servicio_windows se importa adentro de la revision final. Se
REM declara a mano por la misma razon que instancia_unica: si el analisis
REM automatico no lo agarra, el .exe compila igual y falla recien en el
REM local, con el negocio esperando.
REM Para la API del celular usa ademas pos_core.config (leer [api_celular]),
REM win32service (estado con PID y reintentos del servicio) y win32com.client
REM (el acceso directo "Otter - PIN del celular"), todos adentro de funciones.
%PYI% %DATA% %ICON% --name OtterActualizador --paths . ^
    --hidden-import pos_core.servicio_windows ^
    --hidden-import pos_core.config --hidden-import win32service --hidden-import win32com.client ^
    apps\actualizador\main.py

echo.
echo === 9/11 Blindaje de la PC del local ===
REM Un boton que deja la PC del local a prueba de las caidas conocidas.
REM Se le meten adentro los .ps1 de scripts\ (watchdog_celular.ps1 viaja
REM solo, al lado de blindar_local.ps1): la app NO reimplementa esos
REM comandos, los EJECUTA. Duplicar la logica seria garantizar que dentro
REM de tres meses una mitad este arreglada y la otra no.
%PYI% %DATA% %ICON% --name OtterBlindaje --paths . ^
    --add-data "scripts;scripts" ^
    apps\blindaje\main.py

echo.
echo === 10/11 Servicio oculto de stock (Windows Service) ===
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
echo === 11/11 API del celular (servicio, OPCIONAL) ===
REM Compila y corre la autoprueba del .exe (pedidos HTTP reales contra una
REM base temporal en la carpeta TEMP, nunca en dist\): agarra un
REM --hidden-import que falte ANTES de llegar al local. Si falla, ApiCelular
REM NO se lleva al local; lo demas sigue.
REM CELULAR_OK decide mas abajo si va al espejo del USB de Mantenimiento: un
REM ApiCelular que no paso la autoprueba no puede terminar "reparando" una PC.
set "CELULAR_OK=0"
call build\compilar_api_celular.bat
if errorlevel 1 (
    echo ATENCION: ApiCelular NO compilo o NO paso la autoprueba: NO lo lleves al local. Lo demas sigue.
) else (
    set "CELULAR_OK=1"
    echo ApiCelular: autoprueba OK
)

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
REM Opcional: solo si compilo Y paso la autoprueba. El Mantenimiento la repara
REM solo en las PCs que ya la tienen instalada (nunca la instala).
if "%CELULAR_OK%"=="1" if exist dist\ApiCelular xcopy /E /I /Y dist\ApiCelular dist\USB_Mantenimiento\espejo_apps\ApiCelular >nul

REM El espejo tiene que llevar SOLO programa, nunca datos. Al probar los
REM .exe desde dist\, cada app se crea ahi mismo su config.ini, su
REM database\, sus logs y sus tickets de prueba, y el xcopy de arriba se
REM los lleva puestos: el USB de Mantenimiento terminaria copiandolos
REM sobre la instalacion del cliente. Se limpian del espejo (no de dist\,
REM que queda como esta para seguir probando).
for %%A in (MaestroCaja MaestroDueno USB_Caja USB_Dueno StockService OtterBlindaje ApiCelular) do (
    if exist dist\USB_Mantenimiento\espejo_apps\%%A\config.ini del /Q dist\USB_Mantenimiento\espejo_apps\%%A\config.ini
    if exist dist\USB_Mantenimiento\espejo_apps\%%A\database rmdir /S /Q dist\USB_Mantenimiento\espejo_apps\%%A\database
    if exist dist\USB_Mantenimiento\espejo_apps\%%A\logs rmdir /S /Q dist\USB_Mantenimiento\espejo_apps\%%A\logs
    if exist dist\USB_Mantenimiento\espejo_apps\%%A\tickets rmdir /S /Q dist\USB_Mantenimiento\espejo_apps\%%A\tickets
    if exist dist\USB_Mantenimiento\espejo_apps\%%A\SYNC_DATA rmdir /S /Q dist\USB_Mantenimiento\espejo_apps\%%A\SYNC_DATA
    if exist dist\USB_Mantenimiento\espejo_apps\%%A\backups rmdir /S /Q dist\USB_Mantenimiento\espejo_apps\%%A\backups
)

echo.
echo Listo. Los ejecutables quedan en dist\NombreApp\NombreApp.exe
echo Copia cada carpeta dist\MaestroCaja, dist\MaestroDueno, etc. a su
echo destino final (disco de la PC fija o raiz del USB correspondiente).
echo El USB de Mantenimiento (dist\USB_Mantenimiento) ya sale con su
echo espejo_apps\ incluido: al conectarlo, "REPARAR TODO AUTOMATICAMENTE"
echo puede reponer archivos danados o desactualizados en cualquier
echo instalacion usando esta misma copia recien compilada.
echo dist\ApiCelular (opcional) lo instala el Actualizador con la casilla de
echo la API del celular: llevalo solo si arriba dijo "ApiCelular: autoprueba OK".
echo dist\DuenoRemoto se instala en la PC del dueño, en otra ubicación,
echo conectada a la PC del local por Tailscale (ver README, seccion de
echo Dueño Remoto, para habilitar [remoto] en el config.ini del Maestro).
