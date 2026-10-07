"""Actualizador de Otter — pone al día una instalación que ya funciona.

Es lo que se corre en el local cuando hay una versión nueva, en vez de
reinstalar todo: encuentra sola la instalación existente, hace una copia
de seguridad de la base, reemplaza los programas, pone la base al día si
el esquema cambió, y vuelve a dejar el servicio corriendo.

Lo que NUNCA toca:
  - La base de datos con las ventas y el stock (solo le agrega columnas
    nuevas si hicieran falta, algo que no borra ni cambia ningún dato).
  - El config.ini: ahí viven el token del Dueño Remoto, el CUIT y el
    certificado de ARCA, y el bot de Telegram. Pisarlo obligaría a
    reconfigurar todo y a reinstalar el Dueño Remoto en la otra PC.

Sirve para las dos instalaciones y se da cuenta solo de cuál es:
  - PC del local  -> MaestroCaja + MaestroDueno + StockService
                     (+ ApiCelular, la API de la app del celular, si se pide
                     o si ya estaba instalada: es OPCIONAL y si falla la caja
                     se actualiza igual)
  - Laptop del dueño -> DuenoRemoto
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from apps.theme import COLORS, aplicar_tema, habilitar_copiar_pegar_global, ajustar_ventana

NOMBRE_SERVICIO = "SistemaDualStockService"

# Dónde suele estar instalado, para no hacer buscar la carpeta a mano.
CANDIDATOS_LOCAL = [r"C:\SistemaDual", r"C:\Otter", r"C:\Program Files\SistemaDual"]
CANDIDATOS_REMOTO = [r"C:\Otter", r"C:\DuenoRemoto"]

APPS_LOCAL = ["MaestroCaja", "MaestroDueno", "StockService"]
APPS_REMOTO = ["DuenoRemoto"]
# Opcionales: no frenan la actualización si faltan, y si algo de ellas falla
# se anota y la caja se actualiza igual (regla 6). Se manejan APARTE del
# bucle de APPS_LOCAL a propósito: ese bucle es el camino de la caja.
APPS_LOCAL_OPCIONALES = ["ApiCelular"]
APP_CELULAR = "ApiCelular"

TEXTO_CASILLA_CELULAR = "Instalar también la API del celular (app del dueño, puerto 8766)"
TEXTO_CELULAR_YA_INSTALADA = "La API del celular ya está instalada: se actualiza junto con lo demás."
AVISO_PIN = ("Se abrió una ventana negra: que Leo escriba ahí su PIN, dos veces. Nadie más lo "
             "mira, lo anota ni lo manda por chat.")

# Datos del cliente, no programa. Vale en las dos direcciones:
#
#  - NO se traen desde el origen. Al probar los .exe desde dist\ antes de
#    llevarlos, cada app se crea ahí su config.ini, su database\, sus logs
#    y sus tickets de prueba, y el build se los lleva puestos.
#  - NO se pierden del destino. La carpeta vieja se reemplaza entera, así
#    que sin esto se borra lo que viviera adentro. Es exactamente el caso
#    del Dueño Remoto: su config.ini —con la IP de Tailscale y el token
#    REALES— vive AL LADO de su .exe, no en la carpeta padre como las tres
#    del Maestro. Actualizar la laptop de Leo lo borraba y lo dejaba sin
#    panel, y recuperarlo obliga a tipear el token a mano, que es
#    justamente lo que no hay que hacer nunca.
#
# "api_celular" es donde la API del celular guarda su secreto (en la carpeta
# padre, no adentro del programa); está acá por si alguien la deja adentro.
_DATOS_DEL_CLIENTE = {"config.ini", "database", "logs", "tickets", "sync_data",
                       "backups", "sincronizacion_exitosa.txt", "api_celular"}


def _ignorar_datos(carpeta, nombres):
    """Para copytree: qué NO traer desde la carpeta recién compilada."""
    return [n for n in nombres if n.lower() in _DATOS_DEL_CLIENTE]


def _devolver_datos_del_cliente(anterior: str, destino_app: str, log) -> None:
    """Repone en la carpeta nueva los datos que había en la vieja.

    Se hace DESPUÉS de copiar el programa nuevo: lo que se reemplaza es el
    programa, nunca los datos.
    """
    if not os.path.isdir(anterior):
        return
    for nombre in os.listdir(anterior):
        if nombre.lower() not in _DATOS_DEL_CLIENTE:
            continue
        origen = os.path.join(anterior, nombre)
        llegada = os.path.join(destino_app, nombre)
        try:
            if os.path.exists(llegada):
                # Vino del build pese al filtro: los datos del cliente mandan.
                if os.path.isdir(llegada):
                    shutil.rmtree(llegada, ignore_errors=True)
                else:
                    os.remove(llegada)
            if os.path.isdir(origen):
                shutil.copytree(origen, llegada)
            else:
                shutil.copy2(origen, llegada)
            log(f"    (se conservó {nombre} del cliente)")
        except Exception as e:
            # Que no se pueda reponer un dato NO puede dejar la instalación
            # a medio actualizar: se avisa y se sigue.
            log(f"    ATENCIÓN: no se pudo conservar {nombre}: {e}")


def _escritorio() -> str:
    """Dónde dejar los archivos que Matías se lleva o fotografía.

    No alcanza con "~/Desktop": si la PC tiene OneDrive, Windows redirige
    el Escritorio adentro de OneDrive y ahí la carpeta puede llamarse
    Escritorio en castellano. Un informe que queda en un lugar que nadie
    mira es lo mismo que no haberlo escrito, así que se prueban las
    variantes conocidas y, si ninguna existe, se cae a la carpeta del
    usuario — que siempre existe.

    Ojo: en Windows `expanduser("~")` NO mira HOME, mira USERPROFILE.
    """
    casa = os.path.expanduser("~")
    candidatos = [
        os.path.join(casa, "Desktop"),
        os.path.join(casa, "Escritorio"),
        os.path.join(casa, "OneDrive", "Desktop"),
        os.path.join(casa, "OneDrive", "Escritorio"),
    ]
    entorno = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if entorno:
        candidatos += [os.path.join(entorno, "Desktop"), os.path.join(entorno, "Escritorio")]
    for c in candidatos:
        if os.path.isdir(c):
            return c
    return casa


def es_administrador() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def buscar_origen() -> str:
    """Carpeta con las apps NUEVAS ya compiladas (al lado del actualizador)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for candidato in (base, os.path.dirname(base), os.path.join(base, "dist"),
                      os.path.join(os.path.dirname(base), "dist")):
        if os.path.isdir(os.path.join(candidato, "MaestroCaja")) or \
                os.path.isdir(os.path.join(candidato, "DuenoRemoto")):
            return candidato
    return base


def detectar_instalacion() -> tuple:
    """Encuentra la instalación existente. Devuelve (carpeta, modo)."""
    for carpeta in CANDIDATOS_LOCAL:
        if os.path.isdir(os.path.join(carpeta, "MaestroCaja")):
            return carpeta, "local"
    for carpeta in CANDIDATOS_REMOTO:
        if os.path.isdir(os.path.join(carpeta, "DuenoRemoto")):
            return carpeta, "remoto"
    return "", ""


_RE_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _tapar_ips(texto) -> str:
    """La tabla de la revisión se fotografía y se manda por chat: sin IPs.

    Una captura de pantalla de la consola ES un chat (regla 4). Ninguna
    fila escribe una IP a propósito; esto es por si algún detalle de
    Windows la trae puesta.
    """
    return _RE_IPV4.sub("x.x.x.x", str(texto or ""))


# ---------------------------------------------------------------------- #
# API del celular: funciones de módulo (las llama el hilo de trabajo, que
# nunca toca Tk; `log` solo encola).
# ---------------------------------------------------------------------- #
def _parar_celular(log) -> bool:
    """Para la API del celular para poder reemplazar su carpeta. True si paró."""
    from pos_core import servicio_windows
    log("Parando la API del celular...")
    try:
        ok, detalle = servicio_windows.parar(servicio_windows.SERVICIO_CELULAR)
    except Exception as e:
        ok, detalle = False, str(e)
    if ok:
        log(f"   {detalle}.\n")
        return True
    log(f"   ATENCIÓN: la API del celular no se pudo parar y queda en su versión anterior: {detalle}\n")
    return False


def _reemplazar_celular(origen: str, destino: str, log) -> bool:
    """Copia la versión nueva de ApiCelular. Nunca lanza: si falla, queda la anterior."""
    from pos_core import servicio_windows
    log(f"Actualizando {APP_CELULAR}...")
    origen_app = os.path.join(origen, APP_CELULAR)
    destino_app = os.path.join(destino, APP_CELULAR)
    anterior = destino_app + ".anterior"
    try:
        servicio_windows.reemplazar_carpeta(origen_app, destino_app, _ignorar_datos)
        _devolver_datos_del_cliente(anterior, destino_app, log)
        shutil.rmtree(anterior, ignore_errors=True)
        log(f"   {APP_CELULAR} actualizado.\n")
        return True
    except Exception as e:
        log(f"   ATENCIÓN: la API del celular quedó en la versión anterior: {e}\n")
        return False


def _esperar_salud(puerto: int, segundos: int = 20):
    """La salud de la API del celular, esperando a que levante (o None)."""
    from pos_core import servicio_windows
    # Por cantidad de intentos y no por reloj: así las pruebas, que cambian
    # _dormir por una función que no espera, no se quedan girando 20 s.
    for intento in range(max(segundos // 2, 1)):
        salud = servicio_windows.salud_api_celular(puerto)
        if salud is not None:
            return salud
        servicio_windows._dormir(2)
    return servicio_windows.salud_api_celular(puerto)


def _instalar_celular(destino: str, pedida_ahora: bool, log) -> None:
    """Registra/actualiza el servicio de la API del celular, su regla de
    firewall y el acceso directo del PIN. Cada paso aislado: uno que falle
    no se lleva a los demás, y ninguno voltea la actualización."""
    from pos_core import config, paths, servicio_windows as sw
    exe = os.path.join(destino, APP_CELULAR, "ApiCelular.exe")
    log("Instalando la API del celular como servicio de Windows...")
    paths.set_base_override(destino)
    ruta_config = os.path.join(destino, "config.ini")

    if pedida_ahora:
        # SOLO si se pidió ahora. Si ya estaba instalada, [api_celular] no se
        # toca nunca: puede estar apagada a propósito (D21).
        try:
            config.cargar_config(estricto=True)   # nunca pisar un config ilegible con defaults
            config.actualizar_config_dict({"api_celular": {"habilitado": "true", "puerto": "8766"}})
            log("   config.ini: [api_celular] habilitado = true, puerto = 8766")
        except Exception as e:
            log(f"   ATENCIÓN: no se pudo escribir [api_celular] en config.ini ({e}): "
                f"la API queda instalada pero apagada")

    try:
        ok, detalle = sw.instalar_servicio(exe, sw.SERVICIO_CELULAR)
        log(f"   Servicio: {'OK' if ok else 'ATENCIÓN'} — {detalle}")
    except Exception as e:
        log(f"   Servicio: ATENCIÓN — {e}")

    cfg = config.leer_config_celular(ruta_config)
    try:
        ok, detalle = sw.asegurar_regla_firewall_celular(exe, cfg["puerto"])
        log(f"   Firewall: {'OK' if ok else 'ATENCIÓN'} — {detalle}")
    except Exception as e:
        log(f"   Firewall: ATENCIÓN — {e}")

    try:
        ok, detalle = sw.acceso_directo_pin_celular(exe)
        log(f"   Acceso directo del PIN: {'OK' if ok else 'ATENCIÓN'} — {detalle}")
    except Exception as e:
        log(f"   Acceso directo del PIN: ATENCIÓN — {e}")

    try:
        if cfg["habilitado"] and sw.estado_y_pid(sw.SERVICIO_CELULAR)[0] == "corriendo":
            salud = _esperar_salud(cfg["puerto"])
            if salud is None:
                log("   La API del celular todavía no contesta: la revisión final la vuelve a mirar.")
            elif not salud.get("pin_configurado"):
                log('   Falta definir el PIN: con Leo al lado, apretá "Definir PIN del celular".')
            else:
                log("   El PIN del celular ya estaba definido.")
    except Exception as e:
        log(f"   (no se pudo preguntar por el PIN: {e})")
    log("")


def _filas_servicio_stock_extra(destino: str, anotar) -> None:
    """Fila nueva del servicio de stock: que Windows lo reintente si se cae.

    poner_en_automatico() se llama SIEMPRE: es idempotente, y `remove` +
    `install` (lo que hace el paso 5 al relanzar el servicio) borra los
    reintentos sin avisar. Después se verifica leyendo, no se da por hecho.
    """
    que = "Windows reintenta el servicio de stock si se cae"
    try:
        from pos_core import servicio_windows as sw
        sw.poner_en_automatico()
        reintentos = sw.reintentos_configurados(sw.NOMBRE_SERVICIO)
        if reintentos is True:
            anotar(que, True)
        elif reintentos is None:
            anotar(que, False, "no se pudo leer")
        else:
            anotar(que, False, "sin reintentos configurados (¿falta ejecutar como administrador?)")
    except Exception as e:
        anotar(que, False, str(e))


_DESHABILITADA = ("deshabilitada en Windows: no se toca; para apagarla se usa "
                  "[api_celular] habilitado = false")
_SUGERENCIA_WATCHDOG = ("no está instalado: correr OtterBlindaje con «Solo actualizar el "
                        "watchdog» (no corta la red)")


def _filas_celular(destino: str, anotar) -> None:
    """Filas de la API del celular en la revisión final. Cada una en su try.

    Antes de cualquier poner_en_automatico() o arrancar() se mira el tipo
    de arranque: esas funciones desharían un Deshabilitado, y deshabilitarla
    en Windows es decisión de alguien (la forma documentada de apagarla es
    [api_celular] habilitado = false, que también se respeta).
    """
    from pos_core import config, servicio_windows as sw
    carpeta = os.path.join(destino, APP_CELULAR)
    exe = os.path.join(carpeta, "ApiCelular.exe")

    if not os.path.isdir(carpeta):
        try:
            est, _ = sw.estado_y_pid(sw.SERVICIO_CELULAR)
            # "desconocido" no prueba que esté registrado: no se acusa sin saber.
            if est not in ("no_instalado", "desconocido"):
                anotar("Quedó registrado el servicio de la API del celular sin su carpeta", False,
                       'ver "Desinstalar la API del celular" en el README')
        except Exception:
            pass
        return

    hab, puerto = False, sw.PUERTO_CELULAR
    try:
        cfg = config.leer_config_celular(os.path.join(destino, "config.ini"))
        hab, puerto = bool(cfg["habilitado"]), int(cfg["puerto"])
        anotar("La API del celular está habilitada en config.ini", hab,
               "" if hab else "instalada pero apagada a propósito ([api_celular] habilitado = false)")
    except Exception as e:
        anotar("La API del celular está habilitada en config.ini", False, str(e))

    que = "La API del celular arranca sola con Windows"
    try:
        arranque = sw.tipo_de_arranque(sw.SERVICIO_CELULAR)
        if arranque == "auto":
            anotar(que, True)
        elif arranque == "deshabilitado":
            anotar(que, False, _DESHABILITADA)
        elif arranque == "manual":
            ok, detalle = sw.poner_en_automatico(sw.SERVICIO_CELULAR)
            anotar(que, ok, "estaba en MANUAL, corregido" if ok
                   else f"estaba en MANUAL y no se pudo corregir: {detalle}")
        else:
            anotar(que, False, f"no se pudo leer el tipo de arranque ({arranque})")
    except Exception as e:
        anotar(que, False, str(e))

    que = "Windows reintenta la API del celular si se cae"
    try:
        arranque = sw.tipo_de_arranque(sw.SERVICIO_CELULAR)
        if arranque == "deshabilitado":
            anotar(que, False, _DESHABILITADA)
        elif arranque not in ("auto", "manual"):
            anotar(que, False, f"no se pudo leer el tipo de arranque ({arranque})")
        else:
            sw.poner_en_automatico(sw.SERVICIO_CELULAR)   # idempotente: repone los reintentos
            reintentos = sw.reintentos_configurados(sw.SERVICIO_CELULAR)
            anotar(que, reintentos is True, "" if reintentos is True else
                   ("no se pudo leer" if reintentos is None else "sin reintentos configurados"))
    except Exception as e:
        anotar(que, False, str(e))

    que = "La API del celular está corriendo"
    try:
        arranque = sw.tipo_de_arranque(sw.SERVICIO_CELULAR)
        est, _ = sw.estado_y_pid(sw.SERVICIO_CELULAR)
        if est == "corriendo":
            anotar(que, True)
        elif arranque == "deshabilitado":
            anotar(que, False, _DESHABILITADA)
        elif est == "parado":
            ok, detalle = sw.arrancar(sw.SERVICIO_CELULAR)
            anotar(que, ok, "estaba parada, arrancada" if ok else f"estaba parada y no arrancó: {detalle}")
        else:
            anotar(que, False, f"estado: {est}")
    except Exception as e:
        anotar(que, False, str(e))

    salud = None
    if hab:
        que = f"En el {puerto} contesta la API del celular (y es ella)"
        try:
            ok, detalle = sw.api_celular_contesta(puerto)
            anotar(que, ok, "" if ok and detalle == "contesta la API del celular" else detalle)
        except Exception as e:
            anotar(que, False, str(e))

        try:
            salud = sw.salud_api_celular(puerto)
        except Exception:
            salud = None

        que = "La API del celular ve la base del negocio"
        try:
            if salud is None:
                anotar(que, False, "la API del celular no contesta")
            else:
                base = salud.get("base") or {}
                anotar(que, base.get("ok") is True,
                       "" if base.get("ok") is True else str(base.get("detalle", "")))
        except Exception as e:
            anotar(que, False, str(e))

        que = "El PIN del celular está definido"
        try:
            if salud is None:
                anotar(que, False, "la API del celular no contesta")
            else:
                definido = salud.get("pin_configurado") is True
                anotar(que, definido, "" if definido else
                       'con Leo al lado, apretá "Definir PIN del celular" (un PIN recién '
                       'definido puede tardar 15 segundos en verse acá)')
        except Exception as e:
            anotar(que, False, str(e))

    que = f"Firewall: el {puerto} solo abre para Tailscale"
    try:
        ok_regla, detalle_regla = sw.asegurar_regla_firewall_celular(exe, puerto)
        # estricto=True: "no se pudo preguntar" vuelve como None y no como
        # una lista vacía, que se leería como "no hay ninguna" (un SI que no
        # verificó nada).
        bloquean = sw.reglas_que_bloquean(exe, estricto=True)
        de_mas = sw.reglas_que_abren_de_mas(exe, puerto, estricto=True)
        apagados = sw.perfiles_firewall_apagados(estricto=True)
        placas = sw.placas_lan_en_rango_tailscale(estricto=True)
        partes = []
        if not ok_regla:
            partes.append(detalle_regla)
        sin_respuesta = [que for que, valor in (("reglas que bloquean", bloquean),
                                                ("reglas que abren de más", de_mas),
                                                ("perfiles apagados", apagados)) if valor is None]
        if sin_respuesta:
            partes.append("no se pudo preguntar a Windows por: " + ", ".join(sin_respuesta))
        if bloquean:
            partes.append("reglas que BLOQUEAN ApiCelular (no se borraron): " + ", ".join(bloquean))
        if de_mas:
            partes.append("reglas que abren el puerto sin limitarlo a Tailscale (no se borraron): "
                          + ", ".join(de_mas))
        if apagados:
            partes.append("firewall apagado en: " + ", ".join(apagados))
        if placas:
            partes.append("aviso: placas que no son Tailscale con IP en su rango: " + ", ".join(placas))
        ok = ok_regla and bloquean == [] and de_mas == [] and apagados == []
        anotar(que, ok, "; ".join(partes))
    except Exception as e:
        anotar(que, False, str(e))

    if not hab:
        return

    que = "El watchdog vigila la API del celular"
    try:
        tarea = sw.estado_tarea(sw.TAREA_WATCHDOG_CELULAR)
        script = os.path.join(destino, "watchdog", "watchdog_celular.ps1")
        if tarea is None:
            anotar(que, False, "no se pudo preguntar por la tarea programada")
        elif not tarea.get("existe") or not os.path.isfile(script):
            anotar(que, False, _SUGERENCIA_WATCHDOG)
        elif tarea.get("deshabilitada"):
            anotar(que, False, "la tarea está deshabilitada")
        elif tarea.get("ultimo_resultado") == 267011:
            anotar(que, False, "todavía no corrió (267011): esperá 5 minutos")
        elif tarea.get("ultimo_resultado") not in (0, 267009):
            anotar(que, False, f"último resultado {tarea.get('ultimo_resultado')}")
        elif tarea.get("minutos_desde_ultima") is None or tarea["minutos_desde_ultima"] >= 15:
            anotar(que, False, f"no corre hace {tarea.get('minutos_desde_ultima')} min")
        else:
            anotar(que, True)
    except Exception as e:
        anotar(que, False, str(e))

    que = "No quedan restos del ApiDueno viejo"
    try:
        restos = sw.restos_api_dueno()
        if restos is None:
            anotar(que, False, "no se pudo revisar")
        else:
            quien = sw.quien_escucha(sw.PUERTO_POR_DEFECTO)
            if quien.lower().startswith("apidueno"):
                restos = list(restos) + [f"el {sw.PUERTO_POR_DEFECTO} lo tiene {quien}"]
            # Solo informa: no se borra nada a ciegas.
            anotar(que, not restos, "; ".join(restos) + (" (no se borró nada)" if restos else ""))
    except Exception as e:
        anotar(que, False, str(e))


class Actualizador(tk.Tk):

    def __init__(self):
        super().__init__()
        aplicar_tema(self)
        self.title("Actualizador de Otter")
        ajustar_ventana(self, 760, 620, minimo=(620, 460))
        self.origen = buscar_origen()
        detectado, modo = detectar_instalacion()
        self.modo_detectado = modo
        # Todo lo que quiera mostrar el hilo de actualización pasa por acá:
        # Tk no es seguro entre hilos (ver apps/instalador/main.py).
        self._cola = queue.Queue()

        self._armar_ui()
        self._bombear()
        habilitar_copiar_pegar_global(self)

        if detectado:
            self.destino.delete(0, "end")
            self.destino.insert(0, detectado)
            cual = "la PC del local" if modo == "local" else "la laptop del dueño"
            self._log(f"Se encontró una instalación de {cual} en:\n   {detectado}\n")
        else:
            self._log("No se encontró una instalación automáticamente.\n"
                       "Elegí a mano la carpeta donde está instalado Otter (la que tiene\n"
                       "adentro las carpetas MaestroCaja / DuenoRemoto).\n")
        self._log(f"Programas nuevos encontrados en:\n   {self.origen}\n")
        self._reevaluar_celular()
        if not es_administrador():
            self._log("AVISO: no estás como administrador. Todo se actualiza igual, pero el\n"
                       "servicio de stock no se va a poder reiniciar solo. Si esta es la PC\n"
                       "del local, conviene cerrar y abrir con botón derecho ->\n"
                       "'Ejecutar como administrador'.\n")

    def _armar_ui(self):
        ttk.Label(self, text="Actualizador de Otter", style="Header.TLabel"
                  ).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text="Actualiza una instalación que ya funciona. No toca las ventas, "
                              "el stock ni la configuración.", style="Muted.TLabel",
                  wraplength=700, justify="left").pack(anchor="w", padx=18)

        marco = ttk.Frame(self, padding=(18, 14))
        marco.pack(fill="x")
        ttk.Label(marco, text="Carpeta instalada:").grid(row=0, column=0, sticky="w")
        self.destino = ttk.Entry(marco, width=52)
        self.destino.grid(row=0, column=1, padx=8, sticky="we")
        ttk.Button(marco, text="Buscar...", command=self._elegir_carpeta).grid(row=0, column=2)
        marco.grid_columnconfigure(1, weight=1)

        self.var_backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(marco, text="Hacer copia de seguridad de la base antes de actualizar "
                                     "(recomendado)", variable=self.var_backup
                        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # API del celular: solo en la PC del local (_reevaluar_celular la
        # muestra u oculta según la carpeta elegida).
        self.marco_celular = ttk.Frame(marco)
        self.marco_celular.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.var_celular = tk.BooleanVar(value=False)
        self._celular_ya_instalada = False
        self.casilla_celular = ttk.Checkbutton(self.marco_celular, text=TEXTO_CASILLA_CELULAR,
                                               variable=self.var_celular)
        self.casilla_celular.pack(anchor="w")
        self.boton_pin = ttk.Button(self.marco_celular, text="Definir PIN del celular",
                                    command=self._definir_pin_celular, state="disabled")
        self.boton_pin.pack(anchor="w", pady=(4, 0))
        self.destino.bind("<FocusOut>", lambda _e: self._reevaluar_celular())
        self.destino.bind("<Return>", lambda _e: self._reevaluar_celular())

        self.boton = ttk.Button(self, text="ACTUALIZAR", style="Accent.TButton",
                                 command=self._actualizar)
        self.boton.pack(anchor="w", padx=18, pady=(6, 12))

        self.texto = tk.Text(self, height=16, bg="#FFFFFF", relief="flat",
                              padx=8, pady=8, wrap="word")
        self.texto.pack(fill="both", expand=True, padx=18, pady=(0, 16))

    def _elegir_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta donde está instalado Otter")
        if carpeta:
            self.destino.delete(0, "end")
            self.destino.insert(0, carpeta.replace("/", os.sep))
            self._reevaluar_celular()

    # ------------------------------------------------------------------ #
    # API del celular en pantalla (todo esto corre en el hilo de Tk)
    # ------------------------------------------------------------------ #
    def _reevaluar_celular(self):
        """Muestra la casilla y el botón del PIN solo en la PC del local, y
        los deja como corresponde a lo que hay instalado en esa carpeta."""
        destino = self.destino.get().strip()
        if not destino or not os.path.isdir(os.path.join(destino, "MaestroCaja")):
            # La laptop de Leo no lleva la API del celular: ni se ofrece.
            self.marco_celular.grid_remove()
            self.var_celular.set(False)
            self._celular_ya_instalada = False
            return
        self.marco_celular.grid()
        if os.path.isdir(os.path.join(destino, APP_CELULAR)):
            # Ya instalada: se actualiza sí o sí junto con lo demás. Tildada
            # y deshabilitada para que no parezca que destildarla la saca.
            self.var_celular.set(True)
            self.casilla_celular.config(text=TEXTO_CELULAR_YA_INSTALADA, state="disabled")
            self._celular_ya_instalada = True
        else:
            if self._celular_ya_instalada:
                self.var_celular.set(False)
            self.casilla_celular.config(text=TEXTO_CASILLA_CELULAR, state="normal")
            self._celular_ya_instalada = False
        exe = os.path.join(destino, APP_CELULAR, "ApiCelular.exe")
        self.boton_pin.config(state="normal" if os.path.isfile(exe) else "disabled")

    def _reevaluar_boton_pin(self):
        """Lo encola el hilo de trabajo al terminar: el botón del PIN se
        habilita apenas existe ApiCelular.exe. Corre en el hilo de Tk."""
        self._reevaluar_celular()

    def _definir_pin_celular(self):
        exe = os.path.join(self.destino.get().strip(), APP_CELULAR, "ApiCelular.exe")
        if not os.path.isfile(exe):
            self._log(f"No está {exe}: primero hay que instalar la API del celular.")
            self._reevaluar_celular()
            return
        try:
            # Consola NUEVA: getpass necesita una de verdad para que el PIN no
            # se vea al escribirlo. Hereda el administrador del Actualizador.
            subprocess.Popen([exe, "definir-pin", "--pausa"],
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            self._log(AVISO_PIN)
        except Exception as e:
            self._log(f"No se pudo abrir la ventana del PIN: {e}")

    # ------------------------------------------------------------------ #
    def _log(self, texto):
        """Se puede llamar desde cualquier hilo: solo encola."""
        self._cola.put(texto)

    def _bombear(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                pendiente = self._cola.get_nowait()
                if callable(pendiente):
                    try:
                        pendiente()
                    except Exception:
                        pass
                else:
                    self.texto.insert("end", str(pendiente) + "\n")
                    self.texto.see("end")
        except queue.Empty:
            pass
        self.after(100, self._bombear)

    # ------------------------------------------------------------------ #
    def _actualizar(self):
        destino = self.destino.get().strip()
        if not destino or not os.path.isdir(destino):
            messagebox.showerror("Falta la carpeta",
                                  "Elegí la carpeta donde está instalado Otter.")
            return
        self.boton.config(state="disabled")
        self.texto.delete("1.0", "end")
        # Todo lo que el hilo de trabajo necesita de la pantalla se lee ACÁ,
        # en el hilo de Tk: ese hilo nunca toca un widget.
        datos = {"destino": destino, "backup": bool(self.var_backup.get()),
                 "celular": bool(self.var_celular.get())}
        threading.Thread(target=self._actualizar_en_hilo, args=(datos,), daemon=True).start()

    def _actualizar_en_hilo(self, datos):
        try:
            self._hacer_actualizacion(datos)
        except Exception as e:
            self._log(f"\n*** LA ACTUALIZACIÓN SE DETUVO ***\n{type(e).__name__}: {e}\n"
                       f"La instalación anterior sigue funcionando: no se borró nada.")
            mensaje = f"{type(e).__name__}: {e}"
            self._cola.put(lambda: messagebox.showerror("Error al actualizar", mensaje))
        finally:
            self._cola.put(lambda: self.boton.config(state="normal"))

    def _hacer_actualizacion(self, datos):
        destino = datos["destino"]
        modo = "local" if os.path.isdir(os.path.join(destino, "MaestroCaja")) else "remoto"
        apps = APPS_LOCAL if modo == "local" else APPS_REMOTO
        cual = "PC DEL LOCAL" if modo == "local" else "LAPTOP DEL DUEÑO"
        self._log(f"=== ACTUALIZANDO LA {cual} ===\n{destino}\n")

        faltantes = [a for a in apps if not os.path.isdir(os.path.join(self.origen, a))]
        if faltantes:
            raise FileNotFoundError(
                f"No se encontraron los programas nuevos: {', '.join(faltantes)}.\n"
                f"¿Copiaste la carpeta 'dist' completa al pendrive?")

        # La API del celular es OPCIONAL. Se decide todo ANTES de copiar nada.
        # "pedida_ahora" y no la casilla sola: con la API ya instalada la
        # casilla queda tildada y deshabilitada, así que siempre daría True y
        # no distinguiría "la pidieron ahora" de "ya estaba" — y solo en el
        # primer caso se escribe [api_celular] habilitado = true (si ya
        # estaba, puede estar apagada A PROPÓSITO y no se toca).
        cel_origen = os.path.isdir(os.path.join(self.origen, APP_CELULAR))
        cel_instalada = os.path.isdir(os.path.join(destino, APP_CELULAR))
        pedida_ahora = modo == "local" and bool(datos.get("celular")) and not cel_instalada
        actualizar_cel = modo == "local" and cel_origen and (cel_instalada or pedida_ahora)
        if modo == "local" and cel_instalada and not cel_origen:
            self._log("ATENCIÓN: el pendrive no trae ApiCelular: queda la versión anterior.\n")
        elif pedida_ahora and not cel_origen:
            self._log("ATENCIÓN: se pidió instalar la API del celular, pero el pendrive no trae "
                       "ApiCelular: no se instaló (la caja se actualiza igual).\n")

        # 1) Copia de seguridad de la base ANTES de tocar nada.
        if datos["backup"] and modo == "local":
            self._respaldar_base(destino)

        # 2a) La API del celular se para ANTES que el servicio de stock, y
        #     de verdad: que el proceso ya no exista (STOP_PENDING no es
        #     parado). Si no para, se deja en su versión anterior y la caja
        #     sigue: nunca frena la actualización.
        if actualizar_cel and cel_instalada:
            actualizar_cel = _parar_celular(self._log)

        # 2) El servicio tiene el .exe abierto: hay que pararlo para poder
        #    reemplazarlo (si no, Windows no deja escribir encima).
        servicio_estaba = False
        if modo == "local":
            servicio_estaba = self._parar_servicio(destino)

        # 3) Reemplazar los programas. Se copia solo el programa (ver
        #    _DATOS_DEL_CLIENTE) y se repone después lo que hubiera de
        #    datos adentro de la carpeta. En el Maestro la base y el config
        #    viven en la carpeta padre y esto no cambia nada; en el Dueño
        #    Remoto el config vive ADENTRO, y sin esto se perdía.
        for app in apps:
            self._log(f"Actualizando {app}...")
            origen_app = os.path.join(self.origen, app)
            destino_app = os.path.join(destino, app)
            anterior = destino_app + ".anterior"
            if os.path.exists(anterior):
                shutil.rmtree(anterior, ignore_errors=True)
            if os.path.isdir(destino_app):
                # Se mueve la vieja en vez de borrarla: si la copia nueva
                # falla a la mitad, todavía existe con qué volver atrás.
                os.rename(destino_app, anterior)
            try:
                shutil.copytree(origen_app, destino_app, ignore=_ignorar_datos)
            except Exception:
                if os.path.isdir(anterior) and not os.path.isdir(destino_app):
                    os.rename(anterior, destino_app)   # volver a la anterior
                raise
            _devolver_datos_del_cliente(anterior, destino_app, self._log)
            shutil.rmtree(anterior, ignore_errors=True)
        self._log("Programas actualizados.\n")

        # 3b) La API del celular, adentro de su propio try (ver
        #     _reemplazar_celular): si falla, la caja ya quedó actualizada.
        if actualizar_cel:
            _reemplazar_celular(self.origen, destino, self._log)

        # 4) Poner la base al día (agrega columnas nuevas; no borra datos).
        if modo == "local":
            self._migrar_base(destino)

        # 5) Volver a dejar el servicio como estaba.
        if modo == "local" and servicio_estaba:
            self._arrancar_servicio(destino)

        # 5b) Registrar/actualizar el servicio de la API del celular. Nada de
        #     esto puede voltear la actualización: va entero en un try.
        exe_cel = os.path.join(destino, APP_CELULAR, "ApiCelular.exe")
        if modo == "local" and os.path.isfile(exe_cel):
            try:
                _instalar_celular(destino, pedida_ahora, self._log)
            except Exception as e:
                self._log(f"ATENCIÓN: la API del celular no quedó instalada (la caja sí): {e}\n")
            # El botón del PIN lo reevalúa el hilo de Tk, nunca este hilo.
            self._cola.put(self._reevaluar_boton_pin)

        # 6) Revisión final: todo lo que, si no, hay que ir a tipear a mano en
        #    una consola de la PC del local. Nada de esto puede voltear una
        #    actualización que ya terminó bien, así que va entero adentro de
        #    un try (ver _revision_final).
        if modo == "local":
            try:
                self._revision_final(destino)
            except Exception as e:
                # Regla 6: la actualización YA terminó bien. Que la revisión
                # no se pueda completar no puede convertirla en un fracaso ni
                # dejar la pantalla en rojo con todo correcto abajo.
                self._log(f"\n(la revisión final no se pudo completar: {e})")
                self._log("La actualización SÍ terminó bien. Revisá a mano que el servicio "
                           "esté corriendo antes de irte.")

        self._log("=== ACTUALIZACIÓN TERMINADA ===")
        self._log("Abrí la Caja y hacé una venta de prueba para confirmar que quedó todo bien.")

    # ------------------------------------------------------------------ #
    # Revisión final: lo que antes había que tipear a mano en el local
    # ------------------------------------------------------------------ #
    def _revision_final(self, destino):
        """Deja la instalación lista Y DICE si quedó lista, fila por fila.

        Cada cosa de acá es un comando que antes había que acordarse de
        correr en una consola de la PC del local, con el negocio esperando.
        Ninguna es opcional en la práctica:

          - El servicio en Automatic: estar en Manual es lo que dejó a Leo
            sin Dueño Remoto tres veces. Recién instalado no se nota.
          - El servicio corriendo: el Actualizador lo para para poder
            reemplazar el .exe, y solo lo relanza si estaba corriendo antes.
          - El PUERTO escuchando: no es lo mismo que "el servicio dice
            Running". Es lo único que le importa al Dueño Remoto.
          - El antivirus: los .exe de PyInstaller no están firmados, y este
            paso acaba de reemplazarlos TODOS. Es justo el momento en que
            Defender puede poner uno en cuarentena y dejar el negocio sin
            caja sin decir por qué.
          - El respaldo diario: si la copia más nueva es de hace días, lo
            más probable es que el servicio estuviera parado.

        Regla 6: esto corre DESPUÉS de que la actualización terminó bien.
        Que algo de acá falle no puede deshacerla ni esconderla — se anota
        NO en la tabla y se sigue.
        """
        self._log("\n=== REVISIÓN FINAL ===")
        filas = []

        def anotar(que, ok, detalle=""):
            # Esta tabla se fotografía y se manda: ninguna IP, nunca.
            detalle = _tapar_ips(detalle)
            filas.append((que, ok, detalle))
            self._log(f"   [{'SI' if ok else 'NO'}] {que}" + (f" — {detalle}" if detalle else ""))

        from pos_core import servicio_windows

        # --- el servicio arranca solo con Windows ---
        try:
            arranque = servicio_windows.tipo_de_arranque()
            if arranque == "auto":
                anotar("El servicio arranca solo con Windows", True)
            elif arranque in ("manual", "deshabilitado"):
                ok, detalle = servicio_windows.poner_en_automatico()
                anotar("El servicio arranca solo con Windows", ok,
                       "estaba en MANUAL, corregido" if ok else f"estaba en MANUAL y no se pudo corregir: {detalle}")
            else:
                anotar("El servicio arranca solo con Windows", False,
                       f"no se pudo leer el tipo de arranque ({arranque})")
        except Exception as e:
            anotar("El servicio arranca solo con Windows", False, str(e))

        # --- Windows lo reintenta si se cae ---
        _filas_servicio_stock_extra(destino, anotar)

        # --- el servicio está corriendo AHORA ---
        try:
            estado = servicio_windows.estado()
            if estado == "corriendo":
                anotar("El servicio de stock está corriendo", True)
            elif estado == "parado":
                ok, detalle = servicio_windows.arrancar()
                anotar("El servicio de stock está corriendo", ok,
                       "estaba parado, arrancado" if ok else f"estaba parado y no arrancó: {detalle}")
            else:
                anotar("El servicio de stock está corriendo", False, f"estado: {estado}")
        except Exception as e:
            anotar("El servicio de stock está corriendo", False, str(e))

        # --- en el puerto contesta LA API REMOTA (no cualquier programa) ---
        try:
            puerto = self._puerto_remoto(destino)
            contesta, detalle = servicio_windows.remote_api_contesta(puerto)
            if not contesta and detalle == "no hay nadie escuchando":
                detalle += (": el servicio puede decir Running igual, la API se levanta "
                            "adentro y si falla solo lo anota en el log")
            anotar(f"En el {puerto} contesta la API remota (es lo que usa Leo)", contesta,
                   "" if contesta else detalle)
        except Exception as e:
            anotar("En el puerto de la API remota contesta ella (es lo que usa Leo)", False, str(e))

        # --- antivirus ---
        try:
            ok, detalle = self._excluir_del_antivirus(destino)
            anotar(f"«{destino}» excluido del antivirus", ok, detalle)
        except Exception as e:
            anotar(f"«{destino}» excluido del antivirus", False, str(e))

        # --- respaldo diario al día ---
        try:
            ok, detalle = self._estado_de_los_respaldos(destino)
            anotar("Hay una copia de la base reciente", ok, detalle)
        except Exception as e:
            anotar("Hay una copia de la base reciente", False, str(e))

        # --- llevarse la evidencia del blindaje ---
        try:
            ok, detalle = self._rescatar_evidencia(destino)
            if detalle:
                anotar("Evidencia del blindaje copiada al Escritorio", ok, detalle)
        except Exception as e:
            anotar("Evidencia del blindaje copiada al Escritorio", False, str(e))

        # --- la API del celular (opcional) ---
        _filas_celular(destino, anotar)

        pendientes = [q for q, ok, _ in filas if not ok]
        if pendientes:
            self._log("\n   QUEDA PENDIENTE: " + "; ".join(pendientes))
            self._log("   Sacale una foto a esta pantalla antes de irte del local.")
        else:
            self._log("\n   Todo en SI. La instalación quedó lista.")
        self._guardar_informe(filas)

    def _puerto_remoto(self, destino) -> int:
        """El puerto que el cliente tiene configurado, no el que suponemos."""
        from pos_core import servicio_windows
        ruta = os.path.join(destino, "config.ini")
        try:
            import configparser
            cfg = configparser.ConfigParser()
            cfg.read(ruta, encoding="utf-8")
            return int(cfg.get("remoto", "puerto",
                                fallback=str(servicio_windows.PUERTO_POR_DEFECTO)))
        except Exception:
            return servicio_windows.PUERTO_POR_DEFECTO

    def _excluir_del_antivirus(self, destino) -> tuple:
        """Saca la carpeta de Otter del análisis de Windows Defender.

        Va acá y no en el blindaje porque el momento en que hace falta es
        EXACTAMENTE este: acabamos de reemplazar todos los .exe, no están
        firmados, y un antivirus que ponga uno en cuarentena deja el
        negocio sin caja sin avisar. Si algún día el blindaje toma este
        paso, sacarlo de acá y llamarlo desde allá — no dejar los dos.
        """
        if os.name != "nt":
            return False, "solo aplica en Windows"
        if not es_administrador():
            return False, "hace falta ejecutar como administrador"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # Se agrega y después se LEE LA LISTA de vuelta: dar por buena una
        # exclusión porque el comando no tiró error es una verificación que
        # no verifica.
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                        f"Add-MpPreference -ExclusionPath '{destino}'"],
                       capture_output=True, text=True, timeout=60, creationflags=creationflags)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                            "(Get-MpPreference).ExclusionPath -join ';'"],
                           capture_output=True, text=True, timeout=60, creationflags=creationflags)
        listado = (r.stdout or "")
        if destino.lower() in listado.lower():
            return True, "confirmado en la lista de exclusiones"
        if "Get-MpPreference" in (r.stderr or "") or not listado.strip():
            return False, "no parece haber Windows Defender: revisá a mano el antivirus que usen"
        return False, "el comando corrió pero la carpeta no quedó en la lista"

    def _estado_de_los_respaldos(self, destino) -> tuple:
        """¿Hay copia reciente? Una copia de hace tres semanas es casi lo
        mismo que ninguna, y si está atrasada lo más probable es que el
        servicio haya estado parado."""
        import glob
        carpeta = os.path.join(destino, "backups")
        copias = sorted(glob.glob(os.path.join(carpeta, "stock_*.db")),
                        key=os.path.getmtime, reverse=True)
        if not copias:
            return False, ("no hay ninguna copia diaria todavía; aparece a los segundos de "
                           "arrancar el servicio, así que si el servicio quedó en SI, esperá y mirá de nuevo")
        dias = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(copias[0]))).days
        nombre = os.path.basename(copias[0])
        if dias <= 1:
            return True, f"{nombre} ({len(copias)} copias guardadas)"
        return False, f"la más nueva es {nombre}, de hace {dias} días: ¿estuvo parado el servicio?"

    def _rescatar_evidencia(self, destino) -> tuple:
        """Copia al Escritorio el estado que guardó el blindaje antes de
        tocar nada. Es un archivo único: el próximo blindaje lo pisa, y es
        la última pista de por qué se paró el servicio."""
        origen = os.path.join(destino, "watchdog", "estado_antes_del_blindaje.txt")
        if not os.path.isfile(origen):
            return True, ""    # no hubo blindaje en esta PC: no es un pendiente
        destino_archivo = os.path.join(_escritorio(), "estado_antes_del_blindaje.txt")
        shutil.copy2(origen, destino_archivo)
        return True, destino_archivo

    def _guardar_informe(self, filas):
        """Deja la tabla en el Escritorio para poder mandarla sin tipearla."""
        try:
            ruta = os.path.join(_escritorio(), "otter_revision_final.txt")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(f"REVISION FINAL DE OTTER — {datetime.now():%Y-%m-%d %H:%M}\n\n")
                for que, ok, detalle in filas:
                    f.write(f"[{'SI' if ok else 'NO'}] {que}\n")
                    if detalle:
                        f.write(f"     {detalle}\n")
            self._log(f"   Informe guardado en: {ruta}")
        except Exception as e:
            self._log(f"   (no se pudo guardar el informe: {e})")

    # ------------------------------------------------------------------ #
    def _respaldar_base(self, destino):
        base = os.path.join(destino, "database", "stock.db")
        if not os.path.isfile(base):
            self._log("(no hay base de datos todavía: no hace falta respaldar)\n")
            return
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        copia = os.path.join(destino, "database", f"stock.db.backup_{sello}")
        # Se usa la API de backup de SQLite y no una copia de archivo: con
        # WAL, copiar el .db suelto puede llevarse una base a medio escribir.
        try:
            import sqlite3
            origen = sqlite3.connect(base)
            respaldo = sqlite3.connect(copia)
            with respaldo:
                origen.backup(respaldo)
            respaldo.close()
            origen.close()
            mb = os.path.getsize(copia) / (1024 * 1024)
            self._log(f"Copia de seguridad hecha ({mb:.1f} MB):\n   {copia}\n")
        except Exception as e:
            raise RuntimeError(f"No se pudo respaldar la base, se corta acá para no arriesgarla: {e}")

    def _migrar_base(self, destino):
        self._log("Poniendo la base de datos al día...")
        try:
            from pos_core import paths
            paths.set_base_override(destino)
            from pos_core.db import aplicar_migraciones
            cambios = aplicar_migraciones()
        except Exception as e:
            raise RuntimeError(f"No se pudo actualizar la estructura de la base: {e}")
        if cambios:
            for c in cambios:
                self._log(f"   {c}")
        else:
            self._log("   La base ya estaba al día.")
        self._log("")

    def _estado_servicio(self) -> str:
        """'corriendo' | 'parado' | 'desconocido'. Nunca lanza.

        Preguntarle a Windows por el servicio no puede ser lo que voltee
        una actualización: si `sc` no está o contesta cualquier cosa, se
        sigue adelante y se avisa.
        """
        try:
            r = subprocess.run(["sc", "query", NOMBRE_SERVICIO],
                                capture_output=True, text=True, timeout=30)
            salida = (r.stdout or "").upper()
            if "RUNNING" in salida:
                return "corriendo"
            if "STOPPED" in salida or "1060" in salida:   # 1060 = no existe
                return "parado"
        except Exception:
            pass
        return "desconocido"

    def _parar_servicio(self, destino) -> bool:
        """Para el servicio para poder reemplazar su .exe. True si estaba corriendo."""
        exe = os.path.join(destino, "StockService", "StockService.exe")
        if not os.path.isfile(exe):
            return False

        estado = self._estado_servicio()
        if estado == "corriendo" and not es_administrador():
            # Se corta ACÁ, antes de tocar un solo archivo: sin parar el
            # servicio, Windows no deja reemplazar su .exe y la
            # actualización quedaría a medias. Nada cambió todavía.
            raise PermissionError(
                "El servicio de stock está corriendo y hay que pararlo para poder\n"
                "reemplazarlo, pero esto no está corriendo como administrador.\n"
                "Cerrá y volvé a abrir con botón derecho -> 'Ejecutar como administrador'.")
        if estado == "parado":
            self._log("(el servicio de stock no estaba corriendo)\n")
            return False

        self._log("Parando el servicio de stock...")
        try:
            subprocess.run([exe, "stop"], capture_output=True, text=True, timeout=60)
            import time
            for _ in range(20):
                if self._estado_servicio() != "corriendo":
                    break
                time.sleep(0.5)
            self._log("   Servicio detenido.\n")
        except Exception as e:
            self._log(f"   (no se pudo parar el servicio: {e})\n")
        return True

    def _arrancar_servicio(self, destino):
        """Deja el servicio andando de nuevo. Nunca voltea la actualización.

        Si algo falla acá, los programas YA quedaron actualizados: cortar
        con un error dejaría al cliente pensando que se rompió todo. Se
        avisa con letras claras y se sigue.
        """
        exe = os.path.join(destino, "StockService", "StockService.exe")
        self._log("Volviendo a arrancar el servicio de stock...")
        try:
            # Se reinstala apuntando al .exe nuevo: el servicio registrado
            # guarda la ruta del ejecutable, así que reinstalar deja el
            # registro coherente con lo que se acaba de copiar.
            subprocess.run([exe, "remove"], capture_output=True, text=True, timeout=60)
            r = subprocess.run([exe, "install"], capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                r = subprocess.run([exe, "start"], capture_output=True, text=True, timeout=120)
            if self._estado_servicio() == "corriendo":
                self._log("   Servicio corriendo de nuevo.\n")
                return
            detalle = f"{(r.stdout or '').strip()} {(r.stderr or '').strip()}".strip()
        except Exception as e:
            detalle = str(e)

        self._log("   ATENCIÓN: los programas quedaron actualizados, pero el servicio de\n"
                   "   stock no volvió a arrancar solo. Las alertas de Telegram y el acceso\n"
                   "   del Dueño Remoto no van a andar hasta que arranque. Probá con:\n"
                   f"      {exe} install\n      {exe} start\n"
                   "   (en una consola como administrador), o reiniciá la PC.\n"
                   f"   Detalle: {detalle}\n")


def main():
    Actualizador().mainloop()


if __name__ == "__main__":
    main()
