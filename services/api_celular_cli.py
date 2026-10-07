"""Verbos de consola de ApiCelular.exe: definir-pin, cerrar-sesiones,
habilitar, deshabilitar, diagnostico y consola.

La base ya la fijó services/api_celular_servicio.py (--base, o la carpeta
padre del exe). Todos escriben en logs\\api_celular_cli.log y NUNCA en el
log del servicio: en Windows rotar un archivo que tiene abierto otro
proceso falla y crece sin límite.

Regla 4: nada de esto imprime el PIN, tokens ni IPs. Una captura de esta
consola es un chat igual.
"""

import getpass
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from pos_core import acceso_celular, config, db, panel_celular, paths

TEXTO_PIN_ARGUMENTO = ("No escribas el PIN en la línea de comandos: queda en el historial de PowerShell. "
                       "Corré solo «ApiCelular.exe definir-pin» y escribilo cuando lo pida.")
TEXTO_CONSOLA = ("Solo para desarrollo. En la PC del local NO: si aparece el cartel del firewall y alguien "
                 "aprieta Cancelar, Windows bloquea ApiCelular para siempre.")
REGLA_FIREWALL = "OtterApiCelular-Tailscale"
TAREA_WATCHDOG = "OtterWatchdogCelular"
_RE_IPV4 = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")


def tapar_ips(texto: str) -> str:
    return _RE_IPV4.sub("x.x.x.x", texto)


def _log():
    from services import api_celular
    return api_celular.log


def _configurar_logs() -> None:
    # Solo si la carpeta de la instalación existe: un --base equivocado no
    # puede terminar creando carpetas en cualquier lado.
    if os.path.isdir(paths.get_base_path()):
        from services import api_celular
        api_celular.configurar_logs("cli")


def _pausar() -> None:
    try:
        input("Apretá Enter para cerrar esta ventana.")
    except (EOFError, KeyboardInterrupt):
        pass


# --------------------------------------------------------------------- #
# definir-pin
# --------------------------------------------------------------------- #

def definir_pin(args: list) -> int:
    pausa = "--pausa" in args
    resto = [a for a in args if a != "--pausa"]
    try:
        if resto:
            print(TEXTO_PIN_ARGUMENTO)
            return 2
        ruta = panel_celular.ruta_base_datos()
        if not os.path.isfile(ruta):
            # No crea NADA: una base vacía en la carpeta equivocada es la trampa de la regla 2.
            print(f"No encontré la base en {ruta}. ApiCelular tiene que estar en C:\\SistemaDual\\ApiCelular.")
            return 2
        _configurar_logs()
        db.usar_solo_base_existente(ruta)
        for _ in range(3):
            pin = getpass.getpass("PIN nuevo para la app del celular (6 a 12 números, no se ve al escribir): ")
            try:
                acceso_celular.validar_pin_nuevo(pin)
            except ValueError as e:
                print(e)
                continue
            if getpass.getpass("Repetilo: ") != pin:
                print("Los dos PIN no coinciden. Probá de nuevo.")
                continue
            try:
                acceso_celular.definir_pin_dueno(pin)
            except acceso_celular.SecretoIlegibleError:
                _log().error("definir-pin: secreto.json ilegible, no se tocó nada")
                print("El archivo de seguridad de la API (api_celular\\secreto.json) está dañado: no se guardó "
                      "nada. Hay que revisarlo en la PC.")
                return 1
            finally:
                pin = None
            _log().info("PIN del celular definido desde la consola")
            print("PIN guardado. Los celulares que estaban conectados tienen que volver a ingresar el PIN.")
            return 0
        print("No se guardó ningún PIN (tres intentos).")
        return 1
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C, o la ventana sin teclado (stdin cerrado): no se guarda nada.
        print("\nCancelado: no se guardó nada.")
        return 1
    finally:
        db.cerrar_conexion()
        if pausa:
            _pausar()


# --------------------------------------------------------------------- #
# cerrar-sesiones, habilitar, deshabilitar
# --------------------------------------------------------------------- #

def cerrar_sesiones() -> int:
    _configurar_logs()
    try:
        secreto = acceso_celular.leer_secreto()
    except acceso_celular.SecretoIlegibleError:
        print("El archivo de seguridad de la API (api_celular\\secreto.json) está dañado: no se tocó nada.")
        return 1
    if secreto is None:
        print("No hay sesiones abiertas (todavía no se definió ningún PIN).")
        return 0
    acceso_celular.rotar_firma()
    _log().info("cerrar-sesiones: se rotó la firma de las sesiones")
    print("Listo: todos los celulares tienen que volver a ingresar el PIN.")
    return 0


def cambiar_habilitado(habilitado: bool) -> int:
    """El ÚNICO interruptor de la API (D21). Editar config.ini a mano tiene dos
    trampas verificadas: un comentario al final de la línea cambia lo que se
    lee, y un Bloc de notas que lo guarde con BOM hace que ninguna app lo pueda
    leer (se cae también la Caja)."""
    _configurar_logs()
    try:
        config.cargar_config(estricto=True)
    except config.ConfigIlegibleError:
        print("No se pudo leer config.ini: no se tocó nada.")
        return 1
    config.actualizar_config_dict({"api_celular": {"habilitado": "true" if habilitado else "false"}})
    if config.leer_config_celular()["habilitado"] != habilitado:
        print("Se guardó config.ini pero al releerlo no quedó como se pidió: revisalo.")
        return 1
    _log().info("[api_celular] habilitado = %s (desde la consola)", "true" if habilitado else "false")
    if habilitado:
        print("Listo: la API del celular vuelve a escuchar en 30 segundos.")
    else:
        print("Listo: la API del celular deja de escuchar en 30 segundos. Para volver: ApiCelular.exe habilitar")
    return 0


# --------------------------------------------------------------------- #
# diagnostico
# --------------------------------------------------------------------- #

def _fila(ok, texto: str, detalle: str = "") -> None:
    marca = "[SI]" if ok else "[NO]" if ok is False else "[--]"
    print(tapar_ips(f"{marca} {texto}" + (f" — {detalle}" if detalle else "")))


def _sw(nombre: str, *args, **kwargs):
    """Llama a una función de pos_core.servicio_windows; (True, resultado) o
    (False, motivo). Ninguna falla de acá puede cortar el diagnóstico."""
    try:
        from pos_core import servicio_windows
        funcion = getattr(servicio_windows, nombre)
    except Exception:
        return False, f"esta versión no trae servicio_windows.{nombre}"
    try:
        return True, funcion(*args, **kwargs)
    except Exception as e:
        return False, f"no se pudo consultar ({type(e).__name__})"


def _exe_api() -> str:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.join(paths.get_base_path(), "ApiCelular", "ApiCelular.exe")


def _powershell_responde() -> bool:
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Write-Output otter-ok"],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "otter-ok" in (r.stdout or "")
    except Exception:
        return False


def _regla_propia(puerto: int):
    """(ok, detalle) de la regla OtterApiCelular-Tailscale, SOLO LEYENDO (el
    diagnóstico no corrige nada). Se mira dirección remota, puerto y placa."""
    if os.name != "nt":
        return None, "no es Windows"
    script = (
        f"$r = Get-NetFirewallRule -Name '{REGLA_FIREWALL}' -ErrorAction SilentlyContinue; "
        "if ($null -eq $r) { '{}' ; exit 0 }; "
        "$a = ($r | Get-NetFirewallAddressFilter).RemoteAddress -join ','; "
        "$p = ($r | Get-NetFirewallPortFilter).LocalPort -join ','; "
        "$i = ($r | Get-NetFirewallInterfaceFilter).InterfaceAlias -join ','; "
        "@{existe=$true; habilitada=[string]$r.Enabled; accion=[string]$r.Action; remota=$a; puerto=$p; placa=$i} "
        "| ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        datos = json.loads((r.stdout or "{}").strip() or "{}")
    except Exception as e:
        return False, f"no se pudo consultar ({type(e).__name__})"
    if not datos.get("existe"):
        return False, "no existe la regla propia (la crea el Actualizador)"
    problemas = []
    if datos.get("habilitada") not in ("True", "1"):
        problemas.append("está deshabilitada")
    if datos.get("accion") not in ("Allow", "2"):
        problemas.append("no es de permitir")
    if datos.get("remota") not in ("100.64.0.0/10", "100.64.0.0/255.192.0.0"):
        problemas.append("no está limitada al rango de Tailscale")
    if datos.get("puerto") != str(puerto):
        problemas.append(f"no es para el puerto {puerto}")
    placa = datos.get("placa") or ""
    if not placa or placa == "Any":
        problemas.append("no está atada a la placa de Tailscale")
    if problemas:
        return False, "; ".join(problemas)
    return True, f"solo Tailscale, puerto {puerto}, placa «{placa}»"


def diagnostico() -> int:
    _configurar_logs()
    _log().info("diagnostico corrido desde la consola")
    print("Diagnóstico de la API del celular (Otter)")
    print("Una captura de esta pantalla no lleva IPs, PIN ni tokens.\n")

    ruta = panel_celular.ruta_base_datos()
    hay_base = os.path.isfile(ruta)
    _fila(hay_base, "Base del negocio encontrada", ruta if hay_base else f"no está {ruta}")
    if hay_base:
        try:
            faltan = db.columnas_faltantes(ruta)
            _fila(not faltan, "La base tiene todas las columnas",
                  ("faltan: " + ", ".join(faltan) + " (abrí la Caja una vez o corré el Actualizador)") if faltan else "")
        except Exception as e:
            _fila(False, "La base se puede leer", type(e).__name__)
        db.usar_solo_base_existente(ruta)
    estado_pin = acceso_celular.estado_pin() if hay_base else "base_no_disponible"
    db.cerrar_conexion()
    textos_pin = {
        "definido": (True, "definido"),
        "no_definido": (False, "todavía no se definió: ApiCelular.exe definir-pin (lo escribe Leo)"),
        "anulado": (False, "quedó el PIN viejo, anulado: hay que definir uno nuevo"),
        "secreto_perdido": (False, "se perdió api_celular\\secreto.json: hay que volver a definir el PIN"),
        "secreto_ilegible": (False, "api_celular\\secreto.json está dañado (no se regenera solo)"),
        "base_no_disponible": (False, "sin base no se puede mirar"),
    }
    ok_pin, texto_pin = textos_pin.get(estado_pin, (False, estado_pin))
    _fila(ok_pin, "PIN del celular", texto_pin)

    cfg = config.leer_config_celular()
    if not cfg["leido"]:
        _fila(False, "config.ini se puede leer", "no existe o está mal formado")
    _fila(cfg["habilitado"], "La API del celular está habilitada en config.ini",
          f"puerto {cfg['puerto']}" if cfg["habilitado"] else
          "apagada ([api_celular] habilitado no es true; para prenderla: ApiCelular.exe habilitar)")
    _fila(cfg["puerto"] != cfg["puerto_remoto"], "El puerto no choca con el del Dueño Remoto",
          f"[api_celular] puerto {cfg['puerto']}, [remoto] puerto {cfg['puerto_remoto']}")

    servicio = "SistemaDualApiCelular"
    ok, estado = _sw("estado_y_pid", servicio)
    if ok:
        _fila(estado[0] == "corriendo", "Servicio SistemaDualApiCelular corriendo", str(estado[0]))
    else:
        _fila(None, "Servicio SistemaDualApiCelular", estado)
    ok, arranque = _sw("tipo_de_arranque", servicio)
    _fila(ok and arranque == "auto", "El servicio arranca solo con Windows", str(arranque))
    ok, reintentos = _sw("reintentos_configurados", servicio)
    _fila(reintentos if ok else None, "Windows reintenta el servicio si se cae",
          {True: "sí", False: "no (lo corrige el Actualizador)", None: "no se pudo leer"}.get(reintentos, reintentos)
          if ok else reintentos)

    ok, contesta = _sw("api_celular_contesta", cfg["puerto"])
    if ok:
        _fila(bool(contesta[0]), f"En el {cfg['puerto']} contesta la API del celular", str(contesta[1]))
    else:
        _fila(None, f"En el {cfg['puerto']} contesta la API del celular", contesta)
    powershell = _powershell_responde()
    for puerto in (cfg["puerto"], cfg["puerto_remoto"]):
        if not powershell:
            _fila(None, f"Quién escucha en el {puerto}", "no se pudo preguntar a Windows (PowerShell no contesta)")
            continue
        ok, quien = _sw("quien_escucha", puerto)
        _fila(None, f"Quién escucha en el {puerto}", (quien or "nadie") if ok else quien)

    ok_regla, detalle_regla = _regla_propia(cfg["puerto"])
    _fila(ok_regla, f"Firewall: el {cfg['puerto']} solo abre para Tailscale", detalle_regla)
    exe = _exe_api()
    # Las funciones de servicio_windows devuelven [] tanto si no hay nada como
    # si no pudieron preguntar: sin PowerShell, "ninguna" sería un SI que no
    # verificó nada. Se pregunta primero si PowerShell contesta.
    if not powershell:
        for texto in ("Reglas que BLOQUEAN ApiCelular (le ganan a la nuestra)",
                      "Reglas que abren ApiCelular fuera de Tailscale",
                      "Firewall de Windows prendido en todos los perfiles",
                      "Ninguna otra placa tiene una IP del rango de Tailscale"):
            _fila(None, texto, "no se pudo preguntar a Windows (PowerShell no contesta)")
    else:
        for nombre, texto in (("reglas_que_bloquean", "Reglas que BLOQUEAN ApiCelular (le ganan a la nuestra)"),
                              ("reglas_que_abren_de_mas", "Reglas que abren ApiCelular fuera de Tailscale")):
            ok, reglas = _sw(nombre, exe) if nombre == "reglas_que_bloquean" else _sw(nombre, exe, cfg["puerto"])
            if ok:
                _fila(not reglas, texto, ", ".join(reglas) if reglas else "ninguna")
            else:
                _fila(None, texto, reglas)
        ok, perfiles = _sw("perfiles_firewall_apagados")
        _fila((not perfiles) if ok else None, "Firewall de Windows prendido en todos los perfiles",
              (("apagado en: " + ", ".join(perfiles)) if perfiles else "sí") if ok else perfiles)
        ok, placas = _sw("placas_lan_en_rango_tailscale")
        _fila((not placas) if ok else None, "Ninguna otra placa tiene una IP del rango de Tailscale",
              (("ojo: " + ", ".join(placas) + " (módem en puente: sin la regla atada a la placa abrirían el "
                                              "puerto)") if placas else "ninguna") if ok else placas)

    ok, tarea = _sw("estado_tarea", TAREA_WATCHDOG)
    if ok and tarea:
        bien = (tarea.get("existe") and not tarea.get("deshabilitada")
                and tarea.get("ultimo_resultado") in (0, 267009)
                and (tarea.get("minutos_desde_ultima") is not None and tarea.get("minutos_desde_ultima") < 15))
        _fila(bool(bien), "El watchdog OtterWatchdogCelular vigila la API",
              f"existe={tarea.get('existe')}, deshabilitada={tarea.get('deshabilitada')}, último resultado "
              f"{tarea.get('ultimo_resultado')}, hace {tarea.get('minutos_desde_ultima')} min")
    else:
        _fila(None, "El watchdog OtterWatchdogCelular vigila la API", tarea if not ok else "no se pudo preguntar")

    inicio = time.perf_counter()
    hashlib.pbkdf2_hmac("sha256", b"x" * 32, b"y" * 16, acceso_celular.ITERACIONES, 32)
    _fila(None, "Tiempo de un PBKDF2 (cada intento de PIN)", f"{(time.perf_counter() - inicio) * 1000:.0f} ms")

    temporales = glob.glob(os.path.join(paths.get_base_path(), "config.ini.*.tmp"))
    _fila(not temporales, "Sin temporales sueltos de config.ini",
          f"{len(temporales)} (los dejó un corte de luz; se pueden borrar)" if temporales else "")

    print("\nÚltimas líneas de logs\\api_celular.log (IPs tapadas):")
    try:
        with open(os.path.join(paths.get_base_path(), "logs", "api_celular.log"), "r", encoding="utf-8",
                  errors="replace") as f:
            for linea in f.readlines()[-20:]:
                print("  " + tapar_ips(linea.rstrip()))
    except OSError:
        print("  (no hay log todavía)")
    return 0


# --------------------------------------------------------------------- #
# consola (solo desarrollo)
# --------------------------------------------------------------------- #

def consola(args: list) -> int:
    from services import api_celular
    print(TEXTO_CONSOLA)
    puerto = None
    if "--puerto" in args:
        try:
            puerto = int(args[args.index("--puerto") + 1])
        except (IndexError, ValueError):
            print("--puerto necesita un número")
            return 2
    puerto = puerto or config.leer_config_celular()["puerto"]
    api_celular.configurar_logs("servicio")
    estado = api_celular.EstadoApi()
    estado.refrescar()
    servidor = api_celular.iniciar_servidor(puerto, app=api_celular.crear_app(estado=estado))
    print(f"Escuchando en el {puerto}. Ctrl+C para cortar.")
    try:
        while servidor.vivo:
            time.sleep(15)
            estado.refrescar()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.detener()
    return 0


def main(verbo: str, args: list) -> int:
    if verbo == "definir-pin":
        return definir_pin(args)
    if verbo == "cerrar-sesiones":
        return cerrar_sesiones()
    if verbo == "habilitar":
        return cambiar_habilitado(True)
    if verbo == "deshabilitar":
        return cambiar_habilitado(False)
    if verbo == "diagnostico":
        return diagnostico()
    if verbo == "consola":
        return consola(args)
    print(f"Verbo desconocido: {verbo}")
    return 2
