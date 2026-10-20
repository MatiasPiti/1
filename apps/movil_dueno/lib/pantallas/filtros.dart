import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'precios.dart' show HojaPrecios, PantallaAjusteMasivo;

/// Lo máximo que entra en un filtro: lo mismo que acepta un ajuste masivo.
const maximoProductosFiltro = 5000;

const textoPcViejaFiltros = 'La PC del local tiene una versión anterior que no maneja filtros desde el celular: '
    'actualizala con el Actualizador y probá de nuevo.';

String detalleFiltro(FiltroResumen f) {
  final productos = f.cantidad == 1 ? '1 producto' : '${numero(f.cantidad)} productos';
  return f.deCondiciones ? '$productos · armado con condiciones en el Panel' : productos;
}

String textoFaltantes(List<String> faltantes) {
  final lista = faltantes.take(5).join(', ') + (faltantes.length > 5 ? ' y ${faltantes.length - 5} más' : '');
  return faltantes.length == 1
      ? 'Un producto de este filtro ya no existe o está dado de baja ($lista): al guardar sale del filtro.'
      : '${faltantes.length} productos de este filtro ya no existen o están dados de baja ($lista): al guardar '
          'salen del filtro.';
}

bool _mismosCodigos(List<String> a, List<String> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

/// Los filtros guardados: los mismos del Panel de la PC ("Filtros / Edición
/// masiva"). Lo que se guarda acá se ve allá y al revés.
class PantallaFiltros extends StatefulWidget {
  const PantallaFiltros({super.key});

  @override
  State<PantallaFiltros> createState() => _PantallaFiltrosState();
}

class _PantallaFiltrosState extends State<PantallaFiltros> {
  List<FiltroResumen>? _filtros;
  String? _error;
  bool _cargando = false;

  ClienteApi get _api => context.read<SesionEstado>().api;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  Future<void> _cargar() async {
    setState(() => _cargando = true);
    try {
      final f = await _api.filtros();
      if (mounted) {
        setState(() {
          _filtros = f;
          _error = null;
        });
      }
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.pcVieja ? textoPcViejaFiltros : e.mensaje);
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  Future<void> _abrir({String? nombre}) async {
    final mensaje = await Navigator.of(context)
        .push<String>(MaterialPageRoute(builder: (_) => PantallaFiltro(nombre: nombre)));
    if (!mounted) return;
    if (mensaje != null) mostrarMensaje(context, mensaje);
    _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final filtros = _filtros;
    return Scaffold(
      appBar: AppBar(title: const Text('Filtros')),
      body: Column(children: [
        if (_cargando) const LinearProgressIndicator(),
        Expanded(child: _cuerpo(filtros)),
      ]),
      bottomNavigationBar: filtros == null || filtros.isEmpty || _error != null
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: FilledButton.icon(
                    onPressed: () => _abrir(), icon: const Icon(Icons.add), label: const Text('Crear filtro')),
              ),
            ),
    );
  }

  Widget _cuerpo(List<FiltroResumen>? filtros) {
    final t = Theme.of(context);
    if (_error != null) return ErrorConReintento(mensaje: _error!, reintentar: _cargar);
    if (filtros == null) return const SizedBox.shrink();
    if (filtros.isEmpty) {
      return EstadoVacio(
        icono: Icons.filter_list,
        titulo: 'Todavía no hay filtros guardados',
        detalle: 'Un filtro es una lista de productos con nombre (por ejemplo «Gaseosas» o «Promo verano») para '
            'ajustarles el precio juntos. Son los mismos del Panel de la PC.',
        accion: FilledButton.icon(onPressed: () => _abrir(), icon: const Icon(Icons.add), label: const Text('Crear filtro')),
      );
    }
    return RefreshIndicator(
      onRefresh: _cargar,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          Text('Los mismos filtros del Panel de la PC: lo que cambies acá se ve allá.',
              style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
          const SizedBox(height: 12),
          Card(
            child: Column(children: [
              for (final f in filtros)
                ListTile(
                  leading: Icon(f.deCondiciones ? Icons.rule : Icons.filter_alt_outlined, color: t.colorScheme.primary),
                  title: Text(f.nombre),
                  subtitle: Text(detalleFiltro(f)),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _abrir(nombre: f.nombre),
                ),
            ]),
          ),
        ],
      ),
    );
  }
}

/// Qué ofrece el aviso de un error al guardar.
enum _Arreglo { recargar, guardarComoNuevo }

/// Arma o cambia un filtro: los productos se agregan buscándolos o
/// escaneándolos, igual que la grilla de "Filtros" del Panel. Desde acá
/// también se ajustan los precios de todos juntos.
class PantallaFiltro extends StatefulWidget {
  const PantallaFiltro({super.key, this.nombre, this.productosIniciales = const []});

  /// null = filtro nuevo.
  final String? nombre;

  /// "Guardar como filtro" desde Precios: arranca con lo que estaba seleccionado.
  final List<Producto> productosIniciales;

  @override
  State<PantallaFiltro> createState() => _PantallaFiltroState();
}

class _PantallaFiltroState extends State<PantallaFiltro> {
  final _nombre = TextEditingController();
  final _busqueda = TextEditingController();
  late final List<Producto> _productos = [...widget.productosIniciales];
  List<Producto> _resultados = [];

  /// Lo buscado que no trajo nada.
  String? _sinResultados;

  /// El nombre con el que está guardado (null = nuevo, todavía sin guardar).
  late String? _guardado = widget.nombre;
  String? _huella;
  bool _deCondiciones = false;
  List<String> _faltantes = const [];
  bool _recortado = false;

  /// Hay cambios sin guardar (también un filtro nuevo que arrancó con productos).
  late bool _cambios = widget.nombre == null && widget.productosIniciales.isNotEmpty;
  bool _intentado = false;
  bool _cargando = false;
  bool _buscando = false;
  bool _ocupado = false;
  bool _cerrando = false;
  String? _errorCarga;
  String? _aviso;
  _Arreglo? _arreglo;

  /// Un guardado anterior se cortó: si ahora la PC contesta que ya existe o
  /// que cambió, se mira si lo que quedó guardado es justo esto.
  bool _incierto = false;

  ClienteApi get _api => context.read<SesionEstado>().api;
  bool get _esNuevo => _guardado == null;

  @override
  void initState() {
    super.initState();
    if (widget.nombre != null) _cargar();
  }

  @override
  void dispose() {
    _nombre.dispose();
    _busqueda.dispose();
    super.dispose();
  }

  String? get _problemaNombre {
    final t = _nombre.text.trim();
    if (t.isEmpty) return 'Ponele un nombre al filtro.';
    if (t.length > 80) return 'El nombre puede tener hasta 80 letras.';
    return null;
  }

  Future<void> _cargar() async {
    final nombre = _guardado;
    if (nombre == null) return;
    setState(() {
      _cargando = true;
      _errorCarga = null;
    });
    try {
      final f = await _api.filtro(nombre);
      if (!mounted) return;
      setState(() {
        _productos
          ..clear()
          ..addAll(f.productos);
        _huella = f.huella;
        _deCondiciones = f.deCondiciones;
        _faltantes = f.faltantes;
        _recortado = f.recortado;
        _cambios = false;
        _aviso = null;
        _arreglo = null;
        _incierto = false;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _errorCarga = e.pcVieja ? textoPcViejaFiltros : e.mensaje);
    } finally {
      if (mounted) setState(() => _cargando = false);
    }
  }

  bool _contiene(String codigo) => _productos.any((p) => p.codigo == codigo);

  void _agregar(Producto p) {
    if (_contiene(p.codigo)) return;
    if (_productos.length >= maximoProductosFiltro) {
      mostrarMensaje(context, 'Un filtro puede tener hasta ${numero(maximoProductosFiltro)} productos.', error: true);
      return;
    }
    setState(() {
      _productos.add(p);
      _cambios = true;
    });
  }

  void _agregarResultados() {
    for (final p in _resultados) {
      _agregar(p);
    }
    setState(() => _resultados = []);
    _busqueda.clear();
  }

  void _sacar(Producto p) => setState(() {
        _productos.removeWhere((x) => x.codigo == p.codigo);
        _cambios = true;
      });

  Future<void> _buscar(String texto) async {
    final t = texto.trim();
    if (t.isEmpty) {
      setState(() {
        _resultados = [];
        _sinResultados = null;
      });
      return;
    }
    setState(() => _buscando = true);
    try {
      final r = await _api.productos(q: t);
      if (!mounted) return;
      // un código completo (tipeado o del lector) se agrega directo
      if (r.length == 1 && r.first.codigo == t) {
        _agregar(r.first);
        _busqueda.clear();
        setState(() {
          _resultados = [];
          _sinResultados = null;
        });
        return;
      }
      setState(() {
        _resultados = r;
        _sinResultados = r.isEmpty ? t : null;
      });
    } on ApiError catch (e) {
      if (mounted) mostrarMensaje(context, e.mensaje, error: true);
    } finally {
      if (mounted) setState(() => _buscando = false);
    }
  }

  Future<void> _agregarEscaneado(String codigo) async {
    _busqueda.clear();
    try {
      final p = await _api.producto(codigo);
      if (!mounted) return;
      final yaEstaba = _contiene(p.codigo);
      _agregar(p);
      mostrarMensaje(context, yaEstaba ? '${p.nombre} ya estaba en el filtro.' : '${p.nombre}: agregado.');
    } on ApiError catch (e) {
      if (!mounted) return;
      mostrarMensaje(context, e.codigo == 'no_existe' ? 'No hay ningún producto con el código $codigo.' : e.mensaje,
          error: true);
    }
  }

  Future<void> _guardar() async {
    if (_esNuevo && _problemaNombre != null) {
      setState(() => _intentado = true);
      return;
    }
    if (_productos.isEmpty) {
      setState(() {
        _aviso = 'Agregá al menos un producto al filtro.';
        _arreglo = null;
      });
      return;
    }
    final nombre = _esNuevo ? _nombre.text.trim() : _guardado!;
    final codigos = [for (final p in _productos) p.codigo];
    setState(() {
      _ocupado = true;
      _aviso = null;
      _arreglo = null;
    });
    try {
      final r = _esNuevo
          ? await _api.crearFiltro(nombre, codigos)
          : await _api.guardarFiltro(nombre, codigos, huella: _huella!);
      if (mounted) _guardadoOk(r);
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.incierto || (_incierto && (e.codigo == 'filtro_existe' || e.codigo == 'filtro_cambio'))) {
        _incierto = true;
        await _revisarTrasCorte(nombre, codigos);
        return;
      }
      setState(() {
        switch (e.codigo) {
          case 'filtro_cambio':
            _aviso = '${e.mensaje} Lo que cambiaste acá no se guardó.';
            _arreglo = _Arreglo.recargar;
          case 'filtro_no_existe':
            _aviso = '${e.mensaje} Podés guardarlo como un filtro nuevo.';
            _arreglo = _Arreglo.guardarComoNuevo;
          default:
            _aviso = e.pcVieja ? textoPcViejaFiltros : e.mensaje;
            _arreglo = null;
        }
      });
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  void _guardadoOk(FiltroResumen r) {
    final eraNuevo = _esNuevo;
    setState(() {
      _guardado = r.nombre;
      _huella = r.huella;
      _cambios = false;
      _deCondiciones = false;
      _faltantes = const [];
      _incierto = false;
      _aviso = null;
      _arreglo = null;
    });
    mostrarMensaje(context, eraNuevo ? 'Filtro «${r.nombre}» guardado.' : 'Cambios guardados.');
  }

  /// Se cortó: no se sabe si se guardó. Se mira cómo quedó en la PC.
  Future<void> _revisarTrasCorte(String nombre, List<String> codigos) async {
    try {
      final f = await _api.filtro(nombre);
      if (!mounted) return;
      if (_mismosCodigos([for (final p in f.productos) p.codigo], codigos)) {
        _guardadoOk(f);
        return;
      }
      setState(() {
        _aviso = 'Se cortó la comunicación y el filtro no quedó guardado como lo dejaste. Probá de nuevo.';
        _arreglo = null;
      });
    } on ApiError catch (e) {
      if (!mounted) return;
      setState(() {
        _arreglo = null;
        if (e.codigo == 'filtro_no_existe' && _esNuevo) {
          _aviso = 'Se cortó la comunicación y el filtro no se guardó. Probá de nuevo.';
        } else if (e.codigo == 'filtro_no_existe') {
          _aviso = '${e.mensaje} Podés guardarlo como un filtro nuevo.';
          _arreglo = _Arreglo.guardarComoNuevo;
        } else {
          _aviso = 'Se cortó la comunicación y no se sabe si se guardó. Tocá «Guardar» de nuevo cuando vuelva la '
              'señal: si ya se había guardado, te lo dice.';
        }
      });
    }
  }

  Future<void> _guardarComoNuevo() async {
    setState(() {
      _nombre.text = _guardado ?? _nombre.text;
      _guardado = null;
      _huella = null;
      _cambios = true;
    });
    await _guardar();
  }

  Future<void> _borrar() async {
    final nombre = _guardado!;
    final ok = await confirmar(context,
        titulo: '¿Borrar «$nombre»?',
        mensaje: 'Se borra también del Panel de la PC. Los productos y sus precios no se tocan.',
        si: 'Borrar');
    if (!ok || !mounted) return;
    setState(() {
      _ocupado = true;
      _aviso = null;
      _arreglo = null;
    });
    try {
      await _api.borrarFiltro(nombre, huella: _huella!);
      if (mounted) _cerrar('Filtro «$nombre» borrado.');
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.codigo == 'filtro_no_existe') {
        _cerrar('El filtro «$nombre» ya no estaba: lo habían borrado desde otro lado.');
      } else if (e.incierto) {
        await _revisarBorrado(nombre);
      } else {
        setState(() {
          _aviso = e.pcVieja ? textoPcViejaFiltros : e.mensaje;
          _arreglo = e.codigo == 'filtro_cambio' ? _Arreglo.recargar : null;
        });
      }
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _revisarBorrado(String nombre) async {
    try {
      await _api.filtro(nombre);
      if (mounted) setState(() => _aviso = 'Se cortó la comunicación y el filtro no se borró. Probá de nuevo.');
    } on ApiError catch (e) {
      if (!mounted) return;
      if (e.codigo == 'filtro_no_existe') {
        _cerrar('Filtro «$nombre» borrado.');
      } else {
        setState(() => _aviso = 'Se cortó la comunicación y no se sabe si se borró. Probá de nuevo cuando vuelva la señal.');
      }
    }
  }

  void _cerrar(String? mensaje) {
    setState(() => _cerrando = true);
    Navigator.pop(context, mensaje);
  }

  Future<void> _preguntarSalir() async {
    final salir = await confirmar(context,
        titulo: '¿Salir sin guardar?', mensaje: 'Los cambios de este filtro se pierden.', si: 'Salir');
    if (salir && mounted) _cerrar(null);
  }

  Future<void> _ajustarPrecios() async {
    final resumen = await Navigator.of(context).push<String>(MaterialPageRoute(
        builder: (_) => PantallaAjusteMasivo(codigos: [for (final p in _productos) p.codigo])));
    if (resumen != null && mounted) mostrarMensaje(context, resumen);
  }

  Future<void> _editarPrecio(Producto p) async {
    final cambio = await showModalBottomSheet<CambioPrecios>(
      context: context,
      isScrollControlled: true,
      builder: (_) => HojaPrecios(codigo: p.codigo, nombre: p.nombre),
    );
    if (cambio == null || !mounted) return;
    mostrarMensaje(context,
        '${cambio.despues.nombre}: ${moneda(cambio.antes.precioVenta)} → ${moneda(cambio.despues.precioVenta)}');
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: _cerrando || !_cambios,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _preguntarSalir();
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(_guardado ?? 'Filtro nuevo'),
          actions: [
            if (!_esNuevo)
              IconButton(
                key: const Key('borrar_filtro'),
                tooltip: 'Borrar filtro',
                icon: const Icon(Icons.delete_outline),
                onPressed: _ocupado || _cargando || _huella == null ? null : _borrar,
              ),
          ],
        ),
        body: _cuerpo(),
        bottomNavigationBar: _barraInferior(),
      ),
    );
  }

  Widget _cuerpo() {
    if (_errorCarga != null) return ErrorConReintento(mensaje: _errorCarga!, reintentar: _cargar);
    if (!_esNuevo && _huella == null) return const Center(child: CircularProgressIndicator());
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final enFiltro = {for (final p in _productos) p.codigo};
    final faltanAgregar = _resultados.where((p) => !enFiltro.contains(p.codigo)).length;
    Widget conMargen(Widget w) => Padding(padding: const EdgeInsets.only(bottom: 8), child: w);
    final encabezado = <Widget>[
      if (_cargando) const LinearProgressIndicator(),
      if (_esNuevo) ...[
        TextField(
          key: const Key('nombre_filtro'),
          controller: _nombre,
          autofocus: widget.productosIniciales.isNotEmpty,
          textCapitalization: TextCapitalization.sentences,
          inputFormatters: [LengthLimitingTextInputFormatter(80)],
          decoration: InputDecoration(
            labelText: 'Nombre del filtro',
            hintText: 'Ej.: Gaseosas, Promo verano',
            errorText: _intentado ? _problemaNombre : null,
          ),
          onChanged: (_) => setState(() {}),
        ),
        const SizedBox(height: 8),
      ],
      if (_deCondiciones)
        conMargen(Aviso(
          color: c.aviso,
          icono: Icons.rule,
          texto: 'Este filtro se armó en el Panel con condiciones (marca, categoría…): muestra los productos que las '
              'cumplen hoy. Si lo cambiás desde acá, queda como una lista fija con los que elijas.',
        )),
      if (_faltantes.isNotEmpty)
        conMargen(Aviso(color: c.aviso, icono: Icons.inventory_2_outlined, texto: textoFaltantes(_faltantes))),
      if (_recortado)
        conMargen(Aviso(
          color: c.aviso,
          icono: Icons.content_cut,
          texto: 'Este filtro tiene más de ${numero(maximoProductosFiltro)} productos (se armó en el Panel): acá se '
              'ven los primeros y no se puede guardar desde el celular. Cambialo desde la PC.',
        )),
      if (_aviso != null)
        conMargen(Aviso(
          key: const Key('aviso_filtro'),
          color: c.peligro,
          icono: Icons.error_outline,
          texto: _aviso!,
          accion: switch (_arreglo) {
            _Arreglo.recargar => TextButton(onPressed: _ocupado ? null : _cargar, child: const Text('Volver a cargar')),
            _Arreglo.guardarComoNuevo =>
              TextButton(onPressed: _ocupado ? null : _guardarComoNuevo, child: const Text('Guardar como filtro nuevo')),
            null => null,
          },
        )),
      const Seccion('Agregar productos'),
      CampoCodigo(controller: _busqueda, alEnviar: _buscar, alEscanear: _agregarEscaneado),
      if (_buscando) const LinearProgressIndicator(),
      if (_sinResultados != null)
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 12, 4, 0),
          child: Text('No hay productos con «$_sinResultados».',
              style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
        ),
      if (_resultados.isNotEmpty) ...[
        Seccion(
          _resultados.length == 1 ? '1 resultado' : '${_resultados.length} resultados',
          trailing: faltanAgregar > 1
              ? TextButton(onPressed: _agregarResultados, child: Text('Agregar los $faltanAgregar'))
              : null,
        ),
        Card(
          child: Column(children: [
            for (final r in _resultados.take(50))
              ListTile(
                title: Text(r.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text([r.codigo, if (r.marca != null) r.marca!].join(' · '), maxLines: 1),
                trailing: enFiltro.contains(r.codigo)
                    ? Icon(Icons.check_circle, color: c.ok)
                    : Icon(Icons.add_circle_outline, color: t.colorScheme.primary),
                onTap: enFiltro.contains(r.codigo) ? null : () => _agregar(r),
              ),
          ]),
        ),
        if (_resultados.length > 50)
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 8, 4, 0),
            child: Text('… y ${_resultados.length - 50} más: refiná la búsqueda o agregalos todos juntos.',
                style: t.textTheme.bodySmall),
          ),
      ],
      Seccion(_productos.isEmpty ? 'En este filtro' : 'En este filtro (${numero(_productos.length)})'),
      if (_productos.isEmpty)
        const Card(
          child: Padding(
            padding: EdgeInsets.all(20),
            child: Text('Todavía no tiene productos: buscalos o escanealos arriba.'),
          ),
        ),
    ];
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      itemCount: encabezado.length + _productos.length,
      itemBuilder: (context, i) {
        if (i < encabezado.length) return encabezado[i];
        final p = _productos[i - encabezado.length];
        return ListTile(
          key: ValueKey('en_filtro_${p.codigo}'),
          contentPadding: const EdgeInsets.only(left: 4),
          title: Text(p.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
          subtitle: Text([p.codigo, if (p.marca != null) p.marca!].join(' · '), maxLines: 1),
          trailing: IconButton(
            tooltip: 'Sacar del filtro',
            icon: Icon(Icons.remove_circle_outline, color: c.peligro),
            onPressed: _ocupado ? null : () => _sacar(p),
          ),
          onTap: () => _editarPrecio(p),
        );
      },
    );
  }

  Widget? _barraInferior() {
    if (_errorCarga != null || (!_esNuevo && _huella == null)) return null;
    final n = _productos.length;
    final textoAjustar = n == 1 ? 'Ajustar el precio' : 'Ajustar los ${numero(n)} precios';
    final ajustar = n == 0 || _ocupado ? null : _ajustarPrecios;
    final guardar = _ocupado || _recortado ? null : _guardar;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (_cambios || _esNuevo) ...[
            FilledButton.icon(
              key: const Key('guardar_filtro'),
              onPressed: guardar,
              icon: _ocupado
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2.5))
                  : const Icon(Icons.save_outlined),
              label: Text(_esNuevo ? 'Guardar filtro' : 'Guardar cambios'),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(onPressed: ajustar, icon: const Icon(Icons.trending_up), label: Text(textoAjustar)),
          ] else
            FilledButton.icon(onPressed: ajustar, icon: const Icon(Icons.trending_up), label: Text(textoAjustar)),
        ]),
      ),
    );
  }
}
