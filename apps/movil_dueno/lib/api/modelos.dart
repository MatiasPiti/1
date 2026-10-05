double _d(dynamic v) => v == null ? 0 : (v as num).toDouble();
int _i(dynamic v) => v == null ? 0 : (v as num).toInt();

class Salud {
  Salud({required this.nombreLocal, required this.versionApi});
  factory Salud.fromJson(Map<String, dynamic> j) =>
      Salud(nombreLocal: j['nombre_local'] as String? ?? 'Mi Negocio', versionApi: j['version_api'] as String? ?? '');
  final String nombreLocal;
  final String versionApi;
}

class Sesion {
  Sesion({required this.token, required this.usuario, required this.expira});
  factory Sesion.fromJson(Map<String, dynamic> j) => Sesion(
      token: j['token'] as String,
      usuario: j['usuario'] as String,
      expira: DateTime.fromMillisecondsSinceEpoch(_i(j['expira']) * 1000));
  final String token;
  final String usuario;
  final DateTime expira;
}

enum EstadoStock { ok, bajo, sobre }

class Producto {
  Producto({
    required this.codigo,
    required this.nombre,
    required this.precioVenta,
    required this.precioCompra,
    required this.stock,
    required this.stockMinimo,
    required this.stockMaximo,
    this.marca,
    this.proveedor,
    this.categoria,
  });

  factory Producto.fromJson(Map<String, dynamic> j) => Producto(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String,
        precioVenta: _d(j['precio_venta']),
        precioCompra: _d(j['precio_compra']),
        stock: _i(j['stock']),
        stockMinimo: _i(j['stock_minimo']),
        stockMaximo: _i(j['stock_maximo']),
        marca: j['marca'] as String?,
        proveedor: j['proveedor'] as String?,
        categoria: j['categoria'] as String?,
      );

  final String codigo;
  final String nombre;
  final double precioVenta;
  final double precioCompra;
  final int stock;
  final int stockMinimo; // 0 = sin mínimo
  final int stockMaximo; // 0 = sin máximo
  final String? marca;
  final String? proveedor;
  final String? categoria;

  /// Mismo criterio que el bot de Telegram y pos_core/alertas.py.
  EstadoStock get estado {
    if (stockMinimo > 0 && stock <= stockMinimo) return EstadoStock.bajo;
    if (stockMaximo > 0 && stock >= stockMaximo) return EstadoStock.sobre;
    return EstadoStock.ok;
  }

  /// Margen sobre el costo, o null si no hay precio de compra cargado.
  double? get margen => precioCompra > 0 ? (precioVenta / precioCompra - 1) * 100 : null;

  Producto conStock(int nuevo) => Producto(
      codigo: codigo, nombre: nombre, precioVenta: precioVenta, precioCompra: precioCompra,
      stock: nuevo, stockMinimo: stockMinimo, stockMaximo: stockMaximo,
      marca: marca, proveedor: proveedor, categoria: categoria);

  Producto conPrecio(double nuevo) => Producto(
      codigo: codigo, nombre: nombre, precioVenta: nuevo, precioCompra: precioCompra,
      stock: stock, stockMinimo: stockMinimo, stockMaximo: stockMaximo,
      marca: marca, proveedor: proveedor, categoria: categoria);
}

class VentaDia {
  VentaDia(this.dia, this.total, this.tickets);
  final String dia;
  final double total;
  final int tickets;
}

class TopProducto {
  TopProducto(this.codigo, this.nombre, this.cantidad, this.importe);
  final String codigo;
  final String nombre;
  final int cantidad;
  final double importe;
}

class MetodoPago {
  MetodoPago(this.metodo, this.tickets, this.total);
  final String metodo;
  final int tickets;
  final double total;
}

class Dashboard {
  Dashboard({
    required this.totalHoy,
    required this.ticketsHoy,
    required this.ticketPromedio,
    required this.porMetodo,
    required this.ultimos7Dias,
    required this.topProductos,
    required this.alertasActivas,
  });

  factory Dashboard.fromJson(Map<String, dynamic> j) {
    final hoy = j['hoy'] as Map<String, dynamic>;
    return Dashboard(
      totalHoy: _d(hoy['total']),
      ticketsHoy: _i(hoy['tickets']),
      ticketPromedio: _d(hoy['ticket_promedio']),
      porMetodo: [
        for (final m in hoy['por_metodo'] as List)
          MetodoPago(m['metodo_pago'] as String, _i(m['tickets']), _d(m['total'])),
      ],
      ultimos7Dias: [
        for (final d in j['ultimos_7_dias'] as List) VentaDia(d['dia'] as String, _d(d['total']), _i(d['tickets'])),
      ],
      topProductos: [
        for (final t in j['top_productos'] as List)
          TopProducto(t['codigo'] as String, t['nombre'] as String? ?? t['codigo'] as String,
              _i(t['cantidad']), _d(t['importe'])),
      ],
      alertasActivas: _i(j['alertas_activas']),
    );
  }

  final double totalHoy;
  final int ticketsHoy;
  final double ticketPromedio;
  final List<MetodoPago> porMetodo;
  final List<VentaDia> ultimos7Dias;
  final List<TopProducto> topProductos;
  final int alertasActivas;
}

class Movimiento {
  Movimiento({
    required this.fechaHora,
    required this.codigo,
    required this.nombre,
    required this.tipo,
    required this.cantidad,
    required this.stockResultante,
    required this.usuario,
  });

  factory Movimiento.fromJson(Map<String, dynamic> j) => Movimiento(
        fechaHora: j['fecha_hora'] as String,
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String? ?? j['codigo'] as String,
        tipo: j['tipo'] as String,
        cantidad: _i(j['cantidad']),
        stockResultante: _i(j['stock_resultante']),
        usuario: j['usuario'] as String? ?? '',
      );

  final String fechaHora;
  final String codigo;
  final String nombre;
  final String tipo;
  final int cantidad;
  final int stockResultante;
  final String usuario;

  bool get esEntrada => tipo.startsWith('ENTRADA');

  String get tipoLegible => const {
        'ENTRADA_MANUAL': 'Entrada manual',
        'ENTRADA_PDF': 'Factura PDF',
        'ENTRADA_EXCEL': 'Carga Excel',
        'SALIDA_MANUAL': 'Salida manual',
        'SALIDA_VENTA': 'Venta',
        'AJUSTE_BULK': 'Ajuste',
      }[tipo] ??
      tipo;
}

class ResultadoStock {
  ResultadoStock({required this.codigo, required this.nombre, required this.stockNuevo});
  factory ResultadoStock.fromJson(Map<String, dynamic> j) =>
      ResultadoStock(codigo: j['codigo'] as String, nombre: j['nombre'] as String? ?? '', stockNuevo: _i(j['stock_nuevo']));
  final String codigo;
  final String nombre;
  final int stockNuevo;
}

class CambioPrecio {
  CambioPrecio({required this.codigo, required this.ok, this.nombre, this.anterior, this.nuevo, this.error});
  factory CambioPrecio.fromJson(Map<String, dynamic> j) => CambioPrecio(
        codigo: j['codigo'] as String,
        ok: j['ok'] as bool? ?? true,
        nombre: j['nombre'] as String?,
        anterior: j['precio_anterior'] == null ? null : _d(j['precio_anterior']),
        nuevo: j['precio_nuevo'] == null ? null : _d(j['precio_nuevo']),
        error: j['error'] as String?,
      );
  final String codigo;
  final bool ok;
  final String? nombre;
  final double? anterior;
  final double? nuevo;
  final String? error;
}

class Alerta {
  Alerta({required this.codigo, required this.nombre, required this.stock, required this.stockMinimo,
      required this.stockMaximo, required this.tipo});
  factory Alerta.fromJson(Map<String, dynamic> j) => Alerta(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String,
        stock: _i(j['stock']),
        stockMinimo: _i(j['stock_minimo']),
        stockMaximo: _i(j['stock_maximo']),
        tipo: j['tipo'] as String,
      );
  final String codigo;
  final String nombre;
  final int stock;
  final int stockMinimo;
  final int stockMaximo;
  final String tipo; // BAJO | SOBRE
  bool get esBajo => tipo == 'BAJO';
}

class ConfigAlertas {
  ConfigAlertas({
    required this.telegramHabilitado,
    required this.chatId,
    required this.tokenConfigurado,
    required this.tokenMascara,
    required this.stockMinimo,
    required this.stockMaximo,
  });
  factory ConfigAlertas.fromJson(Map<String, dynamic> j) {
    final t = j['telegram'] as Map<String, dynamic>;
    final u = j['umbral_global'] as Map<String, dynamic>;
    return ConfigAlertas(
      telegramHabilitado: t['habilitado'] as bool? ?? false,
      chatId: t['chat_id_default'] as String? ?? '',
      tokenConfigurado: t['token_configurado'] as bool? ?? false,
      tokenMascara: t['token_mascara'] as String? ?? '',
      stockMinimo: _i(u['stock_minimo']),
      stockMaximo: _i(u['stock_maximo']),
    );
  }
  final bool telegramHabilitado;
  final String chatId;
  final bool tokenConfigurado;
  final String tokenMascara;
  final int stockMinimo;
  final int stockMaximo;
}

/// Ítem de factura analizada; mutable porque el dueño lo corrige en pantalla.
class ItemFactura {
  ItemFactura({
    required this.codigo,
    required this.nombre,
    required this.cantidad,
    required this.precioCompra,
    required this.existe,
    this.nombreSistema,
    this.stockActual,
    this.posibleDuplicado = false,
  }) : seleccionado = existe && !posibleDuplicado && cantidadEsValida(cantidad);

  /// Lo que acepta la PC por renglón (ItemFacturaIn en services/api_dueno.py):
  /// un renglón fuera de rango hace rechazar (422) la factura ENTERA.
  static const cantidadMaxima = 100000;
  static bool cantidadEsValida(int cantidad) => cantidad >= 1 && cantidad <= cantidadMaxima;

  factory ItemFactura.fromJson(Map<String, dynamic> j) => ItemFactura(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String? ?? '',
        cantidad: _i(j['cantidad']),
        precioCompra: j['precio_compra'] == null ? null : _d(j['precio_compra']),
        existe: j['existe'] as bool? ?? false,
        nombreSistema: j['nombre_sistema'] as String?,
        stockActual: j['stock_actual'] == null ? null : _i(j['stock_actual']),
        posibleDuplicado: j['posible_duplicado'] as bool? ?? false,
      );

  String codigo;
  final String nombre;
  int cantidad;
  double? precioCompra;
  bool existe;
  String? nombreSistema;
  int? stockActual;
  final bool posibleDuplicado;
  bool seleccionado;

  bool get cantidadValida => cantidadEsValida(cantidad);

  Map<String, dynamic> toJson() =>
      {'codigo': codigo, 'cantidad': cantidad, if (precioCompra != null) 'precio_compra': precioCompra};
}

class FacturaAnalizada {
  FacturaAnalizada({required this.nombre, required this.escaneada, required this.items, required this.noReconocidas});
  factory FacturaAnalizada.fromJson(Map<String, dynamic> j) => FacturaAnalizada(
        nombre: j['factura_nombre'] as String,
        escaneada: j['es_pdf_escaneado'] as bool? ?? false,
        items: [for (final i in j['items'] as List) ItemFactura.fromJson(i as Map<String, dynamic>)],
        noReconocidas: [for (final l in j['lineas_no_reconocidas'] as List) l as String],
      );
  final String nombre;
  final bool escaneada;
  final List<ItemFactura> items;
  final List<String> noReconocidas;
}

class ResultadoFactura {
  ResultadoFactura({required this.codigo, required this.ok, this.stockNuevo, this.error});
  factory ResultadoFactura.fromJson(Map<String, dynamic> j) => ResultadoFactura(
        codigo: j['codigo'] as String,
        ok: j['ok'] as bool,
        stockNuevo: j['stock_nuevo'] == null ? null : _i(j['stock_nuevo']),
        error: j['error'] as String?,
      );
  final String codigo;
  final bool ok;
  final int? stockNuevo;
  final String? error;
}
