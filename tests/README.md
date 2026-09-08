# Pruebas

Scripts sueltos, sin framework: cada uno arma su propia base en una carpeta
temporal (`paths.set_base_override`), abre la app de verdad y termina con
código 0 si pasó. Se corren todos juntos con `correr_todos.py`.

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
xvfb-run -a venv/bin/python tests/correr_todos.py     # Linux (las apps son Tkinter)
venv\Scripts\python tests\correr_todos.py             # Windows
```

| Archivo | Qué cuida |
|---|---|
| `test_usb_caja.py` | La Caja del USB de punta a punta: escanear, sumar cantidad, quitar un artículo suelto sin llevarse los otros, editar cantidad, cobrar, descontar stock y exportar la sincronización. **Lee el texto real de cada celda con `celda.get()`**, que es lo único que agarra el bug del carrito en blanco: las celdas son `tk.Entry` de solo lectura, así que un `config(text=...)` no dibuja nada y `cget("text")` siempre devuelve `""`. |
| `test_usb_dueno.py` | Que el Panel del USB abra con sus 9 pestañas, muestre el cartel de emergencia y el botón de sincronizar, marque todo con origen `USB_DUENO`, **no** tenga el `Ctrl+Shift+M` de conciliación (es exclusivo del Maestro) y entre en una pantalla de 1366x768. |
| `test_usb_mantenimiento.py` | Que reponer archivos del programa **nunca** pise el `config.ini` ni la base del cliente, aunque el espejo los traiga adentro (pasa si se probaron los `.exe` desde `dist\`). |
| `test_mantenimiento_completo.py` | La corrida entera de "REPARAR TODO" sobre una instalación Maestro: migración de esquema, stock negativo corregido con su auditoría, reporte escrito, y el `config.ini` real intacto. |
| `test_reparacion_por_tipo.py` | Que reparar a mano la carpeta de un USB no le meta adentro las apps del Maestro. |
| `test_pendrive_reusado.py` | Pendrive con base de una versión anterior + ejecutable nuevo: la base se pone al día sola al arrancar, sin perder lo que ya había, y la pantalla de precios no se cae con `no such column`. |
| `test_respaldo_y_arranque.py` | Que exista una copia diaria verificada sin que nadie se acuerde, que las viejas se borren y las de la ventana no, y que si la base se rompe la app **avise** y ofrezca restaurar la copia en vez de no abrir en silencio a las 8 de la mañana. |
| `test_respaldos_en_mantenimiento.py` | El USB de Mantenimiento frente a las copias del cliente: que no las pise con las del espejo, que el informe diga si el negocio está respaldado y desde cuándo, que el rescate de último recurso use las copias diarias, y que con la base rota **y** sin ninguna copia el informe salga igual en vez de morir con un traceback. |
| `test_maestro.py` | Regresión de la Caja y el Panel del Maestro, y que una migración que falle no impida abrir la app (regla 6: ante la duda, la caja abre). |
