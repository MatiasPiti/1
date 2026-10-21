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
    await _confirmarCodigo(item, nuevo, porNombre: false);
  }

  /// Lista de productos parecidos por nombre (el sugerido primero). Tocar
  /// uno ES la confirmación humana: recién ahí el renglón se tilda.
  Future<void> _elegirPorNombre(ItemFactura item) async {
    final emp = item.emparejamiento!;
    final elegido = await showModalBottomSheet<Object>(
      context: context,
      isScrollControlled: true,
      builder: (context) {
        final t = Theme.of(context);
        return SafeArea(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.7),
            child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(16, 0, 16, 16), children: [
              Text('En la factura: ${item.nombre}', style: t.textTheme.titleMedium),
              const SizedBox(height: 4),
              Text('Elegí el producto correcto. Si no está, escribí el código a mano.', style: t.textTheme.bodySmall),
              const SizedBox(height: 8),
              for (final c in emp.candidatosOrdenados)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(c.nombre),
                  subtitle: Text(c.codigo == emp.sugerido?.codigo ? '${c.codigo} · sugerido' : c.codigo),
                  trailing: Text('${(c.score * 100).round()} %', style: t.textTheme.titleSmall),
                  onTap: () => Navigator.pop(context, c),
                ),
              TextButton.icon(
                onPressed: () => Navigator.pop(context, 'manual'),
                icon: const Icon(Icons.edit),
                label: const Text('No es ninguno: escribir el código'),
              ),
            ]),
          ),
        );
      },
    );
    if (!mounted || elegido == null) return;
    if (elegido is Candidato) {
      await _confirmarCodigo(item, elegido.codigo, porNombre: true);
    } else {
      await _corregirCodigo(item);
    }
  }

  /// Busca el producto en la PC y, si existe, deja el renglón confirmado y
  /// tildado (con el stock y el costo de ahora).
  Future<void> _confirmarCodigo(ItemFactura item, String codigo, {required bool porNombre}) async {
    try {
      final p = await _api.producto(codigo);
      if (!mounted) return;
      setState(() => item.confirmarProducto(p, porNombre: porNombre));
    } on ApiError catch (e) {
      if (!mounted) return;
      final noExiste = e.codigo == 'no_existe';
      setState(() {
        if (noExiste) {
          item
            ..codigo = codigo
            ..existe = false
            ..nombreSistema = null
            ..stockActual = null;
        }
        item
          ..emparejadoPorNombre = false
          ..seleccionado = false;
      });
      mostrarMensaje(context, noExiste ? 'El código $codigo tampoco existe en el sistema.' : e.mensaje, error: true);
    }
  }

  /// La factura ya se cargó hace un rato (mismo nombre y algún renglón igual).
  /// Por defecto NO se carga: aplicarla dos veces suma el stock dos veces.
  Future<bool> _preguntarCargarIgual(String detalle) async {
    final r = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('¿Esta factura ya se cargó?'),
        content: Text(detalle),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Cargar igual')),
          FilledButton(autofocus: true, onPressed: () => Navigator.pop(context, false), child: const Text('No cargar')),
        ],
      ),
    );
    return r ?? false;
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
    final porNombre = items.where((i) => i.emparejadoPorNombre).length;
    final ok = await confirmar(context,
        titulo: '¿Sumar al stock?',
        mensaje: 'Se van a sumar $unidades unidades de ${items.length} productos (factura "${f.nombre}").'
            '${porNombre == 0 ? '' : '\n\n$porNombre de esos ítems los emparejaste por nombre: revisá que sean los productos correctos.'}',
        si: 'Sumar');
    if (!ok || !mounted) return;
    setState(() => _ocupado = true);
    var forzar = false;
    try {
      while (true) {
        try {
          final r = await _api.aplicarFactura(f.nombre, items, forzar: forzar);
          if (mounted) setState(() => _resultados = r);
          return;
        } on ApiError catch (e) {
          if (e.codigo != 'factura_ya_aplicada' || forzar || !mounted) rethrow;
          setState(() => _ocupado = false);
          if (!await _preguntarCargarIgual(e.mensaje) || !mounted) return;
          setState(() => _ocupado = true);
          forzar = true;
        }
      }
    } on ApiError catch (e) {
      if (!mounted) return;
      mostrarMensaje(context, e.mensaje, error: true);
      if (e.incierto) {
        // no se sabe si se sumó: se destilda todo para que no se cargue dos
        // veces sin querer, y se trae el stock actual de lo que se mandó. Si
        // Leo lo vuelve a mandar, la PC lo frena con "esta factura ya se cargó".
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
            alElegir: _elegirPorNombre,
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
  const _Revision({
    required this.factura,
    required this.incierto,
    required this.alCambiar,
    required this.alCorregir,
    required this.alElegir,
  });
  final FacturaAnalizada factura;
  final bool incierto;
  final VoidCallback alCambiar;
  final Future<void> Function(ItemFactura) alCorregir;
  final Future<void> Function(ItemFactura) alElegir;

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
            texto: '$noExisten renglón(es) sin un producto del sistema. Elegí el producto por nombre o tocá el código '
                'para corregirlo (podés escanearlo), o dalo de alta en la PC. Nada se carga sin que lo confirmes.'),
      const SizedBox(height: 8),
      for (final item in factura.items)
        _FilaItem(item: item, alCambiar: alCambiar, alCorregir: alCorregir, alElegir: alElegir),
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

/// "Coincidencia segura" / "Parecido" / "Sin coincidencias".
String textoConfianza(String confianza) => switch (confianza) {
      'SEGURA' => 'Coincidencia segura',
      'POSIBLE' => 'Parecido',
      _ => 'Sin coincidencias',
    };

const textoDemasiados = 'No se buscó por nombre (la factura tiene muchos renglones sin código): corregí el código a '
    'mano o cargala desde el Panel de la PC.';

class _FilaItem extends StatelessWidget {
  const _FilaItem({required this.item, required this.alCambiar, required this.alCorregir, required this.alElegir});
  final ItemFactura item;
  final VoidCallback alCambiar;
  final Future<void> Function(ItemFactura) alCorregir;
  final Future<void> Function(ItemFactura) alElegir;

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
            key: Key('tildar_${item.indice}'),
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
                    Flexible(
                      child: Text(item.codigo.isEmpty ? '(sin código)' : item.codigo,
                          overflow: TextOverflow.ellipsis,
                          style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.primary)),
                    ),
                    const SizedBox(width: 4),
                    Icon(Icons.edit, size: 14, color: t.colorScheme.primary),
                  ]),
                ),
              ),
              if (item.existe)
                Text(
                    'En sistema: ${item.nombreSistema}${item.stockActual == null ? '' : ' (stock ${item.stockActual})'}'
                    '${item.emparejadoPorNombre ? ' · elegido por nombre' : ''}',
                    style: t.textTheme.bodySmall?.copyWith(color: c.ok))
              else
                Text(item.codigo.isEmpty ? 'La factura no trae código' : 'No existe en el sistema',
                    style: t.textTheme.bodySmall?.copyWith(color: c.peligro)),
              if (!item.existe && item.emparejamiento != null) _Emparejamiento(item: item, alElegir: alElegir),
              if (item.precioSospechoso && item.precioCompra != null)
                Text(
                    item.precioCompraActual != null && item.precioCompraActual! > 0
                        ? 'El precio leído (${moneda(item.precioCompra!)}) es muy distinto del costo guardado '
                            '(${moneda(item.precioCompraActual!)}): revisalo'
                        : 'El precio leído (${moneda(item.precioCompra!)}) parece raro: revisalo',
                    style: t.textTheme.bodySmall?.copyWith(color: c.aviso)),
              if (item.existe && item.precioCompra != null)
                InkWell(
                  onTap: () {
                    item.actualizarCosto = !item.actualizarCosto;
                    alCambiar();
                  },
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Checkbox(
                      key: Key('actualizar_costo_${item.indice}'),
                      visualDensity: VisualDensity.compact,
                      value: item.actualizarCosto,
                      onChanged: (v) {
                        item.actualizarCosto = v ?? false;
                        alCambiar();
                      },
                    ),
                    Flexible(child: Text('Actualizar el costo a ${moneda(item.precioCompra!)}',
                        style: t.textTheme.bodySmall)),
                  ]),
                ),
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

/// Lo que se encontró por nombre para un renglón sin producto del sistema.
class _Emparejamiento extends StatelessWidget {
  const _Emparejamiento({required this.item, required this.alElegir});
  final ItemFactura item;
  final Future<void> Function(ItemFactura) alElegir;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final emp = item.emparejamiento!;
    if (emp.demasiados) {
      return Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(textoDemasiados, style: t.textTheme.bodySmall?.copyWith(color: c.aviso)),
      );
    }
    if (emp.candidatos.isEmpty) {
      return Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text('No hay ningún producto parecido por nombre: corregí el código a mano.',
            style: t.textTheme.bodySmall?.copyWith(color: c.aviso)),
      );
    }
    final color = emp.confianza == 'SEGURA' ? c.ok : c.aviso;
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(color: color.withValues(alpha: 0.16), borderRadius: BorderRadius.circular(10)),
          child: Text(textoConfianza(emp.confianza),
              style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w700)),
        ),
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(minimumSize: const Size(0, 36)),
          onPressed: () => alElegir(item),
          icon: const Icon(Icons.manage_search, size: 18),
          label: Text(emp.sugerido == null ? 'Elegir producto' : '¿Es ${emp.sugerido!.nombre}?'),
        ),
      ]),
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
