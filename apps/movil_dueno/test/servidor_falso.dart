import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/estado.dart';
import 'package:panel_dueno/formato.dart';

/// El contrato que escribe el backend (fuente de verdad, §8.4 de la espec).
/// Lo leen test_api_celular_contrato.py, contrato_test.dart y este servidor.
final Map<String, dynamic> fixture =
    jsonDecode(File('test/fixtures/contrato_api_celular_v2.json').readAsStringSync()) as Map<String, dynamic>;

/// Copia (para poder modificarla) de la respuesta [i] del ejemplo [nombre].
dynamic ejemplo(String nombre, [int i = 0]) =>
    jsonDecode(jsonEncode(((fixture['ejemplos'] as Map)[nombre] as Map)['respuestas'][i]));

http.Response respuesta(Object? cuerpo, [int status = 200]) => http.Response.bytes(
    utf8.encode(jsonEncode(cuerpo)), status, headers: {'content-type': 'application/json; charset=utf-8'});

http.Response errorApi(int status, String codigo, String detalle, [Map<String, dynamic> extra = const {}]) =>
    respuesta({'detail': detalle, 'codigo': codigo, ...extra}, status);

double _d(Object? v) => (v as num).toDouble();

/// pos_core/precios._num: "1.500" y "1.500,50" son mil quinientos.
double? _num(Object? valor) {
  if (valor == null) return null;
  if (valor is num) return valor.toDouble();
  var t = valor.toString().trim().replaceAll(r'$', '').replaceAll(' ', '');
  if (t.isEmpty) return null;
  if (t.contains(',') && t.contains('.')) {
    t = t.lastIndexOf(',') > t.lastIndexOf('.') ? t.replaceAll('.', '').replaceAll(',', '.') : t.replaceAll(',', '');
  } else if (t.contains(',')) {
    t = t.replaceAll(',', '.');
  } else if (t.contains('.')) {
    final i = t.lastIndexOf('.');
    final decimales = t.substring(i + 1);
    if (decimales.length == 3 && RegExp(r'^\d+$').hasMatch(decimales) && i > 0) t = t.replaceAll('.', '');
  }
  return double.tryParse(t);
}

double? _r2(double? v) => v == null ? null : (v * 100).round() / 100;

/// pos_core/precios.recalcular, copiada para que el servidor falso conteste
/// como el de verdad.
Map<String, double?> recalcularComoLaPc(Map<String, dynamic> pedido) {
  final cambio = pedido['cambio'];
  var costo = _num(pedido['costo_sin_iva']);
  var precioCosto = _num(pedido['precio_costo']);
  var margen = _num(pedido['margen']);
  var precioFinal = _num(pedido['precio_final']);
  const factorIva = 1.21;
  if (cambio == 'costo_sin_iva' && costo != null) {
    precioCosto = costo * factorIva;
  } else if (cambio == 'precio_costo' && precioCosto != null) {
    costo = precioCosto / factorIva;
  } else if (costo != null && precioCosto == null) {
    precioCosto = costo * factorIva;
  } else if (precioCosto != null && costo == null) {
    costo = precioCosto / factorIva;
  }
  final tieneCosto = precioCosto != null && precioCosto > 0;
  if (cambio == 'precio_final' && precioFinal != null) {
    if (tieneCosto) {
      margen = (precioFinal / precioCosto - 1) * 100;
    } else if (margen != null && margen > -100) {
      precioCosto = precioFinal / (1 + margen / 100);
      costo = precioCosto / factorIva;
    }
  } else if (cambio == 'margen' || cambio == 'costo_sin_iva' || cambio == 'precio_costo') {
    if (tieneCosto && margen != null && margen > -100) precioFinal = precioCosto * (1 + margen / 100);
  }
  return {'costo_sin_iva': _r2(costo), 'precio_costo': _r2(precioCosto), 'margen': _r2(margen), 'precio_final': _r2(precioFinal)};
}

/// bulk_edit.calcular_nuevo_precio (con su redondeo a la centena superior).
num nuevoPrecioComoLaPc(double anterior, {double? porcentaje, double? montoFijo, bool redondear = true}) {
  var nuevo = porcentaje != null ? anterior * (1 + porcentaje / 100) : anterior + montoFijo!;
  if (nuevo < 0) nuevo = 0;
  return redondear ? (nuevo / 100).ceil() * 100 : (nuevo * 100).round() / 100;
}

/// Imita a services/api_celular.py (contrato 2): las formas salen del
/// fixture y el estado (stock, precios crudos, umbrales, Telegram, facturas
/// aplicadas) vive en memoria.
class ServidorFalso {
  static const pin = '482915';
  static const direccion = '100.101.102.103';
  static const servidorNormalizado = 'http://100.101.102.103:8766';
  static final String token = (ejemplo('login') as Map)['token'] as String;

  /// "MÉTODO /ruta" de cada pedido que llegó.
  final pedidos = <String>[];

  /// Cuerpo JSON de cada pedido, por "MÉTODO /ruta".
  final cuerpos = <String, List<dynamic>>{};

  /// Lo que contesta /api/salud.
  Map<String, dynamic> salud = ejemplo('salud') as Map<String, dynamic>;

  /// false: el login da 503 pin_no_definido (con el texto del fixture).
  bool pinDefinido = true;

  /// true: todo pedido da el 503 en texto plano de uvicorn (limit_concurrency).
  bool ocupada = false;

  /// Simula que se corta la conexión DESPUÉS de que la PC aplicó la escritura.
  bool cortarEscrituras = false;

  /// Catálogo: café (con oferta %), yerba (umbral propio), coca (oferta de
  /// precio fijo, stock 0) y un alfajor que está en $ 0.
  final Map<String, Map<String, dynamic>> catalogo = {
    for (final p in ejemplo('productos') as List) (p as Map<String, dynamic>)['codigo'] as String: p,
    '7790004': {
      'codigo': '7790004', 'nombre': 'Alfajor Triple', 'marca': null, 'proveedor': null, 'categoria': null,
      'subrubro': null, 'stock': 12, 'stock_minimo': 0, 'stock_maximo': 0, 'umbral_propio': false,
      'precio_venta': 0.0, 'precio_efectivo': 0.0, 'oferta': null, 'precio_compra': 0.0, 'costo_sin_iva': 0.0,
      'margen_ganancia': 0.0, 'actualizado_en': '2026-10-04T08:00:00.000',
    },
  };

  /// Cuántos productos devuelve la búsqueda en total (los del catálogo y,
  /// si es más, relleno).
  int cantidadProductos = 0;
  Map<String, String>? ultimaBusqueda;

  /// Ventas que el StockService todavía va a descontar, por código.
  final ventasPendientes = <String, ({int lineas, int unidades})>{'7790003': (lineas: 3, unidades: 6)};

  late final List<Map<String, dynamic>> movimientos = [
    for (final m in ejemplo('movimientos') as List) m as Map<String, dynamic>,
  ];

  // --- alertas y Telegram ---
  bool telegramHabilitado = true;
  String chatId = '-1001234567890';

  /// El token de verdad: NUNCA sale de la PC (ni tapado a medias).
  final tokenTelegramReal = '7123456789:AAH-token-de-prueba-que-no-se-muestra';
  int umbralMinimo = 20;
  int umbralMaximo = 20;
  bool umbralExiste = true;
  int umbralesPropios = 3;
  int umbralesInactivos = 1;

  // --- facturas ---
  /// Lo que devuelve /api/facturas/analizar (los 5 renglones del fixture).
  List<dynamic> itemsFactura = (ejemplo('factura_analizar') as Map)['items'] as List;

  /// Renglones que llegaron a /api/facturas/aplicar y se aplicaron.
  final itemsFacturaAplicados = <Map<String, dynamic>>[];
  final _facturasAplicadas = <({String nombre, Set<String> renglones})>[];

  int get stockCafe => catalogo['7790002']!['stock'] as int;
  set stockCafe(int v) => catalogo['7790002']!['stock'] = v;

  /// Cambia un crudo "desde el Panel" (o una factura) con la hoja abierta.
  void cambiarPorFuera(String codigo, String campo, double valor) {
    catalogo[codigo]![campo] = valor;
    _recalcularEfectivo(catalogo[codigo]!);
  }

  void _recalcularEfectivo(Map<String, dynamic> p) {
    final oferta = p['oferta'] as Map<String, dynamic>?;
    final lista = _d(p['precio_venta']);
    p['precio_efectivo'] = oferta == null
        ? lista
        : oferta['tipo_descuento'] == 'PRECIO_FIJO'
            ? _d(oferta['valor'])
            : _r2(lista * (1 - _d(oferta['valor']) / 100));
  }

  Map<String, dynamic> _relleno(int i) => {
        'codigo': 'R${i.toString().padLeft(5, '0')}', 'nombre': 'Producto $i', 'marca': null, 'proveedor': null,
        'categoria': null, 'subrubro': null, 'stock': 10, 'stock_minimo': 0, 'stock_maximo': 0,
        'umbral_propio': false, 'precio_venta': 1000.0, 'precio_efectivo': 1000.0, 'oferta': null,
        'precio_compra': 0.0, 'costo_sin_iva': 0.0, 'margen_ganancia': 0.0, 'actualizado_en': '2026-10-01T00:00:00.000',
      };

  http.Response _escritura(http.Response r) {
    if (cortarEscrituras) throw http.ClientException('Connection closed while receiving data');
    return r;
  }

  http.Response _noExiste(String codigo) =>
      errorApi(404, 'no_existe', "Producto con código '$codigo' no existe o está inactivo");

  List<Map<String, dynamic>> get _alertas {
    final previas = {for (final a in ejemplo('alertas') as List) (a as Map)['codigo']: a};
    return [
      for (final p in catalogo.values)
        if (_tipoAlerta(p) case final tipo?)
          {
            'codigo': p['codigo'], 'nombre': p['nombre'], 'stock': p['stock'], 'stock_minimo': p['stock_minimo'],
            'stock_maximo': p['stock_maximo'], 'tipo': tipo, 'umbral_propio': p['umbral_propio'],
            'ultima_alerta_enviada': previas[p['codigo']]?['ultima_alerta_enviada'],
            'proximo_aviso_desde': previas[p['codigo']]?['proximo_aviso_desde'],
          },
    ];
  }

  static String? _tipoAlerta(Map<String, dynamic> p) {
    final stock = p['stock'] as int, min = p['stock_minimo'] as int, max = p['stock_maximo'] as int;
    if (min > 0 && stock <= min) return 'BAJO';
    if (max > 0 && stock >= max) return 'SOBRE';
    return null;
  }

  Map<String, dynamic> get _estadoUmbrales => {
        'umbral_global': {'stock_minimo': umbralMinimo, 'stock_maximo': umbralMaximo, 'existe': umbralExiste},
        'umbrales_propios': {'total': umbralesPropios, 'inactivos': umbralesInactivos},
      };

  Map<String, dynamic> get _configAlertas => {
        'telegram': {
          'habilitado': telegramHabilitado,
          'chat_id_default': chatId,
          'token_configurado': tokenTelegramReal.isNotEmpty,
          'token_mascara': tokenTelegramReal.isEmpty ? '' : '••••••••',
        },
        ..._estadoUmbrales,
      };

  static double? _ceroEsNull(Object? v) => v == null || _d(v) == 0 ? null : _d(v);

  Map<String, dynamic> _precios(Map<String, dynamic> p) => {
        'codigo': p['codigo'], 'nombre': p['nombre'], 'categoria': p['categoria'], 'subrubro': p['subrubro'],
        'stock': p['stock'],
        'costo_sin_iva': _ceroEsNull(p['costo_sin_iva']), 'precio_costo': _ceroEsNull(p['precio_compra']),
        'margen': _ceroEsNull(p['margen_ganancia']), 'precio_final': _ceroEsNull(p['precio_venta']),
        'precio_venta': p['precio_venta'],
        'crudos': {
          'costo_sin_iva': p['costo_sin_iva'], 'precio_compra': p['precio_compra'],
          'margen_ganancia': p['margen_ganancia'], 'precio_venta': p['precio_venta'],
        },
        'precio_efectivo': p['precio_efectivo'], 'oferta': p['oferta'], 'actualizado_en': p['actualizado_en'],
      };

  Map<String, dynamic> _movimientoStock(Map<String, dynamic> p, int cantidad, {required bool sumar, String? motivo}) {
    p['stock'] = (p['stock'] as int) + (sumar ? cantidad : -cantidad);
    movimientos.insert(0, {
      'fecha_hora': '2026-10-06T12:00:00.000', 'codigo': p['codigo'], 'nombre': p['nombre'],
      'tipo': sumar ? 'ENTRADA_MANUAL' : 'SALIDA_MANUAL', 'cantidad': cantidad, 'stock_resultante': p['stock'],
      'motivo': motivo ?? (sumar ? 'Alta manual' : 'Baja manual'), 'usuario': 'dueño (celular)',
    });
    final pendientes = ventasPendientes[p['codigo']] ?? (lineas: 0, unidades: 0);
    return {
      'codigo': p['codigo'], 'nombre': p['nombre'], 'stock_nuevo': p['stock'], 'stock_minimo': p['stock_minimo'],
      'stock_maximo': p['stock_maximo'],
      'ventas_pendientes': {'lineas': pendientes.lineas, 'unidades': pendientes.unidades},
    };
  }

  http.Response _stock(Map<String, dynamic> datos, {required int cantidad, String? motivo}) {
    final p = catalogo[datos['codigo']];
    if (p == null) return _noExiste(datos['codigo'] as String);
    final sumar = datos['operacion'] == 'sumar';
    if (!sumar && (p['stock'] as int) < cantidad) {
      return errorApi(409, 'stock_insuficiente',
          "Stock insuficiente para '${p['codigo']}': disponible ${p['stock']}, se pidió descontar $cantidad");
    }
    return _escritura(respuesta(_movimientoStock(p, cantidad, sumar: sumar, motivo: motivo)));
  }

  http.Response _guardarPrecios(String codigo, Map<String, dynamic> datos) {
    final p = catalogo[codigo];
    if (p == null) return _noExiste(codigo);
    final esperado = datos['esperado'] as Map<String, dynamic>;
    const nombres = {
      'costo_sin_iva': 'El costo sin IVA',
      'precio_compra': 'El precio de costo',
      'margen_ganancia': 'El % de ganancia',
      'precio_venta': 'El precio final',
    };
    for (final e in nombres.entries) {
      final antes = _d(esperado[e.key]), ahora = _d(p[e.key]);
      if (antes != ahora) {
        final texto = e.key == 'margen_ganancia' ? '${numero(antes)} %, ahora es ${numero(ahora)} %' : '${moneda(antes)}, ahora es ${moneda(ahora)}';
        return errorApi(409, 'precio_cambio', '${e.value} cambió mientras tanto: era $texto. Revisalo de nuevo.',
            {'campo': e.key});
      }
    }
    final antes = _precios(p);
    void poner(String clave, String columna) {
      if (datos[clave] != null) p[columna] = _d(datos[clave]);
    }

    poner('costo_sin_iva', 'costo_sin_iva');
    poner('precio_costo', 'precio_compra');
    poner('margen', 'margen_ganancia');
    poner('precio_final', 'precio_venta');
    p['actualizado_en'] = '2026-10-06T12:30:00.010';
    _recalcularEfectivo(p);
    return _escritura(respuesta({'antes': antes, 'despues': _precios(p)}));
  }

  List<Map<String, dynamic>> _vistaPrevia(Map<String, dynamic> datos) => [
        for (final codigo in (datos['codigos'] as List).cast<String>())
          if (catalogo[codigo] case final p?)
            () {
              final anterior = _d(p['precio_venta']);
              final nuevo = nuevoPrecioComoLaPc(anterior,
                  porcentaje: (datos['porcentaje'] as num?)?.toDouble(),
                  montoFijo: (datos['monto_fijo'] as num?)?.toDouble(),
                  redondear: datos['redondear'] as bool? ?? true);
              return <String, dynamic>{
                'codigo': codigo, 'nombre': p['nombre'], 'ok': true, 'precio_anterior': anterior,
                'precio_nuevo': nuevo, 'queda_en_cero': anterior > 0 && nuevo <= 0, 'en_oferta': p['oferta'] != null,
              };
            }()
          else
            {'codigo': codigo, 'ok': false, 'error': 'no encontrado'},
      ];

  http.Response _aplicarAjuste(Map<String, dynamic> datos) {
    final codigos = (datos['codigos'] as List).cast<String>();
    final esperados = (datos['esperados'] as Map).cast<String, num>();
    if (esperados.length != codigos.toSet().length || !codigos.every(esperados.containsKey)) {
      return errorApi(422, 'dato_invalido', 'Dato inválido en «esperados»: tiene que traer exactamente los códigos',
          {'campo': 'esperados'});
    }
    final vista = _vistaPrevia(datos);
    final enCero = vista.where((v) => v['queda_en_cero'] == true).length;
    if (enCero > 0) {
      return errorApi(400, 'queda_en_cero',
          '$enCero producto(s) quedarían en \$ 0. Sacalos de la lista o cambiá el ajuste.');
    }
    final resultado = <Map<String, dynamic>>[];
    for (final v in vista) {
      final codigo = v['codigo'] as String;
      if (v['ok'] != true) {
        resultado.add(v);
        continue;
      }
      final p = catalogo[codigo]!;
      final actual = _d(p['precio_venta']), esperado = esperados[codigo]!.toDouble();
      if (actual != esperado) {
        resultado.add({
          'codigo': codigo, 'ok': false,
          'error': 'el precio cambió mientras tanto (era $esperado, ahora es $actual): no se tocó',
        });
        continue;
      }
      p['precio_venta'] = _d(v['precio_nuevo']);
      _recalcularEfectivo(p);
      resultado.add({'codigo': codigo, 'ok': true, 'precio_anterior': actual, 'precio_nuevo': v['precio_nuevo']});
    }
    return _escritura(respuesta(resultado));
  }

  http.Response _aplicarFactura(Map<String, dynamic> datos) {
    final nombre = datos['factura_nombre'] as String;
    final items = (datos['items'] as List).cast<Map<String, dynamic>>();
    final renglones = {for (final i in items) '${i['codigo']}:${i['cantidad']}'};
    if (datos['forzar'] != true) {
      for (final previa in _facturasAplicadas) {
        final iguales = previa.renglones.intersection(renglones).length;
        if (previa.nombre == nombre && iguales > 0) {
          return errorApi(
              409,
              'factura_ya_aplicada',
              'Esta factura ya se cargó hace 0 min ($iguales renglones iguales). Si es otra factura con el mismo '
                  'nombre, tocá «Cargar igual».',
              {'hace_min': 0, 'renglones_iguales': iguales});
        }
      }
    }
    _facturasAplicadas.add((nombre: nombre, renglones: renglones));
    final resultado = <Map<String, dynamic>>[];
    for (final i in items) {
      final p = catalogo[i['codigo']];
      if (p == null) {
        resultado.add({'codigo': i['codigo'], 'ok': false, 'error': "Producto con código '${i['codigo']}' no existe o está inactivo"});
        continue;
      }
      itemsFacturaAplicados.add(i);
      p['stock'] = (p['stock'] as int) + (i['cantidad'] as int);
      if (i['precio_compra'] != null) p['precio_compra'] = _d(i['precio_compra']);
      resultado.add({'codigo': i['codigo'], 'ok': true, 'stock_nuevo': p['stock']});
    }
    return _escritura(respuesta(resultado));
  }

  late final http.Client cliente = MockClient((r) async {
    final clave = '${r.method} ${r.url.path}';
    pedidos.add(clave);
    final esJson = (r.headers['Content-Type'] ?? r.headers['content-type'] ?? '').startsWith('application/json');
    final datos = esJson && r.body.isNotEmpty ? jsonDecode(r.body) : null;
    cuerpos.putIfAbsent(clave, () => []).add(datos);
    if (ocupada) return http.Response('Service Unavailable', 503, headers: {'content-type': 'text/plain'});

    final ruta = r.url.path;
    if (ruta == '/api/salud') return respuesta(salud);
    if (ruta == '/api/auth/login') {
      if (!pinDefinido) {
        final e = ejemplo('error_503_pin') as Map<String, dynamic>;
        return errorApi(503, 'pin_no_definido', e['detail'] as String);
      }
      // la del fixture (nov-2025) ya venció para el reloj de las pruebas
      return (datos as Map)['pin'] == pin
          ? respuesta({...ejemplo('login') as Map<String, dynamic>, 'expira': 4102444800})
          : errorApi(401, 'pin_incorrecto', 'PIN incorrecto.');
    }
    if (r.headers['Authorization'] != 'Bearer $token') {
      return errorApi(401, 'sesion_invalida', 'Sesión vencida o inválida. Ingresá el PIN de nuevo.');
    }
    final q = r.url.queryParameters;
    String resto(String prefijo) => Uri.decodeComponent(ruta.substring(prefijo.length));

    switch ((r.method, ruta)) {
      case ('GET', '/api/auth/yo'):
        return respuesta({'usuario': 'dueño'});
      case ('GET', '/api/dashboard'):
        return respuesta({
          ...ejemplo('dashboard') as Map<String, dynamic>,
          'periodo_top': q['periodo_top'] ?? 'historico',
          'alertas_activas': _alertas.length,
          'telegram_habilitado': telegramHabilitado,
        });
      case ('GET', '/api/productos'):
        ultimaBusqueda = q;
        final limite = (int.tryParse(q['limite'] ?? '') ?? 200).clamp(1, 2000);
        final todos = [
          ...catalogo.values,
          for (var i = catalogo.length; i < cantidadProductos; i++) _relleno(i),
        ];
        final campo = q['campo'];
        if (campo != null && !const ['marca', 'proveedor', 'categoria', 'subrubro'].contains(campo)) {
          return errorApi(400, 'dato_invalido', "Campo de filtro inválido: '$campo'");
        }
        final texto = (campo == null ? q['q'] : q['valor'])?.toLowerCase() ?? '';
        final filtrados = todos.where((p) {
          if (texto.isEmpty) return true;
          if (campo != null) return '${p[campo] ?? ''}'.toLowerCase().contains(texto);
          return '${p['codigo']}'.toLowerCase().contains(texto) || '${p['nombre']}'.toLowerCase().contains(texto);
        });
        return respuesta(filtrados.take(limite).toList());
      case ('GET', final String camino) when camino.startsWith('/api/productos/'):
        final codigo = resto('/api/productos/');
        final p = catalogo[codigo];
        if (p == null) return _noExiste(codigo);
        return respuesta({...p, 'movimientos': movimientos.where((m) => m['codigo'] == codigo).take(15).toList()});
      case ('GET', '/api/movimientos'):
        final limite = int.tryParse(q['limite'] ?? '') ?? 30;
        return respuesta(movimientos.where((m) => q['codigo'] == null || m['codigo'] == q['codigo']).take(limite).toList());
      case ('POST', '/api/stock/movimiento'):
        final d = datos as Map<String, dynamic>;
        return _stock(d, cantidad: d['cantidad'] as int, motivo: d['motivo'] as String?);
      case ('POST', '/api/stock/lector'):
        final d = datos as Map<String, dynamic>;
        return _stock(d,
            cantidad: 1, motivo: d['operacion'] == 'sumar' ? 'Lector de código de barras' : null);
      case ('GET', final String camino) when camino.startsWith('/api/precios/'):
        final codigo = resto('/api/precios/');
        final p = catalogo[codigo];
        return p == null ? _noExiste(codigo) : respuesta(_precios(p));
      case ('POST', '/api/precios/recalcular'):
        return respuesta(recalcularComoLaPc(datos as Map<String, dynamic>));
      case ('PUT', final String camino) when camino.startsWith('/api/precios/'):
        return _guardarPrecios(resto('/api/precios/'), datos as Map<String, dynamic>);
      case ('POST', '/api/precios/previsualizar'):
        return respuesta(_vistaPrevia(datos as Map<String, dynamic>));
      case ('POST', '/api/precios/aplicar'):
        return _aplicarAjuste(datos as Map<String, dynamic>);
      case ('GET', '/api/alertas'):
        return respuesta(_alertas);
      case ('GET', '/api/config/alertas'):
        return respuesta(_configAlertas);
      case ('PUT', '/api/config/telegram'):
        final d = datos as Map<String, dynamic>;
        if (d.keys.toSet().difference({'habilitado'}).isNotEmpty) {
          return errorApi(422, 'solo_en_la_pc', 'El token y el chat del bot se cargan en el Panel de la PC.');
        }
        telegramHabilitado = d['habilitado'] as bool;
        return _escritura(respuesta(_configAlertas));
      case ('POST', '/api/config/telegram/probar'):
        return respuesta(ejemplo('telegram_probar', telegramHabilitado ? 0 : 1));
      case ('PUT', '/api/config/umbrales'):
        final d = datos as Map<String, dynamic>;
        umbralMinimo = d['stock_minimo'] as int;
        umbralMaximo = d['stock_maximo'] as int;
        umbralExiste = true;
        return _escritura(respuesta(_estadoUmbrales));
      case ('DELETE', '/api/config/umbral-global'):
        final borradas = umbralExiste ? 1 : 0;
        umbralExiste = false;
        umbralMinimo = 0;
        umbralMaximo = 0;
        return _escritura(respuesta({'borradas': borradas, ..._estadoUmbrales}));
      case ('POST', '/api/facturas/analizar'):
        return respuesta({
          'factura_nombre': 'remito.pdf', 'es_pdf_escaneado': false, 'lineas_no_reconocidas': ['Subtotal 12.000'],
          'items': itemsFactura,
        });
      case ('POST', '/api/facturas/aplicar'):
        return _aplicarFactura(datos as Map<String, dynamic>);
    }
    return errorApi(404, 'ruta_inexistente', 'Esa función no existe en la PC del local: hay que actualizarla.');
  });

  ClienteApi crear(String servidor) => ClienteApi(servidor: servidor, client: cliente);
}

/// Otro programa contestando en la dirección de la PC.
class Impostor {
  /// El remote_api del Dueño Remoto (8765): no conoce /api/salud.
  Impostor.remoteApi() : _salud = respuesta({'ok': false, 'error': 'not found'}, 404);

  /// El ApiDueno viejo: /api/salud sin "servicio" (y un login que aceptaría el PIN).
  Impostor.apiDuenoViejo() : _salud = respuesta({'ok': true, 'nombre_local': 'Almacén de Leo', 'version_api': '1.0.0'});

  /// Una ApiCelular de un contrato anterior.
  Impostor.contratoViejo() : _salud = respuesta({...ejemplo('salud') as Map<String, dynamic>, 'contrato': 1});

  final http.Response _salud;
  final pedidos = <String>[];

  late final http.Client cliente = MockClient((r) async {
    pedidos.add('${r.method} ${r.url.path}');
    if (r.url.path == '/api/salud') return _salud;
    return respuesta({'token': 'robado', 'usuario': 'dueño', 'expira': 4102444800});
  });

  ClienteApi crear(String servidor) => ClienteApi(servidor: servidor, client: cliente);
}

class BiometriaFalsa implements Biometria {
  BiometriaFalsa({this.hay = false, this.acepta = true});
  bool hay;
  bool acepta;
  int pedidos = 0;

  @override
  Future<bool> disponible() async => hay;

  @override
  Future<bool> autenticar(String motivo) async {
    pedidos++;
    return acepta;
  }
}
