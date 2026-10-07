import 'package:file_picker/file_picker.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/estado.dart';
import 'package:panel_dueno/main.dart';
import 'package:panel_dueno/pantallas/bloqueo.dart';
import 'package:panel_dueno/pantallas/precios.dart';
import 'package:panel_dueno/widgets/comunes.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'servidor_falso.dart';

const textoDemasiadosEsperado =
    'No se buscó por nombre (la factura tiene muchos renglones sin código): corregí el código a mano o cargala '
    'desde el Panel de la PC.';

/// Reemplaza el selector de archivos del teléfono: "elige" un PDF cualquiera.
class SelectorFalso extends FilePickerPlatform {
  @override
  Future<PlatformFile?> pickFile({
    String? dialogTitle,
    String? initialDirectory,
    FileType type = FileType.any,
    List<String>? allowedExtensions,
    Function(FilePickerStatus)? onFileLoading,
    int compressionQuality = 0,
    AndroidOptions androidOptions = const AndroidOptions(),
    DarwinOptions darwinOptions = const DarwinOptions(),
    WindowsOptions windowsOptions = const WindowsOptions(),
    LinuxOptions linuxOptions = const LinuxOptions(),
    WebOptions webOptions = const WebOptions(),
  }) async =>
      PdfFalso();
}

final class PdfFalso extends PlatformFile {
  static final _bytes = Uint8List.fromList([0x25, 0x50, 0x44, 0x46]);
  @override
  String get name => 'remito.pdf';
  @override
  Uri get uri => Uri.parse('file:///remito.pdf');
  @override
  get xFile => throw UnimplementedError();
  @override
  int? lengthSync() => _bytes.length;
  @override
  Future<int?> length() async => _bytes.length;
  @override
  Future<Uint8List> readAsBytes() async => _bytes;
  @override
  Stream<Uint8List> readAsByteStream() => Stream.value(_bytes);
}

void main() {
  setUpAll(() => initializeDateFormatting('es_AR'));

  late ServidorFalso servidor;
  late SesionEstado sesion;
  late AjustesEstado ajustes;
  late AlmacenMemoria almacen;

  Future<void> arrancar(WidgetTester tester, {bool logueado = false}) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 2.75;
    addTearDown(tester.view.reset);
    await sesion.cargar();
    if (logueado) await sesion.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    await tester.pumpWidget(PanelDuenoApp(sesion: sesion, ajustes: ajustes));
    await tester.pumpAndSettle();
  }

  Future<void> terminar(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox()); // cancela el refresco periódico del dashboard
  }

  /// Sesión con reloj manejable, para poder bloquearla por inactividad.
  var ahora = DateTime(2026, 10, 4, 12);
  void conReloj() {
    ahora = DateTime(2026, 10, 4, 12);
    sesion = SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: servidor.crear, reloj: () => ahora);
  }

  Future<void> bloquearPorInactividad(WidgetTester tester) async {
    sesion.pasoASegundoPlano();
    ahora = ahora.add(const Duration(minutes: 3));
    sesion.volvioAPrimerPlano();
    await tester.pumpAndSettle();
    expect(find.text('¡Hola de nuevo!'), findsOneWidget);
  }

  Finder enBloqueo(Type tipo) => find.descendant(of: find.byType(PantallaBloqueo), matching: find.byType(tipo));

  Future<void> desbloquear(WidgetTester tester) async {
    await tester.enterText(enBloqueo(TextField), ServidorFalso.pin);
    await tester.tap(find.text('Entrar'));
    await tester.pumpAndSettle();
    expect(find.text('¡Hola de nuevo!'), findsNothing);
  }

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    servidor = ServidorFalso();
    almacen = AlmacenMemoria();
    sesion = SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: servidor.crear);
    ajustes = AjustesEstado(await SharedPreferences.getInstance());
  });

  testWidgets('primer ingreso: PIN -> dashboard con las ventas del día', (tester) async {
    await arrancar(tester);
    expect(find.text('Panel del Dueño'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, ServidorFalso.direccion);
    await tester.enterText(find.byKey(const Key('campo_pin')), ServidorFalso.pin);
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();

    expect(find.text('Ventas de hoy'), findsOneWidget);
    expect(find.textContaining('152.300'), findsOneWidget);
    expect(find.text('El Galpón Del Nono'), findsOneWidget);
    expect(find.text('3 productos necesitan atención'), findsOneWidget);
    expect(find.textContaining('El bot de Telegram está apagado'), findsNothing);
    await tester.dragUntilVisible(find.text('Yerba Mate 1kg'), find.byType(ListView).first, const Offset(0, -300));
    expect(find.text('Yerba Mate 1kg'), findsOneWidget);
    await terminar(tester);
  });

  testWidgets('PIN incorrecto muestra el error', (tester) async {
    await arrancar(tester);
    await tester.enterText(find.byType(TextField).first, ServidorFalso.direccion);
    await tester.enterText(find.byKey(const Key('campo_pin')), '999999');
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();
    expect(find.text('PIN incorrecto.'), findsOneWidget);
  });

  testWidgets('reabrir la app pide desbloquear', (tester) async {
    await (SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: servidor.crear)..cargar())
        .ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    await arrancar(tester);
    expect(find.text('¡Hola de nuevo!'), findsOneWidget);

    await tester.enterText(find.byType(TextField), ServidorFalso.pin);
    await tester.tap(find.text('Entrar'));
    await tester.pumpAndSettle();
    expect(find.text('Ventas de hoy'), findsOneWidget);
    await terminar(tester);
  });

  testWidgets('el bloqueo por inactividad no pierde lo que Leo estaba haciendo', (tester) async {
    var ahora = DateTime(2026, 10, 4, 12);
    sesion = SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: servidor.crear, reloj: () => ahora);
    await arrancar(tester, logueado: true);

    await tester.tap(find.text('Stock'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '7790002');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('Café Molido 500g'), findsOneWidget);

    sesion.pasoASegundoPlano();
    ahora = ahora.add(const Duration(minutes: 3));
    sesion.volvioAPrimerPlano();
    await tester.pumpAndSettle();
    expect(find.text('¡Hola de nuevo!'), findsOneWidget);
    expect(find.text('Café Molido 500g').hitTestable(), findsNothing, reason: 'el bloqueo tapa la pantalla y no se puede tocar');

    await tester.enterText(find.byType(TextField).last, ServidorFalso.pin);
    await tester.tap(find.text('Entrar'));
    await tester.pumpAndSettle();
    expect(find.text('Café Molido 500g').hitTestable(), findsOneWidget, reason: 'sigue en Stock con el producto abierto');
    await terminar(tester);
  });

  testWidgets('stock: buscar, sumar y ver el stock nuevo', (tester) async {
    await arrancar(tester, logueado: true);

    await tester.tap(find.text('Stock'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '7790002');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('Café Molido 500g'), findsOneWidget);

    await tester.tap(find.text('12'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Sumar 12'));
    await tester.tap(find.text('Sumar 12'));
    await tester.pumpAndSettle();
    expect(servidor.stockCafe, 15);
    expect(find.text('15'), findsWidgets);
    await terminar(tester);
  });

  testWidgets('el tema se puede cambiar a claro y queda guardado', (tester) async {
    await arrancar(tester, logueado: true);
    expect(ajustes.modoTema, ThemeMode.dark, reason: 'arranca en oscuro elegante');

    await tester.tap(find.byTooltip('Ajustes').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Claro'));
    await tester.pumpAndSettle();

    expect(ajustes.modoTema, ThemeMode.light);
    expect((await SharedPreferences.getInstance()).getString('tema'), 'claro');
    expect(Theme.of(tester.element(find.text('APARIENCIA'))).brightness, Brightness.light);
    await terminar(tester);
  });

  testWidgets('alertas: lista el producto bajo mínimo y muestra Telegram', (tester) async {
    await arrancar(tester, logueado: true);

    await tester.tap(find.text('Alertas'));
    await tester.pumpAndSettle();
    expect(find.text('Stock bajo (3)'.toUpperCase()), findsOneWidget);
    expect(find.text('Café Molido 500g'), findsOneWidget);
    expect(find.text('Avisos por Telegram activos'), findsOneWidget);
    expect(find.textContaining('El bot está apagado'), findsNothing);
    // cuándo avisó el bot de cada una (si avisó)
    expect(find.textContaining('próximo aviso desde'), findsOneWidget);
    await terminar(tester);
  });

  for (final idioma in const [Locale('en', 'US'), Locale('es', 'ES'), Locale('pt', 'BR')]) {
    testWidgets('arranca bien con el teléfono en $idioma', (tester) async {
      tester.platformDispatcher.localesTestValue = [idioma];
      tester.platformDispatcher.localeTestValue = idioma;
      addTearDown(tester.platformDispatcher.clearAllTestValues);
      await arrancar(tester, logueado: true);
      expect(find.text('Ventas de hoy'), findsOneWidget);
      expect(find.textContaining(r'$ 152.300'), findsOneWidget, reason: 'siempre pesos argentinos');
      await terminar(tester);
    });
  }

  testWidgets('el botón de cámara carga el código escaneado', (tester) async {
    final control = TextEditingController();
    String? enviado;
    String? escaneado;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: CampoCodigo(
          controller: control,
          alEnviar: (t) => enviado = t,
          alEscanear: (c) => escaneado = c,
          escanearAbre: (_) async => '7791234567890',
        ),
      ),
    ));
    await tester.tap(find.byKey(const Key('boton_escanear')));
    await tester.pump();
    expect(control.text, '7791234567890');
    expect(escaneado, '7791234567890');
    expect(enviado, isNull);

    await tester.enterText(find.byType(TextField), 'yerba');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    expect(enviado, 'yerba');
  });

  testWidgets('el bloqueo tapa también una hoja abierta y el teclado pasa al PIN', (tester) async {
    conReloj();
    await arrancar(tester, logueado: true);
    await tester.tap(find.text('Precios'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Café Molido 500g'));
    await tester.pumpAndSettle();
    final campoPrecio =
        find.descendant(of: find.byKey(const Key('campo_precio_final')), matching: find.byType(EditableText));
    expect(find.text('Guardar precio').hitTestable(), findsOneWidget);
    expect(tester.widget<EditableText>(campoPrecio).focusNode.hasFocus, isTrue);

    await bloquearPorInactividad(tester);
    expect(find.text('Guardar precio').hitTestable(), findsNothing, reason: 'la hoja queda debajo del bloqueo');
    expect(tester.widget<EditableText>(campoPrecio).focusNode.hasFocus, isFalse,
        reason: 'el teclado no puede seguir escribiendo en el precio tapado');
    expect(tester.widget<EditableText>(enBloqueo(EditableText)).focusNode.hasFocus, isTrue,
        reason: 'el PIN toma el foco');

    await desbloquear(tester);
    expect(find.text('Guardar precio').hitTestable(), findsOneWidget, reason: 'la hoja sigue ahí, como la dejó');
    await terminar(tester);
  });

  testWidgets('el bloqueo tapa las pantallas y diálogos abiertos, y el botón atrás no los toca', (tester) async {
    conReloj();
    await arrancar(tester, logueado: true);
    await tester.tap(find.byTooltip('Ajustes').first);
    await tester.pumpAndSettle();
    await tester.dragUntilVisible(find.text('Cerrar sesión / cambiar de PC'), find.byType(ListView), const Offset(0, -300));
    await tester.tap(find.text('Cerrar sesión / cambiar de PC'));
    await tester.pumpAndSettle();
    expect(find.text('¿Cerrar sesión?'), findsOneWidget);

    await bloquearPorInactividad(tester);
    expect(find.text('Cerrar sesión').hitTestable(), findsNothing, reason: 'el diálogo queda debajo del bloqueo');
    expect(find.text('Cancelar').hitTestable(), findsNothing);

    await tester.binding.handlePopRoute(); // botón atrás de Android
    await tester.pumpAndSettle();
    expect(find.text('¡Hola de nuevo!'), findsOneWidget);
    expect(sesion.estado, EstadoSesion.bloqueada);

    await desbloquear(tester);
    expect(find.text('¿Cerrar sesión?').hitTestable(), findsOneWidget, reason: 'el atrás no cerró el diálogo tapado');
    await tester.tap(find.text('Cancelar'));
    await tester.pumpAndSettle();
    expect(find.text('Cerrar sesión / cambiar de PC').hitTestable(), findsOneWidget, reason: 'y sigue en Ajustes');
    expect(sesion.estado, EstadoSesion.activa);
    await terminar(tester);
  });

  testWidgets('al desbloquear, el atrás de Android vuelve a cerrar la pantalla que quedó abierta', (tester) async {
    // con el "atrás predictivo" (Android 16) Android solo le pasa el atrás a
    // la app si ella le avisó que lo maneja; si no, la manda al fondo
    final avisos = <bool>[];
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, (llamada) async {
      if (llamada.method == 'SystemNavigator.setFrameworkHandlesBack') avisos.add(llamada.arguments as bool);
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, null));
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    conReloj();
    await arrancar(tester, logueado: true);
    await tester.tap(find.byTooltip('Ajustes').first);
    await tester.pumpAndSettle();
    expect(avisos.last, isTrue, reason: 'Ajustes abierto: el atrás lo cierra la app');

    await bloquearPorInactividad(tester);
    expect(avisos.last, isFalse, reason: 'bloqueada: el atrás no toca lo tapado');

    await desbloquear(tester);
    expect(avisos.last, isTrue, reason: 'Ajustes sigue abierto: el atrás tiene que volver a cerrarlo');
    await terminar(tester);
  });

  testWidgets('con la app bloqueada, lo que ve la cámara del modo lector no descuenta stock', (tester) async {
    conReloj();
    await arrancar(tester, logueado: true);
    tester.view.physicalSize = const Size(2200, 2400); // más ancho: la fuente de los tests es grande
    await tester.tap(find.text('Stock'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Modo lector'));
    await tester.pumpAndSettle();
    void camaraVe(String codigo) => tester
        .widget<MobileScanner>(find.byType(MobileScanner))
        .onDetect!(BarcodeCapture(barcodes: [Barcode(rawValue: codigo)]));

    // mobile_scanner reanuda la cámara sola al volver del segundo plano,
    // aunque la pantalla quede debajo del bloqueo
    await bloquearPorInactividad(tester);
    camaraVe('7790002');
    await tester.pumpAndSettle();
    expect(servidor.pedidos, isNot(contains('POST /api/stock/lector')));

    await desbloquear(tester);
    camaraVe('7790002');
    await tester.pumpAndSettle();
    expect(servidor.pedidos.where((p) => p == 'POST /api/stock/lector'), hasLength(1));
    await terminar(tester);
  });

  testWidgets('si se cae la sesión, al volver a entrar no quedan pantallas viejas abiertas', (tester) async {
    await arrancar(tester, logueado: true);
    await tester.tap(find.byTooltip('Ajustes').first);
    await tester.pumpAndSettle();
    expect(find.text('APARIENCIA'), findsOneWidget);

    sesion.expirar(); // p. ej. la PC rechazó el token
    await tester.pumpAndSettle();
    expect(find.text('Panel del Dueño'), findsOneWidget);
    expect(find.text('APARIENCIA'), findsNothing);

    await tester.enterText(find.byKey(const Key('campo_pin')), ServidorFalso.pin);
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();
    expect(find.text('Ventas de hoy'), findsOneWidget);
    expect(find.text('APARIENCIA'), findsNothing);
    await terminar(tester);
  });

  testWidgets('al entrar por primera vez ofrece la huella (el diálogo vive en el login)', (tester) async {
    final bio = BiometriaFalsa(hay: true);
    sesion = SesionEstado(almacen: almacen, biometria: bio, crearCliente: servidor.crear);
    await arrancar(tester);
    await tester.enterText(find.byType(TextField).first, ServidorFalso.direccion);
    await tester.enterText(find.byKey(const Key('campo_pin')), ServidorFalso.pin);
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();
    expect(find.text('¿Entrar con huella o rostro?'), findsOneWidget);

    await tester.tap(find.text('Sí, activar'));
    await tester.pumpAndSettle();
    expect(sesion.biometriaActiva, isTrue);
    expect(sesion.estado, EstadoSesion.activa);
    expect(find.text('Ventas de hoy'), findsOneWidget);
    await terminar(tester);
  });

  testWidgets('precios: pide hasta 2000 y, si vienen justo 2000, avisa que hay más', (tester) async {
    servidor.cantidadProductos = 2500;
    await arrancar(tester, logueado: true);
    await tester.tap(find.text('Precios'));
    await tester.pumpAndSettle();
    expect(servidor.ultimaBusqueda?['limite'], '2000');
    expect(find.text('2000 productos'), findsOneWidget);
    expect(find.text(avisoRecorte), findsNWidgets(2), reason: 'arriba de la lista y junto al botón de ajustar');
    expect(find.text('Ajustar los 2000 precios'), findsOneWidget);
    await terminar(tester);
  });

  testWidgets('precios: con menos del máximo no hay aviso', (tester) async {
    await arrancar(tester, logueado: true);
    await tester.tap(find.text('Precios'));
    await tester.pumpAndSettle();
    expect(find.text('4 productos'), findsOneWidget);
    expect(find.text(avisoRecorte), findsNothing);
    await terminar(tester);
  });

  testWidgets('stock: si se corta al sumar, no invita a repetir y muestra el stock real', (tester) async {
    await arrancar(tester, logueado: true);
    await tester.tap(find.text('Stock'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '7790002');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();

    servidor.cortarEscrituras = true; // la PC aplica, pero la respuesta no llega
    await tester.tap(find.text('12'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Sumar 12'));
    await tester.tap(find.text('Sumar 12'));
    await tester.pumpAndSettle();
    expect(servidor.stockCafe, 15);
    expect(find.textContaining('No se sabe si se aplicó'), findsOneWidget);
    expect(find.textContaining('Probá de nuevo'), findsNothing);
    expect(find.text('15'), findsWidgets, reason: 'se recargó el producto con el stock que quedó de verdad');
    await terminar(tester);
  });

  testWidgets('alertas: si se corta al reponer, cierra la hoja y recarga la lista', (tester) async {
    await arrancar(tester, logueado: true);
    await tester.tap(find.text('Alertas'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Café Molido 500g'));
    await tester.pumpAndSettle();
    expect(find.text('Sumar 7 al stock'), findsOneWidget);

    servidor.cortarEscrituras = true;
    await tester.tap(find.text('Sumar 7 al stock'));
    await tester.pumpAndSettle();
    expect(servidor.stockCafe, 10);
    expect(find.text('Sumar 7 al stock'), findsNothing, reason: 'no queda el botón para repetir a ciegas');
    expect(find.textContaining('No se sabe si se aplicó'), findsOneWidget);
    // la lista se recargó con el stock real: con 10 el café ya no está bajo el mínimo
    expect(find.text('Stock bajo (2)'.toUpperCase()), findsOneWidget);
    expect(find.text('Café Molido 500g'), findsNothing);
    await terminar(tester);
  });

  group('facturas', () {
    late FilePickerPlatform selectorOriginal;
    setUp(() {
      selectorOriginal = FilePickerPlatform.instance;
      FilePickerPlatform.instance = SelectorFalso();
    });
    tearDown(() => FilePickerPlatform.instance = selectorOriginal);

    Future<void> abrirFactura(WidgetTester tester) async {
      await arrancar(tester, logueado: true);
      await tester.tap(find.text('Facturas'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Elegir PDF'));
      await tester.pumpAndSettle();
    }

    testWidgets('un renglón con cantidad 0 arranca destildado y no deja mandar la factura', (tester) async {
      servidor.itemsFactura = [
        {'indice': 0, 'codigo': '7790002', 'nombre': 'CAFE MOLIDO X 500', 'cantidad': 12, 'precio_compra': 3000.0,
         'existe': true, 'nombre_sistema': 'Café Molido 500g', 'stock_actual': 3, 'precio_compra_actual': 2900.0,
         'posible_duplicado': false, 'precio_sospechoso': false, 'emparejamiento': null},
        {'indice': 1, 'codigo': '7790001', 'nombre': 'YERBA X 1KG', 'cantidad': 0, 'precio_compra': 2400.0,
         'existe': true, 'nombre_sistema': 'Yerba Mate 1kg', 'stock_actual': 20, 'precio_compra_actual': 2420.0,
         'posible_duplicado': false, 'precio_sospechoso': false, 'emparejamiento': null},
      ];
      await abrirFactura(tester);
      expect(find.text('Sumar 1 ítems al stock'), findsOneWidget, reason: 'el de cantidad 0 arranca destildado');
      expect(find.textContaining('Cantidad inválida'), findsOneWidget);

      // Leo lo tilda igual: se frena antes de mandar (la PC rechazaría la factura entera)
      await tester.tap(find.byKey(const Key('tildar_1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sumar 2 ítems al stock'));
      await tester.pumpAndSettle();
      expect(find.textContaining('con cantidad inválida'), findsOneWidget);
      expect(find.text('¿Sumar al stock?'), findsNothing);
      expect(servidor.itemsFacturaAplicados, isEmpty);
      await terminar(tester);
    });

    testWidgets('si se corta al sumar, destilda todo, avisa y trae el stock actual', (tester) async {
      await abrirFactura(tester);
      servidor.cortarEscrituras = true;
      await tester.tap(find.text('Sumar 1 ítems al stock'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sumar'));
      await tester.pumpAndSettle();
      expect(servidor.stockCafe, 15, reason: 'la PC sí lo sumó');
      expect(find.textContaining('no se sabe si se aplicó'), findsOneWidget);
      expect(find.text('Sumar 0 ítems al stock'), findsOneWidget, reason: 'no se puede repetir sin querer');
      expect(find.textContaining('(stock 15)'), findsOneWidget);
      await terminar(tester);
    });
  });

  // ------------------------------------------------------------------ //
  // Contrato 2 (ApiCelular)
  // ------------------------------------------------------------------ //

  Future<void> irA(WidgetTester tester, String pestana) async {
    await tester.tap(find.text(pestana));
    await tester.pumpAndSettle();
  }

  Future<void> ingresarEnLogin(WidgetTester tester, {String direccion = ServidorFalso.direccion}) async {
    await tester.enterText(find.byType(TextField).first, direccion);
    await tester.enterText(find.byKey(const Key('campo_pin')), ServidorFalso.pin);
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();
  }

  bool habilitado(WidgetTester tester, String texto) => tester
      .widget<ButtonStyleButton>(
          find.ancestor(of: find.text(texto), matching: find.byWidgetPredicate((w) => w is ButtonStyleButton)).first)
      .enabled;

  /// Toca [f] aunque esté más abajo en la lista (los ListView arman solo lo que se ve).
  Future<void> tocar(WidgetTester tester, Finder f) async {
    if (f.evaluate().isEmpty) await tester.dragUntilVisible(f, find.byType(ListView).last, const Offset(0, -250));
    await tester.ensureVisible(f);
    await tester.pumpAndSettle();
    await tester.tap(f);
    await tester.pumpAndSettle();
  }

  Future<void> buscarEnStock(WidgetTester tester, String codigo) async {
    await irA(tester, 'Stock');
    await tester.enterText(find.byType(TextField).first, codigo);
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
  }

  group('login (contrato 2)', () {
    testWidgets('una dirección de la LAN no se usa: ni el PIN ni la prueba salen', (tester) async {
      await arrancar(tester);
      await ingresarEnLogin(tester, direccion: '192.168.0.10');
      expect(find.text(mensajeNoTailscale), findsOneWidget);
      await tester.tap(find.text('Probar'));
      await tester.pumpAndSettle();
      expect(servidor.pedidos, isEmpty);
      expect(sesion.estado, EstadoSesion.sinSesion);
    });

    for (final (nombre, crear, mensaje) in <(String, Impostor Function(), String)>[
      ('el Dueño Remoto', Impostor.remoteApi, 'Ese es el puerto del Dueño Remoto (8765). La app usa el 8766.'),
      ('el ApiDueno viejo', Impostor.apiDuenoViejo, 'En esa dirección contesta otro programa, no la API del celular de Otter.'),
    ]) {
      testWidgets('si en la dirección contesta $nombre, lo dice y el PIN no sale', (tester) async {
        final impostor = crear();
        sesion = SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: impostor.crear);
        await arrancar(tester);
        await ingresarEnLogin(tester);
        expect(find.text(mensaje), findsOneWidget);
        expect(impostor.pedidos, ['GET /api/salud']);
        expect(sesion.estado, EstadoSesion.sinSesion);
      });
    }

    testWidgets('"Probar" muestra el negocio y si la PC encontró la base', (tester) async {
      await arrancar(tester);
      await tester.enterText(find.byType(TextField).first, ServidorFalso.direccion);
      await tester.tap(find.text('Probar'));
      await tester.pumpAndSettle();
      expect(find.text('Conectado a "El Galpón Del Nono"'), findsOneWidget);
      expect(find.text('La PC encontró la base del negocio.'), findsOneWidget);

      servidor.salud = ejemplo('salud', 2) as Map<String, dynamic>; // sin nombre de negocio y sin PIN
      await tester.tap(find.text('Probar'));
      await tester.pumpAndSettle();
      expect(find.text('Contesta la API del celular, pero sin nombre de negocio: puede estar instalada en otra carpeta.'),
          findsOneWidget);
      expect(find.text('Todavía no hay PIN para el celular: hay que definirlo en la PC del local.'), findsOneWidget);

      servidor.salud = ejemplo('salud', 1) as Map<String, dynamic>; // base con columnas viejas
      await tester.tap(find.text('Probar'));
      await tester.pumpAndSettle();
      expect(find.textContaining('faltan columnas: Productos.subrubro'), findsOneWidget);
    });

    testWidgets('sin PIN definido en la PC, muestra lo que dice la PC', (tester) async {
      servidor.pinDefinido = false;
      await arrancar(tester);
      await ingresarEnLogin(tester);
      expect(find.text('El PIN viejo quedó anulado: hay que definir uno nuevo en la PC del local.'), findsOneWidget);
    });

    testWidgets('viniendo de la versión 1 (puerto 8765) avisa y deja la misma PC puesta', (tester) async {
      almacen.datos.addAll({'servidor': 'http://100.101.102.103:8765', 'token': 'viejo', 'token_expira': '4102444800'});
      await arrancar(tester);
      expect(find.text('La app ahora usa el puerto 8766: volvé a ingresar'), findsOneWidget);
      expect(tester.widget<TextField>(find.byType(TextField).first).controller!.text, ServidorFalso.direccion);
      await tester.enterText(find.byKey(const Key('campo_pin')), ServidorFalso.pin);
      await tester.tap(find.text('Ingresar'));
      await tester.pumpAndSettle();
      expect(find.text('Ventas de hoy'), findsOneWidget);
      expect(almacen.datos['servidor'], ServidorFalso.servidorNormalizado);
      await terminar(tester);
    });
  });

  testWidgets('dashboard: avisa si el bot de Telegram está apagado', (tester) async {
    servidor.telegramHabilitado = false;
    await arrancar(tester, logueado: true);
    expect(find.text('El bot de Telegram está apagado: las alertas no se mandan.'), findsOneWidget);
    await terminar(tester);
  });

  group('stock (contrato 2)', () {
    testWidgets('el detalle muestra lo que cobra la Caja (con OFERTA) y quién hizo cada movimiento', (tester) async {
      await arrancar(tester, logueado: true);
      await buscarEnStock(tester, '7790002');
      expect(find.text(r'Precio $ 3.870'), findsOneWidget);
      expect(find.text(r'$ 4.300'), findsOneWidget, reason: 'el de lista, tachado');
      expect(find.text('OFERTA'), findsOneWidget);
      await tester.dragUntilVisible(find.text('ÚLTIMOS MOVIMIENTOS'), find.byType(ListView).first, const Offset(0, -300));
      await tester.dragUntilVisible(find.textContaining('dueño (celular)').first, find.byType(ListView).first,
          const Offset(0, -300));
      expect(find.textContaining('dueño (celular)'), findsWidgets);
      await terminar(tester);
    });

    testWidgets('con ventas sin descontar avisa cómo va a quedar el stock (12 cargadas, 6 vendidas → 6)', (tester) async {
      await arrancar(tester, logueado: true);
      await buscarEnStock(tester, '7790003');
      await tester.tap(find.text('12'));
      await tester.pumpAndSettle();
      await tocar(tester, find.text('Sumar 12'));
      expect(servidor.catalogo['7790003']!['stock'], 12);
      expect(find.byKey(const Key('aviso_pendientes')), findsOneWidget);
      expect(find.textContaining('En unos segundos el stock va a quedar en 6.'), findsOneWidget);
      expect(find.textContaining('sumale esas 6'), findsOneWidget);
      await terminar(tester);
    });

    testWidgets('el 503 en texto plano es "PC ocupada", no "no se sabe si se aplicó"', (tester) async {
      await arrancar(tester, logueado: true);
      await buscarEnStock(tester, '7790002');
      servidor.ocupada = true;
      await tester.tap(find.text('12'));
      await tester.pumpAndSettle();
      await tocar(tester, find.text('Sumar 12'));
      expect(find.text('La PC está ocupada: probá en unos segundos.'), findsOneWidget);
      expect(find.textContaining('No se sabe si se aplicó'), findsNothing);
      expect(servidor.stockCafe, 3);
      await terminar(tester);
    });

    testWidgets('modo lector: sin stock dice que falta el inventario; un código desconocido, que no existe',
        (tester) async {
      conReloj();
      await arrancar(tester, logueado: true);
      tester.view.physicalSize = const Size(2200, 2400); // más ancho: la fuente de los tests es grande
      await irA(tester, 'Stock');
      await irA(tester, 'Modo lector');
      Future<void> escribir(String codigo) async {
        await tester.enterText(find.widgetWithText(TextField, 'O escribí el código y Enter'), codigo);
        await tester.testTextInput.receiveAction(TextInputAction.done);
        await tester.pumpAndSettle();
      }

      await escribir('7790003'); // coca: stock 0, restar
      expect(find.textContaining('El sistema tiene 0 en stock de este producto (falta cargar el inventario).'),
          findsWidgets);
      await escribir('0000000');
      expect(find.text('Código 0000000: no existe en el sistema'), findsWidgets);
      await tester.tap(find.text('Sumar 1'));
      await tester.pumpAndSettle();
      await escribir('7790003');
      expect(find.textContaining('vendida(s) sin descontar todavía'), findsWidgets);
      await terminar(tester);
    });
  });

  group('precios (contrato 2)', () {
    String campo(WidgetTester tester, String c) =>
        tester.widget<TextField>(find.byKey(Key('campo_$c'))).controller!.text;

    Future<void> cambiarPrecioFinal(WidgetTester tester, String texto) async {
      await tester.enterText(find.byKey(const Key('campo_precio_final')), texto);
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
    }

    testWidgets('la cadena de 4 campos la recalcula la PC y se guarda con los crudos como esperado', (tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Precios');
      expect(find.text(r'$ 3.870'), findsOneWidget, reason: 'la lista muestra lo que cobra hoy la Caja');
      expect(find.text('OFERTA'), findsWidgets);
      await tester.tap(find.text('Café Molido 500g'));
      await tester.pumpAndSettle();
      expect([for (final c in ['costo_sin_iva', 'precio_costo', 'margen', 'precio_final']) campo(tester, c)],
          ['2.479,34', '3.000', '43,33', '4.300']);
      expect(find.text(r'La Caja cobra hoy $ 3.870 por una oferta hasta el 09/10.'), findsOneWidget);

      await cambiarPrecioFinal(tester, '4500');
      expect(servidor.cuerpos['POST /api/precios/recalcular']!.single, {
        'cambio': 'precio_final', 'costo_sin_iva': '2.479,34', 'precio_costo': '3.000', 'margen': '43,33',
        'precio_final': '4500',
      });
      expect(campo(tester, 'margen'), '50', reason: 'lo calculó la PC');
      expect(campo(tester, 'precio_final'), '4.500', reason: 'el campo tocado se reescribe como lo entendió la PC');

      await tester.tap(find.text('Guardar precio'));
      await tester.pumpAndSettle();
      final dialogo = find.byType(AlertDialog);
      expect(find.descendant(of: dialogo, matching: find.text('Café Molido 500g')), findsOneWidget);
      expect(find.descendant(of: dialogo, matching: find.text(r'$ 4.300 → $ 4.500')), findsOneWidget);
      expect(find.descendant(of: dialogo, matching: find.text('43,33 % → 50 %')), findsOneWidget);
      expect(servidor.pedidos, isNot(contains('PUT /api/precios/7790002')), reason: 'nada sin confirmar');
      await tester.tap(find.text('Guardar'));
      await tester.pumpAndSettle();

      final put = servidor.cuerpos['PUT /api/precios/7790002']!.single as Map;
      expect(put['esperado'],
          {'costo_sin_iva': 2479.34, 'precio_compra': 3000.0, 'margen_ganancia': 43.33, 'precio_venta': 4300.0},
          reason: 'los crudos tal cual vinieron de la PC');
      expect([put['costo_sin_iva'], put['precio_costo'], put['margen'], put['precio_final']], [2479.34, 3000.0, 50.0, 4500.0]);
      expect(find.text('Guardar precio'), findsNothing, reason: 'la hoja se cerró');
      expect(find.text(r'Café Molido 500g: $ 4.300 → $ 4.500'), findsOneWidget);
      expect(find.text(r'$ 4.050'), findsOneWidget, reason: 'la fila muestra lo que cobra ahora la Caja (oferta del 10 %)');
      await terminar(tester);
    });

    testWidgets('con una oferta de precio fijo, la confirmación avisa que la Caja sigue cobrando la oferta',
        (tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Precios');
      await tester.tap(find.text('Coca Cola 2,25 L'));
      await tester.pumpAndSettle();
      expect(campo(tester, 'costo_sin_iva'), '', reason: 'un 0 guardado es "no cargado": se ve vacío');
      await cambiarPrecioFinal(tester, '2800');
      await tester.tap(find.text('Guardar precio'));
      await tester.pumpAndSettle();
      expect(find.text(r'Mientras dure la oferta la Caja sigue cobrando $ 2.000.'), findsOneWidget);
      await tester.tap(find.text('Cancelar'));
      await tester.pumpAndSettle();
      expect(servidor.pedidos.where((p) => p.startsWith('PUT')), isEmpty);
      await terminar(tester);
    });

    testWidgets('si el costo cambió mientras la hoja estaba abierta: 409, dice qué cambió y recarga', (tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Precios');
      await tester.tap(find.text('Yerba Mate 1kg'));
      await tester.pumpAndSettle();
      servidor.cambiarPorFuera('7790001', 'precio_compra', 3000); // p. ej. una factura desde el Panel
      await cambiarPrecioFinal(tester, '3800');
      await tester.tap(find.text('Guardar precio'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Guardar'));
      await tester.pumpAndSettle();
      expect(find.text(r'El precio de costo cambió mientras tanto: era $ 2.420, ahora es $ 3.000. Revisalo de nuevo.'),
          findsOneWidget);
      expect(campo(tester, 'precio_costo'), '3.000', reason: 'se recargó con lo que hay ahora');
      expect(campo(tester, 'precio_final'), '3.500');
      expect(servidor.catalogo['7790001']!['precio_venta'], 3500.0, reason: 'no se pisó nada');
      await terminar(tester);
    });

    testWidgets('si se corta al guardar, la hoja se recarga con lo que quedó', (tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Precios');
      await tester.tap(find.text('Yerba Mate 1kg'));
      await tester.pumpAndSettle();
      await cambiarPrecioFinal(tester, '3800');
      servidor.cortarEscrituras = true;
      await tester.tap(find.text('Guardar precio'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Guardar'));
      await tester.pumpAndSettle();
      servidor.cortarEscrituras = false;
      expect(find.textContaining('No se sabe si se aplicó'), findsOneWidget);
      expect(find.text('Guardar precio'), findsOneWidget, reason: 'la hoja sigue abierta');
      expect(campo(tester, 'precio_final'), '3.800', reason: 'el PUT había llegado: se ve lo guardado');
      await terminar(tester);
    });

    Future<void> abrirAjuste(WidgetTester tester, {List<String> solo = const []}) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Precios');
      for (final nombre in solo) {
        await tester.tap(find.descendant(of: find.widgetWithText(ListTile, nombre), matching: find.byType(Checkbox)));
        await tester.pumpAndSettle();
      }
      await tester.tap(find.textContaining(solo.isEmpty ? 'Ajustar los' : 'Ajustar ${solo.length} seleccionados'));
      await tester.pumpAndSettle();
    }

    Future<void> escribirAjuste(WidgetTester tester, String valor) async {
      await tester.enterText(find.byType(TextField).first, valor);
      await tester.pumpAndSettle();
    }

    testWidgets('ajuste: no se aplica si alguno quedaría en \$ 0; uno que ya estaba en \$ 0 no frena', (tester) async {
      await abrirAjuste(tester);
      await escribirAjuste(tester, '-100');
      expect(find.textContaining('tiene que estar entre'), findsOneWidget, reason: 'el porcentaje va de −90 a 500');
      expect(habilitado(tester, 'Ver cómo quedan'), isFalse);

      await tester.tap(find.text('Monto fijo'));
      await tester.pumpAndSettle();
      await escribirAjuste(tester, '-5000');
      await tocar(tester, find.text('Ver cómo quedan'));
      await tester.dragUntilVisible(find.text('Aplicar a 4 productos'), find.byType(ListView), const Offset(0, -300));
      expect(find.textContaining(r'3 producto(s) quedarían en $ 0'), findsOneWidget);
      expect(habilitado(tester, 'Aplicar a 4 productos'), isFalse);

      await tester.dragUntilVisible(find.text('Porcentaje'), find.byType(ListView), const Offset(0, 300));
      await tester.tap(find.text('Porcentaje'));
      await tester.pumpAndSettle();
      await escribirAjuste(tester, '10');
      await tocar(tester, find.text('Ver cómo quedan'));
      expect(find.textContaining('La vista previa muestra exactamente lo que se va a guardar'), findsOneWidget);
      expect(find.text('en oferta'), findsWidgets);
      await tester.dragUntilVisible(find.text('Aplicar a 4 productos'), find.byType(ListView), const Offset(0, -300));
      expect(find.textContaining('quedarían en'), findsNothing);
      expect(habilitado(tester, 'Aplicar a 4 productos'), isTrue, reason: 'el alfajor pasa de \$ 0 a \$ 0');
      await tocar(tester, find.text('Aplicar a 4 productos'));
      await tester.tap(find.text('Aplicar'));
      await tester.pumpAndSettle();

      final cuerpo = servidor.cuerpos['POST /api/precios/aplicar']!.single as Map;
      expect((cuerpo['esperados'] as Map).keys.toSet(), (cuerpo['codigos'] as List).toSet());
      expect(cuerpo['esperados'], {'7790002': 4300.0, '7790001': 3500.0, '7790003': 2500.0, '7790004': 0.0});
      expect(find.text('Listo: 4 precios actualizados.'), findsOneWidget);
      await terminar(tester);
    });

    testWidgets('ajuste: tras un corte obliga a ver la vista previa y aplicar de nuevo no suma dos veces',
        (tester) async {
      await abrirAjuste(tester, solo: ['Café Molido 500g', 'Yerba Mate 1kg']);
      await escribirAjuste(tester, '10');
      await tocar(tester, find.text('Ver cómo quedan'));
      servidor.cortarEscrituras = true;
      await tocar(tester, find.text('Aplicar a 2 productos'));
      await tester.tap(find.text('Aplicar'));
      await tester.pumpAndSettle();
      servidor.cortarEscrituras = false;
      expect(servidor.catalogo['7790002']!['precio_venta'], 4800.0, reason: 'el primero sí llegó');
      expect(find.textContaining('No se sabe si se aplicó'), findsOneWidget);
      expect(find.textContaining('no se sabe si el ajuste se aplicó'), findsOneWidget);
      expect(find.text('Ver cómo quedan'), findsOneWidget, reason: 'hay que volver a ver la vista previa');
      expect(find.text('Aplicar a 2 productos'), findsNothing);
      await tester.pump(const Duration(seconds: 6)); // que se vaya el aviso de abajo
      await tester.pumpAndSettle();

      await tocar(tester, find.text('Ver cómo quedan'));
      await tocar(tester, find.text('Aplicar a 2 productos'));
      await tester.tap(find.text('Aplicar'));
      await tester.pumpAndSettle();
      expect(servidor.catalogo['7790002']!['precio_venta'], 4800.0, reason: 'no se aplicó dos veces');
      expect(servidor.catalogo['7790001']!['precio_venta'], 3900.0);
      expect(find.text('Ninguno cambió: ya tenían otro precio (¿se había aplicado antes?).'), findsOneWidget);
      await terminar(tester);
    });
  });

  group('alertas (contrato 2)', () {
    Future<void> abrirConfig(WidgetTester tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Alertas');
      await tester.tap(find.byTooltip('Configurar alertas'));
      await tester.pumpAndSettle();
    }

    testWidgets('Telegram: solo prender/apagar y probar; el token no aparece en ningún lado', (tester) async {
      await abrirConfig(tester);
      expect(find.text('Chat: -1001234567890 · Token: configurado (oculto) — se cargan en el Panel de la PC'),
          findsOneWidget);
      expect(find.widgetWithText(TextField, 'Token del bot'), findsNothing);
      expect(find.widgetWithText(TextField, 'Chat ID'), findsNothing);
      final textos = [
        for (final t in tester.widgetList<Text>(find.byType(Text, skipOffstage: false)))
          t.data ?? t.textSpan?.toPlainText() ?? '',
        for (final e in tester.widgetList<EditableText>(find.byType(EditableText, skipOffstage: false)))
          e.controller.text,
      ];
      expect(textos.where((t) => t.contains('7123456789') || t.contains('AAH-')), isEmpty);

      await tocar(tester, find.text('Enviar mensaje de prueba'));
      expect(find.textContaining('Mensaje enviado'), findsOneWidget);

      await tocar(tester, find.text('Enviar avisos por Telegram'));
      expect(find.text('No va a llegar ninguna alerta hasta que lo vuelvas a prender.'), findsOneWidget);
      await tester.tap(find.text('Apagar'));
      await tester.pumpAndSettle();
      expect(servidor.cuerpos['PUT /api/config/telegram']!.single, {'habilitado': false});
      expect(servidor.telegramHabilitado, isFalse);
      await terminar(tester);
    });

    testWidgets('umbral global: nunca abre vacío, un vacío no es 0, y dice qué va a pasar', (tester) async {
      await abrirConfig(tester);
      String texto(String clave) => tester.widget<TextField>(find.byKey(Key(clave))).controller!.text;
      expect([texto('campo_minimo'), texto('campo_maximo')], ['20', '20']);
      expect(find.text('Poné 0 para no recibir ese aviso. Con 0 y 0 no llega ninguna alerta.'), findsOneWidget);
      expect(
          find.text('3 productos tienen umbral propio: a esos el global no les cambia nada '
              '(1 de esos está apagado y no avisa)'),
          findsOneWidget);

      await tester.enterText(find.byKey(const Key('campo_minimo')), '');
      await tocar(tester, find.text('Guardar umbrales'));
      expect(find.textContaining('Completá el mínimo y el máximo'), findsOneWidget);
      expect(servidor.pedidos, isNot(contains('PUT /api/config/umbrales')));

      await tester.enterText(find.byKey(const Key('campo_minimo')), '0');
      await tester.enterText(find.byKey(const Key('campo_maximo')), '0');
      await tocar(tester, find.text('Guardar umbrales'));
      expect(find.text('Umbral global guardado'), findsOneWidget);
      expect(find.textContaining('No va a llegar ninguna alerta de stock.'), findsOneWidget);
      expect(servidor.cuerpos['PUT /api/config/umbrales']!.single, {'stock_minimo': 0, 'stock_maximo': 0});
      await tester.tap(find.text('Entendido'));
      await tester.pumpAndSettle();
      await terminar(tester);
    });

    testWidgets('quitar el umbral global pide confirmación (y no toca los propios)', (tester) async {
      await abrirConfig(tester);
      final boton = find.widgetWithText(OutlinedButton, 'Quitar el umbral global');
      await tocar(tester, boton);
      final dialogo = find.byType(AlertDialog);
      expect(find.descendant(of: dialogo, matching: find.textContaining('(mínimo 20, máximo 20) se va a borrar')),
          findsOneWidget);
      expect(find.descendant(of: dialogo, matching: find.textContaining('Los 3 producto(s) CON umbral propio siguen avisando')),
          findsOneWidget);
      await tester.tap(find.text('Cancelar'));
      await tester.pumpAndSettle();
      expect(servidor.pedidos, isNot(contains('DELETE /api/config/umbral-global')));

      await tocar(tester, boton);
      await tester.tap(find.descendant(of: find.byType(AlertDialog), matching: find.text('Quitar')));
      await tester.pumpAndSettle();
      expect(servidor.pedidos, contains('DELETE /api/config/umbral-global'));
      expect(find.textContaining('Listo (1 fila(s)).'), findsOneWidget);
      await tester.tap(find.text('Entendido'));
      await tester.pumpAndSettle();
      expect(boton, findsNothing, reason: 'ya no hay umbral global para quitar');
      expect(find.text('Hoy no hay umbral global: los productos sin umbral propio no avisan.'), findsOneWidget);
      await terminar(tester);
    });

    testWidgets('con el bot apagado la lista avisa que no se mandan', (tester) async {
      servidor.telegramHabilitado = false;
      await arrancar(tester, logueado: true);
      await irA(tester, 'Alertas');
      expect(find.text('El bot está apagado: estas alertas no se mandan por Telegram.'), findsOneWidget);
      await terminar(tester);
    });
  });

  group('facturas (contrato 2)', () {
    late FilePickerPlatform selectorOriginal;
    setUp(() {
      selectorOriginal = FilePickerPlatform.instance;
      FilePickerPlatform.instance = SelectorFalso();
    });
    tearDown(() => FilePickerPlatform.instance = selectorOriginal);

    Future<void> elegirPdf(WidgetTester tester) async {
      await tester.tap(find.text('Elegir PDF'));
      await tester.pumpAndSettle();
    }

    Future<void> sumar(WidgetTester tester, int items) async {
      await tester.tap(find.text('Sumar $items ítems al stock'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sumar'));
      await tester.pumpAndSettle();
    }

    testWidgets('un renglón emparejado por nombre no se tilda solo: lo confirma Leo eligiéndolo', (tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Facturas');
      await elegirPdf(tester);
      expect(find.text('Sumar 1 ítems al stock'), findsOneWidget, reason: 'ni el SEGURA ni el POSIBLE se tildan solos');
      expect(find.text('Coincidencia segura'), findsOneWidget);
      expect(tester.widget<Checkbox>(find.byKey(const Key('tildar_1'))).value, isFalse);

      await tocar(tester, find.text('¿Es Coca Cola 2,25 L?'));
      expect(find.text('100 %'), findsOneWidget);
      await tester.tap(find.text('Coca Cola 2,25 L').last);
      await tester.pumpAndSettle();
      expect(tester.widget<Checkbox>(find.byKey(const Key('tildar_1'))).value, isTrue);
      expect(find.text('Sumar 2 ítems al stock'), findsOneWidget);

      // el de precio sospechoso: al elegirlo, la casilla de actualizar el costo queda destildada
      expect(find.text(r'El precio leído ($ 3,50) parece raro: revisalo'), findsOneWidget);
      await tocar(tester, find.text('Elegir producto'));
      await tester.tap(find.text('Yerba Mate 1kg'));
      await tester.pumpAndSettle();
      expect(find.text(r'El precio leído ($ 3,50) es muy distinto del costo guardado ($ 2.420): revisalo'), findsOneWidget);
      expect(tester.widget<Checkbox>(find.byKey(const Key('actualizar_costo_2'))).value, isFalse);

      await tester.dragUntilVisible(find.textContaining('No se buscó por nombre'), find.byType(ListView), const Offset(0, -300));
      expect(find.text(textoDemasiadosEsperado), findsOneWidget);

      await tester.tap(find.text('Sumar 3 ítems al stock'));
      await tester.pumpAndSettle();
      expect(find.textContaining('2 de esos ítems los emparejaste por nombre'), findsOneWidget);
      await tester.tap(find.text('Sumar'));
      await tester.pumpAndSettle();
      expect([for (final i in servidor.itemsFacturaAplicados) [i['codigo'], i['cantidad'], i['precio_compra']]], [
        ['7790002', 12, 3000.0],
        ['7790003', 6, 1250.0],
        ['7790001', 4, null], // sospechoso: no pisa el costo
      ]);
      await terminar(tester);
    });

    testWidgets('factura ya cargada: "No cargar" no manda nada y "Cargar igual" reenvía con forzar', (tester) async {
      await arrancar(tester, logueado: true);
      await irA(tester, 'Facturas');
      await elegirPdf(tester);
      await sumar(tester, 1);
      expect(find.text('¡Listo! Stock actualizado'), findsOneWidget);
      await tester.tap(find.text('Cargar otra factura'));
      await tester.pumpAndSettle();

      await elegirPdf(tester); // la misma factura otra vez
      await sumar(tester, 1);
      expect(find.text('¿Esta factura ya se cargó?'), findsOneWidget);
      expect(find.textContaining('Esta factura ya se cargó hace 0 min (1 renglones iguales)'), findsOneWidget);
      await tester.tap(find.text('No cargar'));
      await tester.pumpAndSettle();
      final aplicar = servidor.cuerpos['POST /api/facturas/aplicar']!;
      expect(aplicar, hasLength(2), reason: '"No cargar" no manda nada más');
      expect(servidor.stockCafe, 15, reason: 'se sumó una sola vez');

      await sumar(tester, 1);
      await tester.tap(find.text('Cargar igual'));
      await tester.pumpAndSettle();
      expect(aplicar, hasLength(4));
      expect((aplicar.last as Map)['forzar'], isTrue);
      expect(servidor.stockCafe, 27);
      expect(find.text('¡Listo! Stock actualizado'), findsOneWidget);
      await terminar(tester);
    });
  });

  testWidgets('ajustes: la IP de la PC sale tapada salvo mientras se mantiene apretada', (tester) async {
    await arrancar(tester, logueado: true);
    await tester.tap(find.byTooltip('Ajustes').first);
    await tester.pumpAndSettle();
    final renglon = find.byKey(const Key('renglon_ip'));
    await tester.dragUntilVisible(renglon, find.byType(ListView), const Offset(0, -200));
    await tester.pumpAndSettle();
    expect(find.textContaining('100.101.102.103'), findsNothing);
    expect(find.textContaining('100.•••.•••.•••'), findsOneWidget);
    expect(find.text('Versión 2.0.0 · compilado 4c01dfc · contrato 2'), findsOneWidget);
    expect(find.textContaining('ApiCelular.exe cerrar-sesiones'), findsOneWidget);

    final gesto = await tester.startGesture(tester.getCenter(renglon));
    await tester.pump(kLongPressTimeout + const Duration(milliseconds: 100));
    expect(find.text('100.101.102.103'), findsOneWidget);
    await gesto.up();
    await tester.pump();
    expect(find.textContaining('100.101.102.103'), findsNothing);
    await terminar(tester);
  });
}
