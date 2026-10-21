import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api/cliente_api.dart';
import 'api/modelos.dart';

/// Almacenamiento de la sesión (token, servidor). En el celular es el
/// llavero cifrado de Android; en los tests, un mapa en memoria.
abstract class Almacen {
  Future<String?> leer(String clave);
  Future<void> escribir(String clave, String valor);
  Future<void> borrar(String clave);
}

class AlmacenSeguro implements Almacen {
  final _s = const FlutterSecureStorage();
  @override
  Future<String?> leer(String clave) => _s.read(key: clave);
  @override
  Future<void> escribir(String clave, String valor) => _s.write(key: clave, value: valor);
  @override
  Future<void> borrar(String clave) => _s.delete(key: clave);
}

class AlmacenMemoria implements Almacen {
  final datos = <String, String>{};
  @override
  Future<String?> leer(String clave) async => datos[clave];
  @override
  Future<void> escribir(String clave, String valor) async => datos[clave] = valor;
  @override
  Future<void> borrar(String clave) async => datos.remove(clave);
}

abstract class Biometria {
  Future<bool> disponible();
  Future<bool> autenticar(String motivo);
}

class BiometriaDispositivo implements Biometria {
  final _auth = LocalAuthentication();

  @override
  Future<bool> disponible() async {
    try {
      return await _auth.isDeviceSupported() && (await _auth.getAvailableBiometrics()).isNotEmpty;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<bool> autenticar(String motivo) async {
    try {
      return await _auth.authenticate(localizedReason: motivo, biometricOnly: true, persistAcrossBackgrounding: true);
    } catch (_) {
      return false; // cancelado, bloqueado por intentos, sin huellas cargadas...
    }
  }
}

enum EstadoSesion { cargando, sinSesion, bloqueada, activa }

/// Sesión con la PC del local: servidor, token y bloqueo de la app.
class SesionEstado extends ChangeNotifier {
  SesionEstado({
    required this.almacen,
    required this.biometria,
    this.crearCliente = _clientePorDefecto,
    this.reloj = DateTime.now,
  });

  static ClienteApi _clientePorDefecto(String servidor) => ClienteApi(servidor: servidor);

  final Almacen almacen;
  final Biometria biometria;
  final ClienteApi Function(String servidor) crearCliente;
  final DateTime Function() reloj;

  static const _kServidor = 'servidor';
  static const _kToken = 'token';
  static const _kExpira = 'token_expira';
  static const _kUsuario = 'usuario';
  static const _kLocal = 'nombre_local';
  static const _kBiometria = 'biometria';

  /// Tras este tiempo en segundo plano, la app vuelve a pedir huella/PIN.
  static const bloqueoPorInactividad = Duration(minutes: 2);

  EstadoSesion estado = EstadoSesion.cargando;
  String? servidor;

  /// El servidor guardado era el de la app vieja (puerto 8765, donde hoy
  /// está el Dueño Remoto): hay que volver a ingresar contra el 8766.
  bool avisoPuertoViejo = false;
  String usuario = '';
  String nombreLocal = '';
  bool biometriaActiva = false;
  bool biometriaDisponible = false;
  ClienteApi? _api;
  DateTime? _enSegundoPlanDesde;

  ClienteApi get api => _api!;

  Future<void> cargar() async {
    servidor = await almacen.leer(_kServidor);
    usuario = await almacen.leer(_kUsuario) ?? '';
    nombreLocal = await almacen.leer(_kLocal) ?? '';
    biometriaActiva = await almacen.leer(_kBiometria) == '1';
    biometriaDisponible = await biometria.disponible();
    avisoPuertoViejo = false;
    final guardado = servidor == null ? null : Uri.tryParse(servidor!);
    if (guardado != null && guardado.hasPort && guardado.port == puertoDuenoRemoto) {
      // La versión 1 de la app hablaba con ApiDueno en el 8765. Ese token no
      // sirve en ApiCelular, y en ese puerto hoy contesta el Dueño Remoto:
      // se vuelve a ingresar, con la misma PC ya puesta en el 8766.
      avisoPuertoViejo = true;
      servidor = normalizarServidor(guardado.host);
      await almacen.borrar(_kToken);
      estado = EstadoSesion.sinSesion;
      notifyListeners();
      return;
    }
    final token = await almacen.leer(_kToken);
    final expira = int.tryParse(await almacen.leer(_kExpira) ?? '');
    final vigente = expira != null && DateTime.fromMillisecondsSinceEpoch(expira * 1000).isAfter(reloj());
    if (servidor != null && token != null && vigente) {
      _armarCliente(token);
      estado = EstadoSesion.bloqueada;
    } else {
      estado = EstadoSesion.sinSesion;
    }
    notifyListeners();
  }

  void _armarCliente(String? token) {
    _api = crearCliente(servidor!)
      ..token = token
      ..alVencerSesion = expirar;
  }

  /// Prueba la conexión sin loguear (botón "Probar conexión"): verifica que
  /// conteste la API del celular y devuelve lo que dice de sí misma.
  Future<Salud> probarServidor(String direccion) => crearCliente(direccion).salud();

  /// Primer ingreso (o reingreso tras vencer el token): servidor + PIN.
  ///
  /// [antesDeEntrar] corre con el PIN ya validado pero todavía en la
  /// pantalla de login (p. ej. para ofrecer la huella): al pasar a `activa`
  /// esa pantalla desaparece, y con ella cualquier diálogo que tuviera abierto.
  Future<void> ingresar(String direccion, String pin, {Future<void> Function()? antesDeEntrar}) async {
    final cliente = crearCliente(direccion);
    final salud = await cliente.salud();
    final sesion = await cliente.login(pin);
    servidor = cliente.servidor;
    avisoPuertoViejo = false;
    usuario = sesion.usuario;
    nombreLocal = salud.nombreLocal;
    await almacen.escribir(_kServidor, servidor!);
    await almacen.escribir(_kUsuario, usuario);
    await almacen.escribir(_kLocal, nombreLocal);
    await _guardarToken(sesion.token, sesion.expira);
    try {
      await antesDeEntrar?.call();
    } finally {
      estado = EstadoSesion.activa;
      notifyListeners();
    }
  }

  Future<void> _guardarToken(String token, DateTime expira) async {
    await almacen.escribir(_kToken, token);
    await almacen.escribir(_kExpira, '${expira.millisecondsSinceEpoch ~/ 1000}');
    _armarCliente(token);
  }

  Future<bool> desbloquearConBiometria() async {
    if (!biometriaActiva || !biometriaDisponible) return false;
    final ok = await biometria.autenticar('Confirmá que sos vos para entrar al panel del negocio');
    if (ok) {
      estado = EstadoSesion.activa;
      notifyListeners();
    }
    return ok;
  }

  /// El PIN se valida contra la PC (no se guarda en el celular) y renueva el token.
  Future<void> desbloquearConPin(String pin) async {
    final sesion = await crearCliente(servidor!).login(pin);
    usuario = sesion.usuario;
    await _guardarToken(sesion.token, sesion.expira);
    estado = EstadoSesion.activa;
    notifyListeners();
  }

  Future<void> configurarBiometria(bool activa) async {
    if (activa && !await biometria.autenticar('Confirmá tu huella o rostro para activar el desbloqueo')) return;
    biometriaActiva = activa;
    await almacen.escribir(_kBiometria, activa ? '1' : '0');
    notifyListeners();
  }

  void pasoASegundoPlano() => _enSegundoPlanDesde = reloj();

  void volvioAPrimerPlano() {
    final desde = _enSegundoPlanDesde;
    _enSegundoPlanDesde = null;
    if (estado == EstadoSesion.activa && desde != null &&
        reloj().difference(desde) >= bloqueoPorInactividad) {
      estado = EstadoSesion.bloqueada;
      notifyListeners();
    }
  }

  /// La PC rechazó el token (vencido o se reseteó el secreto): pedir PIN.
  void expirar() {
    if (estado == EstadoSesion.sinSesion) return;
    almacen.borrar(_kToken);
    estado = EstadoSesion.sinSesion;
    notifyListeners();
  }

  Future<void> cerrarSesion() async {
    await almacen.borrar(_kToken);
    await almacen.borrar(_kExpira);
    estado = EstadoSesion.sinSesion;
    notifyListeners();
  }
}

/// Preferencias visuales: Leo elige claro u oscuro elegante.
class AjustesEstado extends ChangeNotifier {
  AjustesEstado(this._prefs) : modoTema = _prefs.getString(_kTema) == 'claro' ? ThemeMode.light : ThemeMode.dark;

  final SharedPreferences _prefs;
  static const _kTema = 'tema';
  ThemeMode modoTema;

  Future<void> cambiarTema(ThemeMode modo) async {
    modoTema = modo;
    notifyListeners();
    await _prefs.setString(_kTema, modo == ThemeMode.light ? 'claro' : 'oscuro');
  }
}

/// Contador de alertas para el globito de la pestaña "Alertas".
class AlertasEstado extends ChangeNotifier {
  int cantidad = 0;

  void actualizar(int nueva) {
    if (nueva == cantidad) return;
    cantidad = nueva;
    notifyListeners();
  }
}
