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

# OJO: todo módulo que el Panel use como `self.backend.<modulo>` tiene que
# estar acá Y en LocalBackend. Faltaba telegram_bot: el botón "Probar (manda
# un mensaje)" de la pestaña Alertas contestaba "'LocalBackend' object has no
# attribute 'telegram_bot'" en el Panel y en el Dueño Remoto, aunque la
# función existía y estaba permitida en la API remota. Lo cuida
# tests/test_umbral_global.py apretando el botón, no llamando a la función.
_NOMBRES_MODULOS = ("reports", "products", "stock_service", "filters", "bulk_edit",
                     "pdf_import", "excel_import", "config", "sales", "alerts", "ofertas",
                     "audit", "precios", "actualizacion_precios", "telegram_bot")


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
                               config, alerts, audit, products, ofertas, reports, sales,
                               precios, actualizacion_precios, telegram_bot)
        modulos = {"stock_service": stock_service, "bulk_edit": bulk_edit, "pdf_import": pdf_import,
                   "excel_import": excel_import, "filters": filters, "config": config,
                   "alerts": alerts, "audit": audit, "products": products, "ofertas": ofertas,
                   "reports": reports, "sales": sales, "precios": precios,
                   "actualizacion_precios": actualizacion_precios, "telegram_bot": telegram_bot}
        for nombre, modulo in modulos.items():
            setattr(self, nombre, _ModuloLocal(modulo))

    def subir_y_llamar(self, modulo: str, funcion: str, ruta_local: str, **kwargs):
        return getattr(getattr(self, modulo), funcion)(ruta_local, **kwargs)

    def llamar_y_descargar(self, modulo: str, funcion: str, ruta_destino: str, **kwargs):
        return getattr(getattr(self, modulo), funcion)(ruta_destino, **kwargs)

    def verificar_conexion(self) -> bool:
        return True


# Los errores de la capa de abajo (urllib3) que significan "no se llegó a
# conectar": el pedido nunca salió de esta computadora. Se miran por nombre
# y no importando la clase, porque cambian entre versiones de la librería.
_ERRORES_SIN_CONEXION = {"NewConnectionError", "NameResolutionError", "ConnectTimeoutError"}


def _nunca_salio(error) -> bool:
    """¿El pedido no llegó a salir? (el local apagado, Tailscale cortado, un
    nombre que no se puede resolver). Recorre el error y los que lo causaron."""
    vistos = 0
    while error is not None and vistos < 8:
        if type(error).__name__ in _ERRORES_SIN_CONEXION:
            return True
        siguiente = getattr(error, "reason", None)
        if not isinstance(siguiente, BaseException):
            argumentos = getattr(error, "args", ())
            siguiente = argumentos[0] if argumentos and isinstance(argumentos[0], BaseException) else None
        if siguiente is None:
            siguiente = error.__cause__ or error.__context__
        error = siguiente
        vistos += 1
    return False


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

    # Dos esperas distintas, porque son dos preguntas distintas:
    #  - cuánto esperar a que el local CONTESTE EL LLAMADO (si está apagado o
    #    sin VPN no va a contestar nunca: mejor enterarse en segundos);
    #  - cuánto esperar la RESPUESTA una vez que atendió. Un aumento sobre un
    #    grupo grande o la carga de un Excel tardan lo suyo, y con 20 s para
    #    todo el Panel decía "no se pudo conectar" mientras el local seguía
    #    trabajando — una invitación a apretar el botón de nuevo.
    ESPERA_CONEXION_S = 8
    ESPERA_RESPUESTA_S = 120

    def __init__(self, base_url: str, token: str, *, timeout: int = ESPERA_RESPUESTA_S):
        import requests
        self._requests = requests
        base_url = (base_url or "").strip().rstrip("/")
        if base_url and "://" not in base_url:
            # Es muy fácil cargar solo "100.x.x.x:8765" (que es lo que
            # muestra Tailscale). Sin esquema, requests tira MissingSchema y
            # el usuario solo ve "no se pudo conectar", sin pista de que le
            # faltaba el http://.
            base_url = f"http://{base_url}"
        self.base_url = base_url
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

    def _explicar(self, error) -> str:
        """Por qué no se pudo, en castellano y SIN la dirección del local.

        El texto de requests trae adentro la IP de Tailscale y el puerto
        ("HTTPConnectionPool(host='100.x.y.z', port=8765): Max retries
        exceeded with url: /rpc (Caused by..."): ese cartel es el que el
        dueño fotografía para preguntar qué pasa, y una foto es un chat igual
        (regla 4). Además de no entenderse.

        Distingue lo único que al dueño le cambia qué hacer: si el pedido NO
        llegó a salir (se puede repetir tranquilo) o si se cortó cuando el
        local ya lo había recibido (puede haberse hecho igual).
        """
        excepciones = self._requests.exceptions
        if isinstance(error, (excepciones.MissingSchema, excepciones.InvalidSchema,
                              excepciones.InvalidURL)):
            return ("No se pudo conectar con el local: la dirección cargada no es válida. "
                    "Revisala en la pantalla de conexión.")
        if isinstance(error, excepciones.ConnectTimeout) or _nunca_salio(error):
            return ("No se pudo conectar con el local: no contesta. Revisá que la PC del local esté "
                    "prendida y Tailscale conectado en las dos computadoras. No se cambió nada.")
        if isinstance(error, excepciones.ReadTimeout):
            return ("No se pudo conectar con el local: recibió el pedido pero tardó demasiado en "
                    "contestar. Puede que lo haya hecho igual: fijate antes de repetirlo.")
        return (f"No se pudo conectar con el local: se cortó la conexión en el medio "
                f"({type(error).__name__}). Puede que lo haya hecho igual: fijate antes de repetirlo.")

    def _post(self, cuerpo: dict) -> dict:
        try:
            resp = self._requests.post(f"{self.base_url}/rpc",
                                        timeout=(self.ESPERA_CONEXION_S, self.timeout),
                                        headers=self._headers(), json=cuerpo)
        except self._requests.RequestException as e:
            # "from None": que el error original, con la dirección, tampoco
            # quede encadenado en una traza.
            raise RemoteError(self._explicar(e)) from None

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
