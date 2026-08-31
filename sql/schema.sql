-- =====================================================================
-- SISTEMA DUAL DE CAJA Y STOCK - ESQUEMA MAESTRO
-- Compatible con: Maestro (PC fija), USB_Caja, USB_Dueño
-- Motor: SQLite3
-- =====================================================================

PRAGMA journal_mode = WAL;      -- lectura concurrente con escritura en curso
PRAGMA synchronous = FULL;      -- prioridad: cero pérdida de datos ante corte de luz/USB
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Productos
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Productos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid_unico      TEXT UNIQUE NOT NULL,      -- clave estable entre DBs (maestro/USBs)
    codigo          TEXT UNIQUE NOT NULL,      -- código de barras / EAN, clave natural
    nombre          TEXT NOT NULL,
    precio_venta    REAL NOT NULL DEFAULT 0,
    precio_compra   REAL NOT NULL DEFAULT 0,
    stock           INTEGER NOT NULL DEFAULT 0,
    stock_minimo    INTEGER NOT NULL DEFAULT 0,   -- umbral stoploss
    stock_maximo    INTEGER NOT NULL DEFAULT 0,   -- umbral sobre-stock (0 = sin límite)
    proveedor       TEXT,
    marca           TEXT,
    categoria       TEXT,
    activo          INTEGER NOT NULL DEFAULT 1,
    creado_en       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime')),
    actualizado_en  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime')),
    version         INTEGER NOT NULL DEFAULT 1,     -- versionado optimista (evita carreras de escritura)
    origen          TEXT NOT NULL DEFAULT 'MAESTRO' CHECK (origen IN ('MAESTRO','USB_CAJA','USB_DUENO')),
    sincronizado    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_productos_codigo ON Productos(codigo);
CREATE INDEX IF NOT EXISTS idx_productos_nombre ON Productos(nombre);

-- ---------------------------------------------------------------------
-- Usuarios (cajeros / dueño / dev)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT UNIQUE NOT NULL,
    pin_hash    TEXT NOT NULL,
    rol         TEXT NOT NULL CHECK (rol IN ('CAJERO','DUEÑO','DEV')),
    activo      INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------
-- Ventas (cabecera de ticket)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Ventas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid_unico      TEXT UNIQUE NOT NULL,      -- generado en el momento del cobro (UUID4)
    fecha_hora      TEXT NOT NULL,             -- hora ORIGINAL del cobro (no la de importación)
    total           REAL NOT NULL,
    metodo_pago     TEXT NOT NULL CHECK (metodo_pago IN ('EFECTIVO','TARJETA','TRANSFERENCIA','MIXTO')),
    usuario         TEXT NOT NULL,             -- quién cobró
    origen          TEXT NOT NULL DEFAULT 'MAESTRO' CHECK (origen IN ('MAESTRO','USB_CAJA')),
    anulada         INTEGER NOT NULL DEFAULT 0,
    sincronizado    INTEGER NOT NULL DEFAULT 1,
    importado_en    TEXT,  -- NULL si nació en esta DB; fecha real de conciliación si vino de un USB
    facturada           INTEGER NOT NULL DEFAULT 0,   -- 1 = tiene CAE de ARCA asociado
    tipo_comprobante     TEXT,     -- 'B' | 'C', según lo configurado al momento de facturar
    numero_comprobante   INTEGER,  -- número correlativo dentro del punto de venta/tipo
    cae                  TEXT,     -- Código de Autorización Electrónico que devuelve ARCA
    cae_vencimiento       TEXT,    -- 'YYYY-MM-DD', vencimiento del CAE
    arca_error            TEXT     -- motivo si se intentó facturar y ARCA lo rechazó (auditoría)
);
CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON Ventas(fecha_hora);

-- ---------------------------------------------------------------------
-- Detalle_Ventas (líneas de producto por ticket)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Detalle_Ventas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    venta_uuid          TEXT NOT NULL REFERENCES Ventas(uuid_unico) ON DELETE CASCADE,
    producto_codigo     TEXT NOT NULL,   -- clave natural: sobrevive a IDs distintos entre DBs
    producto_nombre     TEXT NOT NULL,   -- snapshot histórico (el nombre pudo cambiar después)
    cantidad            INTEGER NOT NULL,
    precio_unitario     REAL NOT NULL,   -- snapshot histórico del precio al momento de vender
    subtotal            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_detalle_venta_uuid ON Detalle_Ventas(venta_uuid);

-- ---------------------------------------------------------------------
-- Movimientos_Stock (auditoría completa de todo cambio de stock)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Movimientos_Stock (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid_unico          TEXT UNIQUE NOT NULL,
    producto_codigo     TEXT NOT NULL,      -- clave natural (no id, ver sección de conciliación)
    tipo                TEXT NOT NULL CHECK (tipo IN (
                            'ENTRADA_MANUAL','ENTRADA_PDF','ENTRADA_EXCEL',
                            'SALIDA_MANUAL','SALIDA_VENTA','AJUSTE_BULK')),
    cantidad            INTEGER NOT NULL,       -- siempre positivo; el signo lo da 'tipo'
    stock_resultante    INTEGER NOT NULL,       -- stock luego de aplicar el movimiento (auditoría)
    motivo              TEXT,
    ticket_uuid         TEXT,                   -- FK lógica a Ventas.uuid_unico si tipo=SALIDA_VENTA
    usuario             TEXT NOT NULL,
    origen              TEXT NOT NULL DEFAULT 'MAESTRO' CHECK (origen IN ('MAESTRO','USB_CAJA','USB_DUENO')),
    fecha_hora          TEXT NOT NULL,
    sincronizado        INTEGER NOT NULL DEFAULT 1,
    importado_en        TEXT
);
CREATE INDEX IF NOT EXISTS idx_movimientos_producto ON Movimientos_Stock(producto_codigo);
CREATE INDEX IF NOT EXISTS idx_movimientos_fecha ON Movimientos_Stock(fecha_hora);

-- ---------------------------------------------------------------------
-- Configuracion_Alertas (umbrales por producto para el bot de Telegram)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Configuracion_Alertas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_codigo     TEXT UNIQUE,     -- NULL = valores globales por defecto
    stock_minimo        INTEGER NOT NULL DEFAULT 5,
    stock_maximo        INTEGER NOT NULL DEFAULT 0,
    telegram_chat_id    TEXT,
    activo              INTEGER NOT NULL DEFAULT 1,
    ultima_alerta_enviada TEXT     -- evita spamear la misma alerta cada pocos segundos
);

-- ---------------------------------------------------------------------
-- Filtros_Guardados (filtros anidados reutilizables del panel del dueño)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Filtros_Guardados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT UNIQUE NOT NULL,
    definicion_json TEXT NOT NULL,   -- árbol de condiciones AND/OR, ver pos_core/filters.py
    creado_en       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'))
);

-- ---------------------------------------------------------------------
-- Log_Sincronizacion (trazabilidad de cada conciliación aplicada)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Log_Sincronizacion (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_hora          TEXT NOT NULL,
    origen_usb          TEXT NOT NULL,       -- 'USB_CAJA' | 'USB_DUENO'
    ventas_importadas   INTEGER NOT NULL DEFAULT 0,
    ventas_omitidas     INTEGER NOT NULL DEFAULT 0,
    movimientos_importados INTEGER NOT NULL DEFAULT 0,
    precios_actualizados INTEGER NOT NULL DEFAULT 0,
    detalle_json        TEXT
);

-- ---------------------------------------------------------------------
-- Lineas_Eliminadas (auditoría anti-robo: cada "Quitar línea" en la Caja)
-- Solo visible desde el Panel del Dueño, nunca desde la Caja.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Lineas_Eliminadas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid_unico          TEXT UNIQUE NOT NULL,
    producto_codigo     TEXT NOT NULL,
    producto_nombre     TEXT NOT NULL,
    cantidad            INTEGER NOT NULL,
    precio_unitario     REAL NOT NULL,
    subtotal            REAL NOT NULL,
    usuario             TEXT NOT NULL,
    origen              TEXT NOT NULL DEFAULT 'MAESTRO' CHECK (origen IN ('MAESTRO','USB_CAJA')),
    fecha_hora          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lineas_eliminadas_fecha ON Lineas_Eliminadas(fecha_hora);

-- ---------------------------------------------------------------------
-- Ofertas (promociones/rebajas temporales con vencimiento automático)
-- El precio "normal" (Productos.precio_venta) NUNCA se toca: el
-- descuento se calcula al vuelo mientras la oferta está vigente, así que
-- "volver a la normalidad" al vencer no requiere ninguna tarea de fondo.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Ofertas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid_unico          TEXT UNIQUE NOT NULL,
    producto_codigo     TEXT NOT NULL,
    tipo_descuento      TEXT NOT NULL CHECK (tipo_descuento IN ('PORCENTAJE','MONTO_FIJO','PRECIO_FIJO')),
    valor               REAL NOT NULL,
    descripcion         TEXT,
    fecha_inicio        TEXT NOT NULL,   -- 'YYYY-MM-DD'
    fecha_fin           TEXT NOT NULL,   -- 'YYYY-MM-DD', fecha_inicio + dias
    activa              INTEGER NOT NULL DEFAULT 1,   -- permite cancelarla antes de tiempo a mano
    creado_por          TEXT NOT NULL,
    creado_en           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ofertas_producto ON Ofertas(producto_codigo);
