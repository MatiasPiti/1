"""API remota del Maestro — lo que le permite al Dueño Remoto (otra PC,
en otra ubicación, conectada por una VPN privada tipo Tailscale) usar el
Panel del Dueño en vivo sin tocar el archivo de base de datos por red
(que es justamente lo que NO hay que hacer con SQLite: los bloqueos de
escritura sobre una carpeta compartida de red no son confiables y pueden
terminar corrompiendo la base).

En cambio, esta API expone por HTTP las MISMAS funciones de pos_core que
ya usa el Panel del Dueño local — nunca SQL crudo, nunca acceso directo
al archivo: siempre a través de la lógica de negocio ya escrita y ya
probada. Corre adentro del mismo servicio oculto de stock (ver
services/stock_daemon_windows.py), como un hilo más.

Seguridad:
  - Un único token compartido (autogenerado, ver pos_core.config.token_remoto)
    que hay que cargar también en el Dueño Remoto — sin él, cualquier
    pedido a /rpc devuelve 401.
  - Solo escucha en la red privada que arma la VPN (Tailscale u otra) —
    este servidor NO debe exponerse directo a internet ni con port
    forwarding: la seguridad depende de que solo dispositivos dentro de
    esa red privada puedan alcanzar este puerto.
  - Solo se puede llamar a las funciones explícitamente listadas en
    ALLOWLIST — nunca se evalúa ni ejecuta un nombre de función arbitrario
    que venga en el pedido.
"""

import base64
import dataclasses
import json
import logging
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("remote_api")


def _json_default(valor):
    """Los resultados de pos_core a veces son dataclasses (p.ej.
    pdf_import.ResultadoParsingPDF) — json.dumps no las serializa solo."""
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        return dataclasses.asdict(valor)
    return str(valor)

# ---------------------------------------------------------------------- #
# Allowlist: única fuente de verdad de qué se puede llamar remotamente.
# Se arma perezosamente (import tardío) para no acoplar este módulo a
# que pos_core esté 100% inicializado en el momento del import.
# ---------------------------------------------------------------------- #
def _construir_allowlist() -> dict:
    from pos_core import (stock_service, bulk_edit, pdf_import, excel_import, filters, config,
                           alerts, audit, products, ofertas, reports, sales)

    return {
        "reports.resumen_dashboard": reports.resumen_dashboard,
        "reports.totales_por_metodo_pago": reports.totales_por_metodo_pago,
        "products.listar_stock": products.listar_stock,
        "products.listar_para_filtro": products.listar_para_filtro,
        "products.crear_producto": products.crear_producto,
        "stock_service.sumar_stock_manual": stock_service.sumar_stock_manual,
        "stock_service.restar_stock_manual": stock_service.restar_stock_manual,
        "stock_service.restar_stock_por_lector": stock_service.restar_stock_por_lector,
        "stock_service.sumar_stock_por_factura_pdf": stock_service.sumar_stock_por_factura_pdf,
        "filters.listar_filtros_guardados": filters.listar_filtros_guardados,
        "filters.aplicar_filtro": filters.aplicar_filtro,
        "filters.eliminar_filtro": filters.eliminar_filtro,
        "filters.guardar_filtro_manual": filters.guardar_filtro_manual,
        "bulk_edit.aplicar_ajuste_masivo": bulk_edit.aplicar_ajuste_masivo,
        "pdf_import.parsear_factura_pdf": pdf_import.parsear_factura_pdf,
        "excel_import.cargar_masivo": excel_import.cargar_masivo,
        "excel_import.exportar_lista_precios": excel_import.exportar_lista_precios,
        "config.obtener_config_dict": config.obtener_config_dict,
        "config.actualizar_config_dict": config.actualizar_config_dict,
        "sales.resumen_facturacion": sales.resumen_facturacion,
        "sales.facturar_venta_arca": sales.facturar_venta_arca,
        "alerts.listar_umbrales_por_producto": alerts.listar_umbrales_por_producto,
        "alerts.set_umbral_producto": alerts.set_umbral_producto,
        "alerts.quitar_umbral_producto": alerts.quitar_umbral_producto,
        "alerts.set_umbral_global": alerts.set_umbral_global,
        "ofertas.crear_oferta": ofertas.crear_oferta,
        "ofertas.listar_ofertas": ofertas.listar_ofertas,
        "ofertas.cancelar_oferta": ofertas.cancelar_oferta,
        "audit.listar_lineas_eliminadas": audit.listar_lineas_eliminadas,
    }


# Funciones cuyo primer argumento posicional relevante es una RUTA A UN
# ARCHIVO LOCAL: en modo remoto ese archivo vive en la PC del Dueño, no
# en la del local, así que el cliente manda los bytes (base64) y acá se
# escriben a un temporal antes de llamar a la función real.
_FUNCIONES_CON_SUBIDA = {
    "excel_import.cargar_masivo": "ruta",
    "pdf_import.parsear_factura_pdf": "ruta_pdf",
}

# Funciones que ESCRIBEN un archivo en la ruta que reciben: acá se les da
# un temporal, y el contenido resultante vuelve al cliente en base64 para
# que lo guarde donde el dueño remoto elija.
_FUNCIONES_CON_DESCARGA = {
    "excel_import.exportar_lista_precios": "ruta",
}


class _Handler(BaseHTTPRequestHandler):
    allowlist = None
    token = None

    def log_message(self, formato, *args):
        log.info("%s - %s", self.address_string(), formato % args)

    def _responder(self, status: int, payload: dict):
        cuerpo = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _autenticado(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def do_GET(self):
        if self.path == "/health":
            self._responder(200, {"ok": True})
        else:
            self._responder(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/rpc":
            self._responder(404, {"ok": False, "error": "not found"})
            return
        if not self._autenticado():
            self._responder(401, {"ok": False, "error": "token inválido"})
            return

        try:
            largo = int(self.headers.get("Content-Length", 0))
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            self._responder(400, {"ok": False, "error": f"pedido mal formado: {e}"})
            return

        clave = f"{cuerpo.get('modulo')}.{cuerpo.get('funcion')}"
        funcion = self.allowlist.get(clave)
        if funcion is None:
            self._responder(403, {"ok": False, "error": f"función no permitida: {clave}"})
            return

        posicionales = list(cuerpo.get("posicionales") or [])
        args = dict(cuerpo.get("args") or {})
        archivo_temporal = None
        try:
            if clave in _FUNCIONES_CON_SUBIDA:
                archivo_b64 = cuerpo.get("archivo_b64")
                if not archivo_b64:
                    raise ValueError("Falta el archivo a subir")
                fd, archivo_temporal = tempfile.mkstemp(suffix=cuerpo.get("nombre_archivo", ""))
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(archivo_b64))
                args[_FUNCIONES_CON_SUBIDA[clave]] = archivo_temporal
                resultado = funcion(**args)
                self._responder(200, {"ok": True, "resultado": resultado})

            elif clave in _FUNCIONES_CON_DESCARGA:
                fd, archivo_temporal = tempfile.mkstemp(suffix=".xlsx")
                os.close(fd)
                args[_FUNCIONES_CON_DESCARGA[clave]] = archivo_temporal
                resultado = funcion(**args)
                with open(archivo_temporal, "rb") as f:
                    archivo_b64 = base64.b64encode(f.read()).decode("ascii")
                self._responder(200, {"ok": True, "resultado": resultado, "archivo_b64": archivo_b64})

            else:
                resultado = funcion(*posicionales, **args)
                self._responder(200, {"ok": True, "resultado": resultado})

        except Exception as e:
            log.exception("Error ejecutando %s", clave)
            self._responder(400, {"ok": False, "error": str(e)})
        finally:
            if archivo_temporal and os.path.exists(archivo_temporal):
                try:
                    os.remove(archivo_temporal)
                except OSError:
                    pass


def iniciar_servidor(*, puerto: int, token: str) -> ThreadingHTTPServer:
    """Arranca el servidor en un hilo demonio y lo devuelve (para poder
    pedirle shutdown() si hace falta, p.ej. en tests)."""
    handler = type("_HandlerConfigurado", (_Handler,), {
        "allowlist": _construir_allowlist(),
        "token": token,
    })
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), handler)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True, name="RemoteAPI")
    hilo.start()
    log.info("API remota escuchando en el puerto %s", puerto)
    return servidor


def iniciar_si_esta_habilitado():
    """Se llama desde el servicio oculto de stock. No hace nada (y nunca
    tira una excepción hacia arriba) si la API remota está deshabilitada
    o mal configurada — igual que el bot de Telegram, es 'best effort'."""
    from pos_core import config

    cfg = config.cargar_config()
    if cfg.get("remoto", "habilitado", fallback="false").strip().lower() not in ("true", "1", "si", "sí"):
        return None
    try:
        puerto = int(cfg.get("remoto", "puerto", fallback="8765"))
        token = config.token_remoto()
        return iniciar_servidor(puerto=puerto, token=token)
    except Exception:
        log.exception("No se pudo iniciar la API remota")
        return None
