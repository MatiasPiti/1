"""API de la app del celular ("Panel Dueño", Android), puerto 8766.

Corre como servicio de Windows propio (ApiCelular.exe, ver
services/api_celular_servicio.py), aparte del StockService y de la API
remota del 8765, que no se tocan. Es una capa fina HTTP/JSON sobre pos_core:
no escribe SQL (las lecturas nuevas están en pos_core/panel_celular.py) y
las escrituras pasan por las mismas funciones que usa el Panel.

Reglas que esto cuida (ver la espec y CLAUDE.md):
- Regla 1: solo se entra por Tailscale. Además del firewall, el primer
  middleware exige que la IP de origen Y la local sean de Tailscale y
  distintas entre sí; desde la propia PC solo se ve /api/salud.
- Nunca crea la base ni corre migraciones: abre todo con
  db.usar_solo_base_existente (mode=rw) y si falta la base lo dice en
  /api/salud y contesta 503, sin caerse.
- Nada de monitor de Telegram (lo sigue haciendo el StockService).
- Regla 4: nunca devuelve ni loguea el token del bot, el PIN ni la sesión.

FastAPI, pydantic y uvicorn se importan recién en crear_app() /
iniciar_servidor(): el servicio queda Running al instante aunque esas
importaciones tarden, y los verbos de consola no las necesitan.
"""

import functools
import ipaddress
import json
import logging
import math
import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.request
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

from pos_core import acceso_celular, config, db, panel_celular, paths

FIRMA = "otter-api-celular"
CONTRATO = 2
VERSION_API = "2.0.0"
PUERTO_POR_DEFECTO = 8766
SERVICIO = "SistemaDualApiCelular"

ORIGEN = "MAESTRO"
LIMITE_JSON = 1024 * 1024
LIMITE_PDF = 20 * 1024 * 1024
RUTA_ANALIZAR = "/api/facturas/analizar"

log = logging.getLogger("api_celular")
log_cambios = logging.getLogger("api_celular.cambios")

REDES_TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))

TEXTO_BASE_NO = ("La API no encuentra la base del negocio: ApiCelular tiene que estar en "
                 "C:\\SistemaDual\\ApiCelular.")
TEXTO_BASE_VIEJA = ("La base del negocio tiene una versión vieja: abrí la Caja una vez o corré el "
                    "Actualizador. No se tocó nada.")
TEXTO_BASE_OCUPADA = "La base está ocupada: no se aplicó nada. Probá de nuevo."
TEXTO_SECRETO = ("El archivo de seguridad de la API está dañado: hay que revisarlo en la PC "
                 "(no se regeneró nada).")
TEXTO_SESION = "Sesión vencida o inválida. Ingresá el PIN de nuevo."
TEXTO_RED = "Solo se aceptan conexiones por Tailscale."
TEXTO_INTERNO = "Error inesperado en la PC: quedó anotado en el log."
TEXTOS_PIN = {
    "no_definido": "Todavía no hay PIN para el celular: hay que definirlo en la PC del local.",
    "anulado": "El PIN viejo quedó anulado: hay que definir uno nuevo en la PC del local.",
    "secreto_perdido": "Se perdió el secreto de la API: hay que volver a definir el PIN en la PC del local.",
}


# --------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------- #

class FormatterQueTapa(logging.Formatter):
    """Tapa secretos en el texto FINAL (mensaje + traceback).

    Va en el Formatter y no en un logging.Filter a propósito: el traceback
    lo arma el Formatter DESPUÉS de los filtros, y con un filtro el token
    del bot aparecía entero en la traza de una excepción (verificado).
    """

    PATRONES = (
        re.compile(r"bot\d+:[\w-]+"),            # URL de Telegram con el token del bot
        re.compile(r"\b\d{5,}:[\w-]{8,}"),        # el token del bot suelto
        re.compile(r"Bearer \S+"),                # encabezado Authorization
        re.compile(r"v2\.[\w-]+\.[\w-]+"),        # token de sesión de la app
    )

    def format(self, record):
        texto = super().format(record)
        for patron in self.PATRONES:
            texto = patron.sub("[TAPADO]", texto)
        return texto


_handlers_propios = []


def configurar_logs(modo: str = "servicio") -> None:
    """Logs rotativos en <base>/logs.

    modo "servicio" (también consola): api_celular.log (2 MB x 5) y
    api_celular_cambios.log (1 MB x 10). modo "cli": api_celular_cli.log
    (1 MB x 3) y NADA más: en Windows rotar un archivo que otro proceso
    tiene abierto falla y el archivo crece sin límite, así que la consola y
    el servicio nunca comparten archivo.
    """
    carpeta = paths.logs_dir()
    raiz = logging.getLogger()
    cerrar_logs()

    formato = FormatterQueTapa("%(asctime)s %(levelname)s %(message)s")
    if modo == "cli":
        principal = RotatingFileHandler(os.path.join(carpeta, "api_celular_cli.log"),
                                        maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    else:
        principal = RotatingFileHandler(os.path.join(carpeta, "api_celular.log"),
                                        maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8")
    principal.setFormatter(formato)
    raiz.addHandler(principal)
    raiz.setLevel(logging.INFO)
    _handlers_propios.append((raiz, principal))
    # uvicorn y uvicorn.error van al mismo archivo (propagan a la raíz). El
    # access log de uvicorn queda apagado: incluye la query string.
    for nombre in ("uvicorn", "uvicorn.error"):
        logging.getLogger(nombre).propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)

    log_cambios.propagate = False
    if modo != "cli":
        cambios = RotatingFileHandler(os.path.join(carpeta, "api_celular_cambios.log"),
                                      maxBytes=1024 * 1024, backupCount=10, encoding="utf-8")
        cambios.setFormatter(FormatterQueTapa("%(message)s"))
        log_cambios.addHandler(cambios)
        log_cambios.setLevel(logging.INFO)
        _handlers_propios.append((log_cambios, cambios))


def cerrar_logs() -> None:
    """Suelta los archivos de log (la autoprueba borra su carpeta al final, y
    en Windows no se puede borrar un archivo abierto)."""
    for logger, handler in _handlers_propios:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _handlers_propios.clear()


_anotados = {}
_lock_anotados = threading.Lock()
_reloj_anotar = time.monotonic


def _anotar_cada_hora(clave: str, texto: str, nivel: int = logging.WARNING) -> bool:
    """Errores que se repiten (bind, hilo caído, autochequeo, config): una
    línea por hora por tipo, con la cuenta. Sin esto un puerto ocupado
    escribiría una línea por minuto para siempre."""
    with _lock_anotados:
        ahora = _reloj_anotar()
        ultima, cuenta = _anotados.get(clave, (None, 0))
        if ultima is not None and ahora - ultima < 3600:
            _anotados[clave] = (ultima, cuenta + 1)
            return False
        _anotados[clave] = (ahora, 0)
    log.log(nivel, texto + (f" (se repitió {cuenta} veces desde la anotación anterior)" if cuenta else ""))
    return True


# --------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------- #

class ErrorApi(Exception):
    """Un error con su forma del contrato: {"detail", "codigo", ...extra}."""

    def __init__(self, status: int, codigo: str, detalle: str, **extra):
        super().__init__(detalle)
        self.status, self.codigo, self.detalle, self.extra = status, codigo, detalle, extra

    def contenido(self) -> dict:
        return {"detail": self.detalle, "codigo": self.codigo, **self.extra}


def _no_existe(codigo: str) -> ErrorApi:
    return ErrorApi(404, "no_existe", f"Producto con código '{codigo}' no existe o está inactivo")


def _numero_ar(valor: float) -> str:
    """2420 -> '2.420'; 2479.34 -> '2.479,34' (a la argentina, como la app)."""
    valor = round(float(valor), 2)
    entero, centavos = divmod(round(abs(valor) * 100), 100)
    texto = f"{int(entero):,}".replace(",", ".")
    if centavos:
        texto += f",{int(centavos):02d}"
    return ("-" if valor < 0 else "") + texto


_NOMBRE_CAMPO_PRECIO = {"costo_sin_iva": "El costo sin IVA", "precio_compra": "El precio de costo",
                        "margen_ganancia": "El % de ganancia", "precio_venta": "El precio final"}


def texto_precio_cambio(campo: str, antes: float, ahora: float) -> str:
    if campo == "margen_ganancia":
        valores = f"era {_numero_ar(antes)} %, ahora es {_numero_ar(ahora)} %"
    else:
        valores = f"era $ {_numero_ar(antes)}, ahora es $ {_numero_ar(ahora)}"
    return f"{_NOMBRE_CAMPO_PRECIO.get(campo, campo)} cambió mientras tanto: {valores}. Revisalo de nuevo."


def _texto_numero(valor) -> str:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor)


def traducir_error_validacion(errores: list) -> tuple:
    """(status, contenido) para un error de pydantic. NUNCA devuelve el valor
    recibido (el campo "input" de pydantic podría traer el PIN)."""
    if any(e.get("type") == "extra_forbidden" for e in errores):
        return 422, {"detail": "El token y el chat del bot se cargan en el Panel de la PC.",
                     "codigo": "solo_en_la_pc"}
    e = errores[0] if errores else {}
    tipo, ctx = e.get("type", ""), e.get("ctx") or {}
    loc = [str(x) for x in e.get("loc", ())]
    campo = ".".join(loc[1:]) if len(loc) > 1 and loc[0] in ("body", "query", "path", "header") else ".".join(loc)
    if tipo == "missing":
        mensaje = "falta"
    elif tipo == "greater_than":
        mensaje = f"tiene que ser mayor a {_texto_numero(ctx.get('gt'))}"
    elif tipo == "greater_than_equal":
        mensaje = f"tiene que ser mayor o igual a {_texto_numero(ctx.get('ge'))}"
    elif tipo == "less_than":
        mensaje = f"tiene que ser menor a {_texto_numero(ctx.get('lt'))}"
    elif tipo == "less_than_equal":
        mensaje = f"tiene que ser menor o igual a {_texto_numero(ctx.get('le'))}"
    elif tipo in ("int_parsing", "int_type", "int_from_float"):
        mensaje = "tiene que ser un número entero"
    elif tipo in ("float_parsing", "float_type", "finite_number"):
        mensaje = "tiene que ser un número"
    elif tipo in ("string_too_short", "string_too_long", "too_short", "too_long"):
        mensaje = "largo inválido"
    elif tipo == "literal_error":
        mensaje = "valor no permitido"
    elif tipo in ("bool_parsing", "bool_type"):
        mensaje = "tiene que ser verdadero o falso"
    elif tipo == "value_error":
        mensaje = str(e.get("msg", "")).replace("Value error, ", "", 1) or "valor inválido"
    else:
        mensaje = "valor inválido"
    return 422, {"detail": f"Dato inválido en «{campo}»: {mensaje}", "codigo": "dato_invalido", "campo": campo}


# --------------------------------------------------------------------- #
# Middlewares ASGI puros (no dependen de FastAPI)
# --------------------------------------------------------------------- #

async def _responder_json(send, status: int, contenido: dict) -> None:
    cuerpo = json.dumps(contenido, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(cuerpo)).encode("ascii"))]})
    await send({"type": "http.response.body", "body": cuerpo})


def _ip(texto):
    """ip_address o None. Una IPv6 que es una IPv4 mapeada se desarma."""
    try:
        ip = ipaddress.ip_address(str(texto).split("%", 1)[0])
    except (ValueError, TypeError):
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def es_tailscale(ip) -> bool:
    return ip is not None and any(ip.version == red.version and ip in red for red in REDES_TAILSCALE)


def es_loopback(ip) -> bool:
    return ip is not None and ip.is_loopback


def _ip_cliente(scope) -> str:
    cliente = scope.get("client")
    return str(cliente[0]) if cliente else "-"


class FiltroRedMiddleware:
    """Primera barrera de la regla 1, en el código (las otras dos son la
    regla de firewall atada a la placa de Tailscale y la app).

    Pasa solo si la IP de origen Y la local están en el rango de Tailscale y
    son DISTINTAS: una conexión de la PC a su propia 100.x llega con origen
    == destino, y así entraría un `tailscale serve` o un `funnel` montado
    encima (que la publicaría en internet). Desde loopback solo GET
    /api/salud: por ahí entran serve/funnel, y el autochequeo y el watchdog
    solo necesitan la salud. No hay ninguna clave de config que lo afloje.
    """

    def __init__(self, app, permitir_loopback_total: bool = False):
        self.app = app
        self.permitir_loopback_total = permitir_loopback_total

    def _pasa(self, scope) -> bool:
        cliente, servidor = scope.get("client") or (None,), scope.get("server") or (None,)
        c, s = _ip(cliente[0]), _ip(servidor[0])
        if c is not None and es_loopback(c):
            return self.permitir_loopback_total or (scope.get("method") == "GET"
                                                     and scope.get("path") == "/api/salud")
        if c is not None and s is not None and c == s:
            return False
        return c is not None and s is not None and es_tailscale(c) and es_tailscale(s)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        if self._pasa(scope):
            await self.app(scope, receive, send)
            return
        ip = _ip_cliente(scope)
        _anotar_cada_hora(f"red:{ip}", f"RECHAZO de red ip={ip} {scope.get('method')} {scope.get('path')}",
                          logging.INFO)
        await _responder_json(send, 403, {"detail": TEXTO_RED, "codigo": "red_no_permitida"})


class LogPedidosMiddleware:
    """Una línea por pedido, sin query string ni cuerpos ni encabezados."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio, estado = time.perf_counter(), {"status": 500}

        async def send_registrando(mensaje):
            if mensaje["type"] == "http.response.start":
                estado["status"] = mensaje["status"]
            await send(mensaje)

        try:
            await self.app(scope, receive, send_registrando)
        finally:
            log.info("PEDIDO ip=%s sesion=%s %s %s %s %dms", _ip_cliente(scope), scope.get("otter_sesion") or "-",
                     scope.get("method"), scope.get("path"), estado["status"],
                     (time.perf_counter() - inicio) * 1000)


_CLASE_CUERPO_GRANDE = []


def _cuerpo_demasiado_grande(detalle: str):
    """HTTPException a propósito: FastAPI convierte cualquier otra excepción
    al leer el cuerpo en un 400 genérico, y deja pasar las HTTPException."""
    if not _CLASE_CUERPO_GRANDE:
        from starlette.exceptions import HTTPException

        class CuerpoDemasiadoGrande(HTTPException):
            otter_413 = True

        _CLASE_CUERPO_GRANDE.append(CuerpoDemasiadoGrande)
    return _CLASE_CUERPO_GRANDE[0](status_code=413, detail=detalle)


class LimiteCuerpoMiddleware:
    """Corta con 413 un cuerpo más grande que el límite de la ruta ANTES de
    que llegue a memoria o a disco. Mira el Content-Length declarado y además
    cuenta los bytes que llegan (un envío chunked tampoco lo esquiva)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == RUTA_ANALIZAR:
            limite, detalle = LIMITE_PDF, "El PDF supera los 20 MB."
        else:
            limite, detalle = LIMITE_JSON, "El pedido es demasiado grande."
        for nombre, valor in scope.get("headers", []):
            if nombre.lower() == b"content-length":
                try:
                    declarado = int(valor)
                except ValueError:
                    continue
                if declarado > limite:
                    await _responder_json(send, 413, {"detail": detalle, "codigo": "demasiado_grande"})
                    return
        recibidos, iniciada = 0, False

        async def receive_con_limite():
            nonlocal recibidos
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibidos += len(mensaje.get("body", b""))
                if recibidos > limite:
                    raise _cuerpo_demasiado_grande(detalle)
            return mensaje

        async def send_registrando(mensaje):
            nonlocal iniciada
            if mensaje["type"] == "http.response.start":
                iniciada = True
            await send(mensaje)

        try:
            await self.app(scope, receive_con_limite, send_registrando)
        except Exception as e:
            if not getattr(e, "otter_413", False) or iniciada:
                raise
            await _responder_json(send, 413, {"detail": detalle, "codigo": "demasiado_grande"})


class CapturaErroresMiddleware:
    """Cualquier excepción que no tenga manejador propio: 500 con texto fijo
    y el traceback (tapado) solo en el log. Va adentro de los demás
    middlewares para que el log de pedidos lo registre como cualquier otro."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        iniciada = False

        async def send_registrando(mensaje):
            nonlocal iniciada
            if mensaje["type"] == "http.response.start":
                iniciada = True
            await send(mensaje)

        try:
            await self.app(scope, receive, send_registrando)
        except Exception:
            log.exception("ERROR atendiendo %s %s", scope.get("method"), scope.get("path"))
            if not iniciada:
                await _responder_json(send, 500, {"detail": TEXTO_INTERNO, "codigo": "error_interno"})


# --------------------------------------------------------------------- #
# Bloqueo por intentos de PIN
# --------------------------------------------------------------------- #

MAX_INTENTOS_PIN = 5
BLOQUEO_SEGUNDOS = 300
VENTANA_FALLOS_GLOBALES = 300
MAX_FALLOS_GLOBALES = 20


class BloqueoIntentos:
    """5 fallas desde una IP la bloquean 5 minutos; más de 20 fallas en 5
    minutos sumando todas las IPs bloquean TODOS los logins 5 minutos. Vive
    en memoria: reiniciar el servicio lo limpia."""

    def __init__(self, reloj=time.time):
        self._reloj = reloj
        self._lock = threading.Lock()
        self._fallos, self._bloqueado_hasta = {}, {}
        self._fallos_globales = deque()
        self._bloqueo_global_hasta = 0.0

    def segundos_restantes(self, ip: str) -> int:
        with self._lock:
            hasta = max(self._bloqueado_hasta.get(ip, 0), self._bloqueo_global_hasta)
            return max(0, math.ceil(hasta - self._reloj()))

    def registrar_fallo(self, ip: str) -> dict:
        with self._lock:
            ahora = self._reloj()
            self._fallos[ip] = self._fallos.get(ip, 0) + 1
            fallos, bloqueo_ip, bloqueo_global = self._fallos[ip], False, False
            if self._fallos[ip] >= MAX_INTENTOS_PIN:
                self._bloqueado_hasta[ip] = ahora + BLOQUEO_SEGUNDOS
                self._fallos[ip] = 0
                bloqueo_ip = True
            self._fallos_globales.append(ahora)
            while self._fallos_globales and self._fallos_globales[0] <= ahora - VENTANA_FALLOS_GLOBALES:
                self._fallos_globales.popleft()
            if len(self._fallos_globales) > MAX_FALLOS_GLOBALES:
                self._bloqueo_global_hasta = ahora + BLOQUEO_SEGUNDOS
                self._fallos_globales.clear()
                bloqueo_global = True
            return {"fallos": fallos, "bloqueo_ip": bloqueo_ip, "bloqueo_global": bloqueo_global}

    def registrar_exito(self, ip: str) -> None:
        with self._lock:
            self._fallos.pop(ip, None)
            self._bloqueado_hasta.pop(ip, None)


# --------------------------------------------------------------------- #
# Estado que muestra /api/salud
# --------------------------------------------------------------------- #

def compilado() -> str:
    """version.txt al lado del exe (lo escribe compilar_api_celular.bat)."""
    if not getattr(sys, "frozen", False):
        return "desarrollo"
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "version.txt"),
                  "r", encoding="utf-8-sig") as f:
            texto = f.read().strip()
    except OSError:
        texto = ""
    return texto[:40] or "desconocido"


def _ahora_texto() -> str:
    return datetime.now().isoformat(timespec="seconds")


class EstadoApi:
    """Lo que muestra /api/salud. Lo refresca el Supervisor (cada 15 s) o la
    prueba a mano. salud NUNCA lo calcula en el pedido: así contesta aunque
    la base o los hilos estén trabados."""

    def __init__(self):
        self._lock = threading.Lock()
        self._compilado = compilado()
        self._datos = {"compilado": self._compilado, "nombre_local": "", "estado_al": _ahora_texto(),
                       "pin_configurado": False, "login_disponible": False, "motivo": "base_no_disponible",
                       "base_ok": False, "base_detalle": "todavía no se revisó", "estado_pin": "base_no_disponible"}

    def refrescar(self) -> None:
        """Nunca lanza."""
        try:
            base = panel_celular.estado_base()
            if base["motivo"] == "base_no_disponible":
                estado_pin = "base_no_disponible"
            else:
                estado_pin = acceso_celular.estado_pin()
            if not base["ok"]:
                motivo = base["motivo"]
            elif estado_pin == "secreto_ilegible":
                motivo = "secreto_ilegible"
            elif estado_pin == "base_no_disponible":
                motivo = "base_no_disponible"
            elif estado_pin != "definido":
                motivo = "pin_no_definido"
            else:
                motivo = None
            datos = {"compilado": self._compilado, "nombre_local": panel_celular.nombre_local_real(),
                     "estado_al": _ahora_texto(), "pin_configurado": estado_pin == "definido",
                     "login_disponible": motivo is None, "motivo": motivo, "base_ok": bool(base["ok"]),
                     "base_detalle": base["detalle"], "estado_pin": estado_pin}
            with self._lock:
                self._datos = datos
        except Exception:
            log.exception("No se pudo refrescar el estado de /api/salud")
        finally:
            db.cerrar_conexion()

    def instantanea(self) -> dict:
        with self._lock:
            return dict(self._datos, refrescado_en=self._datos["estado_al"])


def salud(estado: EstadoApi) -> dict:
    d = estado.instantanea()
    return {"ok": True, "servicio": FIRMA, "contrato": CONTRATO, "version_api": VERSION_API,
            "compilado": d["compilado"], "nombre_local": d["nombre_local"], "hora_servidor": _ahora_texto(),
            "estado_al": d["estado_al"], "pin_configurado": d["pin_configurado"],
            "login_disponible": d["login_disponible"], "motivo": d["motivo"],
            "base": {"ok": d["base_ok"], "detalle": d["base_detalle"]}}


# --------------------------------------------------------------------- #
# La app
# --------------------------------------------------------------------- #

def con_base(funcion):
    """Cierra la conexión SQLite del hilo al terminar CADA pedido, en el
    mismo hilo (una dependencia con yield puede cerrarse en otro hilo del
    pool). Así la API no queda con stock.db abierta frente a una
    restauración de arranque.py, que en Windows no puede pisar un archivo
    abierto."""
    @functools.wraps(funcion)
    def envoltura(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        finally:
            db.cerrar_conexion()
    return envoltura


@contextmanager
def _como_dato_invalido():
    """Las validaciones de pos_core (ValueError) son un 400 con su texto."""
    from pos_core.precios import PrecioCambiadoError
    try:
        yield
    except PrecioCambiadoError:
        raise
    except ValueError as e:
        raise ErrorApi(400, "dato_invalido", str(e)) from e


def _sin_repetir(codigos: list) -> list:
    vistos, salida = set(), []
    for c in codigos:
        if c not in vistos:
            vistos.add(c)
            salida.append(c)
    return salida


def _finito_o_none(valor):
    return valor if isinstance(valor, (int, float)) and math.isfinite(valor) else None


def crear_app(estado: EstadoApi = None, permitir_loopback_total: bool = False):
    """La app FastAPI. `permitir_loopback_total` es SOLO para la autoprueba y
    las pruebas (deja entrar a todo desde 127.0.0.1): ninguna clave de config
    lo activa."""
    from typing import Annotated, Dict, List, Literal, Optional, Union

    import anyio.to_thread
    from fastapi import Depends, FastAPI, Query, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field
    from starlette.concurrency import run_in_threadpool
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.middleware import Middleware

    from pos_core import alerts, bulk_edit, precios, stock_service

    db.usar_solo_base_existente(panel_celular.ruta_base_datos())
    estado = estado or EstadoApi()

    @asynccontextmanager
    async def lifespan(_app):
        # 8 hilos: una factura grande o un ajuste de 2000 productos no pueden
        # tomar la PC entera (la caja corre al lado).
        anyio.to_thread.current_default_thread_limiter().total_tokens = 8
        await anyio.to_thread.run_sync(estado.refrescar)
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan, middleware=[
        Middleware(FiltroRedMiddleware, permitir_loopback_total=permitir_loopback_total),
        Middleware(LogPedidosMiddleware),
        Middleware(LimiteCuerpoMiddleware),
        Middleware(CapturaErroresMiddleware),
    ])
    app.state.estado = estado

    # ------------------------------------------------------------------ #
    # Manejo de errores
    # ------------------------------------------------------------------ #

    async def _de_error_api(_req, exc: ErrorApi):
        return JSONResponse(status_code=exc.status, content=exc.contenido())

    async def _de_http(_req, exc):
        if getattr(exc, "otter_413", False):
            return JSONResponse(status_code=413, content={"detail": exc.detail, "codigo": "demasiado_grande"})
        if exc.status_code in (404, 405):
            return JSONResponse(status_code=404, content={
                "detail": "Esa función no existe en la PC del local: hay que actualizarla.",
                "codigo": "ruta_inexistente"})
        return JSONResponse(status_code=400, content={"detail": "El pedido está mal armado.",
                                                      "codigo": "dato_invalido"})

    async def _de_validacion(_req, exc):
        status, contenido = traducir_error_validacion(exc.errors())
        return JSONResponse(status_code=status, content=contenido)

    async def _de_no_encontrado(_req, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc), "codigo": "no_existe"})

    async def _de_stock_insuficiente(_req, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc), "codigo": "stock_insuficiente"})

    async def _de_precio_cambiado(_req, exc):
        return JSONResponse(status_code=409, content={
            "detail": texto_precio_cambio(exc.campo, exc.antes, exc.ahora), "codigo": "precio_cambio",
            "campo": exc.campo})

    async def _de_sqlite(_req, exc):
        texto = str(exc).lower()
        if "unable to open database file" in texto:
            return JSONResponse(status_code=503, content={"detail": TEXTO_BASE_NO, "codigo": "base_no_disponible"})
        if "locked" in texto or "busy" in texto:
            return JSONResponse(status_code=503, content={"detail": TEXTO_BASE_OCUPADA, "codigo": "base_ocupada"})
        raise exc

    async def _de_runtime(_req, exc):
        if str(exc).startswith("No se pudo aplicar el movimiento de stock tras"):
            return JSONResponse(status_code=503, content={"detail": TEXTO_BASE_OCUPADA, "codigo": "base_ocupada"})
        raise exc

    async def _de_config_ilegible(_req, _exc):
        return JSONResponse(status_code=503, content={"detail": "No se pudo leer config.ini en la PC: no se tocó nada.",
                                                      "codigo": "config_ilegible"})

    async def _de_secreto(_req, _exc):
        return JSONResponse(status_code=503, content={"detail": TEXTO_SECRETO, "codigo": "secreto_ilegible"})

    async def _de_pdf(_req, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc), "codigo": "pdf_danado"})

    for clase, manejador in (
            (ErrorApi, _de_error_api), (StarletteHTTPException, _de_http),
            (RequestValidationError, _de_validacion),
            (stock_service.ProductoNoEncontradoError, _de_no_encontrado),
            (stock_service.StockInsuficienteError, _de_stock_insuficiente),
            (precios.PrecioCambiadoError, _de_precio_cambiado), (sqlite3.Error, _de_sqlite),
            (RuntimeError, _de_runtime), (config.ConfigIlegibleError, _de_config_ilegible),
            (acceso_celular.SecretoIlegibleError, _de_secreto), (panel_celular.PdfInvalidoError, _de_pdf)):
        app.add_exception_handler(clase, manejador)

    # ------------------------------------------------------------------ #
    # Dependencias
    # ------------------------------------------------------------------ #

    def base_lista() -> bool:
        """Antes de cualquier cosa que toque la base (FastAPI resuelve las
        dependencias ANTES de entrar al endpoint, así que el chequeo vive acá)."""
        try:
            if not os.path.isfile(panel_celular.ruta_base_datos()):
                raise ErrorApi(503, "base_no_disponible", TEXTO_BASE_NO)
            try:
                ok = panel_celular.columnas_ok()
            except (FileNotFoundError, sqlite3.DatabaseError):
                raise ErrorApi(503, "base_no_disponible", TEXTO_BASE_NO)
            if not ok:
                raise ErrorApi(503, "base_desactualizada", TEXTO_BASE_VIEJA)
            return True
        finally:
            db.cerrar_conexion()

    def autenticar(request: Request, _base: bool = Depends(base_lista)) -> dict:
        try:
            tipo, _, token = request.headers.get("authorization", "").partition(" ")
            if tipo.lower() != "bearer" or not token.strip():
                raise ErrorApi(401, "sesion_invalida", TEXTO_SESION)
            sesion = acceso_celular.validar_token(token.strip())
            if not sesion:
                raise ErrorApi(401, "sesion_invalida", TEXTO_SESION)
            request.scope["otter_sesion"] = sesion["sid"]
            return sesion
        finally:
            db.cerrar_conexion()

    def _cambio(request, sesion, accion, codigo, antes, despues, resultado="ok"):
        try:
            log_cambios.info(json.dumps({
                "fecha": datetime.now().isoformat(timespec="milliseconds"), "ip": _ip_cliente(request.scope),
                "sesion": sesion["sid"], "usuario": sesion["usuario"], "accion": accion, "codigo": codigo,
                "antes": antes, "despues": despues, "resultado": resultado}, ensure_ascii=False, default=str))
        except Exception:
            log.exception("No se pudo anotar el cambio %s", accion)

    # ------------------------------------------------------------------ #
    # Modelos de entrada
    # ------------------------------------------------------------------ #

    Monto = Annotated[float, Field(ge=-1e9, le=1e9, allow_inf_nan=False)]
    MontoPositivo = Annotated[float, Field(gt=0, le=1e9, allow_inf_nan=False)]
    MontoNoNegativo = Annotated[float, Field(ge=0, le=1e9, allow_inf_nan=False)]
    Codigo = Annotated[str, Field(min_length=1, max_length=64)]
    Cantidad = Annotated[int, Field(ge=1, le=100000)]
    Umbral = Annotated[int, Field(ge=0, le=100000)]
    TextoNumero = Union[Monto, Annotated[str, Field(max_length=32)], None]

    class LoginIn(BaseModel):
        pin: str = Field(min_length=1, max_length=32)

    class MovimientoIn(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        codigo: Codigo
        cantidad: Cantidad
        operacion: Literal["sumar", "restar"]
        motivo: Optional[Annotated[str, Field(max_length=200)]] = None

    class LectorIn(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        codigo: Codigo
        operacion: Literal["sumar", "restar"]

    class RecalcularIn(BaseModel):
        cambio: Literal["costo_sin_iva", "precio_costo", "margen", "precio_final"]
        costo_sin_iva: TextoNumero = None
        precio_costo: TextoNumero = None
        margen: TextoNumero = None
        precio_final: TextoNumero = None

    class EsperadoIn(BaseModel):
        costo_sin_iva: Monto
        precio_compra: Monto
        margen_ganancia: Monto
        precio_venta: Monto

    class GuardarPreciosIn(BaseModel):
        costo_sin_iva: Optional[MontoNoNegativo] = None
        precio_costo: Optional[MontoNoNegativo] = None
        margen: Optional[Annotated[float, Field(gt=-100, le=10000, allow_inf_nan=False)]] = None
        precio_final: MontoPositivo
        esperado: EsperadoIn

    class AjusteIn(BaseModel):
        codigos: List[Codigo] = Field(min_length=1, max_length=5000)
        porcentaje: Optional[Annotated[float, Field(ge=-90, le=500, allow_inf_nan=False)]] = None
        monto_fijo: Optional[Monto] = None
        redondear: bool = True

    class AplicarAjusteIn(AjusteIn):
        esperados: Dict[str, Monto]

    class TelegramIn(BaseModel):
        # Solo se puede prender y apagar el bot desde el celular: el token y
        # el chat no se tipean en un celular (regla 4), y una sesión robada
        # no puede desviar las alertas a otro chat.
        model_config = ConfigDict(extra="forbid")
        habilitado: bool

    class UmbralesIn(BaseModel):
        stock_minimo: Umbral
        stock_maximo: Umbral

    class ItemFacturaIn(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True)
        codigo: Codigo
        cantidad: Cantidad
        precio_compra: Optional[MontoPositivo] = None

    class AplicarFacturaIn(BaseModel):
        factura_nombre: str = Field(min_length=1, max_length=200)
        items: List[ItemFacturaIn] = Field(min_length=1, max_length=500)
        forzar: bool = False

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #

    bloqueo = BloqueoIntentos()
    app.state.bloqueo = bloqueo
    lock_login = threading.Lock()
    semaforo_analisis = threading.Semaphore(1)
    lock_factura = threading.Lock()
    lock_probar = threading.Lock()
    ultimo_probar = [float("-inf")]

    @app.get("/api/salud")
    async def ver_salud():
        # async y sin tocar la base ni el pool de hilos: el autochequeo mide
        # solo el bucle de eventos y la pila HTTP. Con los 8 hilos ocupados
        # contesta igual al instante (una salud "def" tardaba 5,5 s).
        return salud(estado)

    @app.post("/api/auth/login")
    @con_base
    def login(datos: LoginIn, request: Request, _base: bool = Depends(base_lista)):
        ip = _ip_cliente(request.scope)
        with lock_login:
            # El bloqueo se mira ANTES de calcular el PBKDF2: así no sirve
            # para tirar la CPU de la PC de la caja.
            restante = bloqueo.segundos_restantes(ip)
            if restante:
                raise ErrorApi(429, "demasiados_intentos",
                               f"Demasiados intentos. Probá de nuevo en {math.ceil(restante / 60)} min.",
                               reintentar_en_s=restante)
            estado_pin = acceso_celular.estado_pin()
            if estado_pin == "secreto_ilegible":
                raise ErrorApi(503, "secreto_ilegible", TEXTO_SECRETO)
            if estado_pin == "base_no_disponible":
                raise ErrorApi(503, "base_no_disponible", TEXTO_BASE_NO)
            if estado_pin != "definido":
                raise ErrorApi(503, "pin_no_definido", TEXTOS_PIN.get(estado_pin, TEXTOS_PIN["no_definido"]))
            usuario = acceso_celular.verificar_pin(datos.pin)
            if usuario is None:
                r = bloqueo.registrar_fallo(ip)
                log.warning("LOGIN FALLIDO ip=%s (%d/%d)", ip, r["fallos"], MAX_INTENTOS_PIN)
                if r["bloqueo_ip"]:
                    log.warning("BLOQUEO ip=%s 5 min", ip)
                if r["bloqueo_global"]:
                    log.warning("BLOQUEO GLOBAL: todos los logins frenados 5 min")
                raise ErrorApi(401, "pin_incorrecto", "PIN incorrecto.")
            bloqueo.registrar_exito(ip)
            emitido = acceso_celular.emitir_token(usuario)
        request.scope["otter_sesion"] = emitido["sid"]
        log.info("LOGIN OK ip=%s sesion=%s", ip, emitido["sid"])
        return {"token": emitido["token"], "usuario": usuario, "expira": emitido["expira"]}

    @app.get("/api/auth/yo")
    def yo(sesion: dict = Depends(autenticar)):
        return {"usuario": sesion["usuario"]}

    @app.get("/api/dashboard")
    @con_base
    def dashboard(periodo_top: str = Query("historico", max_length=20), sesion: dict = Depends(autenticar)):
        with _como_dato_invalido():
            return panel_celular.resumen_dashboard(periodo_top)

    @app.get("/api/productos")
    @con_base
    def productos(q: str = Query("", max_length=200), campo: Optional[str] = Query(None, max_length=20),
                  valor: str = Query("", max_length=200), limite: int = Query(200, ge=1, le=2000),
                  sesion: dict = Depends(autenticar)):
        with _como_dato_invalido():
            return panel_celular.buscar_productos(q, campo=campo or None, valor=valor, limite=limite)

    @app.get("/api/productos/{codigo:path}")
    @con_base
    def producto(codigo: str, sesion: dict = Depends(autenticar)):
        codigo = codigo.strip()
        p = panel_celular.obtener_producto(codigo)
        if p is None:
            raise _no_existe(codigo)
        return dict(p, movimientos=panel_celular.movimientos(codigo, 15))

    @app.get("/api/movimientos")
    @con_base
    def ver_movimientos(limite: int = Query(30, ge=1, le=500), codigo: Optional[str] = Query(None, max_length=64),
                        sesion: dict = Depends(autenticar)):
        return panel_celular.movimientos((codigo or "").strip() or None, limite)

    def _resultado_stock(codigo: str, stock_nuevo: int) -> dict:
        p = panel_celular.obtener_producto(codigo) or {}
        return {"codigo": codigo, "nombre": p.get("nombre", ""), "stock_nuevo": stock_nuevo,
                "stock_minimo": p.get("stock_minimo", 0), "stock_maximo": p.get("stock_maximo", 0),
                # Lo que el StockService todavía va a descontar de este producto:
                # con el catálogo en 0, cargar 12 puede quedar en 6 a los 5 s.
                "ventas_pendientes": panel_celular.ventas_pendientes_de_descontar(codigo)}

    @app.post("/api/stock/movimiento")
    @con_base
    def stock_movimiento(datos: MovimientoIn, request: Request, sesion: dict = Depends(autenticar)):
        quien = acceso_celular.autor(sesion["usuario"])
        motivo = (datos.motivo or "").strip()
        if datos.operacion == "sumar":
            nuevo = stock_service.sumar_stock_manual(datos.codigo, datos.cantidad, usuario=quien,
                                                     motivo=motivo or "Alta manual", origen=ORIGEN)
            antes = nuevo - datos.cantidad
        else:
            nuevo = stock_service.restar_stock_manual(datos.codigo, datos.cantidad, usuario=quien,
                                                      motivo=motivo or "Baja manual", origen=ORIGEN)
            antes = nuevo + datos.cantidad
        _cambio(request, sesion, "stock_movimiento", datos.codigo, antes, nuevo)
        return _resultado_stock(datos.codigo, nuevo)

    @app.post("/api/stock/lector")
    @con_base
    def stock_lector(datos: LectorIn, request: Request, sesion: dict = Depends(autenticar)):
        quien = acceso_celular.autor(sesion["usuario"])
        if datos.operacion == "sumar":
            nuevo = stock_service.sumar_stock_manual(datos.codigo, 1, usuario=quien,
                                                     motivo="Lector de código de barras", origen=ORIGEN)
            antes = nuevo - 1
        else:
            nuevo = stock_service.restar_stock_por_lector(datos.codigo, usuario=quien, origen=ORIGEN)
            antes = nuevo + 1
        _cambio(request, sesion, "stock_lector", datos.codigo, antes, nuevo)
        return _resultado_stock(datos.codigo, nuevo)

    @app.get("/api/precios/{codigo:path}")
    @con_base
    def ver_precios(codigo: str, sesion: dict = Depends(autenticar)):
        p = panel_celular.precios_para_pantalla(codigo.strip())
        if p is None:
            raise _no_existe(codigo.strip())
        return p

    @app.post("/api/precios/recalcular")
    def recalcular(datos: RecalcularIn, sesion: dict = Depends(autenticar)):
        # El cálculo lo hace siempre el servidor, con la misma función que el
        # Panel ("1.500" se lee a la argentina). No escribe nada.
        r = precios.recalcular(costo_sin_iva=datos.costo_sin_iva, precio_costo=datos.precio_costo,
                               margen=datos.margen, precio_final=datos.precio_final, cambio=datos.cambio)
        return {clave: _finito_o_none(r.get(clave)) for clave in ("costo_sin_iva", "precio_costo", "margen",
                                                                   "precio_final")}

    @app.put("/api/precios/{codigo:path}")
    @con_base
    def guardar_precios(codigo: str, datos: GuardarPreciosIn, request: Request,
                        sesion: dict = Depends(autenticar)):
        antes = panel_celular.precios_para_pantalla(codigo.strip())
        if antes is None:
            raise _no_existe(codigo.strip())
        with _como_dato_invalido():
            # Se guardan EXACTAMENTE los valores que vio el dueño (null = no
            # tocar, como el Panel), y solo si los 4 guardados siguen siendo
            # los que vio: una factura aplicada mientras tanto da 409.
            precios.actualizar_precios(
                codigo=antes["codigo"], costo_sin_iva=datos.costo_sin_iva, precio_costo=datos.precio_costo,
                margen=datos.margen, precio_final=datos.precio_final,
                usuario=acceso_celular.autor(sesion["usuario"]), origen=ORIGEN,
                esperado=datos.esperado.model_dump())
        despues = panel_celular.precios_para_pantalla(antes["codigo"])
        _cambio(request, sesion, "precio_guardar", antes["codigo"], antes["crudos"], despues["crudos"])
        return {"antes": antes, "despues": despues}

    def _uno_solo(datos) -> None:
        if (datos.porcentaje is None) == (datos.monto_fijo is None):
            raise ErrorApi(422, "dato_invalido",
                           "Dato inválido en «porcentaje»: poné el porcentaje o el monto fijo (uno solo)",
                           campo="porcentaje")

    @app.post("/api/precios/previsualizar")
    @con_base
    def previsualizar(datos: AjusteIn, sesion: dict = Depends(autenticar)):
        _uno_solo(datos)
        with _como_dato_invalido():
            return panel_celular.previsualizar_ajuste(_sin_repetir(datos.codigos), porcentaje=datos.porcentaje,
                                                      monto_fijo=datos.monto_fijo, redondear=datos.redondear)

    @app.post("/api/precios/aplicar")
    @con_base
    def aplicar_precios(datos: AplicarAjusteIn, request: Request, sesion: dict = Depends(autenticar)):
        _uno_solo(datos)
        codigos = _sin_repetir(datos.codigos)
        if set(datos.esperados) != set(codigos):
            raise ErrorApi(422, "dato_invalido", "Dato inválido en «esperados»: tienen que ser los mismos códigos "
                                                 "de la vista previa", campo="esperados")
        with _como_dato_invalido():
            vista = panel_celular.previsualizar_ajuste(codigos, porcentaje=datos.porcentaje,
                                                       monto_fijo=datos.monto_fijo, redondear=datos.redondear)
        en_cero = sum(1 for v in vista if v.get("ok") and v["queda_en_cero"])
        if en_cero:
            raise ErrorApi(400, "queda_en_cero", f"{en_cero} producto(s) quedarían en $ 0. Sacalos de la lista "
                                                 f"o cambiá el ajuste.")
        with _como_dato_invalido():
            resultado = bulk_edit.aplicar_ajuste_masivo(
                codigos, porcentaje=datos.porcentaje, monto_fijo=datos.monto_fijo, redondear=datos.redondear,
                usuario=acceso_celular.autor(sesion["usuario"]), origen=ORIGEN, esperados=datos.esperados)
        aplicados = sum(1 for r in resultado if r.get("ok"))
        _cambio(request, sesion, "precios_ajuste", None,
                {"porcentaje": datos.porcentaje, "monto_fijo": datos.monto_fijo, "redondear": datos.redondear,
                 "codigos": len(codigos)}, {"ok": aplicados, "error": len(resultado) - aplicados})
        return resultado

    @app.get("/api/alertas")
    @con_base
    def alertas(limite: int = Query(1000, ge=1, le=5000), sesion: dict = Depends(autenticar)):
        return panel_celular.listar_alertas(limite)

    def _config_alertas() -> dict:
        return {"telegram": panel_celular.config_telegram_visible(), **panel_celular.resumen_umbrales()}

    @app.get("/api/config/alertas")
    @con_base
    def config_alertas(sesion: dict = Depends(autenticar)):
        return _config_alertas()

    @app.put("/api/config/telegram")
    @con_base
    def guardar_telegram(datos: TelegramIn, request: Request, sesion: dict = Depends(autenticar)):
        # Es el ÚNICO lugar donde la API escribe config.ini. Se lee estricto:
        # con el archivo ilegible no se toca nada (guardar encima pisaría el
        # config real con valores por defecto).
        previo = config.cargar_config(estricto=True)
        antes = previo.get("telegram", "habilitado", fallback="false").lower() == "true"
        config.actualizar_config_dict({"telegram": {"habilitado": "true" if datos.habilitado else "false"}})
        real = config.cargar_config(estricto=True).get("telegram", "habilitado", fallback="false").lower() == "true"
        _cambio(request, sesion, "telegram_habilitado", None, antes, real,
                "ok" if real == datos.habilitado else "config_cambio")
        if real != datos.habilitado:
            raise ErrorApi(409, "config_cambio", "Otro programa guardó la configuración al mismo tiempo. "
                                                 "Probá de nuevo.")
        return _config_alertas()

    @app.post("/api/config/telegram/probar")
    def probar_telegram(sesion: dict = Depends(autenticar)):
        with lock_probar:
            if time.monotonic() - ultimo_probar[0] < 30:
                raise ErrorApi(429, "ocupado", "Esperá unos segundos antes de probar de nuevo.")
            ultimo_probar[0] = time.monotonic()
        try:
            from pos_core import telegram_bot
            r = telegram_bot.probar_envio()
            ok, detalle = bool(r.get("ok")), str(r.get("detalle", ""))
        except Exception as e:
            # Solo el nombre del error: el mensaje puede traer la URL de
            # Telegram, que lleva el token del bot (regla 4).
            log.warning("Probar Telegram falló: %s", type(e).__name__)
            ok, detalle = False, "No se pudo probar el envío: quedó anotado en el log de la PC."
        return {"ok": ok, "enviado": ok, "detalle": detalle}

    @app.put("/api/config/umbrales")
    @con_base
    def guardar_umbrales(datos: UmbralesIn, request: Request, sesion: dict = Depends(autenticar)):
        antes = panel_celular.resumen_umbrales()["umbral_global"]
        with _como_dato_invalido():
            alerts.set_umbral_global(datos.stock_minimo, datos.stock_maximo)
        resumen = panel_celular.resumen_umbrales()
        _cambio(request, sesion, "umbral_global_guardar", None, antes, resumen["umbral_global"])
        return resumen

    @app.delete("/api/config/umbral-global")
    @con_base
    def quitar_umbral_global(request: Request, sesion: dict = Depends(autenticar)):
        # Borra SOLO el global: los umbrales por producto no se tocan. "Quitar
        # TODOS los propios" no está en el celular a propósito (es destructivo
        # y ya se confundieron los dos botones): queda en el Panel.
        antes = panel_celular.resumen_umbrales()["umbral_global"]
        borradas = alerts.quitar_umbral_global()
        resumen = panel_celular.resumen_umbrales()
        _cambio(request, sesion, "umbral_global_quitar", None, antes, resumen["umbral_global"])
        return {"borradas": borradas, **resumen}

    def _analizar_en_hilo(ruta: str, nombre: str) -> dict:
        try:
            return panel_celular.analizar_factura(ruta, nombre)
        finally:
            db.cerrar_conexion()

    @app.post(RUTA_ANALIZAR)
    async def analizar_factura(request: Request, sesion: dict = Depends(autenticar)):
        # Un análisis por vez: leer un PDF y emparejar por nombre es lo más
        # pesado que hace la API, y la caja corre en la misma PC.
        if not semaforo_analisis.acquire(blocking=False):
            raise ErrorApi(429, "ocupado", "Ya se está leyendo otra factura: probá en un minuto.")
        ruta = None
        try:
            # El formulario se lee recién acá: DESPUÉS de validar la sesión.
            formulario = await request.form(max_files=1, max_fields=10)
            try:
                archivo = formulario.get("archivo")
                if archivo is None or not hasattr(archivo, "read"):
                    raise ErrorApi(422, "dato_invalido", "Dato inválido en «archivo»: falta", campo="archivo")
                contenido = await archivo.read()
                nombre = os.path.basename((archivo.filename or "").replace("\\", "/")).strip()[:200] or "factura.pdf"
            finally:
                await formulario.close()
            # Los lectores de PDF aceptan basura antes del encabezado: se busca
            # en los primeros 1024 bytes, no solo al principio.
            if b"%PDF-" not in contenido[:1024]:
                raise ErrorApi(415, "no_es_pdf", "El archivo no es un PDF.")
            fd, ruta = tempfile.mkstemp(prefix="otter_celular_", suffix=".pdf")
            with os.fdopen(fd, "wb") as f:
                f.write(contenido)
            return await run_in_threadpool(_analizar_en_hilo, ruta, nombre)
        finally:
            semaforo_analisis.release()
            if ruta:
                try:
                    os.remove(ruta)
                except OSError:
                    pass

    @app.post("/api/facturas/aplicar")
    @con_base
    def aplicar_factura(datos: AplicarFacturaIn, request: Request, sesion: dict = Depends(autenticar)):
        # De a una: chequear "ya se cargó" y aplicar no se pueden cruzar con
        # otro pedido de la propia API.
        if not lock_factura.acquire(timeout=30):
            raise ErrorApi(429, "ocupado", "Ya se está cargando otra factura: probá en un minuto.")
        try:
            items = [{"codigo": it.codigo, "cantidad": it.cantidad, "precio_compra": it.precio_compra}
                     for it in datos.items]
            nombre = datos.factura_nombre.strip() or "factura.pdf"
            if not datos.forzar:
                # Aplicarla dos veces suma el stock dos veces, y tras un
                # "incierto" con datos móviles es lo primero que uno hace.
                repetida = panel_celular.factura_ya_aplicada(nombre, items)
                if repetida:
                    raise ErrorApi(409, "factura_ya_aplicada",
                                   f"Esta factura ya se cargó hace {repetida['hace_min']} min "
                                   f"({repetida['renglones_iguales']} renglones iguales). Si es otra factura "
                                   f"con el mismo nombre, tocá «Cargar igual».",
                                   hace_min=repetida["hace_min"], renglones_iguales=repetida["renglones_iguales"])
            resultado = stock_service.sumar_stock_por_factura_pdf(
                items, usuario=acceso_celular.autor(sesion["usuario"]), factura_nombre=nombre, origen=ORIGEN)
        finally:
            lock_factura.release()
        aplicados = sum(1 for r in resultado if r.get("ok"))
        _cambio(request, sesion, "factura_aplicar", None, {"nombre": nombre, "renglones": len(items),
                                                           "forzar": datos.forzar},
                {"ok": aplicados, "error": len(resultado) - aplicados})
        return resultado

    return app


# --------------------------------------------------------------------- #
# Servidor (uvicorn en un hilo) y supervisión
# --------------------------------------------------------------------- #

def socket_de_escucha(puerto: int, host: str = "0.0.0.0") -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows: nadie más puede compartir el puerto.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif os.name != "nt":
            # Linux (pruebas): reusar un puerto con conexiones en TIME_WAIT;
            # igual falla si alguien ESCUCHA ahí. En Windows NUNCA
            # SO_REUSEADDR: ahí deja robar el puerto.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, puerto))
        s.listen(64)
    except BaseException:
        s.close()
        raise
    return s


class Servidor:
    """uvicorn corriendo en un hilo daemon llamado ApiCelular."""

    def __init__(self, app, sock: socket.socket, puerto: int):
        import uvicorn
        configuracion = uvicorn.Config(
            app, log_config=None, access_log=False, http="h11", loop="asyncio", ws="none", lifespan="on",
            # proxy_headers=False es OBLIGATORIO: con el valor por defecto
            # uvicorn le cree a X-Forwarded-For si viene de 127.0.0.1, y un
            # proxy local haría pasar cualquier cosa por una IP de Tailscale.
            proxy_headers=False, server_header=False, timeout_keep_alive=5, limit_concurrency=32,
            timeout_graceful_shutdown=5)
        self.puerto = puerto
        self.sock = sock
        self.server = uvicorn.Server(configuracion)
        self.hilo = threading.Thread(target=self._correr, name="ApiCelular", daemon=True)
        self.hilo.start()

    def _correr(self):
        try:
            self.server.run(sockets=[self.sock])
        except BaseException:
            log.exception("El servidor HTTP terminó con un error")
        finally:
            try:
                self.sock.close()
            except OSError:
                pass

    @property
    def iniciado(self) -> bool:
        return bool(getattr(self.server, "started", False))

    @property
    def vivo(self) -> bool:
        return self.hilo.is_alive()

    def esperar_inicio(self, timeout: float = 15) -> bool:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if self.iniciado:
                return True
            if not self.vivo:
                return False
            time.sleep(0.05)
        return self.iniciado

    def detener(self, espera_s: float = 8) -> None:
        self.server.should_exit = True
        self.hilo.join(espera_s)
        try:
            self.sock.close()
        except OSError:
            pass


def iniciar_servidor(puerto: int, *, host: str = "0.0.0.0", app=None) -> Servidor:
    """Abre el socket (OSError si el puerto está tomado) y arranca uvicorn.
    Lo usan igual el servicio, la consola, la autoprueba y las pruebas: lo
    que se prueba es lo que corre."""
    sock = socket_de_escucha(puerto, host)
    try:
        return Servidor(app if app is not None else crear_app(), sock, puerto)
    except BaseException:
        sock.close()
        raise


def _quien_escucha(puerto: int) -> str:
    try:
        from pos_core import servicio_windows
        return servicio_windows.quien_escucha(puerto) or ""
    except Exception:
        return ""


def _pedir_salud(puerto: int, timeout: float = 10):
    abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with abridor.open(f"http://127.0.0.1:{puerto}/api/salud", timeout=timeout) as r:
        return json.loads(r.read(65536).decode("utf-8"))


class Supervisor:
    """Mantiene la API escuchando sin morirse nunca (D5).

    - Puerto tomado: reintenta cada 60 s, sin matar el proceso (un servicio
      que muere por eso entra en ciclo de reinicios y termina Stopped, que
      confunde el diagnóstico).
    - Hilo de uvicorn caído: lo relanza con espera creciente.
    - Config: relee [api_celular] cada 30 s (apagarla no exige reiniciar).
    - Autochequeo (solo en modo servicio): si 3 veces seguidas no contesta
      su propia /api/salud, sale con os._exit(3) y actúa el SCM. Cubre el
      "proceso vivo pero colgado" que ningún reintento de Windows ve.
    """

    ESPERAS_RELANZAR = (5, 10, 20, 40, 60)

    def __init__(self, *, autochequeo: bool, estado: EstadoApi, intervalo_config_s=30, reintento_bind_s=60,
                 intervalo_estado_s=15, intervalo_autochequeo_s=60, fallas_para_salir=3,
                 reloj=time.monotonic, host: str = "0.0.0.0", permitir_loopback_total: bool = False):
        self.autochequeo = autochequeo
        self.estado = estado
        self.intervalo_config_s = intervalo_config_s
        self.reintento_bind_s = reintento_bind_s
        self.intervalo_estado_s = intervalo_estado_s
        self.intervalo_autochequeo_s = intervalo_autochequeo_s
        self.fallas_para_salir = fallas_para_salir
        self.host = host
        self.permitir_loopback_total = permitir_loopback_total
        self._reloj = reloj
        self._salir = os._exit
        self.servidor = None
        self._cfg = None
        self._prox_estado = self._prox_config = self._prox_bind = self._prox_chequeo = float("-inf")
        self._prox_relanzar = float("-inf")
        self._intentos_relanzar = 0
        self._arranco_en = None
        self._fallas_chequeo = 0
        self._detenido = False
        self._lock = threading.Lock()

    @property
    def puerto(self):
        return self._cfg["puerto"] if self._cfg else None

    def _parar(self) -> None:
        if self.servidor is not None:
            try:
                self.servidor.detener()
            except Exception:
                log.exception("No se pudo parar el servidor")
            self.servidor = None
            self._arranco_en = None

    def _arrancar(self, ahora: float) -> None:
        puerto = self._cfg["puerto"]
        try:
            app = crear_app(estado=self.estado, permitir_loopback_total=self.permitir_loopback_total)
            self.servidor = iniciar_servidor(puerto, host=self.host, app=app)
            self._arranco_en = ahora
            self._fallas_chequeo = 0
            self._prox_chequeo = ahora + self.intervalo_autochequeo_s
            log.info("API del celular escuchando en el puerto %s (base: %s)", puerto, panel_celular.ruta_base_datos())
        except OSError as e:
            self._prox_bind = ahora + self.reintento_bind_s
            quien = _quien_escucha(puerto)
            _anotar_cada_hora("bind", f"No se pudo escuchar en el {puerto} ({type(e).__name__}: {e})"
                                      + (f"; lo tiene {quien}" if quien else "") + ": se reintenta cada minuto")

    def paso(self) -> None:
        """Una vuelta de supervisión. NUNCA lanza."""
        with self._lock:
            if self._detenido:
                return   # SvcStop ya pidió parar: no se vuelve a levantar nada
            try:
                self._paso()
            except Exception:
                log.exception("Error en la supervisión de la API del celular")

    def _paso(self) -> None:
        ahora = self._reloj()
        if ahora >= self._prox_estado:
            self._prox_estado = ahora + self.intervalo_estado_s
            self.estado.refrescar()

        if ahora >= self._prox_config:
            self._prox_config = ahora + self.intervalo_config_s
            leido = config.leer_config_celular()
            if leido["leido"]:
                self._cfg = leido
            elif self._cfg is None:
                _anotar_cada_hora("config", "No se pudo leer config.ini: la API del celular no escucha hasta "
                                            "poder leerlo")
            else:
                _anotar_cada_hora("config", "No se pudo leer config.ini: se sigue con lo último leído")

        cfg = self._cfg
        if cfg is None or not cfg["habilitado"]:
            if self.servidor is not None:
                log.info("[api_celular] habilitado no es true: la API deja de escuchar")
            self._parar()
            if cfg is not None:
                _anotar_cada_hora("apagada", "La API del celular está apagada a propósito "
                                             "([api_celular] habilitado = false)", logging.INFO)
            return
        if cfg["puerto"] == cfg["puerto_remoto"]:
            self._parar()
            _anotar_cada_hora("puerto_remoto", f"[api_celular] puerto = [remoto] puerto ({cfg['puerto']}): no se "
                                               f"escucha, el puerto es del Dueño Remoto")
            return
        if self.servidor is not None and self.servidor.puerto != cfg["puerto"]:
            log.info("Cambió el puerto de la API del celular: %s -> %s", self.servidor.puerto, cfg["puerto"])
            self._parar()

        if self.servidor is not None and not self.servidor.vivo:
            espera = self.ESPERAS_RELANZAR[min(self._intentos_relanzar, len(self.ESPERAS_RELANZAR) - 1)]
            _anotar_cada_hora("hilo", f"El hilo del servidor HTTP se cayó: se relanza en {espera} s")
            self.servidor = None
            self._arranco_en = None
            self._prox_relanzar = ahora + espera
            self._intentos_relanzar += 1

        if self.servidor is None:
            if ahora >= self._prox_bind and ahora >= self._prox_relanzar:
                self._arrancar(ahora)
            return

        # Un servidor que anduvo bien un rato vuelve a empezar la escalera de esperas.
        if self._arranco_en is not None and ahora - self._arranco_en >= 120 and self.servidor.iniciado:
            self._intentos_relanzar = 0

        if self.autochequeo and self.servidor.iniciado and ahora >= self._prox_chequeo:
            self._prox_chequeo = ahora + self.intervalo_autochequeo_s
            try:
                bien = _pedir_salud(self.servidor.puerto).get("servicio") == FIRMA
            except Exception:
                bien = False
            if bien:
                self._fallas_chequeo = 0
            else:
                self._fallas_chequeo += 1
                _anotar_cada_hora("autochequeo", f"El autochequeo de /api/salud falló "
                                                 f"({self._fallas_chequeo}/{self.fallas_para_salir})")
                if self._fallas_chequeo >= self.fallas_para_salir:
                    log.error("La API no contesta su propia /api/salud %s veces seguidas: el proceso sale "
                              "para que Windows lo reinicie", self._fallas_chequeo)
                    for h in logging.getLogger().handlers:
                        try:
                            h.flush()
                        except Exception:
                            pass
                    self._salir(3)

    def detener(self, espera_s: float = 8) -> None:
        with self._lock:
            self._detenido = True
            if self.servidor is not None:
                try:
                    self.servidor.detener(espera_s)
                except Exception:
                    log.exception("No se pudo parar el servidor")
                self.servidor = None
