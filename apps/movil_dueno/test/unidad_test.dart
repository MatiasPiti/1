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
      expect(f.esNueva('779', t0.add(const Duration(milliseconds: 200))), isTrue);
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
  });
}
