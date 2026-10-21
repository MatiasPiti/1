"""OtterBlindaje: el .exe de un botón que deja la PC del local blindada.

Lo que se cuida acá es lo que no se ve mirando la ventana:

  - Que encuentre los .ps1 que va a ejecutar. Si no los encuentra, el
    botón no hace NADA y la ventana igual se ve bien: es la peor forma de
    fallar, porque Matías se va del local creyendo que quedó blindado.
  - Que no haya lógica duplicada. La app corre los .ps1, no los reescribe:
    si alguien copia los comandos adentro del .py, en tres meses una mitad
    va a estar arreglada y la otra no.
  - Que los .ps1 sigan cubriendo las causas conocidas (energía, botón,
    tapa, placa de red, puerto).
  - Que «Solo actualizar el watchdog» (-SoloWatchdog) no toque la red, la
    energía ni Tailscale: se verifica qué pasos quedan adentro de qué if,
    con el parser de PowerShell si lo hay.
  - Que el watchdog de la API del celular sea una tarea propia, con tope de
    4 minutos, que nunca nombre al servicio de stock, que lea config.ini
    igual que Python y que su freno frene (corriéndolo con dobles).
  - El watchdog del 8765 (commit aparte): freno con @() y tope de 4 minutos,
    con las decisiones de reinicio todavía por PuertoVivo.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.blindaje.main import buscar_script, _decodificar, USUARIO_SOPORTE

fallos = []

# ---------------------------------------------------------------- #
# 1. Los scripts que el botón va a ejecutar tienen que existir
# ---------------------------------------------------------------- #
for nombre in ("blindar_local.ps1", "soporte_remoto.ps1"):
    ruta = buscar_script(nombre)
    print(f"ENCONTRADO: {nombre} -> {'sí' if ruta else 'NO'}")
    if not ruta:
        fallos.append(f"la app no encuentra {nombre}: el botón no haría nada")

blindar = open(buscar_script("blindar_local.ps1"), encoding="utf-8").read()
soporte = open(buscar_script("soporte_remoto.ps1"), encoding="utf-8").read()

# ---------------------------------------------------------------- #
# 2. Las causas conocidas siguen cubiertas
# ---------------------------------------------------------------- #
COBERTURA = {
    "el servicio arranca solo con Windows": "StartupType Automatic",
    "todos los planes de energía (no solo el activo)": "setacvalueindex",
    "el botón de encendido no suspende": "PBUTTONACTION",
    "cerrar la tapa no suspende": "LIDACTION",
    "la hibernación queda apagada": "hibernate off",
    "la placa de red no se apaga sola": "Disable-NetAdapterPowerManagement",
    "el watchdog mira EL PUERTO, no el estado del servicio": "PuertoVivo",
    "Tailscale queda en unattended": "unattended",
}
for que_cuida, marca in COBERTURA.items():
    if marca not in blindar:
        fallos.append(f"el blindaje ya no cubre: {que_cuida} (falta '{marca}')")
print(f"COBERTURA DEL BLINDAJE: {len(COBERTURA) - len([f for f in fallos if 'ya no cubre' in f])}"
      f"/{len(COBERTURA)} causas")

# El SSH tiene que quedar SOLO por la VPN: si esa línea desaparece, el
# puerto 22 queda abierto a lo que sea que alcance la máquina.
# Sin DefaultShell, la conexión autentica bien y muere con "shell request
# failed on channel 0": parece un problema de contraseña o de red, y no es
# ninguno de los dos.
if "DefaultShell" not in soporte:
    fallos.append("soporte_remoto.ps1 no fija el shell por defecto: el SSH va a "
                   "autenticar y morir con 'shell request failed on channel 0'")
if "100.64.0.0/10" not in soporte:
    fallos.append("¡el soporte remoto ya no limita el SSH al rango de Tailscale!")
if "-Password" not in soporte:
    fallos.append("soporte_remoto.ps1 no acepta -Password: la app no puede pasársela")
if USUARIO_SOPORTE not in soporte:
    fallos.append(f"la app y el script no coinciden en el usuario ({USUARIO_SOPORTE})")

# ---------------------------------------------------------------- #
# 3. Nada de lógica duplicada entre el .py y los .ps1
# ---------------------------------------------------------------- #
app = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "apps", "blindaje", "main.py"), encoding="utf-8").read()
# Se mira el CÓDIGO, no los comentarios: el archivo explica qué hacen los
# scripts, y esas menciones son legítimas.
codigo = "\n".join(l for l in app.splitlines()
                   if not l.strip().startswith("#"))
for comando in ("powercfg", "Set-Service", "Register-ScheduledTask", "Disable-NetAdapter"):
    if comando in codigo:
        fallos.append(f"'{comando}' está reimplementado en el .py: tiene que vivir "
                       f"solo en el .ps1, o se van a desincronizar")

# ---------------------------------------------------------------- #
# 4. La salida de PowerShell viene en la codepage de la consola
# ---------------------------------------------------------------- #
# Si esto rompe, los acentos de los mensajes de Windows llenan la pantalla
# de basura justo cuando hay que leer un error.
for crudo, espera in ((b"Servicio en Automatic", "Servicio en Automatic"),
                      ("conexión".encode("cp1252"), "conexión"),
                      ("configuración".encode("utf-8"), "configuración")):
    obtenido = _decodificar(crudo)
    if obtenido != espera:
        fallos.append(f"decodificar {crudo!r} dio {obtenido!r}, esperaba {espera!r}")
print("DECODIFICACIÓN: ok (utf-8 y cp1252)")

# ---------------------------------------------------------------- #
# 4b. Nada que dependa del IDIOMA de Windows
# ---------------------------------------------------------------- #
# powercfg, sc.exe y compania hablan el idioma del sistema. Buscar "Index"
# en la salida de powercfg funcionaba en inglés y daba SIEMPRE cero
# coincidencias en la PC del cliente (Windows en español dice "Índice"):
# la tabla marcaba NO aunque los cambios hubieran entrado bien. Un chequeo
# que depende del idioma no es un chequeo.
PALABRAS_EN_INGLES = ("'Index'", '"Index"', "'Running'", "'Enabled'", "'Success'")
for archivo, contenido in (("blindar_local.ps1", blindar), ("soporte_remoto.ps1", soporte)):
    for palabra in PALABRAS_EN_INGLES:
        if f"Select-String {palabra}" in contenido or f"-Pattern {palabra}" in contenido:
            fallos.append(f"{archivo} busca {palabra} en la salida de un comando: "
                           f"eso depende del idioma de Windows y falla en español")
print("SIN CHEQUEOS QUE DEPENDAN DEL IDIOMA: ok")

# ---------------------------------------------------------------- #
# 4c. Cada fila de la tabla se gana el SI comprobando algo
# ---------------------------------------------------------------- #
# En la PC del local la fila "Usuario de soporte" dijo SI con el usuario
# INEXISTENTE: se marcaba OK apenas terminaba el bloque sin error, no
# porque el usuario estuviera. Un OK que no comprueba nada es peor que no
# tener la fila, porque hace dar por resuelto algo que no lo está.
if "Get-LocalUser" not in soporte or "net user" not in soporte:
    fallos.append("soporte_remoto.ps1 no comprueba que el usuario exista de verdad "
                   "después de crearlo")
for marca, que_cuida in (("$sinSuspension", "que la PC no se suspenda"),
                          ("$existe", "que el usuario de soporte exista"),
                          ("$puerto", "que el puerto 8765 escuche")):
    fuente = blindar if marca in blindar else soporte
    if marca not in fuente:
        fallos.append(f"no hay verificación real de: {que_cuida}")
print("CADA 'SI' DE LA TABLA COMPRUEBA ALGO: ok")

# ---------------------------------------------------------------- #
# 4d. -SoloWatchdog y el watchdog de la API del celular
# ---------------------------------------------------------------- #
# Lo que se mira acá es la ESTRUCTURA de los .ps1 (qué queda adentro de qué
# if) y, si hay PowerShell, cómo se comportan de verdad. Mirar el texto no
# alcanza: un paso que quede afuera del "if (-not $SoloWatchdog)" corta la
# red con el negocio abierto, que es justo lo que ese modo existe para evitar.
import json
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from pos_core import config as config_otter
from pos_core import servicio_windows

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_BLINDAR = buscar_script("blindar_local.ps1")
RUTA_WATCHDOG_CEL = os.path.join(RAIZ, "scripts", "watchdog_celular.ps1")
INTEGRACION = os.environ.get("OTTER_INTEGRACION") == "1"


def saltear(que, por_que):
    """Saltear se dice en voz alta, y en el CI (OTTER_INTEGRACION=1) es una falla."""
    print(f"SALTEADA: {que} ({por_que})")
    if INTEGRACION:
        fallos.append(f"SALTEADA con OTTER_INTEGRACION=1: {que} ({por_que})")


def mascara_ps(t):
    """El .ps1 con strings, here-strings y comentarios tapados con espacios.

    Mismo largo y mismos saltos de línea que el original: lo que queda a la
    vista es solo código, y las posiciones sirven para el texto original.
    Así las llaves de un mensaje o de un comentario no confunden a nadie.
    """
    n = len(t)
    out = list(t)

    def tapar(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    def fin_simple(i):
        while i < n:
            if t[i] == "'":
                if i + 1 < n and t[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return n

    def fin_subexpresion(i):
        prof = 1
        while i < n:
            c = t[i]
            if c == "'":
                i = fin_simple(i + 1)
                continue
            if c == '"':
                i = fin_doble(i + 1)
                continue
            if c == "(":
                prof += 1
            elif c == ")":
                prof -= 1
                if prof == 0:
                    return i + 1
            i += 1
        return n

    def fin_doble(i):
        while i < n:
            c = t[i]
            if c == "`":
                i += 2
                continue
            if c == '"':
                if i + 1 < n and t[i + 1] == '"':
                    i += 2
                    continue
                return i + 1
            if c == "$" and i + 1 < n and t[i + 1] == "(":
                i = fin_subexpresion(i + 2)
                continue
            i += 1
        return n

    i = 0
    while i < n:
        c = t[i]
        if t.startswith("<#", i):
            j = t.find("#>", i + 2)
            j = n if j < 0 else j + 2
            tapar(i, j)
            i = j
            continue
        if c == "#":
            j = t.find("\n", i)
            j = n if j < 0 else j
            tapar(i, j)
            i = j
            continue
        if c == "@" and i + 1 < n and t[i + 1] in "'\"":
            fin_linea = t.find("\n", i)
            if fin_linea >= 0 and t[i + 2:fin_linea].strip() == "":
                m = re.compile(r"\n" + re.escape(t[i + 1]) + "@").search(t, fin_linea)
                j = n if not m else m.end()
                tapar(i, j)
                i = j
                continue
        if c == "'":
            j = fin_simple(i + 1)
            tapar(i, j)
            i = j
            continue
        if c == '"':
            j = fin_doble(i + 1)
            tapar(i, j)
            i = j
            continue
        if c == "`":
            i += 2
            continue
        i += 1
    return "".join(out)


def cierre_de_llave(mascara, pos_llave):
    prof = 0
    for k in range(pos_llave, len(mascara)):
        if mascara[k] == "{":
            prof += 1
        elif mascara[k] == "}":
            prof -= 1
            if prof == 0:
                return k
    return -1


def bloques(mascara, patron_if):
    """(desde, hasta) del cuerpo de cada if cuyo encabezado matchea el patrón."""
    rangos = []
    for m in re.finditer(patron_if, mascara):
        llave = m.end() - 1
        rangos.append((llave, cierre_de_llave(mascara, llave)))
    return rangos


mascara_blindar = mascara_ps(blindar)

# --- param() es la primera instrucción, y los pasos 0, 1, 2 y 4 quedan adentro ---
if not mascara_blindar.lstrip().startswith("param([switch]$SoloWatchdog)"):
    fallos.append("blindar_local.ps1 no arranca con param([switch]$SoloWatchdog): en PowerShell "
                   "un param() que no es la primera instrucción es un comando más, y el "
                   "parámetro no existe")
rangos_solo = bloques(mascara_blindar, r"if\s*\(\s*-not\s+\$SoloWatchdog\s*\)\s*\{")
for paso, tiene_que_estar_adentro in ((0, True), (1, True), (2, True), (3, False), (4, True), (5, False)):
    pos = blindar.find(f"== {paso}/5")
    if pos < 0:
        fallos.append(f"blindar_local.ps1 ya no tiene el paso {paso}/5")
        continue
    adentro = any(a < pos < b for a, b in rangos_solo)
    if tiene_que_estar_adentro and not adentro:
        fallos.append(f"el paso {paso}/5 corre también con -SoloWatchdog (tiene que estar adentro "
                       f"de if (-not $SoloWatchdog))")
    if not tiene_que_estar_adentro and adentro:
        fallos.append(f"el paso {paso}/5 NO corre con -SoloWatchdog, y es justo lo que ese modo hace")
if "Modo SOLO WATCHDOG" not in blindar:
    fallos.append("con -SoloWatchdog el script no avisa que no toca la red")
print(f"-SoloWatchdog: {len(rangos_solo)} bloques salteables; pasos 0, 1, 2 y 4 adentro, 3 y 5 afuera")

# --- el watchdog del celular es una tarea PROPIA, con tope, copiada de al lado ---
def sentencia(mascara, desde):
    """Una sentencia de PowerShell que puede seguir en la línea siguiente con `."""
    fin = desde
    while True:
        fin_linea = mascara.find("\n", fin)
        if fin_linea < 0:
            return mascara[desde:]
        if not mascara[:fin_linea].rstrip().endswith("`"):
            return mascara[desde:fin_linea]
        fin = fin_linea + 1


m = re.search(r"\$opcionesCel\s*=\s*New-ScheduledTaskSettingsSet", mascara_blindar)
if not m or "-ExecutionTimeLimit (New-TimeSpan -Minutes 4)" not in sentencia(mascara_blindar, m.start()):
    fallos.append("la tarea del watchdog del celular no tiene -ExecutionTimeLimit de 4 minutos: "
                   "una corrida trabada la dejaría sin volver a correr por 72 hs")
m = re.search(r"Register-ScheduledTask\s+-TaskName\s+\"OtterWatchdogCelular\"", blindar)
if not m or "-Settings $opcionesCel" not in sentencia(blindar, m.start()):
    fallos.append("no se registra OtterWatchdogCelular con sus propias opciones ($opcionesCel)")
if '"$PSScriptRoot\\watchdog_celular.ps1"' not in blindar or "Copy-Item $origenCel" not in blindar:
    fallos.append("blindar_local.ps1 no copia watchdog_celular.ps1 desde $PSScriptRoot")
if "falta watchdog_celular.ps1 al lado de blindar_local.ps1" not in blindar:
    fallos.append("si falta watchdog_celular.ps1 la tabla no lo dice")
m = re.search(r"if\s*\(\s*\$svcCel\s*\)\s*\{", mascara_blindar)
if not m:
    fallos.append("el paso 1 ya no revisa la API del celular")
else:
    cuerpo = blindar[m.end():cierre_de_llave(mascara_blindar, m.end() - 1)]
    p_deshab = cuerpo.find("StartType -eq 'Disabled'")
    p_set = cuerpo.find("Set-Service SistemaDualApiCelular")
    if p_deshab < 0 or p_set < 0 or p_deshab > p_set:
        fallos.append("el paso 1 puede poner en Automatic una API del celular Deshabilitada (D21)")
if "CelularContesta 8766" not in blindar or "otter-api-celular" not in blindar:
    fallos.append("el paso 5 no verifica la API del celular por su firma")
print("TAREA OtterWatchdogCelular: propia, con tope de 4 min y copiada de al lado")

# --- scripts/watchdog_celular.ps1, mirado sin ejecutarlo ---
if not os.path.isfile(RUTA_WATCHDOG_CEL):
    fallos.append("falta scripts/watchdog_celular.ps1")
    watchdog_cel = ""
else:
    crudo = open(RUTA_WATCHDOG_CEL, "rb").read()
    try:
        crudo.decode("ascii")
    except UnicodeDecodeError:
        fallos.append("watchdog_celular.ps1 tiene caracteres que no son ASCII: PowerShell 5.1 "
                       "lee en ANSI un .ps1 sin BOM y los rompe")
    watchdog_cel = crudo.decode("utf-8", errors="replace")
mascara_cel = mascara_ps(watchdog_cel)
# El servicio de stock no se nombra ni en un comentario; lo demás se mira en
# el código (los comentarios del script explican justamente por qué no van).
if "SistemaDualStockService" in watchdog_cel:
    fallos.append("watchdog_celular.ps1 nombra al servicio de stock: si este script falla, el "
                   "watchdog del 8765 tiene que seguir solo")
for prohibido, por_que in (("Restart-Service", "espera sin límite a que el servicio pare"),
                           ("netstat", "su salida cambia con el idioma"),
                           ("Invoke-WebRequest", "sin -UseBasicParsing falla como SYSTEM"),
                           ("Select-String", "sobre texto traducido no es un chequeo")):
    if prohibido.lower() in mascara_cel.lower():
        fallos.append(f"watchdog_celular.ps1 usa {prohibido}: {por_que}")
salidas = re.findall(r"\bexit\b[^\n;}]*", mascara_cel)
malas = [s.strip() for s in salidas if not re.fullmatch(r"exit\s+0\s*", s.strip() + " ")]
if not salidas or malas:
    fallos.append(f"watchdog_celular.ps1 tiene salidas que no son 'exit 0' ({malas}): un "
                   f"LastTaskResult distinto de 0 tiene que quedar solo para fallas de verdad")
if "@(Get-Content $marcas" not in watchdog_cel or "(@($rec) +" not in watchdog_cel:
    fallos.append("el freno del watchdog del celular no usa @(): con una sola marca no frena nunca")
if "reinicios_celular.txt" not in watchdog_cel:
    fallos.append("el watchdog del celular no lleva su propio archivo de reinicios")
firmas = set(re.findall(r"'(otter-api-[a-z]+)'", watchdog_cel))
if firmas != {servicio_windows.FIRMA_CELULAR}:
    fallos.append(f"la firma que busca el watchdog ({firmas}) no es la de servicio_windows "
                   f"({servicio_windows.FIRMA_CELULAR})")
p_marca = mascara_cel.find("Set-Content $marcas")
p_reinicio = mascara_cel.find("ReiniciarCelular $cfg.puerto")
if p_marca < 0 or p_reinicio < 0 or p_marca > p_reinicio:
    fallos.append("la marca del freno se tiene que escribir ANTES de reiniciar: si la tarea se "
                   "corta a los 4 minutos en el medio, el reinicio igual tiene que contar")
for palabra in PALABRAS_EN_INGLES:
    if f"Select-String {palabra}" in watchdog_cel or f"-Pattern {palabra}" in watchdog_cel:
        fallos.append(f"watchdog_celular.ps1 busca {palabra} en la salida de un comando")
print("watchdog_celular.ps1: ASCII, solo 'exit 0', freno con @(), sin tocar el servicio de stock")


# --- Con PowerShell: parsear, y correr las partes con dobles ---
def buscar_powershell():
    for nombre in ("pwsh", "powershell.exe", "powershell"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    return None


PWSH = buscar_powershell()

# Los 13 casos de exp-diseno-correccion/k5_regla_ini.py (comentario en la
# línea, mayúsculas, sí, ":", puerto basura o fuera de rango, sección en
# mayúsculas, otra sección después), más el BOM y el puerto de [remoto].
CASOS_INI = [
    ("simple", "[api_celular]\nhabilitado = true\npuerto = 8766\n"),
    ("comentario_misma_linea", "[api_celular]\nhabilitado = true     ; la escribe el Instalador\n"
                               "puerto = 8766   ; falta = 8766\n"),
    ("mayusculas_valor", "[api_celular]\nhabilitado = TRUE\npuerto = 8770\n"),
    ("clave_mayuscula", "[api_celular]\nHabilitado = si\n"),
    ("si_con_acento", "[api_celular]\nhabilitado = sí\n"),
    ("false", "[api_celular]\nhabilitado = false\npuerto = 8766\n"),
    ("sin_seccion", "[remoto]\nhabilitado = true\n"),
    ("seccion_mayus", "[API_CELULAR]\nhabilitado = true\n"),
    ("dos_puntos", "[api_celular]\nhabilitado: 1\npuerto: 9000\n"),
    ("puerto_basura", "[api_celular]\nhabilitado = true\npuerto = 87x6\n"),
    ("puerto_fuera_rango", "[api_celular]\nhabilitado = true\npuerto = 99999\n"),
    ("pegado_punto_coma", "[api_celular]\nhabilitado = true;x\npuerto = 8766;x\n"),
    ("otra_seccion_despues", "[api_celular]\nhabilitado = false\n[telegram]\nhabilitado = true\n"),
    ("con_bom", "﻿[api_celular]\nhabilitado = sí\npuerto = 8790\n"),
    ("puerto_remoto", "[remoto]\npuerto = 9001\n[api_celular]\nhabilitado = on\npuerto = 9001\n"),
]

# Devuelve el TEXTO de las funciones: quien llama las define con ". " en su
# propio alcance (definidas adentro de otra función, desaparecerían al volver).
CARGAR_FUNCIONES = r'''
function FuncionesDe($ruta, $nombres) {
    $e = $null; $t = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($ruta, [ref]$t, [ref]$e)
    foreach ($f in $ast.FindAll({ $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        if ($nombres -contains $f.Name) { $f.Extent.Text }
    }
}
'''

# Un solo PowerShell para todo lo que no hace "exit": parseo, estructura,
# regla del ini, freno y paso 1 del blindaje con dobles.
REVISAR = r'''
param($blindar, $watchdog, $carpeta)
''' + CARGAR_FUNCIONES + r'''
$r = @{}
$parseo = @{}
foreach ($f in @($blindar, $watchdog)) {
    $e = $null; $t = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e)
    $parseo[[IO.Path]::GetFileName($f)] = @($e | ForEach-Object { "$($_.Message) (linea $($_.Extent.StartLineNumber))" })
    # El watchdog del 8765 vive como here-string adentro de blindar_local.ps1: se parsea tambien.
    $ast.FindAll({ $args[0] -is [Management.Automation.Language.StringConstantExpressionAst] -and
                   $args[0].StringConstantType -eq 'SingleQuotedHereString' }, $true) | ForEach-Object {
        $e2 = $null
        [void][Management.Automation.Language.Parser]::ParseInput($_.Value, [ref]$t, [ref]$e2)
        $parseo["here-string linea $($_.Extent.StartLineNumber)"] = @($e2 | ForEach-Object { $_.Message })
    }
}
$r.parseo = $parseo

$e = $null; $t = $null
$astB = [Management.Automation.Language.Parser]::ParseFile($blindar, [ref]$t, [ref]$e)
$r.param = @($astB.ParamBlock.Parameters | ForEach-Object { "$($_.StaticType.Name) $($_.Name.VariablePath.UserPath)" })
$pasos = @{}
foreach ($c in $astB.FindAll({ $args[0] -is [Management.Automation.Language.CommandAst] -and
                               $args[0].Extent.Text -match '^Write-Host "(`n)?== (\d)/5' }, $true)) {
    $null = $c.Extent.Text -match '== (\d)/5'
    $n = $matches[1]
    $conds = @()
    $p = $c.Parent
    while ($null -ne $p) {
        if ($p -is [Management.Automation.Language.IfStatementAst]) {
            $conds += @($p.Clauses | ForEach-Object { $_.Item1.Extent.Text })
        }
        $p = $p.Parent
    }
    $pasos[$n] = $conds
}
$r.pasos = $pasos

# Regla del ini: la misma lectura que pos_core/config.py::leer_config_celular.
foreach ($f in @(FuncionesDe $watchdog @('LeerConfigCelular', 'PuertoValido'))) { . ([scriptblock]::Create($f)) }
$ini = @()
foreach ($f in @(Get-ChildItem (Join-Path $carpeta 'ini_*.ini') | Sort-Object Name)) {
    $x = LeerConfigCelular $f.FullName
    $ini += [pscustomobject]@{ archivo = $f.Name; habilitado = [bool]$x.habilitado; puerto = [int]$x.puerto;
                               puerto_remoto = [int]$x.puerto_remoto }
}
$r.ini = $ini

# El freno: 3 reinicios en 24 hs y en la 4.a corrida ya no.
foreach ($f in @(FuncionesDe $watchdog @('Frenado'))) { . ([scriptblock]::Create($f)) }
$marcas = Join-Path $carpeta 'reinicios_celular.txt'
$cuentas = @()
for ($i = 1; $i -le 5; $i++) {
    $rec = Frenado
    $cuentas += $rec.Count
    if ($rec.Count -lt 3) { (@($rec) + (Get-Date).AddSeconds($i).ToString("o")) | Set-Content $marcas }
}
$r.freno = $cuentas
@((Get-Date).AddHours(-30).ToString("o"), (Get-Date).ToString("o")) | Set-Content $marcas
$r.freno_vieja = (Frenado).Count

# Paso 1 del blindaje sobre la API del celular, con dobles de Windows.
$if1 = $astB.FindAll({ $args[0] -is [Management.Automation.Language.IfStatementAst] -and
                       $args[0].Clauses[0].Item1.Extent.Text -eq '$svcCel' }, $true) | Select-Object -First 1
function Set-Service { $global:hechos += "Set-Service $($args -join ' ')" }
function ScFalso { $global:hechos += "sc $($args -join ' ')" }
Set-Alias -Name sc.exe -Value ScFalso
function Anotar($paso, $ok, $detalle) { $global:hechos += "Anotar|$paso|$ok|$detalle" }
function Get-Service { return $global:svcFalso }
$paso1 = @{}
foreach ($modo in @('Disabled', 'Manual')) {
    $global:hechos = @()
    $global:svcFalso = [pscustomobject]@{ StartType = $modo; Status = 'Stopped' }
    $svcCel = $global:svcFalso
    if ($null -ne $if1) { . ([scriptblock]::Create($if1.Extent.Text)) }
    $paso1[$modo] = @($global:hechos)
}
$r.paso1 = $paso1
$r | ConvertTo-Json -Depth 6 -Compress
'''

# Flujo del watchdog con dobles (exp-diseno-final/f2_flujo.ps1): corre el
# cuerpo del script con Windows simulado. Va un PowerShell por corrida
# porque el cuerpo termina con "exit 0".
FLUJO = r'''
param($script, $dir)
$e = $null; $t = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($script, [ref]$t, [ref]$e)
foreach ($f in $ast.FindAll({ $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    . ([scriptblock]::Create($f.Extent.Text))
}
$base = $dir; $log = Join-Path $base "watchdog_celular.log"; $marcas = Join-Path $base "reinicios_celular.txt"
$hora = "H"; $servicio = "SistemaDualApiCelular"
$llamadas = Join-Path $base "llamadas.txt"
# sc.exe con ALIAS (lo primero que mira PowerShell): en un Windows de verdad,
# una función sola podría perder contra el sc.exe real y parar un servicio.
function ScFalso { Add-Content $llamadas "sc $($args -join ' ')" }
Set-Alias -Name sc.exe -Value ScFalso
function Start-Sleep { }
function Stop-Process { Add-Content $llamadas "Stop-Process $($args -join ' ')" }
$estado = Get-Content (Join-Path $dir "estado.json") -Raw | ConvertFrom-Json
function EstadoServicio { if ($estado.instalado) { return [pscustomobject]@{ State = $estado.State; StartMode = $estado.StartMode; ProcessId = 42 } } else { return $null } }
function EsperarEstado($e, $s) { Add-Content $llamadas "ESPERA $e $s"; return (EstadoServicio) }
function LeerConfigCelular($r) { return @{ habilitado = $estado.habilitado; puerto = 8766; puerto_remoto = $estado.puerto_remoto } }
function CelularContesta($p) { return [bool]$estado.contesta }
function QuienEscucha($p) { return $estado.quien }
function AvisarUnaVezPorDia($clave, $texto) { Add-Content $llamadas "AVISO $clave" }
$cuerpo = $ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] } | Select-Object -First 1
. ([scriptblock]::Create($cuerpo.Extent.Text))
'''

BASE_FLUJO = dict(instalado=True, State="Running", StartMode="Auto", habilitado=True,
                  puerto_remoto=8765, contesta=True, quien="")
# Reiniciar es: sc stop, esperar a que pare, cerrarla a la fuerza si sigue (el doble nunca
# para), esperar de nuevo y sc start. Nunca Restart-Service.
REINICIO = ["sc stop", "ESPERA Stopped", "Stop-Process", "ESPERA Stopped", "sc start"]
ESCENARIOS = [
    ("no instalada", dict(BASE_FLUJO, instalado=False), [[]]),
    ("Deshabilitada", dict(BASE_FLUJO, StartMode="Disabled", contesta=False), [["AVISO deshabilitada"]]),
    ("habilitado = false", dict(BASE_FLUJO, habilitado=False, contesta=False), [[]]),
    ("puerto igual al de [remoto]", dict(BASE_FLUJO, puerto_remoto=8766, contesta=False), [["AVISO puerto"]]),
    ("parada", dict(BASE_FLUJO, State="Stopped"), [["sc start"]]),
    ("corriendo y contesta", dict(BASE_FLUJO), [[]]),
    ("otro programa en el puerto", dict(BASE_FLUJO, contesta=False, quien="python (PID 9)"), [["AVISO otro"]]),
    ("5 corridas sin contestar", dict(BASE_FLUJO, contesta=False),
     [REINICIO, REINICIO, REINICIO, ["AVISO freno"], ["AVISO freno"]]),
    # Start/Stop Pending: primero se le dan 30 s para asentarse; si sigue trabada, pasa por el
    # mismo freno y el mismo reinicio con Stop-Process.
    ("Stop Pending trabado", dict(BASE_FLUJO, State="Stop Pending", contesta=False),
     [["ESPERA Running"] + REINICIO]),
]


def correr_ps(script_ps, *argumentos, timeout=120):
    comando = [PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script_ps]
    return subprocess.run(comando + list(argumentos), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def normalizar(linea):
    partes = linea.split()
    if not partes:
        return ""
    if partes[0] in ("sc", "AVISO", "ESPERA"):
        return " ".join(partes[:2])
    return partes[0]


def correr_escenario(carpeta_ps, nombre, estado, esperado):
    d = tempfile.mkdtemp(prefix="flujo_cel_")
    with open(os.path.join(d, "estado.json"), "w", encoding="utf-8") as f:
        json.dump(estado, f)
    obtenido = []
    for _ in esperado:
        r = correr_ps(os.path.join(carpeta_ps, "flujo.ps1"), RUTA_WATCHDOG_CEL, d)
        archivo = os.path.join(d, "llamadas.txt")
        lineas = open(archivo, encoding="utf-8").read().split("\n") if os.path.exists(archivo) else []
        if os.path.exists(archivo):
            os.remove(archivo)
        obtenido.append((r.returncode, [normalizar(l) for l in lineas if l.strip()], r.stderr.strip()))
    shutil.rmtree(d, ignore_errors=True)
    return nombre, esperado, obtenido


if not PWSH:
    saltear("los .ps1 con PowerShell (parseo, regla del ini, freno, flujo con dobles)",
            "no hay pwsh ni powershell.exe en esta máquina")
else:
    carpeta_ps = tempfile.mkdtemp(prefix="blindaje_ps_")
    with open(os.path.join(carpeta_ps, "revisar.ps1"), "w", encoding="utf-8") as f:
        f.write(REVISAR)
    with open(os.path.join(carpeta_ps, "flujo.ps1"), "w", encoding="utf-8") as f:
        f.write(FLUJO)
    # Los ini los escribe Python, en UTF-8 (como config.guardar_config): así
    # el caso "sí" no depende de la codificación por defecto de cada PowerShell.
    for k, (nombre, texto) in enumerate(CASOS_INI):
        with open(os.path.join(carpeta_ps, f"ini_{k:02d}.ini"), "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futuros = [pool.submit(correr_escenario, carpeta_ps, n, e, esp) for n, e, esp in ESCENARIOS]
        r = correr_ps(os.path.join(carpeta_ps, "revisar.ps1"), RUTA_BLINDAR, RUTA_WATCHDOG_CEL, carpeta_ps)
        resultados_flujo = [fu.result() for fu in futuros]

    try:
        rev = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        rev = None
        fallos.append(f"PowerShell no devolvió el resultado de la revisión de los .ps1:\n"
                       f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    if rev is not None:
        # Parseo
        con_error = {k: v for k, v in rev["parseo"].items() if v}
        if con_error:
            fallos.append(f"los .ps1 tienen errores de sintaxis: {con_error}")
        if len(rev["parseo"]) < 3:
            fallos.append(f"no se parsearon los dos .ps1 y el here-string del watchdog: {list(rev['parseo'])}")
        print(f"PARSEO ({os.path.basename(PWSH)}): {len(rev['parseo'])} bloques, "
              f"{sum(len(v) for v in rev['parseo'].values())} errores")
        # Estructura, según el parser de PowerShell (no según el texto)
        if rev["param"] != ["SwitchParameter SoloWatchdog"]:
            fallos.append(f"el param() de blindar_local.ps1 no es [switch]$SoloWatchdog: {rev['param']}")
        for paso in "012345":
            conds = rev["pasos"].get(paso)
            if conds is None:
                fallos.append(f"PowerShell no encontró el Write-Host del paso {paso}/5")
                continue
            adentro = "-not $SoloWatchdog" in conds
            if adentro != (paso in "0124"):
                fallos.append(f"según el parser de PowerShell, el paso {paso}/5 "
                               f"{'NO ' if not adentro else ''}está adentro de if (-not $SoloWatchdog)")
        # Regla del ini: idéntica a la de Python
        distintos = []
        for k, (nombre, texto) in enumerate(CASOS_INI):
            ruta = os.path.join(carpeta_ps, f"ini_{k:02d}.ini")
            py = config_otter.leer_config_celular(ruta)
            ps = next((x for x in rev["ini"] if x["archivo"] == f"ini_{k:02d}.ini"), None)
            if ps is None or (bool(ps["habilitado"]), int(ps["puerto"]), int(ps["puerto_remoto"])) != \
                    (py["habilitado"], py["puerto"], py["puerto_remoto"]):
                distintos.append(f"{nombre}: python={py} watchdog={ps}")
        if distintos:
            fallos.append("LeerConfigCelular (watchdog) y config.leer_config_celular (Python) leen "
                           "distinto: " + "; ".join(distintos))
        print(f"REGLA DEL INI: {len(CASOS_INI) - len(distintos)}/{len(CASOS_INI)} casos iguales "
              f"en el watchdog y en Python")
        # Freno
        if rev["freno"] != [0, 1, 2, 3, 3] or rev["freno_vieja"] != 1:
            fallos.append(f"el freno del watchdog del celular no frena en la 4.a corrida: "
                           f"cuentas {rev['freno']}, con una marca de hace 30 hs {rev['freno_vieja']}")
        else:
            print("FRENO: reinicia 3 veces y en la 4.a frena; una marca de hace 30 hs no cuenta")
        # Paso 1: Deshabilitada no se toca
        deshab = rev["paso1"].get("Disabled", [])
        manual = rev["paso1"].get("Manual", [])
        if any(h.startswith(("Set-Service", "sc ")) for h in deshab) or \
                not any(h.startswith("Anotar|API del celular en Automatic|False|Deshabilitada") for h in deshab):
            fallos.append(f"el paso 1 tocó una API del celular Deshabilitada (o no lo dijo): {deshab}")
        if not any(h.startswith("Set-Service SistemaDualApiCelular") for h in manual) or \
                not any(h.startswith("sc failure SistemaDualApiCelular") for h in manual) or \
                not any(h.startswith("sc failureflag SistemaDualApiCelular 1") for h in manual):
            fallos.append(f"el paso 1 no pone en Automatic con reintentos una API del celular en Manual: {manual}")
        if deshab and manual:
            print("PASO 1: una API del celular Deshabilitada no se toca; en Manual pasa a Automatic + reintentos")

    malos = []
    for nombre, esperado, obtenido in resultados_flujo:
        llamadas = [o[1] for o in obtenido]
        codigos = [o[0] for o in obtenido]
        if llamadas != esperado or any(c != 0 for c in codigos):
            malos.append(f"{nombre}: esperaba {esperado}, salió {llamadas} (códigos {codigos}) "
                         f"{[o[2][-300:] for o in obtenido if o[2]]}")
    if malos:
        fallos.append("el flujo del watchdog del celular con dobles no da lo esperado: " + " | ".join(malos))
    else:
        print(f"FLUJO DEL WATCHDOG DEL CELULAR: {len(ESCENARIOS)} escenarios con lo esperado "
              f"(Stop Pending trabado y freno incluidos)")
    shutil.rmtree(carpeta_ps, ignore_errors=True)

# ---------------------------------------------------------------- #
# 4e. Watchdog del 8765 (commit aparte): el freno con @() y el tope de 4 min
# ---------------------------------------------------------------- #
# Cambia lo que ya corre en producción, aunque lo devuelve a lo documentado:
# sin @(), con una sola marca Get-Content devuelve un texto suelto, "texto +
# fecha" los pega en una línea ilegible y la cuenta vuelve a 0 (0, 1, 0, 1...
# para siempre). Con el puerto muerto el servicio de stock se reiniciaba cada
# 5 minutos sin frenar nunca. Las decisiones de reinicio NO cambian: siguen
# mirando PuertoVivo.
m = re.search(r"\$vigilante\s*=\s*@'\n(.*?)\n'@", blindar, re.DOTALL)
if not m:
    fallos.append("no encontré el watchdog del 8765 (el here-string $vigilante) en blindar_local.ps1")
else:
    vigilante = m.group(1)
    mascara_vig = mascara_ps(vigilante)
    if "$recientes = @(Get-Content $marcas" not in vigilante:
        fallos.append("el freno del watchdog del 8765 lee las marcas sin @(): con una sola marca "
                       "la cuenta vuelve a 0 y nunca frena")
    if "(@($recientes) + (Get-Date).ToString(\"o\")) | Set-Content $marcas" not in vigilante:
        fallos.append("el freno del watchdog del 8765 escribe las marcas sin @(): pega la fecha "
                       "nueva a la vieja en una línea que no se puede leer")
    puerto_muerto = bloques(mascara_vig, r"if\s*\(\s*-not\s*\(\s*PuertoVivo\s*\)\s*\)\s*\{")
    p_restart = mascara_vig.find("Restart-Service SistemaDualStockService")
    if len(puerto_muerto) != 1 or not (puerto_muerto[0][0] < p_restart < puerto_muerto[0][1]):
        fallos.append("el reinicio del servicio de stock ya no depende de PuertoVivo (las decisiones "
                       "de reinicio del watchdog del 8765 no se tocan en esta tarea)")
    else:
        a, b = puerto_muerto[0]
        if "RemoteApiContesta" in mascara_vig[a:b]:
            fallos.append("RemoteApiContesta decide un reinicio: por ahora solo puede informar")
    if "aviso_8765_otro_" not in vigilante or "(PuertoVivo) -and -not (RemoteApiContesta)" not in vigilante:
        fallos.append("falta el aviso de 'el 8765 lo tiene otro programa'")
m = re.search(r"\$opciones\s*=\s*New-ScheduledTaskSettingsSet", mascara_blindar)
if not m or "-ExecutionTimeLimit (New-TimeSpan -Minutes 4)" not in sentencia(mascara_blindar, m.start()):
    fallos.append("OtterWatchdog sin -ExecutionTimeLimit: con el servicio trabado en Stop Pending, "
                   "Restart-Service espera sin límite y el servicio de stock queda sin watchdog hasta 72 hs")
if not [f for f in fallos if "8765" in f or "OtterWatchdog sin" in f or "PuertoVivo" in f]:
    print("WATCHDOG DEL 8765: freno con @() en las dos líneas, tope de 4 min, reinicio por PuertoVivo")

# ---------------------------------------------------------------- #
# 5. La ventana abre de verdad, y el log es seguro entre hilos
# ---------------------------------------------------------------- #
# Que el .exe compile no dice nada: si _armar_ui explota, el usuario hace
# doble clic y no pasa nada (los .exe van sin consola).
import threading
from apps.blindaje.main import AppBlindaje

ventana = AppBlindaje()
ventana.update()
print("VENTANA: abre y dibuja")

# La casilla "Solo actualizar el watchdog": pasa -SoloWatchdog, no corre el
# SSH, y el cierre no manda a hacer la prueba de apagado (no hace falta).
if not hasattr(ventana, "var_solo_watchdog"):
    fallos.append("OtterBlindaje no tiene la casilla «Solo actualizar el watchdog»")
else:
    ventana.var_ssh.set(True)
    ventana._cambiar_ssh()
    ventana.var_solo_watchdog.set(True)
    ventana._cambiar_solo_watchdog()
    ventana.update()
    if ventana.var_ssh.get() or str(ventana.casilla_ssh.cget("state")) != "disabled":
        fallos.append("con «Solo actualizar el watchdog» tildada, el SSH sigue disponible")
    corridos = []
    ventana._correr_script = lambda ruta, argumentos=None: corridos.append(
        (os.path.basename(ruta), list(argumentos or []))) or True
    ventana._guardar_informe = lambda: None      # no escribir en el Escritorio de quien prueba
    ventana._trabajar(False, "", True)
    texto_cierre = "\n".join(ventana._historial)
    if corridos != [("blindar_local.ps1", ["-SoloWatchdog"])]:
        fallos.append(f"«Solo actualizar el watchdog» no corrió solo blindar_local.ps1 -SoloWatchdog: {corridos}")
    if "Watchdog actualizado. No hace falta la prueba de apagado." not in texto_cierre or \
            "AHORA LA PRUEBA DE VERDAD" in texto_cierre:
        fallos.append("con «Solo actualizar el watchdog» el cierre igual manda a hacer la prueba de apagado")
    corridos.clear()
    ventana._trabajar(False, "", False)
    if corridos != [("blindar_local.ps1", [])]:
        fallos.append(f"sin la casilla, el blindaje entero ya no corre como antes: {corridos}")
    ventana.var_solo_watchdog.set(False)
    ventana._cambiar_solo_watchdog()
    if str(ventana.casilla_ssh.cget("state")) == "disabled":
        fallos.append("al destildar «Solo actualizar el watchdog» el SSH queda deshabilitado")
    print("CASILLA «Solo actualizar el watchdog»: corre blindar_local.ps1 -SoloWatchdog, sin SSH ni prueba de apagado")
    ventana._bombear()


if "self.texto.get(" in app:
    fallos.append("se lee el widget de Tk para armar el informe: eso corre en el hilo "
                   "de trabajo y Tk no es seguro entre hilos (ver CLAUDE.md)")

# El hilo de trabajo escribe en el log; el widget lo pinta el hilo de Tk.
def _desde_otro_hilo():
    for i in range(20):
        ventana._log(f"linea {i}")

h = threading.Thread(target=_desde_otro_hilo)
h.start(); h.join()
# _bombear es lo que el hilo de Tk corre cada 100 ms para vaciar la cola a
# la pantalla. Se lo llama directo en vez de dormir 100 ms: es la misma
# función, sin esperar.
ventana._bombear()
ventana.update()

if len(ventana._historial) < 20:
    fallos.append(f"el historial perdió líneas escritas desde otro hilo "
                   f"({len(ventana._historial)} de 20+)")
dibujado = ventana.texto.get("1.0", "end")
if "linea 19" not in dibujado:
    fallos.append("lo que escribió el otro hilo no llegó a la pantalla")
print(f"LOG ENTRE HILOS: {len(ventana._historial)} líneas guardadas y dibujadas")

# El informe se arma del historial, no del widget: tiene que salir igual
# aunque la ventana ya no exista.
ventana.destroy()
try:
    contenido = "\n".join(ventana._historial)
    if "linea 19" not in contenido:
        fallos.append("el informe no contiene lo que se mostró")
    print(f"INFORME: {len(contenido)} caracteres, armado sin tocar la ventana")
except Exception as e:
    fallos.append(f"no se puede armar el informe con la ventana cerrada: {e}")

print()
if fallos:
    print("=== FALLOS BLINDAJE ===")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("=== BLINDAJE OK ===")
