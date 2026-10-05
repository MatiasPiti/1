"""Bot de Telegram: alertas de stoploss / sobre-stock.

Usa únicamente la API HTTP de Telegram vía `requests` (sin frameworks de
bot pesados), porque lo único que necesitamos es "avisar", no conversar.
Corre en un hilo de fondo con polling propio; funciona en el Maestro y,
si el USB tiene internet, también ahí, ya que no depende del resto del
sistema offline-first (todo lo demás -cobrar, descontar stock- sigue
funcionando sin internet aunque Telegram falle).
"""

import threading
import time
from datetime import datetime, timedelta

import requests

from pos_core import alertas
from pos_core.config import cargar_config
from pos_core.db import get_connection, transaction

API_BASE = "https://api.telegram.org/bot{token}/{method}"
_COOLDOWN = timedelta(hours=4)  # no repetir la misma alerta antes de este tiempo


def enviar_mensaje(texto: str, *, chat_id: str = None, timeout: int = 10) -> bool:
    cfg = cargar_config()
    if cfg.get("telegram", "habilitado", fallback="false").lower() != "true":
        return False
    token = cfg.get("telegram", "bot_token", fallback="")
    chat_id = chat_id or cfg.get("telegram", "chat_id_default", fallback="")
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            API_BASE.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": texto},
            timeout=timeout,
        )
        return resp.ok
    except requests.RequestException:
        return False  # sin internet: no debe romper el resto del sistema


def _productos_fuera_de_umbral():
    """Umbrales con el mismo criterio que pos_core/alertas.py (la app y
    Telegram tienen que coincidir). El cooldown se lee con un JOIN aparte
    que no filtra `activo`, porque las filas de cooldown quedan inactivas."""
    conn = get_connection()
    return conn.execute(
        f"""
        SELECT p.codigo, p.nombre, p.stock,
               {alertas.SQL_UMBRALES_EFECTIVOS},
               {alertas.SQL_CHAT_ID_EFECTIVO},
               c.ultima_alerta_enviada
        FROM Productos p
        {alertas.SQL_JOIN_UMBRAL_PRODUCTO}
        LEFT JOIN Configuracion_Alertas c ON c.producto_codigo = p.codigo
        WHERE p.activo = 1
        """
    ).fetchall()


def revisar_umbrales_y_alertar():
    ahora = datetime.now()
    for row in _productos_fuera_de_umbral():
        alerta = None
        if row["stock_minimo"] and row["stock"] <= row["stock_minimo"]:
            alerta = f"⚠️ ALERTA STOCK BAJO: '{row['nombre']}' ({row['codigo']}) tiene solo {row['stock']} unidades (mínimo: {row['stock_minimo']})"
        elif row["stock_maximo"] and row["stock"] >= row["stock_maximo"]:
            alerta = f"📦 ALERTA SOBRE-STOCK: '{row['nombre']}' ({row['codigo']}) tiene {row['stock']} unidades (máximo: {row['stock_maximo']})"

        if not alerta:
            continue

        ultima = row["ultima_alerta_enviada"]
        if ultima and (ahora - datetime.fromisoformat(ultima)) < _COOLDOWN:
            continue

        if enviar_mensaje(alerta, chat_id=row["chat_id"]):
            # La fila nueva nace con activo = 0: solo guarda el cooldown y
            # no debe pasar a ser el umbral del producto (traería los DEFAULT
            # 5 / 0 del esquema y taparía el global). Si ya hay una fila de
            # umbral real, el ON CONFLICT solo toca ultima_alerta_enviada.
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO Configuracion_Alertas (producto_codigo, ultima_alerta_enviada, activo)
                       VALUES (?, ?, 0)
                       ON CONFLICT(producto_codigo) DO UPDATE SET ultima_alerta_enviada = excluded.ultima_alerta_enviada""",
                    (row["codigo"], ahora.isoformat(timespec="milliseconds")),
                )


class MonitorAlertas(threading.Thread):
    """Hilo de fondo que revisa umbrales cada `intervalo_segundos`."""

    def __init__(self, intervalo_segundos: int = 300):
        super().__init__(daemon=True)
        self.intervalo_segundos = intervalo_segundos
        self._detener = threading.Event()

    def run(self):
        while not self._detener.is_set():
            try:
                revisar_umbrales_y_alertar()
            except Exception:
                pass  # un fallo de red/DB puntual no debe matar el hilo de alertas
            self._detener.wait(self.intervalo_segundos)

    def detener(self):
        self._detener.set()
