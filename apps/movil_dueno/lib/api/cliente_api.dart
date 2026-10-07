import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import 'modelos.dart';

/// Puerto de ApiCelular (services/api_celular.py) en la PC del local.
const puertoPorDefecto = 8766;

/// El de la API remota del Dueño Remoto (services/remote_api.py). La app
/// vieja (ApiDueno) también usaba este número: hoy ahí no hay nada para el celular.
const puertoDuenoRemoto = 8765;

/// Lo que contesta GET /api/salud en "servicio". Se verifica la firma y no
/// solo que el puerto conecte: ya pasó que otro programa en el puerto
/// engañara al diagnóstico.
const firmaApi = 'otter-api-celular';

/// Versión del contrato HTTP que entiende esta app.
const contratoMinimo = 2;

const mensajeNoTailscale =
    'Esa dirección no es de Tailscale: tiene que empezar con 100. Copiala de la app de Tailscale.';

/// "100.101.102.103" -> "http://100.101.102.103:8766"; si Leo escribió el
/// esquema (http/https) se respeta tal cual. Una IPv6 sin esquema va entre
/// corchetes ("fd7a::1" -> "http://[fd7a::1]:8766"), si no la URL no se puede armar.
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

final _ipv4 = RegExp(r'^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$');

/// true solo si la PC es una IPv4 de Tailscale (100.64.0.0/10: primer octeto
/// 100, segundo entre 64 y 127). Es la tercera barrera de la regla 1 (las
/// otras dos son el filtro de la API y el firewall): el PIN y el token no
/// salen nunca hacia una IP de la LAN, una pública o un nombre que alguien
/// pueda hacer resolver a otro lado.
bool esDireccionTailscale(String servidor) {
  final uri = Uri.tryParse(normalizarServidor(servidor));
  if (uri == null) return false;
  final m = _ipv4.firstMatch(uri.host);
  if (m == null) return false;
  final octetos = [for (var i = 1; i <= 4; i++) int.parse(m.group(i)!)];
  if (octetos.any((o) => o > 255)) return false;
  return octetos[0] == 100 && octetos[1] >= 64 && octetos[1] <= 127;
}

/// El HttpClient que usa la app de verdad. La API cierra las conexiones
/// quietas a los 5 s y el HttpClient de Dart, por defecto, las reusa hasta
/// 15 s: una escritura que sale por una conexión que la PC acaba de cerrar
/// falla y se vería como "incierto" sin haber llegado. Con 3 s del lado del
/// celular nunca reusa una que la PC ya cerró.
HttpClient httpClientPorDefecto() => HttpClient()..idleTimeout = const Duration(seconds: 3);

class ApiError implements Exception {
  ApiError(
    this.mensaje, {
    this.status,
    this.incierto = false,
    this.codigo,
    this.reintentarEnS,
    this.campo,
    this.haceMin,
    this.renglonesIguales,
  });

  final String mensaje;
  final int? status;

  /// Se cortó la comunicación (o se agotó el tiempo) en medio de una
  /// escritura: la PC pudo haberla aplicado o no. NO hay que invitar a
  /// reintentar a ciegas, porque puede sumar o restar dos veces; primero
  /// hay que mirar cómo quedó (las pantallas recargan sus datos).
  final bool incierto;

  /// El slug de la API (`no_existe`, `precio_cambio`, `factura_ya_aplicada`…)
  /// o uno de la app: `no_tailscale`, `puerto_equivocado`, `otro_programa`,
  /// `actualizar_pc`, `ocupada`.
  final String? codigo;

  /// 429 del login: cuántos segundos faltan para poder probar de nuevo.
  final int? reintentarEnS;

  /// 422 y 409 `precio_cambio`: qué campo.
  final String? campo;

  /// 409 `factura_ya_aplicada`.
  final int? haceMin;
  final int? renglonesIguales;

  bool get sesionVencida => status == 401;
  bool get sinConexion => status == null && codigo == null;
  @override
  String toString() => mensaje;
}

/// Cliente HTTP de la API del celular (services/api_celular.py, contrato 2).
class ClienteApi {
  ClienteApi({
    required String servidor,
    this.token,
    http.Client? client,
    this.alVencerSesion,
    this.timeoutLectura = const Duration(seconds: 20),
    this.timeoutEscritura = const Duration(seconds: 60),
  })  : servidor = normalizarServidor(servidor),
        httpInterno = client == null ? httpClientPorDefecto() : null {
    _http = client ?? IOClient(httpInterno);
  }

  final String servidor;
  String? token;
  late final http.Client _http;

  /// El HttpClient de verdad cuando no se pasó `client` (las pruebas miran
  /// su idleTimeout); null si se pasó uno.
  final HttpClient? httpInterno;

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
  static const _mensajePuertoEquivocado =
      'Ese es el puerto del Dueño Remoto ($puertoDuenoRemoto). La app usa el $puertoPorDefecto.';
  static const _mensajeOtroPrograma = 'En esa dirección contesta otro programa, no la API del celular de Otter.';
  static const _mensajeActualizarPc = 'La PC del local tiene una versión vieja: hay que actualizarla.';
  static const _mensajeOcupada = 'La PC está ocupada: probá en unos segundos.';

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
  /// vez de "probá de nuevo" se tira un [ApiError.incierto]. Ninguna
  /// escritura se reintenta sola.
  Future<dynamic> _enviar(Future<http.Response> Function() pedido,
      {bool autenticado = true, bool escritura = false, Duration? timeout}) async {
    // Antes de tocar la red: fuera de Tailscale no sale nada (ni el PIN ni el token).
    if (!esDireccionTailscale(servidor)) throw ApiError(mensajeNoTailscale, codigo: 'no_tailscale');
    http.Response r;
    try {
      r = await pedido().timeout(timeout ?? (escritura ? timeoutEscritura : timeoutLectura));
    } on FormatException {
      throw ApiError(_mensajeDireccionInvalida);
    } on ArgumentError {
      throw ApiError(_mensajeDireccionInvalida);
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
    throw _error(cuerpo, r.statusCode);
  }

  dynamic _decodificar(http.Response r) {
    try {
      return jsonDecode(utf8.decode(r.bodyBytes));
    } on FormatException {
      return null;
    }
  }

  static int? _entero(dynamic v) => v is num ? v.toInt() : null;

  ApiError _error(dynamic cuerpo, int status) {
    if (cuerpo is Map) {
      final detalle = cuerpo['detail'];
      String? mensaje;
      if (detalle is String) {
        mensaje = detalle;
      } else if (detalle is List && detalle.isNotEmpty && detalle.first is Map) {
        final msg = (detalle.first as Map)['msg'];
        if (msg is String) mensaje = msg.replaceFirst('Value error, ', '');
      }
      if (mensaje != null) {
        return ApiError(
          mensaje,
          status: status,
          codigo: cuerpo['codigo'] as String?,
          reintentarEnS: _entero(cuerpo['reintentar_en_s']),
          campo: cuerpo['campo']?.toString(),
          haceMin: _entero(cuerpo['hace_min']),
          renglonesIguales: _entero(cuerpo['renglones_iguales']),
        );
      }
      // {"ok": false, "error": "..."} es la forma del remote_api (8765).
      if (cuerpo.containsKey('error')) {
        return ApiError(_mensajePuertoEquivocado, status: status, codigo: 'puerto_equivocado');
      }
    }
    // El 503 en texto plano lo da uvicorn por encima de limit_concurrency,
    // ANTES de pasarle el pedido a la API: no se aplicó nada (no es incierto).
    if (status == 503 && cuerpo == null) return ApiError(_mensajeOcupada, status: status, codigo: 'ocupada');
    return ApiError('Error inesperado del servidor ($status)', status: status);
  }

  Future<dynamic> _get(String ruta, [Map<String, String>? query]) =>
      _enviar(() => _http.get(_uri(ruta, query), headers: _headers));

  /// Un POST se trata como escritura salvo que se diga lo contrario
  /// (vista previa, recalcular, mensaje de prueba): es lo seguro ante un corte.
  Future<dynamic> _post(String ruta, Object cuerpo, {bool escritura = true, Duration? timeout}) => _enviar(
      () => _http.post(_uri(ruta), headers: _headers, body: jsonEncode(cuerpo)),
      escritura: escritura,
      timeout: timeout);

  Future<dynamic> _put(String ruta, Object cuerpo) =>
      _enviar(() => _http.put(_uri(ruta), headers: _headers, body: jsonEncode(cuerpo)), escritura: true);

  Future<dynamic> _delete(String ruta) =>
      _enviar(() => _http.delete(_uri(ruta), headers: _headers), escritura: true);

  static String _ruta(String base, String codigo) => '$base/${Uri.encodeComponent(codigo)}';

  // ------------------------------ sesión ------------------------------ //

  /// GET /api/salud, verificando que conteste la API del celular de Otter
  /// (firma) y que entienda este contrato.
  Future<Salud> salud() async {
    final dynamic j;
    try {
      j = await _enviar(() => _http.get(_uri('/api/salud')), autenticado: false);
    } on ApiError catch (e) {
      // contestó algo que no habla nuestro idioma (ni el del remote_api)
      if (e.status != null && e.codigo == null) {
        throw ApiError(_mensajeOtroPrograma, status: e.status, codigo: 'otro_programa');
      }
      rethrow;
    }
    if (j is! Map<String, dynamic> || j['servicio'] != firmaApi) {
      throw ApiError(_mensajeOtroPrograma, codigo: 'otro_programa');
    }
    final s = Salud.fromJson(j);
    if (s.contrato < contratoMinimo) throw ApiError(_mensajeActualizarPc, codigo: 'actualizar_pc');
    return s;
  }

  Future<Sesion> login(String pin) async {
    final j = await _enviar(
      () => _http.post(_uri('/api/auth/login'), headers: _headers, body: jsonEncode({'pin': pin})),
      autenticado: false,
    );
    return Sesion.fromJson(j as Map<String, dynamic>);
  }

  Future<String> yo() async => (await _get('/api/auth/yo') as Map<String, dynamic>)['usuario'] as String;

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
      Producto.fromJson(await _get(_ruta('/api/productos', codigo)) as Map<String, dynamic>);

  // ------------------------------- stock ------------------------------ //

  Future<ResultadoStock> movimiento(String codigo, int cantidad, {required bool sumar, String? motivo}) async {
    final m = motivo?.trim();
    return ResultadoStock.fromJson(await _post('/api/stock/movimiento', {
      'codigo': codigo,
      'cantidad': cantidad,
      'operacion': sumar ? 'sumar' : 'restar',
      // null = "Alta manual" / "Baja manual" en la PC
      'motivo': m == null || m.isEmpty ? null : m,
    }) as Map<String, dynamic>);
  }

  Future<ResultadoStock> lector(String codigo, {bool sumar = false}) async => ResultadoStock.fromJson(
      await _post('/api/stock/lector', {'codigo': codigo, 'operacion': sumar ? 'sumar' : 'restar'})
          as Map<String, dynamic>);

  Future<List<Movimiento>> movimientos({int limite = 30, String? codigo}) async {
    final lista = await _get('/api/movimientos', {'limite': '$limite', 'codigo': ?codigo}) as List;
    return [for (final m in lista) Movimiento.fromJson(m as Map<String, dynamic>)];
  }

  // ------------------------------ precios ----------------------------- //

  Future<PreciosProducto> precios(String codigo) async =>
      PreciosProducto.fromJson(await _get(_ruta('/api/precios', codigo)) as Map<String, dynamic>);

  /// La cadena Costo s/IVA → Precio costo → % Ganancia → Precio final la
  /// calcula siempre la PC (pos_core/precios.recalcular), con [cambio] = el
  /// campo que tocó Leo. No escribe nada. Los valores van como los escribió
  /// (texto) para que la PC los lea igual que el Panel ("1.500" = mil quinientos).
  Future<ValoresPrecio> recalcularPrecios(
      {required String cambio, Object? costoSinIva, Object? precioCosto, Object? margen, Object? precioFinal}) async {
    Object? limpio(Object? v) => v is String ? (v.trim().isEmpty ? null : v.trim()) : v;
    final j = await _post(
        '/api/precios/recalcular',
        {
          'cambio': cambio,
          'costo_sin_iva': limpio(costoSinIva),
          'precio_costo': limpio(precioCosto),
          'margen': limpio(margen),
          'precio_final': limpio(precioFinal),
        },
        escritura: false);
    return ValoresPrecio.fromJson(j as Map<String, dynamic>);
  }

  /// Guarda exactamente los 4 valores que vio Leo (null = no tocar).
  /// [esperado] es la copia SIN TOCAR de `precios(codigo).crudos`: si en la
  /// base cambió alguno de los cuatro mientras tanto (una factura que pisó el
  /// costo, otro precio desde el Panel), la PC contesta 409 `precio_cambio` y
  /// no pisa nada. También vuelve seguro guardar de nuevo después de un corte.
  Future<CambioPrecios> guardarPrecios(String codigo, ValoresPrecio valores, {required CrudosPrecio esperado}) async =>
      CambioPrecios.fromJson(
          await _put(_ruta('/api/precios', codigo), {...valores.toJson(), 'esperado': esperado.toJson()})
              as Map<String, dynamic>);

  Map<String, dynamic> _ajuste(List<String> codigos, double? porcentaje, double? montoFijo, bool redondear) => {
        'codigos': codigos,
        'porcentaje': porcentaje,
        'monto_fijo': montoFijo,
        'redondear': redondear,
      };

  Future<List<CambioPrecio>> previsualizarPrecios(List<String> codigos,
      {double? porcentaje, double? montoFijo, bool redondear = true}) async {
    final lista = await _post('/api/precios/previsualizar', _ajuste(codigos, porcentaje, montoFijo, redondear),
        escritura: false) as List;
    return [for (final c in lista) CambioPrecio.fromJson(c as Map<String, dynamic>)];
  }

  /// Cada producto es una transacción con synchronous = FULL en el disco del
  /// local: con miles de códigos, un tiempo fijo puede no alcanzar (y cortarlo
  /// antes deja la duda de si se aplicó). 2000 códigos → 2 minutos.
  Duration timeoutAplicar(int cantidad) => timeoutEscritura + Duration(milliseconds: 30 * cantidad);

  /// [esperados] = {codigo: precio anterior} de la vista previa, y [codigos]
  /// tienen que ser exactamente sus claves. Un código cuyo precio ya no es
  /// ese no se toca: así "aplicar de nuevo" tras un corte no suma el
  /// aumento dos veces.
  Future<List<CambioPrecio>> aplicarPrecios(List<String> codigos,
      {required Map<String, double> esperados, double? porcentaje, double? montoFijo, bool redondear = true}) async {
    if (codigos.length != esperados.length || !codigos.every(esperados.containsKey)) {
      throw ArgumentError('esperados tiene que tener exactamente los mismos códigos que codigos');
    }
    final lista = await _post(
        '/api/precios/aplicar', {..._ajuste(codigos, porcentaje, montoFijo, redondear), 'esperados': esperados},
        timeout: timeoutAplicar(codigos.length)) as List;
    return [for (final c in lista) CambioPrecio.fromJson(c as Map<String, dynamic>)];
  }

  // ------------------------------ alertas ----------------------------- //

  Future<List<Alerta>> alertas({int limite = 1000}) async {
    final lista = await _get('/api/alertas', {'limite': '$limite'}) as List;
    return [for (final a in lista) Alerta.fromJson(a as Map<String, dynamic>)];
  }

  Future<ConfigAlertas> configAlertas() async =>
      ConfigAlertas.fromJson(await _get('/api/config/alertas') as Map<String, dynamic>);

  /// Desde el celular solo se prende o apaga el bot (corte de emergencia).
  /// El token y el chat se cargan en el Panel de la PC: un token no se tipea
  /// en un celular (regla 4) y una sesión robada no puede desviar las alertas.
  Future<ConfigAlertas> guardarTelegram({required bool habilitado}) async =>
      ConfigAlertas.fromJson(await _put('/api/config/telegram', {'habilitado': habilitado}) as Map<String, dynamic>);

  /// Solo manda un mensaje de prueba: repetirlo no rompe nada.
  Future<ResultadoPrueba> probarTelegram() async => ResultadoPrueba.fromJson(
      await _post('/api/config/telegram/probar', {}, escritura: false) as Map<String, dynamic>);

  Future<EstadoUmbrales> guardarUmbrales(int minimo, int maximo) async => EstadoUmbrales.fromJson(
      await _put('/api/config/umbrales', {'stock_minimo': minimo, 'stock_maximo': maximo}) as Map<String, dynamic>);

  /// Borra el umbral global; los umbrales propios de cada producto no se tocan.
  Future<EstadoUmbrales> quitarUmbralGlobal() async =>
      EstadoUmbrales.fromJson(await _delete('/api/config/umbral-global') as Map<String, dynamic>);

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

  /// Si en la última hora ya se cargó una factura con el mismo nombre y algún
  /// renglón igual, la PC contesta 409 `factura_ya_aplicada` (ApiError con
  /// [ApiError.haceMin] y [ApiError.renglonesIguales]); [forzar] la carga igual.
  Future<List<ResultadoFactura>> aplicarFactura(String nombre, List<ItemFactura> items, {bool forzar = false}) async {
    final lista = await _post('/api/facturas/aplicar', {
      'factura_nombre': nombre,
      'items': [for (final i in items) i.toJson()],
      'forzar': forzar,
    }) as List;
    return [for (final r in lista) ResultadoFactura.fromJson(r as Map<String, dynamic>)];
  }
}
