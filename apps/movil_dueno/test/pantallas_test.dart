import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:panel_dueno/estado.dart';
import 'package:panel_dueno/main.dart';
import 'package:panel_dueno/pantallas/bloqueo.dart';
import 'package:panel_dueno/pantallas/precios.dart';
import 'package:panel_dueno/widgets/comunes.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'servidor_falso.dart';

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
    if (logueado) await sesion.ingresar('100.101.102.103', '1234');
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
    await tester.enterText(enBloqueo(TextField), '1234');
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

    await tester.enterText(find.byType(TextField).first, '100.101.102.103');
    await tester.enterText(find.byKey(const Key('campo_pin')), '1234');
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();

    expect(find.text('Ventas de hoy'), findsOneWidget);
    expect(find.textContaining('152.300'), findsOneWidget);
    expect(find.text('Almacén de Leo'), findsOneWidget);
    expect(find.text('Yerba Mate 1kg'), findsOneWidget);
    expect(find.text('1 producto necesita atención'), findsOneWidget);
    await terminar(tester);
  });

  testWidgets('PIN incorrecto muestra el error', (tester) async {
    await arrancar(tester);
    await tester.enterText(find.byType(TextField).first, '100.101.102.103');
    await tester.enterText(find.byKey(const Key('campo_pin')), '9999');
    await tester.tap(find.text('Ingresar'));
    await tester.pumpAndSettle();
    expect(find.text('PIN incorrecto'), findsOneWidget);
  });

  testWidgets('reabrir la app pide desbloquear', (tester) async {
    await (SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: servidor.crear)..cargar())
        .ingresar('100.101.102.103', '1234');
    await arrancar(tester);
    expect(find.text('¡Hola de nuevo!'), findsOneWidget);

    await tester.enterText(find.byType(TextField), '1234');
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

    await tester.enterText(find.byType(TextField).last, '1234');
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
    expect(find.text('Stock bajo (1)'.toUpperCase()), findsOneWidget);
    expect(find.text('Café Molido 500g'), findsOneWidget);
    expect(find.text('Avisos por Telegram activos'), findsOneWidget);
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
    final campoPrecio = find.descendant(of: find.byType(BottomSheet), matching: find.byType(EditableText));
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

    await tester.enterText(find.byKey(const Key('campo_pin')), '1234');
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
    await tester.enterText(find.byType(TextField).first, '100.101.102.103');
    await tester.enterText(find.byKey(const Key('campo_pin')), '1234');
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
    expect(find.text('1 productos'), findsOneWidget);
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
    expect(find.text('10'), findsOneWidget, reason: 'la lista muestra el stock real');
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
      await abrirFactura(tester);
      expect(find.text('Sumar 1 ítems al stock'), findsOneWidget, reason: 'el de cantidad 0 arranca destildado');
      expect(find.textContaining('Cantidad inválida'), findsOneWidget);

      // Leo lo tilda igual: se frena antes de mandar (la PC rechazaría la factura entera)
      await tester.tap(find.byType(Checkbox).last);
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
}
