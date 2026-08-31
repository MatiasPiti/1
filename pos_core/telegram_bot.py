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
    conn = get_connection()
    return conn.execute(
        """
        SELECT p.codigo, p.nombre, p.stock,
               COALESCE(a.stock_minimo, ga.stock_minimo, 0) AS stock_minimo,
               COALESCE(a.stock_maximo, ga.stock_maximo, 0) AS stock_maximo,
               COALESCE(a.telegram_chat_id, ga.telegram_chat_id) AS chat_id,
               a.ultima_alerta_enviada
        FROM Productos p
        LEFT JOIN Configuracion_Alertas a ON a.producto_codigo = p.codigo AND a.activo = 1
        LEFT JOIN Configuracion_Alertas ga ON ga.producto_codigo IS NULL AND ga.activo = 1
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
            with transaction() as conn:
                # Si este producto todavía no tiene fila propia en
                # Configuracion_Alertas (está usando el umbral GLOBAL vía
                # el COALESCE de _productos_fuera_de_umbral), el INSERT de
                # acá abajo crea una. Hay que pasarle explícitamente el
                # umbral efectivo (row['stock_minimo']/['stock_maximo'],
                # ya resuelto con COALESCE) — si no, la fila nueva cae en
                # los defaults de la columna (5 / 0) y ese producto queda
                # "pegado" a un umbral distinto del global para siempre,
                # como efecto secundario de solo registrar el cooldown.
                conn.execute(
                    """INSERT INTO Configuracion_Alertas
                       (producto_codigo, stock_minimo, stock_maximo, ultima_alerta_enviada, activo)
                       VALUES (?, ?, ?, ?, 1)
                       ON CONFLICT(producto_codigo) DO UPDATE SET
                           ultima_alerta_enviada = excluded.ultima_alerta_enviada""",
                    (row["codigo"], row["stock_minimo"], row["stock_maximo"],
                     ahora.isoformat(timespec="milliseconds")),
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
