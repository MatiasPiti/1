import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';

/// Lo que está mal en el código de un producto nuevo (null = está bien). Las
/// mismas reglas que la PC: sin espacios, hasta 64 caracteres, y nunca el "1".
String? problemaCodigo(String texto) {
  final t = texto.trim();
  if (t.isEmpty) return 'Escaneá o escribí el código.';
  if (RegExp(r'\s').hasMatch(t)) return 'El código no puede tener espacios.';
  if (t.length > 64) return 'El código puede tener hasta 64 caracteres.';
  if (t == codigoSinBarra) {
    return 'El código 1 lo usa la Caja para los artículos sin código de barras: elegí otro.';
  }
  return null;
}

String? problemaNombre(String texto) {
  final t = texto.trim();
  if (t.isEmpty) return 'Poné el nombre como lo querés ver en la Caja.';
  if (t.length > 120) return 'El nombre puede tener hasta 120 letras.';
  return null;
}

/// "2.500" = dos mil quinientos y "2.500,50" con centavos, como en Precios.
({double? valor, String? problema}) leerPrecio(String texto) {
  if (texto.trim().isEmpty) return (valor: null, problema: 'Poné el precio de venta.');
  final v = parsearNumero(texto);
  if (v == null) return (valor: null, problema: 'Escribí el precio con números (ej.: 2.500 o 2.500,50).');
  if (v < 0) return (valor: null, problema: 'El precio no puede ser negativo.');
  if (v > 1e9) return (valor: null, problema: 'Ese precio es demasiado grande.');
  return (valor: (v * 100).round() / 100, problema: null);
}

/// Vacío = 0 (como el Panel).
({int? valor, String? problema}) leerStock(String texto) {
  final t = texto.trim();
  if (t.isEmpty) return (valor: 0, problema: null);
  final v = int.tryParse(t);
  if (v == null || v < 0) return (valor: null, problema: 'El stock va en unidades enteras.');
  if (v > 100000) return (valor: null, problema: 'Hasta 100.000 unidades.');
  return (valor: v, problema: null);
}

/// Lo que Leo buscó y no estaba: si parece un código (sin espacios y con
/// algún número) va al código; si no, al nombre.
({String codigo, String nombre}) datosDeBusqueda(String texto) {
  final t = texto.trim();
  if (t == codigoSinBarra) return (codigo: '', nombre: '');
  final esCodigo = problemaCodigo(t) == null && RegExp(r'\d').hasMatch(t);
  return esCodigo ? (codigo: t, nombre: '') : (codigo: '', nombre: t);
}

/// Lo que devuelve la pantalla de alta: el producto nuevo o, si Leo tocó
/// "Ver producto", el que ya tenía ese código.
class AltaProducto {
  const AltaProducto(this.producto, {required this.nuevo});
  final Producto producto;
  final bool nuevo;
}

Future<AltaProducto?> abrirAltaProducto(BuildContext context, {String codigo = '', String nombre = ''}) =>
    Navigator.of(context).push<AltaProducto>(
        MaterialPageRoute(builder: (_) => PantallaProductoNuevo(codigo: codigo, nombre: nombre)));

/// Alta de un producto que no estaba en el sistema: los mismos campos que el
/// alta del Panel de la PC. Queda en la Caja apenas se guarda.
class PantallaProductoNuevo extends StatefulWidget {
  const PantallaProductoNuevo({super.key, this.codigo = '', this.nombre = ''});
  final String codigo;
  final String nombre;

  @override
  State<PantallaProductoNuevo> createState() => _PantallaProductoNuevoState();
}

class _PantallaProductoNuevoState extends State<PantallaProductoNuevo> {
  late final _codigo = TextEditingController(text: widget.codigo);
  late final _nombre = TextEditingController(text: widget.nombre);
  final _precio = TextEditingController();
  final _stock = TextEditingController(text: '0');
  final _marca = TextEditingController();
  final _proveedor = TextEditingController();
  final _categoria = TextEditingController();
  final _focoNombre = FocusNode();
  final _focoPrecio = FocusNode();
  final _focoStock = FocusNode();

  /// Los errores de cada campo se muestran recién después del primer intento.
  bool _intentado = false;
  bool _guardando = false;
  bool _cerrando = false;

  /// Lo que contestó la PC y no deja guardar (código repetido, PC vieja…).
  String? _aviso;

  /// El producto que ya tiene ese código, para ofrecer "Ver producto".
  Producto? _existente;

  /// Hubo un corte en un intento anterior: si ahora la PC dice que el código
  /// ya existe con estos mismos datos, es que aquel intento sí llegó.
  bool _incierto = false;

  /// Sugerencias ya pedidas, por "campo|texto": cada letra no viaja dos veces.
  final _sugerencias = <String, List<String>>{};

  ClienteApi get _api => context.read<SesionEstado>().api;

  List<TextEditingController> get _campos => [_codigo, _nombre, _precio, _stock, _marca, _proveedor, _categoria];

  @override
  void initState() {
    super.initState();
    // Cada cambio redibuja: los errores se van apenas se corrigen y el
    // "¿Descartar?" al salir sabe si hay algo escrito (también el código
    // que trajo la cámara).
    for (final c in _campos) {
      c.addListener(_redibujar);
    }
  }

  void _redibujar() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    for (final c in _campos) {
      c.dispose();
    }
    for (final f in [_focoNombre, _focoPrecio, _focoStock]) {
      f.dispose();
    }
    super.dispose();
  }

  bool get _hayCambios =>
      _codigo.text.trim() != widget.codigo.trim() ||
      _nombre.text.trim() != widget.nombre.trim() ||
      _precio.text.trim().isNotEmpty ||
      !['', '0'].contains(_stock.text.trim()) ||
      [_marca, _proveedor, _categoria].any((c) => c.text.trim().isNotEmpty);

  Future<List<String>> _sugerir(String campo, String texto) async {
    final clave = '$campo|${texto.toLowerCase()}';
    final ya = _sugerencias[clave];
    if (ya != null) return ya;
    try {
      final r = await _api.valoresCatalogo(campo, q: texto);
      _sugerencias[clave] = r;
      return r;
    } on ApiError {
      return const []; // son una ayuda: sin señal se escribe a mano
    }
  }

  void _listo(Producto p, {required bool nuevo}) {
    setState(() => _cerrando = true);
    Navigator.pop(context, AltaProducto(p, nuevo: nuevo));
  }

  Future<void> _guardar() async {
    FocusScope.of(context).unfocus();
    setState(() {
      _intentado = true;
      _aviso = null;
      _existente = null;
    });
    final precio = leerPrecio(_precio.text);
    final stock = leerStock(_stock.text);
    if (problemaCodigo(_codigo.text) != null ||
        problemaNombre(_nombre.text) != null ||
        precio.problema != null ||
        stock.problema != null) {
      return;
    }
    final nuevo = ProductoNuevo(
      codigo: _codigo.text,
      nombre: _nombre.text,
      precioVenta: precio.valor!,
      stockInicial: stock.valor!,
      marca: _marca.text,
      proveedor: _proveedor.text,
      categoria: _categoria.text,
    );
    if (nuevo.precioVenta == 0) {
      final seguir = await confirmar(context,
          titulo: r'¿Precio $ 0?',
          mensaje: r'La Caja lo va a cobrar $ 0 hasta que le pongas precio en Precios.',
          si: 'Dar de alta igual');
      if (!seguir || !mounted) return;
    }
    setState(() => _guardando = true);
    try {
      final p = await _api.crearProducto(nuevo);
      if (mounted) _listo(p, nuevo: true);
    } on ApiError catch (e) {
      if (!mounted) return;
      final existente = e.producto;
      if (e.codigo == 'producto_existe' && existente != null && _incierto && nuevo.esElMismo(existente)) {
        _listo(existente, nuevo: true);
      } else if (e.codigo == 'producto_existe') {
        setState(() {
          _existente = existente;
          _aviso = e.mensaje;
        });
      } else if (e.incierto) {
        _incierto = true;
        await _revisarTrasCorte(nuevo);
      } else {
        setState(() => _aviso = e.pcVieja
            ? 'La PC del local tiene una versión anterior que no da de alta productos desde el celular: '
                'actualizala con el Actualizador y probá de nuevo.'
            : e.mensaje);
      }
    } finally {
      if (mounted) setState(() => _guardando = false);
    }
  }

  /// Se cortó: no se sabe si el alta llegó. Se pregunta por el código (dar de
  /// alta dos veces es imposible, así que repetir nunca duplica nada).
  Future<void> _revisarTrasCorte(ProductoNuevo nuevo) async {
    try {
      final p = await _api.producto(nuevo.codigo.trim());
      if (!mounted) return;
      if (nuevo.esElMismo(p)) {
        _listo(p, nuevo: true);
        return;
      }
      setState(() {
        _existente = p;
        _aviso = 'Ya hay un producto con el código ${p.codigo}: «${p.nombre}».';
      });
    } on ApiError catch (e) {
      if (!mounted) return;
      setState(() => _aviso = e.codigo == 'no_existe'
          ? 'Se cortó la comunicación y el producto no quedó cargado. Probá de nuevo.'
          : 'Se cortó la comunicación y no se sabe si el producto quedó cargado. Tocá «Dar de alta» de nuevo '
              'cuando vuelva la señal: si ya estaba, te lo dice (nunca se carga dos veces).');
    }
  }

  Future<void> _preguntarSalir() async {
    final salir = await confirmar(context,
        titulo: '¿Descartar el producto?', mensaje: 'Lo que escribiste no se guarda.', si: 'Descartar');
    if (!salir || !mounted) return;
    setState(() => _cerrando = true);
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = ColoresEstado.de(context);
    final precio = leerPrecio(_precio.text);
    final stock = leerStock(_stock.text);
    final existente = _existente;
    return PopScope(
      canPop: _cerrando || !_hayCambios,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _preguntarSalir();
      },
      child: Scaffold(
        appBar: AppBar(title: const Text('Producto nuevo')),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            Text('Queda en la Caja apenas lo guardás. El costo y el % de ganancia se cargan después, en Precios.',
                style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
            const SizedBox(height: 16),
            CampoCodigo(
              controller: _codigo,
              etiqueta: 'Código de barras',
              icono: Icons.qr_code_2,
              accion: TextInputAction.next,
              autofocus: widget.codigo.isEmpty && widget.nombre.isEmpty,
              error: _intentado ? problemaCodigo(_codigo.text) : null,
              alEnviar: (_) => _focoNombre.requestFocus(),
              alEscanear: (_) => _focoNombre.requestFocus(),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('alta_nombre'),
              controller: _nombre,
              focusNode: _focoNombre,
              autofocus: widget.codigo.isNotEmpty && widget.nombre.isEmpty,
              textCapitalization: TextCapitalization.sentences,
              textInputAction: TextInputAction.next,
              inputFormatters: [LengthLimitingTextInputFormatter(120)],
              decoration: InputDecoration(
                  labelText: 'Nombre', errorText: _intentado ? problemaNombre(_nombre.text) : null),
              onSubmitted: (_) => _focoPrecio.requestFocus(),
            ),
            const SizedBox(height: 12),
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(
                flex: 3,
                child: TextField(
                  key: const Key('alta_precio'),
                  controller: _precio,
                  focusNode: _focoPrecio,
                  keyboardType: const TextInputType.numberWithOptions(decimal: true),
                  textInputAction: TextInputAction.next,
                  decoration: InputDecoration(
                    labelText: 'Precio de venta',
                    prefixText: r'$ ',
                    errorText: _intentado ? precio.problema : null,
                    errorMaxLines: 3,
                  ),
                  onSubmitted: (_) => _focoStock.requestFocus(),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                flex: 2,
                child: TextField(
                  key: const Key('alta_stock'),
                  controller: _stock,
                  focusNode: _focoStock,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(6)],
                  decoration: InputDecoration(
                    labelText: 'Stock inicial',
                    suffixText: 'u.',
                    errorText: _intentado ? stock.problema : null,
                    errorMaxLines: 3,
                  ),
                ),
              ),
            ]),
            const Seccion('Opcional'),
            _CampoSugerido(controller: _marca, campo: 'marca', etiqueta: 'Marca', sugerir: _sugerir),
            const SizedBox(height: 12),
            _CampoSugerido(controller: _proveedor, campo: 'proveedor', etiqueta: 'Proveedor', sugerir: _sugerir),
            const SizedBox(height: 12),
            _CampoSugerido(controller: _categoria, campo: 'categoria', etiqueta: 'Categoría', sugerir: _sugerir),
            if (_aviso != null) ...[
              const SizedBox(height: 16),
              Aviso(
                key: const Key('aviso_alta'),
                texto: _aviso!,
                color: existente != null ? c.aviso : c.peligro,
                icono: existente != null ? Icons.inventory_2_outlined : Icons.error_outline,
                accion: existente == null
                    ? null
                    : TextButton(
                        onPressed: () => _listo(existente, nuevo: false), child: const Text('Ver producto')),
              ),
            ],
          ],
        ),
        bottomNavigationBar: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
            child: FilledButton.icon(
              key: const Key('boton_dar_de_alta'),
              onPressed: _guardando ? null : _guardar,
              icon: _guardando
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2.5))
                  : const Icon(Icons.add_box_outlined),
              label: const Text('Dar de alta'),
            ),
          ),
        ),
      ),
    );
  }
}

/// Campo de texto que sugiere lo que ya se usa (marcas, proveedores, rubros),
/// así no aparecen "Coca Cola" y "CocaCola" como dos marcas en los filtros.
/// Enter NO elige la sugerencia: pasa al campo siguiente.
class _CampoSugerido extends StatefulWidget {
  const _CampoSugerido({required this.controller, required this.campo, required this.etiqueta, required this.sugerir});
  final TextEditingController controller;
  final String campo;
  final String etiqueta;
  final Future<List<String>> Function(String campo, String texto) sugerir;

  @override
  State<_CampoSugerido> createState() => _CampoSugeridoState();
}

class _CampoSugeridoState extends State<_CampoSugerido> {
  final _foco = FocusNode();

  @override
  void dispose() {
    _foco.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, medidas) => RawAutocomplete<String>(
        textEditingController: widget.controller,
        focusNode: _foco,
        optionsBuilder: (valor) async {
          final texto = valor.text.trim();
          if (texto.isEmpty) return const <String>[];
          final opciones = await widget.sugerir(widget.campo, texto);
          return opciones.where((o) => o != texto);
        },
        fieldViewBuilder: (context, controller, foco, _) => TextField(
          key: Key('alta_${widget.campo}'),
          controller: controller,
          focusNode: foco,
          textCapitalization: TextCapitalization.sentences,
          textInputAction: TextInputAction.next,
          inputFormatters: [LengthLimitingTextInputFormatter(80)],
          decoration: InputDecoration(labelText: widget.etiqueta),
          onSubmitted: (_) => FocusScope.of(context).nextFocus(),
        ),
        optionsViewBuilder: (context, elegir, opciones) => Align(
          alignment: Alignment.topLeft,
          child: Material(
            elevation: 4,
            borderRadius: BorderRadius.circular(14),
            clipBehavior: Clip.antiAlias,
            child: ConstrainedBox(
              constraints: BoxConstraints(maxHeight: 240, maxWidth: medidas.maxWidth),
              child: ListView(
                padding: EdgeInsets.zero,
                shrinkWrap: true,
                children: [
                  for (final o in opciones)
                    ListTile(dense: true, leading: const Icon(Icons.history, size: 18), title: Text(o), onTap: () => elegir(o)),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
