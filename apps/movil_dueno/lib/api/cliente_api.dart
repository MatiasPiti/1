import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'modelos.dart';

const puertoPorDefecto = 8765;

/// "100.101.102.103" -> "http://100.101.102.103:8765"; si Leo escribió el
/// esquema (http/https) se respeta tal cual. Una IPv6 sin esquema va entre
/// corchetes ("fd7a::1" -> "http://[fd7a::1]:8765"), si no la URL no se puede armar.
String normalizarServidor(String entrada) {
  var t = entrada.trim();
  while (t.endsWith('/')) {
    t = t.substring(0, t.length - 1);
  }
  if (t.isEmpty) return t;
  final teniaEsquema = t.startsWith('http://') || t.startsWith('https://');
  if (!teniaEsquema) {
    if (!t.startsWith('[') && ':'.allMatches(t).length >= 2) t = '[$t]';
    t = 'http://$t';
  }
  final uri = Uri.tryParse(t);
  if (uri == null || uri.host.isEmpty) return t;
  if (!teniaEsquema && !uri.hasPort) return uri.replace(port: puertoPorDefecto).toString();
  return uri.toString();
}

class ApiError implements Exception {
  ApiError(this.mensaje, {this.status, this.incierto = false});
  final String mensaje;
  final int? status;

  /// Se cortó la comunicación (o se agotó el tiempo) en medio de una
  /// escritura: la PC pudo haberla aplicado o no. NO hay que invitar a
  /// reintentar a ciegas, porque puede sumar o restar dos veces; primero
  /// hay que mirar cómo quedó (las pantallas recargan sus datos).
  final bool incierto;
  bool get sesionVencida => status == 401;
  bool get sinConexion => status == null;
  @override
  String toString() => mensaje;
}

/// Cliente HTTP de la API del dueño (services/api_dueno.py).
class ClienteApi {
  ClienteApi({
    required String servidor,
    this.token,
    http.Client? client,
    this.alVencerSesion,
    this.timeoutLectura = const Duration(seconds: 20),
    this.timeoutEscritura = const Duration(seconds: 60),
  })  : servidor = normalizarServidor(servidor),
        _http = client ?? http.Client();

  final String servidor;
  String? token;
  final http.Client _http;

  /// Se llama ante un 401 en cualquier pedido autenticado.
  void Function()? alVencerSesion;

  final Duration timeoutLectura;

  /// Las escrituras (y la subida de facturas) tienen más margen: cortarlas
  /// antes de tiempo deja la duda de si se aplicaron.
  final Duration timeoutEscritura;

  static const _mensajeSinConexion =
      'No se pudo conectar con la PC del local. Revisá que esté prendida, con internet, y que Tailscale esté activo en los dos equipos.';
  static const _mensajeIncierto =
      'Se cortó la comunicación con la PC. No se sabe si se aplicó: revisá los últimos movimientos/precios antes de repetir.';
  static const _mensajeDireccionInvalida = 'La dirección de la PC no es válida';

  Uri _uri(String ruta, [Map<String, String>? query]) {
    final base = Uri.parse('$servidor$ruta');
    return query == null ? base : base.replace(queryParameters: query);
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      };

  /// [escritura]: el pedido cambia datos en la PC (stock, precios, config).
  /// Ante un corte o un timeout no se sabe si llegó a aplicarse, así que en
  /// vez de "probá de nuevo" se tira un [ApiError.incierto].
  Future<dynamic> _enviar(Future<http.Response> Function() pedido,
      {bool autenticado = true, bool escritura = false, Duration? timeout}) async {
    http.Response r;
    try {
      r = await pedido().timeout(timeout ?? (escritura ? timeoutEscritura : timeoutLectura));
    } on FormatException {
      throw ApiError(_mensajeDireccionInvalida); // p. ej. una IPv6 mal escrita
    } on ArgumentError {
      throw ApiError(_mensajeDireccionInvalida); // p. ej. "http://" sin host
    } on HandshakeException {
      throw ApiError('Error de seguridad (HTTPS) al conectar con la PC del local.');
    } on TimeoutException {
      if (escritura) throw ApiError(_mensajeIncierto, incierto: true);
      throw ApiError('La PC del local tardó demasiado en responder. Probá de nuevo.');
    } on SocketException {
      throw ApiError(escritura ? _mensajeIncierto : _mensajeSinConexion, incierto: escritura);
    } on http.ClientException {
      throw ApiError(escritura ? _mensajeIncierto : _mensajeSinConexion, incierto: escritura);
    }
    final cuerpo = r.bodyBytes.isEmpty ? null : _decodificar(r);
    if (r.statusCode >= 200 && r.statusCode < 300) return cuerpo;
    if (r.statusCode == 401 && autenticado) alVencerSesion?.call();
    throw ApiError(_mensajeDeError(cuerpo, r.statusCode), status: r.statusCode);
  }

  dynamic _decodificar(http.Response r) {
    try {
      return jsonDecode(utf8.decode(r.bodyBytes));
    } on FormatException {
      return null;
    }
  }

  String _mensajeDeError(dynamic cuerpo, int status) {
    final detalle = cuerpo is Map ? cuerpo['detail'] : null;
    if (detalle is String) return detalle;
    if (detalle is List && detalle.isNotEmpty && detalle.first is Map) {
      final msg = (detalle.first as Map)['msg'];
      if (msg is String) return msg.replaceFirst('Value error, ', '');
    }
    return 'Error inesperado del servidor ($status)';
  }

  Future<dynamic> _get(String ruta, [Map<String, String>? query]) =>
      _enviar(() => _http.get(_uri(ruta, query), headers: _headers));

  /// Un POST se trata como escritura salvo que se diga lo contrario
  /// (vista previa, mensaje de prueba): es lo seguro ante un corte.
  Future<dynamic> _post(String ruta, Object cuerpo, {bool escritura = true}) => _enviar(
      () => _http.post(_uri(ruta), headers: _headers, body: jsonEncode(cuerpo)),
      escritura: escritura);

  Future<dynamic> _put(String ruta, Object cuerpo) =>
      _enviar(() => _http.put(_uri(ruta), headers: _headers, body: jsonEncode(cuerpo)), escritura: true);

  // ------------------------------ sesión ------------------------------ //

  Future<Salud> salud() async =>
      Salud.fromJson(await _enviar(() => _http.get(_uri('/api/salud')), autenticado: false) as Map<String, dynamic>);

  Future<Sesion> login(String pin) async {
    final j = await _enviar(
      () => _http.post(_uri('/api/auth/login'), headers: _headers, body: jsonEncode({'pin': pin})),
      autenticado: false,
    );
    return Sesion.fromJson(j as Map<String, dynamic>);
  }

  // ----------------------------- dashboard ---------------------------- //

  Future<Dashboard> dashboard({String periodoTop = 'historico'}) async =>
      Dashboard.fromJson(await _get('/api/dashboard', {'periodo_top': periodoTop}) as Map<String, dynamic>);

  // ----------------------------- productos ---------------------------- //

  /// [limite]: cuántos productos traer como máximo (la API acepta hasta
  /// 2000; si no se indica, devuelve 200).
  Future<List<Producto>> productos({String q = '', String? campo, String valor = '', int? limite}) async {
    final query = {
      if (campo == null) 'q': q else ...{'campo': campo, 'valor': valor},
      if (limite != null) 'limite': '$limite',
    };
    final lista = await _get('/api/productos', query) as List;
    return [for (final p in lista) Producto.fromJson(p as Map<String, dynamic>)];
  }

  Future<Producto> producto(String codigo) async =>
      Producto.fromJson(await _get('/api/productos/${Uri.encodeComponent(codigo)}') as Map<String, dynamic>);

  Future<CambioPrecio> fijarPrecio(String codigo, double precio) async => CambioPrecio.fromJson(
      await _put('/api/productos/${Uri.encodeComponent(codigo)}/precio', {'precio_venta': precio})
          as Map<String, dynamic>);

  // ------------------------------- stock ------------------------------ //

  Future<ResultadoStock> movimiento(String codigo, int cantidad, {required bool sumar, String? motivo}) async =>
      ResultadoStock.fromJson(await _post('/api/stock/movimiento', {
        'codigo': codigo,
        'cantidad': cantidad,
        'operacion': sumar ? 'sumar' : 'restar',
        if (motivo != null && motivo.trim().isNotEmpty) 'motivo': motivo.trim(),
      }) as Map<String, dynamic>);

  Future<ResultadoStock> lector(String codigo, {bool sumar = false}) async => ResultadoStock.fromJson(
      await _post('/api/stock/lector', {'codigo': codigo, 'operacion': sumar ? 'sumar' : 'restar'})
          as Map<String, dynamic>);

  Future<List<Movimiento>> movimientos({int limite = 30}) async {
    final lista = await _get('/api/movimientos', {'limite': '$limite'}) as List;
    return [for (final m in lista) Movimiento.fromJson(m as Map<String, dynamic>)];
  }

  // ------------------------------ precios ----------------------------- //

  Map<String, dynamic> _ajuste(List<String> codigos, double? porcentaje, double? montoFijo, bool redondear) => {
        'codigos': codigos,
        'porcentaje': ?porcentaje,
        'monto_fijo': ?montoFijo,
        'redondear': redondear,
      };

  Future<List<CambioPrecio>> previsualizarPrecios(List<String> codigos,
      {double? porcentaje, double? montoFijo, bool redondear = true}) async {
    final lista = await _post('/api/precios/previsualizar', _ajuste(codigos, porcentaje, montoFijo, redondear),
        escritura: false) as List;
    return [for (final c in lista) CambioPrecio.fromJson(c as Map<String, dynamic>)];
  }

  Future<List<CambioPrecio>> aplicarPrecios(List<String> codigos,
      {double? porcentaje, double? montoFijo, bool redondear = true}) async {
    final lista = await _post('/api/precios/aplicar', _ajuste(codigos, porcentaje, montoFijo, redondear)) as List;
    return [for (final c in lista) CambioPrecio.fromJson(c as Map<String, dynamic>)];
  }

  // ------------------------------ alertas ----------------------------- //

  Future<List<Alerta>> alertas() async {
    final lista = await _get('/api/alertas') as List;
    return [for (final a in lista) Alerta.fromJson(a as Map<String, dynamic>)];
  }

  Future<ConfigAlertas> configAlertas() async =>
      ConfigAlertas.fromJson(await _get('/api/config/alertas') as Map<String, dynamic>);

  Future<ConfigAlertas> guardarTelegram({required bool habilitado, required String chatId, String? botToken}) async =>
      ConfigAlertas.fromJson(await _put('/api/config/telegram', {
        'habilitado': habilitado,
        'chat_id_default': chatId,
        'bot_token': ?botToken,
      }) as Map<String, dynamic>);

  /// Solo manda un mensaje de prueba: repetirlo no rompe nada.
  Future<bool> probarTelegram() async {
    final r = await _post('/api/config/telegram/probar', {}, escritura: false) as Map<String, dynamic>;
    return r['enviado'] as bool? ?? false;
  }

  Future<void> guardarUmbrales(int minimo, int maximo) =>
      _put('/api/config/umbrales', {'stock_minimo': minimo, 'stock_maximo': maximo});

  // ------------------------------ facturas ---------------------------- //

  /// Solo lee el PDF (no toca el stock), pero subirlo y analizarlo puede
  /// tardar: usa el tiempo de las escrituras.
  Future<FacturaAnalizada> analizarFactura(List<int> bytes, String nombre) async {
    final j = await _enviar(() async {
      final pedido = http.MultipartRequest('POST', _uri('/api/facturas/analizar'))
        ..headers.addAll({if (token != null) 'Authorization': 'Bearer $token'})
        ..files.add(http.MultipartFile.fromBytes('archivo', bytes, filename: nombre));
      return http.Response.fromStream(await _http.send(pedido));
    }, timeout: timeoutEscritura);
    return FacturaAnalizada.fromJson(j as Map<String, dynamic>);
  }

  Future<List<ResultadoFactura>> aplicarFactura(String nombre, List<ItemFactura> items) async {
    final lista = await _post('/api/facturas/aplicar', {
      'factura_nombre': nombre,
      'items': [for (final i in items) i.toJson()],
    }) as List;
    return [for (final r in lista) ResultadoFactura.fromJson(r as Map<String, dynamic>)];
  }
}
