"""Primer arranque: crea la base de datos y un usuario dueño por defecto.

Uso:  python scripts/setup_inicial.py
      python scripts/setup_inicial.py --base "C:\\SistemaDual\\MaestroDueno"

`--base` es la carpeta de la app (la que tiene o va a tener database\\stock.db
y config.ini). Sin `--base`, se usa la carpeta de este script.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Primer arranque: base de datos + usuario dueño")
    parser.add_argument("--base", help="Carpeta de la app (donde va database\\stock.db y config.ini)")
    args = parser.parse_args(argv)
    if args.base:
        # Antes de cualquier acceso a la DB: paths.get_base_path() lo lee.
        os.environ["SISTEMA_DUAL_BASE"] = os.path.abspath(args.base)

    from pos_core.db import init_db, transaction
    from pos_core.paths import db_path
    from pos_core.usuarios import definir_pin_dueno

    init_db()
    print(f"Base de datos creada/verificada en {db_path()}")

    while True:
        pin = input("PIN para el usuario 'dueño' (4 a 12 dígitos, Enter para '1234'): ").strip() or "1234"
        try:
            definir_pin_dueno(pin)
            break
        except ValueError as exc:
            print(exc)

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
    print("Usuario 'dueño' listo y umbral global de alerta (mínimo=5) configurado.")
    print("Ahora podés abrir apps/master_dueno/main.py y cargar tu Excel inicial de productos.")


if __name__ == "__main__":
    main()
