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
    await s.ingresar('100.1.2.3', '1234');
    expect(s.estado, EstadoSesion.activa);
    expect(s.nombreLocal, 'Almacén de Leo');
    expect(almacen.datos['servidor'], 'http://100.1.2.3:8765');
    expect(almacen.datos['token'], 'token-ok');
    expect(almacen.datos.values, isNot(contains('1234')));
  });

  test('PIN incorrecto no inicia sesión', () async {
    final s = nueva();
    await s.cargar();
    await expectLater(s.ingresar('100.1.2.3', '0000'), throwsA(isA<ApiError>()));
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('al reabrir la app arranca bloqueada', () async {
    await (nueva()..cargar()).ingresar('100.1.2.3', '1234');
    final s = nueva();
    await s.cargar();
    expect(s.estado, EstadoSesion.bloqueada);
    await s.desbloquearConPin('1234');
    expect(s.estado, EstadoSesion.activa);
  });

  test('desbloqueo con huella solo si está activada', () async {
    final bio = BiometriaFalsa(hay: true);
    final primera = nueva(biometria: bio);
    await primera.cargar();
    await primera.ingresar('100.1.2.3', '1234');
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
    await s.ingresar('100.1.2.3', '1234');
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
    await s.ingresar('100.1.2.3', '1234');

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
    await s.ingresar('100.1.2.3', '1234');
    ahora = DateTime(2100, 2, 1); // pasó la fecha de expiración
    final despues = nueva();
    await despues.cargar();
    expect(despues.estado, EstadoSesion.sinSesion);
    expect(despues.servidor, 'http://100.1.2.3:8765', reason: 'recuerda a qué PC conectarse');
  });

  test('si la PC rechaza el token (401) se vuelve al login', () async {
    final s = nueva();
    await s.cargar();
    await s.ingresar('100.1.2.3', '1234');
    s.api.token = 'token-viejo';
    await expectLater(s.api.dashboard(), throwsA(isA<ApiError>()));
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('una dirección inválida no rompe el login: da un ApiError', () async {
    final s = nueva();
    await s.cargar();
    await expectLater(s.ingresar('fd7a::zz', '1234'),
        throwsA(isA<ApiError>().having((e) => e.mensaje, 'mensaje', 'La dirección de la PC no es válida')));
    await expectLater(s.probarServidor('http://[::1'), throwsA(isA<ApiError>()));
    expect(s.estado, EstadoSesion.sinSesion);
  });

  test('antesDeEntrar corre con el PIN validado pero todavía en el login', () async {
    final s = nueva();
    await s.cargar();
    EstadoSesion? durante;
    await s.ingresar('100.1.2.3', '1234', antesDeEntrar: () async => durante = s.estado);
    expect(durante, EstadoSesion.sinSesion);
    expect(s.estado, EstadoSesion.activa);
  });
}
