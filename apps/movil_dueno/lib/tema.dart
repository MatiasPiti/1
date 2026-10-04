import 'package:flutter/material.dart';

/// Paleta tomada del logo (la nutria de traje): marrón caramelo, crema,
/// carbón del traje y el gris del fondo.
class Paleta {
  static const caramelo = Color(0xFFA0703F);
  static const carameloClaro = Color(0xFFD9A86C);
  static const chocolate = Color(0xFF5C3D24);
  static const crema = Color(0xFFF2E6D3);
  static const cremaClaro = Color(0xFFFAF6EF);
  static const carbon = Color(0xFF17181A);
  static const carbonSuperficie = Color(0xFF212226);
  static const carbonElevado = Color(0xFF2B2C31);

  static const ok = Color(0xFF5FA463);
  static const okOscuro = Color(0xFF7FB77E);
  static const aviso = Color(0xFFC98A1B);
  static const avisoOscuro = Color(0xFFE0B050);
  static const peligro = Color(0xFFC0392B);
  static const peligroOscuro = Color(0xFFE57373);
}

/// Colores semánticos que no están en ColorScheme (stock ok / bajo / sobre).
@immutable
class ColoresEstado extends ThemeExtension<ColoresEstado> {
  const ColoresEstado({required this.ok, required this.aviso, required this.peligro});

  final Color ok;
  final Color aviso;
  final Color peligro;

  static ColoresEstado de(BuildContext context) => Theme.of(context).extension<ColoresEstado>()!;

  @override
  ColoresEstado copyWith({Color? ok, Color? aviso, Color? peligro}) =>
      ColoresEstado(ok: ok ?? this.ok, aviso: aviso ?? this.aviso, peligro: peligro ?? this.peligro);

  @override
  ColoresEstado lerp(ColoresEstado? other, double t) {
    if (other == null) return this;
    return ColoresEstado(
      ok: Color.lerp(ok, other.ok, t)!,
      aviso: Color.lerp(aviso, other.aviso, t)!,
      peligro: Color.lerp(peligro, other.peligro, t)!,
    );
  }
}

ThemeData temaClaro() {
  final esquema = ColorScheme.fromSeed(seedColor: Paleta.caramelo).copyWith(
    primary: Paleta.caramelo,
    onPrimary: Colors.white,
    secondary: Paleta.chocolate,
    surface: Colors.white,
    error: Paleta.peligro,
  );
  return _base(esquema, fondo: Paleta.cremaClaro, estado: const ColoresEstado(
    ok: Paleta.ok, aviso: Paleta.aviso, peligro: Paleta.peligro));
}

ThemeData temaOscuro() {
  final esquema = ColorScheme.fromSeed(seedColor: Paleta.caramelo, brightness: Brightness.dark).copyWith(
    primary: Paleta.carameloClaro,
    onPrimary: const Color(0xFF2B1A0C),
    secondary: Paleta.crema,
    surface: Paleta.carbonSuperficie,
    surfaceContainerHighest: Paleta.carbonElevado,
    error: Paleta.peligroOscuro,
  );
  return _base(esquema, fondo: Paleta.carbon, estado: const ColoresEstado(
    ok: Paleta.okOscuro, aviso: Paleta.avisoOscuro, peligro: Paleta.peligroOscuro));
}

ThemeData _base(ColorScheme esquema, {required Color fondo, required ColoresEstado estado}) {
  final redondeado = RoundedRectangleBorder(borderRadius: BorderRadius.circular(16));
  return ThemeData(
    useMaterial3: true,
    colorScheme: esquema,
    scaffoldBackgroundColor: fondo,
    extensions: [estado],
    appBarTheme: AppBarTheme(
      backgroundColor: fondo,
      surfaceTintColor: Colors.transparent,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontSize: 22, fontWeight: FontWeight.w700, color: esquema.onSurface, letterSpacing: 0.2),
    ),
    cardTheme: CardThemeData(
      color: esquema.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: redondeado.copyWith(side: BorderSide(color: esquema.outlineVariant.withValues(alpha: 0.5))),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: esquema.surfaceContainerHighest.withValues(alpha: 0.5),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(64, 52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(64, 52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: esquema.surface,
      indicatorColor: esquema.primary.withValues(alpha: 0.18),
      labelTextStyle: WidgetStatePropertyAll(
        TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: esquema.onSurface)),
    ),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
    bottomSheetTheme: const BottomSheetThemeData(showDragHandle: true),
  );
}
