import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'modelos.dart';

const puertoPorDefecto = 8765;

/// "100.101.102.103" -> "http://100.101.102.103:8765"; si Leo escribió el
/// esquema (http/https) se respeta tal cual.
String normalizarServidor(String entrada) {
  var t = entrada.trim();
  while (t.endsWith('/')) {
    t = t.substring(0, t.length - 1);
  }
  if (t.isEmpty) return t;
  final teniaEsquema = t.startsWith('http://') || t.startsWith('https://');
  if (!teniaEsquema) t = 'http://$t';
  final uri = Uri.tryParse(t);
  if (uri == null || uri.host.isEmpty) return t;
  if (!teniaEsquema && !uri.hasPort) return uri.replace(port: puertoPorDefecto).toString();
  return uri.toString();
}

class ApiError implements Exception {
  ApiError(this.mensaje, {this.status});
  final String mensaje;
  final int? status;
  bool get sesionVencida => status == 401;
  bool get sinConexion => status == null;
  @override
  String toString() => mensaje;
}

/// Cliente HTTP de la API del dueño (services/api_dueno.py).
class ClienteApi {
  ClienteApi({required String servidor, this.token, http.Client? client, this.alVencerSesion})
      : servidor = normalizarServidor(servidor),
        _http = client ?? http.Client();

  final String servidor;
  String? token;
  final http.Client _http;

  /// Se llama ante un 401 en cualquier pedido autenticado.
  void Function()? alVencerSesion;

  static const _timeout = Duration(seconds: 20);
  static const _mensajeSinConexion =
      'No se pudo conectar con la PC del local. Revisá que esté prendida, con internet, y que Tailscale esté activo en los dos equipos.';

  Uri _uri(String ruta, [Map<String, String>? query]) {
    final base = Uri.parse('$servidor$ruta');
    return query == null ? base : base.replace(queryParameters: query);
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      };

  Future<dynamic> _enviar(Future<http.Response> Function() pedido, {bool autenticado = true}) async {
    http.Response r;
    try {
      r = await pedido().timeout(_timeout);
    } on SocketException {
      throw ApiError(_mensajeSinConexion);
    } on TimeoutException {
      throw ApiError('La PC del local tardó demasiado en responder. Probá de nuevo.');
    } on http.ClientException {
      throw ApiError(_mensajeSinConexion);
    } on HandshakeException {
      throw ApiError('Error de seguridad (HTTPS) al conectar con la PC del local.');
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

  Future<dynamic> _post(String ruta, Object cuerpo) =>
      _enviar(() => _http.post(_uri(ruta), headers: _headers, body: jsonEncode(cuerpo)));

  Future<dynamic> _put(String ruta, Object cuerpo) =>
      _enviar(() => _http.put(_uri(ruta), headers: _headers, body: jsonEncode(cuerpo)));

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

  Future<List<Producto>> productos({String q = '', String? campo, String valor = ''}) async {
    final query = campo == null ? {'q': q} : {'campo': campo, 'valor': valor};
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
    final lista = await _post('/api/precios/previsualizar', _ajuste(codigos, porcentaje, montoFijo, redondear)) as List;
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

  Future<bool> probarTelegram() async =>
      ((await _post('/api/config/telegram/probar', {}) as Map<String, dynamic>)['enviado'] as bool?) ?? false;

  Future<void> guardarUmbrales(int minimo, int maximo) =>
      _put('/api/config/umbrales', {'stock_minimo': minimo, 'stock_maximo': maximo});

  // ------------------------------ facturas ---------------------------- //

  Future<FacturaAnalizada> analizarFactura(List<int> bytes, String nombre) async {
    final pedido = http.MultipartRequest('POST', _uri('/api/facturas/analizar'))
      ..headers.addAll({if (token != null) 'Authorization': 'Bearer $token'})
      ..files.add(http.MultipartFile.fromBytes('archivo', bytes, filename: nombre));
    final j = await _enviar(() async => http.Response.fromStream(await _http.send(pedido)));
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
