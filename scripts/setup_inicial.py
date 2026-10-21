"""Primer arranque: crea la base de datos y el umbral global de alertas.

Uso:  python scripts/setup_inicial.py

Ya NO crea el usuario 'dueño' con un PIN: antes lo guardaba con sha256 y
'1234' por defecto, y ese hash es justamente el que la API del celular no
acepta nunca (cualquiera prueba 1234 primero, y un sha256 sin sal ni
pimienta sale por fuerza bruta de una copia de la base). El PIN de la app
del celular se define aparte, con `definir-pin`, que lo pide sin mostrarlo
y lo guarda con PBKDF2 + sal + pimienta.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pos_core import paths
from pos_core.db import init_db, transaction


def main():
    init_db()
    base = paths.get_base_path()
    print(f"Base de datos creada/verificada en {os.path.join(base, 'database', 'stock.db')}")

    with transaction() as conn:
        # NULL no es comparable vía ON CONFLICT en SQLite (cada NULL cuenta
        # como distinto para el UNIQUE), así que se chequea a mano.
        existe_global = conn.execute(
            "SELECT 1 FROM Configuracion_Alertas WHERE producto_codigo IS NULL"
        ).fetchone()
        if not existe_global:
            conn.execute(
                """INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo, activo)
                   VALUES (NULL, 5, 0, 1)""",
            )
    print("Umbral global de alerta (mínimo=5) configurado.")
    print("Ahora podés abrir apps/master_dueno/main.py y cargar tu Excel inicial de productos.")
    # La carpeta que se usó DE VERDAD (este script no acepta --base: no se
    # le sugiere uno inventado).
    print("El PIN de la app del celular se define con: "
          f"python services/api_celular_servicio.py definir-pin --base \"{base}\"")


if __name__ == "__main__":
    main()
