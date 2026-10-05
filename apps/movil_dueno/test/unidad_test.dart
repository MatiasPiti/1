import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/api/modelos.dart';
import 'package:panel_dueno/formato.dart';
import 'package:panel_dueno/pantallas/modo_lector.dart';

void main() {
  setUpAll(() => initializeDateFormatting('es_AR'));

  group('formato', () {
    test('moneda en pesos argentinos', () {
      expect(moneda(2500), r'$ 2.500');
      expect(moneda(2450.5), r'$ 2.450,50');
      expect(moneda(151300), r'$ 151.300');
    });

    test('parsearNumero acepta formatos es-AR y en-US', () {
      expect(parsearNumero('2500'), 2500);
      expect(parsearNumero('2.500'), 2500);
      expect(parsearNumero('2.500,50'), 2500.5);
      expect(parsearNumero('2500,50'), 2500.5);
      expect(parsearNumero('2500.50'), 2500.5);
      expect(parsearNumero(r'$ 1.234'), 1234);
      expect(parsearNumero('-5'), -5);
      expect(parsearNumero(''), isNull);
      expect(parsearNumero('abc'), isNull);
    });

    test('inicial del día de la semana', () {
      expect(inicialDia('2026-10-05'), 'L');
      expect(inicialDia('2026-10-04'), 'D');
    });
  });

  group('normalizarServidor', () {
    test('agrega http y el puerto por defecto', () {
      expect(normalizarServidor('100.101.102.103'), 'http://100.101.102.103:8765');
      expect(normalizarServidor(' pc-local.tu-red.ts.net/ '), 'http://pc-local.tu-red.ts.net:8765');
    });

    test('respeta puerto y esquema explícitos', () {
      expect(normalizarServidor('100.1.2.3:9000'), 'http://100.1.2.3:9000');
      expect(normalizarServidor('https://pc.tu-red.ts.net'), 'https://pc.tu-red.ts.net');
    });

    test('una IPv6 sin esquema va entre corchetes', () {
      expect(normalizarServidor('fd7a:115c:a1e0::5f01:abcd'), 'http://[fd7a:115c:a1e0::5f01:abcd]:8765');
      expect(normalizarServidor('[fd7a:115c:a1e0::1]'), 'http://[fd7a:115c:a1e0::1]:8765');
      expect(normalizarServidor('[fd7a:115c:a1e0::1]:9000'), 'http://[fd7a:115c:a1e0::1]:9000');
    });
  });

  group('FiltroLecturas (modo lector)', () {
    final t0 = DateTime(2026, 10, 4, 12);

    test('un producto quieto frente a la cámara cuenta una sola vez', () {
      final f = FiltroLecturas();
      expect(f.esNueva('779', t0), isTrue);
      for (var ms = 250; ms <= 5000; ms += 250) {
        expect(f.esNueva('779', t0.add(Duration(milliseconds: ms))), isFalse, reason: 'a los $ms ms');
      }
    });

    test('vuelve a contar si el código salió de cuadro', () {
      final f = FiltroLecturas();
      expect(f.esNueva('779', t0), isTrue);
      expect(f.esNueva('779', t0.add(const Duration(seconds: 2))), isTrue);
    });

    test('un código distinto cuenta enseguida', () {
      final f = FiltroLecturas();
      expect(f.esNueva('779', t0), isTrue);
      expect(f.esNueva('123', t0.add(const Duration(milliseconds: 100))), isTrue);
    });

    test('dos códigos quietos en el recuadro cuentan una vez cada uno', () {
      // la cámara a veces reporta uno, a veces el otro: antes alternaba y descontaba en loop
      final f = FiltroLecturas();
      final contados = <String>[];
      for (var ms = 0; ms <= 5000; ms += 125) {
        final codigo = (ms ~/ 125).isEven ? '779' : '123';
        if (f.esNueva(codigo, t0.add(Duration(milliseconds: ms)))) contados.add(codigo);
      }
      expect(contados, ['779', '123']);

      // y si vienen los dos en la misma captura, igual
      final g = FiltroLecturas();
      expect(g.nuevas(['779', '123'], t0), ['779', '123']);
      for (var ms = 250; ms <= 5000; ms += 250) {
        expect(g.nuevas(['123', '779'], t0.add(Duration(milliseconds: ms))), isEmpty, reason: 'a los $ms ms');
      }
    });

    test('un código que sale de cuadro y vuelve a los 3 s cuenta dos veces', () {
      final f = FiltroLecturas();
      final contados = <String>[];
      void cuadro(int ms, List<String> codigos) =>
          contados.addAll(f.nuevas(codigos, t0.add(Duration(milliseconds: ms))));
      cuadro(0, ['779']);
      cuadro(250, ['779', '123']);
      for (var ms = 500; ms < 3500; ms += 250) {
        cuadro(ms, ['123']); // 779 salió de cuadro; 123 sigue quieto
      }
      cuadro(3500, ['123', '779']); // 779 vuelve 3 s después
      expect(contados, ['779', '123', '779']);
    });
  });

  group('ColaLecturas (modo lector)', () {
    final t0 = DateTime(2026, 10, 4, 12);

    test('un código nuevo que aparece mientras se procesa otro se cuenta al terminar', () async {
      var ahora = t0;
      final procesados = <String>[];
      final respuesta = Completer<void>();
      final cola = ColaLecturas(
        reloj: () => ahora,
        procesar: (codigo, sumar) async {
          procesados.add(codigo);
          if (codigo == '779') await respuesta.future; // la PC tarda en contestar
        },
      );
      cola.detectados(['779'], sumar: false);
      expect(cola.procesando, '779');

      ahora = t0.add(const Duration(milliseconds: 200));
      cola.detectados(['779', '123'], sumar: false); // 123 pasa un instante por el recuadro
      ahora = t0.add(const Duration(milliseconds: 400));
      cola.detectados(['779'], sumar: false);
      ahora = t0.add(const Duration(seconds: 3));
      cola.detectados(['123'], sumar: false); // vuelve a pasar: ya está en la cola, no se repite
      expect(procesados, ['779']);

      respuesta.complete();
      await pumpEventQueue();
      expect(procesados, ['779', '123']);
      expect(cola.procesando, isNull);
    });

    test('cada lectura usa el modo que estaba elegido cuando se leyó', () async {
      final procesados = <String>[];
      final respuesta = Completer<void>();
      final cola = ColaLecturas(
        reloj: () => t0,
        procesar: (codigo, sumar) async {
          procesados.add('$codigo ${sumar ? '+1' : '-1'}');
          if (codigo == '779') await respuesta.future;
        },
      );
      cola.detectados(['779'], sumar: false);
      cola.detectados(['123'], sumar: false); // queda en la cola como "restar"
      cola.manual('456', sumar: true); // Leo cambió a "sumar" y tipeó otro
      respuesta.complete();
      await pumpEventQueue();
      expect(procesados, ['779 -1', '123 -1', '456 +1']);
    });

    test('al cerrar la pantalla lo pendiente no se procesa', () async {
      final procesados = <String>[];
      final respuesta = Completer<void>();
      final cola = ColaLecturas(
        reloj: () => t0,
        procesar: (codigo, sumar) async {
          procesados.add(codigo);
          await respuesta.future;
        },
      );
      cola.detectados(['779', '123'], sumar: false);
      cola.cerrar();
      respuesta.complete();
      await pumpEventQueue();
      expect(procesados, ['779']);
    });
  });

  group('modelos', () {
    Producto p({int stock = 10, int min = 5, int max = 0, double compra = 0}) => Producto(
        codigo: '1', nombre: 'X', precioVenta: 150, precioCompra: compra,
        stock: stock, stockMinimo: min, stockMaximo: max);

    test('estado de stock igual que Telegram', () {
      expect(p(stock: 5).estado, EstadoStock.bajo);
      expect(p(stock: 6).estado, EstadoStock.ok);
      expect(p(stock: 40, max: 40).estado, EstadoStock.sobre);
      expect(p(stock: 0, min: 0).estado, EstadoStock.ok); // 0 = sin mínimo
    });

    test('margen sobre el costo', () {
      expect(p(compra: 100).margen, closeTo(50, 0.001));
      expect(p().margen, isNull);
    });

    test('ítems de factura: no existentes y repetidos arrancan destildados', () {
      final ok = ItemFactura(codigo: 'a', nombre: '', cantidad: 1, precioCompra: null, existe: true);
      final noExiste = ItemFactura(codigo: 'b', nombre: '', cantidad: 1, precioCompra: null, existe: false);
      final repetido = ItemFactura(codigo: 'a', nombre: '', cantidad: 1, precioCompra: null, existe: true, posibleDuplicado: true);
      expect([ok.seleccionado, noExiste.seleccionado, repetido.seleccionado], [true, false, false]);
    });

    test('ítems de factura con cantidad fuera de rango arrancan destildados', () {
      ItemFactura item(int cantidad) =>
          ItemFactura(codigo: 'a', nombre: '', cantidad: cantidad, precioCompra: null, existe: true);
      expect([for (final c in [0, -3, 1, 100000, 100001]) item(c).seleccionado], [false, false, true, true, false]);
      expect(item(0).cantidadValida, isFalse);
      expect(ItemFactura.fromJson({'codigo': 'a', 'cantidad': 0, 'existe': true}).seleccionado, isFalse);
    });
  });
}
