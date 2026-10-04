import 'package:intl/intl.dart';

final _moneda = NumberFormat.currency(locale: 'es_AR', symbol: r'$', decimalDigits: 0);
final _monedaCentavos = NumberFormat.currency(locale: 'es_AR', symbol: r'$', decimalDigits: 2);
final _numero = NumberFormat.decimalPattern('es_AR');

/// $ 2.500 (o $ 2.450,50 si tiene centavos).
String moneda(num valor) {
  final redondo = valor == valor.roundToDouble();
  return (redondo ? _moneda : _monedaCentavos).format(valor);
}

String numero(num valor) => _numero.format(valor);

/// Acepta "2500", "2.500", "2500,50", "2.500,50" y "2500.50".
double? parsearNumero(String texto) {
  var t = texto.trim().replaceAll(r'$', '').replaceAll(' ', '');
  if (t.isEmpty) return null;
  if (t.contains(',') && t.contains('.')) {
    t = t.lastIndexOf(',') > t.lastIndexOf('.')
        ? t.replaceAll('.', '').replaceAll(',', '.')
        : t.replaceAll(',', '');
  } else if (t.contains(',')) {
    t = t.replaceAll(',', '.');
  } else if (RegExp(r'^-?\d{1,3}(\.\d{3})+$').hasMatch(t)) {
    t = t.replaceAll('.', ''); // "2.500" = dos mil quinientos, no 2,5
  }
  return double.tryParse(t);
}

String fechaHora(String iso) {
  final f = DateTime.tryParse(iso);
  if (f == null) return iso;
  final hoy = DateTime.now();
  final mismoDia = f.year == hoy.year && f.month == hoy.month && f.day == hoy.day;
  return mismoDia ? 'Hoy ${DateFormat.Hm('es_AR').format(f)}' : DateFormat('dd/MM HH:mm', 'es_AR').format(f);
}

String fechaLarga(DateTime f) {
  final texto = DateFormat("EEEE d 'de' MMMM", 'es_AR').format(f);
  return texto[0].toUpperCase() + texto.substring(1);
}

/// Inicial del día de la semana para los gráficos: L M X J V S D.
String inicialDia(String isoFecha) {
  final f = DateTime.tryParse(isoFecha);
  if (f == null) return '';
  return const ['L', 'M', 'X', 'J', 'V', 'S', 'D'][f.weekday - 1];
}
