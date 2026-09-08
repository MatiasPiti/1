# Blinda la PC del local para que el Dueno Remoto no se caiga solo.
#
# Se corre UNA vez, como administrador, en la PC del local. Cubre las tres
# formas en que se cortaba la conexion sin que nadie tocara nada:
#
#   1. El StockService quedaba "Stopped" (despues de una actualizacion, o
#      porque alguien lo paro para recompilar) y nadie lo relanzaba. Con el
#      servicio caido no hay API remota: el Dueno Remoto dice "no se pudo
#      conectar" aunque Tailscale, IP y token esten perfectos.
#   2. La PC se suspendia sola por inactividad. Encendida pero dormida es,
#      para la red, lo mismo que apagada.
#   3. Tailscale en Windows se desconecta cuando se cierra o bloquea la
#      sesion del usuario, salvo que este en modo "unattended".
#
# No toca la base de datos ni el config.ini.

Write-Host "== 1/4  El servicio arranca con Windows y se reintenta solo ==" -ForegroundColor Cyan
Set-Service SistemaDualStockService -StartupType Automatic
# Si el servicio se cae, Windows lo reintenta 3 veces (al minuto cada una)
# antes de darse por vencido; el contador se reinicia cada 24 hs.
sc.exe failure SistemaDualStockService reset= 86400 actions= restart/60000/restart/60000/restart/60000

Write-Host "`n== 2/4  La PC no se duerme ==" -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0     # nunca suspender
powercfg /change hibernate-timeout-ac 0   # nunca hibernar
powercfg /change monitor-timeout-ac 10    # la pantalla si se apaga, a los 10 min

Write-Host "`n== 3/4  Watchdog cada 5 minutos ==" -ForegroundColor Cyan
# El reintento de Windows del paso 1 solo actua si el servicio se CAE. No
# cubre el caso de que quede parado a proposito (una actualizacion) y nadie
# lo vuelva a arrancar: para eso esta este watchdog, que ademas deja
# registro de cada vez que tuvo que intervenir.
$carpeta = "C:\SistemaDual\watchdog"
New-Item -ItemType Directory -Force -Path $carpeta | Out-Null

$vigilante = @'
$log = "C:\SistemaDual\watchdog\watchdog.log"
$hora = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$svc = Get-Service SistemaDualStockService -ErrorAction SilentlyContinue
if ($null -eq $svc) {
    Add-Content $log "$hora  el servicio no esta instalado en esta PC"
    exit
}
if ($svc.Status -ne "Running") {
    Add-Content $log "$hora  estaba $($svc.Status): arrancandolo"
    try {
        Start-Service SistemaDualStockService
        Start-Sleep -Seconds 5
        $estado = (Get-Service SistemaDualStockService).Status
        Add-Content $log "$hora  quedo $estado"
    } catch {
        Add-Content $log "$hora  ERROR al arrancarlo: $_"
    }
}
'@
Set-Content -Path "$carpeta\watchdog.ps1" -Value $vigilante -Encoding UTF8

$accion = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$carpeta\watchdog.ps1`""
# -RepetitionDuration con un plazo largo y no infinito: [TimeSpan]::MaxValue
# hace fallar el registro de la tarea en varias versiones de Windows.
$disparo = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
# Corre como SYSTEM: asi funciona con la sesion cerrada, que es justo cuando
# hace falta.
Register-ScheduledTask -TaskName "OtterWatchdog" -Action $accion -Trigger $disparo `
    -Settings $opciones -User "SYSTEM" -RunLevel Highest -Force | Out-Null
Write-Host "Tarea 'OtterWatchdog' registrada (cada 5 minutos, como SYSTEM)."

Write-Host "`n== 4/4  Tailscale conectado aunque nadie inicie sesion ==" -ForegroundColor Cyan
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $ts) {
    & $ts up --unattended
    Write-Host "Tailscale puesto en modo unattended."
} else {
    Write-Host "No se encontro $ts - activalo a mano desde el icono de" -ForegroundColor Yellow
    Write-Host "Tailscale junto al reloj: Preferences -> Run unattended." -ForegroundColor Yellow
}

Write-Host "`n== Verificacion ==" -ForegroundColor Green
Get-Service SistemaDualStockService | Format-Table Name, Status, StartType
Get-ScheduledTask -TaskName OtterWatchdog | Format-Table TaskName, State
Write-Host "Escuchando en el 8765:"
netstat -ano | findstr 8765
Write-Host "`nSi la linea del 8765 aparece con LISTENING y el servicio dice"
Write-Host "Running/Automatic, quedo todo blindado."
