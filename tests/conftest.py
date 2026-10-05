import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PIN = "1234"


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Instalación aislada: database/stock.db y config.ini en tmp_path."""
    monkeypatch.setenv("SISTEMA_DUAL_BASE", str(tmp_path))
    from pos_core.db import init_db, transaction
    from pos_core.usuarios import hash_pin

    init_db()
    with transaction() as conn:
        conn.execute("INSERT INTO Usuarios (nombre, pin_hash, rol) VALUES ('dueño', ?, 'DUEÑO')",
                     (hash_pin(PIN),))
        conn.execute("INSERT INTO Usuarios (nombre, pin_hash, rol) VALUES ('cajero', ?, 'CAJERO')",
                     (hash_pin("9999"),))
        conn.execute("INSERT INTO Configuracion_Alertas (producto_codigo, stock_minimo, stock_maximo) "
                     "VALUES (NULL, 5, 0)")
        for codigo, nombre, precio, stock, marca in [
            ("7790001", "Yerba Mate 1kg", 2500, 20, "Playadito"),
            ("7790002", "Café Molido 500g", 4300, 3, "Colombia"),
            ("7790003", "Galletitas Oreo", 980, 50, "Oreo"),
        ]:
            conn.execute(
                "INSERT INTO Productos (uuid_unico, codigo, nombre, precio_venta, stock, marca) "
                "VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), codigo, nombre, precio, stock, marca),
            )
    return tmp_path


@pytest.fixture
def app_cliente(base):
    from fastapi.testclient import TestClient
    from services.api_dueno import create_app
    return TestClient(create_app())


@pytest.fixture
def cliente(app_cliente):
    """Cliente ya logueado como dueño."""
    r = app_cliente.post("/api/auth/login", json={"pin": PIN})
    assert r.status_code == 200, r.text
    app_cliente.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return app_cliente
