import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/estado.dart';

http.Response respuesta(Object cuerpo, [int status = 200]) =>
    http.Response.bytes(utf8.encode(jsonEncode(cuerpo)), status, headers: {'content-type': 'application/json'});

/// Imita a services/api_dueno.py con un par de productos en memoria.
class ServidorFalso {
  int stockCafe = 3;
  final pedidos = <String>[];

  Map<String, dynamic> get _cafe => {
        'codigo': '7790002', 'nombre': 'Café Molido 500g', 'precio_venta': 4300, 'precio_compra': 3000,
        'stock': stockCafe, 'stock_minimo': 5, 'stock_maximo': 0, 'marca': 'Colombia',
      };

  late final http.Client cliente = MockClient((r) async {
    pedidos.add('${r.method} ${r.url.path}');
    final autenticado = r.headers['Authorization'] == 'Bearer token-ok';
    switch (r.url.path) {
      case '/api/salud':
        return respuesta({'ok': true, 'nombre_local': 'Almacén de Leo', 'version_api': '1.0.0'});
      case '/api/auth/login':
        final pin = (jsonDecode(r.body) as Map)['pin'];
        return pin == '1234'
            ? respuesta({'token': 'token-ok', 'usuario': 'dueño', 'expira': 4102444800})
            : respuesta({'detail': 'PIN incorrecto'}, 401);
    }
    if (!autenticado) return respuesta({'detail': 'Sesión vencida o inválida'}, 401);
    switch (r.url.path) {
      case '/api/dashboard':
        return respuesta({
          'hoy': {
            'total': 152300, 'tickets': 41, 'ticket_promedio': 3714.6,
            'por_metodo': [{'metodo_pago': 'EFECTIVO', 'tickets': 30, 'total': 100000}],
          },
          'ultimos_7_dias': [
            for (var i = 0; i < 7; i++)
              {
                'dia': DateTime(2026, 9, 28).add(Duration(days: i)).toIso8601String().substring(0, 10),
                'total': 1000.0 * i,
                'tickets': i,
              },
          ],
          'top_productos': [{'codigo': '7790001', 'nombre': 'Yerba Mate 1kg', 'cantidad': 18, 'importe': 45000}],
          'alertas_activas': 1,
        });
      case '/api/productos':
        return respuesta([_cafe]);
      case '/api/movimientos':
        return respuesta([]);
      case '/api/stock/movimiento':
        final datos = jsonDecode(r.body) as Map;
        final cantidad = datos['cantidad'] as int;
        stockCafe += datos['operacion'] == 'sumar' ? cantidad : -cantidad;
        return respuesta({'codigo': '7790002', 'nombre': 'Café Molido 500g', 'stock_nuevo': stockCafe});
      case '/api/alertas':
        return respuesta([
          {'codigo': '7790002', 'nombre': 'Café Molido 500g', 'stock': stockCafe, 'stock_minimo': 5,
           'stock_maximo': 0, 'tipo': 'BAJO'},
        ]);
      case '/api/config/alertas':
        return respuesta({
          'telegram': {'habilitado': true, 'chat_id_default': '123', 'token_configurado': true, 'token_mascara': '1234••••abcd'},
          'umbral_global': {'stock_minimo': 5, 'stock_maximo': 0},
        });
    }
    return respuesta({'detail': 'no encontrado'}, 404);
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
