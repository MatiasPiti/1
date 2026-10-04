import 'package:flutter/material.dart';

import '../api/modelos.dart';
import '../formato.dart';
import '../tema.dart';
import 'escaner.dart';

/// Campo para escribir un código de producto, con botón de cámara que
/// escanea el código de barras y lo carga — igual que el lector USB de
/// la PC, que "tipea" el código y aprieta Enter.
class CampoCodigo extends StatelessWidget {
  const CampoCodigo({
    super.key,
    required this.controller,
    required this.alEnviar,
    this.etiqueta = 'Código o nombre',
    this.autofocus = false,
    this.alEscanear,
    this.escanearAbre,
  });

  final TextEditingController controller;
  final void Function(String texto) alEnviar;

  /// Si se indica, se usa en lugar de [alEnviar] cuando el código vino de la cámara.
  final void Function(String codigo)? alEscanear;
  final String etiqueta;
  final bool autofocus;

  /// Para tests: reemplaza la apertura de la cámara.
  final Future<String?> Function(BuildContext)? escanearAbre;

  Future<void> _escanear(BuildContext context) async {
    final codigo = await (escanearAbre ?? escanearCodigo)(context);
    if (codigo == null) return;
    controller.text = codigo;
    (alEscanear ?? alEnviar)(codigo);
  }

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      autofocus: autofocus,
      textInputAction: TextInputAction.search,
      onSubmitted: (t) => alEnviar(t.trim()),
      decoration: InputDecoration(
        labelText: etiqueta,
        prefixIcon: const Icon(Icons.search),
        suffixIcon: IconButton(
          key: const Key('boton_escanear'),
          tooltip: 'Escanear con la cámara',
          icon: const Icon(Icons.qr_code_scanner),
          onPressed: () => _escanear(context),
        ),
      ),
    );
  }
}

void mostrarMensaje(BuildContext context, String texto, {bool error = false}) {
  final esquema = Theme.of(context).colorScheme;
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(texto),
      backgroundColor: error ? esquema.error : null,
      duration: Duration(seconds: error ? 5 : 3),
    ));
}

class EstadoVacio extends StatelessWidget {
  const EstadoVacio({super.key, required this.icono, required this.titulo, this.detalle, this.accion});
  final IconData icono;
  final String titulo;
  final String? detalle;
  final Widget? accion;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icono, size: 64, color: t.colorScheme.primary.withValues(alpha: 0.7)),
          const SizedBox(height: 16),
          Text(titulo, textAlign: TextAlign.center, style: t.textTheme.titleMedium),
          if (detalle != null) ...[
            const SizedBox(height: 8),
            Text(detalle!, textAlign: TextAlign.center,
                style: t.textTheme.bodyMedium?.copyWith(color: t.colorScheme.onSurfaceVariant)),
          ],
          if (accion != null) ...[const SizedBox(height: 20), accion!],
        ]),
      ),
    );
  }
}

class ErrorConReintento extends StatelessWidget {
  const ErrorConReintento({super.key, required this.mensaje, required this.reintentar});
  final String mensaje;
  final VoidCallback reintentar;

  @override
  Widget build(BuildContext context) => EstadoVacio(
        icono: Icons.wifi_off_rounded,
        titulo: 'Algo salió mal',
        detalle: mensaje,
        accion: FilledButton.tonalIcon(onPressed: reintentar, icon: const Icon(Icons.refresh), label: const Text('Reintentar')),
      );
}

Color colorEstado(BuildContext context, EstadoStock estado) {
  final c = ColoresEstado.de(context);
  return switch (estado) { EstadoStock.bajo => c.peligro, EstadoStock.sobre => c.aviso, EstadoStock.ok => c.ok };
}

/// Pastilla con el stock coloreado según umbrales.
class PastillaStock extends StatelessWidget {
  const PastillaStock({super.key, required this.producto});
  final Producto producto;

  @override
  Widget build(BuildContext context) {
    final color = colorEstado(context, producto.estado);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(20)),
      child: Text('${producto.stock} u.', style: TextStyle(color: color, fontWeight: FontWeight.w700)),
    );
  }
}

/// Selector de cantidad: − [n] + con atajos.
class SelectorCantidad extends StatelessWidget {
  const SelectorCantidad({super.key, required this.valor, required this.alCambiar, this.atajos = const [1, 6, 12, 24]});
  final int valor;
  final ValueChanged<int> alCambiar;
  final List<int> atajos;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Column(children: [
      Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        IconButton.filledTonal(
          iconSize: 28,
          tooltip: 'Uno menos',
          onPressed: valor > 1 ? () => alCambiar(valor - 1) : null,
          icon: const Icon(Icons.remove),
        ),
        InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () async {
            final n = await pedirNumero(context, titulo: 'Cantidad', inicial: '$valor');
            if (n != null && n >= 1) alCambiar(n.round());
          },
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 4),
            child: Text('$valor', style: t.textTheme.displaySmall?.copyWith(fontWeight: FontWeight.w700)),
          ),
        ),
        IconButton.filledTonal(
          iconSize: 28,
          tooltip: 'Uno más',
          onPressed: () => alCambiar(valor + 1),
          icon: const Icon(Icons.add),
        ),
      ]),
      const SizedBox(height: 8),
      Wrap(spacing: 8, children: [
        for (final a in atajos)
          ChoiceChip(label: Text('$a'), selected: valor == a, onSelected: (_) => alCambiar(a)),
      ]),
    ]);
  }
}

/// Diálogo para escribir un número (acepta 2.500,50).
Future<double?> pedirNumero(BuildContext context,
    {required String titulo, String inicial = '', String? ayuda, bool decimales = false}) {
  final control = TextEditingController(text: inicial);
  return showDialog<double>(
    context: context,
    builder: (context) {
      void aceptar() {
        Navigator.pop(context, parsearNumero(control.text));
      }

      return AlertDialog(
        title: Text(titulo),
        content: TextField(
          controller: control,
          autofocus: true,
          keyboardType: TextInputType.numberWithOptions(decimal: decimales),
          decoration: InputDecoration(helperText: ayuda),
          onSubmitted: (_) => aceptar(),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancelar')),
          FilledButton(onPressed: aceptar, child: const Text('Aceptar')),
        ],
      );
    },
  );
}

Future<bool> confirmar(BuildContext context, {required String titulo, required String mensaje, String si = 'Confirmar'}) async {
  final r = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(titulo),
      content: Text(mensaje),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancelar')),
        FilledButton(onPressed: () => Navigator.pop(context, true), child: Text(si)),
      ],
    ),
  );
  return r ?? false;
}

class Seccion extends StatelessWidget {
  const Seccion(this.titulo, {super.key, this.trailing});
  final String titulo;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 20, 4, 8),
      child: Row(children: [
        Expanded(
          child: Text(titulo.toUpperCase(),
              style: t.textTheme.labelLarge?.copyWith(color: t.colorScheme.primary, letterSpacing: 1.1)),
        ),
        ?trailing,
      ]),
    );
  }
}
