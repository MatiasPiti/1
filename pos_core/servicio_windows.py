"""Todo lo que hay que saber del servicio de Windows, en un solo lugar.

Existe porque el `StockService` es la pieza que más veces dejó a Leo sin
Dueño Remoto, y la misma pregunta —¿está corriendo? ¿arranca solo?
¿hay alguien escuchando en el puerto?— la necesitan el USB de
Mantenimiento, el Actualizador y cualquier cosa que venga después. Tenerla
copiada en cada uno garantizaba que en tres meses una mitad estuviera
arreglada y la otra no (el mismo criterio por el que OtterBlindaje ejecuta
los .ps1 en vez de reimplementarlos).

Regla de oro de este módulo: **ninguna función lanza una excepción.**
Preguntarle a Windows por un servicio nunca puede ser lo que voltee una
actualización ni lo que impida que la caja abra (regla 6).
"""

import json
import os
import re
import shutil
import socket
import subprocess
import time

NOMBRE_SERVICIO = "SistemaDualStockService"
PUERTO_POR_DEFECTO = 8765

# La API de la app del celular (ApiCelular.exe) es un SEGUNDO servicio, con
# su propio puerto. Las constantes se repiten acá (también están en
# services/api_celular.py) porque el Actualizador y el USB de Mantenimiento
# las necesitan sin importar FastAPI.
SERVICIO_CELULAR = "SistemaDualApiCelular"
PUERTO_CELULAR = 8766
FIRMA_CELULAR = "otter-api-celular"
REGLA_FIREWALL_CELULAR = "OtterApiCelular-Tailscale"
TAREA_WATCHDOG_CELULAR = "OtterWatchdogCelular"

# Se pregunta en tiempo de ejecución (y no con os.name suelto en cada
# función) para que las pruebas puedan simular Windows desde Linux.
_ES_WINDOWS = os.name == "nt"

# Windows: que no aparezca una ventana negra parpadeando al llamar a sc.exe
# desde una app con --windowed.
_SIN_VENTANA = {}
if os.name == "nt":
    _SIN_VENTANA = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _dormir(segundos: float) -> None:
    """time.sleep con nombre propio: las pruebas lo reemplazan para no esperar."""
    time.sleep(segundos)


def _sc(*argumentos, timeout: int = 15):
    """Corre sc.exe y devuelve (codigo, salida). (None, "") si ni se pudo.

    Se usa sc.exe con el .exe explícito A PROPÓSITO: en PowerShell `sc` es
    un alias de Set-Content, así que `sc query ...` no consulta ningún
    servicio — silenciosamente intenta escribir un archivo llamado "query".
    """
    if not _ES_WINDOWS:
        return None, ""
    try:
        r = subprocess.run(["sc.exe", *argumentos], capture_output=True, text=True,
                            timeout=timeout, **_SIN_VENTANA)
        return r.returncode, f"{r.stdout}\n{r.stderr}"
    except Exception as e:
        return None, str(e)


def estado(nombre: str = NOMBRE_SERVICIO) -> str:
    """'corriendo' | 'parado' | 'no_instalado' | 'desconocido'."""
    codigo, salida = _sc("query", nombre)
    if codigo is None:
        return "desconocido"
    texto = salida.upper()
    if "RUNNING" in texto:
        return "corriendo"
    if "STOPPED" in texto or "STOP_PENDING" in texto:
        return "parado"
    # 1060 = el servicio no existe. El texto del error viene traducido, el
    # número no: por eso se mira el número.
    if "1060" in texto or "NO EXIST" in texto or "NO EXISTE" in texto:
        return "no_instalado"
    return "desconocido"


def tipo_de_arranque(nombre: str = NOMBRE_SERVICIO) -> str:
    """'auto' | 'manual' | 'deshabilitado' | 'desconocido'.

    Es LA pregunta que importa: un servicio que hoy corre pero está en
    Manual no vuelve al reiniciar la PC, y ahí Leo se queda sin panel sin
    que nadie haya tocado nada. Pasó tres veces.
    """
    codigo, salida = _sc("qc", nombre)
    if codigo is None:
        return "desconocido"
    texto = salida.upper()
    if "AUTO_START" in texto:
        return "auto"
    if "DEMAND_START" in texto:
        return "manual"
    if "DISABLED" in texto:
        return "deshabilitado"
    return "desconocido"


def poner_en_automatico(nombre: str = NOMBRE_SERVICIO) -> tuple:
    """Deja el servicio en Automatic + reintentos de Windows. (ok, detalle).

    No hay ningún motivo para que esté en Manual: pywin32 lo registra así
    por defecto y eso fue un bug del instalador, no una decisión de nadie.
    """
    codigo, salida = _sc("config", nombre, "start=", "auto")
    if codigo != 0:
        return False, (salida.strip() or "sin detalle") + " (¿falta ejecutar como administrador?)"
    # Que Windows lo reintente solo si se cae: sin esto, un cuelgue puntual
    # lo deja parado hasta que alguien pase por el local.
    _sc("failure", nombre, "reset=", "86400",
        "actions=", "restart/60000/restart/60000/restart/60000")
    return True, "arranque automático + reintentos configurados"


def arrancar(nombre: str = NOMBRE_SERVICIO, espera_s: int = 12) -> tuple:
    """Arranca el servicio y ESPERA a confirmar que quedó corriendo.

    Mandar 'sc start' y dar por hecho que arrancó es una verificación que
    no verifica: sc vuelve enseguida, el servicio puede morir un segundo
    después y el informe diría que está todo bien.
    """
    codigo, salida = _sc("start", nombre)
    if codigo is None:
        return False, salida.strip() or "no se pudo ejecutar sc.exe"
    for _ in range(max(espera_s, 1)):
        if estado(nombre) == "corriendo":
            return True, "corriendo"
        _dormir(1)
    return False, (salida.strip() or "sigue sin quedar corriendo")


def puerto_escuchando(puerto: int = PUERTO_POR_DEFECTO, host: str = "127.0.0.1") -> bool:
    """¿Hay alguien escuchando de verdad en el puerto de la API remota?

    Es lo ÚNICO que le importa al Dueño Remoto, y no es lo mismo que "el
    servicio dice Running": la API se levanta adentro del servicio con
    iniciar_si_esta_habilitado(), que loguea y sigue si falla. El servicio
    puede estar Running y no haber nadie escuchando — para Leo eso es
    exactamente "no me puedo conectar".
    """
    try:
        with socket.create_connection((host, int(puerto)), timeout=3):
            return True
    except Exception:
        return False


# ===================================================================== #
# Lo que se agregó para la API del celular (ApiCelular, puerto 8766).
#
# Mismo criterio que lo de arriba: NINGUNA función lanza (salvo
# reemplazar_carpeta, que la usa el Actualizador adentro de su try), y las
# preguntas a Windows se hacen por cosas que no dependen del idioma: números
# de estado, enums de PowerShell, JSON. Nunca netstat ni texto traducido.
# ===================================================================== #

def _win32service():
    """El módulo win32service si está (pywin32), o None.

    Va en una función propia para que las pruebas puedan simular "sin
    pywin32" y que se use el camino de sc.exe/registro, que es el que queda
    si al .exe le faltara el --hidden-import: un olvido degrada, no rompe.
    """
    if not _ES_WINDOWS:
        return None
    try:
        import win32service
        return win32service
    except Exception:
        return None


def _powershell(script: str, timeout: int = 60):
    """Corre un script de PowerShell 5.1 y devuelve (codigo, salida). (None, "") si no se pudo.

    La salida se pide en UTF-8: si no, un nombre de regla del firewall con
    acento llega en la codepage de la consola y se rompe.
    """
    if not _ES_WINDOWS:
        return None, ""
    completo = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-Command", completo],
                           capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           **_SIN_VENTANA)
        salida = r.stdout or b""
        try:
            texto = salida.decode("utf-8")
        except UnicodeDecodeError:
            texto = salida.decode("cp850", errors="replace")
        return r.returncode, texto
    except Exception as e:
        return None, str(e)


def _comillas_ps(texto: str) -> str:
    """Un texto adentro de comillas simples de PowerShell ('' escapa la ')."""
    return "'" + str(texto).replace("'", "''") + "'"


def _lineas(salida: str) -> list:
    return [l.strip() for l in (salida or "").splitlines() if l.strip()]


def _lista_de_powershell(script: str, timeout: int, estricto: bool):
    """Las líneas que escribió el script, o — si PowerShell no contestó (error,
    tiempo agotado, no es Windows) — [] o None según `estricto`.

    El [] de siempre no distingue "no hay ninguna" de "no se pudo preguntar":
    para un informe eso es un SI que no verificó nada. Quien da un SI/NO
    (la revisión final) pide estricto=True y trata el None como NO.
    """
    try:
        codigo, salida = _powershell(script, timeout=timeout)
        if codigo == 0:
            return _lineas(salida)
    except Exception:
        pass
    return None if estricto else []


# --------------------------------------------------------------------- #
# ¿Quién contesta en el puerto? Por IDENTIDAD, no porque el TCP conecte.
# --------------------------------------------------------------------- #

def _pedir_json(puerto: int, ruta: str, *, host="127.0.0.1", timeout=4) -> tuple:
    """GET http://host:puerto/ruta -> (status | None, dict | None). Nunca lanza.

    Sin proxy A PROPÓSITO: en Windows urllib lee el proxy del registro, y
    un pedido a 127.0.0.1 que sale por un proxy corporativo da cualquier
    cosa. Lee como mucho 64 KB. Un 4xx con cuerpo JSON también se devuelve
    (el remote_api contesta 401 a /health sin token y eso ES la prueba de
    que está vivo).
    """
    import urllib.error
    import urllib.request
    url = f"http://{host}:{int(puerto)}{ruta}"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=timeout) as r:
                status, cuerpo = r.status, r.read(65536)
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                cuerpo = e.read(65536)
            except Exception:
                cuerpo = b""
    except Exception:
        return None, None
    try:
        datos = json.loads(cuerpo.decode("utf-8"))
    except Exception:
        datos = None
    return status, (datos if isinstance(datos, dict) else None)


def salud_api_celular(puerto: int = PUERTO_CELULAR) -> dict | None:
    """El JSON de /api/salud si el que contesta ES la API del celular; si no, None."""
    try:
        _status, datos = _pedir_json(puerto, "/api/salud")
        if datos and datos.get("servicio") == FIRMA_CELULAR:
            return datos
    except Exception:
        pass
    return None


def api_celular_contesta(puerto: int = PUERTO_CELULAR) -> tuple:
    """(ok, detalle). ok solo si en el puerto contesta la API del celular con su firma.

    Que el puerto conecte no alcanza: ya pasó que otro programa en el
    puerto engañó al diagnóstico. Por eso se pregunta /api/salud y se mira
    el campo "servicio".
    """
    try:
        salud = salud_api_celular(puerto)
        if salud is not None:
            base = salud.get("base")
            if isinstance(base, dict) and base.get("ok") is False:
                return True, "contesta, pero no encuentra la base"
            return True, "contesta la API del celular"
        if not puerto_escuchando(puerto):
            return False, "no hay nadie escuchando"
        quien = quien_escucha(puerto)
        if quien.lower().startswith("apicelular"):
            # Es ella, pero no contesta la salud: proceso vivo y colgado.
            return False, f"{quien} tiene el puerto pero no contesta (¿colgada?)"
        return False, f"contesta OTRO programa ({quien or 'no se pudo saber cuál'})"
    except Exception as e:
        return False, f"no se pudo preguntar: {e}"


def remote_api_contesta(puerto: int = PUERTO_POR_DEFECTO) -> tuple:
    """(ok, detalle). ¿En el puerto está la API remota de Otter (la del Dueño Remoto)?

    La API remota no se toca para esto: se la reconoce porque /health SIN
    token contesta 401 {"ok": false, "error": "token inválido"}. Se compara
    el comienzo del texto ("token inv") para no depender del acento.
    """
    try:
        status, datos = _pedir_json(puerto, "/health")
        if status == 401 and datos and datos.get("ok") is False and \
                str(datos.get("error", "")).startswith("token inv"):
            return True, "contesta la API remota"
        if not puerto_escuchando(puerto):
            return False, "no hay nadie escuchando"
        quien = quien_escucha(puerto)
        return False, f"contesta OTRO programa ({quien or 'no se pudo saber cuál'})"
    except Exception as e:
        return False, f"no se pudo preguntar: {e}"


def identificar_puerto(puerto: int) -> str:
    """'nadie' | 'remote_api' | 'api_celular' | 'otro'."""
    try:
        if salud_api_celular(puerto) is not None:
            return "api_celular"
        ok, _ = remote_api_contesta(puerto)
        if ok:
            return "remote_api"
        return "otro" if puerto_escuchando(puerto) else "nadie"
    except Exception:
        return "otro"


def quien_escucha(puerto: int) -> str:
    """'ApiCelular (PID 1234)' o '' si no hay nadie o no se puede saber.

    Get-NetTCPConnection devuelve el estado como enum (no se traduce) y el
    PID dueño del socket: nada de netstat, cuya salida cambia con el idioma.
    """
    try:
        codigo, salida = _powershell(
            f"$c = Get-NetTCPConnection -LocalPort {int(puerto)} -State Listen -ErrorAction Stop "
            f"| Select-Object -First 1\n"
            "$p = Get-Process -Id $c.OwningProcess -ErrorAction Stop\n"
            "Write-Output \"$($p.ProcessName) (PID $($p.Id))\"", timeout=30)
        if codigo != 0:
            return ""
        lineas = _lineas(salida)
        return lineas[0] if lineas else ""
    except Exception:
        return ""


# --------------------------------------------------------------------- #
# Estado del servicio con PID, y pararlo DE VERDAD
# --------------------------------------------------------------------- #

# Números de estado de un servicio (SERVICE_STATUS.dwCurrentState). Son los
# mismos en cualquier idioma, igual que las palabras en mayúscula de sc.exe.
_ESTADOS_SCM = {1: "parado", 2: "arrancando", 3: "parando", 4: "corriendo",
                5: "arrancando", 6: "desconocido", 7: "desconocido"}


def estado_y_pid(nombre: str) -> tuple:
    """(estado, pid). estado: 'corriendo' | 'parado' | 'arrancando' | 'parando' |
    'no_instalado' | 'desconocido'.

    A diferencia de estado(), STOP_PENDING es "parando", NO "parado": con el
    proceso todavía vivo el .exe y sus DLL siguen bloqueados, y renombrar la
    carpeta para actualizarla falla. estado() no se toca porque la usan el
    Mantenimiento y la revisión final del servicio de stock.
    """
    w = _win32service()
    if w is not None:
        try:
            scm = w.OpenSCManager(None, None, w.SC_MANAGER_CONNECT)
            try:
                try:
                    h = w.OpenService(scm, nombre, w.SERVICE_QUERY_STATUS)
                except Exception as e:
                    if getattr(e, "winerror", None) == 1060:   # no existe
                        return "no_instalado", 0
                    raise
                try:
                    st = w.QueryServiceStatusEx(h)
                finally:
                    w.CloseServiceHandle(h)
            finally:
                w.CloseServiceHandle(scm)
            return _ESTADOS_SCM.get(int(st["CurrentState"]), "desconocido"), int(st.get("ProcessId") or 0)
        except Exception:
            pass   # se cae a sc.exe
    try:
        codigo, salida = _sc("queryex", nombre)
        if codigo is None:
            return "desconocido", 0
        texto = salida.upper()
        m = re.search(r"PID\s*:\s*(\d+)", texto)
        pid = int(m.group(1)) if m else 0
        if "STOP_PENDING" in texto:
            return "parando", pid
        if "START_PENDING" in texto or "CONTINUE_PENDING" in texto:
            return "arrancando", pid
        if "RUNNING" in texto:
            return "corriendo", pid
        if "STOPPED" in texto:
            return "parado", pid
        if "1060" in texto:
            return "no_instalado", 0
        return "desconocido", pid
    except Exception:
        return "desconocido", 0


def parar(nombre: str, espera_s: int = 30) -> tuple:
    """(ok, detalle). Para el servicio y ESPERA a que el proceso no exista más.

    "parando" NO es parado: sc stop vuelve enseguida, el proceso puede
    tardar en soltar el .exe, y renombrar la carpeta con el .exe abierto
    falla a mitad de una actualización. OK solo con ('parado', PID 0) o si
    el servicio no existe.
    """
    try:
        if not _ES_WINDOWS:
            return False, "solo en Windows"
        est, pid = estado_y_pid(nombre)
        if est == "no_instalado":
            return True, "no está instalado"
        if est == "parado" and pid == 0:
            return True, "ya estaba parado"
        codigo, salida = _sc("stop", nombre)
        if codigo is None:
            return False, salida.strip() or "no se pudo ejecutar sc.exe"
        limite = time.monotonic() + max(espera_s, 0)
        while True:
            est, pid = estado_y_pid(nombre)
            if est == "no_instalado" or (est == "parado" and pid == 0):
                return True, "parado"
            if time.monotonic() >= limite:
                break
            _dormir(1)
        detalle = f"sigue {est}" + (f" (PID {pid})" if pid else "")
        if codigo != 0 and salida.strip():
            detalle += f": {salida.strip()[:200]}"
        return False, detalle + " (¿falta ejecutar como administrador?)"
    except Exception as e:
        return False, f"no se pudo parar: {e}"


def reemplazar_carpeta(origen_app: str, destino_app: str, ignorar) -> None:
    """Reemplaza la carpeta de un programa. LA ÚNICA FUNCIÓN DE ESTE MÓDULO QUE LANZA.

    La vieja se mueve a <destino>.anterior (no se borra: si la copia falla
    a la mitad, hay con qué volver). El rename se reintenta hasta 10 veces
    ante PermissionError: un proceso que acaba de parar tarda en soltar el
    .exe y las DLL. Si la copia falla, se repone .anterior y se relanza el
    error. Si sale bien, .anterior queda para que quien llama devuelva los
    datos del cliente que vivieran adentro y después la borre.
    """
    anterior = destino_app + ".anterior"
    if os.path.exists(anterior):
        shutil.rmtree(anterior, ignore_errors=True)
    if os.path.isdir(destino_app):
        for intento in range(10):
            try:
                os.rename(destino_app, anterior)
                break
            except PermissionError:
                if intento == 9:
                    raise
                _dormir(0.5)
    try:
        shutil.copytree(origen_app, destino_app, ignore=ignorar)
    except Exception:
        if os.path.isdir(anterior):
            if os.path.exists(destino_app):
                shutil.rmtree(destino_app, ignore_errors=True)
            if not os.path.exists(destino_app):
                os.rename(anterior, destino_app)   # volver a la anterior
        raise


def _decodificar_failure_actions(datos: bytes) -> bool | None:
    """¿El valor binario FailureActions del registro tiene algún reinicio?

    Es un SERVICE_FAILURE_ACTIONS guardado tal cual: cActions en el offset
    12 y las acciones (SC_ACTION, 8 bytes: Type y Delay) desde el offset 20.
    Type 1 = SC_ACTION_RESTART. None si el binario no tiene esa forma.
    """
    import struct
    try:
        if len(datos) < 20:
            return None
        cantidad = struct.unpack_from("<I", datos, 12)[0]
        if cantidad > 64 or len(datos) < 20 + 8 * cantidad:
            return None
        return any(struct.unpack_from("<I", datos, 20 + 8 * i)[0] == 1 for i in range(cantidad))
    except Exception:
        return None


def reintentos_configurados(nombre: str) -> bool | None:
    """True si Windows reinicia el servicio solo cuando se cae. None si no se puede leer.

    `remove` + `install` borra los reintentos sin avisar (pasaba en cada
    actualización del servicio de stock): por eso se verifica leyendo, no
    se da por hecho porque el comando que los pone no tiró error.
    """
    if not _ES_WINDOWS:
        return None
    w = _win32service()
    if w is not None:
        try:
            scm = w.OpenSCManager(None, None, w.SC_MANAGER_CONNECT)
            try:
                h = w.OpenService(scm, nombre, w.SERVICE_QUERY_CONFIG)
                try:
                    cfg = w.QueryServiceConfig2(h, w.SERVICE_CONFIG_FAILURE_ACTIONS)
                finally:
                    w.CloseServiceHandle(h)
            finally:
                w.CloseServiceHandle(scm)
            acciones = (cfg or {}).get("Actions") or []
            return any(int(a[0]) == 1 for a in acciones)
        except Exception:
            pass   # se cae al registro
    try:
        import winreg
        clave = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               rf"SYSTEM\CurrentControlSet\Services\{nombre}")
        try:
            try:
                datos, _tipo = winreg.QueryValueEx(clave, "FailureActions")
            except FileNotFoundError:
                return False   # el servicio existe pero nunca se le pusieron acciones
        finally:
            winreg.CloseKey(clave)
        return _decodificar_failure_actions(bytes(datos))
    except Exception:
        return None


def _ruta_del_exe_registrada(nombre: str) -> str:
    """Con qué .exe arranca Windows el servicio. '' si no se puede saber.

    Ojo con el idioma: en la PC del local `sc.exe qc` dice
    "NOMBRE_RUTA_BINARIO" y no "BINARY_PATH_NAME" (igual que dice
    "TIPO_INICIO"). Buscar la etiqueta en inglés fallaría SIEMPRE ahí, como
    pasó con "Index" en powercfg. Por eso: pywin32, el registro (ImagePath)
    y, como último recurso, la salida ENTERA de sc.exe qc sin buscar
    ninguna etiqueta (quien llama solo mira si la ruta aparece).
    """
    w = _win32service()
    if w is not None:
        try:
            scm = w.OpenSCManager(None, None, w.SC_MANAGER_CONNECT)
            try:
                h = w.OpenService(scm, nombre, w.SERVICE_QUERY_CONFIG)
                try:
                    return str(w.QueryServiceConfig(h)[3] or "")
                finally:
                    w.CloseServiceHandle(h)
            finally:
                w.CloseServiceHandle(scm)
        except Exception:
            pass
    if _ES_WINDOWS:
        try:
            import winreg
            clave = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                   rf"SYSTEM\CurrentControlSet\Services\{nombre}")
            try:
                return str(winreg.QueryValueEx(clave, "ImagePath")[0] or "")
            finally:
                winreg.CloseKey(clave)
        except Exception:
            pass
    codigo, salida = _sc("qc", nombre)
    return "" if codigo is None else salida


def instalar_servicio(exe: str, nombre: str) -> tuple:
    """(ok, detalle). Registra (o actualiza) el servicio de la API del celular.

    Solo para ApiCelular: el servicio de stock sigue con su código de hoy.

    - Si no existe: `<exe> --startup auto install`.
    - Si YA existe: `<exe> --startup auto update` (cambia la ruta del .exe y
      deja el mismo servicio). NUNCA stop + remove + install: con el proceso
      todavía vivo, remove lo deja "marcado para borrar" (error 1072), el
      install falla y el servicio desaparece hasta reiniciar; y remove borra
      los reintentos.
    - Si está Deshabilitado en Windows no se toca: es una decisión de
      alguien (la forma documentada de apagarla es [api_celular]
      habilitado = false, pero tampoco se le deshace lo que hizo).

    OK solo si DESPUÉS de todo está en automático, corriendo, y el registro
    apunta a este .exe (ver _ruta_del_exe_registrada: sin depender del
    idioma de sc.exe).
    """
    try:
        if not _ES_WINDOWS:
            return False, "solo en Windows"
        if tipo_de_arranque(nombre) == "deshabilitado":
            return False, "deshabilitada en Windows a propósito: no se toca"
        est, _pid = estado_y_pid(nombre)
        verbo = "install" if est == "no_instalado" else "update"
        try:
            r = subprocess.run([exe, "--startup", "auto", verbo], capture_output=True, text=True,
                               timeout=120, stdin=subprocess.DEVNULL, **_SIN_VENTANA)
            salida_cmd = f"{r.stdout or ''} {r.stderr or ''}".strip()
            codigo_cmd = r.returncode
        except Exception as e:
            salida_cmd, codigo_cmd = str(e), None
        poner_en_automatico(nombre)
        # failureflag 1: que los reintentos corran también si el servicio
        # termina con error sin "caerse" (por ejemplo el os._exit(3) del
        # autochequeo de la API).
        _sc("failureflag", nombre, "1")
        arrancar(nombre)

        problemas = []
        if tipo_de_arranque(nombre) != "auto":
            problemas.append("no quedó en arranque automático")
        if estado_y_pid(nombre)[0] != "corriendo":
            problemas.append("no quedó corriendo")
        registrada = _ruta_del_exe_registrada(nombre).replace("/", "\\").lower()
        if not registrada or exe.replace("/", "\\").lower() not in registrada:
            problemas.append("el servicio registrado no apunta a este ApiCelular.exe")
        if problemas:
            detalle = "; ".join(problemas)
            if codigo_cmd != 0 and salida_cmd:
                detalle += f" ({verbo}: {salida_cmd[:300]})"
            return False, detalle
        return True, f"{'instalado' if verbo == 'install' else 'actualizado'}, en automático y corriendo"
    except Exception as e:
        return False, f"no se pudo instalar: {e}"


# --------------------------------------------------------------------- #
# Firewall: el 8766 solo para Tailscale, y solo por la placa de Tailscale
# --------------------------------------------------------------------- #

_RANGO_TAILSCALE = ("100.64.0.0/10", "100.64.0.0/255.192.0.0")


def placa_tailscale() -> str:
    """Alias de la placa de Tailscale (InterfaceDescription 'Tailscale...'), o ''."""
    try:
        codigo, salida = _powershell(
            "Get-NetAdapter -IncludeHidden -ErrorAction Stop | "
            "Where-Object { $_.InterfaceDescription -like 'Tailscale*' } | "
            "Select-Object -First 1 -ExpandProperty Name", timeout=30)
        if codigo != 0:
            return ""
        lineas = _lineas(salida)
        return lineas[0] if lineas else ""
    except Exception:
        return ""


def asegurar_regla_firewall_celular(exe: str, puerto: int = PUERTO_CELULAR,
                                    interfaz: str = None) -> tuple:
    """(ok, detalle). Crea/rehace la regla Allow propia y la VERIFICA releyéndola.

    Limitada al rango de Tailscale, al .exe Y a la placa de Tailscale: sin
    -InterfaceAlias, una placa de red con IP CGNAT (módem en puente, común
    en Argentina) abriría el puerto a otros clientes del mismo proveedor, y
    el filtro propio de la API no lo ve porque esa IP también está en el
    rango. `interfaz` existe para la prueba del CI, que no tiene Tailscale.
    """
    try:
        if not _ES_WINDOWS:
            return False, "solo en Windows"
        if interfaz is None:
            interfaz = placa_tailscale()
        if not interfaz:
            return False, "no encontré la placa de Tailscale"
        nombre = _comillas_ps(REGLA_FIREWALL_CELULAR)
        # El error de Windows se devuelve tal cual (ERROR_REGLA: ...): antes se
        # lo tragaba y siempre decía "¿falta ejecutar como administrador?", que
        # en el CI resultó falso (la placa elegida era la que no servía) y en
        # el local mandaría a buscar el problema donde no está.
        script = (
            f"Remove-NetFirewallRule -Name {nombre} -ErrorAction SilentlyContinue\n"
            "try {\n"
            f"  New-NetFirewallRule -Name {nombre} -DisplayName 'Otter API del celular (solo Tailscale)' "
            f"-Direction Inbound -Action Allow -Protocol TCP -LocalPort {int(puerto)} "
            f"-RemoteAddress 100.64.0.0/10 -Program {_comillas_ps(exe)} "
            f"-InterfaceAlias {_comillas_ps(interfaz)} -Profile Any -ErrorAction Stop | Out-Null\n"
            "} catch { 'ERROR_REGLA: ' + $_.Exception.Message; exit 1 }\n"
            f"$r = Get-NetFirewallRule -Name {nombre} -ErrorAction Stop\n"
            "[pscustomobject]@{\n"
            "  remota = (@(($r | Get-NetFirewallAddressFilter).RemoteAddress) -join ',');\n"
            "  puerto = (@(($r | Get-NetFirewallPortFilter).LocalPort) -join ',');\n"
            "  interfaz = (@(($r | Get-NetFirewallInterfaceFilter).InterfaceAlias) -join ',');\n"
            "  habilitada = [string]$r.Enabled\n"
            "} | ConvertTo-Json -Compress\n")
        codigo, salida = _powershell(script, timeout=90)
        lineas = _lineas(salida)
        if codigo != 0 or not lineas:
            error = next((l[len("ERROR_REGLA:"):].strip() for l in lineas if l.startswith("ERROR_REGLA:")), "")
            if error:
                return False, f"Windows no dejó crear la regla: {error[:300]}"
            return False, "no se pudo crear la regla (¿falta ejecutar como administrador?)"
        try:
            leido = json.loads(lineas[-1])
        except Exception:
            return False, "la regla no se pudo releer"
        problemas = []
        if str(leido.get("remota", "")) not in _RANGO_TAILSCALE:
            problemas.append("no quedó limitada al rango de Tailscale")
        if str(leido.get("puerto", "")) != str(int(puerto)):
            problemas.append(f"no quedó en el puerto {int(puerto)}")
        if str(leido.get("interfaz", "")).lower() != str(interfaz).lower():
            problemas.append("no quedó atada a la placa de Tailscale")
        if str(leido.get("habilitada", "")).lower() != "true":
            problemas.append("quedó deshabilitada")
        if problemas:
            return False, "regla propia: " + "; ".join(problemas)
        return True, f"regla propia OK (placa «{interfaz}»)"
    except Exception as e:
        return False, f"no se pudo revisar el firewall: {e}"


# Mapas InstanceID -> filtro, armados una vez: preguntar regla por regla
# (cientos de reglas x tres cmdlets) tarda minutos.
_PS_FILTROS = (
    "$apps = @{}; Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | "
    "ForEach-Object { $apps[$_.InstanceID] = $_.Program }\n"
)


def reglas_que_bloquean(exe: str, estricto: bool = False) -> list:
    """Reglas Inbound Block habilitadas sobre el .exe (las crea el "Cancelar" del cartel
    del firewall). Block le gana a Allow. Nombres visibles; [] si no hay o no se puede
    saber (con estricto=True, None si no se pudo saber)."""
    try:
        script = (
            f"$exe = {_comillas_ps(exe)}\n" + _PS_FILTROS +
            "Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True -ErrorAction SilentlyContinue | "
            "Where-Object { $apps[$_.InstanceID] -eq $exe } | "
            "ForEach-Object { Write-Output $_.DisplayName }\n")
        return _lista_de_powershell(script, 120, estricto)
    except Exception:
        return None if estricto else []


def reglas_que_abren_de_mas(exe: str, puerto: int = PUERTO_CELULAR, estricto: bool = False) -> list:
    """Reglas Inbound Allow habilitadas (no la nuestra) sobre el .exe o sobre el puerto,
    que NO están limitadas al rango de Tailscale. El "Permitir" del cartel crea una así.
    [] si no hay o no se puede saber (con estricto=True, None si no se pudo saber)."""
    try:
        script = (
            f"$exe = {_comillas_ps(exe)}; $puerto = {int(puerto)}\n"
            f"$propia = {_comillas_ps(REGLA_FIREWALL_CELULAR)}\n"
            "$rango = @('100.64.0.0/10', '100.64.0.0/255.192.0.0')\n" + _PS_FILTROS +
            "$puertos = @{}; Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | "
            "ForEach-Object { $puertos[$_.InstanceID] = @($_.LocalPort) }\n"
            "$remotas = @{}; Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue | "
            "ForEach-Object { $remotas[$_.InstanceID] = @($_.RemoteAddress) }\n"
            "function TocaPuerto($lista) {\n"
            "  foreach ($p in @($lista)) {\n"
            "    if ([string]$p -eq [string]$puerto) { return $true }\n"
            "    if ([string]$p -match '^(\\d+)-(\\d+)$' -and [int]$matches[1] -le $puerto -and [int]$matches[2] -ge $puerto) { return $true }\n"
            "  }\n"
            "  return $false\n"
            "}\n"
            "Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Name -ne $propia } | ForEach-Object {\n"
            "  $id = $_.InstanceID\n"
            "  if (($apps[$id] -eq $exe) -or (TocaPuerto $puertos[$id])) {\n"
            "    $r = @($remotas[$id])\n"
            "    $limitada = ($r.Count -gt 0) -and -not ($r | Where-Object { $rango -notcontains $_ })\n"
            "    if (-not $limitada) { Write-Output $_.DisplayName }\n"
            "  }\n"
            "}\n")
        return _lista_de_powershell(script, 180, estricto)
    except Exception:
        return None if estricto else []


def perfiles_firewall_apagados(estricto: bool = False) -> list:
    """Perfiles del firewall (Domain/Private/Public) apagados. Con el firewall apagado,
    nuestra regla no limita nada: queda solo el filtro propio de la API.
    (con estricto=True, None si no se pudo saber)."""
    try:
        return _lista_de_powershell(
            "Get-NetFirewallProfile -ErrorAction Stop | Where-Object { [string]$_.Enabled -eq 'False' } | "
            "ForEach-Object { Write-Output $_.Name }", 30, estricto)
    except Exception:
        return None if estricto else []


def placas_lan_en_rango_tailscale(estricto: bool = False) -> list:
    """Alias de placas que NO son la de Tailscale y tienen una IPv4 en 100.64.0.0/10.

    Solo alias, nunca la IP: esto termina en una tabla que se fotografía.
    (con estricto=True, None si no se pudo saber).
    """
    try:
        return _lista_de_powershell(
            "$ts = @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | "
            "Where-Object { $_.InterfaceDescription -like 'Tailscale*' } | ForEach-Object { $_.ifIndex })\n"
            "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | "
            "Where-Object { $ts -notcontains $_.InterfaceIndex } | Where-Object {\n"
            "  $o = $_.IPAddress.Split('.'); [int]$o[0] -eq 100 -and [int]$o[1] -ge 64 -and [int]$o[1] -le 127\n"
            "} | ForEach-Object { $_.InterfaceAlias } | Select-Object -Unique | "
            "ForEach-Object { Write-Output $_ }", 30, estricto)
    except Exception:
        return None if estricto else []


# --------------------------------------------------------------------- #
# Tarea programada del watchdog, acceso directo del PIN, restos del viejo
# --------------------------------------------------------------------- #

def estado_tarea(nombre: str) -> dict | None:
    """{"existe", "deshabilitada", "ultimo_resultado", "minutos_desde_ultima"}, o None.

    La cuenta de minutos y el "deshabilitada" se calculan ADENTRO de
    PowerShell y vuelven como JSON: así no se parsean fechas en el formato
    regional de la PC ni nombres de estado traducidos.
    """
    try:
        script = (
            f"$t = Get-ScheduledTask -TaskName {_comillas_ps(nombre)} -ErrorAction SilentlyContinue\n"
            "if ($null -eq $t) { Write-Output '{\"existe\": false}'; exit 0 }\n"
            f"$i = Get-ScheduledTaskInfo -TaskName {_comillas_ps(nombre)} -ErrorAction Stop\n"
            "$min = $null\n"
            "if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { "
            "$min = [int][math]::Floor(((Get-Date) - $i.LastRunTime).TotalMinutes) }\n"
            "[pscustomobject]@{ existe = $true; deshabilitada = ([string]$t.State -eq 'Disabled'); "
            "ultimo_resultado = [int64]$i.LastTaskResult; minutos_desde_ultima = $min } | "
            "ConvertTo-Json -Compress\n")
        codigo, salida = _powershell(script, timeout=30)
        lineas = _lineas(salida)
        if codigo != 0 or not lineas:
            return None
        datos = json.loads(lineas[-1])
        return {"existe": bool(datos.get("existe")),
                "deshabilitada": bool(datos.get("deshabilitada", False)),
                "ultimo_resultado": datos.get("ultimo_resultado"),
                "minutos_desde_ultima": datos.get("minutos_desde_ultima")}
    except Exception:
        return None


def acceso_directo_pin_celular(exe: str) -> tuple:
    """(ok, detalle). "Otter - PIN del celular" en el menú Inicio de todos los usuarios.

    Apunta a `ApiCelular.exe definir-pin --pausa`. Existe para que Leo pueda
    cambiar el PIN que olvidó sin consola de administrador ni Matías. Si ya
    existe se pisa: la ruta del .exe puede haber cambiado.
    """
    try:
        if not _ES_WINDOWS:
            return False, "solo en Windows"
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        try:
            programas = str(shell.SpecialFolders("AllUsersPrograms") or "")
        except Exception:
            programas = ""
        if not programas:
            programas = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                                     "Microsoft", "Windows", "Start Menu", "Programs")
        carpeta = os.path.join(programas, "Otter")
        os.makedirs(carpeta, exist_ok=True)
        ruta = os.path.join(carpeta, "Otter - PIN del celular.lnk")
        acceso = shell.CreateShortCut(ruta)
        acceso.TargetPath = exe
        acceso.Arguments = "definir-pin --pausa"
        acceso.WorkingDirectory = os.path.dirname(exe)
        acceso.IconLocation = exe
        acceso.Description = "Cambiar el PIN de la app del celular"
        acceso.save()
        if not os.path.isfile(ruta):
            return False, "se mandó a crear pero no aparece en el menú Inicio"
        return True, "menú Inicio > Otter > «Otter - PIN del celular»"
    except Exception as e:
        return False, f"no se pudo crear el acceso directo: {e}"


def restos_api_dueno() -> list | None:
    """Lo que quede del ApiDueno viejo (el programa descartado): procesos y tareas.

    Solo informa, nunca borra: lista de textos ("proceso ApiDueno (PID 12)",
    "tarea «X» que corre ApiDueno"), [] si no queda nada, None si no se
    puede preguntar. El puerto 8765 lo mira quien llama con quien_escucha.
    """
    try:
        if not _ES_WINDOWS:
            return None
        codigo, salida = _powershell(
            "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'ApiDueno*' } | "
            "ForEach-Object { Write-Output \"proceso $($_.ProcessName) (PID $($_.Id))\" }\n"
            "Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { "
            "@($_.Actions | Where-Object { \"$($_.Execute) $($_.Arguments)\" -like '*ApiDueno*' }).Count -gt 0 } | "
            "ForEach-Object { Write-Output \"tarea $($_.TaskName) que corre ApiDueno\" }\n", timeout=60)
        if codigo != 0:
            return None
        return _lineas(salida)
    except Exception:
        return None
