import 'dart:async';
import 'dart:math' as math;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/cliente_api.dart';
import '../api/modelos.dart';
import '../estado.dart';
import '../formato.dart';
import '../tema.dart';
import '../widgets/comunes.dart';
import 'inicio.dart';

class PantallaDashboard extends StatefulWidget {
  const PantallaDashboard({super.key, required this.activa, required this.irAAlertas});
  final bool activa;
  final VoidCallback irAAlertas;

  @override
  State<PantallaDashboard> createState() => _PantallaDashboardState();
}

class _PantallaDashboardState extends State<PantallaDashboard> {
  Dashboard? _datos;
  String? _error;
  String _periodo = 'historico';
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _cargar();
    _timer = Timer.periodic(const Duration(minutes: 1), (_) {
      if (widget.activa) _cargar();
    });
  }

  @override
  void didUpdateWidget(PantallaDashboard old) {
    super.didUpdateWidget(old);
    if (widget.activa && !old.activa) _cargar();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _cargar() async {
    try {
      final d = await context.read<SesionEstado>().api.dashboard(periodoTop: _periodo);
      if (!mounted) return;
      context.read<AlertasEstado>().actualizar(d.alertasActivas);
      setState(() {
        _datos = d;
        _error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.mensaje);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sesion = context.watch<SesionEstado>();
    final d = _datos;
    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(sesion.nombreLocal.isEmpty ? 'Mi Negocio' : sesion.nombreLocal),
          Text(fechaLarga(DateTime.now()),
              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant)),
        ]),
        actions: const [BotonAjustes()],
      ),
      body: d == null
          ? (_error != null
              ? ErrorConReintento(mensaje: _error!, reintentar: _cargar)
              : const Center(child: CircularProgressIndicator()))
          : RefreshIndicator(
              onRefresh: _cargar,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                children: [
                  if (_error != null) _AvisoSinConexion(mensaje: _error!),
                  _TarjetaHoy(d: d),
                  if (!d.telegramHabilitado) ...[
                    const SizedBox(height: 12),
                    _AvisoTelegramApagado(alTocar: widget.irAAlertas),
                  ],
                  if (d.alertasActivas > 0) ...[
                    const SizedBox(height: 12),
                    _AvisoAlertas(cantidad: d.alertasActivas, alTocar: widget.irAAlertas),
                  ],
                  const Seccion('Últimos 7 días'),
                  _GraficoSemana(dias: d.ultimos7Dias),
                  Seccion('Más vendidos', trailing: _selectorPeriodo()),
                  _TopProductos(top: d.topProductos),
                ],
              ),
            ),
    );
  }

  Widget _selectorPeriodo() => DropdownButton<String>(
        value: _periodo,
        underline: const SizedBox.shrink(),
        borderRadius: BorderRadius.circular(12),
        items: const [
          DropdownMenuItem(value: 'hoy', child: Text('Hoy')),
          DropdownMenuItem(value: '7dias', child: Text('7 días')),
          DropdownMenuItem(value: '30dias', child: Text('30 días')),
          DropdownMenuItem(value: 'historico', child: Text('Siempre')),
        ],
        onChanged: (v) {
          if (v == null) return;
          setState(() => _periodo = v);
          _cargar();
        },
      );
}

class _AvisoSinConexion extends StatelessWidget {
  const _AvisoSinConexion({required this.mensaje});
  final String mensaje;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Card(
          color: Theme.of(context).colorScheme.errorContainer,
          child: ListTile(
            leading: const Icon(Icons.wifi_off),
            title: const Text('Mostrando los últimos datos'),
            subtitle: Text(mensaje),
          ),
        ),
      );
}

class _TarjetaHoy extends StatelessWidget {
  const _TarjetaHoy({required this.d});
  final Dashboard d;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final oscuro = t.brightness == Brightness.dark;
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: oscuro
              ? [const Color(0xFF3A2A1C), Paleta.carbonElevado]
              : [Paleta.caramelo, Paleta.chocolate],
        ),
      ),
      child: DefaultTextStyle(
        style: const TextStyle(color: Colors.white),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('Ventas de hoy', style: TextStyle(fontSize: 15, color: Colors.white70)),
          const SizedBox(height: 6),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(moneda(d.totalHoy),
                style: const TextStyle(fontSize: 40, fontWeight: FontWeight.w800, color: Colors.white)),
          ),
          const SizedBox(height: 16),
          Row(children: [
            _Dato(titulo: 'Tickets', valor: numero(d.ticketsHoy)),
            const SizedBox(width: 28),
            _Dato(titulo: 'Ticket promedio', valor: moneda(d.ticketPromedio.round())),
          ]),
          if (d.porMetodo.isNotEmpty) ...[
            const SizedBox(height: 16),
            Wrap(spacing: 8, runSpacing: 8, children: [
              for (final m in d.porMetodo)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(20)),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(_metodo(m.metodo).$1, size: 16, color: Colors.white70),
                    const SizedBox(width: 6),
                    Text('${_metodo(m.metodo).$2} ${moneda(m.total)}', style: const TextStyle(fontSize: 13)),
                  ]),
                ),
            ]),
          ],
        ]),
      ),
    );
  }

  static (IconData, String) _metodo(String m) => switch (m) {
        'EFECTIVO' => (Icons.payments_outlined, 'Efectivo'),
        'TARJETA' => (Icons.credit_card, 'Tarjeta'),
        'TRANSFERENCIA' => (Icons.account_balance_outlined, 'Transf.'),
        'MIXTO' => (Icons.call_split, 'Mixto'),
        _ => (Icons.attach_money, m),
      };
}

class _Dato extends StatelessWidget {
  const _Dato({required this.titulo, required this.valor});
  final String titulo;
  final String valor;

  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(titulo, style: const TextStyle(fontSize: 13, color: Colors.white70)),
        Text(valor, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700, color: Colors.white)),
      ]);
}

class _AvisoAlertas extends StatelessWidget {
  const _AvisoAlertas({required this.cantidad, required this.alTocar});
  final int cantidad;
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    return Card(
      child: ListTile(
        onTap: alTocar,
        leading: CircleAvatar(backgroundColor: c.peligro.withValues(alpha: 0.15),
            child: Icon(Icons.warning_amber_rounded, color: c.peligro)),
        title: Text(cantidad == 1 ? '1 producto necesita atención' : '$cantidad productos necesitan atención'),
        subtitle: const Text('Stock bajo o sobre-stock'),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}

/// Con el bot apagado las alertas se siguen calculando, pero no le llega
/// ninguna a Leo: que no lo descubra el día que se quedó sin mercadería.
class _AvisoTelegramApagado extends StatelessWidget {
  const _AvisoTelegramApagado({required this.alTocar});
  final VoidCallback alTocar;

  @override
  Widget build(BuildContext context) {
    final c = ColoresEstado.de(context);
    return Card(
      child: ListTile(
        onTap: alTocar,
        leading: CircleAvatar(backgroundColor: c.aviso.withValues(alpha: 0.15),
            child: Icon(Icons.notifications_off_outlined, color: c.aviso)),
        title: const Text('El bot de Telegram está apagado: las alertas no se mandan.'),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}

class _GraficoSemana extends StatelessWidget {
  const _GraficoSemana({required this.dias});
  final List<VentaDia> dias;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    final maximo = dias.fold<double>(0, (m, d) => math.max(m, d.total));
    final total = dias.fold<double>(0, (s, d) => s + d.total);
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 16, 12, 8),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.only(left: 8, bottom: 12),
            child: Text('Total semana: ${moneda(total)}', style: t.textTheme.titleSmall),
          ),
          SizedBox(
            height: 180,
            child: BarChart(BarChartData(
              maxY: maximo == 0 ? 1 : maximo * 1.15,
              gridData: const FlGridData(show: false),
              borderData: FlBorderData(show: false),
              titlesData: FlTitlesData(
                leftTitles: const AxisTitles(),
                rightTitles: const AxisTitles(),
                topTitles: const AxisTitles(),
                bottomTitles: AxisTitles(
                  sideTitles: SideTitles(
                    showTitles: true,
                    reservedSize: 28,
                    getTitlesWidget: (v, meta) => SideTitleWidget(
                      meta: meta,
                      child: Text(inicialDia(dias[v.toInt()].dia),
                          style: TextStyle(
                            fontWeight: v.toInt() == dias.length - 1 ? FontWeight.w800 : FontWeight.w500,
                            color: t.colorScheme.onSurfaceVariant,
                          )),
                    ),
                  ),
                ),
              ),
              barTouchData: BarTouchData(
                touchTooltipData: BarTouchTooltipData(
                  getTooltipColor: (_) => t.colorScheme.inverseSurface,
                  getTooltipItem: (grupo, _, rod, _) => BarTooltipItem(
                    '${moneda(rod.toY)}\n${dias[grupo.x].tickets} tickets',
                    TextStyle(color: t.colorScheme.onInverseSurface, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
              barGroups: [
                for (var i = 0; i < dias.length; i++)
                  BarChartGroupData(x: i, barRods: [
                    BarChartRodData(
                      toY: dias[i].total,
                      width: 22,
                      borderRadius: BorderRadius.circular(6),
                      color: i == dias.length - 1 ? t.colorScheme.primary : t.colorScheme.primary.withValues(alpha: 0.45),
                    ),
                  ]),
              ],
            )),
          ),
        ]),
      ),
    );
  }
}

class _TopProductos extends StatelessWidget {
  const _TopProductos({required this.top});
  final List<TopProducto> top;

  @override
  Widget build(BuildContext context) {
    if (top.isEmpty) {
      return const Card(child: Padding(padding: EdgeInsets.all(24), child: Text('Todavía no hay ventas en este período.')));
    }
    final t = Theme.of(context);
    final maximo = top.first.cantidad == 0 ? 1 : top.first.cantidad;
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(children: [
          for (var i = 0; i < top.length; i++)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(children: [
                SizedBox(
                  width: 28,
                  child: Text('${i + 1}', style: t.textTheme.titleMedium?.copyWith(color: t.colorScheme.primary)),
                ),
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(top[i].nombre, maxLines: 1, overflow: TextOverflow.ellipsis, style: t.textTheme.bodyLarge),
                    const SizedBox(height: 6),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                        value: top[i].cantidad / maximo,
                        minHeight: 6,
                        backgroundColor: t.colorScheme.surfaceContainerHighest,
                      ),
                    ),
                  ]),
                ),
                const SizedBox(width: 12),
                Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                  Text('${numero(top[i].cantidad)} u.', style: t.textTheme.titleSmall),
                  Text(moneda(top[i].importe), style: t.textTheme.bodySmall?.copyWith(color: t.colorScheme.onSurfaceVariant)),
                ]),
              ]),
            ),
        ]),
      ),
    );
  }
}
