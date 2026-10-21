import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';
import 'stock.dart' show avisoVentasPendientesCorto;

class PantallaAlertas extends StatefulWidget {
  const PantallaAlertas({super.key, required this.activa});
  final bool activa;

  @override
  State<PantallaAlertas> createState() => _PantallaAlertasState();
}

class _PantallaAlertasState extends State<PantallaAlertas> {
  List<Alerta>? _alertas;
  ConfigAlertas? _config;
  String? _error;

  ClienteApi get _api => context.read<SesionEstado>().api;

  @override
  void didUpdateWidget(PantallaAlertas old) {
    super.didUpdateWidget(old);
    if (widget.activa && !old.activa) _cargar();
  }

  Future<void> _cargar() async {
    try {
      final a = await _api.alertas();
      final c = await _api.configAlertas();
      if (!mounted) return;
      context.read<AlertasEstado>().actualizar(a.length);
      setState(() {
        _alertas = a;
        _config = c;
        _error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    }
  }

  Future<void> _reponer(Alerta a) async {
    final repuesto = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _HojaReponer(alerta: a, api: _api),
    );
    if (repuesto == true) _cargar();
  }

  Future<void> _abrirConfig() async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => const PantallaConfigAlertas()));
    _cargar();
  }

  @override
  Widget build(BuildContext context) {
    final alertas = _alertas;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Alertas'),
        actions: [
          IconButton(tooltip: 'Configurar alertas', icon: const Icon(Icons.tune), onPressed: _abrirConfig),
          const BotonAjustes(),
        ],
      ),
      body: alertas == null
          ? (_error != null
              ? ErrorConReintento(mensaje: _error!, reintentar: _cargar)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(onRefresh: _cargar, child: _lista(alertas)),
    );
  }

  Widget _lista(List<Alerta> alertas) {
    final bajos = alertas.where((a) => a.esBajo).toList();
    final sobre = alertas.where((a) => !a.esBajo).toList();
    final apagado = _config != null && !_config!.telegramHabilitado;
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 24), children: [
      if (_config != null) _EstadoTelegram(config: _config!, alTocar: _abrirConfig),
      if (apagado && alertas.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text('El bot está apagado: estas alertas no se mandan por Telegram.',
              style: TextStyle(color: ColoresEstado.de(context).aviso, fontWeight: FontWeight.w600)),
        ),
      if (alertas.isEmpty)
        const Padding(
          padding: EdgeInsets.only(top: 48),
          child: EstadoVacio(
            icono: Icons.verified_outlined,
            titulo: 'Todo en orden',
            detalle: 'Ningún producto está por debajo del mínimo ni por encima del máximo.',
          ),
        ),
      if (bajos.isNotEmpty) ...[
        Seccion('Stock bajo (${bajos.length})'),
        Card(child: Column(children: [for (final a in bajos) _FilaAlerta(alerta: a, alTocar: () => _reponer(a))])),
      ],
      if (sobre.isNotEmpty) ...[
        Seccion('Sobre-stock (${sobre.length})'),
        Card(child: Column(children: [for (final a in sobre) _FilaAlerta(alerta: a, alTocar: () => _reponer(a))])),
      ],
    ]);
  }
}

class _EstadoTelegram extends StatelessWidget {
  const _EstadoTelegram({required this.config, required this.alTocar});
  final ConfigAlertas config;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    final cargado = config.tokenConfigurado && config.chatId.isNotEmpty;
    final activo = config.telegramHabilitado && cargado;
    return Card(
      child: ListTile(
        onTap: alTocar,
        leading: Icon(Icons.send_rounded, color: activo ? c.ok : c.aviso),
        title: Text(activo ? 'Avisos por Telegram activos' : 'Avisos por Telegram desactivados'),
        subtitle: Text(activo
            ? 'Te llegan al instante. Mínimo global: ${config.stockMinimo}'
            : (cargado
                ? 'Prendelos para enterarte al instante cuando algo se agota'
                : 'Falta cargar el bot (token y chat) en el Panel de la PC')),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}

/// "avisado hace 3 h · próximo aviso desde 23:00" (null si el bot nunca avisó
/// de este producto y puede avisar ya).
String? textoAvisos(Alerta a, DateTime ahora) {
  final partes = [
    if (a.ultimaAlerta != null) 'avisado ${hace(a.ultimaAlerta!, ahora)}',
    if (a.proximoAviso != null) 'próximo aviso desde ${horaCorta(a.proximoAviso!, ahora)}',
  ];
  return partes.isEmpty ? null : partes.join(' · ');
}

/// "hace 5 min", "hace 3 h", "hace 2 días".
String hace(String iso, DateTime ahora) {
  final f = DateTime.tryParse(iso);
  if (f == null) return iso;
  final d = ahora.difference(f);
  if (d.inMinutes < 1) return 'recién';
  if (d.inMinutes < 60) return 'hace ${d.inMinutes} min';
  if (d.inHours < 24) return 'hace ${d.inHours} h';
  return d.inDays == 1 ? 'hace 1 día' : 'hace ${d.inDays} días';
}

/// "23:00" si es hoy; "07/10 01:30" si es otro día.
String horaCorta(String iso, DateTime ahora) {
  final f = DateTime.tryParse(iso);
  if (f == null) return iso;
  String dos(int n) => n.toString().padLeft(2, '0');
  final hora = '${dos(f.hour)}:${dos(f.minute)}';
  final mismoDia = f.year == ahora.year && f.month == ahora.month && f.day == ahora.day;
  return mismoDia ? hora : '${dos(f.day)}/${dos(f.month)} $hora';
}

class _FilaAlerta extends StatelessWidget {
  const _FilaAlerta({required this.alerta, required this.alTocar});
  final Alerta alerta;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    final color = alerta.esBajo ? c.peligro : c.aviso;
    return ListTile(
      onTap: alTocar,
      leading: CircleAvatar(
        backgroundColor: color.withValues(alpha: 0.15),
        child: Text('${alerta.stock}', style: TextStyle(color: color, fontWeight: FontWeight.w800)),
      ),
      title: Text(alerta.nombre, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        [
          [
            alerta.codigo,
            alerta.esBajo ? 'mínimo ${alerta.stockMinimo}' : 'máximo ${alerta.stockMaximo}',
            if (alerta.umbralPropio) 'umbral propio',
          ].join(' · '),
          ?textoAvisos(alerta, DateTime.now()),
        ].join('\n'),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: alerta.esBajo ? const Icon(Icons.add_shopping_cart) : null,
    );
  }
}

/// Reposición rápida desde la alerta: sumar stock sin cambiar de pestaña.
class _HojaReponer extends StatefulWidget {
  const _HojaReponer({required this.alerta, required this.api});
  final Alerta alerta;
  final ClienteApi api;

  @override
  State<_HojaReponer> createState() => _HojaReponerState();
}

class _HojaReponerState extends State<_HojaReponer> {
  late int _cantidad = widget.alerta.esBajo
      ? (widget.alerta.stockMinimo * 2 - widget.alerta.stock).clamp(1, 1000)
      : 1;
  bool _ocupado = false;

  Future<void> _sumar() async {
    setState(() => _ocupado = true);
    try {
      final r = await widget.api.movimiento(widget.alerta.codigo, _cantidad, sumar: true, motivo: 'Reposición (alerta)');
      if (!mounted) return;
      final pendientes = avisoVentasPendientesCorto(r);
      mostrarMensaje(context, '${r.nombre}: stock ${r.stockNuevo}${pendientes == null ? '' : '. $pendientes'}');
      Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (!mounted) return;
      mostrarMensaje(context, e.mensaje, error: true);
      // no se sabe si se sumó: se cierra la hoja y la lista se recarga con
      // el stock real (repetir acá podría sumarlo dos veces)
      if (e.incierto) {
        Navigator.pop(context, true);
      } else {
        setState(() => _ocupado = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final a = widget.alerta;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, MediaQuery.of(context).viewInsets.bottom + 24),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(a.nombre, style: t.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
        Text('${a.codigo} · stock actual ${a.stock}', style: t.textTheme.bodyMedium),
        const SizedBox(height: 20),
        Text('¿Cuántas unidades entraron?', textAlign: TextAlign.center, style: t.textTheme.titleSmall),
        const SizedBox(height: 8),
        SelectorCantidad(valor: _cantidad, alCambiar: (n) => setState(() => _cantidad = n)),
        const SizedBox(height: 20),
        FilledButton.icon(
          onPressed: _ocupado ? null : _sumar,
          icon: const Icon(Icons.add),
          label: Text('Sumar $_cantidad al stock'),
        ),
      ]),
    );
  }
}

class PantallaConfigAlertas extends StatefulWidget {
  const PantallaConfigAlertas({super.key});

  @override
  State<PantallaConfigAlertas> createState() => _PantallaConfigAlertasState();
}

/// Lo que va a pasar con el umbral recién guardado (mismo texto que el Panel):
/// apagar las alertas sin querer no puede pasar en silencio, y encenderlas tampoco.
String detalleUmbralGlobal(int minimo, int maximo) {
  if (minimo == 0 && maximo == 0) return 'No va a llegar ninguna alerta de stock.';
  if (minimo == 0) return 'Solo va a avisar por sobre-stock (a partir de $maximo). Por stock bajo, no.';
  if (maximo == 0) return 'Solo va a avisar por stock bajo (cuando quede en $minimo o menos). Por sobre-stock, no.';
  return 'Va a avisar cuando un producto quede en $minimo o menos, y cuando llegue a $maximo o más.';
}

/// "3 productos tienen umbral propio: …" (null si ninguno tiene).
String? textoUmbralesPropios(int total, int inactivos) {
  if (total <= 0) return null;
  final base = total == 1
      ? '1 producto tiene umbral propio: a ese el global no le cambia nada'
      : '$total productos tienen umbral propio: a esos el global no les cambia nada';
  if (inactivos <= 0) return base;
  return inactivos == 1
      ? '$base (1 de esos está apagado y no avisa)'
      : '$base ($inactivos de esos están apagados y no avisan)';
}

class _PantallaConfigAlertasState extends State<PantallaConfigAlertas> {
  final _minimo = TextEditingController();
  final _maximo = TextEditingController();
  ConfigAlertas? _config;
  bool _ocupado = false;
  String? _error;
  String? _errorUmbral;

  ClienteApi get _api => context.read<SesionEstado>().api;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  @override
  void dispose() {
    _minimo.dispose();
    _maximo.dispose();
    super.dispose();
  }

  /// Los campos del umbral NUNCA abren vacíos: un vacío que se guardara
  /// como 0 apagaría las alertas sin que nadie lo decida.
  void _mostrar(ConfigAlertas c) {
    _config = c;
    _minimo.text = '${c.stockMinimo}';
    _maximo.text = '${c.stockMaximo}';
    _errorUmbral = null;
  }

  Future<void> _cargar() async {
    try {
      final c = await _api.configAlertas();
      if (mounted) setState(() => _mostrar(c));
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    }
  }

  /// Corre un pedido con la barra de progreso; null si falló (ya avisado).
  /// Los carteles de resultado van DESPUÉS: con la barra andando detrás
  /// parecería que todavía está guardando.
  Future<T?> _ejecutar<T>(Future<T> Function() pedido) async {
    setState(() => _ocupado = true);
    try {
      return await pedido();
    } on ApiError catch (e) {
      if (!mounted) return null;
      mostrarMensaje(context, e.mensaje, error: true);
      if (e.incierto) _cargar(); // mostrar lo que quedó guardado de verdad
      return null;
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _avisar(String titulo, String mensaje) => showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(titulo),
          content: Text(mensaje),
          actions: [FilledButton(onPressed: () => Navigator.pop(context), child: const Text('Entendido'))],
        ),
      );

  /// Prender o apagar el bot es lo único de Telegram que se cambia desde el
  /// celular (corte de emergencia). Apagarlo pide confirmación.
  Future<void> _cambiarTelegram(bool habilitado) async {
    if (!habilitado) {
      final ok = await confirmar(context,
          titulo: '¿Apagar los avisos por Telegram?',
          mensaje: 'No va a llegar ninguna alerta hasta que lo vuelvas a prender.',
          si: 'Apagar');
      if (!ok || !mounted) return;
    }
    final c = await _ejecutar(() => _api.guardarTelegram(habilitado: habilitado));
    if (c == null || !mounted) return;
    setState(() => _config = c);
    mostrarMensaje(context, habilitado ? 'Avisos por Telegram prendidos.' : 'Avisos por Telegram apagados.');
  }

  Future<void> _probar() async {
    final r = await _ejecutar(_api.probarTelegram);
    if (r != null && mounted) mostrarMensaje(context, r.detalle, error: !r.ok);
  }

  Future<void> _guardarUmbrales() async {
    final minimo = int.tryParse(_minimo.text.trim());
    final maximo = int.tryParse(_maximo.text.trim());
    if (minimo == null || maximo == null || minimo < 0 || maximo < 0) {
      // un campo vacío es un error, nunca un 0 (que apaga ese aviso)
      setState(() => _errorUmbral = 'Completá el mínimo y el máximo con números enteros (0 = no avisar).');
      return;
    }
    setState(() => _errorUmbral = null);
    final u = await _ejecutar(() => _api.guardarUmbrales(minimo, maximo));
    if (u == null || !mounted) return;
    setState(() => _mostrar(_config!.conUmbrales(u)));
    await _avisar('Umbral global guardado',
        'Mínimo: ${u.stockMinimo}    Máximo: ${u.stockMaximo}\n\n${detalleUmbralGlobal(u.stockMinimo, u.stockMaximo)}');
  }

  Future<void> _quitarUmbralGlobal() async {
    final c = _config!;
    final propios = c.umbralesPropios;
    final detalle = 'El umbral global de hoy (mínimo ${c.stockMinimo}, máximo ${c.stockMaximo}) se va a borrar.\n\n'
        'Los productos SIN umbral propio dejan de avisar.\n'
        '${propios > 0 ? 'Los $propios producto(s) CON umbral propio siguen avisando igual: no se les toca nada.' : 'Ningún producto tiene umbral propio, así que no va a avisar ninguno.'}'
        '\n\n¿Seguimos?';
    final ok = await confirmar(context, titulo: 'Quitar el umbral global', mensaje: detalle, si: 'Quitar');
    if (!ok || !mounted) return;
    final u = await _ejecutar(_api.quitarUmbralGlobal);
    if (u == null || !mounted) return;
    setState(() => _mostrar(_config!.conUmbrales(u)));
    await _avisar(
        'Umbral global quitado',
        'Listo (${u.borradas ?? 0} fila(s)).\n\n'
            '${u.umbralesPropios > 0 ? 'Siguen avisando los ${u.umbralesPropios} producto(s) con umbral propio.' : 'No va a llegar ninguna alerta de stock.'}');
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final c = _config;
    return Scaffold(
      appBar: AppBar(title: const Text('Configurar alertas')),
      body: c == null
          ? (_error != null
              ? ErrorConReintento(mensaje: _error!, reintentar: _cargar)
              : const Center(child: CircularProgressIndicator()))
          : ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 32), children: [
              if (_ocupado) const LinearProgressIndicator(),
              const Seccion('Umbral global'),
              Text('Se aplica a todos los productos que no tienen un umbral propio.', style: t.textTheme.bodySmall),
              if (!c.umbralGlobalExiste)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('Hoy no hay umbral global: los productos sin umbral propio no avisan.',
                      style: t.textTheme.bodySmall?.copyWith(color: ColoresEstado.de(context).aviso)),
                ),
              const SizedBox(height: 12),
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: TextField(
                    key: const Key('campo_minimo'),
                    controller: _minimo,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'Stock mínimo', helperText: 'Avisa si queda en esto o menos'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    key: const Key('campo_maximo'),
                    controller: _maximo,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'Stock máximo', helperText: 'Avisa si llega a esto o más'),
                  ),
                ),
              ]),
              const SizedBox(height: 8),
              Text('Poné 0 para no recibir ese aviso. Con 0 y 0 no llega ninguna alerta.', style: t.textTheme.bodySmall),
              if (textoUmbralesPropios(c.umbralesPropios, c.umbralesInactivos) case final propios?)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(propios, style: t.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600)),
                ),
              if (_errorUmbral != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(_errorUmbral!, style: TextStyle(color: t.colorScheme.error)),
                ),
              const SizedBox(height: 12),
              FilledButton.tonal(onPressed: _ocupado ? null : _guardarUmbrales, child: const Text('Guardar umbrales')),
              if (c.umbralGlobalExiste) ...[
                const SizedBox(height: 8),
                OutlinedButton(
                  onPressed: _ocupado ? null : _quitarUmbralGlobal,
                  child: const Text('Quitar el umbral global'),
                ),
              ],
              const Seccion('Telegram'),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Enviar avisos por Telegram'),
                value: c.telegramHabilitado,
                onChanged: _ocupado ? null : _cambiarTelegram,
              ),
              // el token no se muestra ni se carga acá: se carga copiando y
              // pegando en el Panel de la PC (regla 4)
              Text(
                'Chat: ${c.chatId.isEmpty ? 'sin cargar' : c.chatId} · '
                'Token: ${c.tokenConfigurado ? 'configurado (oculto)' : 'sin cargar'} — se cargan en el Panel de la PC',
                style: t.textTheme.bodySmall,
              ),
              const SizedBox(height: 16),
              OutlinedButton.icon(
                onPressed: _ocupado ? null : _probar,
                icon: const Icon(Icons.send),
                label: const Text('Enviar mensaje de prueba'),
              ),
            ]),
    );
  }
}
