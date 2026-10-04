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
}
