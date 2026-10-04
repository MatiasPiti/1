import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../estado.dart';
import '../widgets/comunes.dart';

class Logo extends StatelessWidget {
  const Logo({super.key, this.tamano = 120});
  final double tamano;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: tamano,
      height: tamano,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        border: Border.all(color: Theme.of(context).colorScheme.primary, width: 3),
        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.25), blurRadius: 18, offset: const Offset(0, 6))],
        image: const DecorationImage(image: AssetImage('assets/logo.png'), fit: BoxFit.cover),
      ),
    );
  }
}

/// Primer ingreso: dirección de la PC del local (Tailscale) + PIN de dueño.
class PantallaLogin extends StatefulWidget {
  const PantallaLogin({super.key});

  @override
  State<PantallaLogin> createState() => _PantallaLoginState();
}

class _PantallaLoginState extends State<PantallaLogin> {
  final _servidor = TextEditingController();
  final _pin = TextEditingController();
  bool _ocupado = false;
  bool _verPin = false;
  String? _conectadoA;
  String? _error;

  @override
  void initState() {
    super.initState();
    _servidor.text = context.read<SesionEstado>().servidor ?? '';
  }

  @override
  void dispose() {
    _servidor.dispose();
    _pin.dispose();
    super.dispose();
  }

  Future<void> _probar() async {
    if (_servidor.text.trim().isEmpty) return;
    setState(() {
      _ocupado = true;
      _error = null;
      _conectadoA = null;
    });
    try {
      final local = await context.read<SesionEstado>().probarServidor(_servidor.text);
      setState(() => _conectadoA = local);
    } on ApiError catch (e) {
      setState(() => _error = e.mensaje);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _ingresar() async {
    if (_servidor.text.trim().isEmpty || _pin.text.isEmpty) {
      setState(() => _error = 'Completá la dirección de la PC y el PIN.');
      return;
    }
    final sesion = context.read<SesionEstado>();
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      await sesion.ingresar(_servidor.text, _pin.text);
      if (sesion.biometriaDisponible && !sesion.biometriaActiva && mounted) {
        final usar = await confirmar(context,
            titulo: '¿Entrar con huella o rostro?',
            mensaje: 'La próxima vez vas a poder abrir el panel con tu huella o tu cara, sin escribir el PIN.',
            si: 'Sí, activar');
        if (usar) await sesion.configurarBiometria(true);
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Center(child: Logo()),
                const SizedBox(height: 20),
                Text('Panel del Dueño', textAlign: TextAlign.center,
                    style: t.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 6),
                Text('Tu negocio en el bolsillo', textAlign: TextAlign.center,
                    style: t.textTheme.bodyLarge?.copyWith(color: t.colorScheme.onSurfaceVariant)),
                const SizedBox(height: 32),
                TextField(
                  controller: _servidor,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: InputDecoration(
                    labelText: 'Dirección de la PC del local',
                    hintText: '100.101.102.103 o pc-local.tu-red.ts.net',
                    helperText: 'La que muestra Tailscale para la PC',
                    prefixIcon: const Icon(Icons.computer),
                    suffixIcon: TextButton(onPressed: _ocupado ? null : _probar, child: const Text('Probar')),
                  ),
                  onChanged: (_) => setState(() => _conectadoA = null),
                ),
                if (_conectadoA != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8, left: 4),
                    child: Row(children: [
                      Icon(Icons.check_circle, color: t.colorScheme.primary, size: 18),
                      const SizedBox(width: 6),
                      Expanded(child: Text('Conectado a "$_conectadoA"')),
                    ]),
                  ),
                const SizedBox(height: 16),
                TextField(
                  key: const Key('campo_pin'),
                  controller: _pin,
                  obscureText: !_verPin,
                  keyboardType: TextInputType.number,
                  maxLength: 12,
                  decoration: InputDecoration(
                    labelText: 'PIN de dueño',
                    helperText: 'El mismo PIN del sistema de la PC',
                    counterText: '',
                    prefixIcon: const Icon(Icons.lock_outline),
                    suffixIcon: IconButton(
                      icon: Icon(_verPin ? Icons.visibility_off : Icons.visibility),
                      onPressed: () => setState(() => _verPin = !_verPin),
                    ),
                  ),
                  onSubmitted: (_) => _ingresar(),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_error!, style: TextStyle(color: t.colorScheme.error)),
                  ),
                const SizedBox(height: 24),
                FilledButton(
                  onPressed: _ocupado ? null : _ingresar,
                  child: _ocupado
                      ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                      : const Text('Ingresar'),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
