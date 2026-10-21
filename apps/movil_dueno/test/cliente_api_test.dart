import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/api/modelos.dart';

import 'servidor_falso.dart';

http.Response json(Object cuerpo, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(cuerpo)), status, headers: {'content-type': 'application/json'});

const pc = '100.101.102.103';

/// Un cliente que no puede tocar la red: si llega a mandar algo, falla.
final nuncaLlamado = MockClient((_) async => fail('no debería mandar nada'));

void main() {
  test('login manda el PIN al 8766 y devuelve la sesión', () async {
    late http.Request enviado;
    final api = ClienteApi(
      servidor: pc,
      client: MockClient((r) async {
        enviado = r;
        return json({'token': 'tok', 'usuario': 'dueño', 'expira': 2000000000});
      }),
    );
    final s = await api.login('482915');
    expect(enviado.url.toString(), 'http://100.101.102.103:8766/api/auth/login');
    expect(jsonDecode(enviado.body), {'pin': '482915'});
    expect(s.usuario, 'dueño');
    expect(s.token, 'tok');
  });

  test('manda el token y los parámetros de búsqueda', () async {
    late http.Request enviado;
    final api = ClienteApi(
      servidor: 'http://100.101.102.103:8766',
      token: 'abc',
      client: MockClient((r) async {
        enviado = r;
        return json(ejemplo('productos'));
      }),
    );
    final productos = await api.productos(campo: 'marca', valor: 'Colombia');
    expect(enviado.headers['Authorization'], 'Bearer abc');
    expect(enviado.url.queryParameters, {'campo': 'marca', 'valor': 'Colombia'});
    expect(productos.first.precioVenta, 4300.0);
    expect(productos.first.precioEfectivo, 3870.0, reason: 'lo que cobra hoy la Caja, con la oferta');
  });

  test('los errores traen el mensaje y el código del servidor', () async {
    final api = ClienteApi(servidor: pc, client: MockClient((_) async => json(ejemplo('error_409_stock'), 409)));
    await expectLater(
      api.movimiento('7790003', 1, sumar: false),
      throwsA(isA<ApiError>()
          .having((e) => e.mensaje, 'mensaje', contains('Stock insuficiente'))
          .having((e) => e.status, 'status', 409)
          .having((e) => e.codigo, 'codigo', 'stock_insuficiente')),
    );
  });

  test('los extras del error llegan a la pantalla', () async {
    Future<ApiError> error(Map<String, dynamic> cuerpo, int status) async {
      final api = ClienteApi(servidor: pc, client: MockClient((_) async => json(cuerpo, status)));
      try {
        await api.dashboard();
      } on ApiError catch (e) {
        return e;
      }
      fail('tenía que fallar');
    }

    final bloqueo = await error(
        {'detail': 'Demasiados intentos. Probá de nuevo en 5 min.', 'codigo': 'demasiados_intentos', 'reintentar_en_s': 300},
        429);
    expect([bloqueo.codigo, bloqueo.reintentarEnS], ['demasiados_intentos', 300]);
    final factura = await error(ejemplo('error_409_factura') as Map<String, dynamic>, 409);
    expect([factura.codigo, factura.haceMin, factura.renglonesIguales], ['factura_ya_aplicada', 3, 1]);
    final precio = await error(ejemplo('error_409_precio') as Map<String, dynamic>, 409);
    expect([precio.codigo, precio.campo], ['precio_cambio', 'precio_compra']);
  });

  test('errores de validación (422) se vuelven legibles', () async {
    final api = ClienteApi(
      servidor: pc,
      client: MockClient((_) async => json({
            'detail': [{'msg': 'Value error, Indicá porcentaje o monto_fijo (uno solo)'}]
          }, 422)),
    );
    await expectLater(api.aplicarPrecios(['1'], esperados: {'1': 100}),
        throwsA(isA<ApiError>().having((e) => e.mensaje, 'mensaje', 'Indicá porcentaje o monto_fijo (uno solo)')));
  });

  test('un 401 avisa que venció la sesión', () async {
    var avisos = 0;
    final api = ClienteApi(
        servidor: pc,
        client: MockClient((_) async =>
            json({'detail': 'Sesión vencida o inválida. Ingresá el PIN de nuevo.', 'codigo': 'sesion_invalida'}, 401)))
      ..alVencerSesion = () => avisos++;
    await expectLater(api.dashboard(), throwsA(isA<ApiError>().having((e) => e.sesionVencida, 'vencida', isTrue)));
    expect(avisos, 1);
  });

  test('sin conexión da un mensaje claro', () async {
    final api = ClienteApi(servidor: pc, client: MockClient((_) async => throw http.ClientException('falló')));
    await expectLater(
      api.salud(),
      throwsA(isA<ApiError>()
          .having((e) => e.sinConexion, 'sinConexion', isTrue)
          .having((e) => e.mensaje, 'mensaje', contains('Tailscale'))),
    );
  });

  test('analizar factura sube el PDF como multipart', () async {
    late http.BaseRequest enviado;
    final api = ClienteApi(
      servidor: pc,
      token: 't',
      client: MockClient((r) async {
        enviado = r;
        return json(ejemplo('factura_analizar'));
      }),
    );
    final f = await api.analizarFactura([0x25, 0x50, 0x44, 0x46], 'remito.pdf');
    expect(enviado.headers['Authorization'], 'Bearer t');
    expect(enviado.headers['content-type'], startsWith('multipart/form-data'));
    expect(f.items.first.cantidad, 12);
    expect(f.items.first.seleccionado, isTrue);
    expect(f.noReconocidas, ['Subtotal 12.000']);
  });

  test('productos manda el límite pedido', () async {
    late http.Request enviado;
    final api = ClienteApi(servidor: pc, token: 't', client: MockClient((r) async {
      enviado = r;
      return json([]);
    }));
    await api.productos(q: 'yerba', limite: 2000);
    expect(enviado.url.queryParameters, {'q': 'yerba', 'limite': '2000'});
  });

  test('movimientos y alertas mandan su límite (y el código, si se pide)', () async {
    final urls = <Uri>[];
    final api = ClienteApi(servidor: pc, token: 't', client: MockClient((r) async {
      urls.add(r.url);
      return json([]);
    }));
    await api.movimientos(limite: 15, codigo: '7790002');
    await api.alertas();
    expect(urls[0].queryParameters, {'limite': '15', 'codigo': '7790002'});
    expect(urls[1].queryParameters, {'limite': '1000'});
  });

  group('solo direcciones de Tailscale (100.64.0.0/10)', () {
    test('esDireccionTailscale', () {
      for (final (direccion, esperado) in [
        ('100.63.255.255', false),
        ('100.64.0.1', true),
        ('100.101.102.103', true),
        ('100.127.255.254', true),
        ('100.128.0.1', false),
        ('100.101.102.103:8766', true),
        ('http://100.101.102.103:8766', true),
        ('192.168.0.10', false),
        ('10.0.0.5', false),
        ('8.8.8.8', false),
        ('200.45.1.2', false),
        ('100.64.0.256', false),
        ('100.064.1.1', false),
        ('pc-local.tu-red.ts.net', false),
        ('fd7a:115c:a1e0::1', false),
        ('', false),
      ]) {
        expect(esDireccionTailscale(direccion), esperado, reason: direccion);
      }
    });

    for (final direccion in ['192.168.0.10', '8.8.8.8', 'pc-local.tu-red.ts.net', 'fd7a::zz', 'http://[::1']) {
      test('"$direccion" se rechaza sin hacer ningún pedido (ni el PIN ni el token salen)', () async {
        final api = ClienteApi(servidor: direccion, token: 'secreto', client: nuncaLlamado);
        final noTailscale = throwsA(isA<ApiError>()
            .having((e) => e.codigo, 'codigo', 'no_tailscale')
            .having((e) => e.mensaje, 'mensaje', mensajeNoTailscale)
            .having((e) => e.incierto, 'incierto', isFalse));
        await expectLater(api.salud(), noTailscale);
        await expectLater(api.login('482915'), noTailscale);
        await expectLater(api.dashboard(), noTailscale);
        await expectLater(api.movimiento('779', 1, sumar: true), noTailscale);
        await expectLater(api.analizarFactura([1, 2, 3], 'remito.pdf'), noTailscale);
      });
    }
  });

  group('la firma de /api/salud', () {
    test('la API del celular de Otter pasa', () async {
      final s = await ServidorFalso().crear(pc).salud();
      expect(s.servicio, firmaApi);
      expect(s.contrato, 3);
      expect(s.nombreLocal, 'El Galpón Del Nono');
    });

    test('el Dueño Remoto (8765) se reconoce y dice qué puerto usar', () async {
      final impostor = Impostor.remoteApi();
      await expectLater(
          impostor.crear(pc).salud(),
          throwsA(isA<ApiError>()
              .having((e) => e.codigo, 'codigo', 'puerto_equivocado')
              .having((e) => e.mensaje, 'mensaje', 'Ese es el puerto del Dueño Remoto (8765). La app usa el 8766.')));
    });

    test('el ApiDueno viejo (sin "servicio") es otro programa', () async {
      final impostor = Impostor.apiDuenoViejo();
      await expectLater(
          impostor.crear(pc).salud(),
          throwsA(isA<ApiError>()
              .having((e) => e.codigo, 'codigo', 'otro_programa')
              .having((e) => e.mensaje, 'mensaje',
                  'En esa dirección contesta otro programa, no la API del celular de Otter.')));
    });

    test('un programa cualquiera que contesta HTML también es otro programa', () async {
      final api = ClienteApi(
          servidor: pc, client: MockClient((_) async => http.Response('<html>Not Found</html>', 404)));
      await expectLater(api.salud(), throwsA(isA<ApiError>().having((e) => e.codigo, 'codigo', 'otro_programa')));
    });

    test('una ApiCelular con un contrato anterior pide actualizar la PC', () async {
      await expectLater(
          Impostor.contratoViejo().crear(pc).salud(),
          throwsA(isA<ApiError>()
              .having((e) => e.codigo, 'codigo', 'actualizar_pc')
              .having((e) => e.mensaje, 'mensaje', 'La PC del local tiene una versión vieja: hay que actualizarla.')));
    });
  });

  group('el 503 en texto plano (limit_concurrency de uvicorn)', () {
    final ocupada = MockClient((_) async => http.Response('Service Unavailable', 503));

    test('en una lectura dice que la PC está ocupada', () async {
      await expectLater(
          ClienteApi(servidor: pc, client: ocupada).dashboard(),
          throwsA(isA<ApiError>()
              .having((e) => e.codigo, 'codigo', 'ocupada')
              .having((e) => e.mensaje, 'mensaje', 'La PC está ocupada: probá en unos segundos.')));
    });

    test('en una escritura NO es incierto: uvicorn lo contesta sin pasarlo a la API', () async {
      await expectLater(
          ClienteApi(servidor: pc, client: ocupada).movimiento('779', 3, sumar: true),
          throwsA(isA<ApiError>()
              .having((e) => e.codigo, 'codigo', 'ocupada')
              .having((e) => e.incierto, 'incierto', isFalse)
              .having((e) => e.mensaje, 'mensaje', contains('La PC está ocupada'))));
    });
  });

  group('escrituras que se pueden repetir sin duplicar', () {
    test('guardarPrecios manda los 4 crudos SIN TOCAR como "esperado" (y null = no tocar)', () async {
      final servidor = ServidorFalso();
      final api = servidor.crear(pc)..token = ServidorFalso.token;
      final p = await api.precios('7790001');
      await api.guardarPrecios('7790001', const ValoresPrecio(precioFinal: 3800), esperado: p.crudos);
      final cuerpo = servidor.cuerpos['PUT /api/precios/7790001']!.single as Map;
      expect(cuerpo['esperado'], {'costo_sin_iva': 2000.0, 'precio_compra': 2420.0, 'margen_ganancia': 44.63, 'precio_venta': 3500.0});
      expect(cuerpo, containsPair('costo_sin_iva', null));
      expect(cuerpo, containsPair('precio_final', 3800.0));
    });

    test('aplicarPrecios manda "esperados" con las mismas claves que los códigos', () async {
      late Map enviado;
      final api = ClienteApi(servidor: pc, client: MockClient((r) async {
        enviado = jsonDecode(r.body) as Map;
        return json([]);
      }));
      await api.aplicarPrecios(['7790001', '7790002'], esperados: {'7790001': 3500, '7790002': 4300}, porcentaje: 10);
      expect((enviado['esperados'] as Map).keys.toSet(), (enviado['codigos'] as List).toSet());
      expect(enviado['esperados'], {'7790001': 3500, '7790002': 4300});
      expect(() => api.aplicarPrecios(['7790001'], esperados: {'7790002': 1}, porcentaje: 10), throwsArgumentError,
          reason: 'si no coinciden es un error de la app, no se manda nada');
    });

    test('el ajuste espera más cuantos más códigos (2000 códigos → 2 minutos)', () {
      final api = ClienteApi(servidor: pc);
      expect(api.timeoutAplicar(2000), const Duration(minutes: 2));
      expect(api.timeoutAplicar(0), api.timeoutEscritura);
    });

    test('aplicarFactura manda "forzar"', () async {
      final cuerpos = <Map>[];
      final api = ClienteApi(servidor: pc, client: MockClient((r) async {
        cuerpos.add(jsonDecode(r.body) as Map);
        return json([]);
      }));
      final item = ItemFactura(codigo: '779', nombre: '', cantidad: 2, precioCompra: 10, existe: true);
      await api.aplicarFactura('remito.pdf', [item]);
      await api.aplicarFactura('remito.pdf', [item], forzar: true);
      expect([cuerpos[0]['forzar'], cuerpos[1]['forzar']], [false, true]);
    });

    test('Telegram solo manda "habilitado"; quitar el umbral global es un DELETE', () async {
      final servidor = ServidorFalso();
      final api = servidor.crear(pc)..token = ServidorFalso.token;
      final c = await api.guardarTelegram(habilitado: false);
      expect(c.telegramHabilitado, isFalse);
      expect(servidor.cuerpos['PUT /api/config/telegram']!.single, {'habilitado': false});
      final u = await api.quitarUmbralGlobal();
      expect(servidor.pedidos.last, 'DELETE /api/config/umbral-global');
      expect([u.existe, u.borradas, u.umbralesPropios], [false, 1, 3]);
    });
  });

  test('el servidor falso rechaza, como la PC, un token o un chat mandados desde el celular', () async {
    // la app ya no puede mandarlos (guardarTelegram solo lleva "habilitado"); esto
    // cuida que el servidor falso no deje pasar una versión vieja del cliente
    final servidor = ServidorFalso();
    final r = await servidor.cliente.put(Uri.parse('${ServidorFalso.servidorNormalizado}/api/config/telegram'),
        headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ${ServidorFalso.token}'},
        body: jsonEncode({'habilitado': true, 'bot_token': '123:abc', 'chat_id_default': '999'}));
    expect(r.statusCode, 422);
    expect(jsonDecode(utf8.decode(r.bodyBytes)),
        {'detail': 'El token y el chat del bot se cargan en el Panel de la PC.', 'codigo': 'solo_en_la_pc'});
  });

  test('el cliente por defecto no reusa conexiones que la PC ya cerró (idleTimeout menor a 5 s)', () {
    final api = ClienteApi(servidor: pc);
    expect(api.httpInterno, isNotNull);
    expect(api.httpInterno!.idleTimeout, lessThan(const Duration(seconds: 5)));
    expect(ClienteApi(servidor: pc, client: nuncaLlamado).httpInterno, isNull);
  });

  group('cortes y timeouts', () {
    // un pedido que la PC nunca contesta
    final colgado = MockClient((_) => Completer<http.Response>().future);
    const corto = Duration(milliseconds: 20);

    test('por defecto: 20 s para leer y 60 s para escribir', () {
      final api = ClienteApi(servidor: pc);
      expect(api.timeoutLectura, const Duration(seconds: 20));
      expect(api.timeoutEscritura, const Duration(seconds: 60));
    });

    test('una lectura que tarda invita a probar de nuevo', () async {
      final api = ClienteApi(servidor: pc, client: colgado, timeoutLectura: corto);
      await expectLater(
        api.dashboard(),
        throwsA(isA<ApiError>()
            .having((e) => e.incierto, 'incierto', isFalse)
            .having((e) => e.mensaje, 'mensaje', contains('Probá de nuevo'))),
      );
    });

    const crudos = CrudosPrecio(costoSinIva: 0, precioCompra: 0, margenGanancia: 0, precioVenta: 2000);
    for (final (nombre, escribir) in <(String, Future<Object?> Function(ClienteApi))>[
      ('un movimiento de stock', (api) => api.movimiento('779', 3, sumar: true)),
      ('una lectura del lector', (api) => api.lector('779')),
      ('un precio', (api) => api.guardarPrecios('779', const ValoresPrecio(precioFinal: 2500), esperado: crudos)),
      ('un ajuste masivo', (api) => api.aplicarPrecios(['779'], esperados: {'779': 2000}, porcentaje: 3)),
      ('una factura', (api) => api.aplicarFactura('remito.pdf', [])),
      ('el umbral global', (api) => api.guardarUmbrales(5, 0)),
      ('quitar el umbral global', (api) => api.quitarUmbralGlobal()),
      ('prender o apagar Telegram', (api) => api.guardarTelegram(habilitado: true)),
    ]) {
      test('si $nombre se queda sin respuesta, no se sabe si se aplicó', () async {
        final api = ClienteApi(servidor: pc, client: colgado, timeoutLectura: corto, timeoutEscritura: corto);
        await expectLater(
          escribir(api),
          throwsA(isA<ApiError>()
              .having((e) => e.incierto, 'incierto', isTrue)
              .having((e) => e.mensaje, 'mensaje', contains('No se sabe si se aplicó'))
              .having((e) => e.mensaje, 'mensaje', isNot(contains('Probá de nuevo')))),
        );
      });
    }

    test('las escrituras esperan más que las lecturas', () async {
      final lenta = MockClient((_) async {
        await Future<void>.delayed(const Duration(milliseconds: 60));
        return json({'codigo': '779', 'nombre': 'Café', 'stock_nuevo': 6});
      });
      final api = ClienteApi(servidor: pc, client: lenta, timeoutLectura: corto,
          timeoutEscritura: const Duration(seconds: 5));
      expect((await api.movimiento('779', 3, sumar: true)).stockNuevo, 6);
    });

    test('un corte en medio de una escritura también es incierto', () async {
      final api = ClienteApi(servidor: pc, client: MockClient((_) async => throw http.ClientException('Connection reset')));
      await expectLater(api.movimiento('779', 3, sumar: true),
          throwsA(isA<ApiError>().having((e) => e.incierto, 'incierto', isTrue)));
    });

    for (final (nombre, leer) in <(String, Future<Object?> Function(ClienteApi))>[
      ('la vista previa', (api) => api.previsualizarPrecios(['779'], porcentaje: 3)),
      ('recalcular precios', (api) => api.recalcularPrecios(cambio: 'precio_final', precioFinal: '3.800')),
      ('el mensaje de prueba', (api) => api.probarTelegram()),
    ]) {
      test('un corte en $nombre (no escribe) se puede reintentar', () async {
        final api = ClienteApi(servidor: pc, client: MockClient((_) async => throw http.ClientException('Connection reset')));
        await expectLater(leer(api),
            throwsA(isA<ApiError>()
                .having((e) => e.incierto, 'incierto', isFalse)
                .having((e) => e.mensaje, 'mensaje', contains('Tailscale'))));
      });
    }
  });

  group('alta de productos y filtros (contrato 3)', () {
    ClienteApi conServidor(ServidorFalso s) => s.crear(pc)..token = ServidorFalso.token;

    test('una PC de contrato 2 sigue entrando; lo nuevo dice que hay que actualizarla', () async {
      final s = ServidorFalso()
        ..salud = {...ejemplo('salud') as Map<String, dynamic>, 'contrato': 2}
        ..contrato3 = false;
      final api = conServidor(s);
      expect((await api.salud()).contrato, 2);
      await expectLater(
          api.crearProducto(const ProductoNuevo(codigo: '779', nombre: 'X', precioVenta: 1)),
          throwsA(isA<ApiError>()
              .having((e) => e.pcVieja, 'pcVieja', isTrue)
              .having((e) => e.incierto, 'incierto', isFalse)));
      await expectLater(api.filtros(), throwsA(isA<ApiError>().having((e) => e.pcVieja, 'pcVieja', isTrue)));
    });

    test('el alta manda los 7 campos: recortados, vacío = null, y números de verdad (no "2.500")', () async {
      late http.Request enviado;
      final api = ClienteApi(
          servidor: pc,
          client: MockClient((r) async {
            enviado = r;
            return json(ejemplo('producto_alta', 1));
          }));
      await api.crearProducto(const ProductoNuevo(
          codigo: ' 7790101 ', nombre: ' Pan rallado 500g ', precioVenta: 2500.5, marca: '  ', categoria: 'ALMACEN'));
      expect(enviado.method, 'POST');
      expect(enviado.url.path, '/api/productos');
      final cuerpo = jsonDecode(enviado.body) as Map<String, dynamic>;
      expect(cuerpo, {
        'codigo': '7790101',
        'nombre': 'Pan rallado 500g',
        'precio_venta': 2500.5,
        'stock_inicial': 0,
        'marca': null,
        'proveedor': null,
        'categoria': 'ALMACEN',
      });
      expect(cuerpo['precio_venta'], isA<num>(), reason: 'la PC rechaza un precio en texto');
      expect(cuerpo['stock_inicial'], isA<int>(), reason: 'y un stock con coma');
    });

    test('409 producto_existe trae el producto, y esElMismo separa el reintento de otro producto', () async {
      final s = ServidorFalso();
      final api = conServidor(s);
      const mio = ProductoNuevo(codigo: '7790500', nombre: 'Leche 1L', precioVenta: 1250, stockInicial: 6);
      final creado = await api.crearProducto(mio);
      expect(creado.stock, 6);
      // el reintento después de un corte
      final repetido = await api.crearProducto(mio).then<ApiError?>((_) => null, onError: (Object e) => e as ApiError);
      expect(repetido?.codigo, 'producto_existe');
      expect(mio.esElMismo(repetido!.producto!), isTrue);
      // otro producto con el mismo código
      final otro = await api
          .crearProducto(const ProductoNuevo(codigo: '7790002', nombre: 'Otra cosa', precioVenta: 1))
          .then<ApiError?>((_) => null, onError: (Object e) => e as ApiError);
      expect(otro?.producto?.nombre, 'Café Molido 500g');
      expect(const ProductoNuevo(codigo: '7790002', nombre: 'Otra cosa', precioVenta: 1).esElMismo(otro!.producto!),
          isFalse);
      // uno dado de baja: 409 sin producto
      final deBaja = await api
          .crearProducto(const ProductoNuevo(codigo: '7790006', nombre: 'X', precioVenta: 1))
          .then<ApiError?>((_) => null, onError: (Object e) => e as ApiError);
      expect(deBaja?.codigo, 'producto_existe');
      expect(deBaja?.producto, isNull);
      expect(s.pedidos.where((p) => p == 'POST /api/productos'), hasLength(4));
    });

    test('el nombre de un filtro viaja codificado (espacios, barra, %, ¿?) y llega entero', () async {
      final s = ServidorFalso();
      final api = conServidor(s);
      const nombre = '¿Bebidas/Gaseosas 50%?';
      final creado = await api.crearFiltro(nombre, ['7790003', '7790002']);
      expect(creado.nombre, nombre);
      final f = await api.filtro(nombre);
      expect([for (final p in f.productos) p.codigo], ['7790003', '7790002']);
      expect(s.pedidos, contains('GET /api/filtros/%C2%BFBebidas%2FGaseosas%2050%25%3F'));
      final cambiado = await api.guardarFiltro(nombre, ['7790001'], huella: f.huella);
      await api.borrarFiltro(nombre, huella: cambiado.huella);
      expect(s.filtros, isEmpty);
    });

    test('cambiar o borrar con una huella vieja da filtro_cambio y no toca lo que guardó el Panel', () async {
      final s = ServidorFalso()..guardarFiltroPorFuera('Promo', ['7790002']);
      final api = conServidor(s);
      final cargado = await api.filtro('Promo');
      s.guardarFiltroPorFuera('Promo', ['7790001']); // lo guardaron en el Panel mientras tanto
      await expectLater(api.guardarFiltro('Promo', ['7790003'], huella: cargado.huella),
          throwsA(isA<ApiError>().having((e) => e.codigo, 'codigo', 'filtro_cambio')));
      await expectLater(api.borrarFiltro('Promo', huella: cargado.huella),
          throwsA(isA<ApiError>().having((e) => e.codigo, 'codigo', 'filtro_cambio')));
      expect(s.filtros['Promo']!.codigos, ['7790001']);
    });

    test('un corte al guardar un filtro o dar de alta es incierto: nunca se reintenta solo', () async {
      final s = ServidorFalso()..cortarEscrituras = true;
      final api = conServidor(s);
      await expectLater(api.crearFiltro('Promo', ['7790002']),
          throwsA(isA<ApiError>().having((e) => e.incierto, 'incierto', isTrue)));
      await expectLater(api.crearProducto(const ProductoNuevo(codigo: '7790500', nombre: 'X', precioVenta: 1)),
          throwsA(isA<ApiError>().having((e) => e.incierto, 'incierto', isTrue)));
      expect(s.pedidos.where((p) => p == 'POST /api/filtros'), hasLength(1));
      expect(s.pedidos.where((p) => p == 'POST /api/productos'), hasLength(1));
      expect(s.filtros.keys, ['Promo'], reason: 'la PC lo aplicó antes del corte');
    });
  });
}
