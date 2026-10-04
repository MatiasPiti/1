import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:panel_dueno/estado.dart';
import 'package:panel_dueno/main.dart';
import 'package:panel_dueno/widgets/comunes.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'servidor_falso.dart';

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
}
