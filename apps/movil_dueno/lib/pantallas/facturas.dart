import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';

/// Carga de facturas PDF del proveedor en dos pasos: la PC analiza el PDF,
/// Leo revisa/corrige en el celular, y recién ahí se suma al stock.
class PantallaFacturas extends StatefulWidget {
  const PantallaFacturas({super.key});

  @override
  State<PantallaFacturas> createState() => _PantallaFacturasState();
}

class _PantallaFacturasState extends State<PantallaFacturas> {
  FacturaAnalizada? _factura;
  List<ResultadoFactura>? _resultados;
  bool _ocupado = false;

  /// Se cortó la comunicación al sumar y no se sabe si se aplicó.
  bool _incierto = false;

  ClienteApi get _api => context.read<SesionEstado>().api;

  Future<void> _elegir() async {
    final archivo = await FilePicker.pickFile(type: FileType.custom, allowedExtensions: ['pdf']);
    if (archivo == null || !mounted) return;
    setState(() {
      _ocupado = true;
      _resultados = null;
      _incierto = false;
    });
    try {
      final bytes = await archivo.readAsBytes();
      final f = await _api.analizarFactura(bytes, archivo.name);
      if (mounted) setState(() => _factura = f);
    } on ApiError catch (e) {
      if (mounted) mostrarMensaje(context, e.mensaje, error: true);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _corregirCodigo(ItemFactura item) async {
    final control = TextEditingController(text: item.codigo);
    final nuevo = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Corregir código'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('En la factura: ${item.nombre}'),
          const SizedBox(height: 12),
          CampoCodigo(controller: control, etiqueta: 'Código del producto', alEnviar: (t) => Navigator.pop(context, t)),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancelar')),
          FilledButton(onPressed: () => Navigator.pop(context, control.text.trim()), child: const Text('Usar')),
        ],
      ),
    );
    control.dispose();
    if (nuevo == null || nuevo.isEmpty || !mounted) return;
    try {
      final p = await _api.producto(nuevo);
      setState(() {
        item
          ..codigo = p.codigo
          ..existe = true
          ..nombreSistema = p.nombre
          ..stockActual = p.stock
          ..seleccionado = item.cantidadValida;
      });
    } on ApiError catch (e) {
      if (!mounted) return;
      setState(() {
        item
          ..codigo = nuevo
          ..existe = false
          ..nombreSistema = null
          ..stockActual = null
          ..seleccionado = false;
      });
      mostrarMensaje(context, e.status == 404 ? 'El código $nuevo tampoco existe en el sistema.' : e.mensaje, error: true);
    }
  }

  Future<void> _aplicar() async {
    final f = _factura!;
    final items = f.items.where((i) => i.seleccionado && i.existe).toList();
    // un solo renglón fuera de rango hace rechazar la factura entera (422)
    final invalidos = items.where((i) => !i.cantidadValida).length;
    if (invalidos > 0) {
      final rango = 'de 1 a ${numero(ItemFactura.cantidadMaxima)}';
      mostrarMensaje(
          context,
          invalidos == 1
              ? 'Hay 1 renglón con cantidad inválida (marcado en rojo): corregí la cantidad ($rango) o destildalo.'
              : 'Hay $invalidos renglones con cantidad inválida (marcados en rojo): corregí la cantidad ($rango) '
                  'o destildalos.',
          error: true);
      return;
    }
    final unidades = items.fold<int>(0, (s, i) => s + i.cantidad);
    final ok = await confirmar(context,
        titulo: '¿Sumar al stock?',
        mensaje: 'Se van a sumar $unidades unidades de ${items.length} productos (factura "${f.nombre}").',
        si: 'Sumar');
    if (!ok || !mounted) return;
    setState(() => _ocupado = true);
    try {
      final r = await _api.aplicarFactura(f.nombre, items);
      if (mounted) setState(() => _resultados = r);
    } on ApiError catch (e) {
      if (!mounted) return;
      mostrarMensaje(context, e.mensaje, error: true);
      if (e.incierto) {
        // no se sabe si se sumó: se destilda todo para que no se cargue dos
        // veces sin querer, y se trae el stock actual de lo que se mandó
        setState(() {
          _incierto = true;
          for (final i in f.items) {
            i.seleccionado = false;
          }
        });
        _refrescarStock(items);
      }
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _refrescarStock(List<ItemFactura> items) async {
    await Future.wait([
      for (final item in items)
        _api.producto(item.codigo).then((p) {
          if (mounted) setState(() => item.stockActual = p.stock);
        }, onError: (Object _) {}), // sin conexión todavía: queda el stock que había
    ]);
  }

  void _reiniciar() => setState(() {
        _factura = null;
        _resultados = null;
        _incierto = false;
      });

  @override
  Widget build(BuildContext context) {
    final f = _factura;
    final seleccionados = f?.items.where((i) => i.seleccionado && i.existe).length ?? 0;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Facturas'),
        actions: [
          if (f != null) IconButton(tooltip: 'Empezar de nuevo', onPressed: _reiniciar, icon: const Icon(Icons.restart_alt)),
          const BotonAjustes(),
        ],
      ),
      body: Stack(children: [
        if (_resultados != null)
          _Resultados(resultados: _resultados!, alTerminar: _reiniciar)
        else if (f == null)
          EstadoVacio(
            icono: Icons.picture_as_pdf_outlined,
            titulo: 'Cargá una factura PDF del proveedor',
            detalle: 'La que te llegó por WhatsApp, mail o Drive. La PC la lee, vos revisás, y se suma al stock.\n\n'
                'Las fotos de facturas en papel no se pueden leer: esas cargalas desde Stock.',
            accion: FilledButton.icon(
              onPressed: _ocupado ? null : _elegir,
              icon: const Icon(Icons.upload_file),
              label: const Text('Elegir PDF'),
            ),
          )
        else
          _Revision(
            factura: f,
            incierto: _incierto,
            alCambiar: () => setState(() {}),
            alCorregir: _corregirCodigo,
          ),
        if (_ocupado) const Positioned(top: 0, left: 0, right: 0, child: LinearProgressIndicator()),
      ]),
      bottomNavigationBar: f == null || _resultados != null
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: FilledButton.icon(
                  onPressed: _ocupado || seleccionados == 0 ? null : _aplicar,
                  icon: const Icon(Icons.add_task),
                  label: Text('Sumar $seleccionados ítems al stock'),
                ),
              ),
            ),
    );
  }
}

class _Revision extends StatelessWidget {
  const _Revision({required this.factura, required this.incierto, required this.alCambiar, required this.alCorregir});
  final FacturaAnalizada factura;
  final bool incierto;
  final VoidCallback alCambiar;
  final Future<void> Function(ItemFactura) alCorregir;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final noExisten = factura.items.where((i) => !i.existe).length;
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 24), children: [
      Text(factura.nombre, style: t.textTheme.titleMedium),
      Text('${factura.items.length} ítems detectados', style: t.textTheme.bodyMedium),
      if (incierto)
        _Aviso(color: c.peligro, icono: Icons.sync_problem,
            texto: 'Se cortó la comunicación al sumar y no se sabe si se aplicó. Revisá los últimos movimientos en '
                'Stock antes de repetir: destildamos todo para que no se cargue dos veces.'),
      if (factura.escaneada)
        _Aviso(color: c.aviso, icono: Icons.image_outlined,
            texto: 'Este PDF parece una imagen escaneada: no tiene texto para leer. Cargá los productos a mano desde Stock.'),
      if (noExisten > 0)
        _Aviso(color: c.peligro, icono: Icons.help_outline,
            texto: '$noExisten código(s) no existen en el sistema. Tocá el código para corregirlo (podés escanearlo), '
                'o dalo de alta en la PC.'),
      const SizedBox(height: 8),
      for (final item in factura.items) _FilaItem(item: item, alCambiar: alCambiar, alCorregir: alCorregir),
      if (factura.noReconocidas.isNotEmpty) ...[
        const SizedBox(height: 12),
        Card(
          child: ExpansionTile(
            leading: Icon(Icons.warning_amber_rounded, color: c.aviso),
            title: Text('${factura.noReconocidas.length} líneas sin reconocer'),
            subtitle: const Text('Revisalas: puede ser mercadería que hay que cargar a mano'),
            children: [
              for (final l in factura.noReconocidas)
                ListTile(dense: true, title: Text(l, style: const TextStyle(fontFamily: 'monospace'))),
            ],
          ),
        ),
      ],
    ]);
  }
}

class _FilaItem extends StatelessWidget {
  const _FilaItem({required this.item, required this.alCambiar, required this.alCorregir});
  final ItemFactura item;
  final VoidCallback alCambiar;
  final Future<void> Function(ItemFactura) alCorregir;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(4, 8, 12, 8),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Checkbox(
            value: item.seleccionado,
            onChanged: item.existe
                ? (v) {
                    item.seleccionado = v ?? false;
                    alCambiar();
                  }
                : null,
          ),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(item.nombre.isEmpty ? '(sin descripción)' : item.nombre, style: t.textTheme.titleSmall),
              InkWell(
                onTap: () => alCorregir(item),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Text(item.codigo, style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.primary)),
                    const SizedBox(width: 4),
                    Icon(Icons.edit, size: 14, color: t.colorScheme.primary),
                  ]),
                ),
              ),
              if (item.existe)
                Text('En sistema: ${item.nombreSistema} (stock ${item.stockActual})',
                    style: t.textTheme.bodySmall?.copyWith(color: c.ok))
              else
                Text('No existe en el sistema', style: t.textTheme.bodySmall?.copyWith(color: c.peligro)),
              if (item.posibleDuplicado)
                Text('¿Repetido? El lector de PDF a veces lee la misma línea dos veces.',
                    style: t.textTheme.bodySmall?.copyWith(color: c.aviso)),
              if (!item.cantidadValida)
                Text('Cantidad inválida: tiene que ser de 1 a ${numero(ItemFactura.cantidadMaxima)}.',
                    style: t.textTheme.bodySmall?.copyWith(color: c.peligro)),
            ]),
          ),
          Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
            ActionChip(
              label: Text('x${item.cantidad}'),
              labelStyle: item.cantidadValida ? null : TextStyle(color: c.peligro, fontWeight: FontWeight.w700),
              side: item.cantidadValida ? null : BorderSide(color: c.peligro),
              onPressed: () async {
                final n = await pedirNumero(context, titulo: 'Cantidad recibida', inicial: '${item.cantidad}');
                if (n == null || !context.mounted) return;
                if (!n.isFinite || !ItemFactura.cantidadEsValida(n.round())) {
                  mostrarMensaje(context, 'La cantidad tiene que ser de 1 a ${numero(ItemFactura.cantidadMaxima)}.',
                      error: true);
                  return;
                }
                item.cantidad = n.round();
                alCambiar();
              },
            ),
            if (item.precioCompra != null)
              Text('costo ${moneda(item.precioCompra!)}', style: t.textTheme.bodySmall),
          ]),
        ]),
      ),
    );
  }
}

class _Aviso extends StatelessWidget {
  const _Aviso({required this.color, required this.icono, required this.texto});
  final Color color;
  final IconData icono;
  final String texto;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(top: 12),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: color.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(12)),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icono, color: color),
          const SizedBox(width: 10),
          Expanded(child: Text(texto)),
        ]),
      );
}

class _Resultados extends StatelessWidget {
  const _Resultados({required this.resultados, required this.alTerminar});
  final List<ResultadoFactura> resultados;
  final VoidCallback alTerminar;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final ok = resultados.where((r) => r.ok).length;
    final fallidos = resultados.where((r) => !r.ok).toList();
    return ListView(padding: const EdgeInsets.all(24), children: [
      Icon(fallidos.isEmpty ? Icons.check_circle : Icons.error_outline, size: 72, color: fallidos.isEmpty ? c.ok : c.aviso),
      const SizedBox(height: 12),
      Text(fallidos.isEmpty ? '¡Listo! Stock actualizado' : 'Cargado con observaciones',
          textAlign: TextAlign.center, style: t.textTheme.headlineSmall),
      const SizedBox(height: 8),
      Text('$ok productos sumados al stock${fallidos.isEmpty ? '.' : ', ${fallidos.length} con error:'}',
          textAlign: TextAlign.center),
      for (final f in fallidos)
        ListTile(leading: Icon(Icons.close, color: c.peligro), title: Text(f.codigo), subtitle: Text(f.error ?? '')),
      const SizedBox(height: 24),
      FilledButton.icon(onPressed: alTerminar, icon: const Icon(Icons.upload_file), label: const Text('Cargar otra factura')),
    ]);
  }
}
