// El contrato de la API del celular, del lado de la app.
//
// test/fixtures/contrato_api_celular_v2.json lo escribe el backend y lo
// valida tests/test_api_celular_contrato.py contra la API de verdad. Acá se
// pasa CADA variante de CADA respuesta por el cliente real (ClienteApi →
// modelos.dart): si la API cambia una clave, un tipo o una ruta y nadie
// actualiza la app, esto falla en vez de fallar en el celular de Leo.
//
// Para cada ejemplo se mira que el cliente llame al mismo método y ruta, que
// mande el mismo cuerpo que el "pedido" del fixture, que el fromJson no
// lance, y que los campos que la app usa tengan el valor del fixture.

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/api/modelos.dart';

import 'servidor_falso.dart' show fixture;

Map<String, dynamic> get _ejemplos => fixture['ejemplos'] as Map<String, dynamic>;

double? _n(Object? v) => v == null ? null : (v as num).toDouble();

/// Cómo se llama cada endpoint desde la app, con los argumentos que producen
/// exactamente el "pedido" del ejemplo, y qué hay que mirar de lo parseado.
class _Caso {
  _Caso(this.llamar, [this.verificar]);
  final Future<Object?> Function(ClienteApi api) llamar;
  final void Function(Object? resultado, dynamic json)? verificar;
}

void _verificarProducto(Producto p, Map<String, dynamic> j) {
  expect(p.codigo, j['codigo']);
  expect(p.nombre, j['nombre']);
  expect(p.marca, j['marca']);
  expect(p.proveedor, j['proveedor']);
  expect(p.categoria, j['categoria']);
  expect(p.subrubro, j['subrubro']);
  expect(p.stock, j['stock']);
  expect(p.stockMinimo, j['stock_minimo']);
  expect(p.stockMaximo, j['stock_maximo']);
  expect(p.umbralPropio, j['umbral_propio']);
  expect(p.precioVenta, _n(j['precio_venta']));
  expect(p.precioEfectivo, _n(j['precio_efectivo']));
  expect(p.precioCompra, _n(j['precio_compra']));
  expect(p.costoSinIva, _n(j['costo_sin_iva']));
  expect(p.margenGanancia, _n(j['margen_ganancia']));
  expect(p.actualizadoEn, j['actualizado_en']);
  _verificarOferta(p.oferta, j['oferta']);
}

void _verificarOferta(Oferta? o, dynamic j) {
  if (j == null) {
    expect(o, isNull);
    return;
  }
  expect(o, isNotNull);
  expect(o!.id, j['id']);
  expect(o.tipoDescuento, j['tipo_descuento']);
  expect(o.valor, _n(j['valor']));
  expect(o.descripcion, j['descripcion']);
  expect(o.fechaFin, j['fecha_fin']);
}

void _verificarMovimiento(Movimiento m, Map<String, dynamic> j) {
  expect(m.fechaHora, j['fecha_hora']);
  expect(m.codigo, j['codigo']);
  expect(m.nombre, j['nombre'] ?? j['codigo'], reason: 'sin nombre se muestra el código');
  expect(m.tipo, j['tipo']);
  expect(m.cantidad, j['cantidad']);
  expect(m.stockResultante, j['stock_resultante']);
  expect(m.motivo, j['motivo']);
  expect(m.usuario, j['usuario']);
}

void _verificarResultadoStock(ResultadoStock r, Map<String, dynamic> j) {
  expect(r.codigo, j['codigo']);
  expect(r.nombre, j['nombre']);
  expect(r.stockNuevo, j['stock_nuevo']);
  expect(r.stockMinimo, j['stock_minimo']);
  expect(r.stockMaximo, j['stock_maximo']);
  expect(r.ventasPendientes.lineas, j['ventas_pendientes']['lineas']);
  expect(r.ventasPendientes.unidades, j['ventas_pendientes']['unidades']);
}

void _verificarValores(ValoresPrecio v, Map<String, dynamic> j) {
  expect(v.costoSinIva, _n(j['costo_sin_iva']));
  expect(v.precioCosto, _n(j['precio_costo']));
  expect(v.margen, _n(j['margen']));
  expect(v.precioFinal, _n(j['precio_final']));
}

void _verificarPrecios(PreciosProducto p, Map<String, dynamic> j) {
  expect(p.codigo, j['codigo']);
  expect(p.nombre, j['nombre']);
  expect(p.categoria, j['categoria']);
  expect(p.subrubro, j['subrubro']);
  expect(p.stock, j['stock']);
  _verificarValores(p.valores, j);
  expect(p.precioVenta, _n(j['precio_venta']));
  expect(p.precioEfectivo, _n(j['precio_efectivo']));
  expect(p.actualizadoEn, j['actualizado_en']);
  // los crudos vuelven tal cual como "esperado": misma forma y mismos números
  expect(p.crudos.toJson(), j['crudos']);
  _verificarOferta(p.oferta, j['oferta']);
}

void _verificarUmbrales(EstadoUmbrales u, Map<String, dynamic> j) {
  expect(u.stockMinimo, j['umbral_global']['stock_minimo']);
  expect(u.stockMaximo, j['umbral_global']['stock_maximo']);
  expect(u.existe, j['umbral_global']['existe']);
  expect(u.umbralesPropios, j['umbrales_propios']['total']);
  expect(u.umbralesInactivos, j['umbrales_propios']['inactivos']);
  expect(u.borradas, j['borradas']);
}

void _verificarConfig(ConfigAlertas c, Map<String, dynamic> j) {
  expect(c.telegramHabilitado, j['telegram']['habilitado']);
  expect(c.chatId, j['telegram']['chat_id_default']);
  expect(c.tokenConfigurado, j['telegram']['token_configurado']);
  expect(c.tokenMascara, j['telegram']['token_mascara']);
  expect(c.tokenMascara, anyOf('', '••••••••'), reason: 'el token viaja tapado ENTERO, nunca a medias');
  _verificarUmbrales(c.umbrales, j);
}

void _verificarCambios(List<CambioPrecio> r, List<dynamic> j) {
  expect(r, hasLength(j.length));
  for (var i = 0; i < r.length; i++) {
    final c = r[i], e = j[i] as Map<String, dynamic>;
    expect(c.codigo, e['codigo']);
    expect(c.ok, e['ok']);
    expect(c.nombre, e['nombre']);
    expect(c.anterior, _n(e['precio_anterior']));
    expect(c.nuevo, _n(e['precio_nuevo']));
    expect(c.error, e['error']);
    expect(c.quedaEnCero, e['queda_en_cero'] ?? false);
    expect(c.enOferta, e['en_oferta'] ?? false);
  }
}

void _verificarItem(ItemFactura i, Map<String, dynamic> j) {
  expect(i.indice, j['indice']);
  expect(i.codigo, j['codigo']);
  expect(i.nombre, j['nombre']);
  expect(i.cantidad, j['cantidad']);
  expect(i.precioCompra, _n(j['precio_compra']));
  expect(i.existe, j['existe']);
  expect(i.nombreSistema, j['nombre_sistema']);
  expect(i.stockActual, j['stock_actual']);
  expect(i.precioCompraActual, _n(j['precio_compra_actual']));
  expect(i.posibleDuplicado, j['posible_duplicado']);
  expect(i.precioSospechoso, j['precio_sospechoso']);
  expect(i.actualizarCosto, !(j['precio_sospechoso'] as bool), reason: 'con precio sospechoso arranca destildado');
  final emp = j['emparejamiento'];
  if (emp == null) {
    expect(i.emparejamiento, isNull);
  } else {
    final e = i.emparejamiento!;
    expect(e.motivo, emp['motivo']);
    expect(e.confianza, emp['confianza']);
    expect([for (final c in e.candidatos) [c.codigo, c.nombre, c.score]],
        [for (final c in emp['candidatos'] as List) [c['codigo'], c['nombre'], _n(c['score'])]]);
    expect(e.sugerido?.codigo, emp['sugerido']?['codigo']);
    expect(e.demasiados, emp['motivo'] == 'demasiados');
    // un renglón emparejado por nombre NUNCA arranca tildado, aunque sea SEGURA
    expect(i.seleccionado, isFalse);
  }
}

final _itemsFactura = [
  ItemFactura(codigo: '7790002', nombre: 'CAFE MOLIDO X 500', cantidad: 12, precioCompra: 3000, existe: true),
  ItemFactura(codigo: '7790999', nombre: '?', cantidad: 1, precioCompra: null, existe: true),
];

final _casos = <String, _Caso>{
  'salud': _Caso((api) => api.salud(), (r, j) {
    final s = r as Salud;
    expect(s.servicio, firmaApi);
    expect(s.contrato, j['contrato']);
    expect(s.versionApi, j['version_api']);
    expect(s.compilado, j['compilado']);
    expect(s.nombreLocal, j['nombre_local']);
    expect(s.pinConfigurado, j['pin_configurado']);
    expect(s.loginDisponible, j['login_disponible']);
    expect(s.motivo, j['motivo']);
    expect(s.baseOk, j['base']['ok']);
    expect(s.baseDetalle, j['base']['detalle']);
  }),
  'login': _Caso((api) => api.login('482915'), (r, j) {
    final s = r as Sesion;
    expect(s.token, j['token']);
    expect(s.usuario, j['usuario']);
    expect(s.expira.millisecondsSinceEpoch ~/ 1000, j['expira']);
  }),
  'yo': _Caso((api) => api.yo(), (r, j) => expect(r, j['usuario'])),
  'dashboard': _Caso((api) => api.dashboard(), (r, j) {
    final d = r as Dashboard;
    expect(d.totalHoy, _n(j['hoy']['total']));
    expect(d.ticketsHoy, j['hoy']['tickets']);
    expect(d.ticketPromedio, _n(j['hoy']['ticket_promedio']));
    expect([for (final m in d.porMetodo) [m.metodo, m.tickets, m.total]],
        [for (final m in j['hoy']['por_metodo'] as List) [m['metodo_pago'], m['tickets'], _n(m['total'])]]);
    expect(d.ultimos7Dias, hasLength(7));
    expect([for (final v in d.ultimos7Dias) [v.dia, v.total, v.tickets]],
        [for (final v in j['ultimos_7_dias'] as List) [v['dia'], _n(v['total']), v['tickets']]]);
    expect([for (final t in d.topProductos) [t.codigo, t.nombre, t.cantidad, t.importe]],
        [for (final t in j['top_productos'] as List) [t['codigo'], t['nombre'], t['cantidad'], _n(t['importe'])]]);
    expect(d.alertasActivas, j['alertas_activas']);
    expect(d.telegramHabilitado, j['telegram_habilitado']);
  }),
  'productos': _Caso((api) => api.productos(limite: 200), (r, j) {
    final lista = r as List<Producto>;
    expect(lista, hasLength((j as List).length));
    for (var i = 0; i < lista.length; i++) {
      _verificarProducto(lista[i], j[i] as Map<String, dynamic>);
    }
  }),
  'producto': _Caso((api) => api.producto('7790002'), (r, j) {
    _verificarProducto(r as Producto, j as Map<String, dynamic>);
    // los movimientos del detalle se leen con el mismo modelo
    for (final m in j['movimientos'] as List) {
      _verificarMovimiento(Movimiento.fromJson(m as Map<String, dynamic>), m);
    }
  }),
  'movimientos': _Caso((api) => api.movimientos(), (r, j) {
    final lista = r as List<Movimiento>;
    expect(lista, hasLength((j as List).length));
    for (var i = 0; i < lista.length; i++) {
      _verificarMovimiento(lista[i], j[i] as Map<String, dynamic>);
    }
  }),
  'stock_movimiento': _Caso((api) => api.movimiento('7790002', 12, sumar: true, motivo: 'Reposición'),
      (r, j) => _verificarResultadoStock(r as ResultadoStock, j as Map<String, dynamic>)),
  'stock_lector': _Caso((api) => api.lector('7790002'),
      (r, j) => _verificarResultadoStock(r as ResultadoStock, j as Map<String, dynamic>)),
  'precios_ver': _Caso((api) => api.precios('7790001'),
      (r, j) => _verificarPrecios(r as PreciosProducto, j as Map<String, dynamic>)),
  'precios_recalcular': _Caso(
      (api) => api.recalcularPrecios(
          cambio: 'precio_final', costoSinIva: 2000.0, precioCosto: 2420.0, margen: 44.63, precioFinal: '3.800'),
      (r, j) => _verificarValores(r as ValoresPrecio, j as Map<String, dynamic>)),
  'precios_guardar': _Caso(
      (api) => api.guardarPrecios(
          '7790001', const ValoresPrecio(costoSinIva: 2000, precioCosto: 2420, margen: 57.02, precioFinal: 3800),
          esperado: const CrudosPrecio(costoSinIva: 2000, precioCompra: 2420, margenGanancia: 44.63, precioVenta: 3500)),
      (r, j) {
    final c = r as CambioPrecios;
    _verificarPrecios(c.antes, j['antes'] as Map<String, dynamic>);
    _verificarPrecios(c.despues, j['despues'] as Map<String, dynamic>);
  }),
  'precios_previsualizar': _Caso(
      (api) => api.previsualizarPrecios(['7790001', '7790002', '7790004', '0000000'], porcentaje: 10),
      (r, j) => _verificarCambios(r as List<CambioPrecio>, j as List)),
  'precios_aplicar': _Caso(
      (api) => api.aplicarPrecios(['7790001', '7790002'],
          esperados: {'7790001': 3500, '7790002': 4300}, porcentaje: 10), (r, j) {
    _verificarCambios(r as List<CambioPrecio>, j as List);
    expect(r.where((c) => c.precioCambioMientrasTanto).map((c) => c.codigo), ['7790002']);
  }),
  'alertas': _Caso((api) => api.alertas(), (r, j) {
    final lista = r as List<Alerta>;
    expect(lista, hasLength((j as List).length));
    for (var i = 0; i < lista.length; i++) {
      final a = lista[i], e = j[i] as Map<String, dynamic>;
      expect([a.codigo, a.nombre, a.stock, a.stockMinimo, a.stockMaximo, a.tipo, a.umbralPropio],
          [e['codigo'], e['nombre'], e['stock'], e['stock_minimo'], e['stock_maximo'], e['tipo'], e['umbral_propio']]);
      expect(a.ultimaAlerta, e['ultima_alerta_enviada']);
      expect(a.proximoAviso, e['proximo_aviso_desde']);
    }
  }),
  'config_alertas': _Caso((api) => api.configAlertas(),
      (r, j) => _verificarConfig(r as ConfigAlertas, j as Map<String, dynamic>)),
  'config_telegram': _Caso((api) => api.guardarTelegram(habilitado: false),
      (r, j) => _verificarConfig(r as ConfigAlertas, j as Map<String, dynamic>)),
  'telegram_probar': _Caso((api) => api.probarTelegram(), (r, j) {
    final p = r as ResultadoPrueba;
    expect([p.ok, p.enviado, p.detalle], [j['ok'], j['enviado'], j['detalle']]);
  }),
  'umbrales_guardar': _Caso((api) => api.guardarUmbrales(0, 0),
      (r, j) => _verificarUmbrales(r as EstadoUmbrales, j as Map<String, dynamic>)),
  'umbral_global_quitar': _Caso((api) => api.quitarUmbralGlobal(),
      (r, j) => _verificarUmbrales(r as EstadoUmbrales, j as Map<String, dynamic>)),
  'factura_analizar': _Caso((api) => api.analizarFactura([0x25, 0x50, 0x44, 0x46], 'remito.pdf'), (r, j) {
    final f = r as FacturaAnalizada;
    expect(f.nombre, j['factura_nombre']);
    expect(f.escaneada, j['es_pdf_escaneado']);
    expect(f.noReconocidas, j['lineas_no_reconocidas']);
    expect(f.items, hasLength((j['items'] as List).length));
    for (var i = 0; i < f.items.length; i++) {
      _verificarItem(f.items[i], j['items'][i] as Map<String, dynamic>);
    }
  }),
  'factura_aplicar': _Caso((api) => api.aplicarFactura('remito.pdf', _itemsFactura), (r, j) {
    final lista = r as List<ResultadoFactura>;
    expect([for (final x in lista) [x.codigo, x.ok, x.stockNuevo, x.error]],
        [for (final x in j as List) [x['codigo'], x['ok'], x['stock_nuevo'], x['error']]]);
  }),
  // errores: lo que importa es que lleguen como ApiError con su código y extras
  'error_404_no_existe': _Caso((api) => api.producto('0000000')),
  'error_409_stock': _Caso((api) => api.movimiento('7790003', 1, sumar: false)),
  'error_409_precio': _Caso((api) => api.guardarPrecios('7790001', const ValoresPrecio(precioFinal: 3800),
      esperado: const CrudosPrecio(costoSinIva: 2000, precioCompra: 2420, margenGanancia: 44.63, precioVenta: 3500))),
  'error_409_factura': _Caso((api) => api.aplicarFactura('remito.pdf',
      [ItemFactura(codigo: '7790002', nombre: '', cantidad: 12, precioCompra: null, existe: true)])),
  'error_503_pin': _Caso((api) => api.login('1234')),
  'error_503_base_desactualizada': _Caso((api) => api.productos()),
  'error_403_red': _Caso((api) => api.productos()),
};

/// Las rutas de tipos_anulables ("productos[].oferta", "precios_guardar.antes.margen"…)
/// recorridas sobre el fixture: todos los valores que toma ese campo.
List<Object?> _valoresDe(String ruta) {
  final partes = ruta.split('.');
  var actuales = <Object?>[
    for (final r in (_ejemplos[partes.first.replaceAll('[]', '')] as Map)['respuestas'] as List)
      if (partes.first.endsWith('[]')) ...r as List else r,
  ];
  for (final parte in partes.skip(1)) {
    final clave = parte.replaceAll('[]', '');
    final siguientes = <Object?>[];
    for (final a in actuales) {
      if (a is! Map || !a.containsKey(clave)) continue;
      final v = a[clave];
      if (parte.endsWith('[]')) {
        siguientes.addAll(v as List);
      } else {
        siguientes.add(v);
      }
    }
    actuales = siguientes;
  }
  return actuales;
}

void main() {
  test('el fixture es el del contrato que entiende la app, con la misma firma', () {
    expect(fixture['contrato'], contratoMinimo);
    expect(fixture['firma'], firmaApi);
  });

  test('cada ejemplo del fixture tiene su caso acá, y al revés', () {
    expect(_casos.keys.toSet(), _ejemplos.keys.toSet(),
        reason: 'un endpoint nuevo en el fixture sin modelo en la app (o un caso viejo que ya no existe)');
  });

  test('los campos anulables aparecen llenos y en null, así la app parsea las dos formas', () {
    final anulables = (fixture['tipos_anulables'] as Map).keys.where((k) => k != '...').toList();
    expect(anulables, isNotEmpty);
    for (final ruta in anulables) {
      final valores = _valoresDe(ruta);
      expect(valores.contains(null), isTrue, reason: '$ruta nunca aparece en null');
      expect(valores.any((v) => v != null), isTrue, reason: '$ruta nunca aparece lleno');
    }
  });

  for (final MapEntry(key: nombre, value: ej) in _ejemplos.entries) {
    final e = ej as Map<String, dynamic>;
    final respuestas = e['respuestas'] as List;
    for (var i = 0; i < respuestas.length; i++) {
      test('$nombre (${e['metodo']} ${e['ruta']}, variante ${i + 1} de ${respuestas.length})', () async {
        final esperada = Uri.parse(e['ruta'] as String);
        http.BaseRequest? enviado;
        String? cuerpo;
        final api = ClienteApi(
          servidor: '100.101.102.103',
          token: 'v2.token',
          client: MockClient((r) async {
            enviado = r;
            cuerpo = r.body;
            return http.Response.bytes(utf8.encode(jsonEncode(respuestas[i])), e['status'] as int,
                headers: {'content-type': 'application/json; charset=utf-8'});
          }),
        );
        final caso = _casos[nombre]!;
        final status = e['status'] as int;
        Object? resultado;
        ApiError? error;
        try {
          resultado = await caso.llamar(api);
        } on ApiError catch (x) {
          error = x;
        }

        // misma ruta, mismo método (y los mismos parámetros, si el ejemplo los fija)
        expect(enviado, isNotNull);
        expect(enviado!.method, e['metodo']);
        expect(enviado!.url.path, esperada.path);
        if (esperada.hasQuery) expect(enviado!.url.queryParameters, esperada.queryParameters);
        // y el mismo cuerpo que el "pedido" del ejemplo
        if (e.containsKey('pedido')) expect(jsonDecode(cuerpo!), e['pedido']);

        final json = respuestas[i];
        if (status >= 200 && status < 300) {
          expect(error, isNull, reason: 'una respuesta del contrato no puede romper el parseo: $error');
          caso.verificar?.call(resultado, json);
        } else {
          expect(error, isNotNull);
          expect(error!.status, status);
          expect(error.codigo, json['codigo']);
          expect(error.mensaje, json['detail']);
          expect(error.campo, json['campo']);
          expect(error.haceMin, json['hace_min']);
          expect(error.renglonesIguales, json['renglones_iguales']);
          expect(error.reintentarEnS, json['reintentar_en_s']);
          expect(error.incierto, isFalse, reason: 'la PC contestó: se sabe que no se aplicó');
        }
      });
    }
  }
}
