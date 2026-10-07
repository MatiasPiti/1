@echo off
REM Compila SOLO ApiCelular (servicio de Windows de la app del celular) y la prueba arrancando.
REM Lo llaman build_all.bat y el CI: un solo comando, sin deriva entre los dos.
REM Se corre desde la raiz del repo: build\compilar_api_celular.bat
REM Sin --windowed/--noconsole A PROPOSITO (igual que StockService): sin consola, "install" falla
REM sin mostrar nada y "definir-pin" no puede pedir el PIN.
REM Los --hidden-import son todo lo que se importa ADENTRO de una funcion (pywin32, uvicorn,
REM multipart, pdfminer, los modulos propios): si el analisis automatico no los agarra, el .exe
REM compila igual y falla recien en el local. La autoprueba del final los fuerza a todos.
python -m PyInstaller --noconfirm --clean --onedir ^
    --add-data "sql\schema.sql;sql" --icon apps\assets\logo_otter.ico ^
    --name ApiCelular --paths . ^
    --hidden-import win32timezone --hidden-import servicemanager --hidden-import win32serviceutil ^
    --hidden-import win32service --hidden-import win32event --hidden-import win32process --hidden-import win32api ^
    --collect-submodules uvicorn --collect-submodules anyio --hidden-import python_multipart ^
    --collect-data pdfminer ^
    --hidden-import pos_core.servicio_windows --hidden-import pos_core.acceso_celular ^
    --hidden-import pos_core.panel_celular --hidden-import pos_core.telegram_bot ^
    --hidden-import services.api_celular --hidden-import services.api_celular_cli ^
    --hidden-import services.api_celular_autoprueba ^
    --exclude-module tkinter --exclude-module matplotlib --exclude-module numpy ^
    --exclude-module pandas --exclude-module scipy ^
    services\api_celular_servicio.py
if errorlevel 1 exit /b 1
REM Sin git, version.txt queda vacio y /api/salud dice "compilado": "desconocido".
git rev-parse --short HEAD > dist\ApiCelular\version.txt 2>nul
REM La autoprueba arma su base en %TEMP%, NUNCA en dist\: no deja una database\ de prueba
REM en el build (regla 3: dist\database no viaja nunca al cliente).
dist\ApiCelular\ApiCelular.exe autoprueba
if errorlevel 1 exit /b 2
exit /b 0
