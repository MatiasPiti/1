"""Corre todas las pruebas y dice cuáles quedaron en verde.

    python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
    xvfb-run -a venv/bin/python tests/correr_todos.py      # Linux
    venv\\Scripts\\python tests\\correr_todos.py             # Windows

Cada archivo es un script suelto que termina con código 0 si pasó: no hay
framework de tests que instalar ni configurar, y cualquiera se puede correr
solo para ver su salida completa.
"""
import glob
import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    archivos = sorted(glob.glob(os.path.join(AQUI, "test_*.py")))
    ok, fallados = 0, []

    for archivo in archivos:
        nombre = os.path.basename(archivo)
        proceso = subprocess.run([sys.executable, archivo], capture_output=True, text=True)
        if proceso.returncode == 0:
            print(f"OK    {nombre}")
            ok += 1
        else:
            print(f"FALLA {nombre}")
            fallados.append((nombre, proceso.stdout, proceso.stderr))

    print(f"\n{ok} en verde, {len(fallados)} en rojo")
    for nombre, salida, error in fallados:
        print(f"\n===== {nombre} =====")
        print(salida[-3000:])
        print(error[-3000:])
    return 1 if fallados else 0


if __name__ == "__main__":
    raise SystemExit(main())
