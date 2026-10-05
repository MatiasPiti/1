import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../estado.dart';
import '../tema.dart';
import '../widgets/escaner.dart';

/// Decide si una lectura de la cámara cuenta como un "disparo" nuevo.
///
/// La cámara reporta el mismo código varias veces por segundo mientras
/// está a la vista. Un lector USB, en cambio, dispara una vez por lectura.
/// Para imitarlo, un código solo vuelve a contar después de haber salido
/// de cuadro un momento (`pausa`), así un producto quieto frente a la
/// cámara no descuenta stock de más.
///
/// Recuerda CADA código que está a la vista (no solo el último): con dos
/// productos en el recuadro, alternar entre uno y otro no los vuelve a contar.
class FiltroLecturas {
  FiltroLecturas({this.pausa = const Duration(milliseconds: 1500)});
  final Duration pausa;

  /// Código -> última vez que se lo vio.
  final _vistos = <String, DateTime>{};

  bool esNueva(String codigo, DateTime ahora) {
    // lo que no se ve hace más de `pausa` ya contaría como nuevo: se olvida
    _vistos.removeWhere((_, visto) => ahora.difference(visto) >= pausa);
    final nueva = !_vistos.containsKey(codigo);
    _vistos[codigo] = ahora;
    return nueva;
  }

  /// Registra todos los códigos de una captura y devuelve los que cuentan.
  List<String> nuevas(Iterable<String> codigos, DateTime ahora) =>
      [for (final c in codigos) if (esNueva(c, ahora)) c];
}

/// Procesa las lecturas de a una y en orden. Un código nuevo que aparece
/// mientras se procesa otro no se pierde: se encola (sin repetir) y se
/// procesa al terminar. Cada lectura guarda el modo (sumar/restar) que
/// estaba elegido cuando se leyó.
class ColaLecturas {
  ColaLecturas({required this.procesar, FiltroLecturas? filtro, this.reloj = DateTime.now})
      : filtro = filtro ?? FiltroLecturas();

  final Future<void> Function(String codigo, bool sumar) procesar;
  final FiltroLecturas filtro;
  final DateTime Function() reloj;
  final _pendientes = <({String codigo, bool sumar})>[];
  String? _actual;
  bool _cerrada = false;

  /// El código que se está procesando ahora (o null).
  String? get procesando => _actual;

  /// Códigos que vio la cámara en un cuadro: solo cuentan los disparos nuevos.
  void detectados(Iterable<String> codigos, {required bool sumar}) {
    for (final c in filtro.nuevas(codigos, reloj())) {
      if (c == _actual || _pendientes.any((p) => p.codigo == c)) continue;
      _pendientes.add((codigo: c, sumar: sumar));
    }
    _vaciar();
  }

  /// Código escrito a mano: cada Enter cuenta.
  void manual(String codigo, {required bool sumar}) {
    _pendientes.add((codigo: codigo, sumar: sumar));
    _vaciar();
  }

  /// Al salir de la pantalla: lo pendiente ya no se procesa.
  void cerrar() {
    _cerrada = true;
    _pendientes.clear();
  }

  Future<void> _vaciar() async {
    if (_actual != null) return; // ya hay un ciclo andando: va a tomar lo nuevo
    while (_pendientes.isNotEmpty && !_cerrada) {
      final l = _pendientes.removeAt(0);
      _actual = l.codigo;
      try {
        await procesar(l.codigo, l.sumar);
      } finally {
        _actual = null;
      }
    }
  }
}

class _Lectura {
  _Lectura({required this.codigo, required this.ok, required this.texto});
  final String codigo;
  final bool ok;
  final String texto;
}

class PantallaModoLector extends StatefulWidget {
  const PantallaModoLector({super.key});

  @override
  State<PantallaModoLector> createState() => _PantallaModoLectorState();
}

class _PantallaModoLectorState extends State<PantallaModoLector> {
  final _control = MobileScannerController(formats: formatosProducto);
  late final _cola = ColaLecturas(procesar: _procesar);
  final _manual = TextEditingController();
  final _lecturas = <_Lectura>[];
  bool _sumar = false; // igual que el lector de la PC: por defecto resta
  String? _procesando;

  @override
  void dispose() {
    _cola.cerrar();
    _control.dispose();
    _manual.dispose();
    super.dispose();
  }

  void _alDetectar(BarcodeCapture captura) {
    // Con la app bloqueada esta pantalla queda tapada pero viva, y la cámara
    // se reanuda sola al volver del segundo plano: lo que vea no descuenta
    // nada hasta desbloquear.
    if (!mounted || context.read<SesionEstado>().estado != EstadoSesion.activa) return;
    _cola.detectados(codigosDe(captura), sumar: _sumar);
  }

  /// [sumar] es el modo de cuando se leyó el código: si Leo lo cambia
  /// mientras espera la respuesta, el texto no tiene que mentir.
  Future<void> _procesar(String codigo, bool sumar) async {
    if (!mounted) return;
    setState(() => _procesando = codigo);
    final api = context.read<SesionEstado>().api;
    try {
      final r = await api.lector(codigo, sumar: sumar);
      HapticFeedback.mediumImpact();
      SystemSound.play(SystemSoundType.click);
      _agregar(_Lectura(codigo: codigo, ok: true, texto: '${r.nombre}  ${sumar ? '+1' : '−1'}  → quedan ${r.stockNuevo}'));
    } on ApiError catch (e) {
      HapticFeedback.heavyImpact();
      final texto = switch (e) {
        ApiError(status: 404) => 'Código $codigo: no existe en el sistema',
        // no se sabe si descontó: que no lo vuelva a pasar sin revisar
        ApiError(incierto: true) => 'Código $codigo: ${e.mensaje}',
        _ => e.mensaje,
      };
      _agregar(_Lectura(codigo: codigo, ok: false, texto: texto));
    } finally {
      if (mounted) setState(() => _procesando = null);
    }
  }

  void _agregar(_Lectura l) {
    if (!mounted) return;
    setState(() => _lecturas.insert(0, l));
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final ultima = _lecturas.isEmpty ? null : _lecturas.first;
    final correctas = _lecturas.where((l) => l.ok).length;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Modo lector'),
        actions: [BotonLinterna(control: _control)],
      ),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          child: SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: false, icon: Icon(Icons.remove), label: Text('Restar 1')),
              ButtonSegment(value: true, icon: Icon(Icons.add), label: Text('Sumar 1')),
            ],
            selected: {_sumar},
            onSelectionChanged: (s) => setState(() => _sumar = s.first),
          ),
        ),
        Expanded(
          flex: 5,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(20),
              child: VisorCamara(control: _control, alDetectar: _alDetectar),
            ),
          ),
        ),
        AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          padding: const EdgeInsets.all(14),
          width: double.infinity,
          decoration: BoxDecoration(
            color: (ultima == null ? t.colorScheme.surfaceContainerHighest : (ultima.ok ? c.ok : c.peligro))
                .withValues(alpha: 0.18),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Row(children: [
            if (_procesando != null)
              const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
            else
              Icon(ultima == null ? Icons.qr_code_2 : (ultima.ok ? Icons.check_circle : Icons.error),
                  color: ultima == null ? null : (ultima.ok ? c.ok : c.peligro)),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                ultima?.texto ?? 'Apuntá la cámara a un código. Cada lectura ${_sumar ? 'suma' : 'resta'} 1 unidad.',
                style: t.textTheme.titleSmall,
              ),
            ),
          ]),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: TextField(
            controller: _manual,
            keyboardType: TextInputType.number,
            textInputAction: TextInputAction.done,
            decoration: const InputDecoration(labelText: 'O escribí el código y Enter', prefixIcon: Icon(Icons.keyboard)),
            onSubmitted: (t) {
              _manual.clear();
              if (t.trim().isNotEmpty) _cola.manual(t.trim(), sumar: _sumar);
            },
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
          child: Row(children: [
            Text('Lecturas de esta sesión', style: t.textTheme.labelLarge),
            const Spacer(),
            Text('$correctas ok · ${_lecturas.length - correctas} con error', style: t.textTheme.bodySmall),
          ]),
        ),
        Expanded(
          flex: 3,
          child: ListView.builder(
            itemCount: _lecturas.length,
            itemBuilder: (context, i) {
              final l = _lecturas[i];
              return ListTile(
                dense: true,
                leading: Icon(l.ok ? Icons.check : Icons.close, color: l.ok ? c.ok : c.peligro),
                title: Text(l.texto, maxLines: 2),
              );
            },
          ),
        ),
      ]),
    );
  }
}
