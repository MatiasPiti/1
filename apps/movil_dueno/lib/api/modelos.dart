// Modelos del contrato 2 de ApiCelular (services/api_celular.py). La forma
// exacta de cada respuesta está en test/fixtures/contrato_api_celular_v2.json,
// que escribe el backend y que test/contrato_test.dart parsea entero con estos
// fromJson: cambiar un campo acá sin cambiar el fixture (o al revés) rompe esa
// prueba, que es lo que se quiere.

double _d(dynamic v) => v == null ? 0 : (v as num).toDouble();
double? _dn(dynamic v) => v == null ? null : (v as num).toDouble();
int _i(dynamic v) => v == null ? 0 : (v as num).toInt();
int? _in(dynamic v) => v == null ? null : (v as num).toInt();

/// Respuesta de GET /api/salud (sin token). `servicio` es la firma que separa
/// a la API del celular de cualquier otro programa que conteste en ese puerto.
class Salud {
  Salud({
    required this.servicio,
    required this.contrato,
    required this.versionApi,
    required this.compilado,
    required this.nombreLocal,
    required this.pinConfigurado,
    required this.loginDisponible,
    required this.baseOk,
    required this.baseDetalle,
    this.motivo,
  });

  factory Salud.fromJson(Map<String, dynamic> j) {
    final base = j['base'];
    return Salud(
      servicio: j['servicio'] as String? ?? '',
      contrato: _i(j['contrato']),
      versionApi: j['version_api'] as String? ?? '',
      compilado: j['compilado'] as String? ?? '',
      // "" = la API no encontró [general] nombre_local en el config.ini real:
      // puede estar instalada en otra carpeta. No se inventa un nombre.
      nombreLocal: j['nombre_local'] as String? ?? '',
      pinConfigurado: j['pin_configurado'] as bool? ?? false,
      loginDisponible: j['login_disponible'] as bool? ?? false,
      motivo: j['motivo'] as String?,
      baseOk: base is Map ? base['ok'] as bool? ?? false : false,
      baseDetalle: base is Map ? base['detalle'] as String? ?? '' : '',
    );
  }

  final String servicio;
  final int contrato;
  final String versionApi;

  /// Commit con el que se compiló ApiCelular.exe ("desconocido" / "desarrollo").
  final String compilado;
  final String nombreLocal;
  final bool pinConfigurado;
  final bool loginDisponible;

  /// null | pin_no_definido | secreto_ilegible | base_no_disponible | base_desactualizada
  final String? motivo;
  final bool baseOk;
  final String baseDetalle;
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

/// Oferta vigente de un producto: es lo que hace que la Caja cobre otro
/// precio que el de lista.
class Oferta {
  Oferta({required this.id, required this.tipoDescuento, required this.valor, required this.descripcion,
      required this.fechaFin});

  factory Oferta.fromJson(Map<String, dynamic> j) => Oferta(
        id: _i(j['id']),
        tipoDescuento: j['tipo_descuento'] as String? ?? '',
        valor: _d(j['valor']),
        descripcion: j['descripcion'] as String? ?? '',
        fechaFin: j['fecha_fin'] as String? ?? '',
      );

  final int id;

  /// PORCENTAJE | PRECIO_FIJO
  final String tipoDescuento;
  final double valor;
  final String descripcion;

  /// "YYYY-MM-DD"
  final String fechaFin;

  bool get esPrecioFijo => tipoDescuento == 'PRECIO_FIJO';

  /// "09/10": hasta qué día dura, para los carteles.
  String get hasta {
    final f = DateTime.tryParse(fechaFin);
    if (f == null) return fechaFin;
    return '${f.day.toString().padLeft(2, '0')}/${f.month.toString().padLeft(2, '0')}';
  }
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
    double? precioEfectivo,
    this.costoSinIva = 0,
    this.margenGanancia = 0,
    this.umbralPropio = false,
    this.oferta,
    this.marca,
    this.proveedor,
    this.categoria,
    this.subrubro,
    this.actualizadoEn = '',
  }) : precioEfectivo = precioEfectivo ?? precioVenta;

  factory Producto.fromJson(Map<String, dynamic> j) => Producto(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String,
        precioVenta: _d(j['precio_venta']),
        precioEfectivo: _dn(j['precio_efectivo']),
        precioCompra: _d(j['precio_compra']),
        costoSinIva: _d(j['costo_sin_iva']),
        margenGanancia: _d(j['margen_ganancia']),
        stock: _i(j['stock']),
        stockMinimo: _i(j['stock_minimo']),
        stockMaximo: _i(j['stock_maximo']),
        umbralPropio: j['umbral_propio'] as bool? ?? false,
        oferta: j['oferta'] == null ? null : Oferta.fromJson(j['oferta'] as Map<String, dynamic>),
        marca: j['marca'] as String?,
        proveedor: j['proveedor'] as String?,
        categoria: j['categoria'] as String?,
        subrubro: j['subrubro'] as String?,
        actualizadoEn: j['actualizado_en'] as String? ?? '',
      );

  final String codigo;
  final String nombre;

  /// Precio de lista.
  final double precioVenta;

  /// Lo que cobra HOY la Caja (con la oferta vigente, si hay).
  final double precioEfectivo;

  /// Los tres crudos de la base: 0 = no cargado.
  final double precioCompra;
  final double costoSinIva;
  final double margenGanancia;

  /// Umbral EFECTIVO: el propio del producto o, si no tiene, el global (el
  /// mismo que usa el bot de Telegram).
  final int stock;
  final int stockMinimo; // 0 = sin mínimo
  final int stockMaximo; // 0 = sin máximo
  final bool umbralPropio;
  final Oferta? oferta;
  final String? marca;
  final String? proveedor;
  final String? categoria;
  final String? subrubro;
  final String actualizadoEn;

  bool get enOferta => oferta != null;

  /// Mismo criterio que el bot de Telegram y pos_core/alerts.py.
  EstadoStock get estado {
    if (stockMinimo > 0 && stock <= stockMinimo) return EstadoStock.bajo;
    if (stockMaximo > 0 && stock >= stockMaximo) return EstadoStock.sobre;
    return EstadoStock.ok;
  }

  /// El % de ganancia guardado (el que muestra el Panel); si no hay, el que
  /// sale del precio de costo; null si tampoco hay costo cargado.
  double? get margen {
    if (margenGanancia > 0) return margenGanancia;
    return precioCompra > 0 ? (precioVenta / precioCompra - 1) * 100 : null;
  }

  Producto _copiar({
    String? nombre,
    double? precioVenta,
    double? precioEfectivo,
    double? precioCompra,
    double? costoSinIva,
    double? margenGanancia,
    int? stock,
    int? stockMinimo,
    int? stockMaximo,
    Oferta? Function()? oferta,
  }) =>
      Producto(
        codigo: codigo,
        nombre: nombre ?? this.nombre,
        precioVenta: precioVenta ?? this.precioVenta,
        precioEfectivo: precioEfectivo ?? this.precioEfectivo,
        precioCompra: precioCompra ?? this.precioCompra,
        costoSinIva: costoSinIva ?? this.costoSinIva,
        margenGanancia: margenGanancia ?? this.margenGanancia,
        stock: stock ?? this.stock,
        stockMinimo: stockMinimo ?? this.stockMinimo,
        stockMaximo: stockMaximo ?? this.stockMaximo,
        umbralPropio: umbralPropio,
        oferta: oferta == null ? this.oferta : oferta(),
        marca: marca,
        proveedor: proveedor,
        categoria: categoria,
        subrubro: subrubro,
        actualizadoEn: actualizadoEn,
      );

  Producto conStock(int nuevo, {int? stockMinimo, int? stockMaximo}) =>
      _copiar(stock: nuevo, stockMinimo: stockMinimo, stockMaximo: stockMaximo);

  /// Con los precios que acaba de devolver GET/PUT /api/precios/{codigo}.
  Producto conPrecios(PreciosProducto p) => _copiar(
        nombre: p.nombre,
        stock: p.stock,
        precioVenta: p.precioVenta,
        precioEfectivo: p.precioEfectivo,
        precioCompra: p.crudos.precioCompra,
        costoSinIva: p.crudos.costoSinIva,
        margenGanancia: p.crudos.margenGanancia,
        oferta: () => p.oferta,
      );
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
    this.telegramHabilitado = true,
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
      telegramHabilitado: j['telegram_habilitado'] as bool? ?? true,
    );
  }

  final double totalHoy;
  final int ticketsHoy;
  final double ticketPromedio;
  final List<MetodoPago> porMetodo;
  final List<VentaDia> ultimos7Dias;
  final List<TopProducto> topProductos;
  final int alertasActivas;

  /// Con el bot apagado las alertas se calculan igual pero no se mandan.
  final bool telegramHabilitado;
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
    this.motivo,
  });

  factory Movimiento.fromJson(Map<String, dynamic> j) => Movimiento(
        fechaHora: j['fecha_hora'] as String,
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String? ?? j['codigo'] as String,
        tipo: j['tipo'] as String,
        cantidad: _i(j['cantidad']),
        stockResultante: _i(j['stock_resultante']),
        motivo: j['motivo'] as String?,
        usuario: j['usuario'] as String? ?? '',
      );

  final String fechaHora;
  final String codigo;
  final String nombre;
  final String tipo;
  final int cantidad;
  final int stockResultante;
  final String? motivo;
  final String usuario;

  bool get esEntrada => tipo.startsWith('ENTRADA');

  /// Lo que se hizo desde la app queda con usuario "dueño (celular)".
  bool get desdeElCelular => usuario.contains('(celular)');

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

/// Ventas de este producto que el StockService todavía va a descontar (sus
/// líneas quedaron sin SALIDA_VENTA, típicamente porque el stock estaba en 0).
class VentasPendientes {
  const VentasPendientes({this.lineas = 0, this.unidades = 0});
  factory VentasPendientes.fromJson(Map<String, dynamic>? j) =>
      j == null ? const VentasPendientes() : VentasPendientes(lineas: _i(j['lineas']), unidades: _i(j['unidades']));
  final int lineas;
  final int unidades;
}

class ResultadoStock {
  ResultadoStock({
    required this.codigo,
    required this.nombre,
    required this.stockNuevo,
    this.stockMinimo = 0,
    this.stockMaximo = 0,
    this.ventasPendientes = const VentasPendientes(),
  });

  factory ResultadoStock.fromJson(Map<String, dynamic> j) => ResultadoStock(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String? ?? '',
        stockNuevo: _i(j['stock_nuevo']),
        stockMinimo: _i(j['stock_minimo']),
        stockMaximo: _i(j['stock_maximo']),
        ventasPendientes: VentasPendientes.fromJson(j['ventas_pendientes'] as Map<String, dynamic>?),
      );

  final String codigo;
  final String nombre;
  final int stockNuevo;
  final int stockMinimo;
  final int stockMaximo;
  final VentasPendientes ventasPendientes;

  /// Cómo va a quedar el stock cuando el StockService descuente lo pendiente
  /// (nunca menos de 0: una línea que no entra queda pendiente, no negativa).
  int get stockDespuesDePendientes {
    final s = stockNuevo - ventasPendientes.unidades;
    return s < 0 ? 0 : s;
  }
}

/// Una fila de la vista previa o del resultado del ajuste masivo.
class CambioPrecio {
  CambioPrecio({
    required this.codigo,
    required this.ok,
    this.nombre,
    this.anterior,
    this.nuevo,
    this.error,
    this.quedaEnCero = false,
    this.enOferta = false,
  });

  factory CambioPrecio.fromJson(Map<String, dynamic> j) => CambioPrecio(
        codigo: j['codigo'] as String,
        ok: j['ok'] as bool? ?? true,
        nombre: j['nombre'] as String?,
        anterior: _dn(j['precio_anterior']),
        nuevo: _dn(j['precio_nuevo']),
        error: j['error'] as String?,
        quedaEnCero: j['queda_en_cero'] as bool? ?? false,
        enOferta: j['en_oferta'] as bool? ?? false,
      );

  final String codigo;
  final bool ok;
  final String? nombre;
  final double? anterior;
  final double? nuevo;
  final String? error;

  /// Tenía precio mayor que 0 y quedaría en $ 0 (uno que ya estaba en 0, no).
  final bool quedaEnCero;
  final bool enOferta;

  /// El servidor no lo tocó porque su precio ya no era el de la vista previa.
  bool get precioCambioMientrasTanto => !ok && (error ?? '').contains('cambió mientras tanto');
}

/// Los 4 números de la cadena de precios TAL CUAL están en la base (0 = no
/// cargado, nunca null). Se devuelven sin tocar como "esperado" al guardar:
/// si alguno cambió mientras la hoja estaba abierta, la PC contesta 409.
class CrudosPrecio {
  const CrudosPrecio({
    required this.costoSinIva,
    required this.precioCompra,
    required this.margenGanancia,
    required this.precioVenta,
  });

  factory CrudosPrecio.fromJson(Map<String, dynamic> j) => CrudosPrecio(
        costoSinIva: _d(j['costo_sin_iva']),
        precioCompra: _d(j['precio_compra']),
        margenGanancia: _d(j['margen_ganancia']),
        precioVenta: _d(j['precio_venta']),
      );

  final double costoSinIva;
  final double precioCompra;
  final double margenGanancia;
  final double precioVenta;

  Map<String, dynamic> toJson() => {
        'costo_sin_iva': costoSinIva,
        'precio_compra': precioCompra,
        'margen_ganancia': margenGanancia,
        'precio_venta': precioVenta,
      };
}

/// Los 4 campos de la pantalla de precios (null = vacío). Es lo que devuelve
/// POST /api/precios/recalcular y lo que se manda en PUT /api/precios/{codigo}
/// (ahí null = no tocar, como el Panel).
class ValoresPrecio {
  const ValoresPrecio({this.costoSinIva, this.precioCosto, this.margen, this.precioFinal});

  factory ValoresPrecio.fromJson(Map<String, dynamic> j) => ValoresPrecio(
        costoSinIva: _dn(j['costo_sin_iva']),
        precioCosto: _dn(j['precio_costo']),
        margen: _dn(j['margen']),
        precioFinal: _dn(j['precio_final']),
      );

  /// En el orden de la cadena: Costo s/IVA → Precio costo → % Ganancia → Precio final.
  static const campos = ['costo_sin_iva', 'precio_costo', 'margen', 'precio_final'];

  final double? costoSinIva;
  final double? precioCosto;
  final double? margen;
  final double? precioFinal;

  double? valor(String campo) => switch (campo) {
        'costo_sin_iva' => costoSinIva,
        'precio_costo' => precioCosto,
        'margen' => margen,
        'precio_final' => precioFinal,
        _ => throw ArgumentError.value(campo, 'campo'),
      };

  ValoresPrecio con(String campo, double? v) => ValoresPrecio(
        costoSinIva: campo == 'costo_sin_iva' ? v : costoSinIva,
        precioCosto: campo == 'precio_costo' ? v : precioCosto,
        margen: campo == 'margen' ? v : margen,
        precioFinal: campo == 'precio_final' ? v : precioFinal,
      );

  Map<String, dynamic> toJson() => {
        'costo_sin_iva': costoSinIva,
        'precio_costo': precioCosto,
        'margen': margen,
        'precio_final': precioFinal,
      };
}

/// GET /api/precios/{codigo}: lo que muestra la hoja de precios.
class PreciosProducto {
  PreciosProducto({
    required this.codigo,
    required this.nombre,
    required this.stock,
    required this.valores,
    required this.precioVenta,
    required this.crudos,
    required this.precioEfectivo,
    required this.actualizadoEn,
    this.categoria,
    this.subrubro,
    this.oferta,
  });

  factory PreciosProducto.fromJson(Map<String, dynamic> j) => PreciosProducto(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String,
        categoria: j['categoria'] as String?,
        subrubro: j['subrubro'] as String?,
        stock: _i(j['stock']),
        valores: ValoresPrecio.fromJson(j),
        precioVenta: _d(j['precio_venta']),
        crudos: CrudosPrecio.fromJson(j['crudos'] as Map<String, dynamic>),
        precioEfectivo: _d(j['precio_efectivo']),
        oferta: j['oferta'] == null ? null : Oferta.fromJson(j['oferta'] as Map<String, dynamic>),
        actualizadoEn: j['actualizado_en'] as String? ?? '',
      );

  final String codigo;
  final String nombre;
  final String? categoria;
  final String? subrubro;
  final int stock;

  /// Para mostrar (los ceros guardados llegan como null, convención del Panel).
  final ValoresPrecio valores;
  final double precioVenta;
  final CrudosPrecio crudos;
  final double precioEfectivo;
  final Oferta? oferta;
  final String actualizadoEn;
}

/// PUT /api/precios/{codigo}.
class CambioPrecios {
  CambioPrecios({required this.antes, required this.despues});
  factory CambioPrecios.fromJson(Map<String, dynamic> j) => CambioPrecios(
        antes: PreciosProducto.fromJson(j['antes'] as Map<String, dynamic>),
        despues: PreciosProducto.fromJson(j['despues'] as Map<String, dynamic>),
      );
  final PreciosProducto antes;
  final PreciosProducto despues;
}

class Alerta {
  Alerta({
    required this.codigo,
    required this.nombre,
    required this.stock,
    required this.stockMinimo,
    required this.stockMaximo,
    required this.tipo,
    this.umbralPropio = false,
    this.ultimaAlerta,
    this.proximoAviso,
  });

  factory Alerta.fromJson(Map<String, dynamic> j) => Alerta(
        codigo: j['codigo'] as String,
        nombre: j['nombre'] as String,
        stock: _i(j['stock']),
        stockMinimo: _i(j['stock_minimo']),
        stockMaximo: _i(j['stock_maximo']),
        tipo: j['tipo'] as String,
        umbralPropio: j['umbral_propio'] as bool? ?? false,
        ultimaAlerta: j['ultima_alerta_enviada'] as String?,
        proximoAviso: j['proximo_aviso_desde'] as String?,
      );

  final String codigo;
  final String nombre;
  final int stock;
  final int stockMinimo;
  final int stockMaximo;
  final String tipo; // BAJO | SOBRE
  final bool umbralPropio;

  /// Cuándo mandó el bot el último aviso de este producto (null = nunca).
  final String? ultimaAlerta;

  /// Desde cuándo puede volver a avisar (cooldown de 4 h); null = ya puede.
  final String? proximoAviso;

  bool get esBajo => tipo == 'BAJO';
}

/// Umbral global y cuántos productos tienen umbral propio.
class EstadoUmbrales {
  EstadoUmbrales({
    required this.stockMinimo,
    required this.stockMaximo,
    required this.existe,
    required this.umbralesPropios,
    required this.umbralesInactivos,
    this.borradas,
  });

  factory EstadoUmbrales.fromJson(Map<String, dynamic> j) {
    final u = j['umbral_global'] as Map<String, dynamic>;
    final p = j['umbrales_propios'] as Map<String, dynamic>? ?? const {};
    return EstadoUmbrales(
      stockMinimo: _i(u['stock_minimo']),
      stockMaximo: _i(u['stock_maximo']),
      existe: u['existe'] as bool? ?? true,
      umbralesPropios: _i(p['total']),
      umbralesInactivos: _i(p['inactivos']),
      borradas: _in(j['borradas']),
    );
  }

  final int stockMinimo;
  final int stockMaximo;
  final bool existe;
  final int umbralesPropios;
  final int umbralesInactivos;

  /// Solo en DELETE /api/config/umbral-global.
  final int? borradas;
}

class ConfigAlertas {
  ConfigAlertas({
    required this.telegramHabilitado,
    required this.chatId,
    required this.tokenConfigurado,
    required this.tokenMascara,
    required this.umbrales,
  });

  factory ConfigAlertas.fromJson(Map<String, dynamic> j) {
    final t = j['telegram'] as Map<String, dynamic>;
    return ConfigAlertas(
      telegramHabilitado: t['habilitado'] as bool? ?? false,
      chatId: t['chat_id_default'] as String? ?? '',
      tokenConfigurado: t['token_configurado'] as bool? ?? false,
      // Viene tapado entero ("••••••••"): el token real no sale nunca de la PC.
      tokenMascara: t['token_mascara'] as String? ?? '',
      umbrales: EstadoUmbrales.fromJson(j),
    );
  }

  final bool telegramHabilitado;
  final String chatId;
  final bool tokenConfigurado;
  final String tokenMascara;
  final EstadoUmbrales umbrales;

  int get stockMinimo => umbrales.stockMinimo;
  int get stockMaximo => umbrales.stockMaximo;
  bool get umbralGlobalExiste => umbrales.existe;
  int get umbralesPropios => umbrales.umbralesPropios;
  int get umbralesInactivos => umbrales.umbralesInactivos;

  ConfigAlertas conUmbrales(EstadoUmbrales u) => ConfigAlertas(
      telegramHabilitado: telegramHabilitado,
      chatId: chatId,
      tokenConfigurado: tokenConfigurado,
      tokenMascara: tokenMascara,
      umbrales: u);
}

/// POST /api/config/telegram/probar.
class ResultadoPrueba {
  ResultadoPrueba({required this.ok, required this.enviado, required this.detalle});
  factory ResultadoPrueba.fromJson(Map<String, dynamic> j) => ResultadoPrueba(
      ok: j['ok'] as bool? ?? false, enviado: j['enviado'] as bool? ?? false, detalle: j['detalle'] as String? ?? '');
  final bool ok;
  final bool enviado;
  final String detalle;
}

class Candidato {
  Candidato({required this.codigo, required this.nombre, required this.score});
  factory Candidato.fromJson(Map<String, dynamic> j) =>
      Candidato(codigo: j['codigo'] as String, nombre: j['nombre'] as String? ?? '', score: _d(j['score']));
  final String codigo;
  final String nombre;

  /// 0 a 1.
  final double score;
}

/// Productos del sistema parecidos al renglón de la factura (sin código, o
/// con un código que no existe). NUNCA se aplica solo: elegir uno es la
/// confirmación humana (emparejar mal le suma el stock a otro producto).
class Emparejamiento {
  Emparejamiento({required this.motivo, required this.confianza, required this.candidatos, this.sugerido});
  factory Emparejamiento.fromJson(Map<String, dynamic> j) => Emparejamiento(
        motivo: j['motivo'] as String? ?? '',
        confianza: j['confianza'] as String? ?? 'NINGUNA',
        candidatos: [for (final c in j['candidatos'] as List? ?? const []) Candidato.fromJson(c as Map<String, dynamic>)],
        sugerido: j['sugerido'] == null ? null : Candidato.fromJson(j['sugerido'] as Map<String, dynamic>),
      );

  /// sin_codigo | codigo_desconocido | demasiados
  final String motivo;

  /// SEGURA | POSIBLE | NINGUNA
  final String confianza;
  final List<Candidato> candidatos;
  final Candidato? sugerido;

  /// Quedó afuera del tope de la búsqueda por nombre: se carga a mano.
  bool get demasiados => motivo == 'demasiados';

  /// El sugerido primero y después el resto, sin repetir.
  List<Candidato> get candidatosOrdenados => [
        ?sugerido,
        for (final c in candidatos)
          if (c.codigo != sugerido?.codigo) c,
      ];
}

/// Ítem de factura analizada; mutable porque el dueño lo corrige en pantalla.
class ItemFactura {
  ItemFactura({
    required this.codigo,
    required this.nombre,
    required this.cantidad,
    required this.precioCompra,
    required this.existe,
    this.indice = 0,
    this.nombreSistema,
    this.stockActual,
    this.precioCompraActual,
    this.posibleDuplicado = false,
    this.precioSospechoso = false,
    this.emparejamiento,
    bool? actualizarCosto,
  })  : seleccionado = existe && !posibleDuplicado && cantidadEsValida(cantidad),
        actualizarCosto = actualizarCosto ?? !precioSospechoso;

  /// Lo que acepta la PC por renglón (ItemFacturaIn en services/api_celular.py):
  /// un renglón fuera de rango hace rechazar (422) la factura ENTERA.
  static const cantidadMaxima = 100000;
  static bool cantidadEsValida(int cantidad) => cantidad >= 1 && cantidad <= cantidadMaxima;

  /// Misma regla que panel_celular.es_precio_sospechoso: cubre el "3.500"
  /// que el lector de PDF a veces entiende como 3,5.
  static bool esPrecioSospechoso(double? nuevo, double? actual) {
    if (nuevo == null) return false;
    if (actual != null && actual > 0) {
      final cociente = nuevo / actual;
      return !(cociente >= 0.2 && cociente <= 5);
    }
    return nuevo < 10;
  }

  factory ItemFactura.fromJson(Map<String, dynamic> j) => ItemFactura(
        indice: _i(j['indice']),
        codigo: j['codigo'] as String? ?? '',
        nombre: j['nombre'] as String? ?? '',
        cantidad: _i(j['cantidad']),
        precioCompra: _dn(j['precio_compra']),
        existe: j['existe'] as bool? ?? false,
        nombreSistema: j['nombre_sistema'] as String?,
        stockActual: _in(j['stock_actual']),
        precioCompraActual: _dn(j['precio_compra_actual']),
        posibleDuplicado: j['posible_duplicado'] as bool? ?? false,
        precioSospechoso: j['precio_sospechoso'] as bool? ?? false,
        emparejamiento: j['emparejamiento'] == null
            ? null
            : Emparejamiento.fromJson(j['emparejamiento'] as Map<String, dynamic>),
      );

  final int indice;
  String codigo;
  final String nombre;
  int cantidad;
  double? precioCompra;
  bool existe;
  String? nombreSistema;
  int? stockActual;
  double? precioCompraActual;
  final bool posibleDuplicado;
  bool precioSospechoso;
  final Emparejamiento? emparejamiento;

  /// Si se pisa el costo guardado con el precio de la factura (igual que el
  /// Panel). Arranca destildado si el precio leído es sospechoso.
  bool actualizarCosto;

  /// Leo eligió el producto de la lista de parecidos por nombre.
  bool emparejadoPorNombre = false;
  bool seleccionado;

  bool get cantidadValida => cantidadEsValida(cantidad);

  /// Leo confirmó a qué producto corresponde el renglón (escribiendo el
  /// código o eligiéndolo por nombre): recién ahí se puede tildar.
  void confirmarProducto(Producto p, {required bool porNombre}) {
    codigo = p.codigo;
    existe = true;
    nombreSistema = p.nombre;
    stockActual = p.stock;
    precioCompraActual = p.precioCompra;
    precioSospechoso = esPrecioSospechoso(precioCompra, p.precioCompra);
    actualizarCosto = !precioSospechoso;
    emparejadoPorNombre = porNombre;
    seleccionado = cantidadValida;
  }

  Map<String, dynamic> toJson() => {
        'codigo': codigo,
        'cantidad': cantidad,
        'precio_compra': actualizarCosto && precioCompra != null ? precioCompra : null,
      };
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
        stockNuevo: _in(j['stock_nuevo']),
        error: j['error'] as String?,
      );
  final String codigo;
  final bool ok;
  final int? stockNuevo;
  final String? error;
}
