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
exactamente los mismos datos.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime
from typing import List, Literal, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator

import pos_core
from pos_core import alertas, bulk_edit, config, filters, panel_dueno, pdf_import, stock_service, usuarios
from pos_core.db import init_db
from pos_core.stock_service import ProductoNoEncontradoError, StockInsuficienteError

API_VERSION = "1.0.0"
ORIGEN = "MAESTRO"   # la API escribe directo en la base del Maestro
PUERTO_DEFAULT = 8765
DURACION_TOKEN_SEGUNDOS = 30 * 24 * 3600
MAX_INTENTOS_PIN = 5
BLOQUEO_SEGUNDOS = 5 * 60
MAX_PDF_BYTES = 20 * 1024 * 1024
CAMPOS_FILTRO = ["marca", "proveedor", "categoria", "nombre", "codigo"]


# --------------------------------------------------------------------- #
# Tokens de sesión (firmados con HMAC, sin tabla nueva en la DB)
# --------------------------------------------------------------------- #

_lock_secreto = threading.Lock()


def _secreto() -> bytes:
    """Clave de firma guardada en config.ini [api] secreto. Se genera sola
    la primera vez. Borrarla invalida todas las sesiones de celulares."""
    with _lock_secreto:
        cfg = config.cargar_config()
        if not cfg.has_section("api"):
            cfg.add_section("api")
        secreto = cfg.get("api", "secreto", fallback="")
        if not secreto:
            secreto = secrets.token_hex(32)
            cfg.set("api", "secreto", secreto)
            config.guardar_config(cfg)
        return secreto.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def emitir_token(usuario: str, *, ahora: float = None) -> dict:
    exp = int((ahora or time.time()) + DURACION_TOKEN_SEGUNDOS)
    payload = _b64(json.dumps({"u": usuario, "exp": exp}, separators=(",", ":")).encode("utf-8"))
    firma = _b64(hmac.new(_secreto(), payload.encode("ascii"), hashlib.sha256).digest())
    return {"token": f"{payload}.{firma}", "expira": exp}


def validar_token(token: str, *, ahora: float = None) -> Optional[str]:
    try:
        payload, firma = token.split(".", 1)
        esperada = _b64(hmac.new(_secreto(), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(firma, esperada):
            return None
        datos = json.loads(_unb64(payload))
        if datos["exp"] < (ahora or time.time()):
            return None
        return datos["u"]
    except (ValueError, KeyError, TypeError):
        return None


class _BloqueoIntentos:
    """Frena la fuerza bruta del PIN (un PIN de 4 dígitos son solo 10.000
    combinaciones): tras MAX_INTENTOS_PIN fallos seguidos desde una misma
    IP, esa IP queda bloqueada BLOQUEO_SEGUNDOS."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fallos = {}
        self._bloqueado_hasta = {}

    def segundos_restantes(self, ip: str) -> int:
        with self._lock:
            return max(0, int(self._bloqueado_hasta.get(ip, 0) - time.time()))

    def registrar_fallo(self, ip: str) -> None:
        with self._lock:
            self._fallos[ip] = self._fallos.get(ip, 0) + 1
            if self._fallos[ip] >= MAX_INTENTOS_PIN:
                self._bloqueado_hasta[ip] = time.time() + BLOQUEO_SEGUNDOS
                self._fallos[ip] = 0

    def registrar_exito(self, ip: str) -> None:
        with self._lock:
            self._fallos.pop(ip, None)
            self._bloqueado_hasta.pop(ip, None)


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
    porcentaje: Optional[float] = Field(default=None, ge=-100, le=1000)
    monto_fijo: Optional[float] = None
    redondear: bool = True

    @model_validator(mode="after")
    def _uno_solo(self):
        if (self.porcentaje is None) == (self.monto_fijo is None):
            raise ValueError("Indicá porcentaje o monto_fijo (uno solo)")
        return self


class PrecioIn(BaseModel):
    precio_venta: float = Field(ge=0)


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
    precio_compra: Optional[float] = Field(default=None, ge=0)


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


def create_app() -> FastAPI:
    app = FastAPI(title="API del Dueño", version=API_VERSION)
    bloqueo = _BloqueoIntentos()
    bearer = HTTPBearer(auto_error=False)

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
        ip = request.client.host if request.client else "?"
        restantes = bloqueo.segundos_restantes(ip)
        if restantes:
            raise HTTPException(status_code=429,
                                detail=f"Demasiados intentos. Probá de nuevo en {restantes // 60 + 1} min.")
        nombre = usuarios.verificar_pin(datos.pin, rol="DUEÑO", nombre=datos.usuario)
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
        cfg = config.cargar_config()
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

    @app.post("/api/facturas/analizar")
    async def analizar_factura(archivo: UploadFile = File(...), _u: str = Depends(usuario_actual)):
        contenido = await archivo.read(MAX_PDF_BYTES + 1)
        if len(contenido) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="El PDF supera los 20 MB")
        if not contenido.startswith(b"%PDF"):
            raise HTTPException(status_code=415, detail="El archivo no es un PDF")
        fd, ruta = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(contenido)
            resultado = pdf_import.parsear_factura_pdf(ruta)
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
            "factura_nombre": archivo.filename or "factura.pdf",
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

def main(argv=None):
    parser = argparse.ArgumentParser(description="API del Dueño para la app del celular")
    parser.add_argument("--base", help="Carpeta de MaestroDueno (la que tiene database\\stock.db y config.ini)")
    parser.add_argument("--host", default=None, help="Interfaz de escucha (default: config.ini [api] host o 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help=f"Puerto (default: {PUERTO_DEFAULT})")
    args = parser.parse_args(argv)

    if args.base:
        os.environ["SISTEMA_DUAL_BASE"] = os.path.abspath(args.base)

    from pos_core.paths import logs_dir
    if sys.stdout is None or sys.stderr is None:
        # Compilado con --noconsole no hay consola: uvicorn necesita dónde escribir.
        log = open(os.path.join(logs_dir(), "api_dueno.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    init_db()
    cfg = config.cargar_config()
    host = args.host or cfg.get("api", "host", fallback="0.0.0.0")
    port = args.port or cfg.getint("api", "puerto", fallback=PUERTO_DEFAULT)

    import uvicorn
    uvicorn.run(create_app(), host=host, port=port, use_colors=False)


if __name__ == "__main__":
    main()
