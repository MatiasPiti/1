# Deja la PC del local reparable A DISTANCIA, por SSH sobre Tailscale.
#
# Por que existe: hasta ahora, cualquier cosa que se rompiera alla obligaba
# a viajar. El servicio parado, un log que hay que leer, el blindaje que hay
# que correr de nuevo: todo pide estar sentado ahi. Con esto se resuelve
# desde casa, o desde el celular.
#
# Se usa el servidor OpenSSH que YA VIENE con Windows 10/11 (caracteristica
# opcional de Microsoft), no un programa de terceros: es la PC que cobra la
# plata del negocio, no se le instala cualquier cosa.
#
# Tres cuidados que no son opcionales:
#
#  1. El firewall se limita al rango de Tailscale (100.64.0.0/10). Igual que
#     la API remota: NUNCA expuesto a internet, solo por la VPN.
#  2. Se crea un usuario APARTE para soporte. Al usuario del cajero no se le
#     toca nada: si hoy entra sin contrasena, sigue entrando sin contrasena.
#     Ponerle una seria romper la regla 6 (a las 8 de la manana la caja abre).
#  3. La contrasena se pide por pantalla y no queda escrita en ningun lado.
#
# Se corre UNA vez, como administrador, en la PC del local.

# -Password: la contrasena del usuario de soporte, para cuando esto lo
# llama OtterBlindaje.exe (que no tiene consola donde tipearla). Corriendo
# el script a mano se deja vacio y la pide por pantalla, que es mas seguro:
# asi no queda en el historial de PowerShell.
param([string]$Password = "")

$ErrorActionPreference = "Continue"
$resultados = @()
function Anotar($paso, $ok, $detalle) {
    $script:resultados += [PSCustomObject]@{ Paso = $paso; OK = $ok; Detalle = $detalle }
}

$USUARIO = "otter_soporte"

# ===================================================================== #
Write-Host "== 1/4  Instalando el servidor SSH de Windows ==" -ForegroundColor Cyan
# ===================================================================== #
$instalado = $false
try {
    $cap = Get-WindowsCapability -Online -Name OpenSSH.Server* -ErrorAction Stop |
           Select-Object -First 1
    if ($cap.State -eq "Installed") {
        $instalado = $true
    } else {
        Write-Host "Descargando e instalando (puede tardar unos minutos)..."
        try {
            Add-WindowsCapability -Online -Name $cap.Name -ErrorAction Stop | Out-Null
            $instalado = $true
        } catch {
            # 0x8024001e y familia son errores de Windows Update: OpenSSH no
            # viene en el disco, se BAJA de ahi. En la PC del local el
            # servicio estaba deshabilitado -es comun en maquinas donde
            # alguien "apago las actualizaciones"- y la instalacion moria
            # sin que se entendiera por que. Se prende, se reintenta, y se
            # deja como estaba.
            Write-Host "Fallo por Windows Update ($($_.Exception.Message))." -ForegroundColor Yellow
            Write-Host "Prendiendo el servicio de Windows Update y reintentando..." -ForegroundColor Yellow
            $arranqueOriginal = (Get-Service wuauserv -ErrorAction SilentlyContinue).StartType
            try {
                if ($arranqueOriginal -eq "Disabled") {
                    Set-Service wuauserv -StartupType Manual -ErrorAction Stop
                }
                Start-Service wuauserv -ErrorAction Stop
                Start-Sleep -Seconds 3
                Add-WindowsCapability -Online -Name $cap.Name -ErrorAction Stop | Out-Null
                $instalado = $true
                Write-Host "Ahora si: OpenSSH instalado." -ForegroundColor Green
            } catch {
                Write-Host "Tampoco: $($_.Exception.Message)" -ForegroundColor Yellow
            } finally {
                # Se deja Windows Update como estaba: si el dueno lo tenia
                # deshabilitado a proposito, no somos quien para cambiarlo.
                if ($arranqueOriginal -eq "Disabled") {
                    Set-Service wuauserv -StartupType Disabled -ErrorAction SilentlyContinue
                }
            }
        }
    }
} catch {
    Write-Host "No se pudo consultar OpenSSH: $_" -ForegroundColor Yellow
}
if (-not $instalado) {
    Write-Host ""
    Write-Host "OpenSSH no quedo instalado. Dos caminos:" -ForegroundColor Yellow
    Write-Host "  1) Configuracion -> Aplicaciones -> Caracteristicas opcionales" -ForegroundColor Yellow
    Write-Host "     -> Agregar caracteristica -> 'Servidor de OpenSSH'" -ForegroundColor Yellow
    Write-Host "  2) Si esa PC no puede usar Windows Update, bajar el .msi oficial" -ForegroundColor Yellow
    Write-Host "     de github.com/PowerShell/Win32-OpenSSH/releases (es de Microsoft)" -ForegroundColor Yellow
    Write-Host "     y despues volver a correr este script." -ForegroundColor Yellow
}
Anotar "Servidor SSH instalado" $instalado ""

# ===================================================================== #
Write-Host "`n== 2/4  Que arranque solo con Windows ==" -ForegroundColor Cyan
# ===================================================================== #
# En Automatic y no Manual: es el mismo error que tuvo el StockService y que
# dejo a Leo sin conexion tres veces. Un soporte remoto que no levanta
# despues de un reinicio no sirve para nada.
$sshOk = $false
try {
    Set-Service -Name sshd -StartupType Automatic -ErrorAction Stop
    Start-Service sshd -ErrorAction Stop
    sc.exe failure sshd reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
    $sshOk = ((Get-Service sshd).Status -eq "Running")
} catch {
    Write-Host "No se pudo arrancar el servicio sshd: $_" -ForegroundColor Yellow
}
Anotar "SSH corriendo y en Automatic" $sshOk "Status=$((Get-Service sshd -ErrorAction SilentlyContinue).Status)"

# ===================================================================== #
Write-Host "`n== 3/4  Solo alcanzable por la VPN ==" -ForegroundColor Cyan
# ===================================================================== #
# 100.64.0.0/10 es el rango que usa Tailscale. Limitar la regla a ese rango
# es lo que hace que esto NO sea "abrir el puerto 22": desde internet no se
# llega, igual que la API remota (regla 1 del proyecto).
$firewallOk = $false
try {
    $regla = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
    if ($null -eq $regla) {
        New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 `
            -RemoteAddress 100.64.0.0/10 -ErrorAction Stop | Out-Null
    } else {
        Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -RemoteAddress 100.64.0.0/10 `
            -Enabled True -ErrorAction Stop
    }
    $r = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" | Get-NetFirewallAddressFilter
    $firewallOk = ($r.RemoteAddress -join ",") -match "100\.64\.0\.0"
} catch {
    Write-Host "No se pudo ajustar el firewall: $_" -ForegroundColor Yellow
}
Anotar "SSH solo desde Tailscale" $firewallOk "puerto 22 limitado a 100.64.0.0/10"

# ===================================================================== #
Write-Host "`n== 4/4  Usuario de soporte (NO se toca el del cajero) ==" -ForegroundColor Cyan
# ===================================================================== #
$usuarioOk = $false
try {
    if (Get-LocalUser -Name $USUARIO -ErrorAction SilentlyContinue) {
        Write-Host "El usuario $USUARIO ya existe; se deja como esta."
        $usuarioOk = $true
    } else {
        if ($Password) {
            $pass = ConvertTo-SecureString $Password -AsPlainText -Force
        } else {
            Write-Host "Elegi una contrasena para $USUARIO."
            Write-Host "SIN caracteres confundibles (nada de l I 1 O 0): la vas a tipear en el celular." -ForegroundColor Yellow
            $pass = Read-Host "Contrasena" -AsSecureString
        }
        New-LocalUser -Name $USUARIO -Password $pass -FullName "Soporte Otter" `
            -Description "Acceso remoto de mantenimiento por Tailscale" `
            -PasswordNeverExpires -AccountNeverExpires -ErrorAction Stop | Out-Null
        # Por SID y no por nombre: el grupo se llama "Administradores" o
        # "Administrators" segun el idioma de Windows, el SID es siempre igual.
        Add-LocalGroupMember -SID "S-1-5-32-544" -Member $USUARIO -ErrorAction Stop
        $usuarioOk = $true
    }
    # Que no aparezca en la pantalla de inicio de sesion: el cajero no tiene
    # por que verlo ni poder elegirlo por error.
    New-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList" `
        -Name $USUARIO -Value 0 -PropertyType DWord -Force -ErrorAction SilentlyContinue | Out-Null
} catch {
    Write-Host "No se pudo crear el usuario: $_" -ForegroundColor Yellow
}

# Se comprueba que el usuario EXISTA de verdad, en vez de confiar en que
# los comandos de arriba no tiraron error. La primera version marcaba OK
# apenas terminaba el bloque, y en la PC del local la tabla dijo SI con el
# usuario inexistente: Matias se entero recien al querer entrar por SSH.
# Una verificacion que no verifica es peor que ninguna, porque da por
# resuelto algo que no lo esta.
$existe = $false
try {
    $existe = $null -ne (Get-LocalUser -Name $USUARIO -ErrorAction SilentlyContinue)
    if (-not $existe) {
        # Get-LocalUser depende del modulo LocalAccounts, que no siempre
        # esta disponible. 'net user' lo dice igual y esta desde siempre.
        $existe = ((net user 2>&1) -join " ") -match [regex]::Escape($USUARIO)
    }
} catch { }

$detalleUsuario = if ($existe) { $USUARIO } else { "$USUARIO NO quedo creado" }
Anotar "Usuario de soporte" ($usuarioOk -and $existe) $detalleUsuario
if (-not $existe) {
    Write-Host ""
    Write-Host "El usuario $USUARIO no quedo creado. Crealo a mano asi:" -ForegroundColor Yellow
    Write-Host "    net user $USUARIO `"TuContrasena`" /add" -ForegroundColor Yellow
    Write-Host "    Add-LocalGroupMember -SID `"S-1-5-32-544`" -Member $USUARIO" -ForegroundColor Yellow
    Write-Host "(el SID es el grupo de administradores: su nombre cambia con el idioma)" -ForegroundColor Yellow
}

# ===================================================================== #
Write-Host "`n== Resultado ==" -ForegroundColor Cyan
# ===================================================================== #
$resultados | Format-Table @{L='OK';E={ if ($_.OK) { "SI" } else { "NO" } }}, Paso, Detalle -AutoSize

$ip = ""
try { $ip = (& "C:\Program Files\Tailscale\tailscale.exe" ip -4 2>$null | Select-Object -First 1).Trim() } catch { }
Write-Host ""
if ($ip) {
    Write-Host "Para entrar desde tu casa, tu celular o la RG:" -ForegroundColor Green
    Write-Host "    ssh $USUARIO@$ip" -ForegroundColor Green
} else {
    Write-Host "No se pudo leer la IP de Tailscale. Sacala con: tailscale ip -4" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "PROBALO ANTES DE IRTE, con DATOS MOVILES (no con el wifi del local)."
Write-Host "Si no entra estando ahi, tampoco va a entrar desde tu casa."
