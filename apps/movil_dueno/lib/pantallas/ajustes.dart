import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../estado.dart';
import '../widgets/comunes.dart';
import 'login.dart';

const versionApp = '1.0.0';

class PantallaAjustes extends StatefulWidget {
  const PantallaAjustes({super.key});

  @override
  State<PantallaAjustes> createState() => _PantallaAjustesState();
}

class _PantallaAjustesState extends State<PantallaAjustes> {
  String? _versionApi;
  bool _probando = false;

  Future<void> _probarConexion() async {
    setState(() => _probando = true);
    try {
      final s = await context.read<SesionEstado>().api.salud();
      if (!mounted) return;
      setState(() => _versionApi = s.versionApi);
      mostrarMensaje(context, 'Conexión OK con "${s.nombreLocal}".');
    } on ApiError catch (e) {
      if (mounted) mostrarMensaje(context, e.mensaje, error: true);
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
          ]),
        ),
        const Seccion('Conexión con el local'),
        Card(
          child: Column(children: [
            ListTile(leading: const Icon(Icons.store_outlined), title: const Text('Negocio'), subtitle: Text(sesion.nombreLocal)),
            ListTile(leading: const Icon(Icons.computer), title: const Text('PC del local'), subtitle: Text(sesion.servidor ?? '')),
            ListTile(leading: const Icon(Icons.person_outline), title: const Text('Usuario'), subtitle: Text(sesion.usuario)),
            ListTile(
              leading: _probando
                  ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.5))
                  : const Icon(Icons.wifi_tethering),
              title: const Text('Probar conexión'),
              onTap: _probando ? null : _probarConexion,
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
        Text('Panel del Dueño $versionApp${_versionApi == null ? '' : ' · API $_versionApi'}',
            textAlign: TextAlign.center, style: t.textTheme.bodySmall),
      ]),
    );
  }
}
