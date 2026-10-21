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
      child: const Raiz(),
    );
  }
}

/// Arma la app, decide qué se ve según la sesión, y re-bloquea la app si
/// estuvo un rato en segundo plano (tiene control total del negocio).
///
/// El bloqueo y el login van ARRIBA del Navigator de la app (en el `builder`
/// de MaterialApp), no adentro de una ruta: si no, las pantallas, hojas y
/// diálogos que Leo dejó abiertos quedaban por encima del bloqueo y se
/// podían seguir usando con la app bloqueada.
class Raiz extends StatefulWidget {
  const Raiz({super.key});

  @override
  State<Raiz> createState() => _RaizState();
}

class _RaizState extends State<Raiz> with WidgetsBindingObserver {
  final _navegadorApp = GlobalKey<NavigatorState>(debugLabel: 'app');
  final _navegadorBloqueo = GlobalKey<NavigatorState>(debugLabel: 'bloqueo');
  final _navegadorLogin = GlobalKey<NavigatorState>(debugLabel: 'login');

  /// Si Leo ya estaba usando la app y se bloqueó por inactividad, la
  /// pantalla de bloqueo TAPA lo que estaba haciendo en vez de reemplazarlo:
  /// al desbloquear sigue en la misma pestaña, con la factura o el ajuste a
  /// medio revisar.
  bool _yaEntro = false;

  EstadoSesion? _estadoAnterior;

  @override
  void initState() {
    super.initState();
    // Queda registrado antes que el de MaterialApp (que se arma más abajo),
    // así el botón atrás pasa primero por acá: ver didPopRoute.
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

  /// Con la app bloqueada (o sin sesión), el botón atrás no puede tocar lo
  /// que quedó tapado: solo cierra un diálogo de la capa que se ve.
  @override
  Future<bool> didPopRoute() async {
    final estado = context.read<SesionEstado>().estado;
    if (estado == EstadoSesion.activa) return false; // lo maneja MaterialApp
    final capa = estado == EstadoSesion.bloqueada ? _navegadorBloqueo : _navegadorLogin;
    if (await capa.currentState?.maybePop() ?? false) return true;
    // Si abajo quedaron pantallas abiertas, el atrás no hace nada; si no,
    // sigue el camino de siempre (sale de la app).
    return _navegadorApp.currentState?.canPop() ?? false;
  }

  /// Cada Navigator le avisa a Android si el atrás lo maneja la app (con el
  /// "atrás predictivo", que viene por defecto desde Android 16, si dice que
  /// no, Android manda la app al fondo sin preguntar). El del bloqueo avisa
  /// "no" al aparecer y, al desbloquear, el de la app no vuelve a avisar
  /// porque sus rutas no cambiaron: con Ajustes u otra pantalla abierta, el
  /// atrás cerraba la app en vez de esa pantalla. Por eso se le recuerda.
  void _reavisarAtras() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final navegador = _navegadorApp.currentState;
      if (navegador == null || !navegador.mounted) return;
      NavigationNotification(canHandlePop: navegador.canPop()).dispatch(navegador.context);
    });
  }

  @override
  Widget build(BuildContext context) {
    final ajustes = context.watch<AjustesEstado>();
    final estado = context.select<SesionEstado, EstadoSesion>((s) => s.estado);
    if (estado == EstadoSesion.activa) _yaEntro = true;
    // Sin sesión (token vencido o cerró sesión) se descarta el Navigator de
    // la app con todas sus rutas: al volver a entrar arranca desde el Inicio.
    if (estado == EstadoSesion.sinSesion) _yaEntro = false;
    if (estado == EstadoSesion.activa && _estadoAnterior != EstadoSesion.activa) _reavisarAtras();
    _estadoAnterior = estado;
    return MaterialApp(
      navigatorKey: _navegadorApp,
      title: 'Panel Dueño',
      debugShowCheckedModeBanner: false,
      theme: temaClaro(),
      darkTheme: temaOscuro(),
      themeMode: ajustes.modoTema,
      locale: const Locale('es', 'AR'),
      supportedLocales: const [Locale('es', 'AR'), Locale('es')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      builder: (context, navegador) => _capas(estado, navegador!),
      home: const PantallaInicio(),
    );
  }

  Widget _capas(EstadoSesion estado, Widget navegador) {
    final activa = estado == EstadoSesion.activa;
    return Stack(fit: StackFit.expand, children: [
      if (_yaEntro) _Tapada(key: const ValueKey('app'), tapada: !activa, child: navegador),
      if (estado == EstadoSesion.bloqueada)
        _Capa(key: const ValueKey('bloqueo'), navegador: _navegadorBloqueo, child: const PantallaBloqueo()),
      if (estado == EstadoSesion.sinSesion)
        _Capa(key: const ValueKey('login'), navegador: _navegadorLogin, child: const PantallaLogin()),
      if (estado == EstadoSesion.cargando)
        const Scaffold(key: ValueKey('cargando'), body: Center(child: CircularProgressIndicator())),
    ]);
  }
}

/// La app debajo del bloqueo: no se puede tocar ni enfocar (el teclado no
/// puede quedar escribiendo en un campo tapado), el lector de pantalla no
/// la lee y sus animaciones se pausan. Sigue viva para retomarla tal cual.
class _Tapada extends StatelessWidget {
  const _Tapada({super.key, required this.tapada, required this.child});
  final bool tapada;
  final Widget child;

  @override
  Widget build(BuildContext context) => IgnorePointer(
        ignoring: tapada,
        child: ExcludeFocus(
          excluding: tapada,
          child: ExcludeSemantics(
            excluding: tapada,
            child: TickerMode(enabled: !tapada, child: child),
          ),
        ),
      );
}

/// Capa de bloqueo o de login, arriba de toda la app. Tiene su propio
/// Navigator (que trae el Overlay que necesitan los TextField y un lugar
/// para sus diálogos) y su propio ScaffoldMessenger, para no mezclarse con
/// lo que quedó tapado.
class _Capa extends StatelessWidget {
  const _Capa({super.key, required this.navegador, required this.child});
  final GlobalKey<NavigatorState> navegador;
  final Widget child;

  @override
  Widget build(BuildContext context) => ScaffoldMessenger(
        // el HeroController de MaterialApp es del Navigator de la app: no se comparte
        child: HeroControllerScope.none(
          child: Navigator(
            key: navegador,
            onGenerateRoute: (_) => MaterialPageRoute(builder: (_) => child),
          ),
        ),
      );
}
