import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/api/modelos.dart';
import 'package:panel_dueno/formato.dart';
import 'package:panel_dueno/pantallas/ajustes.dart';
import 'package:panel_dueno/pantallas/alertas.dart';
import 'package:panel_dueno/pantallas/modo_lector.dart';
import 'package:panel_dueno/pantallas/precios.dart';
import 'package:panel_dueno/pantallas/stock.dart';

import 'servidor_falso.dart';

void main() {
  setUpAll(() => initializeDateFormatting('es_AR'));

  group('textos que ve Leo', () {
    test('aviso de ventas pendientes: con 12 cargadas y 6 sin descontar, va a quedar en 6', () {
      final r = ResultadoStock(
          codigo: '7790003', nombre: 'Coca', stockNuevo: 12,
          ventasPendientes: const VentasPendientes(lineas: 3, unidades: 6));
      expect(
          avisoVentasPendientes(r),
          'Ojo: hay 6 unidad(es) vendida(s) de este producto que el sistema todavía no descontó. En unos segundos '
          'el stock va a quedar en 6. Si lo que cargaste es un conteo (lo que hay en la góndola), sumale esas 6.');
      expect(avisoVentasPendientesCorto(r), contains('va a quedar en 6'));
      expect(avisoVentasPendientes(ResultadoStock(codigo: 'x', nombre: '', stockNuevo: 12)), isNull);
      final corto = ResultadoStock(
          codigo: 'x', nombre: '', stockNuevo: 2, ventasPendientes: const VentasPendientes(lineas: 1, unidades: 6));
      expect(corto.stockDespuesDePendientes, 0, reason: 'nunca negativo');
    });

    test('los precios se escriben como los lee la PC ("3.800", "2.479,34", "44,63")', () {
      expect(textoPrecio(3800), '3.800');
      expect(textoPrecio(2479.34), '2.479,34');
      expect(textoPrecio(44.63), '44,63');
      expect(textoPrecio(57.02), '57,02');
      expect(textoPrecio(0.5), '0,5');
      expect(textoPrecio(null), '');
      for (final v in [3800.0, 2479.34, 44.63, 1000000.0, 1234.5, -10.5]) {
        expect(parsearNumero(textoPrecio(v)), v, reason: 'ida y vuelta de $v');
      }
    });

    test('resumen del ajuste masivo', () {
      CambioPrecio ok(String c) => CambioPrecio(codigo: c, ok: true, anterior: 100, nuevo: 200);
      CambioPrecio cambio(String c) =>
          CambioPrecio(codigo: c, ok: false, error: 'el precio cambió mientras tanto (era 100.0, ahora es 200.0): no se tocó');
      expect(resumenAjuste([ok('a'), ok('b')]), 'Listo: 2 precios actualizados.');
      expect(resumenAjuste([cambio('a'), cambio('b')]), 'Ninguno cambió: ya tenían otro precio (¿se había aplicado antes?).');
      expect(resumenAjuste([ok('a'), cambio('b')]),
          'Listo: 1 precios actualizados. 1 no se tocaron porque su precio cambió mientras tanto.');
    });

    test('umbral global: el mismo texto que el Panel', () {
      expect(detalleUmbralGlobal(0, 0), 'No va a llegar ninguna alerta de stock.');
      expect(detalleUmbralGlobal(0, 20), 'Solo va a avisar por sobre-stock (a partir de 20). Por stock bajo, no.');
      expect(detalleUmbralGlobal(5, 0),
          'Solo va a avisar por stock bajo (cuando quede en 5 o menos). Por sobre-stock, no.');
      expect(detalleUmbralGlobal(5, 20), 'Va a avisar cuando un producto quede en 5 o menos, y cuando llegue a 20 o más.');
      expect(textoUmbralesPropios(0, 0), isNull);
      expect(textoUmbralesPropios(41, 0), '41 productos tienen umbral propio: a esos el global no les cambia nada');
      expect(textoUmbralesPropios(41, 2),
          '41 productos tienen umbral propio: a esos el global no les cambia nada (2 de esos están apagados y no avisan)');
    });

    test('cuándo avisó el bot', () {
      final ahora = DateTime(2026, 10, 6, 22, 0);
      final a = Alerta.fromJson((ejemplo('alertas') as List).first as Map<String, dynamic>);
      expect(textoAvisos(a, ahora), 'avisado hace 3 h · próximo aviso desde 23:00');
      expect(hace('2026-10-06T21:55:00', ahora), 'hace 5 min');
      expect(hace('2026-10-04T21:00:00', ahora), 'hace 2 días');
      expect(horaCorta('2026-10-07T01:30:00', ahora), '07/10 01:30');
      final nunca = Alerta.fromJson((ejemplo('alertas') as List).last as Map<String, dynamic>);
      expect(textoAvisos(nunca, ahora), isNull);
    });

    test('la IP de la PC sale tapada', () {
      expect(ipTapada('http://100.101.102.103:8766'), '100.•••.•••.•••');
      expect(ipTapada('http://100.101.102.103:8766'), isNot(contains('101')));
    });
  });

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
    test('agrega http y el puerto por defecto (8766, el de la API del celular)', () {
      expect(puertoPorDefecto, 8766);
      expect(normalizarServidor('100.101.102.103'), 'http://100.101.102.103:8766');
      expect(normalizarServidor(' pc-local.tu-red.ts.net/ '), 'http://pc-local.tu-red.ts.net:8766');
    });

    test('respeta puerto y esquema explícitos', () {
      expect(normalizarServidor('100.1.2.3:9000'), 'http://100.1.2.3:9000');
      expect(normalizarServidor('https://pc.tu-red.ts.net'), 'https://pc.tu-red.ts.net');
    });

    test('una IPv6 sin esquema va entre corchetes', () {
      expect(normalizarServidor('fd7a:115c:a1e0::5f01:abcd'), 'http://[fd7a:115c:a1e0::5f01:abcd]:8766');
      expect(normalizarServidor('[fd7a:115c:a1e0::1]'), 'http://[fd7a:115c:a1e0::1]:8766');
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

    test('margen: el % de ganancia guardado y, si no hay, el que sale del costo', () {
      expect(p(compra: 100).margen, closeTo(50, 0.001));
      expect(p().margen, isNull);
      final guardado = Producto(codigo: '1', nombre: 'X', precioVenta: 3500, precioCompra: 2420, stock: 1,
          stockMinimo: 0, stockMaximo: 0, margenGanancia: 44.63);
      expect(guardado.margen, 44.63, reason: 'el que muestra el Panel, no uno recalculado (daría 44,63 por casualidad o no)');
      final otro = Producto(codigo: '1', nombre: 'X', precioVenta: 3000, precioCompra: 2000, stock: 1,
          stockMinimo: 0, stockMaximo: 0, margenGanancia: 40);
      expect(otro.margen, 40, reason: 'con costo y precio daría 50: manda el guardado');
    });

    test('precio efectivo y oferta', () {
      final cafe = Producto.fromJson((ejemplo('productos') as List).first as Map<String, dynamic>);
      expect([cafe.precioVenta, cafe.precioEfectivo, cafe.enOferta], [4300.0, 3870.0, true]);
      expect(cafe.oferta!.hasta, '09/10');
      expect(p().precioEfectivo, 150, reason: 'sin el dato, el de lista');
    });

    test('ítems de factura: no existentes y repetidos arrancan destildados', () {
      final ok = ItemFactura(codigo: 'a', nombre: '', cantidad: 1, precioCompra: null, existe: true);
      final noExiste = ItemFactura(codigo: 'b', nombre: '', cantidad: 1, precioCompra: null, existe: false);
      final repetido = ItemFactura(codigo: 'a', nombre: '', cantidad: 1, precioCompra: null, existe: true, posibleDuplicado: true);
      expect([ok.seleccionado, noExiste.seleccionado, repetido.seleccionado], [true, false, false]);
    });

    test('un ítem emparejado por nombre NO se tilda solo, aunque sea SEGURA', () {
      final items = [
        for (final i in (ejemplo('factura_analizar') as Map)['items'] as List)
          ItemFactura.fromJson(i as Map<String, dynamic>),
      ];
      final segura = items.firstWhere((i) => i.emparejamiento?.confianza == 'SEGURA');
      expect(segura.seleccionado, isFalse);
      expect(segura.emparejamiento!.candidatosOrdenados.first.codigo, '7790003', reason: 'el sugerido primero');
      final demasiados = items.firstWhere((i) => i.emparejamiento?.demasiados ?? false);
      expect(demasiados.seleccionado, isFalse);
      expect([for (final i in items) i.seleccionado], [true, false, false, false, false],
          reason: 'solo el café con código; el repetido tampoco');

      // Leo lo elige: recién ahí queda confirmado y tildado
      segura.confirmarProducto(
          Producto(codigo: '7790003', nombre: 'Coca Cola 2,25 L', precioVenta: 2500, precioCompra: 1200, stock: 0,
              stockMinimo: 0, stockMaximo: 0),
          porNombre: true);
      expect([segura.codigo, segura.existe, segura.seleccionado, segura.emparejadoPorNombre],
          ['7790003', true, true, true]);
      expect(segura.precioSospechoso, isFalse, reason: '1250 contra 1200 guardado');
      expect(segura.actualizarCosto, isTrue);
    });

    test('precio sospechoso: la casilla de actualizar el costo arranca destildada', () {
      final item = ItemFactura.fromJson({
        'codigo': '7790001', 'cantidad': 4, 'precio_compra': 3.5, 'existe': true, 'precio_compra_actual': 2420.0,
        'precio_sospechoso': true,
      });
      expect(item.actualizarCosto, isFalse);
      expect(item.toJson(), {'codigo': '7790001', 'cantidad': 4, 'precio_compra': null},
          reason: 'sin la casilla no se pisa el costo');
      item.actualizarCosto = true;
      expect(item.toJson()['precio_compra'], 3.5);
      // la misma regla que la PC (panel_celular.es_precio_sospechoso)
      expect(ItemFactura.esPrecioSospechoso(3.5, 2420), isTrue);
      expect(ItemFactura.esPrecioSospechoso(3000, 2900), isFalse);
      expect(ItemFactura.esPrecioSospechoso(3.5, null), isTrue);
      expect(ItemFactura.esPrecioSospechoso(null, 100), isFalse);
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
