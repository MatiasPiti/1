import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../widgets/comunes.dart';
import 'login.dart';

const versionApp = '2.0.0';

/// "http://100.101.102.103:8766" -> "100.•••.•••.•••". Una captura de esta
/// pantalla es un chat igual (regla 4): la IP de la PC no se muestra entera
/// salvo mientras se mantiene apretado el renglón.
String ipTapada(String servidor) {
  final host = Uri.tryParse(servidor)?.host ?? '';
  final partes = host.split('.');
  if (partes.length != 4) return '•••';
  return '${partes.first}.•••.•••.•••';
}

class PantallaAjustes extends StatefulWidget {
  const PantallaAjustes({super.key});

  @override
  State<PantallaAjustes> createState() => _PantallaAjustesState();
}

class _PantallaAjustesState extends State<PantallaAjustes> {
  Salud? _salud;
  bool _probando = false;
  bool _verIp = false;

  @override
  void initState() {
    super.initState();
    _probando = true; // en initState no se puede llamar a setState
    _consultar(avisar: false);
  }

  /// Trae versión, compilado y contrato de la PC. Al abrir la pantalla no
  /// molesta si falla; desde "Probar conexión" sí avisa.
  Future<void> _consultar({required bool avisar}) async {
    if (avisar) setState(() => _probando = true);
    try {
      final s = await context.read<SesionEstado>().api.salud();
      if (!mounted) return;
      setState(() => _salud = s);
      if (avisar) {
        mostrarMensaje(
            context,
            s.baseOk
                ? 'Conexión OK con "${s.nombreLocal}".'
                : 'Contesta la PC, pero no encontró la base: ${s.baseDetalle}',
            error: !s.baseOk);
      }
    } on ApiError catch (e) {
      if (avisar && mounted) mostrarMensaje(context, e.mensaje, error: true);
    } finally {
      if (mounted) setState(() => _probando = false);
    }
  }

  Future<void> _cerrarSesion() async {
    final ok = await confirmar(context,
        titulo: '¿Cerrar sesión?',
        mensaje: 'Para volver a entrar vas a necesitar el PIN de dueño.',
        si: 'Cerrar sesión');
    if (!ok || !mounted) return;
    Navigator.of(context).popUntil((r) => r.isFirst);
    await context.read<SesionEstado>().cerrarSesion();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final ajustes = context.watch<AjustesEstado>();
    final sesion = context.watch<SesionEstado>();
    final servidor = sesion.servidor ?? '';
    final s = _salud;
    return Scaffold(
      appBar: AppBar(title: const Text('Ajustes')),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 32), children: [
        const Seccion('Apariencia'),
        SegmentedButton<ThemeMode>(
          segments: const [
            ButtonSegment(value: ThemeMode.light, icon: Icon(Icons.light_mode_outlined), label: Text('Claro')),
            ButtonSegment(value: ThemeMode.dark, icon: Icon(Icons.dark_mode_outlined), label: Text('Oscuro elegante')),
          ],
          selected: {ajustes.modoTema},
          onSelectionChanged: (s) => ajustes.cambiarTema(s.first),
        ),
        const Seccion('Seguridad'),
        Card(
          child: Column(children: [
            SwitchListTile(
              secondary: const Icon(Icons.fingerprint),
              title: const Text('Desbloquear con huella o rostro'),
              subtitle: Text(sesion.biometriaDisponible
                  ? 'Si lo desactivás, se pide el PIN de dueño'
                  : 'Este teléfono no tiene huella/rostro configurado: se usa el PIN'),
              value: sesion.biometriaActiva && sesion.biometriaDisponible,
              onChanged: sesion.biometriaDisponible ? (v) => sesion.configurarBiometria(v) : null,
            ),
            const ListTile(
              leading: Icon(Icons.timer_outlined),
              title: Text('Bloqueo automático'),
              subtitle: Text('La app se bloquea sola después de 2 minutos en segundo plano'),
            ),
            const ListTile(
              leading: Icon(Icons.phonelink_erase_outlined),
              title: Text('Si perdiste el celular'),
              subtitle: Text('Si perdiste el celular: 1) sacalo de Tailscale desde la consola web (Machines → el celular → '
                  'Remove); 2) en la PC, ApiCelular.exe cerrar-sesiones desconecta todos los celulares.'),
            ),
          ]),
        ),
        const Seccion('Conexión con el local'),
        Card(
          child: Column(children: [
            ListTile(leading: const Icon(Icons.store_outlined), title: const Text('Negocio'), subtitle: Text(sesion.nombreLocal)),
            GestureDetector(
              key: const Key('renglon_ip'),
              onLongPressStart: (_) => setState(() => _verIp = true),
              onLongPressEnd: (_) => setState(() => _verIp = false),
              onLongPressCancel: () => setState(() => _verIp = false),
              child: ListTile(
                leading: const Icon(Icons.computer),
                title: const Text('PC del local'),
                subtitle: Text(_verIp
                    ? (Uri.tryParse(servidor)?.host ?? servidor)
                    : '${ipTapada(servidor)}  (mantené apretado para verla)'),
              ),
            ),
            ListTile(leading: const Icon(Icons.person_outline), title: const Text('Usuario'), subtitle: Text(sesion.usuario)),
            ListTile(
              leading: const Icon(Icons.dns_outlined),
              title: const Text('API del celular en la PC'),
              subtitle: Text(s == null
                  ? 'Sin datos todavía'
                  : 'Versión ${s.versionApi} · compilado ${s.compilado} · contrato ${s.contrato}'),
            ),
            ListTile(
              leading: _probando
                  ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.5))
                  : const Icon(Icons.wifi_tethering),
              title: const Text('Probar conexión'),
              onTap: _probando ? null : () => _consultar(avisar: true),
            ),
          ]),
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(foregroundColor: t.colorScheme.error),
          onPressed: _cerrarSesion,
          icon: const Icon(Icons.logout),
          label: const Text('Cerrar sesión / cambiar de PC'),
        ),
        const SizedBox(height: 32),
        const Center(child: Logo(tamano: 56)),
        const SizedBox(height: 8),
        Text('Panel del Dueño $versionApp${s == null ? '' : ' · API ${s.versionApi}'}',
            textAlign: TextAlign.center, style: t.textTheme.bodySmall),
      ]),
    );
  }
}
