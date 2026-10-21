import 'package:flutter_test/flutter_test.dart';
import 'package:panel_dueno/api/cliente_api.dart';
import 'package:panel_dueno/estado.dart';

import 'servidor_falso.dart';

void main() {
  late ServidorFalso servidor;
  late AlmacenMemoria almacen;
  late DateTime ahora;

  SesionEstado nueva({Biometria? biometria}) => SesionEstado(
        almacen: almacen,
        biometria: biometria ?? BiometriaFalsa(),
        crearCliente: servidor.crear,
        reloj: () => ahora,
      );

  setUp(() {
    servidor = ServidorFalso();
    almacen = AlmacenMemoria();
    ahora = DateTime(2026, 10, 4, 12);
  });

  test('sin datos guardados pide ingresar', () async {
    final s = nueva();
    await s.cargar();
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('ingresar guarda servidor normalizado y token, pero nunca el PIN', () async {
    final s = nueva();
    await s.cargar();
    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    expect(s.estado, EstadoSesion.activa);
    expect(s.nombreLocal, 'El Galpón Del Nono');
    expect(almacen.datos['servidor'], 'http://100.101.102.103:8766');
    expect(almacen.datos['token'], ServidorFalso.token);
    expect(almacen.datos.values, isNot(contains(ServidorFalso.pin)));
  });

  test('PIN incorrecto no inicia sesión', () async {
    final s = nueva();
    await s.cargar();
    await expectLater(s.ingresar(ServidorFalso.direccion, '000000'),
        throwsA(isA<ApiError>().having((e) => e.codigo, 'codigo', 'pin_incorrecto')));
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('al reabrir la app arranca bloqueada', () async {
    await (nueva()..cargar()).ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    final s = nueva();
    await s.cargar();
    expect(s.estado, EstadoSesion.bloqueada);
    await s.desbloquearConPin(ServidorFalso.pin);
    expect(s.estado, EstadoSesion.activa);
  });

  test('desbloqueo con huella solo si está activada', () async {
    final bio = BiometriaFalsa(hay: true);
    final primera = nueva(biometria: bio);
    await primera.cargar();
    await primera.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    await primera.configurarBiometria(true);

    final s = nueva(biometria: bio);
    await s.cargar();
    expect(s.estado, EstadoSesion.bloqueada);
    expect(await s.desbloquearConBiometria(), isTrue);
    expect(s.estado, EstadoSesion.activa);
  });

  test('huella rechazada no desbloquea', () async {
    final bio = BiometriaFalsa(hay: true);
    final s = nueva(biometria: bio);
    await s.cargar();
    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    await s.configurarBiometria(true);
    s.pasoASegundoPlano();
    ahora = ahora.add(const Duration(minutes: 5));
    s.volvioAPrimerPlano();
    bio.acepta = false;
    expect(await s.desbloquearConBiometria(), isFalse);
    expect(s.estado, EstadoSesion.bloqueada);
  });

  test('se bloquea sola tras 2 minutos en segundo plano', () async {
    final s = nueva();
    await s.cargar();
    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin);

    s.pasoASegundoPlano();
    ahora = ahora.add(const Duration(seconds: 30));
    s.volvioAPrimerPlano();
    expect(s.estado, EstadoSesion.activa, reason: 'un vistazo rápido a otra app no bloquea');

    s.pasoASegundoPlano();
    ahora = ahora.add(const Duration(minutes: 3));
    s.volvioAPrimerPlano();
    expect(s.estado, EstadoSesion.bloqueada);
  });

  test('token vencido: pide PIN de nuevo', () async {
    final s = nueva();
    await s.cargar();
    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    ahora = DateTime(2100, 2, 1); // pasó la fecha de expiración
    final despues = nueva();
    await despues.cargar();
    expect(despues.estado, EstadoSesion.sinSesion);
    expect(despues.servidor, 'http://100.101.102.103:8766', reason: 'recuerda a qué PC conectarse');
  });

  test('si la PC rechaza el token (401) se vuelve al login', () async {
    final s = nueva();
    await s.cargar();
    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    s.api.token = 'token-viejo';
    await expectLater(s.api.dashboard(), throwsA(isA<ApiError>()));
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('una dirección inválida o fuera de Tailscale no rompe el login ni manda nada', () async {
    final s = nueva();
    await s.cargar();
    for (final direccion in ['fd7a::zz', 'http://[::1', '192.168.0.10', '200.45.1.2']) {
      await expectLater(s.ingresar(direccion, ServidorFalso.pin),
          throwsA(isA<ApiError>().having((e) => e.codigo, 'codigo', 'no_tailscale')), reason: direccion);
      await expectLater(s.probarServidor(direccion), throwsA(isA<ApiError>()), reason: direccion);
    }
    expect(servidor.pedidos, isEmpty);
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('si la app venía del puerto 8765 (versión 1), pide ingresar de nuevo contra el 8766', () async {
    almacen.datos.addAll({
      'servidor': 'http://100.101.102.103:8765',
      'token': 'token-del-apidueno-viejo',
      'token_expira': '4102444800',
      'usuario': 'dueño',
    });
    final s = nueva();
    await s.cargar();
    expect(s.estado, EstadoSesion.sinSesion);
    expect(s.avisoPuertoViejo, isTrue);
    expect(s.servidor, 'http://100.101.102.103:8766', reason: 'la misma PC, ya en el puerto nuevo');
    expect(almacen.datos.containsKey('token'), isFalse, reason: 'el token viejo no sirve en la API nueva');

    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin);
    expect(s.avisoPuertoViejo, isFalse);
    expect(almacen.datos['servidor'], 'http://100.101.102.103:8766');
  });

  test('sin PIN definido en la PC, el login muestra lo que dice la PC', () async {
    servidor.pinDefinido = false;
    final s = nueva();
    await s.cargar();
    await expectLater(
        s.ingresar(ServidorFalso.direccion, ServidorFalso.pin),
        throwsA(isA<ApiError>()
            .having((e) => e.codigo, 'codigo', 'pin_no_definido')
            .having((e) => e.mensaje, 'mensaje', 'El PIN viejo quedó anulado: hay que definir uno nuevo en la PC del local.')));
    expect(s.estado, EstadoSesion.sinSesion);
  });

  for (final (nombre, crear) in <(String, Impostor Function())>[
    ('el Dueño Remoto', Impostor.remoteApi),
    ('el ApiDueno viejo', Impostor.apiDuenoViejo),
  ]) {
    test('si en la dirección contesta $nombre, el PIN no se manda', () async {
      final impostor = crear();
      final s = SesionEstado(almacen: almacen, biometria: BiometriaFalsa(), crearCliente: impostor.crear);
      await s.cargar();
      await expectLater(s.ingresar(ServidorFalso.direccion, ServidorFalso.pin), throwsA(isA<ApiError>()));
      expect(impostor.pedidos, ['GET /api/salud'], reason: 'nunca llegó a /api/auth/login');
      expect(s.estado, EstadoSesion.sinSesion);
      expect(almacen.datos.containsKey('token'), isFalse);
    });
  }

  test('antesDeEntrar corre con el PIN validado pero todavía en el login', () async {
    final s = nueva();
    await s.cargar();
    EstadoSesion? durante;
    await s.ingresar(ServidorFalso.direccion, ServidorFalso.pin, antesDeEntrar: () async => durante = s.estado);
    expect(durante, EstadoSesion.sinSesion);
    expect(s.estado, EstadoSesion.activa);
  });
}
