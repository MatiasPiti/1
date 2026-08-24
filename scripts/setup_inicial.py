"""Primer arranque: crea la base de datos y un usuario dueño por defecto.

Uso:  python scripts/setup_inicial.py
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pos_core.db import init_db, transaction


def _hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def main():
    init_db()
    print("Base de datos creada/verificada en database/stock.db")

    pin = input("PIN para el usuario 'dueño' (4-6 dígitos, Enter para '1234'): ").strip() or "1234"
    with transaction() as conn:
        conn.execute(
            """INSERT INTO Usuarios (nombre, pin_hash, rol, activo)
               VALUES ('dueño', ?, 'DUEÑO', 1)
               ON CONFLICT(nombre) DO UPDATE SET pin_hash = excluded.pin_hash""",
            (_hash_pin(pin),),
        )
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
    print("Usuario 'dueño' listo y umbral global de alerta (mínimo=5) configurado.")
    print("Ahora podés abrir apps/master_dueno/main.py y cargar tu Excel inicial de productos.")


if __name__ == "__main__":
    main()
