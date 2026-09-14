"""Todo lo que hay que saber del servicio de Windows, en un solo lugar.

Existe porque el `StockService` es la pieza que más veces dejó a Leo sin
Dueño Remoto, y la misma pregunta —¿está corriendo? ¿arranca solo?
¿hay alguien escuchando en el puerto?— la necesitan el USB de
Mantenimiento, el Actualizador y cualquier cosa que venga después. Tenerla
copiada en cada uno garantizaba que en tres meses una mitad estuviera
arreglada y la otra no (el mismo criterio por el que OtterBlindaje ejecuta
los .ps1 en vez de reimplementarlos).

Regla de oro de este módulo: **ninguna función lanza una excepción.**
Preguntarle a Windows por un servicio nunca puede ser lo que voltee una
actualización ni lo que impida que la caja abra (regla 6).
"""

import os
import socket
import subprocess

NOMBRE_SERVICIO = "SistemaDualStockService"
PUERTO_POR_DEFECTO = 8765

# Windows: que no aparezca una ventana negra parpadeando al llamar a sc.exe
# desde una app con --windowed.
_SIN_VENTANA = {}
if os.name == "nt":
    _SIN_VENTANA = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _sc(*argumentos, timeout: int = 15):
    """Corre sc.exe y devuelve (codigo, salida). (None, "") si ni se pudo.

    Se usa sc.exe con el .exe explícito A PROPÓSITO: en PowerShell `sc` es
    un alias de Set-Content, así que `sc query ...` no consulta ningún
    servicio — silenciosamente intenta escribir un archivo llamado "query".
    """
    if os.name != "nt":
        return None, ""
    try:
        r = subprocess.run(["sc.exe", *argumentos], capture_output=True, text=True,
                            timeout=timeout, **_SIN_VENTANA)
        return r.returncode, f"{r.stdout}\n{r.stderr}"
    except Exception as e:
        return None, str(e)


def estado(nombre: str = NOMBRE_SERVICIO) -> str:
    """'corriendo' | 'parado' | 'no_instalado' | 'desconocido'."""
    codigo, salida = _sc("query", nombre)
    if codigo is None:
        return "desconocido"
    texto = salida.upper()
    if "RUNNING" in texto:
        return "corriendo"
    if "STOPPED" in texto or "STOP_PENDING" in texto:
        return "parado"
    # 1060 = el servicio no existe. El texto del error viene traducido, el
    # número no: por eso se mira el número.
    if "1060" in texto or "NO EXIST" in texto or "NO EXISTE" in texto:
        return "no_instalado"
    return "desconocido"


def tipo_de_arranque(nombre: str = NOMBRE_SERVICIO) -> str:
    """'auto' | 'manual' | 'deshabilitado' | 'desconocido'.

    Es LA pregunta que importa: un servicio que hoy corre pero está en
    Manual no vuelve al reiniciar la PC, y ahí Leo se queda sin panel sin
    que nadie haya tocado nada. Pasó tres veces.
    """
    codigo, salida = _sc("qc", nombre)
    if codigo is None:
        return "desconocido"
    texto = salida.upper()
    if "AUTO_START" in texto:
        return "auto"
    if "DEMAND_START" in texto:
        return "manual"
    if "DISABLED" in texto:
        return "deshabilitado"
    return "desconocido"


def poner_en_automatico(nombre: str = NOMBRE_SERVICIO) -> tuple:
    """Deja el servicio en Automatic + reintentos de Windows. (ok, detalle).

    No hay ningún motivo para que esté en Manual: pywin32 lo registra así
    por defecto y eso fue un bug del instalador, no una decisión de nadie.
    """
    codigo, salida = _sc("config", nombre, "start=", "auto")
    if codigo != 0:
        return False, (salida.strip() or "sin detalle") + " (¿falta ejecutar como administrador?)"
    # Que Windows lo reintente solo si se cae: sin esto, un cuelgue puntual
    # lo deja parado hasta que alguien pase por el local.
    _sc("failure", nombre, "reset=", "86400",
        "actions=", "restart/60000/restart/60000/restart/60000")
    return True, "arranque automático + reintentos configurados"


def arrancar(nombre: str = NOMBRE_SERVICIO, espera_s: int = 12) -> tuple:
    """Arranca el servicio y ESPERA a confirmar que quedó corriendo.

    Mandar 'sc start' y dar por hecho que arrancó es una verificación que
    no verifica: sc vuelve enseguida, el servicio puede morir un segundo
    después y el informe diría que está todo bien.
    """
    import time
    codigo, salida = _sc("start", nombre)
    if codigo is None:
        return False, salida.strip() or "no se pudo ejecutar sc.exe"
    for _ in range(max(espera_s, 1)):
        if estado(nombre) == "corriendo":
            return True, "corriendo"
        time.sleep(1)
    return False, (salida.strip() or "sigue sin quedar corriendo")


def puerto_escuchando(puerto: int = PUERTO_POR_DEFECTO, host: str = "127.0.0.1") -> bool:
    """¿Hay alguien escuchando de verdad en el puerto de la API remota?

    Es lo ÚNICO que le importa al Dueño Remoto, y no es lo mismo que "el
    servicio dice Running": la API se levanta adentro del servicio con
    iniciar_si_esta_habilitado(), que loguea y sigue si falla. El servicio
    puede estar Running y no haber nadie escuchando — para Leo eso es
    exactamente "no me puedo conectar".
    """
    try:
        with socket.create_connection((host, int(puerto)), timeout=3):
            return True
    except Exception:
        return False
