"""API del Dueño: expone el Panel del Dueño a la app del celular.

Corre en la PC del local (fuente de verdad) y se alcanza desde el celular
del dueño por Tailscale. Es una capa fina HTTP/JSON sobre `pos_core`: no
reimplementa ninguna regla de stock, precios ni alertas, así que el stock
se sigue escribiendo SOLO por `stock_service` (versionado optimista) y
toda escritura sigue pasando por `db.transaction()`.

Uso (desarrollo):
    python services/api_dueno.py --base apps/master_dueno
Uso (producción):
    ApiDueno.exe --base "C:\\SistemaDual\\MaestroDueno"

`--base` debe apuntar a la carpeta que contiene el `database\\stock.db` y
el `config.ini` que usa MaestroDueno, para que la app y la PC vean
exactamente los mismos datos. Si esa base no existe o no tiene ningún
usuario DUEÑO activo, la API no arranca (código de salida 2) y deja el
motivo en `logs\\api_dueno.log`.

Definir o cambiar el PIN del dueño (no levanta el servidor):
    ApiDueno.exe --base "C:\\SistemaDual\\MaestroDueno" --definir-pin 4321
"""

import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from datetime import datetime
from typing import List, Literal, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator
from starlette.datastructures import UploadFile as ArchivoSubido

import pos_core
from pos_core import alertas, bulk_edit, config, filters, panel_dueno, pdf_import, stock_service, usuarios
from pos_core.config import ConfigIlegibleError
from pos_core.db import init_db
from pos_core.paths import config_path, get_base_path
from pos_core.stock_service import ProductoNoEncontradoError, StockInsuficienteError

API_VERSION = "1.0.0"
ORIGEN = "MAESTRO"   # la API escribe directo en la base del Maestro
ROL_DUENO = "DUEÑO"
PUERTO_DEFAULT = 8765
DURACION_TOKEN_SEGUNDOS = 30 * 24 * 3600
MAX_INTENTOS_PIN = 5
BLOQUEO_SEGUNDOS = 5 * 60
# Tope global: con muchas IPs (o una IP que cambia) el bloqueo por IP no
# alcanza, así que más de MAX_FALLOS_GLOBALES PIN fallidos en la ventana,
# sumando todas las IPs, bloquean TODOS los logins BLOQUEO_SEGUNDOS.
MAX_FALLOS_GLOBALES = 20
VENTANA_FALLOS_GLOBALES = 5 * 60
MAX_PDF_BYTES = 20 * 1024 * 1024
# Tamaño máximo del cuerpo de un pedido: el PDF + margen para el multipart
# en /api/facturas/analizar, y 1 MB para todo lo demás (JSON chicos).
RUTA_ANALIZAR_FACTURA = "/api/facturas/analizar"
MAX_CUERPO_FACTURA_BYTES = MAX_PDF_BYTES + 1024 * 1024
MAX_CUERPO_BYTES = 1024 * 1024
MAX_PRECIO = 1e12
CAMPOS_FILTRO = ["marca", "proveedor", "categoria", "nombre", "codigo"]
EXIT_BASE_INVALIDA = 2


# --------------------------------------------------------------------- #
# Tokens de sesión (firmados con HMAC, sin tabla nueva en la DB)
# --------------------------------------------------------------------- #

_lock_secreto = threading.Lock()
# Ruta de config.ini -> secreto. Se lee UNA vez por config.ini (no en cada
# pedido): releerlo mientras otro proceso lo escribe podía devolverlo
# incompleto y terminar regenerando la clave.
_secretos_cache = {}


def _secreto() -> bytes:
    """Clave de firma guardada en config.ini [api] secreto. Se genera sola
    la primera vez, y SOLO si config.ini se pudo leer bien y no la tiene:
    un config.ini ilegible da ConfigIlegibleError (500), nunca se lo pisa.
    Borrarla (y reiniciar la API) invalida todas las sesiones de celulares."""
    ruta = config_path()
    with _lock_secreto:
        secreto = _secretos_cache.get(ruta)
        if secreto is None:
            cfg = config.cargar_config(estricto=True)
            valor = cfg.get("api", "secreto", fallback="").strip()
            if not valor:
                valor = secrets.token_hex(32)
                if not cfg.has_section("api"):
                    cfg.add_section("api")
                cfg.set("api", "secreto", valor)
                config.guardar_config(cfg)
            secreto = _secretos_cache[ruta] = valor.encode("utf-8")
        return secreto


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _huella_pin(pin_hash: str) -> str:
    """Huella del PIN que viaja en el token: si al dueño le cambian el PIN,
    los tokens viejos dejan de coincidir. Con HMAC del secreto, el token no
    expone nada del hash."""
    return hmac.new(_secreto(), pin_hash.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def emitir_token(usuario: str, *, ahora: float = None) -> dict:
    pin_hash = usuarios.obtener_pin_hash(usuario, rol=ROL_DUENO)
    if pin_hash is None:
        raise ValueError(f"'{usuario}' no es un usuario DUEÑO activo")
    exp = int((ahora or time.time()) + DURACION_TOKEN_SEGUNDOS)
    datos = {"u": usuario, "exp": exp, "h": _huella_pin(pin_hash)}
    payload = _b64(json.dumps(datos, separators=(",", ":")).encode("utf-8"))
    firma = _b64(hmac.new(_secreto(), payload.encode("ascii"), hashlib.sha256).digest())
    return {"token": f"{payload}.{firma}", "expira": exp}


def validar_token(token: str, *, ahora: float = None) -> Optional[str]:
    """Usuario del token, o None si la firma no da, venció, o el usuario ya
    no es un DUEÑO activo con el mismo PIN que cuando se emitió."""
    try:
        payload, firma = token.split(".", 1)
        esperada = _b64(hmac.new(_secreto(), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(firma, esperada):
            return None
        datos = json.loads(_unb64(payload))
        if datos["exp"] < (ahora or time.time()):
            return None
        usuario, huella = datos["u"], datos["h"]
        if not isinstance(usuario, str) or not isinstance(huella, str):
            return None
        pin_hash = usuarios.obtener_pin_hash(usuario, rol=ROL_DUENO)
        if pin_hash is None or not hmac.compare_digest(huella, _huella_pin(pin_hash)):
            return None
        return usuario
    except (ValueError, KeyError, TypeError):
        return None


class _BloqueoIntentos:
    """Frena la fuerza bruta del PIN (un PIN de 4 dígitos son solo 10.000
    combinaciones): tras MAX_INTENTOS_PIN fallos seguidos desde una misma
    IP, esa IP queda bloqueada BLOQUEO_SEGUNDOS; y si en
    VENTANA_FALLOS_GLOBALES hay más de MAX_FALLOS_GLOBALES fallos sumando
    todas las IPs, se bloquean todos los logins BLOQUEO_SEGUNDOS."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fallos = {}
        self._bloqueado_hasta = {}
        self._fallos_globales = deque()   # momentos de cada fallo, de cualquier IP
        self._bloqueo_global_hasta = 0.0

    def segundos_restantes(self, ip: str) -> int:
        with self._lock:
            hasta = max(self._bloqueado_hasta.get(ip, 0), self._bloqueo_global_hasta)
            return max(0, int(hasta - time.time()))

    def registrar_fallo(self, ip: str) -> None:
        with self._lock:
            ahora = time.time()
            self._fallos[ip] = self._fallos.get(ip, 0) + 1
            if self._fallos[ip] >= MAX_INTENTOS_PIN:
                self._bloqueado_hasta[ip] = ahora + BLOQUEO_SEGUNDOS
                self._fallos[ip] = 0
            self._fallos_globales.append(ahora)
            while self._fallos_globales and self._fallos_globales[0] <= ahora - VENTANA_FALLOS_GLOBALES:
                self._fallos_globales.popleft()
            if len(self._fallos_globales) > MAX_FALLOS_GLOBALES:
                self._bloqueo_global_hasta = ahora + BLOQUEO_SEGUNDOS
                self._fallos_globales.clear()

    def registrar_exito(self, ip: str) -> None:
        with self._lock:
            self._fallos.pop(ip, None)
            self._bloqueado_hasta.pop(ip, None)


# --------------------------------------------------------------------- #
# Límite de tamaño de los pedidos
# --------------------------------------------------------------------- #

class _CuerpoDemasiadoGrande(HTTPException):
    def __init__(self, detalle: str):
        super().__init__(status_code=413, detail=detalle)


class LimiteCuerpoMiddleware:
    """Middleware ASGI puro: corta con 413 los cuerpos más grandes que el
    límite de la ruta, ANTES de que lleguen a disco o a memoria. Mira el
    Content-Length declarado y además cuenta los bytes que van llegando,
    así que un envío chunked (sin Content-Length) tampoco lo esquiva."""

    def __init__(self, app, *, limite: int, detalle: str, limites_por_ruta: dict):
        """`limites_por_ruta`: {ruta: (limite_en_bytes, detalle)}; el resto
        de las rutas usa `limite` y `detalle`."""
        self.app = app
        self.por_defecto = (limite, detalle)
        self.limites_por_ruta = limites_por_ruta

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limite, detalle = self.limites_por_ruta.get(scope.get("path", ""), self.por_defecto)

        for nombre, valor in scope.get("headers", []):
            if nombre.lower() == b"content-length":
                try:
                    declarado = int(valor)
                except ValueError:
                    continue   # uvicorn ya rechaza un Content-Length mal formado
                if declarado > limite:
                    await JSONResponse(status_code=413, content={"detail": detalle})(scope, receive, send)
                    return

        recibidos = 0
        respuesta_iniciada = False

        async def receive_con_limite():
            nonlocal recibidos
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibidos += len(mensaje.get("body", b""))
                if recibidos > limite:
                    # Es HTTPException: FastAPI la deja pasar al leer el
                    # cuerpo y su manejador responde 413.
                    raise _CuerpoDemasiadoGrande(detalle)
            return mensaje

        async def send_registrando(mensaje):
            nonlocal respuesta_iniciada
            if mensaje["type"] == "http.response.start":
                respuesta_iniciada = True
            await send(mensaje)

        try:
            await self.app(scope, receive_con_limite, send_registrando)
        except _CuerpoDemasiadoGrande:
            if respuesta_iniciada:
                raise
            await JSONResponse(status_code=413, content={"detail": detalle})(scope, receive, send)


# --------------------------------------------------------------------- #
# Modelos de entrada
# --------------------------------------------------------------------- #

class LoginIn(BaseModel):
    pin: str = Field(min_length=1, max_length=32)
    usuario: Optional[str] = None


class MovimientoIn(BaseModel):
    codigo: str = Field(min_length=1)
    cantidad: int = Field(gt=0, le=100000)
    operacion: Literal["sumar", "restar"]
    motivo: Optional[str] = Field(default=None, max_length=200)


class LectorIn(BaseModel):
    codigo: str = Field(min_length=1)
    operacion: Literal["sumar", "restar"] = "restar"


class AjustePreciosIn(BaseModel):
    codigos: List[str] = Field(min_length=1, max_length=10000)
    # Infinity/NaN (el JSON de Python los acepta) no son montos: 422.
    porcentaje: Optional[float] = Field(default=None, ge=-100, le=1000, allow_inf_nan=False)
    monto_fijo: Optional[float] = Field(default=None, ge=-MAX_PRECIO, le=MAX_PRECIO, allow_inf_nan=False)
    redondear: bool = True

    @model_validator(mode="after")
    def _uno_solo(self):
        if (self.porcentaje is None) == (self.monto_fijo is None):
            raise ValueError("Indicá porcentaje o monto_fijo (uno solo)")
        return self


class PrecioIn(BaseModel):
    precio_venta: float = Field(ge=0, le=MAX_PRECIO, allow_inf_nan=False)


class TelegramIn(BaseModel):
    habilitado: bool
    chat_id_default: str = Field(default="", max_length=64)
    bot_token: Optional[str] = Field(default=None, max_length=200)  # None = no cambiar


class UmbralesIn(BaseModel):
    stock_minimo: int = Field(ge=0)
    stock_maximo: int = Field(ge=0)


class ItemFacturaIn(BaseModel):
    codigo: str = Field(min_length=1)
    cantidad: int = Field(gt=0, le=100000)
    precio_compra: Optional[float] = Field(default=None, ge=0, le=MAX_PRECIO, allow_inf_nan=False)


class AplicarFacturaIn(BaseModel):
    factura_nombre: str = Field(min_length=1, max_length=200)
    items: List[ItemFacturaIn] = Field(min_length=1)


# --------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------- #

def _mascara_token(token: str) -> str:
    if not token:
        return ""
    return f"{token[:4]}••••{token[-4:]}" if len(token) > 8 else "••••"


def _json_seguro(valor):
    """Infinity/NaN no se pueden responder en JSON: en el detalle de un 422
    se devuelven como texto ("inf", "nan") en vez de romper con un 500."""
    if isinstance(valor, float) and not math.isfinite(valor):
        return str(valor)
    if isinstance(valor, dict):
        return {k: _json_seguro(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json_seguro(v) for v in valor]
    return valor


def create_app() -> FastAPI:
    app = FastAPI(title="API del Dueño", version=API_VERSION)
    app.add_middleware(
        LimiteCuerpoMiddleware,
        limite=MAX_CUERPO_BYTES, detalle="El pedido es demasiado grande",
        limites_por_ruta={RUTA_ANALIZAR_FACTURA: (MAX_CUERPO_FACTURA_BYTES, "El PDF supera los 20 MB")},
    )
    bloqueo = _BloqueoIntentos()
    # Serializa el login entero (chequeo de bloqueo + PIN + registro): con
    # pedidos simultáneos, todos pasaban el chequeo antes de que se
    # registrara el primer fallo.
    lock_login = threading.Lock()
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_req, exc):
        return JSONResponse(status_code=422, content={"detail": _json_seguro(jsonable_encoder(exc.errors()))})

    @app.exception_handler(ConfigIlegibleError)
    async def _config_ilegible(_req, exc):
        return JSONResponse(status_code=500, content={
            "detail": "No se pudo leer config.ini en la PC: revisalo (no se tocó ni se regeneró nada)."})

    @app.exception_handler(ProductoNoEncontradoError)
    async def _no_encontrado(_req, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(StockInsuficienteError)
    async def _sin_stock(_req, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _valor_invalido(_req, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def usuario_actual(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
        usuario = validar_token(cred.credentials) if cred else None
        if not usuario:
            raise HTTPException(status_code=401, detail="Sesión vencida o inválida. Ingresá el PIN de nuevo.")
        return usuario

    def autor(usuario: str) -> str:
        # Queda en Movimientos_Stock.usuario: en la auditoría se distingue
        # lo hecho desde el celular de lo hecho en la PC.
        return f"{usuario} (app)"

    # ---------------------------- sesión ---------------------------- #

    @app.get("/api/salud")
    def salud():
        cfg = config.cargar_config()
        return {
            "ok": True,
            "nombre_local": cfg.get("general", "nombre_local", fallback="Mi Negocio"),
            "version_api": API_VERSION,
            "version_core": pos_core.__version__,
            "hora_servidor": datetime.now().isoformat(timespec="seconds"),
        }

    @app.post("/api/auth/login")
    def login(datos: LoginIn, request: Request):
        # La IP es la de la conexión real: uvicorn corre con
        # proxy_headers=False, así que un X-Forwarded-For no la cambia.
        ip = request.client.host if request.client else "?"
        with lock_login:
            restantes = bloqueo.segundos_restantes(ip)
            if restantes:
                raise HTTPException(status_code=429,
                                    detail=f"Demasiados intentos. Probá de nuevo en {restantes // 60 + 1} min.")
            nombre = usuarios.verificar_pin(datos.pin, rol=ROL_DUENO, nombre=datos.usuario)
            if not nombre:
                bloqueo.registrar_fallo(ip)
                raise HTTPException(status_code=401, detail="PIN incorrecto")
            bloqueo.registrar_exito(ip)
            return {**emitir_token(nombre), "usuario": nombre}

    @app.get("/api/auth/yo")
    def yo(usuario: str = Depends(usuario_actual)):
        return {"usuario": usuario}

    # --------------------------- dashboard --------------------------- #

    @app.get("/api/dashboard")
    def dashboard(periodo_top: str = "historico", _u: str = Depends(usuario_actual)):
        return panel_dueno.resumen_dashboard(periodo_top=periodo_top)

    # --------------------------- productos --------------------------- #

    @app.get("/api/productos")
    def productos(q: str = "", campo: Optional[str] = None, valor: str = "", limite: int = 200,
                  _u: str = Depends(usuario_actual)):
        limite = max(1, min(limite, 2000))
        if campo:
            if campo not in CAMPOS_FILTRO:
                raise ValueError(f"Campo de filtro no permitido: {campo}")
            # mismo filtro que la pestaña "Filtros / Edición Masiva" de la PC
            encontrados = filters.aplicar_filtro({"campo": campo, "operador": "LIKE", "valor": valor})
            encontrados.sort(key=lambda p: p["nombre"])
            return panel_dueno.completar_productos([p["codigo"] for p in encontrados[:limite]])
        return panel_dueno.buscar_productos(q, limite=limite)

    @app.get("/api/productos/{codigo}")
    def producto(codigo: str, _u: str = Depends(usuario_actual)):
        p = panel_dueno.obtener_producto(codigo)
        if p is None:
            raise ProductoNoEncontradoError(f"Producto con código '{codigo}' no existe o está inactivo")
        return {**p, "movimientos": panel_dueno.movimientos_recientes(codigo=codigo, limite=15)}

    @app.put("/api/productos/{codigo}/precio")
    def fijar_precio(codigo: str, datos: PrecioIn, usuario: str = Depends(usuario_actual)):
        return bulk_edit.fijar_precio(codigo, datos.precio_venta, usuario=autor(usuario), origen=ORIGEN)

    # ----------------------------- stock ----------------------------- #

    def _respuesta_stock(codigo: str, nuevo_stock: int) -> dict:
        p = panel_dueno.obtener_producto(codigo) or {}
        return {"codigo": codigo, "nombre": p.get("nombre", ""), "stock_nuevo": nuevo_stock,
                "stock_minimo": p.get("stock_minimo", 0), "stock_maximo": p.get("stock_maximo", 0)}

    @app.post("/api/stock/movimiento")
    def movimiento(datos: MovimientoIn, usuario: str = Depends(usuario_actual)):
        codigo = datos.codigo.strip()
        if datos.operacion == "sumar":
            nuevo = stock_service.sumar_stock_manual(
                codigo, datos.cantidad, usuario=autor(usuario),
                motivo=datos.motivo or "Alta manual", origen=ORIGEN)
        else:
            nuevo = stock_service.restar_stock_manual(
                codigo, datos.cantidad, usuario=autor(usuario),
                motivo=datos.motivo or "Baja manual", origen=ORIGEN)
        return _respuesta_stock(codigo, nuevo)

    @app.post("/api/stock/lector")
    def lector(datos: LectorIn, usuario: str = Depends(usuario_actual)):
        """Equivalente al lector USB de la PC: cada lectura mueve 1 unidad."""
        codigo = datos.codigo.strip()
        if datos.operacion == "restar":
            nuevo = stock_service.restar_stock_por_lector(codigo, usuario=autor(usuario), origen=ORIGEN)
        else:
            nuevo = stock_service.sumar_stock_manual(
                codigo, 1, usuario=autor(usuario), motivo="Lector de código de barras", origen=ORIGEN)
        return _respuesta_stock(codigo, nuevo)

    @app.get("/api/movimientos")
    def movimientos(limite: int = 30, _u: str = Depends(usuario_actual)):
        return panel_dueno.movimientos_recientes(limite=max(1, min(limite, 500)))

    # ---------------------------- precios ---------------------------- #

    @app.post("/api/precios/previsualizar")
    def previsualizar(datos: AjustePreciosIn, _u: str = Depends(usuario_actual)):
        return bulk_edit.previsualizar_ajuste_masivo(
            datos.codigos, porcentaje=datos.porcentaje, monto_fijo=datos.monto_fijo,
            redondear=datos.redondear)

    @app.post("/api/precios/aplicar")
    def aplicar_precios(datos: AjustePreciosIn, usuario: str = Depends(usuario_actual)):
        return bulk_edit.aplicar_ajuste_masivo(
            datos.codigos, porcentaje=datos.porcentaje, monto_fijo=datos.monto_fijo,
            redondear=datos.redondear, usuario=autor(usuario), origen=ORIGEN)

    # ---------------------------- alertas ---------------------------- #

    @app.get("/api/alertas")
    def lista_alertas(_u: str = Depends(usuario_actual)):
        return alertas.listar_alertas()

    @app.get("/api/config/alertas")
    def config_alertas(_u: str = Depends(usuario_actual)):
        cfg = config.cargar_config()
        token = cfg.get("telegram", "bot_token", fallback="")
        return {
            "telegram": {
                "habilitado": cfg.get("telegram", "habilitado", fallback="false").lower() == "true",
                "chat_id_default": cfg.get("telegram", "chat_id_default", fallback=""),
                "token_configurado": bool(token),
                "token_mascara": _mascara_token(token),
            },
            "umbral_global": alertas.obtener_umbral_global(),
        }

    @app.put("/api/config/telegram")
    def guardar_telegram(datos: TelegramIn, _u: str = Depends(usuario_actual)):
        # estricto: si config.ini no se puede leer, no se lo pisa con defaults
        cfg = config.cargar_config(estricto=True)
        if datos.bot_token is not None:
            cfg["telegram"]["bot_token"] = datos.bot_token.strip()
        cfg["telegram"]["chat_id_default"] = datos.chat_id_default.strip()
        cfg["telegram"]["habilitado"] = "true" if datos.habilitado else "false"
        config.guardar_config(cfg)
        return config_alertas()

    @app.post("/api/config/telegram/probar")
    def probar_telegram(_u: str = Depends(usuario_actual)):
        from pos_core import telegram_bot   # import perezoso: requests solo si se usa
        enviado = telegram_bot.enviar_mensaje("✅ Prueba desde la app del dueño: las alertas llegan bien.")
        return {"enviado": enviado}

    @app.put("/api/config/umbrales")
    def guardar_umbrales(datos: UmbralesIn, _u: str = Depends(usuario_actual)):
        alertas.guardar_umbral_global(datos.stock_minimo, datos.stock_maximo)
        return alertas.obtener_umbral_global()

    # ---------------------------- facturas --------------------------- #

    @app.post(RUTA_ANALIZAR_FACTURA)
    async def analizar_factura(request: Request, _u: str = Depends(usuario_actual)):
        # El multipart se lee recién acá, DESPUÉS de validar el token (si
        # fuera un parámetro File, FastAPI lo volcaría entero a disco antes).
        async with request.form(max_files=1, max_fields=10) as formulario:
            archivo = formulario.get("archivo")
            if not isinstance(archivo, ArchivoSubido):
                raise HTTPException(status_code=422, detail="Falta el PDF (campo 'archivo')")
            nombre_archivo = archivo.filename or "factura.pdf"
            contenido = await archivo.read(MAX_PDF_BYTES + 1)
        if len(contenido) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="El PDF supera los 20 MB")
        if not contenido.startswith(b"%PDF"):
            raise HTTPException(status_code=415, detail="El archivo no es un PDF")
        fd, ruta = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(contenido)
            try:
                resultado = pdf_import.parsear_factura_pdf(ruta)
            except Exception as exc:
                # pdfplumber/pdfminer tiran excepciones de todo tipo ante un
                # PDF roto o cortado a mitad de la descarga.
                raise HTTPException(status_code=422, detail="El PDF está dañado o incompleto") from exc
        finally:
            os.remove(ruta)

        # El parser prueba tablas Y texto plano de la misma página, así que una
        # misma línea puede aparecer dos veces. No se descarta nada: se marca
        # la repetición para que el dueño decida en la app.
        vistos = Counter()
        items = []
        for it in resultado.items:
            clave = (it.codigo, it.cantidad, it.precio_compra)
            vistos[clave] += 1
            p = panel_dueno.obtener_producto(it.codigo)
            items.append({
                "codigo": it.codigo, "nombre": it.nombre, "cantidad": it.cantidad,
                "precio_compra": it.precio_compra,
                "existe": p is not None,
                "nombre_sistema": p["nombre"] if p else None,
                "stock_actual": p["stock"] if p else None,
                "posible_duplicado": vistos[clave] > 1,
            })
        return {
            "factura_nombre": nombre_archivo,
            "es_pdf_escaneado": resultado.es_pdf_escaneado,
            "items": items,
            "lineas_no_reconocidas": resultado.lineas_no_reconocidas,
        }

    @app.post("/api/facturas/aplicar")
    def aplicar_factura(datos: AplicarFacturaIn, usuario: str = Depends(usuario_actual)):
        items = [{"codigo": i.codigo.strip(), "cantidad": i.cantidad, "precio_compra": i.precio_compra}
                 for i in datos.items]
        return stock_service.sumar_stock_por_factura_pdf(
            items, usuario=autor(usuario), factura_nombre=datos.factura_nombre, origen=ORIGEN)

    return app


# --------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------- #

def _carpeta_programa() -> str:
    """Carpeta de ApiDueno.exe (o de este .py en desarrollo)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _ruta_log() -> str:
    """logs\\api_dueno.log de la base. Si la carpeta base no existe (un
    --base mal escrito) no se la crea: el log va a logs\\ junto a
    ApiDueno.exe."""
    base = get_base_path()
    if not os.path.isdir(base):
        base = _carpeta_programa()
    carpeta = os.path.join(base, "logs")
    os.makedirs(carpeta, exist_ok=True)
    return os.path.join(carpeta, "api_dueno.log")


def _registrar(mensaje: str) -> None:
    """Deja `mensaje` en logs\\api_dueno.log y, si hay consola, en stdout.
    Corre sin ventana (tarea programada), así que el log es lo único que
    queda para saber por qué no arrancó."""
    linea = f"{datetime.now().isoformat(sep=' ', timespec='seconds')} {mensaje}"
    try:
        with open(_ruta_log(), "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(linea, flush=True)
        except (OSError, UnicodeError):
            pass


def _definir_pin(pin: str, base: str, ruta_db: str) -> int:
    """--definir-pin: alta o cambio del PIN del usuario 'dueño' en la base,
    sin levantar el servidor. Nunca escribe el PIN en el log."""
    try:
        usuarios.validar_pin_dueno(pin)
    except ValueError as exc:
        _registrar(f"No se definió el PIN: {exc}.")
        return EXIT_BASE_INVALIDA
    if not os.path.isdir(base):
        _registrar(f"No se definió el PIN: no existe la carpeta {base}. "
                   "--base tiene que ser la carpeta de MaestroDueno.")
        return EXIT_BASE_INVALIDA
    try:
        init_db()   # si MaestroDueno todavía no la creó, se crea acá
        resultado = usuarios.definir_pin_dueno(pin)
    except (sqlite3.Error, ValueError) as exc:
        _registrar(f"No se definió el PIN en {ruta_db}: {exc}")
        return EXIT_BASE_INVALIDA
    if resultado == "creado":
        _registrar(f"Usuario 'dueño' (rol DUEÑO) creado con el PIN indicado en {ruta_db}.")
    else:
        _registrar(f"PIN del usuario 'dueño' actualizado en {ruta_db}. "
                   "Los celulares tienen que volver a ingresar el PIN.")
    return 0


def _motivo_base_invalida(base: str, ruta_db: str) -> Optional[str]:
    """Por qué la API no puede arrancar con esta base, o None si está bien.
    Antes de esto la API arrancaba igual sobre una base vacía recién creada
    y el login fallaba siempre, sin pista de por qué."""
    if not os.path.isfile(ruta_db):
        return (f"no existe la base de datos {ruta_db}. --base tiene que ser la carpeta de "
                "MaestroDueno (la que tiene database\\stock.db y config.ini).")
    try:
        init_db()
        if not usuarios.hay_usuario_activo(rol=ROL_DUENO):
            return (f"la base {ruta_db} no tiene ningún usuario DUEÑO activo. Definí el PIN con: "
                    f'ApiDueno.exe --base "{base}" --definir-pin <PIN>')
    except sqlite3.Error as exc:
        return f"no se pudo abrir la base {ruta_db}: {exc}"
    try:
        config.cargar_config(estricto=True)
    except ConfigIlegibleError as exc:
        return str(exc)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="API del Dueño para la app del celular")
    parser.add_argument("--base", help="Carpeta de MaestroDueno (la que tiene database\\stock.db y config.ini)")
    parser.add_argument("--host", default=None, help="Interfaz de escucha (default: config.ini [api] host o 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help=f"Puerto (default: {PUERTO_DEFAULT})")
    parser.add_argument("--definir-pin", metavar="PIN", default=None,
                        help="Crea o actualiza el usuario 'dueño' (rol DUEÑO) con ese PIN de 4 a 12 dígitos "
                             "y sale, sin levantar el servidor")
    args = parser.parse_args(argv)

    if args.base:
        os.environ["SISTEMA_DUAL_BASE"] = os.path.abspath(args.base)
    base = get_base_path()
    # Ruta armada a mano: paths.db_path() crearía la carpeta database\\.
    ruta_db = os.path.join(base, "database", "stock.db")

    if args.definir_pin is not None:
        return _definir_pin(args.definir_pin, base, ruta_db)

    motivo = _motivo_base_invalida(base, ruta_db)
    if motivo:
        _registrar(f"La API no arrancó: {motivo}")
        return EXIT_BASE_INVALIDA

    if sys.stdout is None or sys.stderr is None:
        # Compilado con --noconsole no hay consola: uvicorn necesita dónde escribir.
        log = open(_ruta_log(), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    cfg = config.cargar_config()
    host = args.host or cfg.get("api", "host", fallback="0.0.0.0")
    port = args.port or cfg.getint("api", "puerto", fallback=PUERTO_DEFAULT)

    # Las alertas automáticas de Telegram (stock bajo / sobre-stock) salen de
    # acá: la API es lo único que corre las 24 h en la PC del local, y ninguna
    # app de escritorio arranca el monitor. Es best-effort: sin internet no
    # manda nada y nunca afecta a la API.
    from pos_core import telegram_bot
    telegram_bot.MonitorAlertas().start()

    import uvicorn
    # proxy_headers=False: la API no está detrás de ningún proxy, y con True
    # uvicorn tomaría la IP de X-Forwarded-For en conexiones desde 127.0.0.1,
    # lo que permitía esquivar el bloqueo por IP del login.
    uvicorn.run(create_app(), host=host, port=port, use_colors=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
