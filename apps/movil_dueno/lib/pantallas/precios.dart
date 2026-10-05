import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';

/// "Buscar en": código/nombre, o los mismos campos del filtro de la PC.
const _modos = {'todo': 'Código o nombre', 'marca': 'Marca', 'proveedor': 'Proveedor', 'categoria': 'Categoría'};

/// Lo máximo que devuelve GET /api/productos. Si una búsqueda trae
/// exactamente esto, puede haber más productos que no se ven (y que el
/// ajuste masivo no va a tocar): se avisa.
const limiteProductos = 2000;
const avisoRecorte = 'Se muestran los primeros $limiteProductos: refiná la búsqueda para ajustar el resto';

class PantallaPrecios extends StatefulWidget {
  const PantallaPrecios({super.key, required this.activa});
  final bool activa;

  @override
  State<PantallaPrecios> createState() => _PantallaPreciosState();
}

class _PantallaPreciosState extends State<PantallaPrecios> {
  final _busqueda = TextEditingController();
  String _modo = 'todo';
  List<Producto> _productos = [];
  final _seleccion = <String>{};
  bool _cargando = false;
  bool _cargadoUnaVez = false;
  bool _recortado = false;
  String? _error;

  ClienteApi get _api => context.read<SesionEstado>().api;

  @override
  void didUpdateWidget(PantallaPrecios old) {
    super.didUpdateWidget(old);
    if (widget.activa && !_cargadoUnaVez) _buscar(_busqueda.text);
  }

  @override
  void dispose() {
    _busqueda.dispose();
    super.dispose();
  }

  Future<void> _buscar(String texto) async {
    setState(() {
      _cargando = true;
      _cargadoUnaVez = true;
    });
    try {
      final r = _modo == 'todo'
          ? await _api.productos(q: texto.trim(), limite: limiteProductos)
          : await _api.productos(campo: _modo, valor: texto.trim(), limite: limiteProductos);
      if (!mounted) return;
      setState(() {
        _productos = r;
        _recortado = r.length >= limiteProductos;
        _seleccion.removeWhere((c) => !r.any((p) => p.codigo == c));
        _error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  List<String> get _afectados =>
      _seleccion.isNotEmpty ? _seleccion.toList() : [for (final p in _productos) p.codigo];

  Future<void> _editarPrecio(Producto p) async {
    final nuevo = await showModalBottomSheet<double>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _HojaPrecio(producto: p),
    );
    if (nuevo == null || !mounted) return;
    try {
      final r = await _api.fijarPrecio(p.codigo, nuevo);
      if (!mounted) return;
      setState(() {
        final i = _productos.indexWhere((x) => x.codigo == p.codigo);
        if (i >= 0) _productos[i] = p.conPrecio(r.nuevo ?? nuevo);
      });
      mostrarMensaje(context, '${p.nombre}: ${moneda(p.precioVenta)} → ${moneda(r.nuevo ?? nuevo)}');
    } on ApiError catch (e) {
      if (!mounted) return;
      mostrarMensaje(context, e.mensaje, error: true);
      if (e.incierto) _buscar(_busqueda.text); // que se vea el precio que quedó de verdad
    }
  }

  Future<void> _ajusteMasivo() async {
    // vuelve la cantidad aplicada, o el ApiError si no se sabe si se aplicó
    final resultado = await Navigator.of(context).push<Object>(
      MaterialPageRoute(builder: (_) => PantallaAjusteMasivo(codigos: _afectados)),
    );
    if (resultado == null || !mounted) return;
    if (resultado is ApiError) {
      mostrarMensaje(context, resultado.mensaje, error: true);
    } else {
      mostrarMensaje(context, 'Listo: $resultado precios actualizados.');
    }
    _seleccion.clear();
    _buscar(_busqueda.text);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final todosSeleccionados = _productos.isNotEmpty && _seleccion.length == _productos.length;
    return Scaffold(
      appBar: AppBar(title: const Text('Precios'), actions: const [BotonAjustes()]),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: CampoCodigo(
            controller: _busqueda,
            etiqueta: 'Buscar en: ${_modos[_modo]}',
            alEnviar: _buscar,
            alEscanear: (codigo) {
              setState(() => _modo = 'todo'); // un código escaneado se busca por código, no por marca
              _buscar(codigo);
            },
          ),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: Row(children: [
            for (final e in _modos.entries)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ChoiceChip(
                  label: Text(e.value),
                  selected: _modo == e.key,
                  onSelected: (_) {
                    setState(() => _modo = e.key);
                    if (_busqueda.text.isNotEmpty) _buscar(_busqueda.text);
                  },
                ),
              ),
          ]),
        ),
        if (_cargando) const LinearProgressIndicator(),
        if (_productos.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 4, 16, 0),
            child: Row(children: [
              Checkbox(
                value: todosSeleccionados ? true : (_seleccion.isEmpty ? false : null),
                tristate: true,
                onChanged: (_) => setState(() {
                  if (todosSeleccionados) {
                    _seleccion.clear();
                  } else {
                    _seleccion.addAll(_productos.map((p) => p.codigo));
                  }
                }),
              ),
              Expanded(
                child: Text(
                  _seleccion.isEmpty
                      ? '${_productos.length} productos'
                      : '${_seleccion.length} de ${_productos.length} seleccionados',
                  style: t.textTheme.labelLarge,
                ),
              ),
            ]),
          ),
        if (_recortado && _productos.isNotEmpty)
          const Padding(padding: EdgeInsets.fromLTRB(16, 0, 16, 4), child: _AvisoRecorte()),
        Expanded(child: _lista()),
      ]),
      bottomNavigationBar: _productos.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  if (_recortado) const Padding(padding: EdgeInsets.only(bottom: 8), child: _AvisoRecorte()),
                  FilledButton.icon(
                    onPressed: _ajusteMasivo,
                    icon: const Icon(Icons.trending_up),
                    label: Text(_seleccion.isEmpty
                        ? 'Ajustar los ${_productos.length} precios'
                        : 'Ajustar ${_seleccion.length} seleccionados'),
                  ),
                ]),
              ),
            ),
    );
  }

  Widget _lista() {
    if (!_cargadoUnaVez) {
      return EstadoVacio(
        icono: Icons.sell_outlined,
        titulo: 'Buscá productos para ver y cambiar precios',
        detalle: 'Por código, nombre, marca, proveedor o categoría. También podés escanear.',
        accion: FilledButton.tonal(onPressed: () => _buscar(''), child: const Text('Ver todos')),
      );
    }
    if (_error != null) return ErrorConReintento(mensaje: _error!, reintentar: () => _buscar(_busqueda.text));
    if (_productos.isEmpty && !_cargando) {
      return const EstadoVacio(icono: Icons.search_off, titulo: 'No hay productos que coincidan');
    }
    return RefreshIndicator(
      onRefresh: () => _buscar(_busqueda.text),
      child: ListView.builder(
        padding: const EdgeInsets.only(bottom: 16),
        itemCount: _productos.length,
        itemBuilder: (context, i) {
          final p = _productos[i];
          final t = Theme.of(context);
          final margen = p.margen;
          return ListTile(
            leading: Checkbox(
              value: _seleccion.contains(p.codigo),
              onChanged: (v) => setState(() => v == true ? _seleccion.add(p.codigo) : _seleccion.remove(p.codigo)),
            ),
            title: Text(p.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text([p.codigo, if (p.marca != null) p.marca!].join(' · '), maxLines: 1),
            trailing: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.end, children: [
              Text(moneda(p.precioVenta), style: t.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
              if (margen != null)
                Text('margen ${margen.toStringAsFixed(0)}%',
                    style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
            ]),
            onTap: () => _editarPrecio(p),
          );
        },
      ),
    );
  }
}

/// La búsqueda trajo el máximo de productos: puede haber más que no se ven.
class _AvisoRecorte extends StatelessWidget {
  const _AvisoRecorte();

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    return Row(children: [
      Icon(Icons.warning_amber_rounded, size: 18, color: c.aviso),
      const SizedBox(width: 6),
      Expanded(child: Text(avisoRecorte, style: Theme.of(context).textTheme.bodySmall?.copyWith(color: c.aviso))),
    ]);
  }
}

/// Hoja para poner un precio exacto, con el margen calculado en vivo.
class _HojaPrecio extends StatefulWidget {
  const _HojaPrecio({required this.producto});
  final Producto producto;

  @override
  State<_HojaPrecio> createState() => _HojaPrecioState();
}

class _HojaPrecioState extends State<_HojaPrecio> {
  late final _precio = TextEditingController(text: _sinDecimalesInutiles(widget.producto.precioVenta));

  static String _sinDecimalesInutiles(double v) => v == v.roundToDouble() ? v.toStringAsFixed(0) : v.toStringAsFixed(2);

  @override
  void dispose() {
    _precio.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final p = widget.producto;
    final nuevo = parsearNumero(_precio.text);
    final margen = (nuevo != null && p.precioCompra > 0) ? (nuevo / p.precioCompra - 1) * 100 : null;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(context).viewInsets.bottom + 24),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(p.nombre, style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
        Text(p.codigo, style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
        const SizedBox(height: 16),
        Row(children: [
          Expanded(child: _Info('Precio actual', moneda(p.precioVenta))),
          Expanded(child: _Info('Costo', p.precioCompra > 0 ? moneda(p.precioCompra) : 'sin cargar')),
        ]),
        const SizedBox(height: 16),
        TextField(
          controller: _precio,
          autofocus: true,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: t.textTheme.headlineSmall,
          decoration: InputDecoration(
            labelText: 'Precio nuevo',
            prefixText: r'$ ',
            helperText: margen == null ? null : 'Margen sobre el costo: ${margen.toStringAsFixed(1)}%',
          ),
          onChanged: (_) => setState(() {}),
        ),
        const SizedBox(height: 16),
        FilledButton(
          onPressed: nuevo == null || nuevo < 0 ? null : () => Navigator.pop(context, nuevo),
          child: const Text('Guardar precio'),
        ),
      ]),
    );
  }
}

class _Info extends StatelessWidget {
  const _Info(this.titulo, this.valor);
  final String titulo;
  final String valor;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(titulo, style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
      Text(valor, style: t.textTheme.titleMedium),
    ]);
  }
}

/// Ajuste masivo por % o $ fijo, con la misma regla que la PC (redondeo a
/// la centena superior) y vista previa obligatoria antes de aplicar.
class PantallaAjusteMasivo extends StatefulWidget {
  const PantallaAjusteMasivo({super.key, required this.codigos});
  final List<String> codigos;

  @override
  State<PantallaAjusteMasivo> createState() => _PantallaAjusteMasivoState();
}

class _PantallaAjusteMasivoState extends State<PantallaAjusteMasivo> {
  final _valor = TextEditingController();
  bool _porcentaje = true;
  bool _redondear = true;
  bool _ocupado = false;
  List<CambioPrecio>? _vista;

  ClienteApi get _api => context.read<SesionEstado>().api;

  double? get _numero => parsearNumero(_valor.text);

  @override
  void dispose() {
    _valor.dispose();
    super.dispose();
  }

  void _cambio() => setState(() => _vista = null); // cualquier cambio invalida la vista previa

  void _invertirSigno() {
    final t = _valor.text.trim();
    _valor.text = t.startsWith('-') ? t.substring(1) : '-$t';
    _cambio();
  }

  Future<void> _previsualizar() async {
    final n = _numero;
    if (n == null || n == 0) return;
    setState(() => _ocupado = true);
    try {
      final v = await _api.previsualizarPrecios(widget.codigos,
          porcentaje: _porcentaje ? n : null, montoFijo: _porcentaje ? null : n, redondear: _redondear);
      if (mounted) setState(() => _vista = v);
    } on ApiError catch (e) {
      if (mounted) mostrarMensaje(context, e.mensaje, error: true);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _aplicar() async {
    final n = _numero!;
    final descripcion = _porcentaje ? '${n > 0 ? '+' : ''}${numero(n)}%' : '${n > 0 ? '+' : '−'}${moneda(n.abs())}';
    final ok = await confirmar(context,
        titulo: '¿Aplicar $descripcion?',
        mensaje: 'Se van a cambiar ${widget.codigos.length} precios. La caja cobra el precio nuevo desde la próxima venta.',
        si: 'Aplicar');
    if (!ok || !mounted) return;
    setState(() => _ocupado = true);
    try {
      final r = await _api.aplicarPrecios(widget.codigos,
          porcentaje: _porcentaje ? n : null, montoFijo: _porcentaje ? null : n, redondear: _redondear);
      if (mounted) Navigator.pop(context, r.where((c) => c.ok).length);
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.incierto) {
        // no se sabe si se aplicó: se vuelve a la lista, que se recarga con
        // los precios reales (repetir acá podría subirlos dos veces)
        Navigator.pop(context, e);
        return;
      }
      mostrarMensaje(context, e.mensaje, error: true);
      setState(() => _ocupado = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final atajos = _porcentaje ? [-10, -5, 3, 5, 10, 15] : [-100, 50, 100, 200, 500];
    final vista = _vista;
    return Scaffold(
      appBar: AppBar(title: Text('Ajustar ${widget.codigos.length} precios')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        SegmentedButton<bool>(
          segments: const [
            ButtonSegment(value: true, icon: Icon(Icons.percent), label: Text('Porcentaje')),
            ButtonSegment(value: false, icon: Icon(Icons.attach_money), label: Text('Monto fijo')),
          ],
          selected: {_porcentaje},
          onSelectionChanged: (s) {
            _porcentaje = s.first;
            _cambio();
          },
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _valor,
          keyboardType: const TextInputType.numberWithOptions(signed: true, decimal: true),
          style: t.textTheme.headlineSmall,
          decoration: InputDecoration(
            labelText: _porcentaje ? 'Porcentaje (negativo = baja)' : r'Monto en $ (negativo = baja)',
            prefixIcon: IconButton(tooltip: 'Cambiar signo', icon: const Icon(Icons.exposure), onPressed: _invertirSigno),
            suffixText: _porcentaje ? '%' : r'$',
          ),
          onChanged: (_) => _cambio(),
        ),
        const SizedBox(height: 8),
        Wrap(spacing: 8, children: [
          for (final a in atajos)
            ActionChip(
              label: Text(_porcentaje ? '${a > 0 ? '+' : ''}$a%' : '${a > 0 ? '+' : '−'}\$${a.abs()}'),
              onPressed: () {
                _valor.text = '$a';
                _cambio();
              },
            ),
        ]),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Redondear a la centena superior'),
          subtitle: const Text(r'Ej.: $ 2.575 pasa a $ 2.600 (como en la PC)'),
          value: _redondear,
          onChanged: (v) {
            _redondear = v;
            _cambio();
          },
        ),
        const SizedBox(height: 8),
        if (vista == null)
          FilledButton.tonalIcon(
            onPressed: _ocupado || _numero == null || _numero == 0 ? null : _previsualizar,
            icon: const Icon(Icons.visibility_outlined),
            label: const Text('Ver cómo quedan'),
          )
        else ...[
          Seccion('Vista previa'),
          Card(
            child: Column(children: [
              for (final cp in vista.take(200))
                ListTile(
                  dense: true,
                  title: Text(cp.nombre ?? cp.codigo, maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: cp.ok ? null : Text(cp.error ?? 'error', style: TextStyle(color: c.peligro)),
                  trailing: cp.ok
                      ? Text.rich(TextSpan(children: [
                          TextSpan(text: moneda(cp.anterior!),
                              style: TextStyle(decoration: TextDecoration.lineThrough, color: t.colorScheme.onSurfaceVariant)),
                          WidgetSpan(
                            alignment: PlaceholderAlignment.middle,
                            child: Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 6),
                              child: Icon(Icons.arrow_forward, size: 16, color: t.colorScheme.onSurfaceVariant),
                            ),
                          ),
                          TextSpan(text: moneda(cp.nuevo!),
                              style: TextStyle(fontWeight: FontWeight.w700,
                                  color: cp.nuevo! >= cp.anterior! ? c.ok : c.peligro)),
                        ]))
                      : null,
                ),
              if (vista.length > 200)
                Padding(padding: const EdgeInsets.all(12), child: Text('… y ${vista.length - 200} más')),
            ]),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _ocupado ? null : _aplicar,
            icon: const Icon(Icons.check),
            label: Text('Aplicar a ${vista.where((v) => v.ok).length} productos'),
          ),
        ],
      ]),
    );
  }
}
