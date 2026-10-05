import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:panel_dueno/api/cliente_api.dart';

http.Response json(Object cuerpo, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(cuerpo)), status, headers: {'content-type': 'application/json'});

void main() {
  test('login manda el PIN y devuelve la sesión', () async {
    late http.Request enviado;
    final api = ClienteApi(
      servidor: '100.1.2.3',
      client: MockClient((r) async {
        enviado = r;
        return json({'token': 'tok', 'usuario': 'dueño', 'expira': 2000000000});
      }),
    );
    final s = await api.login('1234');
    expect(enviado.url.toString(), 'http://100.1.2.3:8765/api/auth/login');
    expect(jsonDecode(enviado.body), {'pin': '1234'});
    expect(s.usuario, 'dueño');
    expect(s.token, 'tok');
  });

  test('manda el token y los parámetros de búsqueda', () async {
    late http.Request enviado;
    final api = ClienteApi(
      servidor: 'http://pc:8765',
      token: 'abc',
      client: MockClient((r) async {
        enviado = r;
        return json([
          {'codigo': '779', 'nombre': 'Café', 'precio_venta': 4300, 'precio_compra': 0, 'stock': 3,
           'stock_minimo': 5, 'stock_maximo': 0},
        ]);
      }),
    );
    final productos = await api.productos(campo: 'marca', valor: 'Colombia');
    expect(enviado.headers['Authorization'], 'Bearer abc');
    expect(enviado.url.queryParameters, {'campo': 'marca', 'valor': 'Colombia'});
    expect(productos.single.precioVenta, 4300.0);
  });

  test('los errores muestran el mensaje del servidor', () async {
    final api = ClienteApi(
      servidor: 'pc',
      client: MockClient((_) async => json({'detail': "Stock insuficiente para '779'"}, 409)),
    );
    await expectLater(
      api.movimiento('779', 99, sumar: false),
      throwsA(isA<ApiError>().having((e) => e.mensaje, 'mensaje', contains('Stock insuficiente')).having((e) => e.status, 'status', 409)),
    );
  });

  test('errores de validación (422) se vuelven legibles', () async {
    final api = ClienteApi(
      servidor: 'pc',
      client: MockClient((_) async => json({
            'detail': [{'msg': 'Value error, Indicá porcentaje o monto_fijo (uno solo)'}]
          }, 422)),
    );
    await expectLater(api.aplicarPrecios(['1']),
        throwsA(isA<ApiError>().having((e) => e.mensaje, 'mensaje', 'Indicá porcentaje o monto_fijo (uno solo)')));
  });

  test('un 401 avisa que venció la sesión', () async {
    var avisos = 0;
    final api = ClienteApi(servidor: 'pc', client: MockClient((_) async => json({'detail': 'Sesión vencida'}, 401)))
      ..alVencerSesion = () => avisos++;
    await expectLater(api.dashboard(), throwsA(isA<ApiError>().having((e) => e.sesionVencida, 'vencida', isTrue)));
    expect(avisos, 1);
  });

  test('sin conexión da un mensaje claro', () async {
    final api = ClienteApi(servidor: 'pc', client: MockClient((_) async => throw http.ClientException('falló')));
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
      servidor: 'pc',
      token: 't',
      client: MockClient((r) async {
        enviado = r;
        return json({
          'factura_nombre': 'remito.pdf', 'es_pdf_escaneado': false, 'lineas_no_reconocidas': ['?'],
          'items': [
            {'codigo': '779', 'nombre': 'YERBA', 'cantidad': 12, 'precio_compra': 1800.0, 'existe': true,
             'nombre_sistema': 'Yerba', 'stock_actual': 20, 'posible_duplicado': false},
          ],
        });
      }),
    );
    final f = await api.analizarFactura([0x25, 0x50, 0x44, 0x46], 'remito.pdf');
    expect(enviado.headers['Authorization'], 'Bearer t');
    expect(enviado.headers['content-type'], startsWith('multipart/form-data'));
    expect(f.items.single.cantidad, 12);
    expect(f.items.single.seleccionado, isTrue);
    expect(f.noReconocidas, ['?']);
  });

  test('productos manda el límite pedido', () async {
    late http.Request enviado;
    final api = ClienteApi(servidor: 'pc', token: 't', client: MockClient((r) async {
      enviado = r;
      return json([]);
    }));
    await api.productos(q: 'yerba', limite: 2000);
    expect(enviado.url.queryParameters, {'q': 'yerba', 'limite': '2000'});
  });

  group('cortes y timeouts', () {
    // un pedido que la PC nunca contesta
    final colgado = MockClient((_) => Completer<http.Response>().future);
    const corto = Duration(milliseconds: 20);

    test('por defecto: 20 s para leer y 60 s para escribir', () {
      final api = ClienteApi(servidor: 'pc');
      expect(api.timeoutLectura, const Duration(seconds: 20));
      expect(api.timeoutEscritura, const Duration(seconds: 60));
    });

    test('una lectura que tarda invita a probar de nuevo', () async {
      final api = ClienteApi(servidor: 'pc', client: colgado, timeoutLectura: corto);
      await expectLater(
        api.dashboard(),
        throwsA(isA<ApiError>()
            .having((e) => e.incierto, 'incierto', isFalse)
            .having((e) => e.mensaje, 'mensaje', contains('Probá de nuevo'))),
      );
    });

    for (final (nombre, escribir) in <(String, Future<Object?> Function(ClienteApi))>[
      ('un movimiento de stock', (api) => api.movimiento('779', 3, sumar: true)),
      ('una lectura del lector', (api) => api.lector('779')),
      ('un precio', (api) => api.fijarPrecio('779', 2500)),
      ('un ajuste masivo', (api) => api.aplicarPrecios(['779'], porcentaje: 3)),
      ('una factura', (api) => api.aplicarFactura('remito.pdf', [])),
      ('la config de alertas', (api) => api.guardarUmbrales(5, 0)),
    ]) {
      test('si $nombre se queda sin respuesta, no se sabe si se aplicó', () async {
        final api = ClienteApi(servidor: 'pc', client: colgado, timeoutLectura: corto, timeoutEscritura: corto);
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
      final api = ClienteApi(servidor: 'pc', client: lenta, timeoutLectura: corto,
          timeoutEscritura: const Duration(seconds: 5));
      expect((await api.movimiento('779', 3, sumar: true)).stockNuevo, 6);
    });

    test('un corte en medio de una escritura también es incierto', () async {
      final api = ClienteApi(servidor: 'pc', client: MockClient((_) async => throw http.ClientException('Connection reset')));
      await expectLater(api.movimiento('779', 3, sumar: true),
          throwsA(isA<ApiError>().having((e) => e.incierto, 'incierto', isTrue)));
    });

    test('un corte en la vista previa (no escribe) se puede reintentar', () async {
      final api = ClienteApi(servidor: 'pc', client: MockClient((_) async => throw http.ClientException('Connection reset')));
      await expectLater(api.previsualizarPrecios(['779'], porcentaje: 3),
          throwsA(isA<ApiError>()
              .having((e) => e.incierto, 'incierto', isFalse)
              .having((e) => e.mensaje, 'mensaje', contains('Tailscale'))));
    });
  });

  group('dirección inválida', () {
    final nuncaLlamado = MockClient((_) async => fail('no debería mandar nada'));
    for (final direccion in ['fd7a::zz', 'http://fd7a::1', 'http://[::1']) {
      test('"$direccion" da un error claro en vez de romper', () async {
        final api = ClienteApi(servidor: direccion, client: nuncaLlamado);
        final esDireccionInvalida =
            throwsA(isA<ApiError>().having((e) => e.mensaje, 'mensaje', 'La dirección de la PC no es válida'));
        await expectLater(api.salud(), esDireccionInvalida);
        await expectLater(api.login('1234'), esDireccionInvalida);
        await expectLater(api.analizarFactura([1, 2, 3], 'remito.pdf'), esDireccionInvalida);
      });
    }
  });
}
