import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';
import 'modo_lector.dart';

class PantallaStock extends StatefulWidget {
  const PantallaStock({super.key, required this.activa});
  final bool activa;

  @override
  State<PantallaStock> createState() => _PantallaStockState();
}

class _PantallaStockState extends State<PantallaStock> {
  final _busqueda = TextEditingController();
  final _motivo = TextEditingController();
  List<Producto> _resultados = [];
  Producto? _producto;
  List<Movimiento> _movimientos = [];
  int _cantidad = 1;
  bool _buscando = false;
  bool _moviendo = false;

  ClienteApi get _api => context.read<SesionEstado>().api;

  @override
  void initState() {
    super.initState();
    _cargarMovimientos();
  }

  @override
  void didUpdateWidget(PantallaStock old) {
    super.didUpdateWidget(old);
    if (widget.activa && !old.activa) _cargarMovimientos();
  }

  @override
  void dispose() {
    _busqueda.dispose();
    _motivo.dispose();
    super.dispose();
  }

  Future<void> _cargarMovimientos() async {
    try {
      final m = await _api.movimientos(limite: 20);
      if (mounted) setState(() => _movimientos = m);
    } on ApiError {
      // la lista de movimientos es secundaria: si falla no se interrumpe a Leo
    }
  }

  Future<void> _buscar(String texto) async {
    if (texto.isEmpty) return;
    setState(() => _buscando = true);
    try {
      final r = await _api.productos(q: texto);
      if (!mounted) return;
      final exacto = r.where((p) => p.codigo == texto).toList();
      setState(() {
        _resultados = r;
        _producto = exacto.isNotEmpty ? exacto.first : (r.length == 1 ? r.first : null);
        _cantidad = 1;
      });
      if (r.isEmpty) mostrarMensaje(context, 'No se encontró ningún producto con "$texto".', error: true);
    } on ApiError catch (e) {
      if (mounted) mostrarMensaje(context, e.mensaje, error: true);
    } finally {
      if (mounted) setState(() => _buscando = false);
    }
  }

  Future<void> _mover({required bool sumar}) async {
    final p = _producto;
    if (p == null) return;
    setState(() => _moviendo = true);
    try {
      final r = await _api.movimiento(p.codigo, _cantidad, sumar: sumar, motivo: _motivo.text);
      if (!mounted) return;
      setState(() {
        _producto = p.conStock(r.stockNuevo);
        _motivo.clear();
      });
      mostrarMensaje(context, '${sumar ? '+' : '−'}$_cantidad  ${p.nombre} → stock ${r.stockNuevo}');
      _cargarMovimientos();
    } on ApiError catch (e) {
      if (mounted) mostrarMensaje(context, e.mensaje, error: true);
    } finally {
      if (mounted) setState(() => _moviendo = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _producto;
    return Scaffold(
      appBar: AppBar(title: const Text('Stock'), actions: const [BotonAjustes()]),
      body: RefreshIndicator(
        onRefresh: _cargarMovimientos,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            CampoCodigo(controller: _busqueda, alEnviar: _buscar),
            if (_buscando) const LinearProgressIndicator(),
            const SizedBox(height: 12),
            _BotonModoLector(alVolver: _cargarMovimientos),
            if (p == null && _resultados.length > 1) ...[
              Seccion('${_resultados.length} resultados'),
              Card(
                child: Column(children: [
                  for (final r in _resultados)
                    ListTile(
                      title: Text(r.nombre),
                      subtitle: Text(r.codigo),
                      trailing: PastillaStock(producto: r),
                      onTap: () => setState(() {
                        _producto = r;
                        _cantidad = 1;
                      }),
                    ),
                ]),
              ),
            ],
            if (p != null) ...[
              const SizedBox(height: 16),
              _TarjetaMovimiento(
                producto: p,
                cantidad: _cantidad,
                motivo: _motivo,
                ocupado: _moviendo,
                alCambiarCantidad: (n) => setState(() => _cantidad = n),
                alMover: _mover,
                alCerrar: () => setState(() => _producto = null),
              ),
            ],
            const Seccion('Últimos movimientos'),
            if (_movimientos.isEmpty)
              const Card(child: Padding(padding: EdgeInsets.all(20), child: Text('Sin movimientos todavía.')))
            else
              Card(child: Column(children: [for (final m in _movimientos) FilaMovimiento(m: m)])),
          ],
        ),
      ),
    );
  }
}

class _BotonModoLector extends StatelessWidget {
  const _BotonModoLector({required this.alVolver});
  final VoidCallback alVolver;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Card(
      color: t.colorScheme.primary.withValues(alpha: 0.12),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        leading: Icon(Icons.qr_code_scanner, size: 36, color: t.colorScheme.primary),
        title: const Text('Modo lector', style: TextStyle(fontWeight: FontWeight.w700)),
        subtitle: const Text('Escaneá productos uno tras otro: cada lectura resta (o suma) 1 unidad'),
        trailing: const Icon(Icons.chevron_right),
        onTap: () async {
          await Navigator.of(context).push(MaterialPageRoute(builder: (_) => const PantallaModoLector()));
          alVolver();
        },
      ),
    );
  }
}

class _TarjetaMovimiento extends StatelessWidget {
  const _TarjetaMovimiento({
    required this.producto,
    required this.cantidad,
    required this.motivo,
    required this.ocupado,
    required this.alCambiarCantidad,
    required this.alMover,
    required this.alCerrar,
  });

  final Producto producto;
  final int cantidad;
  final TextEditingController motivo;
  final bool ocupado;
  final ValueChanged<int> alCambiarCantidad;
  final Future<void> Function({required bool sumar}) alMover;
  final VoidCallback alCerrar;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final color = colorEstado(context, producto.estado);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(producto.nombre, style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text([producto.codigo, if (producto.marca != null) producto.marca!].join(' · '),
                    style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
                Text('Precio ${moneda(producto.precioVenta)}', style: t.textTheme.bodyMedium),
              ]),
            ),
            IconButton(tooltip: 'Cerrar', onPressed: alCerrar, icon: const Icon(Icons.close)),
          ]),
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.symmetric(vertical: 14),
            decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(14)),
            child: Column(children: [
              Text('Stock actual', style: TextStyle(color: color)),
              Text('${producto.stock}', style: t.textTheme.displayMedium?.copyWith(color: color, fontWeight: FontWeight.w800)),
              Text(_umbrales(producto), style: t.textTheme.bodySmall),
            ]),
          ),
          const SizedBox(height: 16),
          SelectorCantidad(valor: cantidad, alCambiar: alCambiarCantidad),
          const SizedBox(height: 12),
          TextField(controller: motivo, decoration: const InputDecoration(labelText: 'Motivo (opcional)')),
          const SizedBox(height: 16),
          Row(children: [
            Expanded(
              child: FilledButton.icon(
                style: FilledButton.styleFrom(backgroundColor: c.peligro, foregroundColor: Colors.white),
                onPressed: ocupado ? null : () => alMover(sumar: false),
                icon: const Icon(Icons.remove_circle_outline),
                label: Text('Restar $cantidad'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: FilledButton.icon(
                style: FilledButton.styleFrom(backgroundColor: c.ok, foregroundColor: Colors.white),
                onPressed: ocupado ? null : () => alMover(sumar: true),
                icon: const Icon(Icons.add_circle_outline),
                label: Text('Sumar $cantidad'),
              ),
            ),
          ]),
        ]),
      ),
    );
  }

  static String _umbrales(Producto p) {
    final partes = [
      if (p.stockMinimo > 0) 'mínimo ${p.stockMinimo}',
      if (p.stockMaximo > 0) 'máximo ${p.stockMaximo}',
    ];
    return partes.isEmpty ? 'sin umbrales de alerta' : partes.join(' · ');
  }
}

class FilaMovimiento extends StatelessWidget {
  const FilaMovimiento({super.key, required this.m});
  final Movimiento m;

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    final t = Theme.of(context);
    final color = m.esEntrada ? c.ok : c.peligro;
    return ListTile(
      dense: true,
      leading: Icon(m.esEntrada ? Icons.arrow_downward_rounded : Icons.arrow_upward_rounded, color: color),
      title: Text(m.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text('${m.tipoLegible} · ${fechaHora(m.fechaHora)} · ${m.usuario}', maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.end, children: [
        Text('${m.esEntrada ? '+' : '−'}${m.cantidad}', style: TextStyle(color: color, fontWeight: FontWeight.w700)),
        Text('queda ${m.stockResultante}', style: t.textTheme.bodySmall),
      ]),
    );
  }
}
