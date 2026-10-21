import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../estado.dart';
import 'login.dart';

/// Pantalla de desbloqueo: huella/rostro, o el PIN de dueño (validado contra la PC).
class PantallaBloqueo extends StatefulWidget {
  const PantallaBloqueo({super.key});

  @override
  State<PantallaBloqueo> createState() => _PantallaBloqueoState();
}

class _PantallaBloqueoState extends State<PantallaBloqueo> {
  final _pin = TextEditingController();
  late bool _conPin;
  bool _ocupado = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final sesion = context.read<SesionEstado>();
    _conPin = !(sesion.biometriaActiva && sesion.biometriaDisponible);
    if (!_conPin) WidgetsBinding.instance.addPostFrameCallback((_) => _biometria());
  }

  @override
  void dispose() {
    _pin.dispose();
    super.dispose();
  }

  Future<void> _biometria() async {
    final ok = await context.read<SesionEstado>().desbloquearConBiometria();
    if (!ok && mounted) setState(() => _error = 'No se pudo verificar. Probá de nuevo o usá el PIN.');
  }

  Future<void> _desbloquearConPin() async {
    if (_pin.text.isEmpty) return;
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await context.read<SesionEstado>().desbloquearConPin(_pin.text);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final sesion = context.watch<SesionEstado>();
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Center(child: Logo(tamano: 104)),
                const SizedBox(height: 20),
                Text('¡Hola de nuevo!', textAlign: TextAlign.center,
                    style: t.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700)),
                if (sesion.nombreLocal.isNotEmpty)
                  Text(sesion.nombreLocal, textAlign: TextAlign.center,
                      style: t.textTheme.bodyLarge?.copyWith(color: t.colorScheme.onSurfaceVariant)),
                const SizedBox(height: 32),
                if (!_conPin) ...[
                  FilledButton.icon(
                    onPressed: _biometria,
                    icon: const Icon(Icons.fingerprint, size: 28),
                    label: const Text('Desbloquear'),
                  ),
                  TextButton(onPressed: () => setState(() => _conPin = true), child: const Text('Usar el PIN')),
                ] else ...[
                  TextField(
                    controller: _pin,
                    autofocus: true,
                    obscureText: true,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'PIN de dueño', prefixIcon: Icon(Icons.lock_outline)),
                    onSubmitted: (_) => _desbloquearConPin(),
                  ),
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _ocupado ? null : _desbloquearConPin,
                    child: _ocupado
                        ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                        : const Text('Entrar'),
                  ),
                  if (sesion.biometriaActiva && sesion.biometriaDisponible)
                    TextButton.icon(
                      onPressed: () => setState(() => _conPin = false),
                      icon: const Icon(Icons.fingerprint),
                      label: const Text('Usar huella o rostro'),
                    ),
                ],
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_error!, textAlign: TextAlign.center, style: TextStyle(color: t.colorScheme.error)),
                  ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
