"""Bot de Telegram: alertas de stoploss / sobre-stock.

Usa únicamente la API HTTP de Telegram vía `requests` (sin frameworks de
bot pesados), porque lo único que necesitamos es "avisar", no conversar.
Corre en un hilo de fondo con polling propio; funciona en el Maestro y,
si el USB tiene internet, también ahí, ya que no depende del resto del
sistema offline-first (todo lo demás -cobrar, descontar stock- sigue
funcionando sin internet aunque Telegram falle).
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import requests

from pos_core.config import cargar_config
from pos_core.db import get_connection, transaction

# El mismo logger que el servicio de stock: ahí es donde tiene archivo. En el
# Panel (que también corre un monitor mientras está abierto) no lo tiene y
# el aviso se pierde sin molestar.
log = logging.getLogger("stock_daemon")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
_COOLDOWN = timedelta(hours=4)  # no repetir la misma alerta antes de este tiempo
# Si Telegram no contesta (sin internet, o frenó los envíos por mandar muchos
# juntos), los que siguen tampoco van a salir: se corta la vuelta y se sigue
# en la próxima, en vez de esperar 10 segundos por cada producto.
_MAX_FALLAS_SEGUIDAS = 3


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


def probar_envio() -> dict:
    """Manda un mensaje de prueba y explica en castellano QUÉ pasó.

    Hasta ahora la única forma de saber si el bot estaba bien configurado
    era esperar a que un producto cruzara un umbral. Si no llegaba nada, no
    había manera de distinguir "el token está mal" de "no hay internet" o
    de "todavía ningún producto califica".

    OJO (regla 4): el token va en la URL de Telegram. Nada de lo que se
    devuelve acá puede incluir esa URL ni el token — este texto termina en
    un cartel del Panel, y una captura de ese cartel es un chat igual.
    """
    cfg = cargar_config()
    if cfg.get("telegram", "habilitado", fallback="false").lower() != "true":
        return {"ok": False, "detalle": "El bot está DESTILDADO: con 'Habilitado' sin tildar "
                                         "no se manda ninguna alerta. Tildalo y guardá."}
    token = cfg.get("telegram", "bot_token", fallback="").strip()
    chat_id = cfg.get("telegram", "chat_id_default", fallback="").strip()
    if not token:
        return {"ok": False, "detalle": "Falta el Bot Token (lo da @BotFather en Telegram)."}
    if not chat_id:
        return {"ok": False, "detalle": "Falta el Chat ID: es a quién se le manda el aviso."}

    try:
        resp = requests.post(
            API_BASE.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id,
                  "text": "✅ Otter: prueba de alertas. Si ves este mensaje, el bot está bien configurado."},
            timeout=10,
        )
    except requests.RequestException as e:
        return {"ok": False, "detalle": f"No se pudo llegar a Telegram. ¿La PC tiene internet? "
                                         f"({type(e).__name__})"}

    if resp.ok:
        return {"ok": True, "detalle": "Mensaje enviado. Fijate en el Telegram del chat configurado: "
                                        "si llegó, las alertas van a llegar igual."}

    # Los dos errores que se dan de verdad, traducidos. El cuerpo crudo de
    # Telegram se recorta y NUNCA se arma con la URL, que lleva el token.
    detalle = ""
    try:
        detalle = str(resp.json().get("description", ""))[:200]
    except Exception:
        detalle = ""
    if resp.status_code == 401:
        return {"ok": False, "detalle": "Telegram rechazó el Bot Token: está mal o fue revocado. "
                                         "Pedile uno nuevo a @BotFather."}
    if resp.status_code == 400 and "chat not found" in detalle.lower():
        return {"ok": False, "detalle": "El Chat ID no existe, o el bot todavía no habló nunca con "
                                         "ese chat. Abrí el chat con el bot y mandale un /start primero."}
    return {"ok": False, "detalle": f"Telegram rechazó el envío (código {resp.status_code}). {detalle}".strip()}


def _productos_fuera_de_umbral():
    """Un renglón por producto activo, con su umbral efectivo ya resuelto
    (el propio si tiene, si no el global).

    El global se toma con un subselect de UNA sola fila a propósito: si por
    lo que sea quedaran varias filas globales en la tabla (producto_codigo
    IS NULL no lo impide el UNIQUE, porque en SQLite cada NULL es distinto),
    un LEFT JOIN común multiplicaría cada producto por esa cantidad y
    mandaría la misma alerta repetida N veces.
    """
    conn = get_connection()
    return conn.execute(
        """
        WITH global AS (
            SELECT stock_minimo, stock_maximo, telegram_chat_id
            FROM Configuracion_Alertas
            WHERE producto_codigo IS NULL AND activo = 1
            ORDER BY id LIMIT 1
        )
        SELECT p.codigo, p.nombre, p.stock,
               COALESCE(a.stock_minimo, (SELECT stock_minimo FROM global), 0) AS stock_minimo,
               COALESCE(a.stock_maximo, (SELECT stock_maximo FROM global), 0) AS stock_maximo,
               COALESCE(a.telegram_chat_id, (SELECT telegram_chat_id FROM global)) AS chat_id,
               e.ultima_alerta_enviada
        FROM Productos p
        LEFT JOIN Configuracion_Alertas a ON a.producto_codigo = p.codigo AND a.activo = 1
        LEFT JOIN Alertas_Enviadas e ON e.producto_codigo = p.codigo
        WHERE p.activo = 1
        """
    ).fetchall()


def _en_cooldown(ultima, ahora: datetime) -> bool:
    """¿Esa alerta se mandó hace menos de 4 horas?

    Una fecha que no se puede leer (base vieja, edición manual) cuenta como
    "sin cooldown" y se reescribe al mandar: nunca debe cortar la revisión
    del RESTO de los productos. Y una fecha a más de 4 horas en el FUTURO
    tampoco frena: es de cuando el reloj de la PC estaba mal, y esperar a
    que "pase" dejaría ese producto sin avisar por años.
    """
    if not ultima:
        return False
    try:
        return abs(ahora - datetime.fromisoformat(ultima)) < _COOLDOWN
    except (TypeError, ValueError):
        return False


def _reservar_alerta(codigo: str):
    """Anota "esta alerta se está mandando ahora", SOLO si nadie la mandó en
    las últimas 4 horas. Devuelve (marca, anterior) si la reservó, o None si
    otro ya la mandó.

    Se anota ANTES de mandar, y la pregunta y la anotación van en la misma
    transacción. Las alertas las revisan dos programas a la vez —el servicio
    de stock, todo el día, y el Panel del Dueño mientras está abierto— y
    cada uno miraba el "ya se mandó" que había leído al empezar su vuelta:
    cuando las dos vueltas se pisaban, el mismo aviso llegaba dos veces. Con
    la reserva, el segundo ve que ya está tomada.

    Solo se anota CUÁNDO, en su tabla propia (Alertas_Enviadas). Antes esto
    escribía en Configuracion_Alertas, y para un producto que usaba el
    umbral global le creaba una fila propia con el valor del global
    CONGELADO adentro: al dueño le pasó exactamente eso — puso 20/20,
    salieron las alertas, y después poner 0/0 no apagó nada. Anotar el
    cooldown no puede configurar nada.
    """
    ahora = datetime.now()
    marca = ahora.isoformat(timespec="milliseconds")
    with transaction() as conn:
        fila = conn.execute(
            "SELECT ultima_alerta_enviada FROM Alertas_Enviadas WHERE producto_codigo = ?",
            (codigo,)).fetchone()
        anterior = fila["ultima_alerta_enviada"] if fila else None
        if _en_cooldown(anterior, ahora):
            return None
        conn.execute(
            """INSERT INTO Alertas_Enviadas (producto_codigo, ultima_alerta_enviada)
               VALUES (?, ?)
               ON CONFLICT(producto_codigo) DO UPDATE SET
                   ultima_alerta_enviada = excluded.ultima_alerta_enviada""",
            (codigo, marca))
    return marca, anterior


def _devolver_reserva(codigo: str, marca: str, anterior) -> None:
    """El envío no salió: se deja como estaba, para reintentarlo en la
    próxima vuelta. Solo si la marca sigue siendo la nuestra."""
    with transaction() as conn:
        if anterior is None:
            conn.execute(
                "DELETE FROM Alertas_Enviadas WHERE producto_codigo = ? AND ultima_alerta_enviada = ?",
                (codigo, marca))
        else:
            conn.execute(
                "UPDATE Alertas_Enviadas SET ultima_alerta_enviada = ? "
                "WHERE producto_codigo = ? AND ultima_alerta_enviada = ?",
                (anterior, codigo, marca))


def revisar_umbrales_y_alertar():
    # Con el bot apagado o sin token no se manda nada: tampoco se anota ni se
    # reserva nada (enviar_mensaje hace la misma pregunta; acá es para no
    # escribir en la base por cada producto que califica).
    cfg = cargar_config()
    if cfg.get("telegram", "habilitado", fallback="false").lower() != "true":
        return
    if not cfg.get("telegram", "bot_token", fallback=""):
        return
    chat_por_defecto = cfg.get("telegram", "chat_id_default", fallback="")

    fallas_seguidas = 0
    for row in _productos_fuera_de_umbral():
        alerta = None
        if row["stock_minimo"] and row["stock"] <= row["stock_minimo"]:
            alerta = f"⚠️ ALERTA STOCK BAJO: '{row['nombre']}' ({row['codigo']}) tiene solo {row['stock']} unidades (mínimo: {row['stock_minimo']})"
        elif row["stock_maximo"] and row["stock"] >= row["stock_maximo"]:
            alerta = f"📦 ALERTA SOBRE-STOCK: '{row['nombre']}' ({row['codigo']}) tiene {row['stock']} unidades (máximo: {row['stock_maximo']})"

        if not alerta:
            continue
        if not (row["chat_id"] or chat_por_defecto):
            continue          # no hay a quién mandársela
        # Primero contra lo leído al empezar la vuelta (barato, sin escribir)...
        if _en_cooldown(row["ultima_alerta_enviada"], datetime.now()):
            continue
        # ...y recién ahí la reserva, que vuelve a preguntar con la base de ahora.
        reserva = _reservar_alerta(row["codigo"])
        if reserva is None:
            continue          # la mandó el otro programa hace un instante

        if enviar_mensaje(alerta, chat_id=row["chat_id"]):
            fallas_seguidas = 0
            continue
        _devolver_reserva(row["codigo"], *reserva)
        fallas_seguidas += 1
        if fallas_seguidas >= _MAX_FALLAS_SEGUIDAS:
            return


class MonitorAlertas(threading.Thread):
    """Hilo de fondo que revisa umbrales cada `intervalo_segundos`."""

    def __init__(self, intervalo_segundos: int = 300):
        super().__init__(daemon=True)
        self.intervalo_segundos = intervalo_segundos
        self._detener = threading.Event()

    def run(self):
        ultimo_error = None
        while not self._detener.is_set():
            try:
                revisar_umbrales_y_alertar()
                ultimo_error = None
            except Exception as e:
                # Un fallo de red o de la base no debe matar el hilo de
                # alertas, pero tampoco puede quedar mudo: si las alertas no
                # llegan, hasta ahora no había dónde leer por qué. Se anota
                # una vez por error distinto, no en cada vuelta de 5 minutos.
                descripcion = f"{type(e).__name__}: {e}"
                if descripcion != ultimo_error:
                    ultimo_error = descripcion
                    log.exception("No se pudieron revisar las alertas de stock")
            self._detener.wait(self.intervalo_segundos)

    def detener(self):
        self._detener.set()
