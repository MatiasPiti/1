# Watchdog de la API del celular (tarea OtterWatchdogCelular, cada 5 min, SYSTEM, tope 4 min).
# Lo copia scripts\blindar_local.ps1 a C:\SistemaDual\watchdog\. Solo ASCII (PowerShell 5.1
# lee en ANSI un .ps1 sin BOM).
# NUNCA nombra al servicio de stock: si este script se cuelga o falla, el watchdog del 8765 sigue solo.
# Todas las salidas son "exit 0": LastTaskResult distinto de 0 queda solo para fallas de verdad.
$base     = "C:\SistemaDual"
$log      = "$base\watchdog\watchdog_celular.log"
$marcas   = "$base\watchdog\reinicios_celular.txt"
$hora     = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$servicio = "SistemaDualApiCelular"

function Escribir($texto) {
    try { if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Move-Item $log "$log.viejo" -Force } } catch { }
    try { Add-Content $log "$hora  $texto" } catch { }
}
function AvisarUnaVezPorDia($clave, $texto) {
    $marca = "$base\watchdog\aviso_celular_${clave}_$(Get-Date -Format yyyyMMdd).txt"
    if (-not (Test-Path $marca)) {
        Escribir $texto; try { "avisado" | Out-File $marca } catch { }
        # Las marcas de dias anteriores no sirven para nada: que no se junten para siempre.
        try { Get-ChildItem "$base\watchdog\aviso_celular_*.txt" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } | Remove-Item -Force } catch { }
    }
}
function PuertoValido($texto, $defecto) {
    if ($texto -cmatch '^[0-9]{1,5}$' -and [int]$texto -ge 1 -and [int]$texto -le 65535) { return [int]$texto }
    return $defecto
}
function LeerConfigCelular($ruta) {
    # MISMA regla que pos_core/config.py::leer_config_celular: primer token del valor,
    # clave sin distinguir mayusculas, seccion con mayusculas exactas (como configparser).
    $r = @{ habilitado = $false; puerto = 8766; puerto_remoto = 8765 }
    $sec = ""
    foreach ($linea in (Get-Content $ruta -Encoding UTF8 -ErrorAction Stop)) {
        if ($linea -match '^\s*\[(.+)\]') { $sec = $matches[1]; continue }
        if ($linea -notmatch '^\s*([A-Za-z_]+)\s*[=:]\s*(\S+)') { continue }
        $clave = $matches[1].ToLower(); $valor = $matches[2]
        if ($sec -ceq 'api_celular' -and $clave -eq 'habilitado') { $r.habilitado = ($valor -match '^(true|1|si|s\u00ed|yes|on)$') }
        elseif ($sec -ceq 'api_celular' -and $clave -eq 'puerto') { $r.puerto = PuertoValido $valor 8766 }
        elseif ($sec -ceq 'remoto' -and $clave -eq 'puerto') { $r.puerto_remoto = PuertoValido $valor 8765 }
    }
    return $r
}
function CelularContesta($puerto) {
    # WebRequest a mano: sin proxy y sin el motor de IE (Invoke-WebRequest sin -UseBasicParsing falla como SYSTEM).
    try {
        $req = [Net.WebRequest]::Create("http://127.0.0.1:$puerto/api/salud"); $req.Proxy = $null; $req.Timeout = 5000
        $resp = $req.GetResponse(); $txt = (New-Object IO.StreamReader($resp.GetResponseStream())).ReadToEnd(); $resp.Close()
        return (($txt | ConvertFrom-Json).servicio -eq 'otter-api-celular')
    } catch { return $false }
}
function QuienEscucha($puerto) {
    try {
        $c = Get-NetTCPConnection -LocalPort $puerto -State Listen -ErrorAction Stop | Select-Object -First 1
        $p = Get-Process -Id $c.OwningProcess -ErrorAction Stop
        return "$($p.ProcessName) (PID $($p.Id))"
    } catch { return "" }
}
function EstadoServicio {
    try { return (Get-CimInstance Win32_Service -Filter "Name='$servicio'" -ErrorAction Stop) } catch { return $null }
}
function EsperarEstado($estado, $segundos) {
    $limite = (Get-Date).AddSeconds($segundos)
    do { Start-Sleep -Seconds 1; $s = EstadoServicio } while ($null -ne $s -and $s.State -ne $estado -and (Get-Date) -lt $limite)
    return $s
}
function ReiniciarCelular($puerto) {
    # Sin Restart-Service: espera SIN LIMITE a que el servicio pare, y una API trabada en
    # "Stop Pending" dejaria esta tarea colgada.
    & sc.exe stop $servicio | Out-Null
    $s = EsperarEstado 'Stopped' 30
    if ($null -ne $s -and $s.State -ne 'Stopped' -and $s.ProcessId -gt 0) {
        Escribir "no paro en 30 s: se cierra a la fuerza el proceso $($s.ProcessId)"
        Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
        $s = EsperarEstado 'Stopped' 15
    }
    & sc.exe start $servicio | Out-Null
    Start-Sleep -Seconds 15
    return (CelularContesta $puerto)
}
function Frenado {
    # @() SIEMPRE: sin eso, con una sola marca Get-Content devuelve un texto suelto,
    # "texto + fecha" los pega en una linea ilegible y la cuenta vuelve a 0 (verificado, c7).
    $rec = @()
    if (Test-Path $marcas) { $rec = @(Get-Content $marcas | Where-Object { try { [datetime]$_ -gt (Get-Date).AddHours(-24) } catch { $false } }) }
    return ,$rec
}

try {
    $svc = EstadoServicio
    if ($null -eq $svc) { exit 0 }                     # no esta instalada: nada que vigilar
    if ($svc.StartMode -eq 'Disabled') {
        AvisarUnaVezPorDia "deshabilitada" "la API del celular esta Deshabilitada en Windows: no se toca (para apagarla se usa [api_celular] habilitado = false)"
        exit 0
    }
    try { $cfg = LeerConfigCelular "$base\config.ini" } catch {
        AvisarUnaVezPorDia "config" "no se pudo leer config.ini: no se vigila la API del celular"; exit 0
    }
    if (-not $cfg.habilitado) { exit 0 }               # apagada a proposito
    if ($cfg.puerto -eq $cfg.puerto_remoto) {
        AvisarUnaVezPorDia "puerto" "[api_celular] puerto = [remoto] puerto ($($cfg.puerto)): la API no escucha a proposito"; exit 0
    }
    if ($svc.State -eq 'Stopped') {
        Escribir "API del celular Stopped: arrancandola"
        & sc.exe start $servicio | Out-Null
        Start-Sleep -Seconds 15
        Escribir "quedo $((EstadoServicio).State), contesta=$(CelularContesta $cfg.puerto)"
        exit 0
    }
    if ($svc.State -ne 'Running') {
        # Start Pending / Stop Pending / Paused: se le dan 30 s para asentarse antes de tocar nada.
        $svc = EsperarEstado 'Running' 30
        if ($null -eq $svc) { exit 0 }
    }
    if ($svc.State -eq 'Running' -and (CelularContesta $cfg.puerto)) { exit 0 }
    $quien = QuienEscucha $cfg.puerto
    if ($quien -and $quien -notmatch '^ApiCelular') {
        AvisarUnaVezPorDia "otro" "el $($cfg.puerto) lo tiene otro programa ($quien): NO se reinicia la API del celular"; exit 0
    }
    $rec = Frenado
    if ($rec.Count -ge 3) {
        AvisarUnaVezPorDia "freno" "la API del celular sigue sin contestar tras $($rec.Count) reinicios en 24 hs: NO se reinicia mas"; exit 0
    }
    Escribir "API del celular $($svc.State) y no contesta en $($cfg.puerto): reiniciandola"
    (@($rec) + (Get-Date).ToString("o")) | Set-Content $marcas
    $vivo = ReiniciarCelular $cfg.puerto
    Escribir "tras reiniciar, contesta=$vivo"
} catch { Escribir "ERROR vigilando la API del celular: $_" }
exit 0
