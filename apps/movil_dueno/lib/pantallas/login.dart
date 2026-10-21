import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../tema.dart';
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
  Salud? _probada;
  String? _error;

  @override
  void initState() {
    super.initState();
    _servidor.text = _paraMostrar(context.read<SesionEstado>().servidor);
  }

  /// "http://100.101.102.103:8766" -> "100.101.102.103": lo mismo que se copia
  /// de la app de Tailscale.
  static String _paraMostrar(String? servidor) {
    if (servidor == null) return '';
    final uri = Uri.tryParse(servidor);
    if (uri == null || uri.host.isEmpty) return servidor;
    return uri.port == puertoPorDefecto && uri.scheme == 'http' ? uri.host : servidor;
  }

  @override
  void dispose() {
    _servidor.dispose();
    _pin.dispose();
    super.dispose();
  }

  /// Se valida ANTES de llamar: el PIN no sale nunca hacia una dirección que
  /// no sea de Tailscale (el cliente lo vuelve a frenar igual).
  String? _problemaDireccion() {
    final t = _servidor.text.trim();
    if (t.isEmpty) return 'Completá la dirección de la PC.';
    if (!esDireccionTailscale(t)) return mensajeNoTailscale;
    return null;
  }

  Future<void> _probar() async {
    final problema = _problemaDireccion();
    setState(() {
      _error = problema;
      _probada = null;
    });
    if (problema != null) return;
    setState(() => _ocupado = true);
    try {
      final salud = await context.read<SesionEstado>().probarServidor(_servidor.text);
      if (mounted) setState(() => _probada = salud);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _ingresar() async {
    final problema = _problemaDireccion();
    if (problema != null || _pin.text.isEmpty) {
      setState(() => _error = problema ?? 'Escribí el PIN.');
      return;
    }
    final sesion = context.read<SesionEstado>();
    setState(() {
      _ocupado = true;
      _error = null;
    });
    try {
      // la huella se ofrece antes de entrar: después esta pantalla (y su
      // diálogo) ya no está
      await sesion.ingresar(_servidor.text, _pin.text, antesDeEntrar: () async {
        if (!sesion.biometriaDisponible || sesion.biometriaActiva || !mounted) return;
        setState(() => _ocupado = false); // ya validó: ahora espera la respuesta de Leo
        final usar = await confirmar(context,
            titulo: '¿Entrar con huella o rostro?',
            mensaje: 'La próxima vez vas a poder abrir el panel con tu huella o tu cara, sin escribir el PIN.',
            si: 'Sí, activar');
        if (usar) await sesion.configurarBiometria(true);
      });
    } on ApiError catch (e) {
      // pin_no_definido, base_no_disponible, otro_programa, puerto_equivocado…:
      // el texto ya dice qué hacer (el de la PC o el del cliente)
      if (mounted) setState(() => _error = e.mensaje);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final avisoPuertoViejo = context.select<SesionEstado, bool>((s) => s.avisoPuertoViejo);
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
                if (avisoPuertoViejo)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: _Renglon(
                        icono: Icons.info_outline,
                        color: c.aviso,
                        texto: 'La app ahora usa el puerto $puertoPorDefecto: volvé a ingresar'),
                  ),
                TextField(
                  controller: _servidor,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: InputDecoration(
                    labelText: 'Dirección de la PC del local',
                    hintText: '100.x.y.z (copiala de la app de Tailscale)',
                    helperText: 'En el celular: app de Tailscale → mantené apretada la PC del local → '
                        'Copy IP → pegala acá.',
                    helperMaxLines: 3,
                    prefixIcon: const Icon(Icons.computer),
                    suffixIcon: TextButton(onPressed: _ocupado ? null : _probar, child: const Text('Probar')),
                  ),
                  onChanged: (_) => setState(() => _probada = null),
                ),
                if (_probada != null) _ResultadoPrueba(salud: _probada!),
                const SizedBox(height: 16),
                TextField(
                  key: const Key('campo_pin'),
                  controller: _pin,
                  obscureText: !_verPin,
                  keyboardType: TextInputType.number,
                  maxLength: 12,
                  decoration: InputDecoration(
                    labelText: 'PIN de dueño',
                    helperText: '6 a 12 números; se define en la PC del local',
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

/// Lo que contestó "Probar": a qué negocio se conectó y si la PC encontró
/// la base. Una API instalada en otra carpeta contesta igual, pero sin
/// nombre de negocio y sin base: es la trampa de la regla 2 en versión celular.
class _ResultadoPrueba extends StatelessWidget {
  const _ResultadoPrueba({required this.salud});
  final Salud salud;

  static const _motivos = {
    'pin_no_definido': 'Todavía no hay PIN para el celular: hay que definirlo en la PC del local.',
    'secreto_ilegible': 'El archivo de seguridad de la API está dañado: hay que revisarlo en la PC.',
  };

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    final t = Theme.of(context);
    final motivo = salud.loginDisponible ? null : _motivos[salud.motivo];
    return Padding(
      padding: const EdgeInsets.only(top: 8, left: 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (salud.nombreLocal.isEmpty)
          _Renglon(
              icono: Icons.warning_amber_rounded,
              color: c.aviso,
              texto: 'Contesta la API del celular, pero sin nombre de negocio: puede estar instalada en otra carpeta.')
        else
          _Renglon(icono: Icons.check_circle, color: t.colorScheme.primary, texto: 'Conectado a "${salud.nombreLocal}"'),
        if (salud.baseOk)
          _Renglon(icono: Icons.storage_outlined, color: c.ok, texto: 'La PC encontró la base del negocio.')
        else
          _Renglon(
              icono: Icons.error_outline,
              color: c.peligro,
              texto: 'La PC no encontró la base del negocio: ${salud.baseDetalle}'),
        if (motivo != null) _Renglon(icono: Icons.lock_clock_outlined, color: c.aviso, texto: motivo),
      ]),
    );
  }
}

class _Renglon extends StatelessWidget {
  const _Renglon({required this.icono, required this.color, required this.texto});
  final IconData icono;
  final Color color;
  final String texto;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icono, color: color, size: 18),
          const SizedBox(width: 6),
          Expanded(child: Text(texto)),
        ]),
      );
}
