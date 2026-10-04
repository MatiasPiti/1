import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'estado.dart';
import 'pantallas/bloqueo.dart';
import 'pantallas/inicio.dart';
import 'pantallas/login.dart';
import 'tema.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await initializeDateFormatting('es_AR');
  final prefs = await SharedPreferences.getInstance();
  final sesion = SesionEstado(almacen: AlmacenSeguro(), biometria: BiometriaDispositivo());
  runApp(PanelDuenoApp(sesion: sesion, ajustes: AjustesEstado(prefs)));
  await sesion.cargar();
}

class PanelDuenoApp extends StatelessWidget {
  const PanelDuenoApp({super.key, required this.sesion, required this.ajustes});
  final SesionEstado sesion;
  final AjustesEstado ajustes;

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: sesion),
        ChangeNotifierProvider.value(value: ajustes),
        ChangeNotifierProvider(create: (_) => AlertasEstado()),
      ],
      child: Consumer<AjustesEstado>(
        builder: (context, ajustes, _) => MaterialApp(
          title: 'Panel Dueño',
          debugShowCheckedModeBanner: false,
          theme: temaClaro(),
          darkTheme: temaOscuro(),
          themeMode: ajustes.modoTema,
          locale: const Locale('es', 'AR'),
          supportedLocales: const [Locale('es', 'AR'), Locale('es')],
          localizationsDelegates: GlobalMaterialLocalizations.delegates,
          home: const Raiz(),
        ),
      ),
    );
  }
}

/// Decide qué mostrar según la sesión, y re-bloquea la app si estuvo un
/// rato en segundo plano (tiene control total del negocio).
class Raiz extends StatefulWidget {
  const Raiz({super.key});

  @override
  State<Raiz> createState() => _RaizState();
}

class _RaizState extends State<Raiz> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final sesion = context.read<SesionEstado>();
    if (state == AppLifecycleState.paused) sesion.pasoASegundoPlano();
    if (state == AppLifecycleState.resumed) sesion.volvioAPrimerPlano();
  }

  @override
  Widget build(BuildContext context) {
    final estado = context.select<SesionEstado, EstadoSesion>((s) => s.estado);
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 300),
      child: switch (estado) {
        EstadoSesion.cargando => const Scaffold(body: Center(child: CircularProgressIndicator())),
        EstadoSesion.sinSesion => const PantallaLogin(key: ValueKey('login')),
        EstadoSesion.bloqueada => const PantallaBloqueo(key: ValueKey('bloqueo')),
        EstadoSesion.activa => const PantallaInicio(key: ValueKey('inicio')),
      },
    );
  }
}
