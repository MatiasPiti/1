""""Backend" del Panel del Dueño: la capa que decide si cada acción
(ver stock, cargar un Excel, crear una oferta...) se resuelve tocando la
base de datos local directamente (LocalBackend, el Maestro de siempre) o
pidiéndosela por HTTP a la PC del local (RemoteBackend, el Dueño Remoto).

apps/master_dueno/main.py llama siempre a `self.backend.<modulo>.<función>`
en vez de importar los módulos de pos_core directamente — así el mismo
código de la interfaz sirve para los dos modos sin ningún `if remoto:`
repetido en cada botón.
"""

import base64
import dataclasses
import os

_NOMBRES_MODULOS = ("reports", "products", "stock_service", "filters", "bulk_edit",
                     "pdf_import", "excel_import", "config", "sales", "alerts", "ofertas", "audit")


def _normalizar(valor):
    """Convierte dataclasses (p.ej. ResultadoParsingPDF de pdf_import) a
    dicts comunes, recursivamente. Así el resultado de una llamada tiene
    SIEMPRE la misma forma (dict/list/primitivo) sin importar si vino de
    LocalBackend o de RemoteBackend (que ya lo recibe como JSON)."""
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        return {f.name: _normalizar(getattr(valor, f.name)) for f in dataclasses.fields(valor)}
    if isinstance(valor, list):
        return [_normalizar(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _normalizar(v) for k, v in valor.items()}
    return valor


class _ModuloLocal:
    """Envuelve un módulo real de pos_core para que sus funciones
    devuelvan resultados normalizados (ver _normalizar), igual que
    vendrían por la API remota."""

    def __init__(self, modulo_real):
        self._modulo_real = modulo_real

    def __getattr__(self, nombre_funcion):
        funcion_real = getattr(self._modulo_real, nombre_funcion)

        def _llamar(*args, **kwargs):
            return _normalizar(funcion_real(*args, **kwargs))
        return _llamar


class LocalBackend:
    """Modo Maestro (de siempre): cada atributo envuelve el módulo real
    de pos_core — mismo comportamiento de siempre, solo que el resultado
    se normaliza a dict/list/primitivo para que el código de la UI no
    tenga que distinguir si está hablando local o remoto."""

    def __init__(self):
        from pos_core import (stock_service, bulk_edit, pdf_import, excel_import, filters,
                               config, alerts, audit, products, ofertas, reports, sales)
        modulos = {"stock_service": stock_service, "bulk_edit": bulk_edit, "pdf_import": pdf_import,
                   "excel_import": excel_import, "filters": filters, "config": config,
                   "alerts": alerts, "audit": audit, "products": products, "ofertas": ofertas,
                   "reports": reports, "sales": sales}
        for nombre, modulo in modulos.items():
            setattr(self, nombre, _ModuloLocal(modulo))

    def subir_y_llamar(self, modulo: str, funcion: str, ruta_local: str, **kwargs):
        return getattr(getattr(self, modulo), funcion)(ruta_local, **kwargs)

    def llamar_y_descargar(self, modulo: str, funcion: str, ruta_destino: str, **kwargs):
        return getattr(getattr(self, modulo), funcion)(ruta_destino, **kwargs)

    def verificar_conexion(self) -> bool:
        return True


class RemoteError(Exception):
    """Cualquier problema hablando con el local: sin conexión, token
    inválido, o la propia función remota falló (p.ej. ARCA rechazó una
    factura). El mensaje está pensado para mostrarse tal cual al dueño."""


class _ModuloRemoto:
    def __init__(self, backend: "RemoteBackend", nombre_modulo: str):
        self._backend = backend
        self._nombre_modulo = nombre_modulo

    def __getattr__(self, nombre_funcion):
        def _llamar(*args, **kwargs):
            return self._backend.llamar(self._nombre_modulo, nombre_funcion, args, kwargs)
        return _llamar


class RemoteBackend:
    """Modo Dueño Remoto: cada `self.<modulo>.<función>(...)` termina
    siendo un POST a http://<PC del local>:<puerto>/rpc."""

    def __init__(self, base_url: str, token: str, *, timeout: int = 20):
        import requests
        self._requests = requests
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        for nombre in _NOMBRES_MODULOS:
            setattr(self, nombre, _ModuloRemoto(self, nombre))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def verificar_conexion(self) -> bool:
        try:
            resp = self._requests.get(f"{self.base_url}/health", headers=self._headers(), timeout=5)
            return resp.status_code == 200 and resp.json().get("ok") is True
        except self._requests.RequestException:
            return False

    def _post(self, cuerpo: dict) -> dict:
        try:
            resp = self._requests.post(f"{self.base_url}/rpc", timeout=self.timeout,
                                        headers=self._headers(), json=cuerpo)
        except self._requests.RequestException as e:
            raise RemoteError(f"No se pudo conectar con el local: {e}")

        if resp.status_code == 401:
            raise RemoteError("Token remoto inválido — revisar la configuración de conexión.")
        try:
            data = resp.json()
        except ValueError:
            raise RemoteError(f"El local devolvió una respuesta inválida (código {resp.status_code}).")
        if not data.get("ok"):
            raise RemoteError(data.get("error") or f"Error desconocido (código {resp.status_code}).")
        return data

    def llamar(self, modulo: str, funcion: str, args=(), kwargs=None):
        data = self._post({"modulo": modulo, "funcion": funcion,
                            "posicionales": list(args), "args": kwargs or {}})
        return data.get("resultado")

    def subir_y_llamar(self, modulo: str, funcion: str, ruta_local: str, **kwargs):
        """Para funciones que hoy reciben una ruta de archivo LOCAL (Carga
        Excel, Facturas PDF): ese archivo está en la PC del dueño remoto,
        no en la del local, así que se mandan los bytes."""
        with open(ruta_local, "rb") as f:
            archivo_b64 = base64.b64encode(f.read()).decode("ascii")
        data = self._post({
            "modulo": modulo, "funcion": funcion, "args": kwargs,
            "archivo_b64": archivo_b64, "nombre_archivo": os.path.splitext(ruta_local)[1],
        })
        return data.get("resultado")

    def llamar_y_descargar(self, modulo: str, funcion: str, ruta_destino: str, **kwargs):
        """Para funciones que ESCRIBEN un archivo (exportar lista de
        precios): el resultado hay que guardarlo acá, en la PC del dueño
        remoto, no en la del local."""
        data = self._post({"modulo": modulo, "funcion": funcion, "args": kwargs})
        with open(ruta_destino, "wb") as f:
            f.write(base64.b64decode(data["archivo_b64"]))
        return data.get("resultado")
