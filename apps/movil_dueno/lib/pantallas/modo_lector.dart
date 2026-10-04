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
class FiltroLecturas {
  FiltroLecturas({this.pausa = const Duration(milliseconds: 1500)});
  final Duration pausa;
  String? _ultimo;
  DateTime _visto = DateTime.fromMillisecondsSinceEpoch(0);

  bool esNueva(String codigo, DateTime ahora) {
    final repetida = codigo == _ultimo && ahora.difference(_visto) < pausa;
    _ultimo = codigo;
    _visto = ahora;
    return !repetida;
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
  final _filtro = FiltroLecturas();
  final _manual = TextEditingController();
  final _lecturas = <_Lectura>[];
  bool _sumar = false; // igual que el lector de la PC: por defecto resta
  String? _procesando;

  @override
  void dispose() {
    _control.dispose();
    _manual.dispose();
    super.dispose();
  }

  void _alDetectar(BarcodeCapture captura) {
    final codigo = primerCodigo(captura);
    if (codigo == null) return;
    // mientras se procesa una lectura, otro código distinto se ignora sin
    // "consumirlo": se va a tomar en el próximo cuadro
    if (_procesando != null && codigo != _procesando) return;
    if (_filtro.esNueva(codigo, DateTime.now()) && _procesando == null) _procesar(codigo);
  }

  Future<void> _procesar(String codigo) async {
    setState(() => _procesando = codigo);
    final api = context.read<SesionEstado>().api;
    try {
      final r = await api.lector(codigo, sumar: _sumar);
      HapticFeedback.mediumImpact();
      SystemSound.play(SystemSoundType.click);
      _agregar(_Lectura(codigo: codigo, ok: true, texto: '${r.nombre}  ${_sumar ? '+1' : '−1'}  → quedan ${r.stockNuevo}'));
    } on ApiError catch (e) {
      HapticFeedback.heavyImpact();
      _agregar(_Lectura(codigo: codigo, ok: false, texto: e.status == 404 ? 'Código $codigo: no existe en el sistema' : e.mensaje));
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
              if (t.trim().isNotEmpty && _procesando == null) _procesar(t.trim());
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
