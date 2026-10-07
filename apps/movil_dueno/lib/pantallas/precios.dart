import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';
import 'stock.dart' show ChipOferta;

/// "Buscar en": código/nombre, o los mismos campos del filtro de la PC.
const _modos = {'todo': 'Código o nombre', 'marca': 'Marca', 'proveedor': 'Proveedor', 'categoria': 'Categoría'};

/// Lo máximo que devuelve GET /api/productos. Si una búsqueda trae
/// exactamente esto, puede haber más productos que no se ven (y que el
/// ajuste masivo no va a tocar): se avisa.
const limiteProductos = 2000;
const avisoRecorte = 'Se muestran los primeros $limiteProductos: refiná la búsqueda para ajustar el resto';

/// Límites del porcentaje del ajuste masivo (los mismos que acepta la PC).
const porcentajeMinimo = -90.0;
const porcentajeMaximo = 500.0;

final _numeroCampo = NumberFormat('#,##0.##', 'es_AR');

/// Cómo se escribe un número en un campo de precio: "3.800", "2.479,34",
/// "44,63". La PC lo vuelve a leer igual (pos_core/precios._num: el punto con
/// tres dígitos detrás es de miles, la coma es el decimal), así que el texto
/// que ve Leo y el número que se guarda son el mismo.
String textoPrecio(double? v) => v == null ? '' : _numeroCampo.format(v);

String _porcentaje(double? v) => v == null ? 'sin cargar' : '${textoPrecio(v)} %';

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

  /// La hoja avisa cada vez que trae precios frescos de la PC (al abrir, al
  /// recargar tras un 409 o un corte, al guardar): la fila queda al día.
  void _actualizarFila(PreciosProducto p) {
    if (!mounted) return;
    setState(() {
      final i = _productos.indexWhere((x) => x.codigo == p.codigo);
      if (i >= 0) _productos[i] = _productos[i].conPrecios(p);
    });
  }

  Future<void> _editarPrecio(Producto p) async {
    final cambio = await showModalBottomSheet<CambioPrecios>(
      context: context,
      isScrollControlled: true,
      builder: (_) => HojaPrecios(codigo: p.codigo, nombre: p.nombre, alActualizar: _actualizarFila),
    );
    if (cambio == null || !mounted) return;
    mostrarMensaje(context,
        '${cambio.despues.nombre}: ${moneda(cambio.antes.precioVenta)} → ${moneda(cambio.despues.precioVenta)}');
  }

  Future<void> _ajusteMasivo() async {
    final resumen = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (_) => PantallaAjusteMasivo(codigos: _afectados)),
    );
    if (!mounted) return;
    if (resumen != null) mostrarMensaje(context, resumen);
    _seleccion.clear();
    _buscar(_busqueda.text); // que se vean los precios que quedaron de verdad
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
          final distinto = p.precioEfectivo != p.precioVenta;
          return ListTile(
            leading: Checkbox(
              value: _seleccion.contains(p.codigo),
              onChanged: (v) => setState(() => v == true ? _seleccion.add(p.codigo) : _seleccion.remove(p.codigo)),
            ),
            title: Text(p.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text([p.codigo, if (p.marca != null) p.marca!].join(' · '), maxLines: 1),
            trailing: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.end, children: [
              // lo que cobra HOY la Caja; si hay oferta, el de lista tachado
              Row(mainAxisSize: MainAxisSize.min, children: [
                if (distinto) ...[const ChipOferta(), const SizedBox(width: 6)],
                Text(moneda(p.precioEfectivo), style: t.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
              ]),
              if (distinto)
                Text(moneda(p.precioVenta),
                    style: t.textTheme.bodySmall?.copyWith(
                        decoration: TextDecoration.lineThrough, color: t.colorScheme.onSurfaceVariant))
              else if (margen != null)
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

/// La cadena de 4 precios del Panel: Costo s/IVA → Precio costo → % Ganancia
/// → Precio final. Solo el final es obligatorio. Al confirmar un campo,
/// la PC recalcula los otros según cuál se tocó (la cuenta no se hace acá),
/// y al guardar se manda exactamente lo que se ve, con los 4 crudos de la
/// base como "esperado".
class HojaPrecios extends StatefulWidget {
  const HojaPrecios({super.key, required this.codigo, required this.nombre, this.alActualizar});
  final String codigo;
  final String nombre;
  final void Function(PreciosProducto)? alActualizar;

  @override
  State<HojaPrecios> createState() => _HojaPreciosState();
}

class _HojaPreciosState extends State<HojaPrecios> {
  static const _etiquetas = {
    'costo_sin_iva': 'Costo s/IVA',
    'precio_costo': 'Precio costo',
    'margen': '% Ganancia',
    'precio_final': 'Precio final',
  };

  final _controles = {for (final c in ValoresPrecio.campos) c: TextEditingController()};
  final _focos = {for (final c in ValoresPrecio.campos) c: FocusNode()};
  PreciosProducto? _p;

  /// Lo que se ve en los 4 campos, como números (null = vacío).
  ValoresPrecio _valores = const ValoresPrecio();

  /// Campo que Leo editó y todavía no se recalculó.
  String? _sinRecalcular;
  Future<bool>? _recalculando;
  bool _cargando = true;
  bool _ocupado = false;
  String? _errorCarga;

  /// 409, corte o validación: se muestra adentro de la hoja (un SnackBar
  /// quedaría tapado por ella).
  String? _aviso;

  /// Al cerrar la hoja los campos pierden el foco y avisan: no hay que
  /// recalcular nada para una hoja que ya se va.
  bool _cerrada = false;

  late final ClienteApi _api = context.read<SesionEstado>().api;

  @override
  void initState() {
    super.initState();
    for (final e in _focos.entries) {
      e.value.addListener(() {
        if (!_cerrada && !e.value.hasFocus) _recalcular(e.key);
      });
    }
    _cargar();
  }

  @override
  void deactivate() {
    _cerrada = true;
    super.deactivate();
  }

  @override
  void activate() {
    super.activate();
    _cerrada = false;
  }

  @override
  void dispose() {
    for (final c in _controles.values) {
      c.dispose();
    }
    for (final f in _focos.values) {
      f.dispose();
    }
    super.dispose();
  }

  Future<void> _cargar() async {
    if (_p != null) setState(() => _cargando = true);
    try {
      final p = await _api.precios(widget.codigo);
      if (!mounted) return;
      widget.alActualizar?.call(p);
      setState(() {
        _p = p;
        _valores = p.valores;
        _sinRecalcular = null;
        _errorCarga = null;
        for (final c in ValoresPrecio.campos) {
          _controles[c]!.text = textoPrecio(p.valores.valor(c));
        }
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _errorCarga = e.mensaje);
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  /// Pide a la PC la cadena a partir de [campo], si ese campo tiene una
  /// edición sin recalcular (enviar y perder el foco llegan los dos: se
  /// recalcula una sola vez). Los pedidos van de a uno y en orden.
  Future<bool> _recalcular(String campo) {
    if (_sinRecalcular != campo) return _recalculando ?? Future.value(true);
    _sinRecalcular = null;
    final anterior = _recalculando;
    final f = () async {
      if (anterior != null) await anterior;
      return _pedirRecalculo(campo);
    }();
    _recalculando = f;
    f.whenComplete(() {
      if (identical(_recalculando, f)) _recalculando = null;
    });
    return f;
  }

  Future<bool> _pedirRecalculo(String campo) async {
    final textos = {for (final c in ValoresPrecio.campos) c: _controles[c]!.text};
    try {
      final r = await _api.recalcularPrecios(
        cambio: campo,
        costoSinIva: textos['costo_sin_iva'],
        precioCosto: textos['precio_costo'],
        margen: textos['margen'],
        precioFinal: textos['precio_final'],
      );
      if (!mounted) return false;
      setState(() {
        for (final c in ValoresPrecio.campos) {
          // Leo ya volvió a escribir en ese campo mientras tanto: no se le pisa.
          if (_sinRecalcular == c) continue;
          final v = r.valor(c);
          // Como el Panel: el campo tocado se reescribe siempre (así se ve
          // cómo se entendió "1.500") y los demás solo si la PC les dio valor.
          if (c == campo || v != null) {
            final texto = textoPrecio(v);
            if (_controles[c]!.text.trim() != texto) _controles[c]!.text = texto;
            _valores = _valores.con(c, v);
          }
        }
        _aviso = null;
      });
      return true;
    } on ApiError catch (e) {
      if (mounted) {
        setState(() {
          _sinRecalcular ??= campo; // queda pendiente: Guardar lo vuelve a intentar
          _aviso = e.mensaje;
        });
      }
      return false;
    }
  }

  Future<void> _guardar() async {
    final p = _p;
    if (p == null || _ocupado) return;
    setState(() {
      _ocupado = true;
      _aviso = null;
    });
    try {
      // si quedó un campo editado sin recalcular, primero se recalcula
      if (_recalculando != null && !await _recalculando!) return;
      final pendiente = _sinRecalcular;
      if (pendiente != null && !await _recalcular(pendiente)) return;
      if (!mounted) return;
      final v = _valores;
      final problema = _problema(v);
      if (problema != null) {
        setState(() => _aviso = problema);
        return;
      }
      setState(() => _ocupado = false); // mientras Leo lee la confirmación no hay nada en curso
      if (!await _confirmar(p, v) || !mounted) return;
      setState(() => _ocupado = true);
      final r = await _api.guardarPrecios(p.codigo, v, esperado: p.crudos);
      widget.alActualizar?.call(r.despues);
      if (mounted) Navigator.pop(context, r);
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.codigo == 'precio_cambio' || e.incierto) {
        // 409: dice qué cambió y se recarga con lo que hay ahora. Corte: si
        // el PUT llegó, los crudos nuevos ya son los guardados y un segundo
        // guardado con los viejos daría 409, no un doble cambio.
        await _cargar();
      }
      if (mounted) setState(() => _aviso = e.mensaje);
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  static String? _problema(ValoresPrecio v) {
    final f = v.precioFinal;
    if (f == null || f <= 0) return 'Falta el precio final: es lo que cobra la Caja.';
    if (f > 1e9) return 'El precio final es demasiado grande.';
    final m = v.margen;
    if (m != null && (m <= -100 || m > 10000)) return 'El % de ganancia tiene que estar entre −100 y 10.000.';
    if ((v.costoSinIva ?? 0) < 0 || (v.precioCosto ?? 0) < 0) return 'Los costos no pueden ser negativos.';
    return null;
  }

  Future<bool> _confirmar(PreciosProducto p, ValoresPrecio v) async {
    final r = await showDialog<bool>(
      context: context,
      builder: (context) {
        final t = Theme.of(context);
        final oferta = p.oferta;
        return AlertDialog(
          title: Text(p.nombre, style: t.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800)),
          content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            _LineaCambio(
                titulo: 'Precio final',
                antes: p.precioVenta > 0 ? moneda(p.precioVenta) : 'sin precio',
                despues: moneda(v.precioFinal!)),
            const SizedBox(height: 8),
            _LineaCambio(titulo: '% Ganancia', antes: _porcentaje(p.valores.margen), despues: _porcentaje(v.margen)),
            if (oferta != null && oferta.esPrecioFijo) ...[
              const SizedBox(height: 12),
              Text('Mientras dure la oferta la Caja sigue cobrando ${moneda(oferta.valor)}.'),
            ],
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancelar')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Guardar')),
          ],
        );
      },
    );
    return r ?? false;
  }

  Widget _campo(String clave, {bool grande = false}) {
    final t = Theme.of(context);
    return TextField(
      key: Key('campo_$clave'),
      controller: _controles[clave],
      focusNode: _focos[clave],
      autofocus: clave == 'precio_final', // como el Panel: lo primero que se cambia
      keyboardType: TextInputType.numberWithOptions(decimal: true, signed: clave == 'margen'),
      textInputAction: TextInputAction.done,
      style: grande ? t.textTheme.headlineSmall : null,
      decoration: InputDecoration(
        labelText: _etiquetas[clave],
        prefixText: clave == 'margen' ? null : r'$ ',
        suffixText: clave == 'margen' ? '%' : null,
      ),
      onChanged: (_) => setState(() {
        _sinRecalcular = clave;
        _aviso = null;
      }),
      onSubmitted: (_) => _recalcular(clave),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final p = _p;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(context).viewInsets.bottom + 24),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(p?.nombre ?? widget.nombre, style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
        Text(widget.codigo, style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
        const SizedBox(height: 12),
        if (p == null && _cargando)
          const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator()))
        else if (p == null)
          ErrorConReintento(mensaje: _errorCarga ?? 'No se pudo leer el producto.', reintentar: _cargar)
        else ...[
          if (_cargando) const LinearProgressIndicator(),
          if (p.oferta != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Row(children: [
                const ChipOferta(),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('La Caja cobra hoy ${moneda(p.precioEfectivo)} por una oferta hasta el ${p.oferta!.hasta}.'),
                ),
              ]),
            ),
          Row(children: [
            Expanded(child: _campo('costo_sin_iva')),
            const SizedBox(width: 12),
            Expanded(child: _campo('precio_costo')),
          ]),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(child: _campo('margen')),
            const SizedBox(width: 12),
            Expanded(child: _campo('precio_final', grande: true)),
          ]),
          const SizedBox(height: 8),
          Text('Solo el precio final es obligatorio. Al pasar de campo, la PC recalcula los demás.',
              style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
          if (_aviso != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_aviso!, style: TextStyle(color: c.peligro, fontWeight: FontWeight.w600)),
            ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _ocupado || _cargando ? null : _guardar,
            child: _ocupado
                ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                : const Text('Guardar precio'),
          ),
        ],
      ]),
    );
  }
}

class _LineaCambio extends StatelessWidget {
  const _LineaCambio({required this.titulo, required this.antes, required this.despues});
  final String titulo;
  final String antes;
  final String despues;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(titulo, style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
      Text('$antes → $despues', style: t.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
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

  /// Los precios de la vista previa del aplicar que se cortó sin saber si
  /// llegó. Mandan sobre los de la vista previa nueva: si el primer aplicar
  /// llegó, la PC ve que ya no son esos y no toca nada (no se suma el
  /// aumento dos veces); si no llegó, coinciden y se aplica.
  Map<String, double>? _esperadosDelCorte;

  ClienteApi get _api => context.read<SesionEstado>().api;

  double? get _numero => parsearNumero(_valor.text);

  /// Problema con lo escrito (null = se puede previsualizar).
  String? get _problemaValor {
    final n = _numero;
    if (n == null || n == 0) return null;
    if (_porcentaje && (n < porcentajeMinimo || n > porcentajeMaximo)) {
      return 'El porcentaje tiene que estar entre −90 y 500.';
    }
    return null;
  }

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
    if (n == null || n == 0 || _problemaValor != null) return;
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
    final vista = _vista!;
    final n = _numero!;
    final esperados = {
      for (final c in vista.where((c) => c.ok)) c.codigo: _esperadosDelCorte?[c.codigo] ?? c.anterior!,
    };
    final descripcion = _porcentaje ? '${n > 0 ? '+' : ''}${numero(n)}%' : '${n > 0 ? '+' : '−'}${moneda(n.abs())}';
    final ok = await confirmar(context,
        titulo: '¿Aplicar $descripcion?',
        mensaje: 'Se van a cambiar ${esperados.length} precios. La caja cobra el precio nuevo desde la próxima venta.',
        si: 'Aplicar');
    if (!ok || !mounted) return;
    setState(() => _ocupado = true);
    try {
      final r = await _api.aplicarPrecios(esperados.keys.toList(),
          esperados: esperados,
          porcentaje: _porcentaje ? n : null,
          montoFijo: _porcentaje ? null : n,
          redondear: _redondear);
      if (mounted) Navigator.pop(context, resumenAjuste(r));
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.incierto) {
        // No se sabe si se aplicó: hay que volver a ver la vista previa, y el
        // próximo "Aplicar" manda los precios de ANTES del corte.
        setState(() {
          _esperadosDelCorte = {...esperados, ...?_esperadosDelCorte};
          _vista = null;
        });
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
    final problema = _problemaValor;
    final enCero = vista?.where((v) => v.ok && v.quedaEnCero).length ?? 0;
    final aAplicar = vista?.where((v) => v.ok).length ?? 0;
    return Scaffold(
      appBar: AppBar(title: Text('Ajustar ${widget.codigos.length} precios')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        if (_esperadosDelCorte != null)
          _AvisoAjuste(
              color: c.peligro,
              icono: Icons.sync_problem,
              texto: 'Se cortó la comunicación y no se sabe si el ajuste se aplicó. Mirá la vista previa de nuevo: '
                  'si ya se había aplicado, al aplicar otra vez la PC no toca nada.'),
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
            helperText: _porcentaje ? 'Entre −90 y 500' : null,
            errorText: problema,
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
            onPressed: _ocupado || _numero == null || _numero == 0 || problema != null ? null : _previsualizar,
            icon: const Icon(Icons.visibility_outlined),
            label: const Text('Ver cómo quedan'),
          )
        else ...[
          const Seccion('Vista previa'),
          Text('La vista previa muestra exactamente lo que se va a guardar (redondeo a la centena superior, '
              'igual que en la PC).', style: t.textTheme.bodySmall),
          const SizedBox(height: 8),
          Card(
            child: Column(children: [
              for (final cp in vista.take(200)) _FilaVista(cp: cp),
              if (vista.length > 200)
                Padding(padding: const EdgeInsets.all(12), child: Text('… y ${vista.length - 200} más')),
            ]),
          ),
          if (enCero > 0)
            _AvisoAjuste(
                color: c.peligro,
                icono: Icons.money_off,
                texto: '$enCero producto(s) quedarían en \$ 0 (marcados en rojo). Sacalos de la lista o cambiá el ajuste.'),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _ocupado || enCero > 0 || aAplicar == 0 ? null : _aplicar,
            icon: const Icon(Icons.check),
            label: Text('Aplicar a $aAplicar productos'),
          ),
        ],
      ]),
    );
  }
}

/// Lo que se le dice a Leo después de aplicar el ajuste.
String resumenAjuste(List<CambioPrecio> r) {
  final aplicados = r.where((c) => c.ok).length;
  final cambiados = r.where((c) => c.precioCambioMientrasTanto).length;
  final otros = r.length - aplicados - cambiados;
  if (aplicados == 0 && cambiados > 0 && otros == 0) {
    return 'Ninguno cambió: ya tenían otro precio (¿se había aplicado antes?).';
  }
  return [
    'Listo: $aplicados precios actualizados.',
    if (cambiados > 0) '$cambiados no se tocaron porque su precio cambió mientras tanto.',
    if (otros > 0) '$otros con error.',
  ].join(' ');
}

class _FilaVista extends StatelessWidget {
  const _FilaVista({required this.cp});
  final CambioPrecio cp;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final rojo = cp.ok && cp.quedaEnCero;
    return ListTile(
      dense: true,
      tileColor: rojo ? c.peligro.withValues(alpha: 0.12) : null,
      title: Text(cp.nombre ?? cp.codigo, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: !cp.ok
          ? Text(cp.error ?? 'error', style: TextStyle(color: c.peligro))
          : (rojo || cp.enOferta
              ? Wrap(spacing: 6, runSpacing: 2, crossAxisAlignment: WrapCrossAlignment.center, children: [
                  if (cp.enOferta) const ChipOferta(texto: 'en oferta'),
                  if (rojo) Text('quedaría en \$ 0', style: TextStyle(color: c.peligro)),
                ])
              : null),
      trailing: cp.ok
          ? Text.rich(TextSpan(children: [
              TextSpan(
                  text: moneda(cp.anterior!),
                  style: TextStyle(decoration: TextDecoration.lineThrough, color: t.colorScheme.onSurfaceVariant)),
              WidgetSpan(
                alignment: PlaceholderAlignment.middle,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  child: Icon(Icons.arrow_forward, size: 16, color: t.colorScheme.onSurfaceVariant),
                ),
              ),
              TextSpan(
                  text: moneda(cp.nuevo!),
                  style: TextStyle(
                      fontWeight: FontWeight.w700, color: !rojo && cp.nuevo! >= cp.anterior! ? c.ok : c.peligro)),
            ]))
          : null,
    );
  }
}

class _AvisoAjuste extends StatelessWidget {
  const _AvisoAjuste({required this.color, required this.icono, required this.texto});
  final Color color;
  final IconData icono;
  final String texto;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(top: 12, bottom: 4),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: color.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(12)),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icono, color: color),
          const SizedBox(width: 10),
          Expanded(child: Text(texto)),
        ]),
      );
}
