// Alta de productos y filtros guardados (contrato 3), probados como los usa
// Leo: desde Stock, desde el modo lector y desde Precios, contra el servidor
// falso que imita a la API (test/servidor_falso.dart).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:panel_dueno/estado.dart';
import 'package:panel_dueno/main.dart';
import 'package:panel_dueno/pantallas/filtros.dart';
import 'package:panel_dueno/pantallas/producto_nuevo.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'servidor_falso.dart';

void main() {
  setUpAll(() => initializeDateFormatting('es_AR'));

  late ServidorFalso servidor;
  late SesionEstado sesion;
  late AjustesEstado ajustes;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    servidor = ServidorFalso();
    sesion = SesionEstado(almacen: AlmacenMemoria(), biometria: BiometriaFalsa(), crearCliente: servidor.crear);
    ajustes = AjustesEstado(await SharedPreferences.getInstance());
  });

  Future<void> arrancar(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 2.75;
    addTearDown(tester.view.reset);
    await sesion.cargar();
    await sesion.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    await tester.pumpWidget(PanelDuenoApp(sesion: sesion, ajustes: ajustes));
    await tester.pumpAndSettle();
  }

  Future<void> terminar(WidgetTester tester) => tester.pumpWidget(const SizedBox());

  /// La flecha de la AppBar (pageBack() busca el texto "Back" y la app está en castellano).
  Future<void> atras(WidgetTester tester) async {
    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();
  }

  /// El botón atrás de Android.
  Future<void> atrasAndroid(WidgetTester tester) async {
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
  }

  Future<void> irA(WidgetTester tester, String pestana) async {
    await tester.tap(find.text(pestana));
    await tester.pumpAndSettle();
  }

  Future<void> tocar(WidgetTester tester, Finder f) async {
    await tester.ensureVisible(f);
    await tester.pumpAndSettle();
    await tester.tap(f);
    await tester.pumpAndSettle();
  }

  Future<void> buscar(WidgetTester tester, String texto, {Finder? campo}) async {
    await tester.enterText(campo ?? find.byType(TextField).first, texto);
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
  }

  String texto(WidgetTester tester, Finder campo) => tester.widget<TextField>(campo).controller!.text;

  final campoCodigo = find.widgetWithText(TextField, 'Código de barras');
  final darDeAlta = find.byKey(const Key('boton_dar_de_alta'));

  Future<void> completarAlta(WidgetTester tester,
      {String? codigo, String nombre = 'Pan rallado 500g', String precio = '2.500', String stock = '12'}) async {
    if (codigo != null) await tester.enterText(campoCodigo, codigo);
    await tester.enterText(find.byKey(const Key('alta_nombre')), nombre);
    await tester.enterText(find.byKey(const Key('alta_precio')), precio);
    await tester.enterText(find.byKey(const Key('alta_stock')), stock);
    await tester.pumpAndSettle();
  }

  List<Map<String, dynamic>> altasEnviadas() =>
      [for (final c in servidor.cuerpos['POST /api/productos'] ?? const []) c as Map<String, dynamic>];

  group('alta de productos', () {
    testWidgets('stock: un código que no está se ofrece dar de alta y queda abierto con su stock', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await buscar(tester, '7790999');
      expect(find.byKey(const Key('no_encontrado')), findsOneWidget);
      expect(find.text('No hay ningún producto con «7790999».'), findsOneWidget);

      await tocar(tester, find.text('Darlo de alta'));
      expect(find.text('Producto nuevo'), findsOneWidget);
      expect(texto(tester, campoCodigo), '7790999', reason: 'trae el código buscado');
      await completarAlta(tester);
      await tocar(tester, darDeAlta);

      expect(altasEnviadas(), [
        {
          'codigo': '7790999', 'nombre': 'Pan rallado 500g', 'precio_venta': 2500, 'stock_inicial': 12,
          'marca': null, 'proveedor': null, 'categoria': null,
        }
      ], reason: '"2.500" es dos mil quinientos, como en Precios (no 2,5)');
      expect(find.text('Producto nuevo'), findsNothing, reason: 'vuelve a Stock');
      expect(find.text('Pan rallado 500g: dado de alta. Ya está en la Caja.'), findsOneWidget);
      expect(find.text('Sumar 1'), findsOneWidget, reason: 'queda abierto para cargarle stock');
      expect(find.byKey(const Key('no_encontrado')), findsNothing);
      expect(servidor.catalogo['7790999']!['stock'], 12);
      await terminar(tester);
    });

    testWidgets('lo buscado con espacios va al nombre, no al código', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await buscar(tester, 'Fernet 750');
      await tocar(tester, find.text('Darlo de alta'));
      expect(texto(tester, campoCodigo), '');
      expect(texto(tester, find.byKey(const Key('alta_nombre'))), 'Fernet 750');
      await terminar(tester);
    });

    testWidgets('valida antes de mandar: el 1 de la Caja, espacios, sin nombre ni precio', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await tocar(tester, darDeAlta);
      expect(find.text('Escaneá o escribí el código.'), findsOneWidget);
      expect(find.text('Poné el nombre como lo querés ver en la Caja.'), findsOneWidget);
      expect(find.text('Poné el precio de venta.'), findsOneWidget);

      await tester.enterText(campoCodigo, '1');
      await tester.pumpAndSettle();
      expect(find.text('El código 1 lo usa la Caja para los artículos sin código de barras: elegí otro.'),
          findsOneWidget);
      await tester.enterText(campoCodigo, '7790 999');
      await tester.enterText(find.byKey(const Key('alta_precio')), 'mil');
      await tester.pumpAndSettle();
      expect(find.text('El código no puede tener espacios.'), findsOneWidget);
      expect(find.textContaining('Escribí el precio con números'), findsOneWidget);
      await tocar(tester, darDeAlta);
      expect(servidor.pedidos, isNot(contains('POST /api/productos')));
      await terminar(tester);
    });

    testWidgets(r'un precio de $ 0 pide confirmación', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await completarAlta(tester, codigo: '7790998', precio: '0', stock: '');
      await tocar(tester, darDeAlta);
      expect(find.text(r'¿Precio $ 0?'), findsOneWidget);
      await tocar(tester, find.text('Cancelar'));
      expect(servidor.pedidos, isNot(contains('POST /api/productos')));

      await tocar(tester, darDeAlta);
      await tocar(tester, find.text('Dar de alta igual'));
      expect(altasEnviadas().single['precio_venta'], 0);
      expect(altasEnviadas().single['stock_inicial'], 0, reason: 'el stock vacío es 0');
      await terminar(tester);
    });

    testWidgets('un código que ya existe no se pisa: muestra cuál es y deja abrirlo', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await completarAlta(tester, codigo: '7790002', nombre: 'Otra cosa');
      await tocar(tester, darDeAlta);
      expect(find.text('Ya hay un producto con el código 7790002: «Café Molido 500g».'), findsOneWidget);
      expect(servidor.catalogo['7790002']!['nombre'], 'Café Molido 500g');

      await tocar(tester, find.text('Ver producto'));
      expect(find.text('Producto nuevo'), findsNothing);
      expect(find.text('Café Molido 500g'), findsOneWidget);
      expect(find.textContaining('dado de alta'), findsNothing);
      await terminar(tester);
    });

    testWidgets('un código dado de baja lo dice y no ofrece abrirlo', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await completarAlta(tester, codigo: '7790006');
      await tocar(tester, darDeAlta);
      expect(find.textContaining('está dado de baja: revisalo en el Panel de la PC'), findsOneWidget);
      expect(find.text('Ver producto'), findsNothing);
      await terminar(tester);
    });

    testWidgets('si se corta, pregunta en la PC: si el alta llegó, la da por hecha (sin mandarla dos veces)',
        (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await completarAlta(tester, codigo: '7790997');
      servidor.cortarEscrituras = true;
      await tocar(tester, darDeAlta);
      expect(servidor.pedidos.where((p) => p == 'POST /api/productos'), hasLength(1));
      final despues = servidor.pedidos.sublist(servidor.pedidos.indexOf('POST /api/productos') + 1);
      expect(despues.first, 'GET /api/productos/7790997', reason: 'pregunta por el código, no repite el alta');
      expect(find.text('Pan rallado 500g: dado de alta. Ya está en la Caja.'), findsOneWidget);
      expect(find.text('Producto nuevo'), findsNothing);
      await terminar(tester);
    });

    testWidgets('si se corta y la PC tampoco contesta después, no lo repite solo; tocar de nuevo no duplica',
        (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await completarAlta(tester, codigo: '7790996');
      servidor
        ..cortarEscrituras = true
        ..sinSenalDespuesDelCorte = true;
      await tocar(tester, darDeAlta);
      expect(find.textContaining('no se sabe si el producto quedó cargado'), findsOneWidget);
      expect(find.text('Producto nuevo'), findsOneWidget, reason: 'no se da por hecho sin saberlo');
      expect(servidor.pedidos.where((p) => p == 'POST /api/productos'), hasLength(1));

      // vuelve la señal: el alta ya estaba hecha y la PC contesta 409 con el producto
      servidor
        ..sinSenal = false
        ..cortarEscrituras = false;
      await tocar(tester, darDeAlta);
      expect(find.text('Producto nuevo'), findsNothing);
      expect(find.text('Pan rallado 500g: dado de alta. Ya está en la Caja.'), findsOneWidget);
      expect(servidor.pedidos.where((p) => p == 'POST /api/productos'), hasLength(2));
      expect(servidor.catalogo['7790996']!['stock'], 12, reason: 'un solo producto, con su stock una sola vez');
      expect(servidor.movimientos.where((m) => m['codigo'] == '7790996'), hasLength(1));
      await terminar(tester);
    });

    testWidgets('sugiere las marcas que ya se usan, y Enter no elige la sugerencia sola', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await tester.enterText(find.byKey(const Key('alta_marca')), 'co');
      await tester.pumpAndSettle();
      expect(find.text('Coca-Cola'), findsOneWidget);
      expect(find.text('Colombia'), findsOneWidget);
      expect(servidor.pedidos, contains('GET /api/catalogo/valores'));
      await tester.tap(find.text('Colombia'));
      await tester.pumpAndSettle();
      expect(texto(tester, find.byKey(const Key('alta_marca'))), 'Colombia');

      await tester.enterText(find.byKey(const Key('alta_proveedor')), 'Dis');
      await tester.pumpAndSettle();
      expect(find.text('Distribuidora Sur'), findsOneWidget);
      await tester.testTextInput.receiveAction(TextInputAction.next);
      await tester.pumpAndSettle();
      expect(texto(tester, find.byKey(const Key('alta_proveedor'))), 'Dis', reason: 'lo escrito queda como está');
      await terminar(tester);
    });

    testWidgets('salir con algo escrito pregunta antes de descartar', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await tester.enterText(find.byKey(const Key('alta_nombre')), 'Algo');
      await tester.pumpAndSettle();
      await atrasAndroid(tester);
      expect(find.text('¿Descartar el producto?'), findsOneWidget);
      await tocar(tester, find.text('Cancelar'));
      expect(find.text('Producto nuevo'), findsOneWidget);
      await atras(tester);
      await tocar(tester, find.text('Descartar'));
      expect(find.text('Producto nuevo'), findsNothing);
      expect(find.text('Stock'), findsWidgets);
      await terminar(tester);
    });

    testWidgets('salir sin haber escrito nada no pregunta', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Stock');
      await buscar(tester, '7790999');
      await tocar(tester, find.text('Darlo de alta'));
      await atras(tester);
      expect(find.text('¿Descartar el producto?'), findsNothing);
      expect(find.text('Producto nuevo'), findsNothing);
      await terminar(tester);
    });

    testWidgets('con una PC de contrato 2, dice que hay que actualizarla', (tester) async {
      servidor.contrato3 = false;
      await arrancar(tester);
      await irA(tester, 'Stock');
      await tocar(tester, find.byKey(const Key('boton_producto_nuevo')));
      await completarAlta(tester, codigo: '7790995');
      await tocar(tester, darDeAlta);
      expect(find.textContaining('no da de alta productos desde el celular'), findsOneWidget);
      await terminar(tester);
    });

    testWidgets('modo lector: un código desconocido se da de alta sin salir del lector', (tester) async {
      await arrancar(tester);
      tester.view.physicalSize = const Size(2200, 2400); // más ancho: la fuente de los tests es grande
      await irA(tester, 'Stock');
      await irA(tester, 'Modo lector');
      await tester.enterText(find.widgetWithText(TextField, 'O escribí el código y Enter'), '7790994');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(find.text('Código 7790994: no existe en el sistema'), findsWidgets);

      await tocar(tester, find.text('Dar de alta').first);
      expect(find.text('Producto nuevo'), findsOneWidget);
      expect(texto(tester, campoCodigo), '7790994');
      await completarAlta(tester, nombre: 'Turrón', precio: '900', stock: '30');
      await tocar(tester, darDeAlta);

      expect(find.text('Modo lector'), findsOneWidget, reason: 'vuelve al lector');
      expect(find.text('Turrón: dado de alta con 30 u.'), findsWidgets);
      expect(find.text('Dar de alta'), findsNothing, reason: 'la lectura quedó resuelta');
      expect(servidor.pedidos.where((p) => p == 'POST /api/stock/lector'), hasLength(1),
          reason: 'solo la lectura del principio: el alta no descuenta nada');
      await terminar(tester);
    });
  });

  testWidgets('modo lector: con el alta abierta encima, lo que ve la cámara no descuenta nada', (tester) async {
    await arrancar(tester);
    tester.view.physicalSize = const Size(2200, 2400);
    await irA(tester, 'Stock');
    await irA(tester, 'Modo lector');
    await tester.enterText(find.widgetWithText(TextField, 'O escribí el código y Enter'), '7790994');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    await tocar(tester, find.text('Dar de alta').first);
    expect(find.text('Producto nuevo'), findsOneWidget);
    // mobile_scanner reanuda la cámara sola al volver del fondo, aunque el lector quede tapado
    tester
        .widget<MobileScanner>(find.byType(MobileScanner, skipOffstage: false))
        .onDetect!(BarcodeCapture(barcodes: [Barcode(rawValue: '7790002')]));
    await tester.pumpAndSettle();
    expect(servidor.pedidos.where((p) => p == 'POST /api/stock/lector'), hasLength(1),
        reason: 'solo la lectura que abrió el alta');
    expect(servidor.stockCafe, 3);
    await terminar(tester);
  });

  group('filtros', () {
    Future<void> abrirFiltros(WidgetTester tester) async {
      await irA(tester, 'Precios');
      await tocar(tester, find.byKey(const Key('boton_filtros')));
    }

    Finder enFiltro(String codigo) => find.byKey(ValueKey('en_filtro_$codigo'));
    Finder sacar(String codigo) =>
        find.descendant(of: enFiltro(codigo), matching: find.byIcon(Icons.remove_circle_outline));

    testWidgets('sin filtros explica qué son; se crea uno y queda como uno del Panel', (tester) async {
      await arrancar(tester);
      await abrirFiltros(tester);
      expect(find.text('Todavía no hay filtros guardados'), findsOneWidget);

      await tocar(tester, find.text('Crear filtro'));
      expect(find.text('Filtro nuevo'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('nombre_filtro')), 'Gaseosas');
      await buscar(tester, 'coca', campo: find.widgetWithText(TextField, 'Código o nombre'));
      expect(find.text('1 RESULTADO'), findsOneWidget);
      await tocar(tester, find.text('Coca Cola 2,25 L'));
      expect(enFiltro('7790003'), findsOneWidget);
      expect(find.text('EN ESTE FILTRO (1)'), findsOneWidget);

      await tocar(tester, find.byKey(const Key('guardar_filtro')));
      expect(find.text('Filtro «Gaseosas» guardado.'), findsOneWidget);
      expect(servidor.filtros['Gaseosas']!.codigos, ['7790003']);
      expect(find.text('Gaseosas'), findsOneWidget, reason: 'el título pasa a ser el nombre');

      await atras(tester);
      expect(find.text('Gaseosas'), findsOneWidget);
      expect(find.text('1 producto'), findsOneWidget);
      await terminar(tester);
    });

    testWidgets('un código completo se agrega directo; sin nombre o sin productos no se guarda', (tester) async {
      await arrancar(tester);
      await abrirFiltros(tester);
      await tocar(tester, find.text('Crear filtro'));
      await tocar(tester, find.byKey(const Key('guardar_filtro')));
      expect(find.text('Ponele un nombre al filtro.'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('nombre_filtro')), 'Kiosco');
      await tocar(tester, find.byKey(const Key('guardar_filtro')));
      expect(find.text('Agregá al menos un producto al filtro.'), findsOneWidget);
      expect(servidor.pedidos, isNot(contains('POST /api/filtros')));

      await buscar(tester, '7790002', campo: find.widgetWithText(TextField, 'Código o nombre'));
      expect(enFiltro('7790002'), findsOneWidget);
      expect(find.textContaining('RESULTADO'), findsNothing);
      await tocar(tester, find.byKey(const Key('guardar_filtro')));
      expect(servidor.filtros['Kiosco']!.codigos, ['7790002']);
      await terminar(tester);
    });

    testWidgets('si lo guardaron en el Panel mientras tanto: avisa, no pisa y deja volver a cargar', (tester) async {
      servidor.guardarFiltroPorFuera('Promo', ['7790002', '7790001']);
      await arrancar(tester);
      await abrirFiltros(tester);
      await tocar(tester, find.text('Promo'));
      expect(enFiltro('7790002'), findsOneWidget);
      expect(find.text('Ajustar los 2 precios'), findsOneWidget);
      await tocar(tester, sacar('7790001'));
      expect(find.text('Guardar cambios'), findsOneWidget);

      servidor.guardarFiltroPorFuera('Promo', ['7790003']);
      await tocar(tester, find.text('Guardar cambios'));
      expect(find.textContaining('cambió mientras tanto'), findsOneWidget);
      expect(servidor.filtros['Promo']!.codigos, ['7790003'], reason: 'lo del Panel no se pisa');

      await tocar(tester, find.text('Volver a cargar'));
      expect(enFiltro('7790003'), findsOneWidget);
      expect(enFiltro('7790002'), findsNothing);
      expect(find.text('Guardar cambios'), findsNothing);
      await terminar(tester);
    });

    testWidgets('borrar pide confirmación y lo saca de la lista (también del Panel)', (tester) async {
      servidor.guardarFiltroPorFuera('Promo', ['7790002']);
      await arrancar(tester);
      await abrirFiltros(tester);
      await tocar(tester, find.text('Promo'));
      await tocar(tester, find.byKey(const Key('borrar_filtro')));
      expect(find.text('¿Borrar «Promo»?'), findsOneWidget);
      await tocar(tester, find.text('Borrar'));
      expect(find.text('Filtro «Promo» borrado.'), findsOneWidget);
      expect(find.text('Todavía no hay filtros guardados'), findsOneWidget);
      expect(servidor.filtros, isEmpty);
      await terminar(tester);
    });

    testWidgets('salir con cambios sin guardar pregunta', (tester) async {
      servidor.guardarFiltroPorFuera('Promo', ['7790002', '7790001']);
      await arrancar(tester);
      await abrirFiltros(tester);
      await tocar(tester, find.text('Promo'));
      await tocar(tester, sacar('7790002'));
      await atras(tester);
      expect(find.text('¿Salir sin guardar?'), findsOneWidget);
      await tocar(tester, find.text('Salir'));
      expect(find.text('Filtros'), findsOneWidget);
      expect(servidor.filtros['Promo']!.codigos, ['7790002', '7790001']);
      await terminar(tester);
    });

    testWidgets('desde el filtro se ajustan los precios de todos juntos', (tester) async {
      servidor.guardarFiltroPorFuera('Promo', ['7790002', '7790001']);
      await arrancar(tester);
      await abrirFiltros(tester);
      await tocar(tester, find.text('Promo'));
      await tocar(tester, find.text('Ajustar los 2 precios'));
      expect(find.text('Ajustar 2 precios'), findsOneWidget);
      await tester.enterText(find.byType(TextField).first, '10');
      await tocar(tester, find.text('Ver cómo quedan'));
      final vista = servidor.cuerpos['POST /api/precios/previsualizar']!.last as Map<String, dynamic>;
      expect(vista['codigos'], ['7790002', '7790001']);
      await terminar(tester);
    });

    testWidgets('en Precios, lo seleccionado se guarda como filtro', (tester) async {
      await arrancar(tester);
      await irA(tester, 'Precios'); // al entrar ya trae todos
      await tester.tap(find.byType(Checkbox).at(1));
      await tester.tap(find.byType(Checkbox).at(2));
      await tester.pumpAndSettle();
      await tocar(tester, find.byKey(const Key('guardar_como_filtro')));
      expect(find.text('EN ESTE FILTRO (2)'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('nombre_filtro')), 'Infusiones');
      await tocar(tester, find.byKey(const Key('guardar_filtro')));
      expect(servidor.filtros['Infusiones']!.codigos, hasLength(2));
      await terminar(tester);
    });

    testWidgets('un filtro de condiciones o con productos dados de baja lo avisa', (tester) async {
      servidor.guardarFiltroPorFuera('Almacén', ['7790002', '7790999'], tipo: 'condiciones');
      await arrancar(tester);
      await abrirFiltros(tester);
      expect(find.text('1 producto · armado con condiciones en el Panel'), findsOneWidget);
      await tocar(tester, find.text('Almacén'));
      expect(find.textContaining('se armó en el Panel con condiciones'), findsOneWidget);
      expect(find.textContaining('ya no existe o está dado de baja (7790999)'), findsOneWidget);
      await terminar(tester);
    });

    testWidgets('si se corta al guardar, pregunta en la PC y si quedó guardado lo da por hecho', (tester) async {
      await arrancar(tester);
      await abrirFiltros(tester);
      await tocar(tester, find.text('Crear filtro'));
      await tester.enterText(find.byKey(const Key('nombre_filtro')), 'Corte');
      await buscar(tester, '7790002', campo: find.widgetWithText(TextField, 'Código o nombre'));
      servidor.cortarEscrituras = true;
      await tocar(tester, find.byKey(const Key('guardar_filtro')));
      expect(servidor.pedidos.where((p) => p == 'POST /api/filtros'), hasLength(1));
      expect(find.text('Filtro «Corte» guardado.'), findsOneWidget);
      expect(find.text('Guardar filtro'), findsNothing);
      await terminar(tester);
    });

    testWidgets('con una PC de contrato 2, dice que hay que actualizarla', (tester) async {
      servidor.contrato3 = false;
      await arrancar(tester);
      await abrirFiltros(tester);
      expect(find.text(textoPcViejaFiltros), findsOneWidget);
      await terminar(tester);
    });
  });

  group('reglas del alta (sin pantalla)', () {
    test('el código: sin espacios, hasta 64 y nunca el 1', () {
      expect(problemaCodigo(' 7790001 '), isNull);
      expect(problemaCodigo('ÑANDÚ-7'), isNull);
      expect(problemaCodigo(''), isNotNull);
      expect(problemaCodigo('77 90'), contains('espacios'));
      expect(problemaCodigo('7' * 65), contains('64'));
      expect(problemaCodigo(' 1 '), contains('sin código de barras'));
    });

    test('el precio se lee como en Precios y el stock vacío es 0', () {
      expect(leerPrecio('2.500').valor, 2500);
      expect(leerPrecio(r'$ 2.500,50').valor, 2500.5);
      expect(leerPrecio('0').valor, 0);
      expect(leerPrecio('').problema, isNotNull);
      expect(leerPrecio('-5').problema, isNotNull);
      expect(leerStock('').valor, 0);
      expect(leerStock('12').valor, 12);
      expect(leerStock('100001').problema, isNotNull);
    });

    test('lo buscado va al código si parece un código, si no al nombre', () {
      expect(datosDeBusqueda('7790999'), (codigo: '7790999', nombre: ''));
      expect(datosDeBusqueda('Fernet 750'), (codigo: '', nombre: 'Fernet 750'));
      expect(datosDeBusqueda('fernet'), (codigo: '', nombre: 'fernet'));
      expect(datosDeBusqueda('1'), (codigo: '', nombre: ''));
    });

  });
}
