# Blinda la PC del local para que el Dueno Remoto no se caiga solo.
#
# Se corre UNA vez, como administrador, en la PC del local.
#
# ANTES de cambiar nada, guarda el estado actual en un archivo: es la
# ultima oportunidad de saber POR QUE se paro el servicio la segunda vez
# (nunca se confirmo). Una vez que esto corre, el StartupType queda en
# Automatic y esa pista se pierde para siempre.
#
# Cubre las tres formas en que se cortaba la conexion sin que nadie
# tocara nada:
#
#   1. El StockService quedaba "Stopped" (despues de una actualizacion, o
#      porque alguien lo paro para recompilar) y nadie lo relanzaba.
#   2. La PC se suspendia sola. Encendida pero dormida es, para la red,
#      lo mismo que apagada.
#   3. Tailscale en Windows se desconecta cuando se cierra o bloquea la
#      sesion del usuario, salvo que este en modo "unattended".
#
# No toca la base de datos ni el config.ini.

$ErrorActionPreference = "Continue"
$resultados = @()

function Anotar($paso, $ok, $detalle) {
    $script:resultados += [PSCustomObject]@{ Paso = $paso; OK = $ok; Detalle = $detalle }
}

# ===================================================================== #
Write-Host "== 0/5  Guardando el estado ACTUAL (antes de tocar nada) ==" -ForegroundColor Cyan
# ===================================================================== #
$carpeta = "C:\SistemaDual\watchdog"
New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
$antes = "$carpeta\estado_antes_del_blindaje.txt"

"=== Estado antes del blindaje: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $antes
"--- sc.exe qc (aca se ve si el arranque estaba en Manual, que explicaria" | Out-File $antes -Append
"    que el servicio no volviera despues de un reinicio) ---" | Out-File $antes -Append
(sc.exe qc SistemaDualStockService 2>&1) | Out-File $antes -Append
"`n--- Estado del servicio ---" | Out-File $antes -Append
(Get-Service SistemaDualStockService -ErrorAction SilentlyContinue | Format-List * | Out-String) | Out-File $antes -Append
"`n--- Ultimas 60 lineas del log del servicio ---" | Out-File $antes -Append
(Get-Content C:\SistemaDual\logs\stock_daemon.log -Tail 60 -ErrorAction SilentlyContinue) | Out-File $antes -Append
"`n--- Suspensiones y despertares (Kernel-Power: 42 = se durmio, 107 = desperto) ---" | Out-File $antes -Append
(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'} -MaxEvents 30 -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id | Format-Table -AutoSize | Out-String) | Out-File $antes -Append
"`n--- Estados de suspension que soporta esta PC ---" | Out-File $antes -Append
(powercfg /a 2>&1 | Out-String) | Out-File $antes -Append
"`n--- Que la desperto la ultima vez ---" | Out-File $antes -Append
(powercfg /lastwake 2>&1 | Out-String) | Out-File $antes -Append
"`n--- Errores del sistema en los ultimos 7 dias (apagones, cuelgues) ---" | Out-File $antes -Append
(Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 40 -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, ProviderName, Message | Format-List | Out-String) | Out-File $antes -Append
Write-Host "Guardado en $antes  <-- MANDASELO A CLAUDE, dice por que se cayo."
Anotar "Estado previo guardado" $true $antes

# ===================================================================== #
Write-Host "`n== 1/5  El servicio arranca con Windows y se reintenta solo ==" -ForegroundColor Cyan
# ===================================================================== #
Set-Service SistemaDualStockService -StartupType Automatic
# Si el servicio se CAE, Windows lo reintenta 3 veces (al minuto cada una).
# Esto no cubre que quede parado a proposito: de eso se ocupa el watchdog.
sc.exe failure SistemaDualStockService reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
$svc = Get-Service SistemaDualStockService -ErrorAction SilentlyContinue
Anotar "Servicio en Automatic" ($svc -and $svc.StartType -eq "Automatic") "StartType=$($svc.StartType)"

# ===================================================================== #
Write-Host "`n== 2/5  La PC no se duerme, y la placa de red tampoco ==" -ForegroundColor Cyan
# ===================================================================== #
# 'powercfg /change' toca SOLO el plan de energia ACTIVO. Si Windows
# cambia de plan -una actualizacion, el software del fabricante, o alguien
# que toca el icono de la bateria- los tiempos vuelven y la PC se duerme
# igual. Paso de verdad en El Galpon: se corrio este script, quedo bien, y
# la PC se siguio suspendiendo. Por eso ahora se recorren TODOS los planes
# uno por uno, no solo el que este activo hoy.
$planes = @()
try {
    $planes = (powercfg /L) |
        Select-String -Pattern '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})' -AllMatches |
        ForEach-Object { $_.Matches.Value } | Select-Object -Unique
} catch { }

foreach ($plan in $planes) {
    # STANDBYIDLE = suspender; HIBERNATEIDLE = hibernar. AC = enchufado,
    # DC = a bateria (una notebook sin esto se duerme apenas se corta la
    # luz, que es justo cuando mas importa que siga viva).
    powercfg /setacvalueindex $plan SUB_SLEEP STANDBYIDLE 0    2>&1 | Out-Null
    powercfg /setdcvalueindex $plan SUB_SLEEP STANDBYIDLE 0    2>&1 | Out-Null
    powercfg /setacvalueindex $plan SUB_SLEEP HIBERNATEIDLE 0  2>&1 | Out-Null
    powercfg /setdcvalueindex $plan SUB_SLEEP HIBERNATEIDLE 0  2>&1 | Out-Null
}
foreach ($plan in $planes) {
    # Los BOTONES y la TAPA. Windows suele traer el boton de encendido en
    # "Suspender": el cajero lo aprieta para "apagar" al cerrar el negocio,
    # la PC se duerme, y para la red es lo mismo que apagada. En notebook,
    # cerrar la tapa hace exactamente lo mismo.
    #   0 = no hacer nada | 1 = suspender | 2 = hibernar | 3 = apagar
    # El boton de encendido queda en APAGAR y no en "nada": tiene que
    # seguir sirviendo para apagar de verdad cuando alguien quiera.
    powercfg /setacvalueindex $plan SUB_BUTTONS PBUTTONACTION 3 2>&1 | Out-Null
    powercfg /setdcvalueindex $plan SUB_BUTTONS PBUTTONACTION 3 2>&1 | Out-Null
    powercfg /setacvalueindex $plan SUB_BUTTONS SBUTTONACTION 0 2>&1 | Out-Null
    powercfg /setdcvalueindex $plan SUB_BUTTONS SBUTTONACTION 0 2>&1 | Out-Null
    powercfg /setacvalueindex $plan SUB_BUTTONS LIDACTION 0     2>&1 | Out-Null
    powercfg /setdcvalueindex $plan SUB_BUTTONS LIDACTION 0     2>&1 | Out-Null
}

# La pantalla SI se apaga (10 min): no tiene nada que ver con la red y
# ahorra el monitor. Que la pantalla este negra NO es que la PC duerma.
powercfg /change monitor-timeout-ac 10
powercfg /change monitor-timeout-dc 10
# Sin hibernacion: es la otra forma en que la PC desaparece de la red.
powercfg /hibernate off 2>&1 | Out-Null
powercfg /setactive SCHEME_CURRENT 2>&1 | Out-Null

# Se verifica leyendo la config de vuelta, no se da por hecho.
#
# Se buscan las lineas con un valor hexadecimal y NO la palabra "Index":
# powercfg habla el idioma de Windows, y en la PC del local (Windows en
# espanol) dice "Indice de configuracion actual de CA". Buscar "Index"
# daba SIEMPRE cero coincidencias y la fila salia NO aunque los cambios
# hubieran entrado perfecto. Un chequeo que depende del idioma no es un
# chequeo: es un susto garantizado.
$sinSuspension = $false
try {
    $valores = @(powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE |
                 Select-String -Pattern '0x[0-9a-fA-F]{8}' -AllMatches |
                 ForEach-Object { $_.Matches } | ForEach-Object { $_.Value })
    # Los dos primeros hexadecimales de esa salida son el GUID del subgrupo
    # y el del ajuste; los que interesan son los indices de CA y CC, que
    # son los ultimos dos.
    $indices = @($valores | Select-Object -Last 2)
    $sinSuspension = ($indices.Count -eq 2) -and
                     -not ($indices | Where-Object { $_ -ne '0x00000000' })
} catch { }
Anotar "PC sin suspension" $sinSuspension "$($planes.Count) plan(es): sin suspender, sin hibernar, boton y tapa no duermen"

# ---------------------------------------------------------------- #
# La placa de red se apaga sola "para ahorrar energia": la PC sigue
# despierta pero desaparecio de la red. Es exactamente el sintoma
# "esta prendida y no responde", y NO se ve en powercfg ni en la app de
# Tailscale del lado del local. Se apaga esa opcion en cada placa fisica.
$placas = 0
$vistas = 0
$seEnumero = $false
try {
    $adaptadores = @(Get-NetAdapter -Physical -ErrorAction Stop | Where-Object { $_.Status -ne 'Not Present' })
    $seEnumero = $true
    if ($adaptadores.Count -gt 0) {
        Write-Host "Ajustando $($adaptadores.Count) placa(s) de red. La red se corta un instante." -ForegroundColor Yellow
    }
    foreach ($ad in $adaptadores) {
        $vistas++
        try {
            Disable-NetAdapterPowerManagement -Name $ad.Name -ErrorAction Stop -Confirm:$false
            $placas++
        } catch {
            # Hay placas que directamente no exponen esa opcion. No es un
            # problema: si no la exponen, tampoco se apagan solas.
        }
    }
} catch {
    # Sin el modulo NetAdapter (Windows viejo) no se puede hacer desde aca.
}
$detalleRed = if (-not $seEnumero) { "no se pudieron leer las placas de red" }
              elseif ($vistas -eq 0) { "no hay placas fisicas activas" }
              else { "$placas de $vistas ajustada(s); el resto no expone la opcion" }
Anotar "Placa de red siempre despierta" $seEnumero $detalleRed

# ===================================================================== #
Write-Host "`n== 3/5  Watchdog cada 5 minutos ==" -ForegroundColor Cyan
# ===================================================================== #
# OJO con lo que vigila: NO alcanza con que el servicio diga "Running".
# La API remota se levanta ADENTRO del servicio y su arranque es "best
# effort": si falla (puerto ocupado, config mal), el codigo la loguea y
# el servicio sigue corriendo igual. En ese estado, para Leo, el sistema
# esta caido y el servicio dice que esta bien. Por eso el watchdog mira
# EL PUERTO, que es lo que de verdad le importa al Dueno Remoto.

$vigilante = @'
$log    = "C:\SistemaDual\watchdog\watchdog.log"
$marcas = "C:\SistemaDual\watchdog\reinicios.txt"
$hora   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

function Escribir($texto) {
    # Rotacion simple: este archivo lo escribe una tarea que corre cada 5
    # minutos, para siempre, y nadie lo borra nunca. Un disco lleno es una
    # de las formas de corromper la base, o sea justo lo que hay que evitar.
    try {
        if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) {
            Move-Item $log "$log.viejo" -Force
        }
    } catch { }
    Add-Content $log "$hora  $texto"
}

function PuertoVivo {
    try {
        $cliente = New-Object Net.Sockets.TcpClient
        $conecta = $cliente.ConnectAsync("127.0.0.1", 8765)
        $ok = $conecta.Wait(2000) -and $cliente.Connected
        $cliente.Close()
        return $ok
    } catch { return $false }
}

# Aviso temprano del asesino silencioso: con el disco lleno, SQLite puede
# corromper la base al escribir.
try {
    $libres = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
    if ($libres -lt 1) { Escribir "ATENCION: quedan $libres GB libres en C:. Con el disco lleno la base se puede corromper." }
} catch { }

$svc = Get-Service SistemaDualStockService -ErrorAction SilentlyContinue
if ($null -eq $svc) { Escribir "el servicio no esta instalado en esta PC"; exit }

if ($svc.Status -ne "Running") {
    Escribir "servicio $($svc.Status): arrancandolo"
    try {
        Start-Service SistemaDualStockService
        Start-Sleep -Seconds 8
        Escribir "quedo $((Get-Service SistemaDualStockService).Status), puerto vivo=$(PuertoVivo)"
    } catch { Escribir "ERROR al arrancarlo: $_" }
    exit
}

# Servicio Running pero puerto muerto: el caso silencioso.
if (-not (PuertoVivo)) {
    # Si la API remota no esta habilitada en el config, el puerto NUNCA va
    # a escuchar: reiniciar cada 5 minutos para siempre seria peor que el
    # problema. Se avisa una vez por dia y se sale.
    $habilitado = $false
    try {
        $seccion = $false
        foreach ($linea in Get-Content C:\SistemaDual\config.ini) {
            if ($linea -match '^\s*\[(.+)\]') { $seccion = ($matches[1] -eq 'remoto') }
            elseif ($seccion -and $linea -match '^\s*habilitado\s*=\s*(\S+)') {
                $habilitado = ($matches[1] -match '^(true|1|si|s\u00ed)$')
            }
        }
    } catch { }

    if (-not $habilitado) {
        $avisoHoy = "C:\SistemaDual\watchdog\aviso_deshabilitado_$(Get-Date -Format yyyyMMdd).txt"
        if (-not (Test-Path $avisoHoy)) {
            Escribir "el puerto 8765 no escucha y [remoto] habilitado NO es true: no se reinicia, hay que corregir el config.ini"
            "avisado" | Out-File $avisoHoy
        }
        exit
    }

    # Freno de mano: si ya hubo 3 reinicios por esta causa en 24 hs, el
    # problema no se arregla reiniciando. Mejor dejar de dar vueltas y que
    # quede escrito, que entrar en un ciclo de reinicios.
    $recientes = @()
    if (Test-Path $marcas) {
        $recientes = Get-Content $marcas | Where-Object {
            try { [datetime]$_ -gt (Get-Date).AddHours(-24) } catch { $false }
        }
    }
    if ($recientes.Count -ge 3) {
        Escribir "el puerto sigue muerto tras $($recientes.Count) reinicios en 24 hs: NO se reinicia mas, requiere revision manual"
        exit
    }

    Escribir "servicio Running pero el puerto 8765 NO escucha (la API remota no levanto): reiniciando"
    try {
        Restart-Service SistemaDualStockService -Force
        Start-Sleep -Seconds 10
        $vivo = PuertoVivo
        Escribir "tras reiniciar, puerto vivo=$vivo"
        ($recientes + (Get-Date).ToString("o")) | Set-Content $marcas
    } catch { Escribir "ERROR al reiniciar: $_" }
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
# Corre como SYSTEM: asi funciona con la sesion cerrada, que es justo
# cuando hace falta.
Register-ScheduledTask -TaskName "OtterWatchdog" -Action $accion -Trigger $disparo `
    -Settings $opciones -User "SYSTEM" -RunLevel Highest -Force | Out-Null
$tarea = Get-ScheduledTask -TaskName OtterWatchdog -ErrorAction SilentlyContinue
Anotar "Watchdog instalado" ($null -ne $tarea) "Estado=$($tarea.State)"

# ===================================================================== #
Write-Host "`n== 4/5  Tailscale conectado aunque nadie inicie sesion ==" -ForegroundColor Cyan
# ===================================================================== #
$ts = "C:\Program Files\Tailscale\tailscale.exe"
$tsOk = $false
if (Test-Path $ts) {
    # 'tailscale set' cambia UNA preferencia sin tocar el resto. Con
    # 'tailscale up --unattended' Tailscale suele responder que hay que
    # repetir TODOS los flags no-default, y el comando falla sin aplicar
    # nada: por eso 'set' primero y 'up' solo como respaldo.
    $salida = (& $ts set --unattended=true 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) {
        $tsOk = $true
        Write-Host "Tailscale en modo unattended (via 'tailscale set')."
    } else {
        Write-Host "'tailscale set' no funciono ($salida). Probando con 'up'..." -ForegroundColor Yellow
        $salida2 = (& $ts up --unattended 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) { $tsOk = $true } else {
            Write-Host "Tampoco: $salida2" -ForegroundColor Yellow
        }
    }
}
if (-not $tsOk) {
    Write-Host "ACTIVALO A MANO: icono de Tailscale junto al reloj ->" -ForegroundColor Yellow
    Write-Host "Preferences -> Run unattended. Es el punto mas importante" -ForegroundColor Yellow
    Write-Host "de todos: sin esto, al cerrar sesion la PC desaparece de la red." -ForegroundColor Yellow
}
Anotar "Tailscale unattended" $tsOk $(if ($tsOk) { "aplicado por CLI" } else { "HAY QUE HACERLO A MANO desde el icono" })

# ===================================================================== #
Write-Host "`n== 5/5  Verificacion ==" -ForegroundColor Cyan
# ===================================================================== #
if ((Get-Service SistemaDualStockService -ErrorAction SilentlyContinue).Status -ne "Running") {
    Write-Host "El servicio no estaba corriendo: arrancandolo." -ForegroundColor Yellow
    Start-Service SistemaDualStockService -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 8
}
$svcFinal = Get-Service SistemaDualStockService -ErrorAction SilentlyContinue
Anotar "Servicio corriendo" ($svcFinal.Status -eq "Running") "Status=$($svcFinal.Status)"

$puerto = $false
try {
    $c = New-Object Net.Sockets.TcpClient
    $puerto = $c.ConnectAsync("127.0.0.1", 8765).Wait(3000) -and $c.Connected
    $c.Close()
} catch { }
Anotar "Puerto 8765 escuchando" $puerto $(if ($puerto) { "responde" } else { "NO responde: revisar [remoto] habilitado en config.ini y el log del servicio" })

Write-Host ""
$resultados | Format-Table @{L='OK';E={ if ($_.OK) { "SI" } else { "NO" } }}, Paso, Detalle -AutoSize

$fallaron = @($resultados | Where-Object { -not $_.OK })
if ($fallaron.Count -eq 0) {
    Write-Host "TODO OK. Ahora la prueba de verdad: cerra sesion (NO apagar) y" -ForegroundColor Green
    Write-Host "fijate desde el celular si http://<ip>:8765/health sigue contestando." -ForegroundColor Green
} else {
    Write-Host "QUEDARON $($fallaron.Count) COSA(S) SIN RESOLVER (mira la columna OK)." -ForegroundColor Yellow
    Write-Host "Mandale a Claude esta salida y el archivo $antes" -ForegroundColor Yellow
}
