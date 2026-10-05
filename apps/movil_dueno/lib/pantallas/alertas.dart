import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';

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
    return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 24), children: [
      if (_config != null) _EstadoTelegram(config: _config!, alTocar: _abrirConfig),
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
    final activo = config.telegramHabilitado && config.tokenConfigurado && config.chatId.isNotEmpty;
    return Card(
      child: ListTile(
        onTap: alTocar,
        leading: Icon(Icons.send_rounded, color: activo ? c.ok : c.aviso),
        title: Text(activo ? 'Avisos por Telegram activos' : 'Avisos por Telegram desactivados'),
        subtitle: Text(activo
            ? 'Te llegan al instante. Mínimo global: ${config.stockMinimo}'
            : 'Configuralos para enterarte al instante cuando algo se agota'),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
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
      subtitle: Text(alerta.esBajo
          ? '${alerta.codigo} · mínimo ${alerta.stockMinimo}'
          : '${alerta.codigo} · máximo ${alerta.stockMaximo}'),
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
      mostrarMensaje(context, '${r.nombre}: stock ${r.stockNuevo}');
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

class _PantallaConfigAlertasState extends State<PantallaConfigAlertas> {
  final _chat = TextEditingController();
  final _token = TextEditingController();
  final _minimo = TextEditingController();
  final _maximo = TextEditingController();
  ConfigAlertas? _config;
  bool _habilitado = false;
  bool _ocupado = false;
  String? _error;

  ClienteApi get _api => context.read<SesionEstado>().api;

  @override
  void initState() {
    super.initState();
    _cargar();
  }

  @override
  void dispose() {
    for (final c in [_chat, _token, _minimo, _maximo]) {
      c.dispose();
    }
    super.dispose();
  }

  void _mostrar(ConfigAlertas c) {
    _config = c;
    _habilitado = c.telegramHabilitado;
    _chat.text = c.chatId;
    _token.clear();
    _minimo.text = '${c.stockMinimo}';
    _maximo.text = '${c.stockMaximo}';
  }

  Future<void> _cargar() async {
    try {
      final c = await _api.configAlertas();
      if (mounted) setState(() => _mostrar(c));
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    }
  }

  Future<void> _ejecutar(Future<void> Function() accion) async {
    setState(() => _ocupado = true);
    try {
      await accion();
    } on ApiError catch (e) {
      if (!mounted) return;
      mostrarMensaje(context, e.mensaje, error: true);
      if (e.incierto) _cargar(); // mostrar lo que quedó guardado de verdad
    } finally {
      if (mounted) setState(() => _ocupado = false);
    }
  }

  Future<void> _guardarTelegram() => _ejecutar(() async {
        final c = await _api.guardarTelegram(
          habilitado: _habilitado,
          chatId: _chat.text.trim(),
          botToken: _token.text.trim().isEmpty ? null : _token.text.trim(),
        );
        if (!mounted) return;
        setState(() => _mostrar(c));
        mostrarMensaje(context, 'Configuración de Telegram guardada.');
      });

  Future<void> _probar() => _ejecutar(() async {
        final enviado = await _api.probarTelegram();
        if (!mounted) return;
        mostrarMensaje(context,
            enviado ? '¡Mensaje enviado! Fijate en Telegram.' : 'No se pudo enviar: revisá que esté habilitado, el token y el chat ID.',
            error: !enviado);
      });

  Future<void> _guardarUmbrales() => _ejecutar(() async {
        final minimo = int.tryParse(_minimo.text.trim());
        final maximo = int.tryParse(_maximo.text.trim());
        if (minimo == null || maximo == null || minimo < 0 || maximo < 0) {
          mostrarMensaje(context, 'Escribí números enteros (0 = sin límite).', error: true);
          return;
        }
        await _api.guardarUmbrales(minimo, maximo);
        if (mounted) mostrarMensaje(context, 'Umbrales guardados.');
      });

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
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _minimo,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'Stock mínimo', helperText: 'Avisa si baja de acá'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _maximo,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'Stock máximo', helperText: '0 = sin límite'),
                  ),
                ),
              ]),
              const SizedBox(height: 12),
              FilledButton.tonal(onPressed: _ocupado ? null : _guardarUmbrales, child: const Text('Guardar umbrales')),
              const Seccion('Telegram'),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Enviar avisos por Telegram'),
                value: _habilitado,
                onChanged: (v) => setState(() => _habilitado = v),
              ),
              TextField(
                controller: _chat,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Chat ID', helperText: 'Pedíselo a @userinfobot en Telegram'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _token,
                autocorrect: false,
                decoration: InputDecoration(
                  labelText: c.tokenConfigurado ? 'Token del bot (guardado: ${c.tokenMascara})' : 'Token del bot',
                  helperText: c.tokenConfigurado ? 'Dejalo vacío para no cambiarlo' : 'Lo da @BotFather al crear el bot',
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(onPressed: _ocupado ? null : _guardarTelegram, child: const Text('Guardar Telegram')),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: _ocupado ? null : _probar,
                icon: const Icon(Icons.send),
                label: const Text('Enviar mensaje de prueba'),
              ),
            ]),
    );
  }
}
