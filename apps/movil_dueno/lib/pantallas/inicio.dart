import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../estado.dart';
import 'ajustes.dart';
import 'alertas.dart';
import 'dashboard.dart';
import 'facturas.dart';
import 'precios.dart';
import 'stock.dart';

/// Contenedor con la barra de navegación inferior. Las pestañas se
/// mantienen vivas (IndexedStack) para no perder lo que Leo venía haciendo.
class PantallaInicio extends StatefulWidget {
  const PantallaInicio({super.key});

  @override
  State<PantallaInicio> createState() => _PantallaInicioState();
}

class _PantallaInicioState extends State<PantallaInicio> {
  int _pestana = 0;

  void _ir(int i) => setState(() => _pestana = i);

  @override
  Widget build(BuildContext context) {
    final alertas = context.watch<AlertasEstado>().cantidad;
    final pestanas = [
      PantallaDashboard(activa: _pestana == 0, irAAlertas: () => _ir(4)),
      PantallaStock(activa: _pestana == 1),
      PantallaPrecios(activa: _pestana == 2),
      const PantallaFacturas(),
      PantallaAlertas(activa: _pestana == 4),
    ];
    return Scaffold(
      // TickerMode pausa las animaciones (spinners, etc.) de las pestañas que
      // no se ven: si no, siguen corriendo ocultas y gastan batería.
      body: IndexedStack(index: _pestana, children: [
        for (var i = 0; i < pestanas.length; i++) TickerMode(enabled: i == _pestana, child: pestanas[i]),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _pestana,
        onDestinationSelected: _ir,
        destinations: [
          const NavigationDestination(icon: Icon(Icons.insights_outlined), selectedIcon: Icon(Icons.insights), label: 'Inicio'),
          const NavigationDestination(
              icon: Icon(Icons.inventory_2_outlined), selectedIcon: Icon(Icons.inventory_2), label: 'Stock'),
          const NavigationDestination(icon: Icon(Icons.sell_outlined), selectedIcon: Icon(Icons.sell), label: 'Precios'),
          const NavigationDestination(
              icon: Icon(Icons.receipt_long_outlined), selectedIcon: Icon(Icons.receipt_long), label: 'Facturas'),
          NavigationDestination(
            icon: Badge(isLabelVisible: alertas > 0, label: Text('$alertas'),
                child: const Icon(Icons.notifications_outlined)),
            selectedIcon: Badge(isLabelVisible: alertas > 0, label: Text('$alertas'),
                child: const Icon(Icons.notifications)),
            label: 'Alertas',
          ),
        ],
      ),
    );
  }
}

/// Botón de engranaje que abre Ajustes (tema, huella, conexión).
class BotonAjustes extends StatelessWidget {
  const BotonAjustes({super.key});

  @override
  Widget build(BuildContext context) => IconButton(
        tooltip: 'Ajustes',
        icon: const Icon(Icons.settings_outlined),
        onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const PantallaAjustes())),
      );
}
