import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

/// Formatos de los códigos de producto (EAN/UPC del súper, Code128/39 de
/// proveedores, QR por las dudas). Restringirlos acelera la lectura.
const formatosProducto = [
  BarcodeFormat.ean13,
  BarcodeFormat.ean8,
  BarcodeFormat.upcA,
  BarcodeFormat.upcE,
  BarcodeFormat.code128,
  BarcodeFormat.code39,
  BarcodeFormat.code93,
  BarcodeFormat.itf14,
  BarcodeFormat.codabar,
  BarcodeFormat.qrCode,
];

/// Abre la cámara, lee UN código y lo devuelve (o null si se cancela).
Future<String?> escanearCodigo(BuildContext context) =>
    Navigator.of(context).push<String>(MaterialPageRoute(builder: (_) => const PantallaEscaner()));

String? primerCodigo(BarcodeCapture captura) {
  final codigos = codigosDe(captura);
  return codigos.isEmpty ? null : codigos.first;
}

/// Todos los códigos legibles de una captura (puede haber varios en cuadro).
List<String> codigosDe(BarcodeCapture captura) => [
      for (final b in captura.barcodes)
        if (b.rawValue?.trim() case final v? when v.isNotEmpty) v,
    ];

class PantallaEscaner extends StatefulWidget {
  const PantallaEscaner({super.key});

  @override
  State<PantallaEscaner> createState() => _PantallaEscanerState();
}

class _PantallaEscanerState extends State<PantallaEscaner> {
  final _control = MobileScannerController(formats: formatosProducto);
  bool _listo = false;

  @override
  void dispose() {
    _control.dispose();
    super.dispose();
  }

  void _alDetectar(BarcodeCapture captura) {
    final codigo = primerCodigo(captura);
    if (codigo == null || _listo) return;
    _listo = true;
    HapticFeedback.mediumImpact();
    SystemSound.play(SystemSoundType.click);
    Navigator.of(context).pop(codigo);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('Escanear código', style: TextStyle(color: Colors.white, fontSize: 20)),
        actions: [BotonLinterna(control: _control)],
      ),
      body: VisorCamara(control: _control, alDetectar: _alDetectar,
          ayuda: 'Apuntá al código de barras del producto'),
    );
  }
}

class BotonLinterna extends StatelessWidget {
  const BotonLinterna({super.key, required this.control});
  final MobileScannerController control;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<MobileScannerState>(
      valueListenable: control,
      builder: (context, estado, _) {
        final prendida = estado.torchState == TorchState.on;
        return IconButton(
          tooltip: prendida ? 'Apagar linterna' : 'Prender linterna',
          color: Colors.white,
          icon: Icon(prendida ? Icons.flash_on : Icons.flash_off),
          onPressed: estado.torchState == TorchState.unavailable ? null : control.toggleTorch,
        );
      },
    );
  }
}

/// Vista de cámara con el recuadro de lectura y mensajes de error claros.
class VisorCamara extends StatelessWidget {
  const VisorCamara({super.key, required this.control, required this.alDetectar, this.ayuda});

  final MobileScannerController control;
  final void Function(BarcodeCapture) alDetectar;
  final String? ayuda;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final ancho = c.maxWidth * 0.8;
      final ventana = Rect.fromCenter(center: Offset(c.maxWidth / 2, c.maxHeight / 2), width: ancho, height: ancho * 0.55);
      return Stack(fit: StackFit.expand, children: [
        MobileScanner(
          controller: control,
          onDetect: alDetectar,
          scanWindow: ventana,
          tapToFocus: true,
          errorBuilder: (context, error) => _ErrorCamara(error: error),
        ),
        IgnorePointer(child: CustomPaint(painter: _Recuadro(ventana, Theme.of(context).colorScheme.primary))),
        if (ayuda != null)
          Positioned(
            left: 24,
            right: 24,
            top: ventana.bottom + 24,
            child: Text(ayuda!, textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white, fontSize: 16, shadows: [Shadow(blurRadius: 6)])),
          ),
      ]);
    });
  }
}

class _ErrorCamara extends StatelessWidget {
  const _ErrorCamara({required this.error});
  final MobileScannerException error;

  @override
  Widget build(BuildContext context) {
    final mensaje = switch (error.errorCode) {
      MobileScannerErrorCode.permissionDenied =>
        'La app no tiene permiso para usar la cámara. Activalo en Ajustes del teléfono > Apps > Panel Dueño > Permisos.',
      MobileScannerErrorCode.unsupported => 'Este teléfono no permite escanear con la cámara.',
      _ => 'No se pudo abrir la cámara. Cerrá otras apps que la estén usando y probá de nuevo.',
    };
    return ColoredBox(
      color: Colors.black,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.no_photography_outlined, color: Colors.white70, size: 56),
            const SizedBox(height: 16),
            Text(mensaje, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white, fontSize: 16)),
          ]),
        ),
      ),
    );
  }
}

class _Recuadro extends CustomPainter {
  _Recuadro(this.ventana, this.color);
  final Rect ventana;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final r = RRect.fromRectAndRadius(ventana, const Radius.circular(18));
    final fondo = Path()
      ..addRect(Offset.zero & size)
      ..addRRect(r)
      ..fillType = PathFillType.evenOdd;
    canvas.drawPath(fondo, Paint()..color = Colors.black.withValues(alpha: 0.55));
    canvas.drawRRect(r, Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3);
  }

  @override
  bool shouldRepaint(_Recuadro old) => old.ventana != ventana || old.color != color;
}
